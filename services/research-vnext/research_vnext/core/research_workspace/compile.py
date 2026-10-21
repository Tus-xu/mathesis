"""RV17 · 项目级真实编译（P0-4，扩展既有 `core/export/compiler.py`）。

链路：`research_project_file` → 临时目录树 → **复用** `DockerTexCompiler`
（镜像 `math-arena-tex:0.1.0`，non-root / read-only / no-net / cpus·mem·pids）
→ PDF 存对象存储 → 解析 `.log` 成结构化 `log_lines`（含 file+line）。

安全（宪法 §53：用户 LaTeX 是不可信输入）
------------------------------------------
进入容器**之前**对每个 `.tex` 做 `validate_project_tex()`。它与 `core/xf/latex.py`
的守卫同源，但**放行** `\\input` / `\\include` 的**相对**路径 —— 多文件是本轮
的硬需求（宪法 §52），而相对引用本身不扩大容器内的读面：绝对路径与 `..`
仍被拒，容器仍 no-net / read-only / non-root，产物仍有金丝雀扫描（最后一层）。

多文件 / images / BibTeX / 中英文（XeLaTeX）
---------------------------------------------
- 多文件：全部文件按项目路径写入 workdir，`\\input{chapters/intro}` 相对解析；
- images / pdf：由 `asset_id` 从对象存储取字节写入；
- BibTeX/Biber：检测 `\\bibliography` / `\\addbibresource` 后，在 xelatex 两遍之间
  用**同一** `_docker_runner`（同隔离参数）跑 `bibtex`/`biber`，再跑两遍 xelatex；
- 中英文：`build_xelatex_argv` 已钉死 XeLaTeX。

编译失败**不得清空 preview**：失败运行的 `pdf_artifact_id` 为空，`latest` 端点
单独返回 `last_successful_pdf_artifact_id`（宪法 §23）。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..api.deps import AccessContext
from ..assets import service as asset_svc
from ..assets.storage import MinioObjectStorage, StorageConfig
from ..config import (
    tex_compile_timeout_seconds,
    tex_cpus,
    tex_image,
    tex_max_output_bytes,
    tex_memory,
    tex_pids_limit,
)
from ..domain.errors import DomainError, NotFound
from ..export.compiler import (
    CompileError,
    CompileTimeout,
    DockerTexCompiler,
    tex_workdir_root_path,
)
from ..xf.latex import (
    _ABS_PATH_RE,
    _BRACE_ARG_RE,
    _DANGEROUS_PACKAGES,
    _HEX_ESCAPE_RE,
    _PACKAGE_RE,
    _PARENT_PATH_RE,
)
from .files import list_files
from .logparse import parse_log_lines
from .models import (
    COMPILE_ERROR,
    COMPILE_FAILED,
    COMPILE_RUNNING,
    COMPILE_SUCCESS,
    COMPILE_TIMEOUT,
    DATA_FILE_KINDS,
    ResearchCompileRunRow,
    utcnow,
)

#: 危险控制序列 —— 与 `xf/latex.py` 同源，但**放行** `\input` / `\include`
#: （多文件必需）。二者仍受「无绝对路径 / 无 `..`」约束（见模块 docstring）。
_DANGEROUS_CMD_NO_INPUT_RE = re.compile(
    r"\\(?:"
    r"@@input|lstinputlisting|VerbatimInput|verbatiminput|inputminted|"
    r"includeonly|ShellEscape|closeout|closein|"
    r"openin|openout|catcode|csname|directlua|luaexec|"
    r"special|write|read|immediate"
    r")(?![A-Za-z])"
)


class TexSourceRejected(DomainError):
    code = "XF_TEX_SOURCE_INVALID"
    http_status = 400


class CompileEntryMissing(DomainError):
    code = "COMPILE_ENTRY_MISSING"
    http_status = 422


class UnsupportedEngine(DomainError):
    """不支持的编译引擎（诚实失败，不静默退回）。"""

    code = "UNSUPPORTED_ENGINE"
    http_status = 422


class CompileInProgress(DomainError):
    """同一入口文件已有编译在跑（并发重复触发）→ 409。

    为什么是 409 而不是"静默再开一个 run"：连点编译会产生**并发**的同项目同入口
    编译，彼此争抢临时目录与产物键；旧行为每次都新建 run，既浪费又让前端拿到
    互相覆盖的结果。这里用 `COMPILE_IN_PROGRESS`（机器可读）让前端能给人话提示
    （「正在编译中，请稍候」）并复用既有 run 继续轮询，不新建、不白屏、不报裸错误。
    """

    code = "COMPILE_IN_PROGRESS"
    http_status = 409


#: 视为"进行中"的编译状态（`queued` 预留给未来的异步排队；当前同步编译只有 `running`）。
_COMPILE_ACTIVE_STATUSES: tuple[str, ...] = ("queued", COMPILE_RUNNING)


def _compile_lock_key(
    tenant_id: uuid.UUID, project_id: uuid.UUID, entry: str
) -> int:
    """`(tenant_id, project_id, entry)` → 64 位有符号 advisory lock 键。

    复用 `research_workspace/agent._idempotency_lock_key` 的同一套做法（sha256 前 8 字节），
    把"同项目同入口"的并发编译请求串行化，避免 check-then-insert 的竞态。
    """
    digest = hashlib.sha256(
        f"{tenant_id}|{project_id}|{entry}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def active_compile_run(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, entry: str
) -> ResearchCompileRunRow | None:
    """该项目入口下**正在运行**的编译 run（无则 None）。"""
    return db.execute(
        select(ResearchCompileRunRow)
        .where(
            ResearchCompileRunRow.tenant_id == tenant_id,
            ResearchCompileRunRow.project_id == project_id,
            ResearchCompileRunRow.entry == entry,
            ResearchCompileRunRow.status.in_(_COMPILE_ACTIVE_STATUSES),
        )
        .order_by(ResearchCompileRunRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def validate_project_tex(text: str, *, filename: str = "") -> None:
    """项目 `.tex` 编译前守卫（放行相对 `\\input`/`\\include`）。命中即 400。"""
    src = text or ""
    if _HEX_ESCAPE_RE.search(src):
        raise TexSourceRejected(f"{filename}: 拒绝 TeX catcode 十六进制转义 '^^'")
    m = _DANGEROUS_CMD_NO_INPUT_RE.search(src)
    if m is not None:
        raise TexSourceRejected(f"{filename}: 拒绝控制序列 {m.group(0)!r}（文件读写/脚本执行类）")
    for pm in _PACKAGE_RE.finditer(src):
        names = {n.strip().lower() for n in pm.group(1).split(",") if n.strip()}
        bad = names & _DANGEROUS_PACKAGES
        if bad:
            raise TexSourceRejected(f"{filename}: 拒绝宏包 {sorted(bad)[0]!r}")
    for bm in _BRACE_ARG_RE.finditer(src):
        arg = bm.group(1)
        if _PARENT_PATH_RE.search(arg):
            raise TexSourceRejected(f"{filename}: 参数中包含上跳路径 '..'")
        if _ABS_PATH_RE.search(arg):
            raise TexSourceRejected(f"{filename}: 参数中包含绝对路径")


def compile_object_key(prefix: str, tenant_id: str, run_id: str) -> str:
    return f"{prefix}{tenant_id}/project-compile/{run_id}/main.pdf"


def _storage() -> MinioObjectStorage:
    return MinioObjectStorage(StorageConfig.from_env())


def _materialize(
    workdir: Path, rows, *, entry: str, db: Session, storage: MinioObjectStorage
) -> str:
    """把项目文件写成 workdir 目录树。返回入口内容（供 main.tex 落盘）。

    入口文件名固定 `main.tex`（`build_xelatex_argv` 钉死）；若 `entry != main.tex`，
    入口内容**另存**一份为 `main.tex`，其余文件按项目路径写入。
    """
    entry_content = ""
    for row in rows:
        target = workdir / row.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if row.kind in DATA_FILE_KINDS and row.asset_id is not None:
            asset = asset_svc.get_asset(db, row.asset_id)
            if asset is None:
                raise CompileEntryMissing(f"文件 {row.path} 引用的 asset 不存在: {row.asset_id}")
            target.write_bytes(storage.get_bytes(asset.storage_key))
        else:
            content = row.content or ""
            if row.path == entry:
                entry_content = content
            target.write_text(content, encoding="utf-8", newline="\n")
    if entry_content and entry != "main.tex":
        (workdir / "main.tex").write_text(entry_content, encoding="utf-8", newline="\n")
    return entry_content


def _read_log(workdir: Path) -> str:
    p = workdir / "main.log"
    try:
        return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    except OSError:
        return ""


def _detect_bib_tool(entry_tex: str, rows) -> str:
    has_bib = any(str(r.path).endswith(".bib") for r in rows)
    uses_biblatex = "\\addbibresource" in entry_tex or "biblatex" in entry_tex
    uses_bibtex = "\\bibliography" in entry_tex
    if uses_biblatex and has_bib:
        return "biber"
    if uses_bibtex and has_bib:
        return "bibtex"
    return ""


def _run_aux_tool(workdir: Path, tool: str, *, timeout: int):
    """用**同一**隔离 runner 跑 bibtex/biber。

    复用 `core/export/compiler._docker_runner`（唯一隔离来源，避免第二套 flag）。
    先删除 `main.log`，否则 runner 会把上一遍 xelatex 的旧日志当成本次输出。
    """
    from ..export.compiler import _docker_runner

    (workdir / "main.log").unlink(missing_ok=True)
    runner = _docker_runner(
        tex_image(), cpus=tex_cpus(), memory=tex_memory(), pids_limit=tex_pids_limit()
    )
    return runner([tool, "main"], workdir=workdir, timeout_seconds=timeout, env=None)


def run_compile(
    workdir: Path, *, engine: str, entry: str, bib_tool: str
) -> tuple[str, str, str, str, int, int, str]:
    """在 workdir 内执行编译。

    Returns:
        `(status, raw_log, final_log, error_code, pages, exit_code, error_message)`。

    `raw_log` 是**各遍拼接**（诊断用，含第一遍 xelatex、bibtex、末遍 xelatex）；
    `final_log` 是**末遍 xelatex** 的日志（用户可见的 log_lines 只解析它）。

    为什么区分：BibTeX 生效前，第一遍 xelatex 会报 `Citation ... undefined`，
    而末遍（bibtex 之后）已解析成功——若解析拼接日志，UI 会看到一条**已被取代
    的**误导性警告。只解析末遍，warning 才反映**最终产物状态**。失败路径下
    `final_log` 为空，调用方回退到 `raw_log`（失败日志本身即末遍）。
    """
    timeout = max(1, int(tex_compile_timeout_seconds()))
    max_out = int(tex_max_output_bytes())
    log_parts: list[str] = []
    pages = 0
    exit_code = 0

    compiler = DockerTexCompiler(timeout_seconds=timeout, max_output_bytes=max_out)
    try:
        res1 = compiler.compile(workdir=workdir, passes=1)
        log_parts.append(res1.log or "")
        if bib_tool:
            bres = _run_aux_tool(workdir, bib_tool, timeout=timeout)
            log_parts.append(f"--- {bib_tool} (rc={bres.returncode}) ---\n{bres.log or ''}")
        res2 = compiler.compile(workdir=workdir, passes=2)
        log_parts.append(res2.log or "")
        pages = int(res2.pages or 0)
        raw = "\n".join(log_parts)
        final_log = res2.log or ""
        if not (workdir / "main.pdf").exists() or (workdir / "main.pdf").stat().st_size == 0:
            return COMPILE_FAILED, raw, final_log, "COMPILE_NO_PDF", 0, 1, "编译结束但没有 PDF 产物"
        return COMPILE_SUCCESS, raw, final_log, "", pages, 0, ""
    except CompileTimeout as e:
        log_parts.append(_read_log(workdir))
        return COMPILE_TIMEOUT, "\n".join(log_parts), "", "EXPORT_COMPILE_TIMEOUT", 0, 124, str(e)
    except CompileError as e:
        log_parts.append(_read_log(workdir))
        raw = "\n".join(log_parts)
        return COMPILE_FAILED, raw, "", getattr(e, "code", "EXPORT_COMPILE_FAILED"), 0, 1, e.message


def _dedupe_log_lines(lines: list[dict]) -> list[dict]:
    """去掉跨 pass 的**完全重复**行（同 level/text/file/line），保留首次出现。

    多遍 xelatex 会让同一条 warning 出现多次；UI 上重复只增噪。
    错误一旦真实存在，重复行本就同源，去重不影响可定位性。
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    for ln in lines:
        key = (ln.get("level"), ln.get("text"), ln.get("file"), ln.get("line"))
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def compile_project(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    engine: str = "xelatex",
    entry: str = "main.tex",
) -> ResearchCompileRunRow:
    """项目级编译：建 run → 组装目录树 → 编译 → 存 PDF → 解析日志。"""
    if (engine or "xelatex") not in ("xelatex",):
        raise UnsupportedEngine(f"不支持的引擎: {engine!r}（当前仅 xelatex）")
    rows = list_files(db, tenant_id=ctx.tenant_id, project_id=project_id)
    if not rows:
        raise CompileEntryMissing("项目没有草稿文件，无法编译")

    paths = {r.path for r in rows}
    if entry not in paths:
        raise CompileEntryMissing(f"入口文件不存在: {entry}")

    # 守卫：所有 .tex 文件在进入容器前校验。
    for r in rows:
        if r.kind == "tex":
            validate_project_tex(r.content or "", filename=r.path)

    entry_tex = next((r.content or "" for r in rows if r.path == entry), "")
    bib_tool = _detect_bib_tool(entry_tex, rows)

    # 并发编译去重（§31/§54 同形幂等）：同一 (tenant, project, entry) 用事务级
    # advisory lock 串行化，命中"进行中"的 run 就**不新建**、直接 409。
    # 为什么先取锁再查：check-then-insert 在并发下有竞态（两个请求同时查空、同时建 run）。
    db.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": _compile_lock_key(ctx.tenant_id, project_id, entry)},
    )
    running = active_compile_run(
        db, tenant_id=ctx.tenant_id, project_id=project_id, entry=entry
    )
    if running is not None:
        raise CompileInProgress(
            "该入口文件已有编译正在进行，请等待本次编译完成后再试。",
            run_id=str(running.id),
            status=running.status,
        )

    storage = _storage()
    now = utcnow()
    run = ResearchCompileRunRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        actor_id=ctx.actor_id,
        engine=engine or "xelatex",
        entry=entry,
        status=COMPILE_RUNNING,
        created_at=now,
    )
    db.add(run)
    db.flush()
    # **立即提交**：让本次 run 的 `running` 态对其它请求可见（否则它要等整个编译结束
    # 才提交，并发请求在锁释放后仍看不到它）。提交同时释放 advisory lock，后到的请求
    # 拿到锁后会命中上面的 `active_compile_run` → 409。
    db.commit()

    root = tex_workdir_root_path()
    root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="rv17-", dir=str(root)))
    # r7-deploy 阶段二修复（eng-deploy）：同 export/service.py —— mkdtemp 0700+root
    # 会导致 tex 容器（nobody:65534）无法读写 workdir，Linux 上编译必挂。
    import os as _os

    if _os.name == "posix":
        _os.chmod(workdir, 0o777)
    started = time.monotonic()
    try:
        _materialize(workdir, rows, entry=entry, db=db, storage=storage)
        status, raw_log, final_log, error_code, pages, exit_code, error_message = run_compile(
            workdir, engine=engine, entry=entry, bib_tool=bib_tool
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        # 只解析**末遍 xelatex** 日志：bibtex 生效后被取代的第一遍警告（如
        # `Citation ... undefined`）不应出现在最终结果里（避免误导性警告）。
        # `final_log` 为空（失败/超时路径）时回退到拼接日志。
        log_lines, _raw_error_count, _raw_warning_count = parse_log_lines(
            final_log or raw_log, entry=entry, base_ts=run.created_at
        )
        log_lines = _dedupe_log_lines(log_lines)
        error_count = sum(1 for ln in log_lines if ln.get("level") == "error")
        warning_count = sum(1 for ln in log_lines if ln.get("level") == "warning")

        pdf_path = workdir / "main.pdf"
        if status == COMPILE_SUCCESS and pdf_path.exists() and pdf_path.stat().st_size > 0:
            pdf_bytes = pdf_path.read_bytes()
            artifact_id = uuid.uuid4()
            key = compile_object_key(storage.cfg.prefix, str(ctx.tenant_id), str(run.id))
            storage.put_bytes(key, pdf_bytes)
            run.pdf_artifact_id = artifact_id
            run.pdf_storage_key = key
            run.pdf_size_bytes = len(pdf_bytes)

        run.status = status
        run.page_count = int(pages)
        run.duration_ms = duration_ms
        run.error_count = int(error_count)
        run.warning_count = int(warning_count)
        run.exit_code = int(exit_code)
        run.log_lines = log_lines
        run.diagnostics = {
            "bib_tool": bib_tool,
            "log_char_count": len(raw_log),
            "engine": run.engine,
            "entry": entry,
        }
        run.error_code = error_code
        run.error_message = (error_message or "")[:2000]
        run.finished_at = utcnow()
        db.flush()
        return run
    except Exception:
        # 意外异常（非 `run_compile` 的失败返回）：run 已在上面提交过，**必须**把它
        # 落到终态，否则它会永远停在 `running`，后续编译被永久 409，谁也没法再编。
        run.status = COMPILE_ERROR
        run.error_code = "COMPILE_INTERNAL_ERROR"
        run.error_message = "编译过程中出现内部错误，请重试。"
        run.finished_at = utcnow()
        try:
            db.flush()
            db.commit()
        except Exception:  # noqa: BLE001 - 兜底提交失败也不掩盖原始异常
            db.rollback()
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_public_dict(row: ResearchCompileRunRow) -> dict:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "engine": row.engine,
        "entry": row.entry,
        "status": row.status,
        "pdf_artifact_id": str(row.pdf_artifact_id) if row.pdf_artifact_id else None,
        "pdf_available": row.pdf_artifact_id is not None,
        "page_count": int(row.page_count or 0),
        "pdf_size_bytes": int(row.pdf_size_bytes or 0),
        "duration_ms": int(row.duration_ms or 0),
        "error_count": int(row.error_count or 0),
        "warning_count": int(row.warning_count or 0),
        "exit_code": int(row.exit_code or 0),
        "log_lines": list(row.log_lines or []),
        "diagnostics": dict(row.diagnostics or {}),
        "error_code": row.error_code or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def get_run(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID, run_id: uuid.UUID
) -> ResearchCompileRunRow:
    row = db.get(ResearchCompileRunRow, run_id)
    if row is None or row.tenant_id != tenant_id or row.project_id != project_id:
        raise NotFound(f"编译运行不存在: {run_id}")
    return row


def latest_run(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> ResearchCompileRunRow | None:
    return db.execute(
        select(ResearchCompileRunRow)
        .where(
            ResearchCompileRunRow.tenant_id == tenant_id,
            ResearchCompileRunRow.project_id == project_id,
        )
        .order_by(ResearchCompileRunRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def last_successful_pdf_artifact_id(
    db: Session, *, tenant_id: uuid.UUID, project_id: uuid.UUID
) -> str | None:
    row = db.execute(
        select(ResearchCompileRunRow)
        .where(
            ResearchCompileRunRow.tenant_id == tenant_id,
            ResearchCompileRunRow.project_id == project_id,
            ResearchCompileRunRow.status == COMPILE_SUCCESS,
            ResearchCompileRunRow.pdf_artifact_id.is_not(None),
        )
        .order_by(ResearchCompileRunRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(row.pdf_artifact_id) if row is not None and row.pdf_artifact_id else None


def read_run_pdf(db: Session, row: ResearchCompileRunRow) -> bytes | None:
    if not row.pdf_storage_key:
        return None
    return _storage().get_bytes(row.pdf_storage_key)


__all__ = [
    "CompileEntryMissing",
    "CompileInProgress",
    "TexSourceRejected",
    "UnsupportedEngine",
    "active_compile_run",
    "compile_project",
    "get_run",
    "last_successful_pdf_artifact_id",
    "latest_run",
    "read_run_pdf",
    "run_public_dict",
    "validate_project_tex",
]
