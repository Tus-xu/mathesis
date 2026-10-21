"""RV17 · 科研工作区 REST 端点（挂在双前缀下）。

由 `app.py` 在 `api_prefixes()` 的**每个**前缀下 `include_router`，因此
`APIRouter` **不写 prefix**（契约测试 `test_dual_prefix_and_startup.py` 守门）。

端点分组
--------
- 文献：`GET/POST /projects/{id}/papers`、`DELETE/PATCH .../{paper_id}`、`.../text`
- 笔记：`GET/POST /projects/{id}/notes`、`PATCH/DELETE /notes/{note_id}`、`.../recent`
- 文件树：`GET/PUT/POST/DELETE /projects/{id}/files...`、`POST .../files:rename`、`.../outline`
- 编译：`POST /projects/{id}/compile`、`GET .../compile-runs/{run_id}`、`.../latest`、`.../{run_id}/pdf`
- 概览：`GET /projects/{id}/overview`
- 知识：`POST /projects/{id}/knowledge/search`
- Agent：`POST /projects/{id}/agent/tasks`、`GET /agent/tasks/{task_id}`
- 恢复：`GET/PUT /projects/{id}/recent`、`GET /projects/{id}/history`

授权：全部沿用 `authorize_session` 六层鉴权的同形前置（workspace → 撤权 →
project tenant+owner → capability），复用 `core/api/deps.py` 原语，不自造。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from ..application import message_service
from ..application import session_service as session_svc
from ..db.engine import session_scope
from ..domain.errors import DomainError, NotFound
from ..research_workspace import agent as agent_svc
from ..research_workspace import compile as compile_svc
from ..research_workspace import files as files_svc
from ..research_workspace import knowledge as knowledge_svc
from ..research_workspace import notes as notes_svc
from ..research_workspace import overview as overview_svc
from ..research_workspace import papers as papers_svc
from ..research_workspace.service import authorize_project
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    input_error_response,
    load_access_context,
    trace_from,
)

# 前缀由 include_router(prefix=...) 注入（见模块 docstring），此处**不要**写 prefix。
router = APIRouter(tags=["research-workspace"])


class InvalidRequest(DomainError):
    """请求体字段非法（可预期输入错误）→ 422。"""

    code = "INVALID_INPUT"
    http_status = 422


def _as_uuid(value: object, field: str) -> uuid.UUID:
    """把请求体里的原始值解析为 UUID；非法 → 422（而非 ValueError→500）。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise InvalidRequest(f"{field} 不是合法 UUID: {value!r}") from e


