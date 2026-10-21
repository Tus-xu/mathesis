"""LangGraph astream → AgentEvent 适配器（astream→SSE 骨架，M1）。

选型依据 notes/B1-notes.md §1.3：不用 astream_events（custom StreamWriter 事件
在那里不可见），而用合并多路流 `stream_mode=["messages","custom","updates"]`：

- ``messages``：LLM token 级增量 → ``token``；``reasoning_content`` → ``thinking``
- ``custom``  ：节点/工具经 get_stream_writer() 发的 {"event": name, "data": ...}
                → 直接映射为同名 AgentEvent（card/citation/进度等非 LLM 事件的唯一通道）
- ``updates`` ：节点状态增量 → ToolMessage → ``tool_result``；
                ``__interrupt__`` → ``card``（确认卡）+``action``（执行/取消动作）
流首尾由适配器补 ``meta`` 与 ``done``（规范序列 §7.4）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.messages.ai import AIMessageChunk

from .events import AgentEvent


async def graph_to_events(
    graph: Any,
    input_state: Any,
    config: dict,
    *,
    meta_payload: dict | None = None,
    done_payload: dict | None = None,
) -> AsyncIterator[AgentEvent]:
    """跑图并把多路流折叠为 AgentEvent 序列（meta … done）。

    custom 事件约定：节点内 `get_stream_writer()({"event": "citation", "data": {...}})`；
    缺 "event" 键的 custom 载荷视为 progress，包装成 thinking 事件（可观测但不进契约）。
    """
    if meta_payload is not None:
        yield AgentEvent("meta", meta_payload)  # None = 图内节点自带 meta（如 butler route），勿双发
    async for item in graph.astream(input_state, config, stream_mode=["messages", "custom", "updates"], subgraphs=True):
        # subgraphs=True 时 item = (namespace, mode, chunk)；否则 (mode, chunk)
        if len(item) == 3:
            _ns, mode, chunk = item
        else:
            mode, chunk = item
        if mode == "messages":
            for event in _messages_part(chunk):
                yield event
        elif mode == "custom":
            for event in _custom_part(chunk):
                yield event
        elif mode == "updates":
            for event in _updates_part(chunk):
                yield event
    yield AgentEvent("done", done_payload or {})


def _messages_part(chunk: Any) -> list[AgentEvent]:
    if not isinstance(chunk, tuple) or not chunk:
        return []
    message, _meta = chunk[0], chunk[1]
    out: list[AgentEvent] = []
    if isinstance(message, AIMessageChunk):
        reasoning = getattr(message, "reasoning_content", None) or _reasoning_from_additional(message)
        if reasoning:
            out.append(AgentEvent("thinking", {"text": reasoning}))
        content = message.content
        if isinstance(content, str) and content:
            out.append(AgentEvent("token", {"text": content}))
        elif isinstance(content, list):  # 多模态块
            for block in content:
                text = block.get("text") if isinstance(block, dict) else None
                if text:
                    out.append(AgentEvent("token", {"text": text}))
        for tc in getattr(message, "tool_call_chunks", None) or []:
            name = tc.get("name")
            if name:
                out.append(
                    AgentEvent(
                        "tool_call",
                        {"name": name, "args": _safe_json(tc.get("args")), "id": tc.get("id")},
                    )
                )
    return out


def _custom_part(chunk: Any) -> list[AgentEvent]:
    if isinstance(chunk, tuple):
        chunk = chunk[-1]
    if not isinstance(chunk, dict):
        return [AgentEvent("thinking", {"text": str(chunk)})]
    name = chunk.get("event")
    if not name:
        return [AgentEvent("thinking", {"text": chunk.get("text", "")})]
    return [AgentEvent(name, chunk.get("data", {}))]


def _updates_part(chunk: Any) -> list[AgentEvent]:
    if not isinstance(chunk, dict):
        return []
    out: list[AgentEvent] = []
    # interrupt 以 chunk 顶层键 "__interrupt__" 出现（langgraph ≥0.2.31 流协议）
    interrupts = chunk.get("__interrupt__")
    if interrupts:
        for interrupt in interrupts:
            value = getattr(interrupt, "value", interrupt)
            # interrupt 值即确认卡对象（mock card 形状：{type:'action', id, title, ...}）
            card_id = value.get("id") if isinstance(value, dict) else None
            out.append(AgentEvent("card", value))
            out.append(AgentEvent("action", {"card_id": card_id, "options": ["execute", "cancel"]}))
    for _node, update in chunk.items():
        if _node == "__interrupt__" or update is None:
            continue
        if isinstance(update, dict):
            messages = update.get("messages")
            if isinstance(messages, list):
                for m in messages:
                    if isinstance(m, ToolMessage):
                        out.append(
                            AgentEvent(
                                "tool_result",
                                {"ok": m.status != "error", "name": getattr(m, "name", None), "data": _safe_json(m.content)},
                            )
                        )
    return out


def _reasoning_from_additional(message: AIMessageChunk) -> str | None:
    additional = getattr(message, "additional_kwargs", {}) or {}
    return additional.get("reasoning_content")


def _safe_json(value: Any) -> Any:
    import json

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value
