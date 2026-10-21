"""M3 教师端：资源 / 预处理 / 理解（§14）。

- 上传复用现有受控文件链（创建受控异步任务，不重复对象存储）；
- preprocess_course / understand_document 通过业务任务包装（queued→…）；
- 引用必须含资源 ID/页码或切片定位；检索继续复用 /tools/retrieve；
- 响应对齐前端 TeacherResource / UploadTicket 契约（审计 C-04）。
"""

from __future__ import annotations

import io
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.scope import assert_teacher_in_class, raise_http
from app.models.question_bank import QuestionBank, stem_hash
from app.models.teacher import TeacherTask

ERR_NOT_FOUND = 40400
MAX_RESOURCE_BYTES = 20 * 1024 * 1024
RESOURCE_ROOT = Path(tempfile.gettempdir()) / "math-arena-m3-resources"
RESOURCE_CAPABILITIES = {"resource.upload", "resource.external_reference"}

# 任务状态 → 前端资源状态
_STATUS_MAP = {
    "queued": "preprocessing",
    "running": "preprocessing",
    "succeeded": "ready",
    "failed": "failed",
    "cancelled": "cancelled",
}


async def _create_task(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    capability: str,
    payload: dict,
    client_request_id: str | None = None,
) -> TeacherTask:
    t = TeacherTask(
        owner_id=teacher_id,
        class_id=class_id,
        capability=capability,
        status="queued",
        payload={**payload, "client_request_id": client_request_id}
        if client_request_id
        else payload,
    )
    db.add(t)
    await db.flush()
    return t


def _serialize_resource(t: TeacherTask) -> dict:
    """对齐前端 TeacherResource 契约（task 以 resource 维度呈现）。"""
    payload = t.payload or {}
    name = payload.get("filename") or payload.get("resource_id") or "教学材料"
    return {
        "resource_id": str(t.id),
        "name": str(name),
        "resource_kind": payload.get("resource_kind") or "uploaded_file",
        "external_url": payload.get("external_url"),
        "provider": payload.get("provider"),
        "attribution": payload.get("attribution"),
        "intended_use": payload.get("intended_use"),
        "file_type": payload.get("file_type") or "file",
        "size_bytes": int(payload.get("size_bytes") or 0),
        "status": _STATUS_MAP.get(t.status, "preprocessing"),
        "task_id": str(t.id),
        "error": t.error_code,
        "pages": (t.result or {}).get("pages") or [],
        "slices": (t.result or {}).get("slices") or [],
        "summary": (t.result or {}).get("summary") or "",
        "published": bool((t.result or {}).get("published", False)),
        "degraded": bool((t.result or {}).get("degraded", True)),
        "warnings": (t.result or {}).get("warnings") or [],
        "question_candidates": (t.result or {}).get("question_candidates") or [],
        "download_url": f"/api/teacher/resources/{t.id}/download" if payload.get("resource_kind") != "external_reference" else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _serialize_ticket(t: TeacherTask) -> dict:
    """对齐前端 UploadTicket 契约。"""
    return {
        "resource_id": str(t.id),
        "task_id": str(t.id),
        "status": _STATUS_MAP.get(t.status, "preprocessing"),
    }


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "resource.bin").name
    cleaned = re.sub(r"[^\w.\-()\u4e00-\u9fff]", "_", name, flags=re.UNICODE)
    return cleaned[:180] or "resource.bin"


def _extract_text(data: bytes, filename: str, content_type: str | None) -> tuple[str, list[str]]:
    suffix = Path(filename).suffix.lower()
    warnings: list[str] = []
    try:
        if suffix in {".txt", ".md", ".csv"} or (content_type or "").startswith("text/"):
            return data.decode("utf-8", errors="replace").strip(), warnings
        if suffix == ".docx":
            from docx import Document

            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip(), warnings
        if suffix == ".pdf":
            import fitz

            with fitz.open(stream=data, filetype="pdf") as doc:
                return "\n".join(page.get_text("text") for page in doc).strip(), warnings
    except Exception:
        warnings.append("本地文本提取失败，原文件仍可下载")
    warnings.append("当前文件类型未提取文本，可下载原文件或接入外部解析工作流")
    return "", warnings


async def _owned_resource(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str
) -> TeacherTask:
    try:
        rid = uuid.UUID(resource_id)
    except (ValueError, TypeError):
        raise_http(ERR_NOT_FOUND, 404, "resource_not_found", recoverable=False)
    task = await db.get(TeacherTask, rid)
    if (
        task is None
        or task.owner_id != teacher_id
        or task.capability not in RESOURCE_CAPABILITIES
    ):
        raise_http(ERR_NOT_FOUND, 404, "resource_not_found", recoverable=False)
    return task


