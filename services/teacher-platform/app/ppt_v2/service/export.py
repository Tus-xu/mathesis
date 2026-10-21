"""导出作业编排（T24）：提交/查询/取消 + artifact 查找。

作业执行一律走 tasks 表 + taskiq worker（不在 API 进程里跑长任务）：
`reliability/__init__.py` 明确禁止把 PPT 长任务塞进 BackgroundTasks。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.db.models import TaskEvents, Tasks
from app.storage.models import TeacherArtifact

_DONE = {"done", "failed", "cancelled"}


async def latest_artifact(
    session: AsyncSession, *, spec_kind: str, spec_id: str, kind: str
) -> TeacherArtifact | None:
    return (
        await session.execute(
            select(TeacherArtifact)
            .where(
                TeacherArtifact.spec_kind == spec_kind,
                TeacherArtifact.spec_id == spec_id,
                TeacherArtifact.kind == kind,
            )
            .order_by(TeacherArtifact.created_at.desc())
        )
    ).scalars().first()


async def _deck_slide_count(session: AsyncSession, deck_id: str) -> int:
    """读 deck spec 的真实页数（0 页/无 spec 都不允许导出）。"""
    from app.ppt_v2.service import spec_edit

    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    return len(spec.slides)


async def submit_deck_export(
    session: AsyncSession,
    *,
    user_id: str,
    deck_id: str,
    fmt: str,
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.tasks.service import TaskService

    if fmt not in ("pptx", "pdf"):
        raise ApiError(ErrorCode.VALIDATION, f"deck 导出格式 {fmt} 不受支持（仅 pptx/pdf）")
    # G12 前置校验：0 页 deck 不允许导出（前端按钮同口径禁用；拒绝时给出真实原因）
    pages = await _deck_slide_count(session, deck_id)
    if pages <= 0:
        raise ApiError(ErrorCode.VALIDATION, "课件暂无页面，无法导出")
    task = await TaskService.submit(
        "export_deck_v2",
        {"deck_id": deck_id, "format": fmt, "owner_id": user_id, "refs": refs or {}, "page_count": pages},
        user_id,
    )
    return {"job_id": task["id"], "task_id": task["id"], "format": fmt, "status": task["status"]}


async def submit_lesson_plan_export(
    session: AsyncSession, *, user_id: str, spec_id: str, fmt: str
) -> dict[str, Any]:
    from app.tasks.service import TaskService

    if fmt not in ("markdown", "pdf", "docx"):
        raise ApiError(ErrorCode.VALIDATION, f"教案导出格式 {fmt} 不受支持（仅 markdown/pdf/docx）")
    # G12 前置校验（无 spec / 空内容）由调用方 specs/router.py::start_export 完成
    # （该路由已 load_spec_row；此处不反向 import teacher_prep，保持 ppt_v2 → 平台单向依赖）。
    task = await TaskService.submit(
        "export_lesson_plan_v2",
        {"spec_id": spec_id, "format": fmt, "owner_id": user_id},
        user_id,
    )
    return {"job_id": task["id"], "task_id": task["id"], "format": fmt, "status": task["status"]}


def _stage_of(row: Tasks) -> dict[str, Any]:
    result = row.result or {}
    if row.status == "running":
        return {"stage": "running", "page_no": None, "page_count": result.get("page_count")}
    return {"stage": row.status, "page_no": None, "page_count": result.get("page_count")}


async def job_view(session: AsyncSession, job_id: str, *, user_id: str) -> dict[str, Any]:
    try:
        tid = uuid.UUID(job_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"导出作业 {job_id} 不存在") from exc
    row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"导出作业 {job_id} 不存在")
    if str(row.user_id) != user_id:
        # 作业结果里含 artifact_id，越权可见等于泄露他人产物引用
        raise ApiError(ErrorCode.FORBIDDEN, "无权查看该导出作业")
    events = (
        await session.execute(
            select(TaskEvents).where(TaskEvents.task_id == tid).order_by(TaskEvents.seq)
        )
    ).scalars().all()
    result = dict(row.result or {})
    # run_task 终态会以 result={"ok": True} 覆盖 handler 写入的 result，
    # 因此产物 id 必须从 durable 事件取（artifact 事件是作业自己发的权威事实）
    events_view: list[dict[str, Any]] = []
    pptx_artifact_id = result.get("pptx_artifact_id")
    pdf_artifact_id = result.get("pdf_artifact_id")
    other_artifact_id = result.get("artifact_id")
    qa_status = result.get("qa_status")
    for e in events:
        detail = e.payload or {}
        events_view.append(
            {
                "seq": e.seq,
                "type": e.event_type,
                "ts": e.created_at.isoformat() if e.created_at else None,
                "detail": detail,
            }
        )
        if e.event_type == "artifact":
            kind = detail.get("kind")
            if kind == "pptx":
                pptx_artifact_id = detail.get("artifact_id")
            elif kind == "pdf":
                pdf_artifact_id = pdf_artifact_id or detail.get("artifact_id")
            else:
                other_artifact_id = other_artifact_id or detail.get("artifact_id")
        elif e.event_type == "qa":
            qa_status = detail
    artifact_id = pptx_artifact_id or pdf_artifact_id or other_artifact_id
    return {
        "job_id": str(row.id),
        "type": row.type,
        "format": (row.payload or {}).get("format"),
        "status": row.status,
        "progress": _stage_of(row),
        "artifact_id": artifact_id,
        "pptx_artifact_id": pptx_artifact_id,
        "pdf_artifact_id": pdf_artifact_id,
        "qa_status": qa_status,
        "error": row.error,
        "events": events_view,
    }


async def cancel_job(session: AsyncSession, job_id: str, *, user_id: str) -> dict[str, Any]:
    try:
        tid = uuid.UUID(job_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"导出作业 {job_id} 不存在") from exc
    row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"导出作业 {job_id} 不存在")
    if str(row.user_id) != user_id:
        raise ApiError(ErrorCode.FORBIDDEN, "无权取消该导出作业")
    if row.status in _DONE:
        return {"job_id": str(row.id), "status": row.status, "changed": False}
    row.status = "cancelled"
    await session.flush()
    await session.commit()
    return {"job_id": str(row.id), "status": "cancelled", "changed": True}
