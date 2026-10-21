"""RV06 · ProjectAssetProvider 适配器（只读当前 project ACL）。

隔离口径（三条同时成立才可见）：

1. `tenant_id` 相等；
2. `project_id` 落在已冻结 scope 的 project 集合内；
3. ACL 通过 —— `actor` 是 owner、在 `readers` 里，或资产被标记 `public`。

再加上 RV04 既有的可用性门槛：未撤权、`scan_status == CLEAN`。

`snippet` **只**由文件名/媒体类型/大小组成，绝不取字节；
locator 也不含 `storage_key` —— 字节交付仍然只能走 Gateway 签名路径。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...assets.models import SCAN_CLEAN, ResearchAssetRow
from ..contracts import (
    AccessRights,
    AssetLocator,
    FederatedQuery,
    HitScope,
    Provenance,
    ProviderHit,
    ScoreComponents,
)
from ..scope import ResolvedScope
from ..scoring import lexical_score, recency_component
from ..types import EvidenceType, WarnCode
from .base import FederatedProvider, ProviderError

PARSER_VERSION = "rv04-asset-metadata@1.0.0"
SNIPPET_MAX = 400
#: 只读检索不返回正文字节，因此这里恒为 False（全文只能经签名交付按需取）。
_METADATA_ONLY_CONTENT = False


def _acl_allows(row: ResearchAssetRow, actor_id: str) -> bool:
    acl = row.acl or {}
    if bool(acl.get("public")):
        return True
    if str(row.owner_id) == actor_id:
        return True
    return actor_id in {str(x) for x in (acl.get("readers") or [])}


def _rights_of(row: ResearchAssetRow) -> AccessRights:
    raw = dict(row.rights or {})
    status = (raw.get("status") or "UNKNOWN").upper()
    evidence_ref = str(raw.get("rights_evidence_ref") or f"research_asset:{row.id}")
    return AccessRights(
        rights_status=status,
        rights_evidence_ref=evidence_ref,
        redistributable=bool(raw.get("redistributable", False)),
        license_id=str(raw.get("license") or ""),
        # RV04 口径：UNKNOWN/RESTRICTED 一律不外传全文
        fulltext_allowed=status == "PUBLIC" and bool(raw.get("license")),
    )


def _snippet_of(row: ResearchAssetRow) -> str:
    size_kb = round((row.size_bytes or 0) / 1024, 1)
    name = row.original_filename or str(row.id)
    return f"{name}（{row.media_type}，{size_kb} KB）"[:SNIPPET_MAX]


class ProjectAssetAdapter(FederatedProvider):
    """RV04 资产元数据的 project-scoped 只读检索。"""

    name = "project_asset"
    version = "1.0.0"
    evidence_type = EvidenceType.PROJECT_ASSET

    def __init__(self, db: Session) -> None:
        self._db = db

    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        # 资产是 project 级对象：scope 未限定 project 时 fail closed（返回空，不放行全部）
        if not scope.project_ids:
            return []

        try:
            tenant_id = uuid.UUID(scope.tenant_id)
        except ValueError as exc:
            raise ProviderError(f"tenant_id 非法: {scope.tenant_id}") from exc

        project_uuids: list[uuid.UUID] = []
        for raw in sorted(scope.project_ids):
            try:
                project_uuids.append(uuid.UUID(raw))
            except ValueError:
                continue
        if not project_uuids:
            return []

        stmt = (
            select(ResearchAssetRow)
            .where(
                ResearchAssetRow.tenant_id == tenant_id,
                ResearchAssetRow.project_id.in_(project_uuids),
                ResearchAssetRow.revoked_at.is_(None),
                ResearchAssetRow.scan_status == SCAN_CLEAN,
            )
        )
        rows = list(self._db.execute(stmt).scalars())

        # ACL 在**应用层**复核：public 之外的资产必须由 owner/readers 命中
        rows = [r for r in rows if _acl_allows(r, scope.actor_id)]
        if not rows:
            return []

        scored: list[tuple[float, ResearchAssetRow]] = []
        for row in rows:
            score = lexical_score(query.query_text, row.original_filename, row.media_type)
            # 有查询词时才按词法过滤；空查询 = 广度浏览（列出该范围内候选）
            if query.query_text.strip() and score <= 0:
                continue
            scored.append((score, row))
        if not scored:
            return []

        stamps = [row.created_at for _, row in scored if row.created_at is not None]
        newest = max(stamps) if stamps else None
        oldest = min(stamps) if stamps else None

        hits: list[ProviderHit] = []
        for score, row in scored:
            rights = _rights_of(row)
            warnings: list[str] = []
            if rights.rights_status == "UNKNOWN":
                warnings.append(WarnCode.RIGHTS_MISSING.value)
            if not rights.fulltext_allowed:
                warnings.append(WarnCode.NO_RIGHTS_FULLTEXT_BLOCKED.value)
            hits.append(
                ProviderHit(
                    evidence_type=EvidenceType.PROJECT_ASSET,
                    provider=self.name,
                    provider_version=self.version,
                    locator=AssetLocator(
                        asset_id=str(row.id),
                        asset_version=int(row.version or 1),
                        content_hash=row.content_hash or "",
                    ),
                    snippet=_snippet_of(row),
                    rights=rights,
                    provenance=Provenance(
                        retrieved_at=row.created_at.isoformat() if row.created_at else "",
                        parser_version=PARSER_VERSION,
                        source_hash=row.content_hash or "",
                        index_version="",
                    ),
                    retrieved_at=row.created_at.isoformat() if row.created_at else "",
                    score_components=ScoreComponents(
                        lexical=round(score, 6),
                        vector=0.0,  # 资产检索无向量列，不编造
                        rights_penalty=0.0 if rights.redistributable else 0.05,
                        recency=round(recency_component(row.created_at, newest=newest, oldest=oldest), 6),
                    ),
                    scope_meta=HitScope(
                        tenant_id=str(row.tenant_id),
                        project_id=str(row.project_id),
                        school_id=None,
                        visibility="project",
                    ),
                    warnings=tuple(dict.fromkeys(warnings)),
                    labels=("PROJECT_ASSET", "METADATA_ONLY"),
                    citation_eligible=False,
                    record_kind="ASSET_METADATA",
                )
            )
        return hits


__all__ = ["ProjectAssetAdapter", "PARSER_VERSION"]
