"""RV14 · TeX 编译器（容器隔离 + 可注入本地替身）。

任务书 3：编译只发生在独立容器（non-root / read-only rootfs / no-network
/ CPU·内存·PID 上限）；命令 argv 由 `build_xelatex_argv` 钉死
（-no-shell-escape 永远在列）；超时与输出上限由本模块强制。

`LocalTexCompiler` 是**通用执行骨架**（daemon 线程 + 超时 + 每遍输出
上限检查），runner 可注入 —— 单测用 stub runner 做确定性超时/限额测试，
生产用 `DockerTexCompiler`（docker run + 固定镜像）。
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import (
    tex_compile_timeout_seconds,
    tex_cpus,
    tex_image,
    tex_image_id,
    tex_max_output_bytes,
    tex_memory,
    tex_pids_limit,
    tex_workdir_root,
)
from .diagnostics import parse_compile_log

TexRunner = Callable[..., "CompileResult"]


class CompileError(Exception):
    """编译失败（code 供 JobContract 分类；diagnostics 带结构化诊断）。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        diagnostics: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.diagnostics = diagnostics or {}


class CompileTimeout(CompileError):
    """编译超时（瞬时负载类 → 可重试）。"""

    def __init__(self, message: str = "编译超时") -> None:
        super().__init__("EXPORT_COMPILE_TIMEOUT", message, retryable=True)


@dataclass(frozen=True)
class CompileResult:
    returncode: int
    log: str
    pages: int


def build_xelatex_argv(*, workdir_name: str, passes: int) -> list[str]:
    """单遍 xelatex argv（**allowlist**；passes 由 runner 循环控制）。

    铁律：`-no-shell-escape` 永远在列；本函数是编译命令的唯一来源，
    任何调用方不得在容器里拼接自定义命令。

    文件访问硬化（kpathsea `-cnf-line`）—— **只保留写侧**：

    - `openout_any=p`（写侧，**真有效**）：禁止写绝对路径 / 含 `..` 的路径，
      写操作限制在当前目录。本镜像默认即 `p`，此处**显式钉死**（显式优于
      隐式），以防镜像默认值被放松后写侧失守。

    - **读侧不能靠 kpathsea jail**：本镜像为 TeX Live 2026，其 `texmf.cnf`
      已将 `openin_any` 声明为**永久 no-op**（官方原文："as of 2026,
      openin_any no longer has any effect ... it gave a false sense of
      security"，底层 `kpse_in_name_ok` 恒返回真）。实测：传与不传
      `-cnf-line=openin_any=p`，`\\VerbatimInput{/etc/passwd}` 都能把文件读进
      PDF。故本函数**故意不传** `openin_any` —— 挂一个无效的安全开关只会
      给后来人**虚假的安全感**（比没有更坏）。

    读侧**事实上**的控制是应用层的**输入校验**
    （`core/xf/latex.py::_validate_tex_source`：拒 `^^`、文件读写命令、危险
    宏包、绝对 / `..` 路径）+ read-only rootfs / non-root(65534) / no-network
    容器 + 编译产物金丝雀扫描（`_assert_no_output_leak`，最后一层 detect）。
    这些都不是等价的 TeX 词法器。

    ⚠️ 给后来人指路：**若未来确实需要核层隔离，正确方向是最小化镜像
    （镜像内不含任何敏感文件）+ seccomp/AppArmor 路径白名单**，而不是再去
    kpathsea 里找读侧变量（TL2026 起已无此能力）。
    """
    _ = passes  # 单次 argv 与遍数无关（多遍由 compile() 循环）
    return [
        "xelatex",
        "-no-shell-escape",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-cnf-line=openout_any=p",
        f"-output-directory={workdir_name}",
        "main.tex",
    ]