async def resource_upload(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    file: UploadFile,
    client_request_id: str,
) -> dict:
    if class_id:
        await assert_teacher_in_class(db, teacher_id, class_id)
    data = await file.read(MAX_RESOURCE_BYTES + 1)
    if len(data) > MAX_RESOURCE_BYTES:
        raise_http(40001, 400, "resource_too_large", recoverable=True)
    filename = _safe_filename(file.filename)
    resource_key = uuid.uuid4()
    owner_dir = RESOURCE_ROOT / str(teacher_id)
    owner_dir.mkdir(parents=True, exist_ok=True)
    storage_path = owner_dir / f"{resource_key}_{filename}"
    storage_path.write_bytes(data)
    extracted_text, warnings = _extract_text(data, filename, file.content_type)
    task = await _create_task(
        db,
        teacher_id,
        class_id,
        capability="resource.upload",
        payload={
            "filename": filename,
            "file_type": file.content_type or "file",
            "size_bytes": len(data),
            "storage_path": str(storage_path),
            "client_request_id": client_request_id,
        },
    )
    task.status = "succeeded"
    task.progress = 100
    task.result = {
        "text": extracted_text,
        "warnings": warnings,
        "degraded": True,
        "published": False,
        "pages": [],
        "slices": [],
    }
    await db.flush()
    return {**_serialize_ticket(task), **_serialize_resource(task)}


async def create_external_reference(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None,
    title: str,
    url: str,
    provider: str | None,
    attribution: str | None,
    intended_use: str | None,
) -> dict:
    """Save provenance for a public resource without fetching or copying it."""
    if class_id:
        await assert_teacher_in_class(db, teacher_id, class_id)
    task = await _create_task(
        db, teacher_id, class_id, capability="resource.external_reference",
        payload={
            "filename": title.strip(), "file_type": "external_reference", "size_bytes": 0,
            "resource_kind": "external_reference", "external_url": url,
            "provider": provider.strip() if provider else None,
            "attribution": attribution.strip() if attribution else None,
            "intended_use": intended_use.strip() if intended_use else None,
        },
    )
    task.status = "succeeded"
    task.progress = 100
    task.result = {
        "text": "", "slices": [], "pages": [], "published": False, "degraded": False,
        "warnings": ["这是外部引用记录；平台未下载、转载或解析该第三方内容。"],
    }
    await db.flush()
    return _serialize_resource(task)


async def resource_preprocess(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    resource_id: str,
    client_request_id: str,
) -> dict:
    if class_id:
        await assert_teacher_in_class(db, teacher_id, class_id)
    task = await _owned_resource(db, teacher_id, resource_id)
    text = str((task.result or {}).get("text") or "")
    slices = [
        {"slice_id": f"{task.id}:{i // 500 + 1}", "text": text[i : i + 500]}
        for i in range(0, len(text), 500)
    ]
    task.result = {
        **(task.result or {}),
        "slices": slices,
        "structure": {"title": (task.payload or {}).get("filename"), "section_count": len(slices)},
        "warnings": list((task.result or {}).get("warnings") or [])
        + (["未提取到文本，当前仅支持原文件下载"] if not slices else []),
    }
    task.status = "succeeded"
    task.progress = 100
    await db.flush()
    return _serialize_resource(task)


async def resource_understand(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    resource_id: str,
    *,
    question: str | None,
    output_type: str | None,
    client_request_id: str,
) -> dict:
    if class_id:
        await assert_teacher_in_class(db, teacher_id, class_id)
    task = await _owned_resource(db, teacher_id, resource_id)
    text = " ".join(str((task.result or {}).get("text") or "").split())
    summary = text[:300] if text else "未提取到可理解文本，请下载原文件检查或接入外部解析。"
    task.result = {
        **(task.result or {}),
        "summary": summary,
        "question": question,
        "answer": text[:800] if question and text else None,
        "output_type": output_type,
        "degraded": True,
    }
    task.status = "succeeded"
    task.progress = 100
    await db.flush()
    return _serialize_resource(task)


async def set_resource_published(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str, published: bool
) -> dict:
    task = await _owned_resource(db, teacher_id, resource_id)
    task.result = {**(task.result or {}), "published": published}
    await db.flush()
    return _serialize_resource(task)


