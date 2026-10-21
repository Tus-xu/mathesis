"""RV07 · EvidenceCard 类型系统。

**唯一的语义轴是 `EvidenceCardType`。** 七类证据在支持强度上**互不等价**，
这不是注释里的约定，而是 `SUPPORT_PROFILES` 这张表在类型层的落地：
任何"这条证据够不够撑住这个结论"的判断都必须读它，禁止用 provider 名或
字段组合去猜。

与 RV06 `federation.types.EvidenceType`（provider 维度，5 类）的关系：
RV06 的五类是**检索维度**（谁提供了这条命中）；本模块的七类是**证据语义**
（这条命中作为证据能支撑什么）。两者是多对一映射（见 `from_federation_type`），
映射之后**不丢失**语义：`LITERATURE` 需要按 source 的 record_kind 再细分
`PAPER_FULL_TEXT` / `PAPER_ABSTRACT`，绝不把"只有摘要"当成"论文全文"。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceCardType(str, Enum):
    """七类证据（任务书 §必须完成 2）。

    刻意**不**沿用 RV06 的 `LITERATURE`：把"全文"和"摘要"合成一类，
    正是"摘要冒充论文原文证据"的温床。
    """

    PAPER_FULL_TEXT = "PAPER_FULL_TEXT"
    PAPER_ABSTRACT = "PAPER_ABSTRACT"
    PROJECT_ASSET = "PROJECT_ASSET"
    TEXTBOOK = "TEXTBOOK"
    DATASET = "DATASET"
    WEB = "WEB"
    DERIVED = "DERIVED"


class SupportStrength(str, Enum):
    """可支持强度的偏序（不是等距标尺，只表达"谁比谁强"）。"""

    #: 可支撑逐字引用（只有论文全文可以）
    PRIMARY_VERBATIM = "PRIMARY_VERBATIM"
    #: 只能支撑"作者如此概述/主张"级别的转述
    PRIMARY_SUMMARY = "PRIMARY_SUMMARY"
    #: 项目内自有资产（实验/数据/上传件），支撑项目局部事实
    PROJECT_LOCAL = "PROJECT_LOCAL"
    #: 数据事实（数据集行/统计口径）
    DATA_DERIVED = "DATA_DERIVED"
    #: 外部网页：可核验但声誉/稳定性弱于同行评议
    EXTERNAL_WEB = "EXTERNAL_WEB"
    #: 只能作背景（教材：不参与"文献已支持"）
    BACKGROUND_ONLY = "BACKGROUND_ONLY"
    #: 无独立支持力（AI 派生：必须指回上游证据）
    NON_INDEPENDENT = "NON_INDEPENDENT"


class ClaimKind(str, Enum):
    """被支撑主张的种类 —— 支持判定必须按它分流。"""

    VERBATIM_QUOTE = "VERBATIM_QUOTE"
    PARAPHRASE = "PARAPHRASE"
    DATA_FACT = "DATA_FACT"
    BACKGROUND = "BACKGROUND"


@dataclass(frozen=True)
class SupportProfile:
    """一类的支持档案。`signature()` 用于机器断言"两类不静默等价"。"""

    strength: SupportStrength
    supports_verbatim_quote: bool
    citation_eligible: bool
    counts_as_literature_support: bool
    requires_upstream: bool
    claim_kinds: frozenset[ClaimKind]

    def signature(self) -> tuple:
        return (
            self.strength.value,
            self.supports_verbatim_quote,
            self.citation_eligible,
            self.counts_as_literature_support,
            self.requires_upstream,
            tuple(sorted(k.value for k in self.claim_kinds)),
        )


#: 七类支持档案。**这张表就是 EVIDENCE-TYPE-MATRIX.md 的机器形态。**
SUPPORT_PROFILES: dict[EvidenceCardType, SupportProfile] = {
    EvidenceCardType.PAPER_FULL_TEXT: SupportProfile(
        strength=SupportStrength.PRIMARY_VERBATIM,
        supports_verbatim_quote=True,
        citation_eligible=True,
        counts_as_literature_support=True,
        requires_upstream=False,
        claim_kinds=frozenset(
            {ClaimKind.VERBATIM_QUOTE, ClaimKind.PARAPHRASE, ClaimKind.DATA_FACT}
        ),
    ),
    EvidenceCardType.PAPER_ABSTRACT: SupportProfile(
        # 摘要只能支撑"作者如此概述"；**不能**支撑逐字引用。
        strength=SupportStrength.PRIMARY_SUMMARY,
        supports_verbatim_quote=False,
        citation_eligible=True,
        counts_as_literature_support=True,
        requires_upstream=False,
        claim_kinds=frozenset({ClaimKind.PARAPHRASE}),
    ),
    EvidenceCardType.PROJECT_ASSET: SupportProfile(
        strength=SupportStrength.PROJECT_LOCAL,
        supports_verbatim_quote=False,
        citation_eligible=False,
        counts_as_literature_support=False,
        requires_upstream=False,
        claim_kinds=frozenset({ClaimKind.DATA_FACT, ClaimKind.PARAPHRASE}),
    ),
    EvidenceCardType.TEXTBOOK: SupportProfile(
        # 教材是背景材料：**不参与**"文献已支持"的声明。
        strength=SupportStrength.BACKGROUND_ONLY,
        supports_verbatim_quote=False,
        citation_eligible=False,
        counts_as_literature_support=False,
        requires_upstream=False,
        claim_kinds=frozenset({ClaimKind.BACKGROUND}),
    ),
    EvidenceCardType.DATASET: SupportProfile(
        strength=SupportStrength.DATA_DERIVED,
        supports_verbatim_quote=False,
        citation_eligible=True,
        counts_as_literature_support=False,
        requires_upstream=False,
        claim_kinds=frozenset({ClaimKind.DATA_FACT}),
    ),
    EvidenceCardType.WEB: SupportProfile(
        strength=SupportStrength.EXTERNAL_WEB,
        supports_verbatim_quote=False,
        citation_eligible=True,
        counts_as_literature_support=False,
        requires_upstream=False,
        claim_kinds=frozenset({ClaimKind.PARAPHRASE, ClaimKind.DATA_FACT}),
    ),
    EvidenceCardType.DERIVED: SupportProfile(
        # AI 派生：没有上游证据就什么也撑不住。
        strength=SupportStrength.NON_INDEPENDENT,
        supports_verbatim_quote=False,
        citation_eligible=False,
        counts_as_literature_support=False,
        requires_upstream=True,
        claim_kinds=frozenset(),
    ),
}


def profile_for(card_type: EvidenceCardType) -> SupportProfile:
    try:
        return SUPPORT_PROFILES[EvidenceCardType(card_type)]
    except KeyError as exc:  # pragma: no cover - 枚举封闭，理论不可达
        raise ValueError(f"未知证据类型: {card_type!r}") from exc


def supports_claim_kind(card_type: EvidenceCardType, claim_kind: ClaimKind) -> bool:
    """该类型能否支撑该类主张（支持矩阵的唯一入口）。"""
    return ClaimKind(claim_kind) in profile_for(card_type).claim_kinds


# --------------------------------------------------------------------------
# 状态 / 权利 / 定位 / 判定
# --------------------------------------------------------------------------


class EvidenceStatus(str, Enum):
    """证据卡生命周期。只有 `ACTIVE` 参与支撑判定。"""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"
    REJECTED = "REJECTED"


class StaleState(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"


class RightsStatus(str, Enum):
    """权利档位。`UNKNOWN` 与 `RESTRICTED` 的处置**不同**，不得合并。

    - `UNKNOWN`：连"能不能展示"都不知道 → 一律不可见、不可导出、不进 prompt；
    - `RESTRICTED`：允许在授权阅读器里看，但**不得**进 prompt / 导出 / 日志。
    """

    UNKNOWN = "UNKNOWN"
    RESTRICTED = "RESTRICTED"
    LICENSED = "LICENSED"
    PERMITTED = "PERMITTED"
    OPEN_ACCESS = "OPEN_ACCESS"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    INTERNAL = "INTERNAL"


class LocatorKind(str, Enum):
    #: 指向 RV05 `asset_anchor`（有 page/bbox/section/table/figure/formula）
    ANCHOR = "ANCHOR"
    #: 可验证的外部定位（DOI / URL+hash / 教材 source→section→chunk）
    EXTERNAL = "EXTERNAL"


class ConfidenceBand(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class NoEvidenceVerdict(str, Enum):
    """无证据判定的封闭三值。**没有第四个"大概可以"**。"""

    SUPPORTED = "SUPPORTED"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class Warn(str, Enum):
    """证据层机读告警码（进 `card.warnings` / 响应 `warnings`）。"""

    QUOTE_TRUNCATED = "QUOTE_TRUNCATED"
    SNIPPET_REDACTED_BY_RIGHTS = "SNIPPET_REDACTED_BY_RIGHTS"
    RIGHTS_MISSING = "RIGHTS_MISSING"
    EXPORT_BLOCKED_BY_RIGHTS = "EXPORT_BLOCKED_BY_RIGHTS"
    PROMPT_VERBATIM_BLOCKED_BY_RIGHTS = "PROMPT_VERBATIM_BLOCKED_BY_RIGHTS"
    LOCATOR_STALE = "LOCATOR_STALE"
    SOURCE_HASH_CHANGED = "SOURCE_HASH_CHANGED"
    FULLTEXT_SOURCE_MISSING = "FULLTEXT_SOURCE_MISSING"
    ABSTRACT_CANNOT_SUPPORT_VERBATIM = "ABSTRACT_CANNOT_SUPPORT_VERBATIM"
    TEXTBOOK_NOT_LITERATURE_SUPPORT = "TEXTBOOK_NOT_LITERATURE_SUPPORT"
    DERIVED_REQUIRES_UPSTREAM = "DERIVED_REQUIRES_UPSTREAM"
    NO_EVIDENCE = "NO_EVIDENCE"
    ALL_STALE = "ALL_STALE"
    TEXTBOOK_PAGE_DROPPED = "TEXTBOOK_PAGE_DROPPED"


# --------------------------------------------------------------------------
# 与 RV06 维度的映射
# --------------------------------------------------------------------------

#: 教材标签：与 RV06 `FOUNDATIONAL_MATH_LABELS` 一致（防伪装成文献）。
TEXTBOOK_LABELS: tuple[str, ...] = ("TEXTBOOK", "FOUNDATIONAL_MATH")


def from_federation_type(
    federation_type: str, *, record_kind: str = ""
) -> EvidenceCardType:
    """RV06 检索维度 → RV07 证据语义。

    `LITERATURE` 必须按 `record_kind` 细分：只有拿到全文（`FULL_TEXT`）才允许
    是 `PAPER_FULL_TEXT`；`METADATA_ONLY` / `ABSTRACT` 一律降为 `PAPER_ABSTRACT`
    —— 这是"摘要冒充论文原文证据"在映射层的封堵点。
    """
    kind = (record_kind or "").upper()
    if federation_type == "LITERATURE":
        if kind == "FULL_TEXT":
            return EvidenceCardType.PAPER_FULL_TEXT
        return EvidenceCardType.PAPER_ABSTRACT
    mapping = {
        "PROJECT_ASSET": EvidenceCardType.PROJECT_ASSET,
        "FOUNDATIONAL_MATH": EvidenceCardType.TEXTBOOK,
        "DATASET": EvidenceCardType.DATASET,
        "WEB_EXTERNAL": EvidenceCardType.WEB,
    }
    if federation_type not in mapping:
        raise ValueError(f"未知 federation evidence_type: {federation_type!r}")
    return mapping[federation_type]


__all__ = [
    "EvidenceCardType",
    "SupportStrength",
    "ClaimKind",
    "SupportProfile",
    "SUPPORT_PROFILES",
    "profile_for",
    "supports_claim_kind",
    "EvidenceStatus",
    "StaleState",
    "RightsStatus",
    "LocatorKind",
    "ConfidenceBand",
    "NoEvidenceVerdict",
    "Warn",
    "TEXTBOOK_LABELS",
    "from_federation_type",
]