class LocalTexCompiler:
    """通用编译骨架：daemon 线程限时执行 runner + 输出上限 + 多遍循环。"""

    def __init__(
        self,
        *,
        runner: TexRunner,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
        workdir_name: str = ".",
    ) -> None:
        self._runner = runner
        self._timeout = int(timeout_seconds if timeout_seconds is not None else tex_compile_timeout_seconds())
        self._max_out = int(max_output_bytes if max_output_bytes is not None else tex_max_output_bytes())
        self._workdir_name = workdir_name

    def compile(self, *, workdir: Any, passes: int = 2) -> CompileResult:
        argv = build_xelatex_argv(workdir_name=self._workdir_name, passes=passes)
        last: CompileResult | None = None
        for _p in range(max(1, passes)):
            res = self._run_once(argv, workdir)
            if len(res.log or "") > self._max_out:
                raise CompileError(
                    "EXPORT_OUTPUT_LIMIT",
                    f"编译输出 {len(res.log)} 超过上限 {self._max_out}（可能为失控日志）",
                )
            if res.returncode != 0:
                diag = parse_compile_log(res.log or "")
                raise CompileError(
                    "EXPORT_COMPILE_FAILED",
                    f"xelatex 退出码 {res.returncode}（{len(diag['errors'])} 个错误）",
                    diagnostics=diag,
                )
            last = res
        assert last is not None
        return last

    def _run_once(self, argv: list[str], workdir: Any) -> CompileResult:
        box: dict[str, Any] = {}
        err: list[BaseException] = []

        def _target() -> None:
            try:
                box["res"] = self._runner(
                    argv, workdir=workdir, timeout_seconds=self._timeout, env=None
                )
            except BaseException as e:  # noqa: BLE001 - 原样转抛给调用线程
                err.append(e)

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(self._timeout)
        if t.is_alive():
            # daemon 线程留在后台等其自身超时；本调用立即失败，不再跑第二遍
            raise CompileTimeout(f"单遍编译超过 {self._timeout}s")
        if err:
            raise err[0]
        res = box.get("res")
        if not isinstance(res, CompileResult):
            raise CompileError("EXPORT_COMPILE_FAILED", f"runner 返回类型异常: {type(res)!r}")
        return res


# --------------------------------------------------------------------------
# 生产 runner：docker run（固定镜像）
# --------------------------------------------------------------------------


def _kill_container(name: str) -> None:
    """best-effort 强制移除命名容器（超时/异常兜底清理）。

    `docker run` 前台调用被 `subprocess` 超时 kill 时，只 kill 掉本进程的
    `docker` CLI，**daemon 里的容器仍在运行**（`--rm` 只在容器自身退出时才
    移除）；失控源码（`\\loop\\iftrue\\repeat`）会以 100% CPU 永久驻留，每请求
    泄漏一个。故对**唯一命名**的容器显式 `docker rm -f`。

    清理失败**绝不能**影响主流程：吞掉一切异常，最多记 debug 日志。
    """
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception as e:  # noqa: BLE001 - 清理是兜底，失败不掩盖真实编译结果
        logging.getLogger(__name__).debug(
            "tex_container_cleanup_failed name=%s err=%s", name, type(e).__name__
        )


