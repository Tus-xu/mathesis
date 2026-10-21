"""RV07 · EvidenceCard 契约。

三条铁律在**类型层**落地，不靠代码评审兜底：

1. **类型 ↔ 定位 ↔ 权利三方互锁**：`evidence_type` 决定允许的 locator 维度，
   也决定 `rights` 允许存多少原文。任何一处越界都是构造期异常。
2. **受限/未知权利下不可能存原文**：`quote_max_chars == 0` 时 `snippet` 必须为空
   —— 于是"把受限全文复制进 card"在对象层面就无法表达。
3. **AI 只能 propose**：`EvidenceProposal` 没有 `id` / `status` / `version` /
   `snippet_hash`；这些是**服务端**在验证后才生成的，模型无从设置。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ..federation.contracts import (
    ASSET_FORBIDDEN_KEYS,
    LITERATURE_FORBIDDEN_KEYS,
    TEXTBOOK_FORBIDDEN_KEYS,
)
from ..parser.anchors import AnchorType
from .errors import (
    EvidenceACLViolation,
    EvidenceContractError,
    EvidenceFabricationRejected,
    EvidenceRightsViolation,
)
from .types import (
    TEXTBOOK_LABELS,
    ClaimKind,
    ConfidenceBand,
    EvidenceCardType,
    EvidenceStatus,
    LocatorKind,
    RightsStatus,
    StaleState,
    SupportProfile,
    profile_for,
    supports_claim_kind,
)

#: `EvidenceCard.to_dict()` 必须携带的字段（任务书 §必须完成 1）。
REQUIRED_CARD_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "type",
        "source_ref",
        "locator",
        "snippet",
        "structured_fact",
        "snippet_hash",
        "provenance",
        "rights",
        "retrieved_at",
        "created_by_run",
        "confidence",
        "limitations",
        "status",
        "version",
        "acl",
    }
)

#: `source_ref.record_kind` 的封闭集合。
RECORD_KINDS: frozenset[str] = frozenset(
    {"METADATA_ONLY", "ABSTRACT", "FULL_TEXT", "TEXTBOOK_CHUNK", "PROJECT_ASSET", "DERIVED"}
)

#: 允许出现在 locator 里的锚点类型（与 RV05 `AnchorType` 对齐）。
ALLOWED_ANCHOR_TYPES: frozenset[str] = frozenset(a.value for a in AnchorType)

#: 类型 → 允许的 locator 维度。**唯一**的合法配对表。
ALLOWED_LOCATOR_DIMENSIONS: dict[EvidenceCardType, frozenset[str]] = {
    EvidenceCardType.PAPER_FULL_TEXT: frozenset({"ANCHOR", "DOI", "URL"}),
    EvidenceCardType.PAPER_ABSTRACT: frozenset({"ANCHOR", "DOI", "URL"}),
    EvidenceCardType.PROJECT_ASSET: frozenset({"ANCHOR"}),
    EvidenceCardType.TEXTBOOK: frozenset({"TEXTBOOK"}),
    EvidenceCardType.DATASET: frozenset({"ANCHOR", "DOI", "URL"}),
    EvidenceCardType.WEB: frozenset({"URL", "DOI"}),
    # 派生证据可以指向任何上游定位；但它必须显式列上游 card id（见 SourceRef）。
    EvidenceCardType.DERIVED: frozenset({"ANCHOR", "DOI", "URL", "TEXTBOOK"}),
}

VISIBILITIES: frozenset[str] = frozenset({"private", "session", "project", "tenant"})

#: 外部 locator 的禁止字段：内部存储标识一律不得外泄。
#: 刻意**不**包含 `url` —— 外部定位的 `url` 是合法标识符（资产 locator 才禁止 url）。
EXTERNAL_FORBIDDEN_KEYS: frozenset[str] = ASSET_FORBIDDEN_KEYS - {"url"}

#: 权利档位 → 单条引用允许的最大字符数。`0` 表示**不得存储任何原文**。
QUOTE_MAX_CHARS: dict[RightsStatus, int] = {
    RightsStatus.UNKNOWN: 0,
    RightsStatus.RESTRICTED: 240,
    RightsStatus.LICENSED: 400,
    RightsStatus.PERMITTED: 600,
    RightsStatus.OPEN_ACCESS: 600,
    RightsStatus.PUBLIC_DOMAIN: 1200,
    RightsStatus.INTERNAL: 800,
}

#: 允许在**阅读器**里看到全文的档位（`fulltext_allowed` 之外的第二道门）。
READER_FULLTEXT_STATUSES: frozenset[RightsStatus] = frozenset(
    {
        RightsStatus.PERMITTED,
        RightsStatus.OPEN_ACCESS,
        RightsStatus.PUBLIC_DOMAIN,
        RightsStatus.INTERNAL,
        RightsStatus.LICENSED,
        RightsStatus.RESTRICTED,
    }
)

#: 允许逐字进 **prompt** 的档位。LICENSED / RESTRICTED / UNKNOWN **不在其中**。
PROMPT_VERBATIM_STATUSES: frozenset[RightsStatus] = frozenset(
    {RightsStatus.PERMITTED, RightsStatus.OPEN_ACCESS, RightsStatus.PUBLIC_DOMAIN, RightsStatus.INTERNAL}
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_hex64(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _reject_keys(mapping: Mapping[str, Any], forbidden: frozenset[str], where: str) -> None:
    hit = {k for k in mapping if k in forbidden}
    if hit:
        raise EvidenceContractError(f"{where} 出现禁止字段 {sorted(hit)}")


# --------------------------------------------------------------------------
# 值对象
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRef:
    """来源引用：**证据从哪来**（不是证据内容）。

    `record_kind` 是"摘要冒充原文"的第一道封堵：只有 `FULL_TEXT` 才允许
    支撑逐字引用；`METADATA_ONLY` / `ABSTRACT` 只能降级成摘要级证据。
    """

    provider: str
    provider_record_id: str
    record_kind: str
    fulltext_ref: str = ""
    abstract_ref: str = ""
    url: str = ""
    doi: str = ""
    source_hash: str = ""
    upstream_card_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider or not self.provider_record_id:
            raise EvidenceContractError("SourceRef 必须给出 provider 与 provider_record_id")
        kind = (self.record_kind or "").upper()
        if kind not in RECORD_KINDS:
            raise EvidenceContractError(f"未知 record_kind: {self.record_kind!r}")
        object.__setattr__(self, "record_kind", kind)
        if not _is_hex64(self.source_hash):
            raise EvidenceContractError("SourceRef.source_hash 必须是 64 位十六进制（内容指纹）")
        if kind == "FULL_TEXT" and not self.fulltext_ref:
            raise EvidenceFabricationRejected(
                "record_kind=FULL_TEXT 必须给出 fulltext_ref（否则无法证明拿到的是全文）"
            )
        if kind == "DERIVED" and not self.upstream_card_ids:
            raise EvidenceFabricationRejected(
                "record_kind=DERIVED 必须列出 upstream_card_ids（派生证据不得无上游）"
            )
        object.__setattr__(self, "upstream_card_ids", tuple(str(x) for x in self.upstream_card_ids))

    @property
    def is_full_text(self) -> bool:
        return self.record_kind == "FULL_TEXT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_record_id": self.provider_record_id,
            "record_kind": self.record_kind,
            "fulltext_ref": self.fulltext_ref,
            "abstract_ref": self.abstract_ref,
            "url": self.url,
            "doi": self.doi,
            "source_hash": self.source_hash,
            "upstream_card_ids": list(self.upstream_card_ids),
        }


@dataclass(frozen=True)
class LocatorBinding:
    """定位绑定：**点击证据能回源**的机器形态。

    - `ANCHOR`：指向 RV05 `asset_anchor`（page / bbox / section / table / figure / formula）；
    - `EXTERNAL`：DOI / URL+hash / 教材 source→section→chunk。

    教材三元组与文献 DOI **在同一个类里也不允许混用**：出现 `doi`/`page`/`bbox`
    的教材 locator 当场拒绝（教材 citation 伪 DOI 的封堵点）。
    """

    kind: LocatorKind
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        try:
            kind = LocatorKind(self.kind)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 locator kind: {self.kind!r}") from exc
        data = dict(self.payload or {})
        if kind is LocatorKind.ANCHOR:
            _reject_keys(data, ASSET_FORBIDDEN_KEYS, "ANCHOR locator")
            missing = [
                k
                for k in ("asset_id", "asset_version", "content_hash", "anchor_id", "anchor_type")
                if data.get(k) in (None, "")
            ]
            if missing:
                raise EvidenceContractError(f"ANCHOR locator 缺少 {missing}")
            version = int(data["asset_version"])
            if version < 1:
                raise EvidenceContractError("ANCHOR locator 的 asset_version 必须 >= 1")
            anchor_type = str(data["anchor_type"])
            if anchor_type not in ALLOWED_ANCHOR_TYPES:
                raise EvidenceContractError(f"未知 anchor_type: {anchor_type!r}")
            data["asset_version"] = version
            data["anchor_type"] = anchor_type
        else:
            _reject_keys(data, EXTERNAL_FORBIDDEN_KEYS, "EXTERNAL locator")
            if {"source_id", "section_id", "chunk_id"} <= set(data):
                # 教材 direct-chunk：显式拒绝页码 / DOI / 期刊字段
                _reject_keys(data, TEXTBOOK_FORBIDDEN_KEYS, "教材 locator")
                missing = [k for k in ("source_id", "section_id", "chunk_id") if not data.get(k)]
                if missing:
                    raise EvidenceContractError(f"教材 locator 缺少 {missing}")
            elif data.get("doi"):
                # 文献外部定位：不得夹带教材/资产维度字段
                _reject_keys(data, LITERATURE_FORBIDDEN_KEYS, "文献 locator")
            elif data.get("url"):
                pass
            else:
                raise EvidenceContractError(
                    "EXTERNAL locator 必须给出 doi / url / 教材三元组之一（不得产出空定位）"
                )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", MappingProxyType(data))

    @property
    def dimension(self) -> str:
        if self.kind is LocatorKind.ANCHOR:
            return "ANCHOR"
        keys = set(self.payload)
        if {"source_id", "section_id", "chunk_id"} <= keys:
            return "TEXTBOOK"
        if self.payload.get("doi"):
            return "DOI"
        return "URL"

    @property
    def is_textbook(self) -> bool:
        return self.dimension == "TEXTBOOK"

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, **{k: v for k, v in self.payload.items()}}


@dataclass(frozen=True)
class RightsInfo:
    """权利事实（来自 provider / 资产登记，**不是**模型判断）。"""

    rights_status: RightsStatus
    redistributable: bool = False
    fulltext_allowed: bool = False
    license_id: str = ""
    rights_evidence_ref: str = ""

    def __post_init__(self) -> None:
        try:
            status = RightsStatus(self.rights_status)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 rights_status: {self.rights_status!r}") from exc
        object.__setattr__(self, "rights_status", status)
        if status is not RightsStatus.UNKNOWN and not self.rights_evidence_ref:
            # 声明了权利却没有出处 = 无法复核的权利声明。
            raise EvidenceContractError(
                f"rights_status={status.value} 必须给出 rights_evidence_ref（权利无出处不成立）"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rights_status": self.rights_status.value,
            "redistributable": bool(self.redistributable),
            "fulltext_allowed": bool(self.fulltext_allowed),
            "license_id": self.license_id,
            "rights_evidence_ref": self.rights_evidence_ref,
        }


@dataclass(frozen=True)
class RightsPolicy:
    """权利档位**推导**出的可执行边界（唯一推导入口在 `rights.policy_for`）。"""

    quote_max_chars: int
    reader_fulltext_visible: bool
    export_included: bool
    prompt_verbatim_allowed: bool
    log_verbatim_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_max_chars": self.quote_max_chars,
            "reader_fulltext_visible": self.reader_fulltext_visible,
            "export_included": self.export_included,
            "prompt_verbatim_allowed": self.prompt_verbatim_allowed,
            "log_verbatim_allowed": self.log_verbatim_allowed,
        }


@dataclass(frozen=True)
class Provenance:
    """溯源：这条证据是怎么被拿到的（版本 + 指纹）。"""

    source_hash: str
    parser_version: str
    provider: str
    provider_version: str = ""
    retrieved_at: str = ""
    index_version: str = ""

    def __post_init__(self) -> None:
        if not _is_hex64(self.source_hash):
            raise EvidenceContractError("Provenance.source_hash 必须是 64 位十六进制")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_hash": self.source_hash,
            "parser_version": self.parser_version,
            "provider": self.provider,
            "provider_version": self.provider_version,
            "retrieved_at": self.retrieved_at,
            "index_version": self.index_version,
        }


@dataclass(frozen=True)
class Confidence:
    band: ConfidenceBand
    score: float
    method: str

    def __post_init__(self) -> None:
        try:
            band = ConfidenceBand(self.band)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 confidence band: {self.band!r}") from exc
        if not 0.0 <= float(self.score) <= 1.0:
            raise EvidenceContractError("Confidence.score 必须落在 [0, 1]")
        if not self.method:
            raise EvidenceContractError("Confidence.method 必须说明置信来源")
        object.__setattr__(self, "band", band)
        object.__setattr__(self, "score", float(self.score))

    def as_dict(self) -> dict[str, Any]:
        return {"band": self.band.value, "score": self.score, "method": self.method}


@dataclass(frozen=True)
class StructuredFact:
    """结构化事实：**非逐字**的可验证陈述。

    权利禁止存原文时（`quote_max_chars == 0`），card 仍能携带事实 —— 但
    `normalized` 是归一化陈述，不是原文照抄。
    """

    predicate: str
    subject: str
    object: str
    qualifier: str = ""
    unit: str = ""
    normalized: str = ""

    def __post_init__(self) -> None:
        if not self.predicate or not self.subject:
            raise EvidenceContractError("StructuredFact 必须给出 predicate 与 subject")
        if not self.normalized:
            raise EvidenceContractError("StructuredFact.normalized 不得为空（须可复核）")

    def as_dict(self) -> dict[str, Any]:
        return {
            "predicate": self.predicate,
            "subject": self.subject,
            "object": self.object,
            "qualifier": self.qualifier,
            "unit": self.unit,
            "normalized": self.normalized,
        }


@dataclass(frozen=True)
class EvidenceACL:
    """访问控制：证据卡只能被授权范围读到。"""

    tenant_id: str
    project_id: str = ""
    session_id: str = ""
    owner_id: str = ""
    visibility: str = "project"
    allowed_roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise EvidenceACLViolation("EvidenceACL.tenant_id 不得为空（租户是硬边界）")
        if self.visibility not in VISIBILITIES:
            raise EvidenceACLViolation(
                f"未知 visibility: {self.visibility!r}（允许 {sorted(VISIBILITIES)}）"
            )
        if self.visibility in {"session", "project"} and not (self.session_id or self.project_id):
            raise EvidenceACLViolation(
                f"visibility={self.visibility} 必须给出 session_id 或 project_id"
            )
        object.__setattr__(self, "allowed_roles", tuple(str(r) for r in self.allowed_roles))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "visibility": self.visibility,
            "allowed_roles": list(self.allowed_roles),
        }


# --------------------------------------------------------------------------
# EvidenceCard
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceCard:
    """事实对象。**AI 不能构造它** —— 只能构造 `EvidenceProposal`。

    构造期即完成 schema / locator / rights / ACL 四类校验；任一不过即异常。
    """

    card_id: str
    evidence_type: EvidenceCardType
    source_ref: SourceRef
    locator: LocatorBinding
    snippet: str
    structured_fact: StructuredFact | None
    snippet_hash: str
    provenance: Provenance
    rights: RightsInfo
    retrieved_at: datetime
    created_by_run: str
    confidence: Confidence
    limitations: tuple[str, ...]
    status: EvidenceStatus
    version: int
    acl: EvidenceACL
    stale_state: StaleState = StaleState.FRESH
    stale_reason: str = ""
    labels: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    # -- 校验 --------------------------------------------------------------

    def __post_init__(self) -> None:
        if not self.card_id:
            raise EvidenceContractError("EvidenceCard.card_id 不得为空")
        try:
            card_type = EvidenceCardType(self.evidence_type)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 evidence_type: {self.evidence_type!r}") from exc
        try:
            status = EvidenceStatus(self.status)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 status: {self.status!r}") from exc
        try:
            stale = StaleState(self.stale_state)
        except ValueError as exc:
            raise EvidenceContractError(f"未知 stale_state: {self.stale_state!r}") from exc
        if int(self.version) < 1:
            raise EvidenceContractError("EvidenceCard.version 必须 >= 1")
        if not isinstance(self.retrieved_at, datetime):
            raise EvidenceContractError("EvidenceCard.retrieved_at 必须是 datetime")
        if not self.created_by_run:
            raise EvidenceContractError("EvidenceCard.created_by_run 不得为空（没有 run 就没有证据）")

        # 1) snippet hash 必须可验证
        if self.snippet_hash != _sha(self.snippet):
            raise EvidenceContractError(
                "snippet_hash 与 snippet 不一致（证据完整性不可验证）"
            )
        # 2) snippet 与 structured_fact 至少有一个
        if not self.snippet and self.structured_fact is None:
            raise EvidenceContractError(
                "EvidenceCard 必须携带 snippet 或 structured_fact（不得是空证据）"
            )
        # 3) 类型 ↔ locator 维度
        allowed = ALLOWED_LOCATOR_DIMENSIONS[card_type]
        if self.locator.dimension not in allowed:
            raise EvidenceContractError(
                f"evidence_type={card_type.value} 不允许 locator 维度 "
                f"{self.locator.dimension}（允许 {sorted(allowed)}）"
            )
        # 4) 全文类型必须真的拿到全文
        if card_type is EvidenceCardType.PAPER_FULL_TEXT and not self.source_ref.is_full_text:
            raise EvidenceFabricationRejected(
                "PAPER_FULL_TEXT 的 source_ref.record_kind 必须是 FULL_TEXT："
                "不得把摘要/元数据写成论文原文证据"
            )
        # 5) 教材：标签 + 无 DOI/页码
        if card_type is EvidenceCardType.TEXTBOOK:
            if self.locator.dimension != "TEXTBOOK":
                raise EvidenceContractError("TEXTBOOK 必须使用 direct-chunk locator（source→section→chunk）")
            if not self.source_ref.record_kind == "TEXTBOOK_CHUNK":
                raise EvidenceContractError("TEXTBOOK 的 source_ref.record_kind 必须是 TEXTBOOK_CHUNK")
            object.__setattr__(
                self, "labels", tuple(dict.fromkeys((*TEXTBOOK_LABELS, *self.labels)))
            )
        # 6) 派生证据必须有上游
        if profile_for(card_type).requires_upstream and not self.source_ref.upstream_card_ids:
            raise EvidenceFabricationRejected("DERIVED 证据必须列出 upstream_card_ids")
        # 7) 权利上限：受限/未知 → 不可能存原文
        policy = rights_policy_for(self.rights)
        if policy.quote_max_chars == 0 and self.snippet:
            raise EvidenceRightsViolation(
                f"rights_status={self.rights.rights_status.value} 不允许存储任何原文"
                f"（quote_max_chars=0），实际 snippet 长度 {len(self.snippet)}"
            )
        if self.snippet and len(self.snippet) > policy.quote_max_chars:
            raise EvidenceRightsViolation(
                f"snippet 长度 {len(self.snippet)} 超过 rights 允许的 "
                f"{policy.quote_max_chars} 字符"
            )

        object.__setattr__(self, "evidence_type", card_type)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "stale_state", stale)
        object.__setattr__(self, "version", int(self.version))
        object.__setattr__(self, "limitations", tuple(str(x) for x in self.limitations))
        object.__setattr__(self, "warnings", tuple(str(x) for x in self.warnings))
        object.__setattr__(self, "labels", tuple(str(x) for x in self.labels))

    # -- 派生 --------------------------------------------------------------

    @property
    def type(self) -> EvidenceCardType:
        """`type` 是 `evidence_type` 的对外别名（任务书字段名即 `type`）。"""
        return self.evidence_type

    @property
    def profile(self) -> SupportProfile:
        return profile_for(self.evidence_type)

    @property
    def citation_eligible(self) -> bool:
        """能否作为**文献引用**出现。教材与 AI 派生一律 False。"""
        return self.profile.citation_eligible

    @property
    def is_stale(self) -> bool:
        return self.stale_state is StaleState.STALE

    @property
    def usable(self) -> bool:
        """是否可参与支撑判定：ACTIVE 且未 stale。"""
        return self.status is EvidenceStatus.ACTIVE and not self.is_stale

    def supports(self, claim_kind: ClaimKind) -> bool:
        return self.usable and supports_claim_kind(self.evidence_type, claim_kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.card_id,
            "type": self.evidence_type.value,
            "source_ref": self.source_ref.as_dict(),
            "locator": self.locator.as_dict(),
            "snippet": self.snippet,
            "structured_fact": self.structured_fact.as_dict() if self.structured_fact else None,
            "snippet_hash": self.snippet_hash,
            "provenance": self.provenance.as_dict(),
            "rights": self.rights.as_dict(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "created_by_run": self.created_by_run,
            "confidence": self.confidence.as_dict(),
            "limitations": list(self.limitations),
            "status": self.status.value,
            "version": self.version,
            "acl": self.acl.as_dict(),
            "citation_eligible": self.citation_eligible,
            "labels": list(self.labels),
            "warnings": list(self.warnings),
            "stale_state": self.stale_state.value,
            "stale_reason": self.stale_reason,
        }


# --------------------------------------------------------------------------
# AI 只能产出 EvidenceProposal
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceProposal:
    """模型**唯一**能产出的形态。

    刻意没有 `card_id` / `status` / `version` / `snippet_hash` / `stale_state`：
    这些由服务端在 schema / locator / rights / ACL 验证通过后生成。
    """

    evidence_type: EvidenceCardType
    source_ref: SourceRef
    locator: LocatorBinding
    snippet: str
    structured_fact: StructuredFact | None
    provenance: Provenance
    rights: RightsInfo
    retrieved_at: datetime
    created_by_run: str
    confidence: Confidence
    limitations: tuple[str, ...]
    acl: EvidenceACL
    labels: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # 入口可能给字符串（HTTP body / JSON）；在这里收敛成枚举，
        # 否则 `canonical_json()` 与支持矩阵会在下游炸出 AttributeError。
        try:
            object.__setattr__(self, "evidence_type", EvidenceCardType(self.evidence_type))
        except ValueError as exc:
            raise EvidenceContractError(f"未知 evidence_type: {self.evidence_type!r}") from exc
        object.__setattr__(self, "limitations", tuple(str(x) for x in self.limitations))
        object.__setattr__(self, "labels", tuple(str(x) for x in self.labels))
        object.__setattr__(self, "warnings", tuple(str(x) for x in self.warnings))

    def canonical_json(self) -> str:
        """确定性序列化（幂等键的输入）。"""
        import json

        return json.dumps(
            {
                "evidence_type": self.evidence_type.value,
                "source_ref": self.source_ref.as_dict(),
                "locator": self.locator.as_dict(),
                "snippet": self.snippet,
                "structured_fact": self.structured_fact.as_dict() if self.structured_fact else None,
                "provenance": self.provenance.as_dict(),
                "rights": self.rights.as_dict(),
                "retrieved_at": self.retrieved_at.isoformat(),
                "created_by_run": self.created_by_run,
                "confidence": self.confidence.as_dict(),
                "limitations": list(self.limitations),
                "acl": self.acl.as_dict(),
                "labels": list(self.labels),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def proposal_hash(self) -> str:
        return _sha(self.canonical_json())


def rights_policy_for(rights: RightsInfo) -> RightsPolicy:
    """由权利事实**推导**可执行边界（无第二处推导）。"""
    status = RightsStatus(rights.rights_status)
    cap = QUOTE_MAX_CHARS[status]
    return RightsPolicy(
        quote_max_chars=cap,
        reader_fulltext_visible=bool(rights.fulltext_allowed)
        and status in READER_FULLTEXT_STATUSES,
        # 导出逐字引用需要"可再分发"**且**权利档位允许引用
        export_included=bool(rights.redistributable) and cap > 0,
        prompt_verbatim_allowed=status in PROMPT_VERBATIM_STATUSES and cap > 0,
        log_verbatim_allowed=False,
    )


def materialize_card(
    proposal: EvidenceProposal,
    *,
    card_id: str,
    status: EvidenceStatus = EvidenceStatus.ACTIVE,
    version: int = 1,
) -> EvidenceCard:
    """把 proposal 落成事实卡。

    **只在**完成 snippet 权利裁剪之后构造 card —— 因此受限原文不会进对象。
    """
    policy = rights_policy_for(proposal.rights)
    warnings = list(proposal.warnings)
    snippet = proposal.snippet
    # 顺序很重要：cap=0 是"整体抹除"（不是"截断到 0"），必须先判，
    # 否则会得到一个误导性的 QUOTE_TRUNCATED 而不是 SNIPPET_REDACTED_BY_RIGHTS。
    if policy.quote_max_chars == 0:
        if snippet:
            snippet = ""
            warnings.append("SNIPPET_REDACTED_BY_RIGHTS")
    elif len(snippet) > policy.quote_max_chars:
        snippet = snippet[: policy.quote_max_chars]
        warnings.append("QUOTE_TRUNCATED")
    if not snippet and proposal.structured_fact is None:
        raise EvidenceContractError(
            "权利裁剪后证据既无 snippet 也无 structured_fact：无法入库（不得产出空证据）"
        )
    if proposal.rights.rights_status is RightsStatus.UNKNOWN:
        warnings.append("RIGHTS_MISSING")
    return EvidenceCard(
        card_id=card_id,
        evidence_type=proposal.evidence_type,
        source_ref=proposal.source_ref,
        locator=proposal.locator,
        snippet=snippet,
        structured_fact=proposal.structured_fact,
        snippet_hash=_sha(snippet),
        provenance=proposal.provenance,
        rights=proposal.rights,
        retrieved_at=proposal.retrieved_at,
        created_by_run=proposal.created_by_run,
        confidence=proposal.confidence,
        limitations=proposal.limitations,
        status=status,
        version=version,
        acl=proposal.acl,
        labels=proposal.labels,
        warnings=tuple(dict.fromkeys(warnings)),
    )


__all__ = [
    "REQUIRED_CARD_FIELDS",
    "RECORD_KINDS",
    "ALLOWED_ANCHOR_TYPES",
    "ALLOWED_LOCATOR_DIMENSIONS",
    "VISIBILITIES",
    "QUOTE_MAX_CHARS",
    "SourceRef",
    "LocatorBinding",
    "RightsInfo",
    "RightsPolicy",
    "Provenance",
    "Confidence",
    "StructuredFact",
    "EvidenceACL",
    "EvidenceCard",
    "EvidenceProposal",
    "rights_policy_for",
    "materialize_card",
]
