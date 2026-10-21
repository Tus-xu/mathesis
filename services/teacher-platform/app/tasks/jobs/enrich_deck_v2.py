"""课件逐页增稠作业（8100 worker 侧）。

链路：tasks 行(payload.deck_id) → app.ppt_v2.service.enrich.enrich_deck（逐页 LLM，Semaphore(8)，
单页 300s）→ 新 revision 写回 ppt_v2_deck_specs（提案 source=regen → apply）。

失败语义：单页失败走确定性兜底（不整单失败）；作业本身仅在 DB 写回失败时抛错。

重投语义（已知平台行为，未在本任务内改造）：taskiq broker 的 idle_timeout 已于 2026-09-23
从 30s 调到 900s（app/tasks/broker.py，P10 修复：原 30s 短于真实任务时长，会把运行中的
长任务重复投递）。增稠一次 ~80s 远低于 900s 阈值，正常不再触发重复投递；仅任务 wall
超过 900s 时仍会被 XAUTOCLAIM 接管重跑。本作业对该机制**安全但不免费**：写回受 T24
乐观并发保护（expected_revision 不匹配 → 409，绝不双写、只产生一个 revision），
代价是重复执行会重复消耗 LLM 额度。
（导出作业 export_deck_v2 同样暴露在该机制下：R22 起 QA 卡门把导出拉长为「生成 + QA +
≤2 轮修复重渲染」，其 8200 轮询上限 900s/轮与该阈值同量级，长课件仍可能触达 900s idle
被重复投递；同 deck 的 8200 生成受 dedupe_key 约束、8100 产物登记为内容寻址，重放安全。）

结果：run_task 会以 {"ok": True} 覆盖 tasks.result，因此增稠结果以 durable 事件
（enrich_result）为准，供 job 视图/前端读取（与 export_artifact_v2 同口径）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.events import EventPublisher
from app.db.models import Tasks
from app.db.session import session_factory
from app.ppt_v2.service import enrich as enrich_service


async def _emit(publisher: EventPublisher, task_id: str, event: str, payload: dict[str, Any]) -> None:
    await publisher.publish_durable(
        scope_kind="task", scope_id=uuid.UUID(task_id), event_type=event, payload=payload
    )


async def enrich_deck_v2(task_id: str, *, publisher: EventPublisher, user_id: str) -> None:
    tid = uuid.UUID(task_id)
    async with session_factory()() as session:
        row = (await session.execute(select(Tasks).where(Tasks.id == tid))).scalar_one_or_none()
        if row is None:
            raise RuntimeError(f"task {task_id} 不存在")
        payload = dict(row.payload or {})
    deck_id = str(payload.get("deck_id") or "")
    if not deck_id:
        raise RuntimeError("enrich_deck_v2 缺少 payload.deck_id")
    refs = payload.get("refs") if isinstance(payload.get("refs"), dict) else None

    await _emit(publisher, task_id, "stage", {"stage": "enrich_start", "deck_id": deck_id})

    # G13 真实进度：每页完成即发 durable progress 事件（done/total 均为服务端真实计数，
    # 前端只准读 job 事件流渲染 n/N，禁止自造进度条）。
    async def _on_page_done(done: int, total: int, slide_id: str) -> None:
        await _emit(
            publisher,
            task_id,
            "progress",
            {"stage": "enrich_page", "done": done, "total": total, "slide_id": slide_id},
        )

    async with session_factory()() as session:
        result = await enrich_service.enrich_deck(
            session, deck_id, refs=refs, actor_id=payload.get("owner_id"),
            on_page_done=_on_page_done,
        )
        await session.commit()

    await _emit(publisher, task_id, "enrich_result", result)
