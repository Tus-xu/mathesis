"""活动线程解析（kernel/thread.py）

M2 对话重构（规格 §1）：消息表引入 parent_id 线性链 + superseded_at 版本分支后，
「会话当前对话内容」不再等于 created_at 线性序列，而是沿活动分支解析出的线程：

- 取会话最近 cap 条消息，构 children map（parent_id → 按 created_at 升序的子列表）
- 从根（parent_id 为 NULL 或 parent 不在集合内——cap 截断窗口的首条）出发
- 每层选 superseded_at IS NULL 的子节点（多个取最新），直到底
- 返回活动线程（时间升序）

上下文装配（memory.get_working_memory）与 messages 端点都必须走这里，不再线性取。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message

# 线程解析窗口上限（会话消息数超出时只解析最近 cap 条，
# 窗口首条消息 parent 不在集合内，按根处理——保证截断后线程仍完整可走）
THREAD_CAP = 600


def build_children_map(messages: list[Message]) -> dict[uuid.UUID | None, list[Message]]:
    """parent_id → 按 (created_at, id) 升序的子列表"""
    children: dict[uuid.UUID | None, list[Message]] = {}
    for m in messages:
        children.setdefault(m.parent_id, []).append(m)
    for group in children.values():
        group.sort(key=lambda x: (x.created_at, x.id))
    return children


def walk_active_thread(
    messages: list[Message],
) -> tuple[list[Message], dict[uuid.UUID | None, list[Message]]]:
    """在已取出的消息集合上解析活动线程（纯函数，便于测试）

    返回 (活动线程升序, children map)。
    """
    id_set = {m.id for m in messages}
    children = build_children_map(messages)

    # 根候选：parent_id 为 NULL 或 parent 不在集合内（cap 截断窗口首条）
    roots = [m for m in messages if m.parent_id is None or m.parent_id not in id_set]
    if not roots:
        return [], children
    # 根同样遵守活动规则：superseded_at IS NULL 优先（编辑首条消息后旧根被 supersede，
    # 不剔除会把线程错误地带回旧分支）；多个取最新；全被 supersede 的异常数据兜底取最早
    active_roots = [m for m in roots if m.superseded_at is None]
    pool = active_roots or roots
    pool.sort(key=lambda x: (x.created_at, x.id))
    node: Message | None = pool[-1] if active_roots else pool[0]

    thread: list[Message] = []
    while node is not None:
        thread.append(node)
        group = children.get(node.id) or []
        # 活动子节点：superseded_at IS NULL，多个取最新
        active = [c for c in group if c.superseded_at is None]
        node = active[-1] if active else None
    return thread, children


async def resolve_thread(
    session: AsyncSession,
    conversation_id: str,
    cap: int = THREAD_CAP,
) -> tuple[list[Message], dict[uuid.UUID | None, list[Message]]]:
    """取会话最近 cap 条消息并解析活动线程

    返回 (活动线程升序, children map)；children map 供 versions 元数据计算复用。
    """
    result = await session.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.deleted_at.is_(None),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(cap)
    )
    messages = list(result.scalars().all())
    messages.reverse()  # 升序
    return walk_active_thread(messages)


def versions_of(
    msg: Message,
    children: dict[uuid.UUID | None, list[Message]],
) -> dict:
    """版本元数据：siblings = children(m.parent_id)（created_at 升序）

    versions = {index: m 在 siblings 中的 1 基位置, count: len(siblings), ids: 兄弟 id 升序}
    count == 1 时前端不显示版本导航。
    """
    siblings = children.get(msg.parent_id) or [msg]
    ids = [str(s.id) for s in siblings]
    try:
        index = ids.index(str(msg.id)) + 1
    except ValueError:
        index = 1
    return {"index": index, "count": len(siblings), "ids": ids}
