"""事件适配单测：真实 LangGraph 图 → AgentEvent 规范序列（§7.4）。

覆盖三条通路：custom（citation 等）、__interrupt__（card+action 确认流）、
updates 里的 ToolMessage（tool_result）；以及恢复后节点重执行的可见行为。
"""
from __future__ import annotations

from typing import TypedDict

import pytest
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.events import skeleton_of
from app.agent.stream_adapter import graph_to_events


class S(TypedDict):
    decision: dict
    executed: bool
    messages: list


async def _collect(ait):
    return [e async for e in ait]


def _build_graph() -> Any:  # noqa: ANN401
    from typing import Any

    async def ask(state: S) -> dict:
        writer = get_stream_writer()
        writer({"event": "citation", "data": {"sources": [{"title": "人教A版", "url": "https://example.com"}]}})
        decision = interrupt(
            {
                "type": "action",
                "id": "act-1",
                "title": "存入题库",
                "confirm_required": True,
                "status": "pending",
                "tool": "quiz_import",
                "params": {"count": 5},
            }
        )
        return {"decision": decision}

    async def execute(state: S) -> dict:
        tool_msg = ToolMessage(
            content='{"ok": true}', name="quiz_import", tool_call_id="call-1", status="success"
        )
        return {"executed": bool(state["decision"].get("approved")), "messages": [tool_msg]}

    builder = StateGraph(S)
    builder.add_node("ask", ask)
    builder.add_node("execute", execute)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=MemorySaver())


async def test_first_pass_emits_meta_citation_card_action_done() -> None:
    graph = _build_graph()
    config = {"configurable": {"thread_id": "t1"}}
    events = await _collect(graph_to_events(graph, {"executed": False}, config, meta_payload={"session_id": "t1"}))
    names = [e.name for e in events]
    assert names[0] == "meta" and names[-1] == "done"
    assert skeleton_of(names) == ["meta", "citation", "card", "action", "done"]
    card = next(e for e in events if e.name == "card")
    assert card.payload["type"] == "action"  # interrupt 值即 mock 卡片对象
    assert card.payload["tool"] == "quiz_import"
    action = next(e for e in events if e.name == "action")
    assert action.payload["options"] == ["execute", "cancel"]


async def test_resume_pass_maps_tool_result() -> None:
    graph = _build_graph()
    config = {"configurable": {"thread_id": "t2"}}
    await _collect(graph_to_events(graph, {"executed": False}, config))
    events = await _collect(
        graph_to_events(graph, Command(resume={"approved": True}), config, meta_payload={"session_id": "t2", "resumed": True})
    )
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "citation", "tool_result", "done"]
    tool_result = next(e for e in events if e.name == "tool_result")
    assert tool_result.payload["ok"] is True
    assert tool_result.payload["name"] == "quiz_import"


async def test_sse_tuple_serialization() -> None:
    from app.agent.events import AgentEvent

    name, data = AgentEvent("token", {"text": "椭圆"}).sse_tuple()
    assert name == "token"
    assert '"椭圆"' in data


def test_skeleton_folding() -> None:
    assert skeleton_of(["meta", "token", "token", "token", "done"]) == ["meta", "token", "done"]
    assert skeleton_of(["meta", "asr_partial", "asr_partial", "asr_final", "card", "done"]) == [
        "meta",
        "asr_partial",
        "asr_final",
        "card",
        "done",
    ]