def _as_int(value: object, field: str) -> int:
    """把请求体里的原始值解析为 int；非法 → 422（而非 ValueError→500）。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError) as e:
        raise InvalidRequest(f"{field} 不是合法整数: {value!r}") from e


def _ctx(request: Request) -> AccessContext:
    return load_access_context(request)


def _err(e: Exception) -> JSONResponse:
    if isinstance(e, DomainError):
        return JSONResponse(status_code=e.http_status, content={"error": e.to_dict()})
    # LLM 供应商容量闸（单账号单点）：必须变成 429 + Retry-After，而不是 500 ——
    # 与 `routes.py::_err` 同形，前端统一按「稍后重试」处理（宪法 §56）。
    from ..llm.capacity import LlmCapacityExceeded

    if isinstance(e, LlmCapacityExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "BACKPRESSURE",
                    "message": str(e),
                    "reason": e.reason,
                    "scope": e.scope,
                    "retry_after": e.retry_after,
                }
            },
            headers={"Retry-After": str(e.retry_after)},
        )
    if isinstance(e, (papers_svc.InvalidPaperInput, notes_svc.InvalidNoteInput)):
        return JSONResponse(
            status_code=422, content={"error": {"code": "INVALID_INPUT", "message": str(e)}}
        )
    if isinstance(e, files_svc.InvalidFilePath):
        return JSONResponse(
            status_code=422, content={"error": {"code": e.code, "message": e.message}}
        )
    # 可预期输入错误（含手工构造模型的 ValidationError）统一收敛为 422；其余仍 500。
    input_err = input_error_response(e)
    if input_err is not None:
        return input_err
    raise e


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content=body)


async def _raw_body(request: Request) -> Any:
    ctype = (request.headers.get("content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        return await request.form()
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


# ==========================================================================
# 文献
# ==========================================================================


@router.get("/projects/{project_id}/papers")
def list_papers(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            rows = papers_svc.list_papers(db, tenant_id=ctx.tenant_id, project_id=project_id)
            items = [papers_svc.paper_public_dict(r) for r in rows]
        return _json({"items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/papers")
async def create_paper(request: Request, project_id: uuid.UUID) -> JSONResponse:
    """上传 PDF（multipart `file`）或挂载既有 asset（JSON `{asset_id}`）。

    可选元数据字段：title/authors/year/venue/doi。元数据解析失败不影响文献可用。
    同内容去重 → 200；新建 → 201。
    """
    ctx = _ctx(request)
    try:
        body = await _raw_body(request)
        client_request_id = request.headers.get("X-Client-Request-Id", "")
        upload = body.get("file") if hasattr(body, "get") else None
        deduped = False

        if hasattr(upload, "read"):  # multipart：UploadFile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                path = Path(tmp.name)
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
            try:
                with session_scope() as db:
                    authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
                    row, deduped = papers_svc.attach_from_upload(
                        db,
                        ctx,
                        project_id=project_id,
                        data_path=path,
                        filename=getattr(upload, "filename", "") or "",
                        claimed_media_type=getattr(upload, "content_type", "") or "",
                        title=_form_str(body, "title"),
                        authors=_form_str(body, "authors"),
                        year=_form_str(body, "year"),
                        venue=_form_str(body, "venue"),
                        doi=_form_str(body, "doi"),
                        client_request_id=client_request_id,
                    )
                    out = papers_svc.paper_public_dict(row)
            finally:
                path.unlink(missing_ok=True)
        else:
            asset_id_raw = body.get("asset_id") if isinstance(body, dict) else None
            if not asset_id_raw:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "code": "INVALID_INPUT",
                            "message": "缺少 file（multipart）或 asset_id（JSON）",
                        }
                    },
                )
            with session_scope() as db:
                authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
                row, deduped = papers_svc.attach_from_asset(
                    db,
                    ctx,
                    project_id=project_id,
                    asset_id=_as_uuid(asset_id_raw, "asset_id"),
                    title=str(body.get("title") or ""),
                    authors=body.get("authors"),
                    year=body.get("year"),
                    venue=str(body.get("venue") or ""),
                    doi=str(body.get("doi") or ""),
                )
                out = papers_svc.paper_public_dict(row)
        return _json({"paper": out, "deduplicated": bool(deduped)}, status=200 if deduped else 201)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _form_str(form, key: str) -> str:
    v = form.get(key) if hasattr(form, "get") else None
    return str(v) if v is not None else ""


@router.delete("/projects/{project_id}/papers/{paper_id}")
def delete_paper(request: Request, project_id: uuid.UUID, paper_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            papers_svc.delete_paper(
                db, tenant_id=ctx.tenant_id, project_id=project_id, paper_id=paper_id
            )
        return _json({"ok": True, "deleted": str(paper_id)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.patch("/projects/{project_id}/papers/{paper_id}")
def patch_paper(
    request: Request,
    project_id: uuid.UUID,
    paper_id: uuid.UUID,
    body_in: dict | None = Body(default=None),
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row = papers_svc.update_paper(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                paper_id=paper_id,
                read_state=raw.get("read_state"),
                last_page=raw.get("last_page"),
            )
            out = papers_svc.paper_public_dict(row)
        return _json({"paper": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/papers/{paper_id}/text")
def paper_text(
    request: Request, project_id: uuid.UUID, paper_id: uuid.UUID, page: int = 1
) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = papers_svc.paper_text(
                db, tenant_id=ctx.tenant_id, project_id=project_id, paper_id=paper_id, page=page
            )
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ==========================================================================
# 笔记
# ==========================================================================


@router.get("/projects/{project_id}/notes")
def list_notes(request: Request, project_id: uuid.UUID, limit: int = 100) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            rows = notes_svc.list_notes(
                db, tenant_id=ctx.tenant_id, project_id=project_id, limit=limit
            )
            items = [notes_svc.note_public_dict(r) for r in rows]
        return _json({"items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/notes/recent")
def recent_notes(request: Request, project_id: uuid.UUID, limit: int = 6) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            rows = notes_svc.recent_notes(
                db, tenant_id=ctx.tenant_id, project_id=project_id, limit=limit
            )
            items = [notes_svc.note_public_dict(r) for r in rows]
        return _json({"items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/notes")
def create_note(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            paper_id_raw = raw.get("paper_id")
            row = notes_svc.create_note(
                db,
                ctx,
                project_id=project_id,
                title=str(raw.get("title") or ""),
                body=str(raw.get("body") or ""),
                paper_id=_as_uuid(paper_id_raw, "paper_id") if paper_id_raw else None,
                page=_as_int(raw["page"], "page") if raw.get("page") is not None else None,
                section=str(raw.get("section") or ""),
                selected_text=str(raw.get("selected_text") or ""),
                bbox=raw.get("bbox"),
                xref_kind=str(raw.get("xref_kind") or "manual"),
                xref_id=str(raw.get("xref_id") or ""),
            )
            out = notes_svc.note_public_dict(row)
        return _json({"note": out}, status=201)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.patch("/notes/{note_id}")
def patch_note(
    request: Request, note_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            note = notes_svc.get_note(
                db,
                tenant_id=ctx.tenant_id,
                project_id=_note_project(db, ctx, note_id),
                note_id=note_id,
            )
            row = notes_svc.update_note(
                db,
                tenant_id=ctx.tenant_id,
                project_id=note.project_id,
                note_id=note_id,
                title=raw.get("title"),
                body=raw.get("body"),
                page=_as_int(raw["page"], "page") if raw.get("page") is not None else None,
                section=raw.get("section"),
                selected_text=raw.get("selected_text"),
            )
            out = notes_svc.note_public_dict(row)
        return _json({"note": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.delete("/notes/{note_id}")
def delete_note(request: Request, note_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            project_id = _note_project(db, ctx, note_id)
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            notes_svc.delete_note(
                db, tenant_id=ctx.tenant_id, project_id=project_id, note_id=note_id
            )
        return _json({"ok": True, "deleted": str(note_id)})
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _note_project(db, ctx, note_id: uuid.UUID) -> uuid.UUID:
    """由 note 反查并授权其所属项目（`/notes/{note_id}` 无 project 路径段）。"""
    from ..research_workspace.models import ResearchNoteRow

    row = db.get(ResearchNoteRow, note_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.deleted_at is not None:
        raise NotFound(f"笔记不存在: {note_id}")
    authorize_project(db, ctx, row.project_id, capability=READ_CAPABILITY)
    return row.project_id


# ==========================================================================
# 文件树
# ==========================================================================


@router.get("/projects/{project_id}/files")
def list_files(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            rows = files_svc.list_files(db, tenant_id=ctx.tenant_id, project_id=project_id)
            tree = files_svc.build_tree(rows)
            items = [files_svc.file_public_dict(r) for r in rows]
        return _json({"tree": tree, "items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/files:rename")
def rename_file(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        src = str(raw.get("from") or "")
        dst = str(raw.get("to") or "")
        if not src or not dst:
            return JSONResponse(
                status_code=422,
                content={"error": {"code": "INVALID_INPUT", "message": "缺少 from/to"}},
            )
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row = files_svc.rename_file(
                db, tenant_id=ctx.tenant_id, project_id=project_id, src=src, dst=dst
            )
            out = files_svc.file_public_dict(row)
        return _json({"file": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/files")
def create_file(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        if not raw.get("path"):
            return JSONResponse(
                status_code=422,
                content={"error": {"code": "INVALID_INPUT", "message": "缺少 path"}},
            )
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row = files_svc.create_file(
                db,
                ctx,
                project_id=project_id,
                path=str(raw.get("path")),
                kind=raw.get("kind"),
                content=raw.get("content"),
            )
            out = files_svc.file_public_dict(row, include_content=False)
        return _json({"file": out}, status=201)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/files/{file_path:path}")
def read_file(request: Request, project_id: uuid.UUID, file_path: str) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            row = files_svc.get_file(
                db, tenant_id=ctx.tenant_id, project_id=project_id, path=file_path
            )
            out = files_svc.file_public_dict(row, include_content=True)
        return _json({"file": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.put("/projects/{project_id}/files/{file_path:path}")
def write_file(
    request: Request,
    project_id: uuid.UUID,
    file_path: str,
    body_in: dict | None = Body(default=None),
) -> JSONResponse:
    """写文件（自动保存）。`expected_revision` 不匹配 → 409（乐观并发）。"""
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        if "content" not in raw:
            return JSONResponse(
                status_code=422,
                content={"error": {"code": "INVALID_INPUT", "message": "缺少 content"}},
            )
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row = files_svc.write_file(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                path=file_path,
                content=str(raw.get("content") or ""),
                expected_revision=raw.get("expected_revision"),
            )
            out = files_svc.file_public_dict(row, include_content=False)
        return _json({"file": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.delete("/projects/{project_id}/files/{file_path:path}")
def delete_file(request: Request, project_id: uuid.UUID, file_path: str) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            files_svc.delete_file(
                db, tenant_id=ctx.tenant_id, project_id=project_id, path=file_path
            )
        return _json({"ok": True, "deleted": file_path})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/outline")
def get_outline(request: Request, project_id: uuid.UUID, entry: str = "main.tex") -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = files_svc.outline(
                db, tenant_id=ctx.tenant_id, project_id=project_id, entry=entry
            )
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ==========================================================================
# 编译
# ==========================================================================


@router.post("/projects/{project_id}/compile")
def compile_project(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            run = compile_svc.compile_project(
                db,
                ctx,
                project_id=project_id,
                engine=str(raw.get("engine") or "xelatex"),
                entry=str(raw.get("entry") or "main.tex"),
            )
            out = compile_svc.run_public_dict(run)
            last_ok = compile_svc.last_successful_pdf_artifact_id(
                db, tenant_id=ctx.tenant_id, project_id=project_id
            )
        return _json({"compile_run": out, "last_successful_pdf_artifact_id": last_ok}, status=201)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/compile-runs/latest")
def latest_compile(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            run = compile_svc.latest_run(db, tenant_id=ctx.tenant_id, project_id=project_id)
            last_ok = compile_svc.last_successful_pdf_artifact_id(
                db, tenant_id=ctx.tenant_id, project_id=project_id
            )
            out = compile_svc.run_public_dict(run) if run is not None else None
        return _json({"compile_run": out, "last_successful_pdf_artifact_id": last_ok})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/compile-runs/{run_id}/pdf")
def download_compile_pdf(request: Request, project_id: uuid.UUID, run_id: uuid.UUID) -> Response:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            run = compile_svc.get_run(
                db, tenant_id=ctx.tenant_id, project_id=project_id, run_id=run_id
            )
            pdf = compile_svc.read_run_pdf(db, run)
        if not pdf:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "PDF_NOT_AVAILABLE", "message": "该运行没有可用的 PDF"}},
            )
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="run-{run_id}.pdf"',
                "X-Artifact-Id": str(run.pdf_artifact_id or ""),
            },
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/compile-runs/{run_id}")
def get_compile_run(request: Request, project_id: uuid.UUID, run_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            run = compile_svc.get_run(
                db, tenant_id=ctx.tenant_id, project_id=project_id, run_id=run_id
            )
            out = compile_svc.run_public_dict(run)
            last_ok = compile_svc.last_successful_pdf_artifact_id(
                db, tenant_id=ctx.tenant_id, project_id=project_id
            )
        return _json({"compile_run": out, "last_successful_pdf_artifact_id": last_ok})
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ==========================================================================
# 概览 / 知识 / 恢复
# ==========================================================================


@router.get("/projects/{project_id}/overview")
async def get_overview(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = await overview_svc.overview(
                db, tenant_id=ctx.tenant_id, project_id=project_id
            )
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/knowledge/search")
def knowledge_search(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = knowledge_svc.search(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                query=str(raw.get("query") or ""),
                limit=_as_int(raw.get("limit") or 10, "limit"),
            )
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/recent")
def get_recent(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = overview_svc.get_recent(
                db, tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.actor_id
            )
        return _json({"recent": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.put("/projects/{project_id}/recent")
def put_recent(
    request: Request, project_id: uuid.UUID, body_in: dict | None = Body(default=None)
) -> JSONResponse:
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            out = overview_svc.put_recent(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                actor_id=ctx.actor_id,
                workspace=raw.get("workspace"),
                paper_id=_as_uuid(raw["paper_id"], "paper_id") if raw.get("paper_id") else None,
                page=_as_int(raw["page"], "page") if raw.get("page") is not None else None,
                file_path=raw.get("file_path"),
                line=_as_int(raw["line"], "line") if raw.get("line") is not None else None,
            )
        return _json({"recent": out})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/projects/{project_id}/history")
def get_history(request: Request, project_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            out = overview_svc.history(
                db, tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.actor_id
            )
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ==========================================================================
# Agent 任务
# ==========================================================================


@router.get("/projects/{project_id}/agent/tasks")
def list_agent_tasks(
    request: Request,
    project_id: uuid.UUID,
    limit: int = 1,
) -> JSONResponse:
    """项目**最近的 Agent 任务**（含结果）。

    用途：前端重新进入项目时**恢复上次的能力结果**，而不是每次都从空态开始 ——
    这是宪法 §84「再次打开 Project 应尽量恢复必要 panel state」的落地，
    也避免 §54「用户关闭 Accordion 后 Task 神秘消失」。

    默认只取最近 1 条：重进项目时还原"上次那张卡"，而不是把整段历史一次拉回。
    """
    ctx = _ctx(request)
    try:
        n = max(1, min(_as_int(limit, "limit") or 1, 20))
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            rows = (
                db.execute(
                    select(agent_svc.ResearchAgentTaskRow)
                    .where(
                        agent_svc.ResearchAgentTaskRow.tenant_id == ctx.tenant_id,
                        agent_svc.ResearchAgentTaskRow.project_id == project_id,
                    )
                    .order_by(agent_svc.ResearchAgentTaskRow.created_at.desc())
                    .limit(n)
                )
                .scalars()
                .all()
            )
            items = [agent_svc.task_public_dict(r) for r in rows]
        return _json({"items": items})
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/projects/{project_id}/agent/tasks")
def create_agent_task(
    request: Request,
    project_id: uuid.UUID,
    background: BackgroundTasks,
    body_in: dict | None = Body(default=None),
) -> JSONResponse:
    """创建统一 Research Agent 任务（异步执行；`GET /agent/tasks/{id}` 取结果）。

    幂等键 `(tenant_id, actor_id, client_request_id)`（宪法 §31/§54）：客户端可传
    `X-Client-Request-Id` 头或 body 的 `client_request_id`；重复提交返回**同一个**
    `task_id`，且**不会**重跑已经跑过的任务。
    """
    ctx = _ctx(request)
    raw = body_in or {}
    try:
        preset = str(raw.get("preset") or "")
        context = raw.get("context") if isinstance(raw.get("context"), dict) else {}
        cid = str(
            request.headers.get("X-Client-Request-Id")
            or raw.get("client_request_id")
            or ""
        )
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row, created = agent_svc.create_task(
                db,
                ctx,
                project_id=project_id,
                preset=preset,
                context=context,
                client_request_id=cid,
            )
            task_id = row.id
            status = row.status
        if created:
            background.add_task(agent_svc.execute_task, task_id)
        return _json(
            {"task_id": str(task_id), "status": status, "preset": preset, "created": created},
            status=202,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/agent/tasks/{task_id}")
def get_agent_task(request: Request, task_id: uuid.UUID) -> JSONResponse:
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            row = db.get(agent_svc.ResearchAgentTaskRow, task_id)
            if row is None or row.tenant_id != ctx.tenant_id:
                raise NotFound(f"任务不存在: {task_id}")
            authorize_project(db, ctx, row.project_id, capability=READ_CAPABILITY)
            out = agent_svc.task_public_dict(row)
        return _json(out)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ==========================================================================
# Agent 对话（宪法 §31：输入框 = 用户用自然语言说话 → 真实 LLM 对话）
#
# 设计取舍（与「点能力 = preset 直接执行」严格分工）：
# - **点能力** 走 `POST /projects/{id}/agent/tasks`（现有，不变）；
# - **自由文本** 走本组端点：落到既有会话子系统（`message_service.send_message`
#   → `llm/gateway`），产出**真实 assistant 消息**，而不是把自由文本关键词路由到
#   preset。会话由项目隐式持有（一个用户一个项目一段「科研对话」）。
# ==========================================================================


def _latest_assistant_message(snapshot: dict) -> dict | None:
    """从快照 `timeline` 里取**最新一条** assistant 消息（对话回答）。

    返回的就是 `timeline_item()` 的形状（`id/role/text/createdAt/evidenceRefs`
    + 可选 `component`），前端直接渲染纯文本，无需再解析。
    """
    timeline = snapshot.get("timeline")
    if not isinstance(timeline, list):
        return None
    for item in reversed(timeline):
        if isinstance(item, dict) and item.get("role") == "assistant":
            return item
    return None


@router.post("/projects/{project_id}/agent/chat")
async def post_agent_chat(
    request: Request,
    project_id: uuid.UUID,
    body_in: dict | None = Body(default=None),
) -> JSONResponse:
    """自由文本 → 真实对话（宪法 §31）。

    行为：鉴权项目（写）→ 取或建项目对话会话 → `send_message`（真实 LLM）→
    返回最新 assistant 回答 + 完整 timeline。

    - 201：新消息已写入（`X-Idempotency-Status: EXECUTED`）；
    - 200：同 `client_message_id` + 同内容重放（REPLAYED，返回原回答）。
    """
    ctx = _ctx(request)
    raw = body_in or {}
    text = str(raw.get("message") or "").strip()
    if not text:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_INPUT", "message": "缺少 message"}},
        )
    crid = str(
        raw.get("client_message_id") or request.headers.get("X-Client-Request-Id") or ""
    ).strip()
    trace = trace_from(request, ctx)

    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=WRITE_CAPABILITY)
            row = session_svc.get_or_create_conversation_session(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                owner_id=ctx.actor_id,
                trace=trace,
            )
            session_id = row.id
    except Exception as e:  # noqa: BLE001
        return _err(e)

    try:
        snapshot, idem, status = await message_service.send_message(
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            message=text,
            expected_revision=raw.get("expected_revision"),
            client_message_id=crid or None,
            attachments=raw.get("attachments"),
            trace=trace,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)

    return JSONResponse(
        status_code=status,
        content={
            "session_id": str(session_id),
            "reply": _latest_assistant_message(snapshot),
            "timeline": snapshot.get("timeline", []),
            "capabilities": snapshot.get("capabilities", []),
        },
        headers={"X-Idempotency-Status": idem} if idem else None,
    )


@router.get("/projects/{project_id}/agent/chat")
def get_agent_chat(request: Request, project_id: uuid.UUID) -> JSONResponse:
    """读取项目对话历史（重进项目时恢复对话线程，宪法 §84）。

    尚无对话会话时返回 `{"session_id": null, "timeline": []}`（诚实空态，不伪造）。
    """
    ctx = _ctx(request)
    try:
        with session_scope() as db:
            authorize_project(db, ctx, project_id, capability=READ_CAPABILITY)
            row = session_svc.find_conversation_session(
                db,
                tenant_id=ctx.tenant_id,
                project_id=project_id,
                owner_id=ctx.actor_id,
            )
            if row is None:
                return _json({"session_id": None, "timeline": []})
            session_id = row.id
            state, reason = message_service.default_message_state()
            snapshot = message_service.get_message_snapshot(
                db, session_id, message_state=state, message_reason=reason
            )
        return _json(
            {
                "session_id": str(session_id),
                "timeline": snapshot.get("timeline", []),
                "capabilities": snapshot.get("capabilities", []),
            }
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


__all__ = ["router"]
