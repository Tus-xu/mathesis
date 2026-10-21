"""Butler Kernel v2 运行账本模型（阶段 3B）

覆盖：
- AgentRun / AgentStep / ToolInvocation 表结构与约束；
- UniqueConstraint(user_id, client_request_id)；
- AgentStep(run_id, sequence) 唯一；
- 账本只存脱敏摘要，不存原始密钥/完整文本/工具输入输出。
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.agent_run import AgentRun, AgentStep, ToolInvocation
from app.models.database import async_session_factory
from app.models.user import User


async def _make_user(s) -> uuid.UUID:
    u = User(phone=f"13{uuid.uuid4().int % 100000000:08d}", nickname="")
    s.add(u)
    await s.commit()
    return u.id


async def test_agent_run_has_unique_user_client_request():
    async with async_session_factory() as s:
        uid = await _make_user(s)
        run = AgentRun(
            user_id=uid,
            role="student",
            scene="student.dashboard",
            client_request_id="same-req",
            intent="review",
        )
        s.add(run)
        await s.commit()
        dup = AgentRun(
            user_id=uid,
            role="student",
            scene="student.dashboard",
            client_request_id="same-req",
            intent="review",
        )
        s.add(dup)
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_agent_step_run_sequence_unique():
    async with async_session_factory() as s:
        uid = await _make_user(s)
        run = AgentRun(
            user_id=uid,
            role="student",
            scene="student.dashboard",
            client_request_id="crid-step",
            intent="review",
        )
        s.add(run)
        await s.commit()
        s.add(AgentStep(run_id=run.id, sequence=1, stage="context", status="ok"))
        await s.commit()
        s.add(AgentStep(run_id=run.id, sequence=1, stage="plan", status="ok"))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_ledger_columns_and_indexes():
    cols = {c.name for c in AgentRun.__table__.columns}
    assert {"user_id", "client_request_id", "intent", "status", "degraded",
            "model_request_count", "tool_call_count", "latency_ms", "error_code"} <= cols
    # 约束/索引存在
    names = {ix.name for ix in AgentRun.__table__.indexes}
    assert "ix_agent_runs_user_created" in names
    step_cols = {c.name for c in AgentStep.__table__.columns}
    assert {"run_id", "sequence", "stage", "latency_ms"} <= step_cols
    ti_cols = {c.name for c in ToolInvocation.__table__.columns}
    assert {"run_id", "tool_name", "tool_version", "status", "idempotency_key",
            "arguments_digest", "result_digest", "error_code"} <= ti_cols


async def test_ledger_stores_digests_not_raw():
    """账本字段是摘要/元数据，不存在原始内容列（密钥/完整文本/工具输入输出）。"""
    for model in (AgentRun, AgentStep, ToolInvocation):
        cols = {c.name for c in model.__table__.columns}
        for forbidden in ("api_key", "secret", "prompt", "content", "arguments_raw", "result_raw"):
            assert forbidden not in cols, f"{model.__tablename__} 不应有 {forbidden} 列"


async def test_tool_invocation_persist_roundtrip():
    async with async_session_factory() as s:
        uid = await _make_user(s)
        run = AgentRun(
            user_id=uid,
            role="student",
            scene="student.dashboard",
            client_request_id="crid-ti",
            intent="review",
        )
        s.add(run)
        await s.commit()
        ti = ToolInvocation(
            run_id=run.id,
            tool_name="test.write",
            tool_version="1.0.0",
            status="ok",
            latency_ms=3,
            idempotency_key="k1",
            arguments_digest="abc123",
            result_digest="def456",
        )
        s.add(ti)
        await s.commit()
        fresh = await s.get(ToolInvocation, ti.id)
        assert fresh.tool_name == "test.write"
        assert fresh.arguments_digest == "abc123"