async def save_question_candidates(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str, candidates: list[dict]
) -> dict:
    """Store editable extracted candidates; they are never student-bank rows before approval."""
    task = await _owned_resource(db, teacher_id, resource_id)
    if (task.payload or {}).get("resource_kind") == "external_reference":
        raise_http(40001, 422, "external_reference_has_no_extractable_text", recoverable=True)
    rows = []
    for candidate in candidates:
        value = dict(candidate)
        value["candidate_id"] = value.get("candidate_id") or uuid.uuid4().hex
        value["review_status"] = "pending_review"
        rows.append(value)
    task.result = {**(task.result or {}), "question_candidates": rows}
    await db.flush()
    return {"resource_id": str(task.id), "candidates": rows, "review_required": True}


async def approve_question_candidates(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str, candidate_ids: list[str]
) -> dict:
    """Teacher approval is the only transition from extracted text to student-eligible bank rows."""
    task = await _owned_resource(db, teacher_id, resource_id)
    if (task.payload or {}).get("resource_kind") == "external_reference":
        raise_http(40001, 422, "external_reference_has_no_extractable_text", recoverable=True)
    result = dict(task.result or {})
    candidates = list(result.get("question_candidates") or [])
    wanted = set(candidate_ids)
    selected = [row for row in candidates if row.get("candidate_id") in wanted]
    if len(selected) != len(wanted):
        raise_http(40001, 422, "question_candidate_not_found", recoverable=True)
    created: list[str] = []
    for row in selected:
        digest = stem_hash(str(row["stem"]))
        existing = await db.scalar(select(QuestionBank).where(QuestionBank.hash == digest))
        if existing is None:
            bank_row = QuestionBank(
                stem=str(row["stem"]), q_type=str(row["q_type"]), answer=str(row["answer"]),
                options=row.get("options"), analysis=row.get("analysis"),
                difficulty=str(row.get("difficulty") or "medium"),
                kp_codes=list(row.get("knowledge_points") or []), hash=digest,
                source=f"teacher_upload:{(task.payload or {}).get('filename', 'resource')}",
                # This column records question-bank extraction health and is limited to
                # 16 characters. Review is represented separately below and this row
                # exists only after the teacher explicitly approves it.
                source_batch=str(task.id), scope="student", kp_status="ok",
                annotate_meta={"resource_id": str(task.id), "candidate_id": row["candidate_id"], "approved_by": str(teacher_id), "review_status": "approved"},
            )
            db.add(bank_row)
            created.append(digest)
        row["review_status"] = "approved"
    result["question_candidates"] = candidates
    task.result = result
    await db.flush()
    return {"resource_id": str(task.id), "approved_hashes": created, "review_required": False}


async def resource_delete(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str
) -> dict:
    """删除教师自有的资源记录：移除本地存储文件并删除任务行。

    - 已审核入库的题目（QuestionBank）不随之删除——它们是教师确认过的独立资产；
    - 未审核候选题只存在于任务 result 中，随任务行一并消失；
    - 外部引用没有本地文件，仅删除记录。
    """
    task = await _owned_resource(db, teacher_id, resource_id)
    payload = task.payload or {}
    storage_path = Path(str(payload.get("storage_path") or ""))
    if storage_path.name:
        try:
            owner_root = (RESOURCE_ROOT / str(teacher_id)).resolve()
            resolved = storage_path.resolve(strict=True)
            if owner_root in resolved.parents:
                resolved.unlink(missing_ok=True)
        except (FileNotFoundError, OSError):
            pass  # 文件已不存在：仅清理任务记录
    await db.delete(task)
    await db.flush()
    return {"resource_id": resource_id, "deleted": True}


async def resource_content(
    db: AsyncSession, teacher_id: uuid.UUID, resource_id: str
) -> tuple[bytes, str, str]:
    task = await _owned_resource(db, teacher_id, resource_id)
    payload = task.payload or {}
    storage_path = Path(str(payload.get("storage_path") or ""))
    owner_root = (RESOURCE_ROOT / str(teacher_id)).resolve()
    try:
        resolved = storage_path.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise_http(ERR_NOT_FOUND, 404, "resource_file_not_found", recoverable=False)
    if owner_root not in resolved.parents:
        raise_http(ERR_NOT_FOUND, 404, "resource_file_not_found", recoverable=False)
    return resolved.read_bytes(), str(payload.get("filename") or "resource.bin"), str(
        payload.get("file_type") or "application/octet-stream"
    )


async def list_resources(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID | None
) -> list[dict]:
    stmt = select(TeacherTask).where(
        TeacherTask.owner_id == teacher_id,
        TeacherTask.capability.in_(RESOURCE_CAPABILITIES),
    )
    if class_id:
        stmt = stmt.where(TeacherTask.class_id == class_id)
    rows = (await db.execute(stmt.order_by(TeacherTask.created_at.desc()).limit(100))).scalars().all()
    return [_serialize_resource(t) for t in rows]
