"""R20 TeachingBlueprint revision proposal schema（LLM 输出契约，schema 校验后才是提案）。

铁律（DECISIONS.md §6/§7/§8，与 R16 proposal schema 同法）：
- LLM 只产生修订提案载荷；教师确认前不成为任何事实（确认闸门复用 confirm_blueprint）；
- 模型不得生成任意 HTML/CSS：所有文本字段复用 BlueprintItem 的 no-HTML 校验；
- 所有 source_ref 必须是可解析 SourceLocator（grounding 校验在 revision_projector）；
- `base` 指向当前 approved revision（未经确认不得成为 approved revision）；
- `profile` 指向版本化 ClassLearningProfile（时效/低覆盖/冲突 warning 随提案保留）；
- `evidence` findings 的 `applies_to` 命中 8 维之一（驱动 diff 证据绑定）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .diff import DIFF_DIMENSIONS
from .schema import BLUEPRINT_PROPOSAL_SCHEMA_VERSION, BlueprintItem, TeachingBlueprintProposal

# 修订提案契约版本（语义断裂 bump；reader fail closed）
REVISION_PROPOSAL_SCHEMA_VERSION = "0.1.0"

FindingCode = Literal[
    "low_mastery",
    "misconception",
    "participation_gap",
    "confidence_gap",
    "coverage_low",
    "stale_profile",
    "evidence_conflict",
    "teacher_preference",
    "other",
]


class BaseBlueprintRef(BaseModel):
    """被修订的 approved revision 锚点（旧 revision 不可变保留）。"""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    revision: int = Field(ge=1)


class ProfileEvidenceRef(BaseModel):
    """版本化 Profile 证据引用（时效/覆盖/warnings 必须随提案保留）。"""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    profile_revision: int = Field(ge=1)
    computed_at: datetime
    expires_at: datetime | None = None
    stale: bool = False
    coverage_ratio: float = Field(default=0.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=12)


class EvidenceFinding(BaseModel):
    """一条画像证据发现（驱动 diff 的证据绑定）。"""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1, max_length=64)
    code: FindingCode  # type: ignore[valid-type]
    dimension: str = Field(min_length=1, max_length=32)
    applies_to: list[str] = Field(default_factory=list, max_length=8)
    detail: str = Field(min_length=1, max_length=500)
    ref: str | None = Field(default=None, max_length=512)

    @field_validator("dimension")
    @classmethod
    def _dimension_valid(cls, v: str) -> str:
        if v not in DIFF_DIMENSIONS:
            raise ValueError(f"dimension 非法: {v!r}（允许 {DIFF_DIMENSIONS}）")
        return v

    @field_validator("applies_to")
    @classmethod
    def _applies_to_valid(cls, v: list[str]) -> list[str]:
        invalid = [d for d in v if d not in DIFF_DIMENSIONS]
        if invalid:
            raise ValueError(f"applies_to 含非法维度: {invalid}（允许 {DIFF_DIMENSIONS}）")
        return v


class TeachingBlueprintRevisionProposal(BaseModel):
    """上下文 + 已确认蓝图 + 版本化 Profile → 可编辑修订提案（schema 校验后接受）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = REVISION_PROPOSAL_SCHEMA_VERSION
    base_blueprint_schema_version: str = BLUEPRINT_PROPOSAL_SCHEMA_VERSION
    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base: BaseBlueprintRef
    profile: ProfileEvidenceRef
    # 修订后的完整蓝图内容（复用 R16 schema；目标/重点难点/时间/活动/例题在内）
    blueprint: TeachingBlueprintProposal
    # 新增维度：分层支持 / 诊断响应
    stratified_support: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    diagnostic_response: list[BlueprintItem] = Field(default_factory=list, max_length=12)
    # 证据发现（驱动 diff 证据绑定）
    evidence: list[EvidenceFinding] = Field(default_factory=list, max_length=20)
    # 低覆盖/旧 Profile/证据冲突 warning 显式回执
    warnings: list[str] = Field(default_factory=list, max_length=30)
    warnings_ack: list[str] = Field(default_factory=list, max_length=30)


def collect_revision_refs(proposal: TeachingBlueprintRevisionProposal) -> list[str]:
    """收集修订提案全部 source_ref（去重保序）——grounding 校验输入。"""
    from .schema import collect_refs

    refs = collect_refs(proposal.blueprint)
    seen: set[str] = set(refs)
    for item in (*proposal.stratified_support, *proposal.diagnostic_response):
        if item.source_ref and item.source_ref not in seen:
            seen.add(item.source_ref)
            refs.append(item.source_ref)
    return refs


__all__ = [
    "REVISION_PROPOSAL_SCHEMA_VERSION",
    "TeachingBlueprintRevisionProposal",
    "BaseBlueprintRef",
    "ProfileEvidenceRef",
    "EvidenceFinding",
    "collect_revision_refs",
]
