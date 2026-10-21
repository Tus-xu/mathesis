"""R17 生产装配：ConversationService + LLM gateway（惰性单例）。

- 生产 llm_generate：LlmGateway.chat（PostgresAiRunsSink 审计；use_case=teacher_prep_intent）；
- 测试通过 `set_conversation_service_factory` / monkeypatch 注入 fake；
- live 发布：get_publisher().publish_live（Valkey 失败已由 publisher 内部吞并）。
"""

from __future__ import annotations

from collections.abc import Callable

from .orchestrator import ConversationService

ServiceFactory = Callable[[], ConversationService]


def _default_service() -> ConversationService:
    from app.core.events import get_publisher
    from app.db.session import session_factory
    from app.llm.ai_runs import PostgresAiRunsSink
    from app.llm.gateway import LlmGateway

    factory = session_factory()

    async def _intent_llm(prompt: str) -> str:
        gateway = LlmGateway(PostgresAiRunsSink(factory))
        result = await gateway.chat(
            messages=[{"role": "user", "content": prompt}],
            use_case="teacher_prep_intent",
            temperature=0.0,
        )
        return result.content

    return ConversationService(
        llm_generate=_intent_llm,
        live_publisher=get_publisher().publish_live,
        session_factory=factory,
    )


_factory: ServiceFactory = _default_service


def get_conversation_service() -> ConversationService:
    return _factory()


def set_conversation_service_factory(factory: ServiceFactory | None) -> None:
    """测试注入点：替换生产装配（None → 还原默认）。"""
    global _factory
    _factory = factory or _default_service


__all__ = ["get_conversation_service", "set_conversation_service_factory"]
