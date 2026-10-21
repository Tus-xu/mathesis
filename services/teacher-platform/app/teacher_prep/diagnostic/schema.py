"""R18 学前诊断任务 proposal schema（LLM 输出契约 + 教师可编辑草稿契约）。

铁律（DECISIONS.md §6/§8，与 blueprint/schema.py 同构）：
- LLM 只产生 proposal；教师审阅/编辑后确认发布才成为事实（R13 PreClassDiagnosticTask）；
- 每题必须携带：目标（objective）/ 类型（item_type）/ 答案（answer）/ 解析（explanation）/
  难度（difficulty）/ 知识点（kp）/ 证据（source_ref）；缺一即 schema 校验失败；
- 范围校验（scope）：诊断只允许覆盖「本课先备知识 / 关键误区 / 信心」，且
  kp 必须 ∈ 已确认蓝图 + 上下文选择声明的允许范围；不足时**不得偷偷扩大知识点范围**
  ——任何 kp 越界 → ScopeViolation（proposal 不成为事实，绝不静默裁剪）；
- 时长 5–10 分钟、题目 1–8 题：把预习当轻量诊断，不变成大量作业；
- 模型不得生成任意 HTML/CSS：所有文本字段过 no-HTML 校验。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..text_safety import HTML_TAG_RE as _HTML_TAG_RE  # noqa: E402  -- E-1

# 契约版本（语义断裂 bump；reader fail closed）
DIAGNOSTIC_SCHEMA_VERSION = "0.1.0"

# 与 textbook_v2 locator v1/legacy ref 匹配（防发明引用；与 blueprint/schema 同构）
_LOCATOR_REF_RE = re.compile(
    r"^textbook_v2/locator/v1/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/chunk/[0-9a-fA-F-]{36}|/chunk-range/\d+-\d+)?"
    r"(?:/(?:unit|problem|asset)/[0-9a-fA-F-]{36})?$"
    r"|^textbook_v2/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/(?:unit|problem|chunk)/[0-9a-fA-F-]{36})?$"
)
# 真标签白名单正则（E-1：数学不等式 0<a<1 时 f(a)>0 不得误判为 HTML）；import 置于文件头（E402）

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

# 目标类型（只诊断三类，禁止扩大到其他维度）
OBJECTIVES = Literal["prior_knowledge", "misconception", "confidence"]
# 题型
ITEM_TYPES = Literal["single_choice", "fill_blank", "short_answer", "confidence_rating"]
# 难度
DIFFICULTIES = Literal["easy", "medium", "hard"]

# 范围硬约束（可测常量）
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 10
MAX_ITEMS = 8
MAX_KPS_DECLARED = 10


def _no_html(text: str, field: str) -> str:
    text = text.strip()
    if _HTML_TAG_RE.search(text):
        raise ValueError(f"{field} 禁止包含 HTML 标签（模型不得生成任意 HTML/CSS）")
    return text


def _ref_shape(ref: str | None) -> str | None:
    if ref is None:
        return None
    if not _LOCATOR_REF_RE.match(ref):
        raise ValueError("source_ref 必须是可解析的教材 SourceLocator ref")
    return ref


def norm_kp(text: str) -> str:
    """知识点规范化（范围比对用）：去空白、全角括号归一。"""
    t = (text or "").strip().replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", t)


class DiagnosticItemProposal(BaseModel):
    """单题 proposal（LLM 输出 / 教师可编辑载荷共用；全部字段必填）。

    - `kp`：本诊断的知识点（范围校验输入；必须 ∈ 允许范围）；
    - `source_ref`：证据定位（可选；提供则必须可解析；grounding 层再验一次）。
    """

    model_config = ConfigDict(extra="forbid")

    item_no: int = Field(ge=1)
    objective: OBJECTIVES  # type: ignore[valid-type]
    item_type: ITEM_TYPES  # type: ignore[valid-type]
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=500)
    explanation: str = Field(min_length=1, max_length=800)
    difficulty: DIFFICULTIES  # type: ignore[valid-type]
    kp: str = Field(min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=512)
    max_score: float | None = Field(default=None, ge=0.5, le=10)

    @field_validator("question", "answer", "explanation")
    @classmethod
    def _no_html(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        return _no_html(v, info.field_name)

    @field_validator("kp")
    @classmethod
    def _kp_shape(cls, v: str) -> str:
        v = v.strip()
        if _HTML_TAG_RE.search(v):
            raise ValueError("kp 禁止包含 HTML 标签")
        return v

    @field_validator("source_ref")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        return _ref_shape(v)


class DiagnosticProposal(BaseModel):
    """LLM 输出的学前诊断任务 proposal（schema 校验后才被接受为 proposal）。

    - `context_snapshot_hash`：锁定来源上下文（换上下文 → 旧 proposal 失效）；
    - `blueprint_id`：来源已确认蓝图；
    - `knowledge_points`：范围声明（诊断覆盖的知识点全集）；
    - `estimated_duration_minutes`：5–10 分钟硬约束；
    - `items`：1–8 题（不把预习变成大量作业）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = DIAGNOSTIC_SCHEMA_VERSION
    context_snapshot_hash: Sha256Hex
    blueprint_id: str = Field(min_length=36, max_length=36)
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    estimated_duration_minutes: int = Field(ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES)
    knowledge_points: list[str] = Field(default_factory=list, min_length=1, max_length=MAX_KPS_DECLARED)
    items: list[DiagnosticItemProposal] = Field(default_factory=list, min_length=1, max_length=MAX_ITEMS)
    warnings_ack: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("blueprint_id")
    @classmethod
    def _uuid_shape(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", v):
            raise ValueError("blueprint_id 必须是 UUID 形态")
        return v

    @field_validator("title")
    @classmethod
    def _no_html_title(cls, v: str) -> str:
        return _no_html(v, "title")

    @field_validator("description")
    @classmethod
    def _no_html_desc(cls, v: str | None) -> str | None:
        return _no_html(v, "description") if v is not None else None

    @field_validator("knowledge_points")
    @classmethod
    def _kps_no_html(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for p in v:
            p = p.strip()
            if not p:
                continue
            if _HTML_TAG_RE.search(p):
                raise ValueError("knowledge_points 禁止包含 HTML 标签")
            cleaned.append(p)
        return cleaned


class DiagnosticDraft(BaseModel):
    """教师可编辑草稿（tp_diagnostic_tasks.body 的权威 shape；编辑/落库共用）。

    与 DiagnosticProposal 的差异：不声明 context_snapshot_hash/blueprint_id
    （编辑不改变来源），其余字段与范围硬约束保持一致。
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    estimated_duration_minutes: int = Field(ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES)
    knowledge_points: list[str] = Field(default_factory=list, min_length=1, max_length=MAX_KPS_DECLARED)
    items: list[DiagnosticItemProposal] = Field(default_factory=list, min_length=1, max_length=MAX_ITEMS)

    @field_validator("title")
    @classmethod
    def _no_html_title(cls, v: str) -> str:
        return _no_html(v, "title")

    @field_validator("description")
    @classmethod
    def _no_html_desc(cls, v: str | None) -> str | None:
        return _no_html(v, "description") if v is not None else None

    @field_validator("knowledge_points")
    @classmethod
    def _kps_no_html(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for p in v:
            p = p.strip()
            if not p:
                continue
            if _HTML_TAG_RE.search(p):
                raise ValueError("knowledge_points 禁止包含 HTML 标签")
            cleaned.append(p)
        return cleaned

    @field_validator("items")
    @classmethod
    def _item_nos_unique(cls, v: list[DiagnosticItemProposal]) -> list[DiagnosticItemProposal]:
        seen: set[int] = set()
        for it in v:
            if it.item_no in seen:
                raise ValueError(f"items.item_no 重复: {it.item_no}")
            seen.add(it.item_no)
        return v


def draft_from_proposal(proposal: DiagnosticProposal) -> DiagnosticDraft:
    """proposal → 可编辑草稿（编辑链起点；不复制上下文锚点）。"""
    return DiagnosticDraft(
        title=proposal.title,
        description=proposal.description,
        estimated_duration_minutes=proposal.estimated_duration_minutes,
        knowledge_points=proposal.knowledge_points,
        items=[it.model_dump() for it in proposal.items],
    )


# ── 范围校验（铁律：不足时不得偷偷扩大知识点范围）──────────────────────────────


def allowed_scope_of(blueprint: dict | None, selection_kps: list[str] | None) -> set[str]:
    """允许的诊断知识点范围：已确认蓝图的重点/难点/先备知识 + 上下文选择的知识点。

    越界判定以规范化字符串精确匹配；LLM 只能在该集合内声明 kp。
    """
    allowed: set[str] = set()
    blueprint = blueprint or {}
    for key in ("key_points", "difficult_points", "prior_knowledge"):
        for item in blueprint.get(key) or []:
            text = (item or {}).get("text")
            if text:
                allowed.add(norm_kp(text))
    for kp in selection_kps or []:
        if kp:
            allowed.add(norm_kp(kp))
    return allowed


def scope_violations(*, kps: list[str], allowed: set[str]) -> list[str]:
    """返回越界知识点列表（空 = 范围合规）。不允许空 allowed 时静默放行。"""
    if not allowed:
        # 蓝图/上下文均未提供任何允许范围 → 视为范围不足，任何声明均越界（fail closed）
        return list(dict.fromkeys(kps))
    return list(dict.fromkeys(k for k in kps if norm_kp(k) not in allowed))


def check_diagnostic_scope(
    proposal: DiagnosticProposal,
    *,
    blueprint: dict | None,
    selection_kps: list[str] | None,
) -> list[str]:
    """proposal 全范围校验：items.kp + 声明的 knowledge_points 都必须 ∈ 允许范围。"""
    item_kps = [it.kp for it in proposal.items]
    return scope_violations(kps=[*item_kps, *proposal.knowledge_points], allowed=allowed_scope_of(blueprint, selection_kps))


def collect_diagnostic_refs(proposal: DiagnosticProposal) -> list[str]:
    """收集 proposal 中全部 source_ref（去重、保序）——grounding 校验输入。"""
    refs: list[str] = []
    seen: set[str] = set()
    for it in proposal.items:
        if it.source_ref and it.source_ref not in seen:
            seen.add(it.source_ref)
            refs.append(it.source_ref)
    return refs


def draft_to_items(draft: DiagnosticDraft) -> list[dict]:  # 视图/编辑往返用
    return [it.model_dump(mode="json") for it in draft.items]


__all__ = [
    "DIAGNOSTIC_SCHEMA_VERSION",
    "DiagnosticProposal",
    "DiagnosticItemProposal",
    "DiagnosticDraft",
    "draft_from_proposal",
    "allowed_scope_of",
    "scope_violations",
    "check_diagnostic_scope",
    "collect_diagnostic_refs",
    "norm_kp",
    "MIN_DURATION_MINUTES",
    "MAX_DURATION_MINUTES",
    "MAX_ITEMS",
]
