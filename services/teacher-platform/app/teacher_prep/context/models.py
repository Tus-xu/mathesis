"""R16 TextbookTeachingContext 不可变契约（8100 业务实体）。

设计约束（DECISIONS.md §2/§3/§6）：
- **不可变**：`frozen=True`，构建后任何字段不可改写；内容随
  `snapshot_hash` 一次性固化，后续任何改动可检测（防篡改/可审计）；
- **确定性**：`canonical_snapshot_hash` 对规范化 JSON（排序键）做 sha256；
  同 DB 状态 + 同确认输入 → 同 hash（可复现验收）;
- **无编造**：教学目标/重点难点只收教材显式证据（summary/introduction 等），
  无证据即空 + 显式警告，绝不推测；
- **可定位**：每条事实带 `source_ref`（resolve_source_ref 可解析）与
  `chunk_ref`（canonical v1 locator），见 locator.py；
- 与 R08 检索聚合（textbook_v2.retrieval.dto.TextbookTeachingContext）分层：
  本模型是 8100 业务快照（含 selection/coverage/confidence/hash），
  检索聚合是教材域原始装配；本包不 import 检索聚合模型（只复用其加载函数）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 上下文契约版本（内容语义断裂必须 bump）
CONTEXT_SCHEMA_VERSION = "1.0.0"

# 警告码枚举（builder 显式产出；前端 allowlist 渲染依据）
WarningCode = Literal[
    "no_units",
    "rule_extraction_fallback",
    "low_coverage",
    "knowledge_point_unmatched",
    "no_objectives_evidence",
    "no_key_difficult_evidence",
    "no_design_intent",
    "no_prior_knowledge",
    "figure_placeholder",
    "bad_chunk",
    "injection_detected",
    "conflicting_evidence",
    "incomplete_chapter",
    "pending_review",
    "low_confidence",
    "truncated",
]

# 教学事实分类桶（与 R07 10 类 content unit + problem kind 映射后收口）
FactKind = Literal["unit", "problem"]


class ContextSelection(BaseModel):
    """教师确认的上下文选择（构建输入，归一化后不可变）。

    - `knowledge_points`：构建时排序去重（确定性）；
    - `lesson_scope`：课时说明（如“第一课时”），仅元数据；缩小范围用
      `chunk_range`（全局 1-based 行号区间，可选）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    section_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    # E-3 可选班级：None = 未绑定班级（班级画像类证据降级，教材上下文不受影响）
    class_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    knowledge_points: list[str] = Field(default_factory=list, max_length=50)
    lesson_scope: str | None = Field(default=None, max_length=200)
    chunk_range: tuple[int, int] | None = None  # 可选课时范围（全局 1-based 行号）


class ContextFact(BaseModel):
    """一条教材事实（unit 或 problem），带可解析定位。

    `text` 已做 UNTRUSTED 行为中和（neutralize_untrusted）；原文本不经此字段外泄。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    kind: FactKind
    fact_type: str  # R07 10 类之一 / problem kind
    text: str
    solution: str | None = None
    confidence: float | None = None
    review_status: str | None = None
    provenance: str | None = None
    source_ref: str = ""  # resolve_source_ref 可解析（legacy 兼容）
    chunk_ref: str | None = None  # canonical v1 locator（resolve_locator 可解析）
    section_ref: str = ""
    chunk_start: int | None = None
    chunk_end: int | None = None


class ContextCoverage(BaseModel):
    """覆盖报告：知识点命中 + chunk 区间覆盖，全部确定性可复现。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expected_points: int
    matched_points: int
    point_ratio: float  # 0..1；无知识点输入 = 1.0（不判缺失）
    expected_chunks: int  # 期望区间长度（chunk_start..chunk_end）
    present_chunks: int  # 实际在区间内的有序 chunk 数
    chunk_ratio: float  # 0..1；区间为空 = 1.0
    overall: float  # 加权综合（0..1）


class ContextWarning(BaseModel):
    """显式警告（不编造；前端 allowlist 渲染）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: WarningCode
    message: str
    ref: str | None = None


class SourceInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    publisher: str
    edition: str
    grade: str
    volume: str
    rights_status: str
    batch_id: str | None = None
    manifest_sha256: str | None = None
    source_kind: str = "direct-chunk"


class SectionInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    textbook_id: str
    title: str
    section_path: str | None = None
    level: int
    order: int
    chunk_start: int | None = None
    chunk_end: int | None = None


class TextbookTeachingContext(BaseModel):
    """不可变教材教学上下文（R16 核心产物）。

    字段语义：
    - `objectives`：教学目标（summary/introduction 显式证据；无则空+警告）；
    - `prior_knowledge`：先备知识（父章节 definition/theorem/summary 事实）；
    - `concepts/theorems/examples/inquiries`：概念/定理/例题/探究事实分桶；
    - `design_intent`：教材设计意图（introduction/derivation/summary 信号）；
    - `key_difficult_points`：重点难点（仅教材显式“重点/难点”措辞证据；
      无则空 + 警告，由 Blueprint proposal（LLM+教师确认）派生）；
    - `coverage/confidence/warnings`：确定性覆盖/置信/显式警告；
    - `snapshot_hash`：canonical sha256，构建即固化。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = CONTEXT_SCHEMA_VERSION
    snapshot_hash: str = ""
    built_at: datetime | None = None  # 仅审计展示；不参与 hash（保持确定性）
    selection: ContextSelection
    source: SourceInfo
    section: SectionInfo
    parent: SectionInfo | None = None
    children: list[SectionInfo] = Field(default_factory=list)

    facts: list[ContextFact] = Field(default_factory=list)
    objectives: list[ContextFact] = Field(default_factory=list)
    prior_knowledge: list[ContextFact] = Field(default_factory=list)
    concepts: list[ContextFact] = Field(default_factory=list)
    theorems: list[ContextFact] = Field(default_factory=list)
    examples: list[ContextFact] = Field(default_factory=list)
    inquiries: list[ContextFact] = Field(default_factory=list)
    design_intent: list[ContextFact] = Field(default_factory=list)
    key_difficult_points: list[ContextFact] = Field(default_factory=list)

    coverage: ContextCoverage
    confidence: float
    warnings: list[ContextWarning] = Field(default_factory=list)

    locator: str | None = None  # section 级 canonical v1 locator
    chunk_order: list[int] = Field(default_factory=list)


def canonical_json(obj: Any) -> str:
    """规范化 JSON（排序键、紧凑分隔、无空白差异）——确定性 hash 的前提。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_snapshot_hash(model: BaseModel) -> str:
    """不可变快照 hash：model_dump(mode=json) → 规范化 JSON → sha256。

    排除 `built_at`（时间戳破坏可复现性）与 `snapshot_hash`（自引用）；
    字段顺序/键序差异不影响 hash；同内容重建 → 同 hash。
    """
    payload = model.model_dump(mode="json", exclude={"built_at", "snapshot_hash"})
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
