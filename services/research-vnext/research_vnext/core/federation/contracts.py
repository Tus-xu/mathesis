"""RV06 · Federation 契约：Query / ProviderHit / FederatedResult / Locator。

三条铁律在**类型层**落地，不靠代码评审兜底：

1. `evidence_type` 决定 locator 类型；不匹配即 `DimensionMismatch`。
   这是「不把教材伪装成学术文献」的机器化形态 —— 教材永远拿不到 DOI 字段。
2. locator 的字段白名单按维度分裂；越界字段即 `DimensionLeak`。
   Teacher 表里真实存在的 `page_range` 必须被显式丢弃，而不是顺手投影。
3. 检索文本**永远** untrusted：`to_untrusted_block()` 返回只读映射，
   调用方无法把它提升成 system/tool 指令。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .errors import (
    DimensionLeak,
    DimensionMismatch,
    MissingLocator,
    QueryInvalid,
)
from .types import (
    FOUNDATIONAL_MATH_LABELS,
    EvidenceType,
    TrustLevel,
    WarnCode,
)

# --------------------------------------------------------------------------
# Locator：按维度分裂的字段白名单
# --------------------------------------------------------------------------

#: `purpose` / `prompt_version` 是受控标识（会落 varchar(64)），不是自由文本。
_PURPOSE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")

#: 教材 locator 永不允许出现的字段（教材源铁律的机器化）。
TEXTBOOK_FORBIDDEN_KEYS = frozenset(
    {
        "doi",
        "venue",
        "journal",
        "publisher_journal",
        "issn",
        "isbn",
        "page",
        "pages",
        "page_no",
        "page_number",
        "page_range",
        "page_ref",
        "page_refs",
        "pnn",
        "bbox",
        "abstract",
    }
)

#: 资产 locator 永不允许出现的字段（防内部存储地址外泄）。
ASSET_FORBIDDEN_KEYS = frozenset(
    {"storage_key", "storage_backend", "endpoint", "uri", "url", "bucket", "minio_endpoint"}
)

#: 文献 locator 不允许出现教材/资产维度字段（防反向混淆）。
LITERATURE_FORBIDDEN_KEYS = frozenset({"storage_key", "chunk_id", "section_path", "pnn"})


def _reject_keys(mapping: Mapping[str, Any], forbidden: frozenset[str], dimension: str) -> None:
    hit = {k for k in mapping if k in forbidden}
    if hit:
        raise DimensionLeak(
            f"{dimension} locator 出现不属于该维度的字段 {sorted(hit)}"
        )


@dataclass(frozen=True)
class LiteratureLocator:
    """学术文献定位：DOI / 期刊 / 页码都是**合法**的。

    没有 DOI 时 `doi` 必须留空 —— 契约不提供任何"补一个 DOI"的入口。
    """

    doi: str = ""
    venue: str = ""
    section: str = ""
    page: int | None = None
    asset_anchor_id: str = ""

    def __post_init__(self) -> None:
        if self.doi:
            self_doi = self.doi.strip().lower()
            for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
                if self_doi.startswith(prefix):
                    self_doi = self_doi[len(prefix) :]
            object.__setattr__(self, "doi", self_doi)

    @property
    def fabricated(self) -> bool:
        """恒为 False：locator 只承载真实取回的标识，不生成代理标识。"""
        return False

    @property
    def discriminator(self) -> str:
        if self.doi:
            return f"doi:{self.doi}"
        if self.asset_anchor_id:
            return f"anchor:{self.asset_anchor_id}"
        # 无 DOI 且无 anchor 时不能返回一个固定串：那会让所有"无标识文献"共用同一
        # 去重键，把不同记录折叠成一条。改为对 locator 自身内容取指纹 —— 仍确定性，
        # 但不同记录得到不同键。
        blob = json.dumps(
            {"venue": self.venue, "section": self.section, "page": self.page},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return f"unidentified:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]}"

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"doi": self.doi, "venue": self.venue, "section": self.section}
        if self.page is not None:
            d["page"] = self.page
        if self.asset_anchor_id:
            d["asset_anchor_id"] = self.asset_anchor_id
        return d

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> LiteratureLocator:
        _reject_keys(mapping, LITERATURE_FORBIDDEN_KEYS, "LITERATURE")
        known = {"doi", "venue", "section", "page", "asset_anchor_id"}
        return cls(**{k: v for k, v in mapping.items() if k in known})


@dataclass(frozen=True)
class TextbookLocator:
    """教材 direct-chunk 定位：**只允许** source → section → chunk。

    类型层不提供 `page` / `bbox` / `doi` 字段；`from_mapping` 还会主动拒绝
    这些键，因此任何"顺手把 Teacher 的 page_range 传下来"的写法都会当场失败。
    """

    source_id: str
    section_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("source_id", self.source_id),
                ("section_id", self.section_id),
                ("chunk_id", self.chunk_id),
            )
            if not value
        ]
        if missing:
            raise MissingLocator(f"教材 locator 缺少 {missing}（必须 source→section→chunk）")

    @property
    def discriminator(self) -> str:
        return f"textbook:{self.source_id}:{self.section_id}:{self.chunk_id}"

    def as_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "section_id": self.section_id,
            "chunk_id": self.chunk_id,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> TextbookLocator:
        _reject_keys(mapping, TEXTBOOK_FORBIDDEN_KEYS, "FOUNDATIONAL_MATH")
        return cls(
            source_id=str(mapping.get("source_id") or ""),
            section_id=str(mapping.get("section_id") or ""),
            chunk_id=str(mapping.get("chunk_id") or ""),
        )


@dataclass(frozen=True)
class AssetLocator:
    """本项目资产定位：版本绑定 + 内容 hash，**不含**任何存储地址。"""

    asset_id: str
    asset_version: int
    content_hash: str
    anchor_id: str = ""

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise MissingLocator("资产 locator 缺少 asset_id")
        if self.asset_version < 1:
            raise MissingLocator("资产 locator 的 asset_version 必须 >= 1")

    @property
    def discriminator(self) -> str:
        return f"asset:{self.asset_id}:{self.asset_version}:{self.content_hash or '-'}"

    def as_dict(self) -> dict[str, Any]:
        d = {
            "asset_id": self.asset_id,
            "asset_version": self.asset_version,
            "content_hash": self.content_hash,
        }
        if self.anchor_id:
            d["anchor_id"] = self.anchor_id
        return d

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> AssetLocator:
        _reject_keys(mapping, ASSET_FORBIDDEN_KEYS, "PROJECT_ASSET")
        return cls(
            asset_id=str(mapping.get("asset_id") or ""),
            asset_version=int(mapping.get("asset_version") or 0),
            content_hash=str(mapping.get("content_hash") or ""),
            anchor_id=str(mapping.get("anchor_id") or ""),
        )


@dataclass(frozen=True)
class DatasetLocator:
    """数据集定位（contract 保留，本轮 provider 默认 disabled）。"""

    dataset_id: str
    table: str = ""
    row_id: str = ""

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise MissingLocator("数据集 locator 缺少 dataset_id")

    @property
    def discriminator(self) -> str:
        return f"dataset:{self.dataset_id}:{self.table}:{self.row_id}"

    def as_dict(self) -> dict[str, Any]:
        return {"dataset_id": self.dataset_id, "table": self.table, "row_id": self.row_id}


@dataclass(frozen=True)
class WebLocator:
    """外部网页定位（contract 保留，本轮 provider 默认 disabled）。"""

    url: str
    fetched_at: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            raise MissingLocator("网页 locator 缺少 url")

    @property
    def discriminator(self) -> str:
        return f"web:{self.url}"

    def as_dict(self) -> dict[str, Any]:
        return {"url": self.url, "fetched_at": self.fetched_at, "content_hash": self.content_hash}


Locator = (
    LiteratureLocator | TextbookLocator | AssetLocator | DatasetLocator | WebLocator
)

#: 维度 → locator 类型。**唯一**的合法配对表。
LOCATOR_TYPES: dict[EvidenceType, type] = {
    EvidenceType.LITERATURE: LiteratureLocator,
    EvidenceType.PROJECT_ASSET: AssetLocator,
    EvidenceType.FOUNDATIONAL_MATH: TextbookLocator,
    EvidenceType.DATASET: DatasetLocator,
    EvidenceType.WEB_EXTERNAL: WebLocator,
}


# --------------------------------------------------------------------------
# 值对象
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessRights:
    """权利与再分发约束。

    `fulltext_allowed` 默认 **False**：没有明确 rights 的资产不得外传全文，
    这是 RV04 `rights_status=UNKNOWN` 语义在检索层的延续。
    """

    rights_status: str = "UNKNOWN"
    rights_evidence_ref: str = ""
    redistributable: bool = False
    license_id: str = ""
    fulltext_allowed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "rights_status": self.rights_status,
            "rights_evidence_ref": self.rights_evidence_ref,
            "redistributable": self.redistributable,
            "license_id": self.license_id,
            "fulltext_allowed": self.fulltext_allowed,
        }


@dataclass(frozen=True)
class Provenance:
    retrieved_at: str
    parser_version: str
    source_hash: str
    provider_version: str = ""
    index_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieved_at": self.retrieved_at,
            "parser_version": self.parser_version,
            "source_hash": self.source_hash,
            "provider_version": self.provider_version,
            "index_version": self.index_version,
        }


@dataclass(frozen=True)
class ScoreComponents:
    """分项打分（不是只有总分，便于审计与 rerank 复算）。"""

    lexical: float = 0.0
    vector: float = 0.0
    rights_penalty: float = 0.0
    recency: float = 0.0

    @property
    def total(self) -> float:
        return max(0.0, self.lexical + self.vector + self.recency - self.rights_penalty)

    def as_dict(self) -> dict[str, float]:
        return {
            "lexical": self.lexical,
            "vector": self.vector,
            "rights_penalty": self.rights_penalty,
            "recency": self.recency,
            "total": self.total,
        }


@dataclass(frozen=True)
class Budget:
    max_results: int
    max_provider_calls: int
    cost_ceiling: float
    token_ceiling: int = 0

    def __post_init__(self) -> None:
        if self.max_results < 1:
            raise QueryInvalid("budget.max_results 必须 >= 1")
        if self.max_provider_calls < 1:
            raise QueryInvalid("budget.max_provider_calls 必须 >= 1")
        if self.cost_ceiling < 0:
            raise QueryInvalid("budget.cost_ceiling 不得为负")


@dataclass(frozen=True)
class HitScope:
    """命中的归属维度（用于 orchestrator 侧的越界复核）。"""

    tenant_id: str
    project_id: str | None = None
    school_id: str | None = None
    visibility: str = "project"


@dataclass(frozen=True)
class FederatedQuery:
    """统一检索请求。

    必填：actor / tenant / project / session / purpose / provider allowlist /
    filters / budget / deadline / trace（任务书 §必须完成 2）。
    provider **不能**扩展其中任何一项：scope 由 orchestrator 从 AccessContext
    派生后冻结，见 `scope.resolve_scope`。
    """

    actor_id: str
    tenant_id: str
    project_id: str | None
    session_id: str | None
    purpose: str
    provider_allowlist: Sequence[str]
    filters: Mapping[str, Any]
    budget: Budget
    deadline_ms: int
    trace_id: str
    query_text: str = ""
    prompt_version: str = ""
    strategy: str = "federated_hybrid_v1"

    def __post_init__(self) -> None:
        for name in ("actor_id", "tenant_id", "purpose", "trace_id"):
            if not getattr(self, name):
                raise QueryInvalid(f"FederatedQuery 缺少必填项 {name}")
        if self.deadline_ms <= 0:
            raise QueryInvalid("FederatedQuery.deadline_ms 必须为正")
        # purpose / prompt_version 会落进 varchar(64) 列。不设上界时超长值会在
        # **检索跑完之后**才以 DataError 炸在写入处（用户已经付出检索成本却拿到 500）。
        # 长度与字符集都在契约层挡住，并且不进日志原文 —— 它们是受控标识，不是自由文本。
        if len(self.purpose) > 64:
            raise QueryInvalid("FederatedQuery.purpose 不得超过 64 字符")
        if len(self.prompt_version) > 64:
            raise QueryInvalid("FederatedQuery.prompt_version 不得超过 64 字符")
        if not _PURPOSE_PATTERN.match(self.purpose):
            raise QueryInvalid(
                "FederatedQuery.purpose 只允许 [A-Za-z0-9._:-]（受控标识，非自由文本）"
            )
        if self.prompt_version and not _PURPOSE_PATTERN.match(self.prompt_version):
            raise QueryInvalid("FederatedQuery.prompt_version 只允许 [A-Za-z0-9._:-]")
        allow = tuple(dict.fromkeys(str(x) for x in self.provider_allowlist if str(x)))
        if not allow:
            raise QueryInvalid("FederatedQuery 必须给出非空 provider allowlist")
        object.__setattr__(self, "provider_allowlist", allow)

    @property
    def query_hash(self) -> str:
        """稳定查询指纹：相同租户/project/意图/查询/allowlist → 相同 hash。"""
        blob = json.dumps(
            {
                "tenant": self.tenant_id,
                "project": self.project_id or "",
                "purpose": self.purpose,
                "query_text": self.query_text,
                "allowlist": sorted(self.provider_allowlist),
                "strategy": self.strategy,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderHit:
    """provider 返回的**原始命中**（尚未过 scope / locator / 注入防线）。

    `locator` 可以是 `None` —— provider 必须如实汇报自己没能定位，
    由 orchestrator 记 `MISSING_LOCATOR` 并丢弃，而不是让 provider 自己拼一个。
    """

    evidence_type: EvidenceType
    provider: str
    provider_version: str
    locator: Locator | None
    snippet: str
    rights: AccessRights
    provenance: Provenance
    retrieved_at: str
    score_components: ScoreComponents
    scope_meta: HitScope
    warnings: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    citation_eligible: bool = True
    record_kind: str = "EVIDENCE"


@dataclass(frozen=True)
class FederatedResult:
    """通过全部防线后的结果（任务书 §必须完成 3 的字段集合）。"""

    result_id: str
    evidence_type: EvidenceType
    provider: str
    provider_version: str
    locator: Locator
    snippet: str
    snippet_hash: str
    rights: AccessRights
    provenance: Provenance
    retrieved_at: str
    score_components: ScoreComponents
    warnings: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    sanitized: bool = False
    citation_eligible: bool = True
    trust_level: TrustLevel = TrustLevel.UNTRUSTED_DATA
    record_kind: str = "EVIDENCE"

    def __post_init__(self) -> None:
        if self.locator is None:
            raise MissingLocator(
                f"结果 {self.result_id} 缺少 locator（禁止产出不可回源的假结果）"
            )
        expected = LOCATOR_TYPES.get(self.evidence_type)
        # 用精确类型比较而非 isinstance：子类可以带上白名单外的字段
        # （例如继承 TextbookLocator 再加一个 doi），isinstance 会放行它，
        # 而 as_dict() 又恰好不投影那些字段 —— 于是"肉眼干净、类型脏"。
        if expected is None or type(self.locator) is not expected:
            raise DimensionMismatch(
                f"evidence_type={self.evidence_type.value} 必须配 "
                f"{expected.__name__ if expected else '已知 locator'}，"
                f"实际收到 {type(self.locator).__name__}"
            )
        if self.snippet_hash != hashlib.sha256(self.snippet.encode("utf-8")).hexdigest():
            raise DimensionMismatch(f"结果 {self.result_id} 的 snippet_hash 与 snippet 不一致")

        if self.evidence_type is EvidenceType.FOUNDATIONAL_MATH:
            if not self.citation_eligible:
                pass
            else:
                raise DimensionMismatch(
                    "教材（FOUNDATIONAL_MATH）结果不得声明 citation_eligible=True："
                    "教材不得被用于声称「文献已支持」"
                )
            merged = tuple(dict.fromkeys((*FOUNDATIONAL_MATH_LABELS, *self.labels)))
            object.__setattr__(self, "labels", merged)

        # trust label 是常量：构造期强制，调用方无法赋值成别的级别
        object.__setattr__(self, "trust_level", TrustLevel.UNTRUSTED_DATA)
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "labels", tuple(self.labels))

    @property
    def untrusted(self) -> bool:
        return self.trust_level is TrustLevel.UNTRUSTED_DATA

    def has_warning(self, code: WarnCode | str) -> bool:
        value = code.value if isinstance(code, WarnCode) else str(code)
        return value in self.warnings

    def to_untrusted_block(self) -> Mapping[str, Any]:
        """导出为**只读**数据块；调用方无法把它提升为指令。

        返回 `MappingProxyType`：任何 `block["trust_level"] = ...` 都会 `TypeError`。
        """
        return MappingProxyType(
            {
                "role": "data",
                "trust_level": TrustLevel.UNTRUSTED_DATA.value,
                "evidence_type": self.evidence_type.value,
                "provider": self.provider,
                "provider_version": self.provider_version,
                "locator": self.locator.as_dict(),
                "snippet": self.snippet,
                "snippet_hash": self.snippet_hash,
                "rights": self.rights.as_dict(),
                "provenance": self.provenance.as_dict(),
                "retrieved_at": self.retrieved_at,
                "score": self.score_components.as_dict(),
                "labels": list(self.labels),
                "citation_eligible": self.citation_eligible,
                "warnings": list(self.warnings),
                "sanitized": self.sanitized,
            }
        )


def snippet_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "Locator",
    "LOCATOR_TYPES",
    "LiteratureLocator",
    "TextbookLocator",
    "AssetLocator",
    "DatasetLocator",
    "WebLocator",
    "AccessRights",
    "Provenance",
    "ScoreComponents",
    "Budget",
    "HitScope",
    "FederatedQuery",
    "ProviderHit",
    "FederatedResult",
    "snippet_hash",
    "TEXTBOOK_FORBIDDEN_KEYS",
    "ASSET_FORBIDDEN_KEYS",
    "LITERATURE_FORBIDDEN_KEYS",
]
