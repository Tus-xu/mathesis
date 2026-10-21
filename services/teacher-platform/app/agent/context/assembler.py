"""ContextAssembler：会话粘性上下文 + 单轮引用 + EvidencePack 组装（V2.1 §4.10）。

R11.5-B 增补（DECISIONS §5）：
  - token_budget：预算内裁剪事实/示例/约束（串行段逐段预算），超限标 truncated；
  - summary：注入会话/域总结（typed refs 显式化）；
  - UNTRUSTED 数据边界：外部来源（教材/学生/上传/tool 输出/用户输入/turn_ref）
    一律 neutralize_untrusted（指令注入行为中和）并标记 untrusted_boundary；
  - snapshot_hash：装配产物一次性 freeze_snapshot（不可变快照）。
其余语义保持既有契约：get_sticky/upsert_from_request/turn_refs/assemble。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EVIDENCE_REQUIRED_FEATURES = {"deck", "plan", "variant", "review", "classroom_ai", "quiz_gen"}

# UNTRUSTED 计词换算：约 4 字符/token（中文近似；预算为保守上限时的粗略估计）
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, (len(str(text)) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def _trim_to_budget(segments: list[str], remaining: int) -> tuple[list[str], bool]:
    """按 token 预算裁剪字符串段；返回 (裁剪后文本, 是否截断)。"""
    out: list[str] = []
    used = 0
    truncated = False
    for seg in segments:
        cost = _estimate_tokens(seg)
        if used + cost > remaining:
            truncated = True
            keep = max(0, remaining - used) * _CHARS_PER_TOKEN
            if keep > 0:
                out.append(seg[:keep] + "…[truncated]")
            break
        out.append(seg)
        used += cost
    return out, truncated


class ContextAssembler:
    def __init__(
        self,
        store,
        *,
        knowledge=None,  # KnowledgeWorkspace | None（B2 未交付期为 None/fake）
        learning=None,  # LearningStateProvider | None
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._learning = learning

    async def get_sticky(self, session_id: str, teacher_id: str | None = None) -> "TeacherSessionContext":
        """取会话粘性上下文；不存在时创建（有 teacher_id 则从其最近会话种子拷贝一次）。"""
        existing = await self._store.get(session_id)
        if existing is not None:
            return existing
        if teacher_id is None:
            raise ValueError(f"会话 {session_id} 无粘性上下文且未提供 teacher_id，无法创建")
        seed = await self._store.latest_for_teacher(teacher_id, exclude_session=session_id)
        ctx = TeacherSessionContext(session_id=session_id, teacher_id=teacher_id)
        if seed is not None:
            # 种子拷贝一次：教材/章节/班级/检索范围延续；会话身份不拷贝
            ctx.textbook_id = seed.textbook_id
            ctx.chapter_path = seed.chapter_path
            ctx.class_id = seed.class_id
            ctx.retrieval_scope_ids = list(seed.retrieval_scope_ids)
            ctx.sticky = dict(seed.sticky)
        await self._store.upsert(ctx)
        return ctx

    async def upsert_from_request(self, session_id: str, teacher_id: str, ctx_payload: dict) -> "TeacherSessionContext":
        """前端捎带 context 的合并 upsert（None 字段不覆盖已有粘性值）。"""
        ctx = await self.get_sticky(session_id, teacher_id)
        for field in ("textbook_id", "chapter_path", "class_id", "deck_id"):
            value = ctx_payload.get(field)
            if value is not None:
                setattr(ctx, field, value)
        scope = ctx_payload.get("retrieval_scope_ids")
        if scope is not None:
            ctx.retrieval_scope_ids = list(scope)
        sticky_patch = ctx_payload.get("sticky")
        if isinstance(sticky_patch, dict):
            ctx.sticky = {**ctx.sticky, **sticky_patch}
        await self._store.upsert(ctx)
        return ctx

    async def assemble(
        self,
        ctx: "TeacherSessionContext | dict",
        query: str,
        *,
        feature: str,
        turn_refs: list["TurnReference"] | None = None,
        token_budget: int | None = None,
        summary: str | None = None,
    ) -> "EvidencePack":
        """证据组装（生成类图的第一节点；KNR 红线 9）。

        ctx 允许裸 dict（worker 侧生成任务常无完整会话行），内部归一化。
        turn_refs 只进 EvidencePack 的定位信息，不写入粘性存储（K2：TURN_ONLY）。
        token_budget：预算内裁剪事实/示例/约束/偏好；超限标 truncated。
        summary：注入会话/域总结（typed refs 显式化）。
        返回的 pack 已 freeze_snapshot()（不可变 hash）。
        """
        normalize = isinstance(ctx, dict)
        if normalize:
            ctx = TeacherSessionContext(
                session_id=ctx.get("session_id", "worker"),
                teacher_id=ctx.get("teacher_id", "unknown"),
                class_id=ctx.get("class_id"),
                deck_id=ctx.get("deck_id"),
                retrieval_scope_ids=list(ctx.get("retrieval_scope_ids") or []),
            )
        pack = EvidencePack(feature=feature, query=query, token_budget=token_budget)
        untrusted = bool(turn_refs)
        if self._knowledge is not None:
            facts, sources = await self._knowledge.retrieve(
                query, scope_ids=list(ctx.retrieval_scope_ids), feature=feature
            )
            # UNTRUSTED 边界：检索片段/来源默认不可信 → 指令注入行为中和
            pack.textbook_facts = [neutralize_untrusted(f) for f in facts]
            for s in sources:
                s.untrusted = s.source_type in UNTRUSTED_KINDS
                untrusted = untrusted or s.untrusted
            pack.sources = list(sources)
        if self._learning is not None and ctx.class_id:
            state = await self._learning.learning_state(ctx.class_id)
            pack.class_context = state
            untrusted = True  # 学情数据（学生侧）→ UNTRUSTED 边界
        if turn_refs:
            for ref in turn_refs:
                src = EvidenceSource(
                    source_id=ref.ref_id,
                    source_type="turn_reference",
                    locator=ref.kind,
                    used=True,
                    untrusted=True,
                )
                pack.sources.append(src)
            untrusted = True
        if summary:
            pack.summary = neutralize_untrusted(summary)
            untrusted = True

        # token budget：逐段裁剪（事实 → 示例 → 偏好 → 约束），预算不足则截断
        truncated = False
        if token_budget is not None and token_budget > 0:
            budget = token_budget
            pack.textbook_facts, tf = _trim_to_budget(pack.textbook_facts, budget)
            truncated = truncated or tf
            pack.examples, ex = _trim_to_budget(pack.examples, max(1, budget))
            truncated = truncated or ex
            # 偏好/约束轻量兜底（避免预算被大段事实吃光时全空）
            fixed = sum(_estimate_tokens(x) for x in pack.textbook_facts) + sum(
                _estimate_tokens(x) for x in pack.examples
            )
            rest = max(0, token_budget - fixed)
            pack.teacher_preferences, pp = _trim_to_budget(pack.teacher_preferences, rest)
            truncated = truncated or pp
            if rest <= 0:
                pack.teacher_preferences, pack.curriculum_constraints, pack.unresolved_questions = [], [], []
        pack.truncated = truncated
        pack.untrusted_boundary = untrusted
        pack.freeze_snapshot()
        return pack


from .models import (  # noqa: E402
    UNTRUSTED_KINDS,
    EvidencePack,
    EvidenceSource,
    TeacherSessionContext,
    TurnReference,
    neutralize_untrusted,
)