"""export_pptx / export_pdf：课件导出真实引擎（M2-B，架构 C-7）。

流程：tasks(payload={export_job_id, deck_id, format}) → 读 deck →
python-pptx+OMML 生成 → MinIO teacher-exports/decks/{deck_id}/{ts}.pptx →
export_jobs(done, file_key) → durable progress/done 事件（任务中心 + SLO P3 观测点）。

注：registry 只向 handler 传 task_id/publisher/user_id——export_job_id/deck_id
从 tasks.payload 取（B3 既有派发契约）。
"""
from __future__ import annotations

import datetime as _dt
import uuid

from ...core.events import EventPublisher
from ...export.pptx_engine import build_pptx


def _uuid(value: str):
    return uuid.UUID(str(value))


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


async def _load_payload(task_id: str) -> dict:
    from sqlalchemy import select

    from ...db.models import Tasks
    from ...db.session import session_factory

    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == _uuid(task_id)))).scalar_one_or_none()
        return dict(row.payload or {}) if row and row.payload else {}


async def _mark_job(export_job_id: str, *, status: str, file_key: str | None = None, error: str | None = None) -> None:
    from sqlalchemy import update

    from ...db.models import ExportJobs
    from ...db.session import session_factory

    values: dict = {"status": status, "finished_at": _now()}
    if file_key:
        values["file_key"] = file_key
    if error:
        values["error"] = error[:500]
    async with session_factory()() as session:
        await session.execute(update(ExportJobs).where(ExportJobs.id == _uuid(export_job_id)).values(**values))
        await session.commit()


async def export_pptx(task_id: str, *, publisher: EventPublisher, user_id: str = "") -> None:
    payload = await _load_payload(task_id)
    export_job_id = str(payload.get("export_job_id") or "")
    deck_id = str(payload.get("deck_id") or "")
    fmt = str(payload.get("format") or "pptx")
    if not export_job_id or not deck_id:
        await _mark_job(export_job_id, status="failed", error="任务参数缺失（export_job_id/deck_id）")
        return

    from sqlalchemy import update

    from ...db.models import Tasks

    async def progress(pct: int, message: str) -> None:
        await publisher.publish_durable(scope_kind="task", scope_id=task_id,
                                        event_type="progress", payload={"progress": pct, "message": message})
        try:
            from ...db.session import session_factory as _sf

            async with _sf()() as session:
                await session.execute(update(Tasks).where(Tasks.id == _uuid(task_id))
                                      .values(progress=pct, message=message[:200]))
                await session.commit()
        except Exception:
            pass  # 进度回写失败不阻塞导出

    await progress(10, "读取课件数据…")

    from sqlalchemy import select

    from ...db.session import session_factory
    from ...domains.contents.models import Deck

    async with session_factory()() as session:
        deck_row = await session.get(Deck, _uuid(deck_id))
        if deck_row is None:
            await _mark_job(export_job_id, status="failed", error=f"课件不存在：{deck_id}")
            await publisher.publish_durable(scope_kind="task", scope_id=task_id,
                                            event_type="error", payload={"progress": 0, "message": "课件不存在"})
            return
        # 读取时按模板主题补背景层（缺背景的旧课件导出也带模板视觉，与编辑器一致）
        from ...domains.contents.service import hydrate_deck_backgrounds

        await hydrate_deck_backgrounds(session, deck_row)
        deck_data = {"title": deck_row.title, "slides": deck_row.slides or []}

    await progress(40, "渲染页面与原生公式（OMML）…")
    if fmt == "pdf":
        # PDF 引擎未实现（架构只锁 PPTX 侧选型）——诚实失败，不产假 PDF
        await _mark_job(export_job_id, status="failed", error="PDF 导出引擎未实现（02-ARCHITECTURE C-7 仅定案 PPTX）")
        await publisher.publish_durable(scope_kind="task", scope_id=task_id,
                                        event_type="error", payload={"progress": 0, "message": "PDF 导出引擎未实现"})
        return
    try:
        data = build_pptx(deck_data)
    except Exception as e:  # noqa: BLE001 —— 渲染失败：export_jobs 诚实 failed
        await _mark_job(export_job_id, status="failed", error=f"PPTX 渲染失败：{e}")
        await publisher.publish_durable(scope_kind="task", scope_id=task_id,
                                        event_type="error", payload={"progress": 0, "message": f"PPTX 渲染失败：{e}"})
        return

    await progress(80, "上传对象存储…")
    from ...storage.minio import get_storage

    key = f"decks/{deck_id}/{int(_dt.datetime.now().timestamp())}.pptx"
    storage = get_storage()
    storage.put_bytes("teacher-exports", key, data,
                      "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    await _mark_job(export_job_id, status="done", file_key=key)
    await progress(100, f"导出完成（{len(data) // 1024} KB）")
    await publisher.publish_durable(scope_kind="task", scope_id=task_id,
                                    event_type="done", payload={"progress": 100, "file_key": key,
                                                                "message": f"导出完成（{len(data) // 1024} KB）"})
