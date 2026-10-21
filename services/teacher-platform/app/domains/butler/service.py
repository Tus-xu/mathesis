"""Butler 端点壳服务层（B1-owned `app/domains/butler/**`；路由由 B3 挂到 /butler/chat）。

消费方式（§8）：`stream_butler(...)` 返回 AgentEvent 流；B3 薄路由把它映射为
SSE `event: <name>\ndata: <json>` 并接 Last-Event-ID 恢复（sse_channel）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.events import AgentEvent
from app.agent.graphs.butler import build_butler_graph
from app.agent.stream_adapter import graph_to_events


@dataclass
class ButlerMessage:
    session_id: str
    teacher_id: str
    message: str
    has_images: bool = False
    force_web_search: bool = False
    context: dict[str, Any] = field(default_factory=dict)  # 前端捎带（route_title/selection...）
    history: list[dict] = field(default_factory=list)  # 多轮上下文：[{role:user|assistant, text}]
    resume: dict[str, Any] | None = None  # 确认卡裁决：{"approved": bool}（Command(resume)）


class ButlerService:
    """进程内持有编译好的图；checkpointer 生产换 PostgresSaver（ManagedCheckpointer）。"""

    def __init__(
        self,
        *,
        gateway=None,
        registry=None,
        search_web=None,
        checkpointer=None,
        prompt_renderer=None,
        memories_provider=None,
    ) -> None:
        builder = build_butler_graph(
            gateway=gateway, registry=registry, search_web=search_web,
            prompt_renderer=prompt_renderer, memories_provider=memories_provider,
        )
        self.graph: CompiledStateGraph = builder.compile(checkpointer=checkpointer or MemorySaver())
        self._checkpointer = checkpointer

    async def stream_butler(self, message: ButlerMessage) -> AsyncIterator[AgentEvent]:
        """一轮管家对话（或一次确认卡裁决）→ AgentEvent 流（meta…done）。

        新轮的 meta 由图内 route 节点唯一发出（含 intent，对齐 mock）；resume 轮
        route 不重执行，由适配器补 meta（session_id + resumed）。
        """
        if message.resume is not None:
            input_state: Any = Command(resume=message.resume)
            meta_payload = {"session_id": message.session_id, "resumed": True}
        else:
            input_state = {
                "session_id": message.session_id,
                "teacher_id": message.teacher_id,
                "message": message.message,
                "has_images": message.has_images,
                "force_web_search": message.force_web_search,
                "ctx": message.context,
                "history": message.history,
            }
            meta_payload = None
        config = {"configurable": {"thread_id": message.session_id}}
        async for event in graph_to_events(self.graph, input_state, config, meta_payload=meta_payload, done_payload={"finish_reason": "stop"}):
            yield event
