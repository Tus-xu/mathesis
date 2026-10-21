"""R11.5-B：ToolCallRecord 记录 + 脱敏 + 协议层 invoke_tool 审计单测。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agent.tool_calls import MemoryToolCallSink, PostgresToolCallSink, ToolCallRecord
from app.agent.tools.protocol import (
    ToolRegistry,
    ToolSpec,
    get_tool_call_sink,
    invoke_tool,
    set_tool_call_sink,
)
from app.llm.ai_runs import digest_payload


class QuizParams(BaseModel):
    kp_name: str
    api_key: str | None = None


def _registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def handler(params, ctx):
        return {"ok": True, "kp": params.kp_name}

    reg.register(
        ToolSpec(
            name="quiz_import_question",
            label="题目入库",
            kind="write",
            description="x",
            params_schema=QuizParams,
            confirm_required=True,
        ),
        handler,
    )
    return reg


async def test_invoke_tool_records_with_hash_and_redaction() -> None:
    sink = MemoryToolCallSink()
    set_tool_call_sink(sink)  # 全局注入（protocol 模块级）
    try:
        result = await invoke_tool(
            "quiz_import_question",
            {"kp_name": "椭圆", "api_key": "sk-SECRET12345"},
            {"teacher_id": "t1", "session_id": "s1"},
            confirmed=True,
            registry=_registry(),
            run_id="run-1",
        )
        assert result["ok"] is True
        assert len(sink.records) == 1
        rec: ToolCallRecord = sink.records[0]
        assert rec.tool_name == "quiz_import_question"
        assert rec.status == "ok"
        assert rec.params_hash and rec.params_hash != digest_payload({"kp_name": "椭圆"})  # 全参 hash（含 key）
        # 脱敏：redacted params 绝不包含明文密钥
        assert rec.params_redacted["api_key"] == "<redacted>"
        assert "SECRET12345" not in repr(rec.params_redacted)
        assert rec.teacher_id == "t1" and rec.session_id == "s1" and rec.run_id == "run-1"
    finally:
        set_tool_call_sink(None)


async def test_denied_write_still_records_denied_status() -> None:
    sink = MemoryToolCallSink()
    set_tool_call_sink(sink)
    try:
        from app.agent.tools.protocol import ToolDenied

        with pytest.raises(ToolDenied):
            await invoke_tool(
                "quiz_import_question",
                {"kp_name": "椭圆"},
                {"teacher_id": "t1"},
                confirmed=False,  # 红线 1：未经确认直调
                registry=_registry(),
            )
        assert sink.records and sink.records[0].status == "denied"
        assert "确认" in (sink.records[0].error or "")
    finally:
        set_tool_call_sink(None)


async def test_handler_error_records_error_status() -> None:
    sink = MemoryToolCallSink()
    set_tool_call_sink(sink)
    reg = ToolRegistry()

    async def bad_handler(params, ctx):
        raise RuntimeError("boom sk-TOPSECRETKEY99")

    reg.register(
        ToolSpec(name="boom_tool", label="x", kind="read", description="x", params_schema=QuizParams),
        bad_handler,
    )
    try:
        with pytest.raises(RuntimeError):
            await invoke_tool("boom_tool", {"kp_name": "x"}, {}, registry=reg)
        rec = sink.records[0]
        assert rec.status == "error"
        assert "sk-" not in (rec.error or "")  # 错误脱敏
    finally:
        set_tool_call_sink(None)


def _digest_of(obj) -> str:
    from app.llm.ai_runs import digest_payload

    return digest_payload(obj)


def test_postgres_sink_fails_closed_on_missing_table() -> None:
    class FakeSessionFactory:
        def __call__(self):
            class S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    class R:
                        def scalar(self):
                            return None

                    return R()

            return S()

    from app.db.ownership import RuntimeOwnershipError

    sink = PostgresToolCallSink(FakeSessionFactory())
    with pytest.raises(RuntimeOwnershipError):
        import asyncio

        asyncio.run(sink.write(ToolCallRecord(tool_name="x", params_hash="h", status="ok")))


def test_get_tool_call_sink_default_none() -> None:
    assert get_tool_call_sink() is None