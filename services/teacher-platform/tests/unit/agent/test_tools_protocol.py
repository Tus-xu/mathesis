"""ToolSpec 协议单测（红线 1：write 必须 confirm；协议独占 B1）。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agent.tools.protocol import (
    TeacherCtx,
    ToolDenied,
    ToolRegistry,
    ToolSpec,
    invoke_tool,
    register_tool,
)


class QuizImportParams(BaseModel):
    count: int
    kp_code: str


class ReadParams(BaseModel):
    query: str


def _write_spec() -> ToolSpec:
    return ToolSpec(
        name="quiz_import",
        label="题库导入",
        kind="write",
        description="把生成题目写入题库（草稿态）",
        params_schema=QuizImportParams,
        confirm_required=True,
        domain="quiz",
    )


def test_write_tool_must_confirm() -> None:
    with pytest.raises(ValueError, match="confirm_required"):
        ToolSpec(
            name="bad_write",
            label="坏工具",
            kind="write",
            description="x",
            params_schema=QuizImportParams,
            confirm_required=False,
        )


def test_spec_name_and_schema_validation() -> None:
    with pytest.raises(ValueError, match="不合法"):
        ToolSpec(name="Bad-Name", label="x", kind="read", description="x", params_schema=ReadParams)
    with pytest.raises(ValueError, match="Pydantic"):
        ToolSpec(name="ok_name", label="x", kind="read", description="x", params_schema=dict)  # type: ignore[arg-type]


def test_duplicate_register_rejected() -> None:
    registry = ToolRegistry()

    async def handler(params, ctx):  # pragma: no cover
        return {}

    registry.register(_write_spec(), handler)
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(_write_spec(), handler)


async def test_invoke_write_requires_confirmation() -> None:
    registry = ToolRegistry()
    calls: list[tuple[str, dict]] = []

    async def handler(params: QuizImportParams, ctx: TeacherCtx) -> dict:
        calls.append(("quiz_import", params.model_dump()))
        return {"ok": True, "draft_id": "d1"}

    register_tool(_write_spec(), handler, registry=registry)

    # 红线 1：未确认的 write 调用直接拒绝
    with pytest.raises(ToolDenied, match="红线 1"):
        await invoke_tool("quiz_import", {"count": 5, "kp_code": "G10-3"}, TeacherCtx(), registry=registry)

    # 确认流 resume 后（confirmed=True）才执行
    result = await invoke_tool(
        "quiz_import", {"count": 5, "kp_code": "G10-3"}, TeacherCtx(), confirmed=True, registry=registry
    )
    assert result["ok"] is True
    assert calls == [("quiz_import", {"count": 5, "kp_code": "G10-3"})]


async def test_invoke_params_validated_by_schema() -> None:
    registry = ToolRegistry()

    async def handler(params: QuizImportParams, ctx: TeacherCtx) -> dict:  # pragma: no cover
        return {"ok": True}

    register_tool(_write_spec(), handler, registry=registry)
    result = await invoke_tool(
        "quiz_import", {"count": "不是数字", "kp_code": "x"}, TeacherCtx(), confirmed=True, registry=registry
    )
    assert result["ok"] is False
    assert "参数校验失败" in result["error"]


async def test_unknown_tool_raises() -> None:
    with pytest.raises(KeyError, match="未注册"):
        await invoke_tool("nope", {}, TeacherCtx())


def test_fc_schema_exposes_read_and_write_only() -> None:
    registry = ToolRegistry()

    async def handler(params, ctx):  # pragma: no cover
        return {}

    registry.register(_write_spec(), handler)
    registry.register(
        ToolSpec(name="search_kb", label="查知识库", kind="read", description="检索", params_schema=ReadParams),
        handler,
    )
    registry.register(
        ToolSpec(name="goto_slide", label="跳转", kind="frontend", description="翻页", params_schema=ReadParams),
        handler,
    )
    names = [entry["function"]["name"] for entry in registry.fc_schema()]
    assert names == ["search_kb", "quiz_import"]  # frontend 不进 FC；read 在 write 前
