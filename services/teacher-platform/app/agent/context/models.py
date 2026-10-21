"""上下文与证据的类型契约（V2.1 Knowledge-Native Runtime，§4.10）。

R11.5-B（DECISIONS §5 / 安全基线）：EvidencePack 增加
  - token_budget / truncated 字段（ContextAssembler 预算控制）；
  - summary 字段（会话/域总结注入，typed refs 显式化）；
  - UNTRUSTED 数据边界（教材/学生/上传/用户输入/tool 输出均为 UNTRUSTED DATA，
    装配时标记 untrusted 并做指令注入行为中和，不进入系统指令槽位）；
  - snapshot_hash：不可变快照 hash（装配产物一次性固化，后续任何改动可检测）。
LearningStateProvider 签名保持冻结。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# ── 会话现场（V3ButlerContext，前端每轮捎带）──
TeacherCtx = dict[str, Any]  # {teacher_id, session_id, class_id, deck_id, slide_index, selection}

# UNTRUSTED 数据边界（KNR：外部来源数据一律不可信，禁止当系统指令）
UNTRUSTED_KINDS: frozenset[str] = frozenset(
    {"textbook", "student_input", "upload", "tool_output", "user_input", "turn_reference", "class_data"}
)

# 指令注入模式（装配时对 UNTRUSTED 文本做行为中和：截断/包裹为数据而非指令）
_INSTRUCTION_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "忽略以上指令",
    "忽略之前的指令",
    "you are now",
    "你现在是",
    "system prompt",
    "系统提示词",
    "print the system prompt",
    "repeat the prompt",
    "do not follow",
    "不要遵循",
    "override",
    "覆盖系统",
)


def snapshot_hash_of(payload: dict[str, Any]) -> str:
    """不可变快照 hash：规范化 JSON（排序键）→ sha256。

    装配产物（EvidencePack.model_dump）经此固化；后续任何改动（哪怕是重排序）
    都会改变 hash → 可防篡改/可审计。
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def neutralize_untrusted(text: str) -> str:
    """UNTRUSTED 文本指令注入中和：命中注入模式时截断为数据引用，不保留原指令文本。"""
    lowered = text.lower()
    for pattern in _INSTRUCTION_INJECTION_PATTERNS:
        if pattern in lowered:
            # 中标 → 只保留前缀（数据语义），拒绝把指令当指令执行
            idx = lowered.index(pattern)
            return text[:idx].rstrip() + "…[untrusted-boundary: 截断潜在指令注入内容]"
    return text


class TeacherSessionContext(BaseModel):
    """会话级粘性上下文（K1：teacher_session_contexts，会话级主键=session_id）。

    新会话从该教师最近会话种子拷贝一次，此后两会话互不串台。
    """

    session_id: str
    teacher_id: str
    textbook_id: str | None = None
    chapter_path: str | None = None
    class_id: str | None = None
    deck_id: str | None = None
    retrieval_scope_ids: list[str] = Field(default_factory=list)  # V2.1.1 收口②
    sticky: dict[str, Any] = Field(default_factory=dict)  # 其余粘性字段（避免频繁迁移）


class TurnReference(BaseModel):
    """单轮引用（K2）：scope=TURN_ONLY，只属当前轮，下一轮自动失效。"""

    scope: Literal["TURN_ONLY"] = "TURN_ONLY"
    kind: str  # e.g. "slide" | "selection" | "message"
    ref_id: str
    label: str | None = None


# ── EvidencePack（V2.1 证据强制链）──


class EvidenceSource(BaseModel):
    """可溯源证据项；与 ai_runs.evidence_manifest 的紧凑清单同构（收口④，不存原文）。"""

    source_id: str  # knowledge_documents.id 或业务表 id
    source_type: str  # textbook/curriculum/benchmark/quiz/deck/class_data/teacher_memory
    locator: str | None = None  # 页码/题号/章节
    retrieval_score: float | None = None
    content_hash: str | None = None
    used: bool = False
    # R11.5-B：UNTRUSTED 数据边界（教材/学生/上传/用户输入/tool 输出默认不可信）
    untrusted: bool = False


class EvidencePack(BaseModel):
    """运行时完整证据包（不落库；ai_runs 只落 to_manifest() 的紧凑投影）。

    R11.5-B 增补：
    - summary：会话/域总结（typed refs 显式化，来自 conversation_summaries 或调用方）；
    - token_budget / truncated：ContextAssembler 预算控制；truncated=True 表示预算内截断过；
    - untrusted_boundary：装配产物整体是否含 UNTRUSTED 数据（含 true 时下游不得当系统指令）；
    - snapshot_hash：不可变快照 hash（装配结果一次性固化）。
    """

    feature: str
    query: str
    textbook_facts: list[str] = Field(default_factory=list)
    curriculum_constraints: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    class_context: dict[str, Any] = Field(default_factory=dict)
    teacher_preferences: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    summary: str | None = None
    token_budget: int | None = None
    truncated: bool = False
    untrusted_boundary: bool = False
    snapshot_hash: str = ""

    def to_manifest(self) -> list[dict[str, Any]]:
        """紧凑 EvidenceManifest：[{source_id, source_type, locator, retrieval_score, content_hash, used}]。

        保持既有收口④契约（不含 untrusted 标记，避免 ai_runs.evidence_manifest 漂移）；
        untrusted 边界只在运行时 EvidencePack/sources 上表达。
        """
        return [
            {
                "source_id": s.source_id,
                "source_type": s.source_type,
                "locator": s.locator,
                "retrieval_score": s.retrieval_score,
                "content_hash": s.content_hash,
                "used": s.used,
            }
            for s in self.sources
        ]

    @property
    def evidence_source_ids(self) -> list[str]:
        return [s.source_id for s in self.sources]

    def freeze_snapshot(self) -> str:
        """置入不可变 snapshot_hash（基于当前装配产物）。"""
        self.snapshot_hash = snapshot_hash_of(self.model_dump(exclude={"snapshot_hash"}))
        return self.snapshot_hash

    def verify_snapshot(self) -> bool:
        """校验 snapshot_hash 是否仍与当前产物一致（防篡改/防未经声明修改）。"""
        if not self.snapshot_hash:
            return True  # 未冻结视为通过（兼容旧路径）
        return self.snapshot_hash == snapshot_hash_of(self.model_dump(exclude={"snapshot_hash"}))


class KnowledgeWorkspace(Protocol):
    """B2 供给（未交付期用协议级 fake；B1 禁止自建检索实现）。"""

    async def retrieve(
        self, query: str, *, scope_ids: list[str], feature: str
    ) -> tuple[list[str], list[EvidenceSource]]:
        """返回 (事实片段文本列表, 可溯源来源列表)。"""
        ...


class LearningStateProvider(Protocol):
    """学情上下文协议（V2.1.1 收口③，签名冻结）。

    P0 实现 LocalTeachingEvidenceProvider 聚合 classroom/grading/assignment 真实行
    （correct_rate/error_cluster/recent_attempts/class_distribution；空态即空，不编造）。
    返回 dict 的键集合即本协议的数据契约。
    """

    async def learning_state(self, class_id: str, *, topic: str | None = None) -> dict[str, Any]: ...
