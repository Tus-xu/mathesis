# coding: utf-8
"""管家跨会话记忆：会话消息持久化 + 记忆加载。

R11.5-B 接管（DECISIONS §5 / 迁移 d17b2c3e4f5a）：`butler_messages` 所有权归正式
Alembic 迁移，**移除运行期幂等建表**（ensure_butler_tables 改为所有权校验，缺表抛
RuntimeOwnershipError——生产/本地统一 fail-closed，不再静默吞）。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.butler import ButlerMessageRow

_TEXT_LIMIT = 4000


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


async def ensure_butler_tables(session: AsyncSession) -> None:
    """所有权校验：butler_messages 由迁移 d17b2c3e4f5a 所有，缺失 → fail-closed（不建表）。"""
    from app.db.ownership import RuntimeOwnershipError

    row = await session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'butler_messages'"
        )
    )
    if row.scalar() is None:
        raise RuntimeOwnershipError(
            "butler_messages 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
        )


async def record_butler_message(
    session: AsyncSession,
    *,
    teacher_id: str,
    session_id: str,
    role: str,
    text_: str,
) -> None:
    """落一条消息（当前阶段只落 user 消息支撑记忆）。"""
    await ensure_butler_tables(session)
    session.add(ButlerMessageRow(
        teacher_id=_as_uuid(teacher_id),
        session_id=str(session_id)[:64],
        role=str(role)[:16],
        text=str(text_)[:_TEXT_LIMIT],
    ))
    await session.commit()


async def load_butler_memories(
    session: AsyncSession,
    *,
    teacher_id: str,
    exclude_session_id: str | None = None,
    limit: int = 6,
) -> list[str]:
    """取该教师最近的 user 消息作为记忆（默认排除当前会话，避免与 history 重复）。"""
    try:
        await ensure_butler_tables(session)
        q = (
            select(ButlerMessageRow.text)
            .where(ButlerMessageRow.teacher_id == _as_uuid(teacher_id), ButlerMessageRow.role == "user")
            .order_by(ButlerMessageRow.created_at.desc())
            .limit(limit)
        )
        if exclude_session_id:
            q = q.where(ButlerMessageRow.session_id != str(exclude_session_id))
        rows = (await session.execute(q)).all()
        return [str(r[0])[:200] for r in rows]
    except Exception as exc:  # noqa: BLE001
        from app.db.ownership import RuntimeOwnershipError

        if isinstance(exc, RuntimeOwnershipError):
            raise  # 所有权缺失是配置错误：fail-closed，不静默降级丢记忆
        return []  # 数据检索瞬时失败 → 返回空（不拦对话；生产靠 preflight 启动门兜底缺表）
