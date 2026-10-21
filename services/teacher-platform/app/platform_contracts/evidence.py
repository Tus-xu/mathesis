"""契约 6 · EvidenceRef / SourceLocator（P00）。

字段（任务书原文）：
provider / source / chunk or page+bbox / section / table / figure / formula /
quote hash / rights / provenance / retrieved_at / validity。

**教材铁律（DECISIONS §3、AGENTS.md 跨边界约定、M00 DIRECT-CHUNK-GUARD-AUDIT）**：
Teacher 教材 locator **只用 `source → section → chunk`**，
**不新写教材 `page` / `page_refs` / `pNN`**。本契约在**类型层**拒绝这三类字段，
使"新链路写教材页码"在编译/校验期即不可能，而不只靠代码评审。

非教材 source_type（LITERATURE / WEB / DATASET / …）允许 `page` + `bbox` 锚点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import OpaqueRef, Sha256Hex, VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

SourceType = Literal["TEXTBOOK", "LITERATURE", "WEB", "DATASET", "EXPERIMENT", "USER_INPUT"]
SupportRelation = Literal["SUPPORTS", "CHALLENGES", "CONTEXT_ONLY"]
ValidityStatus = Literal["VALID", "STALE", "REVOKED", "UNKNOWN"]

# 教材证据禁止出现的字段名（教材源铁律的机器化）
FORBIDDEN_TEXTBOOK_KEYS = frozenset({"page", "page_no", "page_number", "page_ref", "page_refs", "pnn"})


class BBox(VersionedModel):
    """归一化坐标（0..1），避免像素/DPI 差异导致锚点漂移。"""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class StructuralAnchor(VersionedModel):
    """论文/网页等非教材来源的结构锚点。"""

    section: str | None = Field(default=None, max_length=256)
    table: str | None = Field(default=None, max_length=256)
    figure: str | None = Field(default=None, max_length=256)
    formula: str | None = Field(default=None, max_length=256)
    paragraph: str | None = Field(default=None, max_length=256)


class RightsInfo(VersionedModel):
    """权利与再分发约束（教材必须带 `rights_evidence_ref='队伍确认授权'`）。"""

    rights_evidence_ref: str = Field(min_length=1, max_length=256)
    redistributable: bool = False
    license_id: str | None = Field(default=None, max_length=128)
    quote_length_limit: int | None = Field(default=None, ge=0)


class ProvenanceInfo(VersionedModel):
    retrieved_at: datetime
    parser_version: str = Field(min_length=1, max_length=64)
    provider_version: str | None = Field(default=None, max_length=32)
    index_version: str | None = Field(default=None, max_length=64)
    asset_hash: Sha256Hex | None = None


class ValidityInfo(VersionedModel):
    status: ValidityStatus = "VALID"
    checked_at: datetime
    note: str | None = Field(default=None, max_length=512)


class EvidenceRef(VersionedModel):
    """统一证据引用（教材 / 文献 / 网页 / 数据集 / 实验 / 用户输入）。"""

    evidence_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=512)

    # 教材 direct-chunk 三元组
    section_id: str | None = Field(default=None, max_length=512)
    chunk_id: str | None = Field(default=None, max_length=512)
    unit_id: str | None = Field(default=None, max_length=512)
    problem_id: str | None = Field(default=None, max_length=512)
    asset_id: str | None = Field(default=None, max_length=512)

    # 非教材锚点
    page: int | None = Field(default=None, ge=1)
    bbox: BBox | None = None
    anchor: StructuralAnchor | None = None

    quote_hash: Sha256Hex
    verbatim_excerpt: str | None = Field(default=None, max_length=4000)
    normalized_statement: str | None = Field(default=None, max_length=4000)
    support_relation: SupportRelation = "CONTEXT_ONLY"
    rights: RightsInfo
    provenance: ProvenanceInfo
    validity: ValidityInfo
    created_by_run_id: str | None = Field(default=None, max_length=128)
    locator_version: str = Field(default="1.0.0", max_length=32)

    @field_validator("quote_hash")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _textbook_must_use_source_section_chunk_only(self) -> EvidenceRef:
        extra_keys = {k.lower() for k in (self.model_extra or {})}
        if self.source_type == "TEXTBOOK":
            if not (self.section_id and self.chunk_id):
                raise ContractValidationFailed(
                    "TEXTBOOK 证据必须携带 section_id + chunk_id"
                    "（教材链路铁律：source → section → chunk）"
                )
            if self.page is not None or self.bbox is not None:
                raise ContractPolicyViolation(
                    "TEXTBOOK 证据禁止 page/bbox（教材源铁律：不新写教材 page/page_refs/pNN）"
                )
            hit = extra_keys & FORBIDDEN_TEXTBOOK_KEYS
            if hit:
                raise ContractPolicyViolation(
                    f"TEXTBOOK 证据出现被禁字段 {sorted(hit)}；"
                    "教材 locator 只允许 source→section→chunk"
                )
            if self.rights.rights_evidence_ref == "":
                raise ContractValidationFailed("TEXTBOOK 证据必须携带 rights_evidence_ref")
        else:
            if self.section_id is None and self.chunk_id is None and self.page is None and self.anchor is None:
                raise ContractValidationFailed(
                    f"{self.source_type} 证据必须至少有一个定位锚点（page/anchor/section/chunk）"
                )
        if self.page is not None and self.source_type == "TEXTBOOK":
            raise ContractPolicyViolation("TEXTBOOK 证据禁止 page")
        return self


__all__ = [
    "EvidenceRef",
    "SourceType",
    "SupportRelation",
    "BBox",
    "StructuralAnchor",
    "RightsInfo",
    "ProvenanceInfo",
    "ValidityInfo",
    "FORBIDDEN_TEXTBOOK_KEYS",
]
