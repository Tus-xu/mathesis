# coding: utf-8
"""管家跨会话记忆集成测试：record → load 往返（真实 PG；不可达 skip）。"""
from __future__ import annotations

import uuid

import pytest

PG_DSN = "postgres://teacher:teacher_dev_pw@127.0.0.1:5433/teacher_platform"


@pytest.mark.asyncio
async def test_butler_memory_roundtrip():
    """写入一条 user 消息 → 跨会话能读回；排除当前会话生效；按教师隔离。"""
    asyncpg = pytest.importorskip("asyncpg", reason="asyncpg 未安装（非 compose 环境）")
    try:
        conn = await asyncpg.connect(PG_DSN, timeout=5)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 不可达（compose 未启动？）：{exc}")
    await conn.close()

    from app.db.session import session_factory
    from app.domains.butler.memory import load_butler_memories, record_butler_message

    tid_a, tid_b = str(uuid.uuid4()), str(uuid.uuid4())

    async with session_factory()() as session:
        await record_butler_message(session, teacher_id=tid_a, session_id="sess-1", role="user", text_="张老师偏好用真题引入课堂")
        await record_butler_message(session, teacher_id=tid_a, session_id="sess-2", role="user", text_="高二(5)班用新人教A版教材")
        await record_butler_message(session, teacher_id=tid_b, session_id="sess-1", role="user", text_="李老师只讲函数")

        # 跨会话：新会话能看到旧会话的记忆
        mems = await load_butler_memories(session, teacher_id=tid_a)
        joined = " | ".join(mems)
        assert "张老师偏好用真题引入课堂" in joined
        assert "高二(5)班用新人教A版教材" in joined
        # 教师隔离：不串台
        mems_b = await load_butler_memories(session, teacher_id=tid_b)
        assert all("张老师" not in m for m in mems_b)
        # 排除当前会话
        mems_ex = await load_butler_memories(session, teacher_id=tid_a, exclude_session_id="sess-2")
        assert all("高二(5)班" not in m for m in mems_ex)

        # 清理本用例数据
        from sqlalchemy import delete

        from app.db.models.butler import ButlerMessageRow

        await session.execute(delete(ButlerMessageRow).where(ButlerMessageRow.teacher_id.in_([uuid.UUID(tid_a), uuid.UUID(tid_b)])))
        await session.commit()