def _docker_runner(image: str, *, cpus: str, memory: str, pids_limit: str) -> TexRunner:
    """docker run 骨架。隔离项：no-network / read-only rootfs / nobody /
    CPU·内存·PID 上限 / 独立 tmpfs（HOME 字体缓存）。"""

    def run(argv: list[str], *, workdir: Any, timeout_seconds: int, env: dict | None = None) -> CompileResult:
        _ = env  # 容器环境固定，不透传宿主环境变量
        wd = Path(workdir).resolve()
        # 唯一容器名：超时后能对其做**精确** `docker rm -f`（`--rm` 只在容器
        # 退出时才移除，超时容器不会自动消失 → 必须先命名再清理）。
        name = f"mat-arena-tex-{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--network", "none",
            "--read-only",
            "--user", "65534:65534",
            "--cpus", cpus,
            "--memory", memory,
            "--pids-limit", pids_limit,
            "--tmpfs", "/tmp:rw,size=64m,noexec,nosuid",
            "-v", f"{wd}:/work",
            "-w", "/work",
            "-e", "HOME=/tmp",
            image,
            *argv,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout_seconds, check=False
            )
        except subprocess.TimeoutExpired as e:
            # subprocess 超时只 kill `docker` CLI，必须显式清理容器后
            # 才能抛出超时（否则孤儿容器永久驻留）。
            _kill_container(name)
            raise CompileTimeout(f"docker run 超过 {timeout_seconds}s") from e
        finally:
            # 兜底：正常退出时 `--rm` 已移除容器，此调用会失败（check=False 吞掉）；
            # 异常路径则确保清理。清理失败绝不影响主流程。
            _kill_container(name)
        log_path = wd / "main.log"
        log = ""
        if log_path.exists():
            log = log_path.read_text(encoding="utf-8", errors="replace")
        if not log:
            log = (proc.stdout or b"").decode("utf-8", errors="replace")
        # xdvipdfmx/libpng 的 fatal 信息走 **stderr**（如 `libpng error: IDAT:
        # CRC error`）—— 不并入 main.log 的诊断就少了最关键的一行。
        stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-2000:]
        if stderr_tail.strip():
            log = f"{log}\n--- stderr tail ---\n{stderr_tail}"
        pages = _pages_of_pdf(wd / "main.pdf")
        return CompileResult(returncode=int(proc.returncode), log=log, pages=pages)

    return run


def _pages_of_pdf(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(path)) as doc:
            return len(doc)
    except Exception:  # noqa: BLE001 - 页数解析失败不掩盖编译结果
        return 0


# --------------------------------------------------------------------------
# 编译产物金丝雀扫描（纵深防御**最后一层**：detect，不是 isolate）
#
# 背景：读侧无法在核层 jail（见 `build_xelatex_argv` docstring）。应用层的
# 输入校验是 prevent，但它不是等价的 TeX 词法器，可能有未知绕过（宏展开 /
# catcode / 新宏包）。因此对**已产出的 PDF**再做一次文本层金丝雀扫描：命中
# 即拒绝产出，绝不把“疑似含容器内文件内容”的 PDF 交给调用方。
#
# 局限（**如实声明**）：这是启发式检测，**不是**隔离。抽取以可见文本层 +
# （best-effort 解压的）内容流为准；若把内容渲染成图像或以非常规方式编码，
# 可能漏检。它是“网”，不是“墙”——与输入校验（prevent）互补。
# --------------------------------------------------------------------------

#: 窄口径金丝雀：容器常见敏感文件的**特征串**（正常论文几乎不可能出现）。
_LEAK_CANARIES: tuple[str, ...] = (
    "root:x:0:0",       # /etc/passwd
    "daemon:x:1:1",     # /etc/passwd（daemon 行）
    ":/bin/bash",       # /etc/passwd 的 shell 字段
    "/bin/sh",          # shell 路径
    "HOSTNAME=",        # /etc/environment / /proc
    "/etc/os-release",  # 发行版标识（路径被回显时命中）
    "DISTRIB_ID=",      # /etc/os-release 字段（Ubuntu 系）
    "PRETTY_NAME=",     # /etc/os-release 字段（DeB/Ubuntu 通用；覆盖“只打印内容不打印路径”）
    "VERSION_CODENAME=",  # /etc/os-release 字段
)
#: 形如 `/etc/<name>` 的绝对路径（宽口径，覆盖 /etc 下任意文件）。
_ETC_PATH_RE = re.compile(r"/etc/[A-Za-z0-9._-]+")


def _extract_pdf_text(path: Path) -> str:
    """抽取 PDF 可见文本层（**复用已在依赖里的 pypdfium2**，不引入新依赖）。

    失败（PDF 损坏 / 库缺失）返回空串 —— 由 `_pdf_stream_blob` 字节扫描兜底。
    """
    try:
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(str(path)) as doc:
            return "\n".join(
                doc[i].get_textpage().get_text_range() for i in range(len(doc))
            )
    except Exception:  # noqa: BLE001 - 抽取失败不掩盖：交给字节扫描兜底
        return ""


