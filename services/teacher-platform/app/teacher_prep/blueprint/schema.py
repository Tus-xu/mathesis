"""R16 TeachingBlueprint proposal schema（LLM 输出契约，schema 校验后才是 proposal）。

铁律（DECISIONS.md §6/§7/§8）：
- LLM 只产生 proposal；教师确认前不成为任何事实（确认/驳回闸门在 R14 service）；
- 模型不得生成任意 HTML/CSS：所有文本字段过 no-HTML 校验，命中即 ValidationError；
- 每条可编辑项可选携带 `source_ref`：必须能解析到上下文（grounding 校验在
  projector，禁止模型发明引用）；
- 本 schema 是 proposal 的 body 契约（propose_blueprint 的 body 写入 tp_blueprints
  时以此为准）；JSON Schema 由 tests 生成验证（与 R13 契约同法）。
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..text_safety import HTML_TAG_RE as _HTML_TAG_RE  # noqa: E402  -- E-1

# proposal 契约版本（语义断裂 bump；reader fail closed）
BLUEPRINT_PROPOSAL_SCHEMA_VERSION = "0.1.0"

# 与 locator.py v1/legacy ref 匹配（LLM 只能引用上下文定位器，防发明引用）
_LOCATOR_REF_RE = re.compile(
    r"^textbook_v2/locator/v1/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/chunk/[0-9a-fA-F-]{36}|/chunk-range/\d+-\d+)?"
    r"(?:/(?:unit|problem|asset)/[0-9a-fA-F-]{36})?$"
    r"|^textbook_v2/source/[0-9a-fA-F-]{36}"
    r"(?:/section/[0-9a-fA-F-]{36})?"
    r"(?:/(?:unit|problem|chunk)/[0-9a-fA-F-]{36})?$"
)
# HTML 标记检测（禁止模型输出任意 HTML/CSS）；真标签白名单，数学不等式不误判（E-1）
# import 置于文件头（E402）；text_safety 仅依赖 stdlib，无环。

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BlueprintItem(BaseModel):
    """一条可编辑提案项（可选 grounding 引用；文本禁止 HTML）。

    W1-A additive（可选、默认 None；旧 proposal body 读取完全兼容）：`source_origin`
    标注条目来源，用于**例题三源分标签**（KERNEL-RUBRIC §1.2.7）：
    - `"textbook_problems"`：来自教材例题/习题（必有 source_ref locator）；
    - `"ai_generated"`：教材无例题时由 AI 生成的变式（禁止冒充教材例题）；
    - `None`：未标注（既有行为不变）。
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=400)
    source_ref: str | None = Field(default=None, max_length=512)
    source_origin: str | None = Field(default=None, max_length=32)

    @field_validator("text")
    @classmethod
    def _no_html_in_text(cls, v: str) -> str:
        if _HTML_TAG_RE.search(v):
            raise ValueError("提案文本禁止包含 HTML 标签（模型不得生成任意 HTML/CSS）")
        return v.strip()

    @field_validator("source_origin")
    @classmethod
    def _origin_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if v not in ("textbook_problems", "textbook_unit", "ai_generated"):
            raise ValueError("source_origin 只能是 textbook_problems/textbook_unit/ai_generated")
        return v

    @field_validator("source_ref")
    @classmethod
    def _ref_shape(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _LOCATOR_REF_RE.match(v):
            raise ValueError("source_ref 必须是可解析的教材 SourceLocator ref")
        return v


class LessonStage(BaseModel):
    """教学流程阶段（枚举受限；教师可编辑文本，结构固定）。

    R3 additive（全部可选、默认空 —— 旧 proposal body 不受影响）：
    - `teacher_questions` / `student_activities` / `board_notes`：展开卡左栏三组；
    - `hints`：教学提示（基于学情/教材；无则诚实隐藏）。
    """

    model_config = ConfigDict(extra="forbid")

    stage: Literal["introduction", "new_content", "example", "exercise", "summary", "homework"]
    title: str = Field(min_length=1, max_length=80)
    # W1.3 additive（可选、默认空——旧 proposal body 不受影响）：环节描述性摘要
    # （陈述句，前端节点摘要优先取它，避免「全疑问句」；§3.2）。≤80 字为目标，
    # schema 上限放宽到 120 容忍 LLM 轻微超限（超限走兜底梯级代价更高）。
    summary: str | None = Field(default=None, max_length=120)
    items: list[BlueprintItem] = Field(default_factory=list, max_length=20)
    duration_minutes: int | None = Field(default=None, ge=1, le=120)
    teacher_questions: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    student_activities: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    board_notes: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    hints: list[BlueprintItem] = Field(default_factory=list, max_length=12)

    @field_validator("title")
    @classmethod
    def _no_html_in_title(cls, v: str) -> str:
        if _HTML_TAG_RE.search(v):
            raise ValueError("阶段标题禁止包含 HTML 标签")
        return v.strip()

    @field_validator("summary")
    @classmethod
    def _no_html_in_summary(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if _HTML_TAG_RE.search(v):
            raise ValueError("环节摘要禁止包含 HTML 标签")
        return v.strip() or None


class TeachingBlueprintProposal(BaseModel):
    """上下文 → 可编辑 Blueprint proposal（schema 校验后才被接受为 proposal）。

    - `warnings_ack`：LLM 显式回执的上下文警告码（低覆盖等必须承认并降级措辞）；
    - `context_snapshot_hash`：锁定来源上下文（换上下文 → 换 hash → 旧 proposal 失效）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BLUEPRINT_PROPOSAL_SCHEMA_VERSION
    context_snapshot_hash: Sha256Hex
    title: str = Field(min_length=1, max_length=120)
    objectives: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    prior_knowledge: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    concepts: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    theorems: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    examples: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    inquiries: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    design_intent: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    key_points: list[BlueprintItem] = Field(default_factory=list, max_length=8)
    difficult_points: list[BlueprintItem] = Field(default_factory=list, max_length=8)
    # FROZEN-CONTRACT §F.4：作业布置 / 教学反思——LLM 推导桶（教材无原文，由 LLM 基于
    # 教材事实推导；无依据留空并由投影器记 warning，禁止编造）。
    homework: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    reflection: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    lesson_flow: list[LessonStage] = Field(default_factory=list, min_length=1, max_length=20)
    warnings_ack: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def _no_html_title(cls, v: str) -> str:
        if _HTML_TAG_RE.search(v):
            raise ValueError("蓝图标题禁止包含 HTML 标签")
        return v.strip()


def collect_refs(proposal: TeachingBlueprintProposal) -> list[str]:
    """收集 proposal 中全部 source_ref（去重、保序）——grounding 校验输入。"""
    refs: list[str] = []
    seen: set[str] = set()
    for bucket in (
        "objectives",
        "prior_knowledge",
        "concepts",
        "theorems",
        "examples",
        "inquiries",
        "design_intent",
        "key_points",
        "difficult_points",
    ):
        for item in getattr(proposal, bucket):
            if item.source_ref and item.source_ref not in seen:
                seen.add(item.source_ref)
                refs.append(item.source_ref)
    for stage in proposal.lesson_flow:
        for item in stage.items:
            if item.source_ref and item.source_ref not in seen:
                seen.add(item.source_ref)
                refs.append(item.source_ref)
        for group in (stage.teacher_questions, stage.student_activities, stage.board_notes, stage.hints):
            for item in group:
                if item.source_ref and item.source_ref not in seen:
                    seen.add(item.source_ref)
                    refs.append(item.source_ref)
    return refs


def resolvable_refs_of(context: object) -> set[str]:
    """上下文可解析定位器集合：事实 source_ref/chunk_ref + section locator。

    LLM 只能引用该集合内的 ref；集合外一律视为发明引用（GroundingViolation）。
    """
    refs: set[str] = set()
    for f in context.facts:
        if f.source_ref:
            refs.add(f.source_ref)
        if f.chunk_ref:
            refs.add(f.chunk_ref)
    for f in context.prior_knowledge:
        if f.source_ref:
            refs.add(f.source_ref)
        if f.chunk_ref:
            refs.add(f.chunk_ref)
    if context.locator:
        refs.add(context.locator)
    return refs
