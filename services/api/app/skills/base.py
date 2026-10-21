"""Skill 抽象基类与上下文（skills/base.py）

所有 skill 继承 SkillExecutor，通过 manifest 注册。
SkillContext 是内核暴露给 skill 的唯一接口（§8.2）。
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class SkillContext:
    """Skill 执行上下文（内核暴露给 skill 的唯一接口）

    skill 通过 ctx 访问所有内核能力，禁止直接 import providers。
    """

    user_id: str
    user_role: str  # active_role
    conversation_id: str
    request_id: str
    db: AsyncSession

    # 内核能力（由主链路注入）
    llm: Any = None  # ModelRouter
    rag: Any = None  # RAGPipeline
    memory: Any = None  # MemoryManager
    context_assembler: Any = None  # ContextAssembler

    # M2 对话重构：工作记忆截断点（regenerate 时上下文截到该消息（含））
    memory_upto: str | None = None

    # 当前用户消息 id（chat_command 等需要把错题记录关联到具体消息）
    message_id: str | None = None

    # Citation 收集器（skill 通过 set_citations 交给主链路）
    _citations: list[dict] = field(default_factory=list)

    def set_citations(self, citations: list[dict]) -> None:
        """设置引用映射 [{n:1, chunk_id, source, loc}]"""
        self._citations = citations

    def get_citations(self) -> list[dict]:
        """获取已设置的引用"""
        return self._citations


class SkillExecutor(ABC):
    """Skill 执行器基类"""

    manifest: dict  # SkillManifest

    @abstractmethod
    async def run(self, params: dict, ctx: SkillContext) -> AsyncIterator[dict]:
        """产出 SSE 事件序列。

        chat 兜底技能 yield token 事件；
        功能性 skill 通常 yield status(处理中) → token* → card* → 结束。

        禁止 yield meta/citation/badge/done —— 这四个由主链路统一发（§7.7）。

        Yields:
            {"type": "token", "data": {"text": "..."}}
            {"type": "status", "data": {"stage": "...", "text": "..."}}
            {"type": "card", "data": {...}}           # 卡片
            {"type": "figure", "data": {...}}         # F13 可视化图形（可多次，见 kernel/figure_block）
            {"type": "_result_meta", "data": {...}}  # 内部元信息
            {"type": "error", "data": {"code": ..., "message": ..., "recoverable": ...}}
        """
        yield {}  # pragma: no cover

    @property
    def skill_id(self) -> str:
        return self.manifest.get("id", "unknown")

    @property
    def skill_name(self) -> str:
        return self.manifest.get("name", "Unknown")
