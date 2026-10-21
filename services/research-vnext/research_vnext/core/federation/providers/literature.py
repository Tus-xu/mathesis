"""RV06 · LiteratureProvider 适配器（复用 RV04 的 metadata / rights / anchors）。

只做**只读检索**：数据来自 RV04 已入库的 `literature_record`，
仍是 `record_kind == METADATA_ONLY` —— 所以：

- `snippet` 只由 title/venue/date 组成，**不**把 abstract 当全文正文塞进上下文；
- `fulltext_allowed` 只有在 rights 明确为 PUBLIC 且带 license 时才为 True；
- 没有 DOI 的行 `doi` 留空，绝不生成代理标识。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...literature.models import LiteratureRecordRow
from ..contracts import (
    AccessRights,
    FederatedQuery,
    HitScope,
    LiteratureLocator,
    Provenance,
    ProviderHit,
    ScoreComponents,
)
from ..scope import ResolvedScope
from ..scoring import lexical_score, parse_ts, recency_component
from ..types import EvidenceType, WarnCode
from .base import FederatedProvider, ProviderError

SNIPPET_MAX = 400
PARSER_VERSION = "rv04-crossref-adapter@1.0.0"


def _rights_of(row: LiteratureRecordRow) -> AccessRights:
    status = (row.rights_status or "UNKNOWN").upper()
    detail = dict(row.rights_detail or {})
    evidence_ref = str(
        detail.get("rights_evidence_ref")
        or f"literature_record:{row.provider}:{row.provider_record_id}"
    )
    license_id = str(detail.get("license") or detail.get("license_id") or "")
    fulltext_allowed = status == "PUBLIC" and bool(license_id)
    return AccessRights(
        rights_status=status,
        rights_evidence_ref=evidence_ref,
        redistributable=bool(detail.get("redistributable", False)),
        license_id=license_id,
        # 只有 rights 明确允许时才可能外传全文；否则永远 False
        fulltext_allowed=fulltext_allowed,
    )


def _snippet_of(row: LiteratureRecordRow) -> str:
    parts = [p for p in (row.title, row.venue, row.published_date) if p]
    text = " ｜ ".join(parts) or row.doi or "(metadata without title)"
    return text[:SNIPPET_MAX]


class LiteratureAdapter(FederatedProvider):
    """RV04 metadata 的只读检索适配器。"""

    name = "literature"
    version = "1.0.0"
    evidence_type = EvidenceType.LITERATURE

    def __init__(self, db: Session, *, project_scoped: bool = True) -> None:
        self._db = db
        self._project_scoped = project_scoped

    def search(self, query: FederatedQuery, scope: ResolvedScope) -> Sequence[ProviderHit]:
        try:
            tenant_id = uuid.UUID(scope.tenant_id)
        except ValueError as exc:
            raise ProviderError(f"tenant_id 非法: {scope.tenant_id}") from exc

        stmt = select(LiteratureRecordRow).where(LiteratureRecordRow.tenant_id == tenant_id)
        if self._project_scoped and scope.project_ids:
            project_uuids: list[uuid.UUID] = []
            for raw in scope.project_ids:
                try:
                    project_uuids.append(uuid.UUID(raw))
                except ValueError:
                    continue
            if project_uuids:
                stmt = stmt.where(LiteratureRecordRow.project_id.in_(project_uuids))

        rows = list(self._db.execute(stmt).scalars())
        if not rows:
            return []

        scored: list[tuple[float, LiteratureRecordRow]] = []
        author_text = {
            row.id: " ".join(
                str(a.get("name") or a.get("family") or "") for a in (row.authors or [])
            )
            for row in rows
        }
        for row in rows:
            score = lexical_score(query.query_text, row.title, author_text.get(row.id, ""), row.venue, row.doi)
            # 有查询词时才按词法过滤；空查询 = 广度浏览（列出该范围内候选）
            if query.query_text.strip() and score <= 0:
                continue
            scored.append((score, row))
        if not scored:
            return []

        timestamps = [parse_ts(row.retrieved_at) for _, row in scored]
        known = [t for t in timestamps if t is not None]
        newest = max(known) if known else None
        oldest = min(known) if known else None

        hits: list[ProviderHit] = []
        for score, row in scored:
            warnings: list[str] = []
            rights = _rights_of(row)
            if not rights.fulltext_allowed:
                # 如实标注：本轮不提供全文，也不假装它有
                warnings.append(WarnCode.NO_RIGHTS_FULLTEXT_BLOCKED.value)
            locator = LiteratureLocator(
                doi=row.doi or "",
                venue=row.venue or "",
                section="",
            )
            hits.append(
                ProviderHit(
                    evidence_type=EvidenceType.LITERATURE,
                    provider=self.name,
                    provider_version=self.version,
                    locator=locator,
                    snippet=_snippet_of(row),
                    rights=rights,
                    provenance=Provenance(
                        retrieved_at=row.retrieved_at.isoformat() if row.retrieved_at else "",
                        parser_version=PARSER_VERSION,
                        source_hash=row.raw_response_hash or "",
                        provider_version=row.provider_version or "",
                        index_version="",
                    ),
                    retrieved_at=row.retrieved_at.isoformat() if row.retrieved_at else "",
                    score_components=ScoreComponents(
                        lexical=round(score, 6),
                        vector=0.0,  # literature_record 无向量列，不编造
                        rights_penalty=0.0 if rights.fulltext_allowed else 0.1,
                        recency=round(recency_component(row.retrieved_at, newest=newest, oldest=oldest), 6),
                    ),
                    scope_meta=HitScope(
                        tenant_id=str(row.tenant_id),
                        project_id=str(row.project_id),
                        school_id=None,
                        visibility="project",
                    ),
                    warnings=tuple(warnings),
                    labels=("LITERATURE", "METADATA_ONLY"),
                    # 有 DOI 才构成可引用标识；无 DOI 的 metadata-only 行不可被当成引用来源
                    citation_eligible=bool(row.doi),
                    record_kind=row.record_kind or "METADATA_ONLY",
                )
            )
        return hits


__all__ = ["LiteratureAdapter", "PARSER_VERSION"]
