"""RV-XF · LaTeX → PDF 单文件编译（**复用** export 层 xelatex 内核）。

铁律（继承自 `core/export/compiler.py`）：

- 编译命令唯一来源是 `build_xelatex_argv()`（`-no-shell-escape` 永远在列）；
  本模块**绝不**自己拼 xelatex 命令行；
- 编译只在 docker 容器内进行（no-network / read-only rootfs / 非 root /
  CPU·内存·PID 上限），本进程只发起 `docker run` 与读取产物；
- 超时与输出上限来自配置（`tex_compile_timeout_seconds` /
  `tex_max_output_bytes`），本模块把超时再钳到 `<= 90s`。

源码前置校验（纵深防御第一层，**非**完备沙箱）
----------------------------------------------
QA（黑盒 + 真机 docker）证伪了「仅靠 `guard_latex`」的纵深：`^^5c`（catcode-0
十六进制转义，TeX 词法层解释）、`\\lstinputlisting`、`\\VerbatimInput`、
`\\@@input`、`\\includegraphics{绝对路径}`、`\\openout` 等均可绕过逐字正则，
把容器内文件（如 `/etc/passwd`）读进返回的 PDF 文本层。

因此本模块在**进入容器之前**强制执行 `_validate_tex_source()`：

1. 拒绝一切 `^^`（TeX catcode 十六进制转义入口；正常数学 `x^2` 不含 `^^`）；
2. 拒绝文件读写 / 脚本执行类控制序列（`\\input \\include \\openin \\openout
   \\write \\read \\immediate \\catcode \\csname \\directlua \\luaexec
   \\special \\lstinputlisting \\VerbatimInput \\verbatiminput \\@@input
   \\inputminted \\ShellEscape …`）；
3. 拒绝危险宏包（`shellesc` / `minted` / `listings` / `fancyvrb` …）；
4. 拒绝任何 `{}` 参数中的绝对路径（`/…`）或上跳路径（`../`）——宽口径，宁可误拒；
5. **fail-closed**：无法判断即拒绝（`XF_TEX_SOURCE_INVALID` / 400）。

⚠️ **诚实声明**：上述正则是**纵深防御的第一层，并非等价的 TeX 词法器，也不是
完备的沙箱**。它无法覆盖所有 catcode/宏展开技巧。**根本解法**是在
`-no-shell-escape` 之外，启用 kpathsea 的 `openin_any=p` / `openout_any=p`
收紧文件访问，并保证编译镜像内不含任何敏感文件。该根因修复需要改
`core/export/compiler.py`（本文件当前**不属于可改范围**，属 RV14 未提交工作），
已上报待批。

孤儿容器清扫（对 `core/export/compiler.py` 根因的**补偿**）
------------------------------------------------------------
根因：`compiler.py` 的 docker runner 在超时后只 kill 本进程的 `subprocess`，
**不 kill 容器**；`docker run --rm` 仅在容器退出时才移除，故超时容器会以
100% CPU 永久驻留，每请求泄漏一个。本模块在 `finally` 里做 best-effort 清扫
（只针对本镜像的容器，绝不按名字模糊匹配），并加服务端并发闸自保。
根因修复 = 超时后 `docker rm -f` 该容器，已上报待批。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .models import (
    XF_TEX_COMPILE_FAILED,
    XF_TEX_KERNEL_UNAVAILABLE,
    XF_TEX_SOURCE_INVALID,
    XF_TEX_TIMEOUT,
    XfError,
)

logger = logging.getLogger(__name__)

#: 单文件源码上限（2 MB）——远端语义简单，够用且防滥用。
XF_TEX_MAX_SOURCE_BYTES = 2 * 1024 * 1024
#: 同步编译的硬上限（秒），云端无法在此时间内完成即如实失败。
XF_TEX_TIMEOUT_CEILING_SECONDS = 90
#: `build_xelatex_argv` 钉死的输入文件名（源码始终写入该名）。
XF_TEX_INPUT_NAME = "main.tex"

# --------------------------------------------------------------------------
# 前置校验规则（见模块 docstring）
# --------------------------------------------------------------------------

#: TeX catcode-0 十六进制转义入口（`^^5c` = `\`）。正常数学 `x^2` 不含 `^^`。
_HEX_ESCAPE_RE = re.compile(r"\^\^")

#: 危险控制序列（文件读写 / 脚本执行 / 词法改写）。`(?![A-Za-z])` 防止
#: `\include` 误伤 `\includegraphics`、`\input` 误伤 `\inputenc` 等。
_DANGEROUS_COMMAND_RE = re.compile(
    r"\\(?:"
    r"@@input|lstinputlisting|VerbatimInput|verbatiminput|inputminted|"
    r"includeonly|ShellEscape|closeout|closein|"
    r"input|include|openin|openout|catcode|csname|directlua|luaexec|"
    r"special|write|read|immediate"
    r")(?![A-Za-z])"
)

#: 危险宏包（任一出现即拒）。
_DANGEROUS_PACKAGES = frozenset(
    {"shellesc", "minted", "listings", "fancyvrb", "pythontex", "sagetex", "bashful"}
)

#: `\usepackage[opts]{a,b,c}` / `\RequirePackage[opts]{...}`
_PACKAGE_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")

#: 任意 `{...}` 参数（最内层，够用）
_BRACE_ARG_RE = re.compile(r"\{([^{}]*)\}")

#: 上跳路径：`..` 后跟分隔符
_PARENT_PATH_RE = re.compile(r"\.\.[/\\]")
#: 绝对路径：**段首边界**（行首 / 空白 / `{` / `,` / `=` / `(`）紧跟 `/`（POSIX）
#: 或 `X:/` · `X:\`（Windows）。边界要求避免误伤 URL（`https://` 中的 `s:/`
#: 其前是字母 `p`，非边界 → 不匹配）。
_ABS_PATH_RE = re.compile(r"(?:^|[\s{,=(])(?:/|[A-Za-z]:[/\\])")


def _invalid(message: str) -> XfError:
    """前置校验拒绝（fail-closed，400）。**只说明命中规则，不回显源码。**"""
    return XfError(message, code=XF_TEX_SOURCE_INVALID, http_status=400)


def _validate_tex_source(text: str) -> None:
    """编译前的强制前置校验；命中即抛 `XF_TEX_SOURCE_INVALID`（400）。

    规则见模块 docstring 第 1~5 条。**这是纵深防御第一层，非完备 TeX 沙箱。**
    """
    if _HEX_ESCAPE_RE.search(text):
        raise _invalid(
            "拒绝：检测到 TeX catcode 十六进制转义 '^^'（可隐藏反斜杠以绕过控制序列校验）"
        )

    m = _DANGEROUS_COMMAND_RE.search(text)
    if m is not None:
        raise _invalid(f"拒绝：检测到被禁止的 LaTeX 控制序列 {m.group(0)!r}（文件读写/脚本执行类）")

    for pm in _PACKAGE_RE.finditer(text):
        names = {n.strip().lower() for n in pm.group(1).split(",") if n.strip()}
        bad = names & _DANGEROUS_PACKAGES
        if bad:
            raise _invalid(f"拒绝：检测到被禁止的宏包 {sorted(bad)[0]!r}")

    for bm in _BRACE_ARG_RE.finditer(text):
        arg = bm.group(1)
        if _PARENT_PATH_RE.search(arg):
            raise _invalid("拒绝：参数中包含上跳路径 '..'")
        if _ABS_PATH_RE.search(arg):
            raise _invalid("拒绝：参数中包含绝对路径")


# --------------------------------------------------------------------------
# 服务端并发闸（xf 层自保，不依赖 config.py 的 compile_pool_enabled）
# --------------------------------------------------------------------------

_gate_lock = threading.Lock()
_compile_gate: threading.BoundedSemaphore | None = None


def _gate_size() -> int:
    raw = os.getenv("RESEARCH_XF_TEX_MAX_CONCURRENCY", "2")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 2
    return max(1, n)


def _get_compile_gate() -> threading.BoundedSemaphore:
    """惰性创建编译并发闸（线程安全）。

    用 `threading.BoundedSemaphore`（非 asyncio）—— 编译是**同步**执行，
    既可被 FastAPI 的线程池调用，也可被探针的多线程直接调用。
    """
    global _compile_gate
    with _gate_lock:
        if _compile_gate is None:
            _compile_gate = threading.BoundedSemaphore(_gate_size())
        return _compile_gate


# --------------------------------------------------------------------------
# 孤儿容器清扫（best-effort；对 core/export/compiler.py 根因的补偿）
# --------------------------------------------------------------------------


def _parse_rfc3339(s: str) -> float | None:
    """解析 docker inspect 的 `State.StartedAt`（RFC3339 / 纳秒）。失败返回 None。"""
    from datetime import datetime, timezone

    t = (s or "").strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _running_containers_of_image(image: str) -> list[tuple[str, float]]:
    """列出**正在运行**且镜像为 `image` 的容器 `[(id, started_epoch)]`。

    只按 `--filter ancestor=<image>` 精确匹配镜像，**绝不**按名字/模糊匹配。
    任何 docker 调用失败都返回空/跳过（best-effort，绝不影响主流程）。
    """
    out: list[tuple[str, float]] = []
    try:
        p = subprocess.run(
            ["docker", "ps", "--filter", f"ancestor={image}", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("xf_tex_orphan_list_failed err=%s", type(e).__name__)
        return out
    if p.returncode != 0:
        return out
    for cid in [ln.strip() for ln in (p.stdout or "").splitlines() if ln.strip()]:
        try:
            q = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.StartedAt}}", cid],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("xf_tex_orphan_inspect_failed err=%s", type(e).__name__)
            continue
        if q.returncode != 0:
            continue
        started = _parse_rfc3339(q.stdout or "")
        if started is not None:
            out.append((cid, started))
    return out


def _sweep_orphan_containers(
    image: str, request_started_at: float, *, remove_started_after: bool
) -> int:
    """清扫本镜像的孤儿容器（best-effort）。

    - 始终清掉「**早于**本次请求开始时间却仍在运行」的容器（历史孤儿）；
    - `remove_started_after=True`（本次编译**超时**）时，连带清掉「本次窗口内
      新出现且仍在运行」的容器（即本次泄漏的容器）。

    失败只记 WARNING，绝不让主流程报错；日志**不含**源码。
    """
    removed = 0
    margin = 1.0
    for cid, started in _running_containers_of_image(image):
        is_pre_existing = started < request_started_at - margin
        is_ours_new = remove_started_after and started >= request_started_at - margin
        if not (is_pre_existing or is_ours_new):
            continue
        try:
            r = subprocess.run(
                ["docker", "rm", "-f", cid],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("xf_tex_orphan_rm_failed container=%s err=%s", cid[:12], type(e).__name__)
            continue
        if r.returncode == 0:
            removed += 1
            logger.warning("xf_tex_orphan_container_removed container=%s", cid[:12])
    return removed


# --------------------------------------------------------------------------
# 编译
# --------------------------------------------------------------------------


def _kernel_unavailable(reason: str, hint: str) -> XfError:
    return XfError(
        reason,
        code=XF_TEX_KERNEL_UNAVAILABLE,
        http_status=503,
        retryable=True,
        detail={"available": False, "reason": reason, "hint": hint},
    )


def _looks_like_missing_kernel(log: str) -> bool:
    """docker 镜像/守护进程缺失的典型信号 → 判为「内核不可用」而非用户源码错误。"""
    lowered = (log or "").lower()
    markers = (
        "unable to find image",
        "pull access denied",
        "no such image",
        "cannot connect to the docker daemon",
        "docker: not found",
        "error during connect",
        "is the docker daemon running",
    )
    return any(m in lowered for m in markers)


def compile_latex_sync(source: str, filename: str = XF_TEX_INPUT_NAME) -> tuple[bytes, dict[str, Any]]:
    """把单份 `.tex` 源码同步编译为 PDF 字节。

    Args:
        source: 完整 `.tex` 源码（写入容器 workdir 的 `main.tex`）。
        filename: 展示用文件名（argv 固定 `main.tex`，此处仅回填元信息）。

    Returns:
        `(pdf_bytes, meta)`，`meta` 含 `filename` / `pages` / `sizeBytes` /
        `argvAllowlist`。

    Raises:
        XfError: `XF_TEX_SOURCE_INVALID`(400) / `XF_TEX_KERNEL_UNAVAILABLE`(503) /
            `XF_TEX_COMPILE_FAILED`(422) / `XF_TEX_TIMEOUT`(504, 可重试)。
    """
    text = source or ""
    if not text.strip():
        raise _invalid("LaTeX 源码为空")
    if len(text.encode("utf-8")) > XF_TEX_MAX_SOURCE_BYTES:
        raise _invalid(f"LaTeX 源码超过上限 {XF_TEX_MAX_SOURCE_BYTES} 字节")

    # 纵深防御第一层：进入容器**之前**的强制前置校验（规则见模块 docstring）。
    _validate_tex_source(text)

    # 延迟 import：保持本模块在 export 依赖缺失时仍可被 import。
    from ..config import tex_compile_timeout_seconds, tex_image, tex_max_output_bytes
    from ..export.compiler import (
        CompileError,
        CompileTimeout,
        DockerTexCompiler,
        build_xelatex_argv,
        tex_workdir_root_path,
    )
    from ..export.serializer import SerializerError, guard_latex

    # 第二层：沿用导出链路既有静态守卫（数学片段的控制序列 allowlist）。
    try:
        guard_latex(text)
    except SerializerError as e:
        raise _invalid(
            f"源码包含被禁止的 LaTeX 控制序列: {getattr(e, 'message', str(e))}"
        ) from e

    timeout = min(XF_TEX_TIMEOUT_CEILING_SECONDS, max(1, int(tex_compile_timeout_seconds())))
    max_out = int(tex_max_output_bytes())
    image = tex_image()
    argv = build_xelatex_argv(workdir_name=".", passes=2)
    meta: dict[str, Any] = {
        "filename": _safe_filename(filename),
        "pages": 0,
        "sizeBytes": 0,
        "argvAllowlist": argv,
    }

    root = tex_workdir_root_path()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # 工作根不可写 → 内核不可用（环境问题，非用户错误）
        raise _kernel_unavailable(
            f"TeX 工作目录不可用: {type(e).__name__}",
            "检查 RESEARCH_TEX_WORKDIR 指向的目录权限。",
        ) from e

    workdir = Path(tempfile.mkdtemp(prefix="rv-xf-", dir=str(root)))
    # r7-deploy 阶段二修复（eng-deploy）：同 export/service.py —— mkdtemp 0700+root
    # 会导致 tex 容器（nobody:65534）无法读写 workdir，Linux 上编译必挂。
    import os as _os

    if _os.name == "posix":
        _os.chmod(workdir, 0o777)

    gate = _get_compile_gate()
    request_started_at = time.time()
    timed_out = False
    gate.acquire()
    try:
        (workdir / XF_TEX_INPUT_NAME).write_text(text, encoding="utf-8", newline="\n")

        compiler = DockerTexCompiler(timeout_seconds=timeout, max_output_bytes=max_out)
        try:
            res = compiler.compile(workdir=workdir, passes=2)
        except CompileTimeout as e:
            # 超时是瞬时负载类 → 504 可重试（此前误归 422 不可重试，必修 5）。
            timed_out = True
            raise XfError(
                f"编译超时（>{timeout}s）",
                code=XF_TEX_TIMEOUT,
                http_status=504,
                retryable=True,
                detail={"diagnostics": getattr(e, "diagnostics", {}) or {}},
            ) from e
        except (FileNotFoundError, NotADirectoryError) as e:
            # docker 可执行文件缺失等 → 内核不可用。
            raise _kernel_unavailable(
                f"容器运行时不可用（{type(e).__name__}）",
                "安装并启动 Docker，或确认 `docker` 在 PATH。",
            ) from e
        except CompileError as e:
            log = getattr(e, "log", "") or e.message
            if _looks_like_missing_kernel(str(log)):
                timed_out = True  # 未真正产出 PDF，按超时同样清扫本次容器
                raise _kernel_unavailable(
                    f"TeX 镜像不可用: {str(log)[:200]}",
                    f"拉取镜像后重试：docker pull {image}",
                ) from e
            diag = getattr(e, "diagnostics", {}) or {}
            raise XfError(
                e.message,
                code=XF_TEX_COMPILE_FAILED,
                http_status=422,
                detail={"diagnostics": diag} if diag else {},
            ) from e

        pdf_path = workdir / "main.pdf"
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise XfError(
                "编译结束但没有 PDF 产物",
                code=XF_TEX_COMPILE_FAILED,
                http_status=422,
            )
        pdf_bytes = pdf_path.read_bytes()
        meta["pages"] = int(getattr(res, "pages", 0) or 0)
        meta["sizeBytes"] = len(pdf_bytes)
        return pdf_bytes, meta
    finally:
        gate.release()
        shutil.rmtree(workdir, ignore_errors=True)
        # 补偿式清扫：清历史孤儿；超时时连带清本次泄漏的容器（见模块 docstring）。
        try:
            _sweep_orphan_containers(image, request_started_at, remove_started_after=timed_out)
        except Exception as e:  # noqa: BLE001 - 清扫绝不能影响主流程
            logger.warning("xf_tex_orphan_sweep_failed err=%s", type(e).__name__)


def _safe_filename(filename: str) -> str:
    """仅用于回显的展示名：去路径分隔符，避免任何路径语义。"""
    base = str(filename or XF_TEX_INPUT_NAME).replace("\\", "/").split("/")[-1]
    base = "".join(c for c in base if c.isalnum() or c in "._-") or XF_TEX_INPUT_NAME
    return base[:128]


__all__ = [
    "compile_latex_sync",
    "XF_TEX_TIMEOUT_CEILING_SECONDS",
    "_validate_tex_source",
]
