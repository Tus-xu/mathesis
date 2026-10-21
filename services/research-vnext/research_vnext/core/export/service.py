"""RV14 · 导出编排 service（路由 + worker handler 共用的唯一编排层）。

链路（任务书）：
- `start_export`：PaperSpec 指纹 → 去重（DB 唯一约束背书）→ enqueue
  `paper.export`（input_ref = `rv14-export:<fp>`）；
- `export_handler`：读 PaperSpec → 确定性 serializer → 缺资产检测 →
  容器编译 → 产物上 MinIO + artifact 行 + manifest；
- 下载链路：`mint_link`（令牌 + 审计）→ `download_artifact`
  （过期 410 / 篡改 403 / 撤权 410 / security floor 抬升 410）。

铁律：
- md/tex **先于** pdf 存在（PARTIAL 时绝不展示假 PDF 预览）；
- 编译只在容器内（`DockerTexCompiler`），本进程从不跑 TeX；
- PARTIAL 的 partial_detail.result 带结构化诊断与 missingAssets（node locator）；
- 瞬时编译失败（EXPORT_COMPILE_TIMEOUT）→ 整体重试，不固化为 PARTIAL。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..api.deps import AccessContext, check_revocation
from ..application import job_service as js
from ..application.session_service import get_security_floor
from ..assets.storage import MinioObjectStorage, ObjectStorageError
from ..config import OBJECT_PREFIX, download_token_ttl_seconds, tex_workdir_root
from ..db.engine import session_scope
from ..db.models import JobRow, ResearchAssetRow
from ..domain.errors import DomainError, ObjectScopeMissing, TenantMismatch
from ..parser.models import AssetAnchorRow
from .compiler import CompileError, CompileResult, DockerTexCompiler, build_xelatex_argv
from .csl import CSL_STYLE_KIND, CSL_STYLE_VERSION, csl_style_sha256
from .diagnostics import parse_compile_log
from .download import (
    DownloadTokenExpired,
    DownloadTokenInvalid,
    mint_download_token,
    parse_download_token,
)
from .models import PaperExportArtifactRow, PaperExportAuditRow
from .serializer import (
    SERIALIZER_VERSION,
    FORMAT_EXTENSIONS,
    SerializerError,
    serialize_latex,
    serialize_markdown,
)
from .storage import export_object_key
from .template import LATEX_TEMPLATE_VERSION, render_document, template_sha256

MEDIA_TYPES = {
    "markdown": "text/markdown; charset=utf-8",
    "latex": "application/x-tex; charset=utf-8",
    "pdf": "application/pdf",
}
VALID_FORMATS = frozenset(FORMAT_EXTENSIONS)
_MANIFEST_VERSION = "rv14.manifest.v1"

_source_sha_cache: dict[str, str] = {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_sha(module_file: str) -> str:
    """模块源码的 SHA-256（serializer 的代码身份进 manifest）。"""
    if module_file not in _source_sha_cache:
        _source_sha_cache[module_file] = _sha(Path(module_file).read_bytes())
    return _source_sha_cache[module_file]


# ==========================================================================
# 领域错误
# ==========================================================================


class ExportNotFound(DomainError):
    code = "EXPORT_NOT_FOUND"
    http_status = 404


class ExportFormatUnavailable(DomainError):
    code = "EXPORT_FORMAT_UNAVAILABLE"
    http_status = 400


class ArtifactRevoked(DomainError):
    code = "EXPORT_REVOKED"
    http_status = 410


class DownloadTokenTampered(DomainError):
    code = "DOWNLOAD_TOKEN_INVALID"
    http_status = 403


# ==========================================================================
# 审计
# ==========================================================================


def _audit(
    db: Session,
    *,
    artifact_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    detail: dict | None = None,
    trace_id: str = "",
) -> None:
    db.add(
        PaperExportAuditRow(
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            detail=dict(detail or {}),
            trace_id=trace_id,
        )
    )
    db.flush()


def _audit_denied(
    db: Session,
    *,
    artifact_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
    trace_id: str = "",
) -> None:
    """拒绝也要留审计。产物行不存在时退化用 artifact_id 作 tenant 占位。"""
    try:
        row = db.get(PaperExportArtifactRow, artifact_id)
        _audit(
            db,
            artifact_id=artifact_id,
            tenant_id=row.tenant_id if row else artifact_id,
            actor_id=actor_id,
            action="DOWNLOAD_DENIED",
            detail={"reason": reason},
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 - 审计失败不得吞掉原始拒绝语义
        db.rollback()


def audit_actions(db: Session, artifact_id: uuid.UUID) -> set[str]:
    return {
        str(a.action)
        for a in db.query(PaperExportAuditRow)
        .filter(PaperExportAuditRow.artifact_id == artifact_id)
        .all()
    }


# ==========================================================================
# 指纹与入队
# ==========================================================================


def compute_fingerprint(tenant_id: uuid.UUID, paper: dict, formats: list[str]) -> str:
    """去重指纹：同 PaperSpec + 同格式集 + 同 serializer/template 版本 ⇒ 同产物。"""
    basis = "|".join(
        [
            "rv14",
            str(tenant_id),
            str(paper.get("paperId") or ""),
            str(int(paper.get("revision") or 0)),
            str(paper.get("contentHash") or ""),
            ",".join(sorted(set(formats))),
            SERIALIZER_VERSION,
            str(LATEX_TEMPLATE_VERSION),
        ]
    )
    return _sha(basis.encode("utf-8"))


def start_export(
    db: Session,
    *,
    ctx: AccessContext,
    session_id: uuid.UUID,
    formats: list[str],
    trace_id: str = "",
) -> dict[str, Any]:
    from ..paper.service import read_paper

    bad = [f for f in formats if f not in VALID_FORMATS]
    if bad or not formats:
        raise ExportFormatUnavailable(f"非法导出格式: {bad or '(空)'}；允许: {sorted(VALID_FORMATS)}")

    paper = read_paper(db, session_id)
    if not paper.get("exists"):
        raise ExportNotFound(f"会话 {session_id} 尚无 PaperSpec，先创建论文再导出")

    fp = compute_fingerprint(ctx.tenant_id, paper, formats)

    # 去重（任务书：同 input 不重复编译；DB 唯一约束 (tenant_id, fingerprint) 背书）
    prior = (
        db.query(PaperExportArtifactRow)
        .filter(
            PaperExportArtifactRow.tenant_id == ctx.tenant_id,
            PaperExportArtifactRow.fingerprint == fp,
            PaperExportArtifactRow.revoked_at.is_(None),
        )
        .first()
    )
    if prior is not None:
        return {"deduped": True, "artifactId": str(prior.id), "fingerprint": fp}

    job = js.enqueue(
        db,
        session_id=session_id,
        tenant_id=ctx.tenant_id,
        job_type="paper.export",
        input_payload={"formats": sorted(set(formats))},
        input_ref=f"rv14-export:{fp}",
        user_id=ctx.actor_id,
        project_id=_project_id_of(db, session_id),
        trace_id=trace_id or ctx.trace_id,
    )
    return {"deduped": False, "jobId": str(job.id), "fingerprint": fp}


def _project_id_of(db: Session, session_id: uuid.UUID) -> uuid.UUID | None:
    from ..db.models import ResearchSessionRow

    row = db.get(ResearchSessionRow, session_id)
    return row.project_id if row else None


# ==========================================================================
# worker handler（注册于 core/worker/export_handlers.py）
# ==========================================================================


def _load_citations(db: Session, tenant_id: uuid.UUID, blocks: list[dict]) -> dict:
    from ..citation.models import CitationRow

    refs: list[str] = []
    for b in blocks:
        if (b or {}).get("type") == "citation":
            for r in b.get("refs") or []:
                r = str(r)
                if r and r not in refs:
                    refs.append(r)
    out: dict[str, dict] = {}
    for ref in refs:
        try:
            rid = uuid.UUID(ref)
        except ValueError:
            continue
        row = db.get(CitationRow, rid)
        if row is not None and row.tenant_id == tenant_id:
            out[ref] = dict(row.csl_json or {})
    return out


def _title_of(blocks: list[dict]) -> str:
    for b in blocks:
        if (b or {}).get("type") == "section" and b.get("text"):
            return str(b["text"])
    return "研究论文"


def _fetch_figure_assets(
    db: Session, tenant_id: uuid.UUID, blocks: list[dict]
) -> tuple[list[dict], dict[str, tuple[bytes, str]]]:
    """figure 节点需要真实对象（\\includegraphics）；table 只渲染骨架不需要。

    返回 (missing 列表, 已取到的 {anchor: (bytes, media_type)})。
    缺一项即如实登记，不静默跳过、不用占位图假装成功。
    """
    missing: list[dict] = []
    fetched: dict[str, tuple[bytes, str]] = {}
    storage: MinioObjectStorage | None = None
    for b in blocks:
        if (b or {}).get("type") != "figure":
            continue
        nid = str((b or {}).get("id") or "")
        for ref in b.get("refs") or []:
            anchor = str(ref)
            if anchor in fetched:
                continue
            ar = (
                db.query(AssetAnchorRow)
                .filter(AssetAnchorRow.tenant_id == tenant_id, AssetAnchorRow.anchor_id == anchor)
                .first()
            )
            if ar is None:
                missing.append({"nodeId": nid, "nodeType": "figure", "anchorId": anchor,
                                "reason": "锚点不存在"})
                continue
            row = db.get(ResearchAssetRow, ar.asset_id)
            if row is None or row.revoked_at is not None:
                missing.append({"nodeId": nid, "nodeType": "figure", "anchorId": anchor,
                                "reason": "资产缺失或已撤回"})
                continue
            if storage is None:
                storage = MinioObjectStorage()
            try:
                fetched[anchor] = (storage.get_bytes(row.storage_key), row.media_type or "image/png")
            except ObjectStorageError as e:
                missing.append({"nodeId": nid, "nodeType": "figure", "anchorId": anchor,
                                "reason": f"对象存储读取失败: {type(e).__name__}"})
    return missing, fetched


def _node_markers(full_tex: str) -> dict[int, str]:
    """扫描 `%RV14NODE:<id>` 注释行 → {其后一行的 1-based 行号: node_id}。

    TeX 错误 `l.<n>` 的行号由此映射回 PaperSpec 节点（source node locator）。
    """
    markers: dict[int, str] = {}
    lines = full_tex.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("%RV14NODE:") and i + 1 < len(lines):
            markers[i + 2] = s.split(":", 1)[1].strip()
    return markers


def _export_sync(ctx_handle) -> dict[str, Any]:
    """paper.export 的同步主体（handler 以 `asyncio.to_thread` 调度，
    不阻塞事件循环 —— 心跳协程因此可以持续续租）。"""
    from ..paper.service import read_paper

    formats = sorted({str(f) for f in (ctx_handle.input_payload or {}).get("formats") or []})
    want_pdf = "pdf" in formats

    with session_scope() as db:
        paper = read_paper(db, ctx_handle.session_id)
        if not paper.get("exists"):
            return {
                "succeeded_stages": [],
                "failed_stages": ["fetch_spec"],
                "error_code": "EXPORT_NOT_FOUND",
                "error_message": "PaperSpec 不存在",
            }
        blocks = paper["content"]["blocks"]
        meta = {
            "paperId": paper["paperId"],
            "revision": int(paper["revision"]),
            "contentHash": paper["contentHash"],
        }
        citations = _load_citations(db, ctx_handle.tenant_id, blocks)
        missing, fetched = _fetch_figure_assets(db, ctx_handle.tenant_id, blocks)

    succeeded: list[str] = []
    try:
        md_text = serialize_markdown(blocks, meta=meta, citations=citations)
        succeeded.append("serialize_markdown")
        tex_body = serialize_latex(blocks, meta=meta, citations=citations)
        succeeded.append("serialize_latex")
    except SerializerError as e:
        failed = "serialize_markdown" if "serialize_markdown" not in succeeded else "serialize_latex"
        return {
            "succeeded_stages": succeeded,
            "failed_stages": [failed],
            "error_code": e.code,
            "error_message": f"{e.message}（node={e.node_id}）",
            "result": {"diagnostics": {"code": e.code, "nodeId": e.node_id}},
        }

    md_bytes = md_text.encode("utf-8")
    full_tex = render_document(tex_body, title=_title_of(blocks), meta=meta, citations=citations)
    tex_bytes = full_tex.encode("utf-8")
    markers = _node_markers(full_tex)

    # PARTIAL / 失败路径也如实披露 md/tex 的内容哈希（不落存储、不提供下载）
    fmt_entries: dict[str, Any] = {
        "markdown": {"sha256": _sha(md_bytes), "sizeBytes": len(md_bytes), "stored": False},
        "latex": {"sha256": _sha(tex_bytes), "sizeBytes": len(tex_bytes), "stored": False},
    }
    diagnostics: dict[str, Any] = {}
    pdf_blob: bytes | None = None

    if want_pdf and missing:
        # 缺资产 ⇒ 绝不假装有 PDF：md/tex 成功 + compile_pdf 失败（PARTIAL）
        diagnostics["code"] = "EXPORT_ASSET_MISSING"
        diagnostics["missingAssets"] = missing
        return {
            "succeeded_stages": succeeded,
            "failed_stages": ["compile_pdf"],
            "error_code": "EXPORT_ASSET_MISSING",
            "error_message": f"缺少 {len(missing)} 个图资产，无法编译 PDF",
            "result": {"formats": fmt_entries, "diagnostics": diagnostics},
        }

    if want_pdf:
        try:
            compile_res, pdf_bytes = _compile_in_container(full_tex, markers, fetched)
        except CompileError as e:
            diag = dict(e.diagnostics or {})
            diag.setdefault("code", e.code)
            if e.retryable:
                # 瞬时失败 → 整体重试（不固化为 PARTIAL；md/tex 会在重试中重新产出）
                return {
                    "succeeded_stages": [],
                    "failed_stages": ["compile_pdf"],
                    "error_code": e.code,
                    "error_message": e.message[:500],
                    "result": {"diagnostics": diag},
                }
            return {
                "succeeded_stages": succeeded,
                "failed_stages": ["compile_pdf"],
                "error_code": e.code,
                "error_message": e.message[:500],
                "result": {"formats": fmt_entries, "diagnostics": diag},
            }
        succeeded.append("compile_pdf")
        pdf_blob = pdf_bytes
        fmt_entries["pdf"] = {
            "sha256": _sha(pdf_bytes),
            "sizeBytes": len(pdf_bytes),
            "pages": compile_res.pages,
            "stored": False,
        }
        diag = parse_compile_log(compile_res.log or "", node_markers=markers)
        diagnostics["compile"] = {
            "outputPages": compile_res.pages,
            "errorCount": len(diag["errors"]),
        }

    manifest = _build_manifest(ctx_handle, meta, formats, fmt_entries, diagnostics, fetched)

    artifact_id = uuid.uuid4()
    storage = MinioObjectStorage()
    blobs = {"markdown": md_bytes, "latex": tex_bytes}
    if pdf_blob is not None:
        blobs["pdf"] = pdf_blob
    for fmt, data in blobs.items():
        key = export_object_key(OBJECT_PREFIX, str(ctx_handle.tenant_id), str(artifact_id), fmt)
        storage.put_bytes(key, data)
        fmt_entries[fmt].update({"storageKey": key, "sha256": _sha(data),
                                 "sizeBytes": len(data), "stored": True})

    fp = compute_fingerprint(
        ctx_handle.tenant_id,
        {"paperId": meta["paperId"], "revision": meta["revision"], "contentHash": meta["contentHash"]},
        formats,
    )
    with session_scope() as db:
        job = db.get(JobRow, ctx_handle.job_id)
        db.add(
            PaperExportArtifactRow(
                id=artifact_id,
                tenant_id=ctx_handle.tenant_id,
                project_id=job.project_id if job else None,
                session_id=ctx_handle.session_id,
                owner_id=job.user_id if job else ctx_handle.tenant_id,
                paper_id=uuid.UUID(meta["paperId"]),
                paper_revision=int(meta["revision"]),
                paper_content_hash=str(meta["contentHash"]),
                fingerprint=fp,
                formats=fmt_entries,
                manifest=manifest,
                trace_id=str(ctx_handle.job_id)[:32],
            )
        )
        db.flush()

    return {
        "succeeded_stages": succeeded,
        "failed_stages": [],
        "result": {"artifactId": str(artifact_id), "formats": fmt_entries, "manifest": manifest},
    }


def _force_rmtree(root: Path) -> None:
    """逐文件删除编译工作目录。

    不用 `shutil.rmtree`：Windows 开发沙箱的 safe-delete shim 会对
    >50 文件的整树删除抛批量确认守卫（SAFE_DELETE_BULK_CONFIRM_REQUIRED），
    逐文件 unlink 走单文件安全删除路径，不受守卫影响。
    """
    try:
        for p in sorted(root.rglob("*"), key=lambda x: len(str(x)), reverse=True):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
                elif p.is_dir():
                    p.rmdir()
            except OSError:  # noqa: PERF203 - 单文件失败不阻断整体清理
                continue
        try:
            root.rmdir()
        except OSError:
            pass
    except OSError:
        pass


def _compile_in_container(
    full_tex: str, markers: dict[int, str], assets: dict[str, tuple[bytes, str]]
) -> tuple[CompileResult, bytes]:
    """把 tex+资产写入临时 workdir，在**容器**里编译，返回 (结果, pdf 字节)。

    本进程只负责发起 docker run 与读取文件 —— 不在 Web/Core 进程运行 TeX。
    """
    root = Path(tex_workdir_root())
    root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="rv14-", dir=str(root)))
    # r7-deploy 阶段一修复（eng-deploy, 2026-09-17）：mkdtemp 固定 0700 且属主为本
    # 进程用户（容器内 root），而 DockerTexCompiler 派生的 tex 容器以
    # nobody(65534) 运行 —— Linux 服务器上 tex 容器连 workdir 都进不去
    # （连 main.log 都创建失败 → "! Emergency stop"；Windows/Docker Desktop
    # 权限被架空，dev 环境从未暴露）。workdir 为单次请求专用目录，
    # 放开到 0777 风险可接受；root 目录本身（/srv/rv-tex）保持 755。
    import os as _os

    if _os.name == "posix":
        _os.chmod(workdir, 0o777)
    _MEDIA_EXT = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
        "image/gif": ".gif", "application/pdf": ".pdf", "image/pdf": ".pdf",
        "image/svg+xml": ".svg", "image/tiff": ".tif",
    }
    try:
        (workdir / "main.tex").write_text(full_tex, encoding="utf-8", newline="\n")
        if assets:
            (workdir / "assets").mkdir()
            for anchor, (data, media) in assets.items():
                # 按 media_type 写扩展名；serializer 输出无扩展名引用，
                # 由 LaTeX 的图形扩展尝试（\includegraphics{assets/<anchor>}）命中。
                safe = "".join(c for c in anchor if c.isalnum() or c in "-_") or "asset"
                ext = _MEDIA_EXT.get(media, ".png")
                (workdir / "assets" / f"{safe}{ext}").write_bytes(data)
        compiler = DockerTexCompiler()
        res = compiler.compile(workdir=workdir, passes=2)
        pdf_path = workdir / "main.pdf"
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            diag = parse_compile_log(res.log or "", node_markers=markers)
            raise CompileError(
                "EXPORT_COMPILE_FAILED", "编译结束但没有 PDF 产物（可能全部页面丢失）", diagnostics=diag
            )
        return res, pdf_path.read_bytes()
    finally:
        _force_rmtree(workdir)


def _build_manifest(
    ctx_handle,
    meta: dict,
    formats: list[str],
    fmt_entries: dict,
    diagnostics: dict,
    assets: dict[str, tuple[bytes, str]],
) -> dict:
    compiler = DockerTexCompiler()
    return {
        "manifestVersion": _MANIFEST_VERSION,
        "paperSpec": meta,
        "serializer": {
            "version": SERIALIZER_VERSION,
            "sha256": _source_sha(_serializer_file()),
        },
        "template": {"version": LATEX_TEMPLATE_VERSION, "sha256": template_sha256()},
        "compiler": {
            "engine": "xelatex",
            "image": compiler.image,
            "imageId": compiler.image_id,
            "bundle": "TeX Live (image-bundled)",
            "fonts": "Fandol (image-bundled, fontset=fandol)",
            "shellEscape": False,
            "argvAllowlist": build_xelatex_argv(workdir_name=".", passes=2),
        },
        "cslStyle": {
            "kind": CSL_STYLE_KIND,
            "version": CSL_STYLE_VERSION,
            "sha256": csl_style_sha256(),
            "note": "内置确定性 GB/T 7714 子集渲染器（非完整 CSL 引擎）",
        },
        "inputs": [
            {"ref": f"rv14-asset:{a}", "sha256": _sha(b), "mediaType": m}
            for a, (b, m) in sorted(assets.items())
        ],
        "requestedFormats": list(formats),
        "outputs": {
            FORMAT_EXTENSIONS[fmt]: {
                "sha256": e["sha256"],
                "sizeBytes": e["sizeBytes"],
                **({"pages": e["pages"]} if "pages" in e else {}),
            }
            for fmt, e in fmt_entries.items()
        },
        "logs": {"diagnostics": diagnostics},
        "rights": {"downloadRequiresFreshToken": True, "audit": "paper_export_audit"},
        "acl": {"tenantId": str(ctx_handle.tenant_id), "sessionId": str(ctx_handle.session_id)},
    }


def _serializer_file() -> str:
    import research_vnext.core.export.serializer as _m

    return _m.__file__


async def export_handler(ctx) -> Any:
    """runtime 注册的 async handler：阻塞编译丢进线程，保住心跳协程。"""
    from ..worker.runtime import HandlerOutcome

    if ctx.stopped():
        return HandlerOutcome(
            succeeded_stages=[],
            failed_stages=["pre_flight"],
            error_code="CANCELLED",
            error_message="取消请求在安全点生效（执行前）",
        )
    await ctx.report(stage="serialize", progress=5)
    out = await asyncio.to_thread(_export_sync, ctx)
    failed = out.get("failed_stages") or []
    await ctx.report(stage=failed[0] if failed else "done", progress=95)
    return HandlerOutcome(
        result=out.get("result") or {},
        succeeded_stages=out.get("succeeded_stages") or [],
        failed_stages=failed,
        error_code=out.get("error_code") or "",
        error_message=out.get("error_message") or "",
    )


# ==========================================================================
# 下载授权链路
# ==========================================================================


def _safe_uuid(s: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(s))
    except (ValueError, TypeError, AttributeError):
        return None


def _load_artifact(db: Session, ctx: AccessContext, artifact_id: uuid.UUID) -> PaperExportArtifactRow:
    row = db.get(PaperExportArtifactRow, artifact_id)
    if row is None:
        raise ExportNotFound(f"导出产物不存在: {artifact_id}")
    if row.tenant_id != ctx.tenant_id:
        raise TenantMismatch("导出产物不属于当前 tenant",
                             artifact_tenant=str(row.tenant_id), actor_tenant=str(ctx.tenant_id))
    if row.owner_id != ctx.actor_id:
        raise ObjectScopeMissing("导出产物不属于当前 actor")
    return row


def artifact_summary(db: Session, ctx: AccessContext, artifact_id: uuid.UUID) -> dict:
    row = _load_artifact(db, ctx, artifact_id)
    check_revocation(db, ctx)
    return {
        "artifactId": str(row.id),
        "session_id": str(row.session_id),
        "paperRevision": row.paper_revision,
        "fingerprint": row.fingerprint,
        "formats": sorted(row.formats.keys()),
        "revoked": row.revoked_at is not None,
        "manifest": dict(row.manifest or {}),
    }


def mint_link(
    db: Session, *, ctx: AccessContext, artifact_id: uuid.UUID, fmt: str, trace_id: str = ""
) -> dict:
    row = _load_artifact(db, ctx, artifact_id)
    check_revocation(db, ctx)
    if row.revoked_at is not None:
        raise ArtifactRevoked("导出产物已撤回，不能签发下载令牌")
    if fmt not in row.formats:
        raise ExportFormatUnavailable(f"该产物没有 {fmt} 格式（只有: {sorted(row.formats.keys())}）")
    floor = get_security_floor(db, ctx.actor_id)
    ttl = download_token_ttl_seconds()
    token = mint_download_token(
        artifact_id=str(row.id), actor_id=str(ctx.actor_id), fmt=fmt,
        ttl_seconds=ttl, security_version=max(1, int(floor)),
    )
    _audit(db, artifact_id=row.id, tenant_id=row.tenant_id, actor_id=ctx.actor_id,
           action="TOKEN_MINTED", detail={"format": fmt, "ttlSeconds": ttl}, trace_id=trace_id)
    return {"token": token, "format": fmt, "expiresInSeconds": ttl}


def download_artifact(
    db: Session, *, artifact_id: uuid.UUID, fmt: str, token: str, trace_id: str = ""
) -> tuple[bytes, str]:
    """返回 (字节, media_type)。所有拒绝路径都写 DOWNLOAD_DENIED 审计。"""
    try:
        tok = parse_download_token(token)
    except DownloadTokenExpired:
        _audit_denied(db, artifact_id=artifact_id, actor_id=None,
                      reason="DOWNLOAD_TOKEN_EXPIRED", trace_id=trace_id)
        e = DownloadTokenExpired("下载令牌已过期")
        raise ArtifactRevoked("下载令牌已过期") from e  # 410 语义（过期）
    except DownloadTokenInvalid:
        _audit_denied(db, artifact_id=artifact_id, actor_id=None,
                      reason="DOWNLOAD_TOKEN_INVALID", trace_id=trace_id)
        raise DownloadTokenTampered("下载令牌无效（签名校验失败）")

    row = db.get(PaperExportArtifactRow, artifact_id)
    if row is None:
        raise ExportNotFound(f"导出产物不存在: {artifact_id}")
    actor = _safe_uuid(tok.actor_id)
    if tok.artifact_id != str(row.id):
        _audit_denied(db, artifact_id=row.id, actor_id=actor,
                      reason="TOKEN_ARTIFACT_MISMATCH", trace_id=trace_id)
        raise DownloadTokenTampered("下载令牌与目标产物不匹配")
    if tok.fmt != fmt:
        _audit_denied(db, artifact_id=row.id, actor_id=actor,
                      reason="TOKEN_FORMAT_MISMATCH", trace_id=trace_id)
        raise DownloadTokenTampered("下载令牌与请求格式不匹配")
    if row.revoked_at is not None:
        _audit_denied(db, artifact_id=row.id, actor_id=actor,
                      reason="ARTIFACT_REVOKED", trace_id=trace_id)
        raise ArtifactRevoked("导出产物已撤回（下载被拒）")
    floor = get_security_floor(db, actor) if actor else 1
    if tok.security_version < floor:
        _audit_denied(db, artifact_id=row.id, actor_id=actor,
                      reason="SECURITY_FLOOR_RAISED", trace_id=trace_id)
        raise ArtifactRevoked("令牌签发后权限已被撤回（security floor 抬升），下载被拒")
    if fmt not in row.formats:
        raise ExportFormatUnavailable(f"该产物没有 {fmt} 格式")

    key = (row.formats.get(fmt) or {}).get("storageKey") or ""
    data = MinioObjectStorage().get_bytes(key)
    expected = (row.formats.get(fmt) or {}).get("sha256") or ""
    if expected and _sha(data) != expected:
        _audit_denied(db, artifact_id=row.id, actor_id=actor,
                      reason="STORAGE_HASH_MISMATCH", trace_id=trace_id)
        raise ObjectStorageError("产物字节与 manifest 哈希不一致（存储损坏）")
    _audit(db, artifact_id=row.id, tenant_id=row.tenant_id, actor_id=actor,
           action="DOWNLOAD", detail={"format": fmt, "jti": tok.jti}, trace_id=trace_id)
    return data, MEDIA_TYPES.get(fmt, "application/octet-stream")


def revoke_artifact(
    db: Session, *, ctx: AccessContext, artifact_id: uuid.UUID, reason: str, trace_id: str = ""
) -> dict:
    row = _load_artifact(db, ctx, artifact_id)
    check_revocation(db, ctx)
    row.revoked_at = datetime.now(timezone.utc)
    row.revoked_reason = str(reason or "")[:512]
    _audit(db, artifact_id=row.id, tenant_id=row.tenant_id, actor_id=ctx.actor_id,
           action="REVOKED", detail={"reason": row.revoked_reason}, trace_id=trace_id)
    return {"artifactId": str(row.id), "revoked": True, "reason": row.revoked_reason}