def _pdf_stream_blob(path: Path) -> bytes:
    """原始 PDF 字节 + best-effort 解压所有 `FlateDecode` 流（zlib）。

    `\\VerbatimInput` / `\\lstinputlisting` 会把明文写进内容流；内容流可能被
    Flate 压缩（压缩后正则看不见），故先按原始字节搜，再把能 inflate 的流
    一并解出搜索。解压失败（非 Flate 流 / 损坏）静默跳过。
    """
    raw = path.read_bytes()
    parts: list[bytes] = [raw]
    for m in re.finditer(rb"stream\r?\n", raw):
        start = m.end()
        end = raw.find(b"endstream", start)
        if end == -1:
            continue
        chunk = raw[start:end]
        for wbits in (15, -15):  # zlib 头 / 裸 deflate
            try:
                parts.append(zlib.decompress(chunk, wbits))
                break
            except Exception:  # noqa: BLE001 - 非该格式，换下一种
                continue
    return b"\n".join(parts)


def _scan_pdf_for_leak(path: Path) -> list[str]:
    """返回 PDF 命中的金丝雀列表（去重、稳定排序）；无命中返回 `[]`。"""
    text = _extract_pdf_text(path)
    blob = _pdf_stream_blob(path)
    blob_text = blob.decode("latin-1", "ignore")
    hits: set[str] = set()
    for canary in _LEAK_CANARIES:
        if canary in text or canary.encode("latin-1") in blob:
            hits.add(canary)
    for hay in (text, blob_text):
        hits.update(_ETC_PATH_RE.findall(hay))
    return sorted(hits)


def _assert_no_output_leak(pdf_path: Path) -> None:
    """编译产物 PDF 的金丝雀闸：命中即 `EXPORT_OUTPUT_LEAK_SUSPECT`（拒绝产出）。

    定位：纵深防御**最后一层（detect）**，与服务层输入校验（prevent）互补。
    产物不存在 / 为空时不扫描（空产物由调用方按“无 PDF”另行处理）。

    命中时除了抛异常拒产，还会**显式删除**该 PDF（`unlink(missing_ok=True)`）：
    这是扫描拒绝后的**第二重确保** —— 不再依赖调用方的 `finally` 清 workdir
    （隐式）；即便未来出现新调用方，疑似含容器文件内容的产物也不会留在磁盘上。
    删除是 best-effort：失败不掩盖“拒绝产出”本身。
    """
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return
    hits = _scan_pdf_for_leak(pdf_path)
    if hits:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:
            pass  # 删除是第二重兜底，失败不影响“拒绝产出”
        raise CompileError(
            "EXPORT_OUTPUT_LEAK_SUSPECT",
            f"编译产物 PDF 命中敏感金丝雀 {hits}（疑似容器内文件被读入文本层），已拒绝产出",
        )


class DockerTexCompiler(LocalTexCompiler):
    """生产编译器：固定镜像 + 资源上限 + LocalTexCompiler 通用骨架。

    在 `LocalTexCompiler.compile()` 之上追加一道**产物金丝雀闸**：拿到
    `main.pdf` 后、返回前扫描文本层，命中容器文件特征串即拒绝产出
    （`EXPORT_OUTPUT_LEAK_SUSPECT`）。这是纵深防御的**最后一层 detect**，
    不是隔离（见 `_assert_no_output_leak`）。
    """

    def __init__(
        self,
        *,
        image: str | None = None,
        timeout_seconds: int | None = None,
        max_output_bytes: int | None = None,
    ) -> None:
        self.image = image or tex_image()
        self.image_id = tex_image_id()
        super().__init__(
            runner=_docker_runner(
                self.image, cpus=tex_cpus(), memory=tex_memory(), pids_limit=tex_pids_limit()
            ),
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            workdir_name=".",
        )

    def compile(self, *, workdir: Any, passes: int = 2) -> CompileResult:
        """多遍编译 + 产物金丝雀闸（命中敏感串 → 拒绝产出，不返回 PDF）。"""
        res = super().compile(workdir=workdir, passes=passes)
        _assert_no_output_leak(Path(workdir) / "main.pdf")
        return res


def tex_workdir_root_path() -> Path:
    return Path(tex_workdir_root())
