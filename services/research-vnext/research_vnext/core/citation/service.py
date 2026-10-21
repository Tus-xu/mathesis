"""RV07 · Citation 服务：canonical 落库 + 派生格式 + 往返对账。

两条硬规则：

1. **引用只能从"可引用"的证据来**。教材卡（`citation_eligible=False`）不能
   作为引用的来源 —— 这是"教材 citation 伪 DOI"在服务层的封堵点；
2. **DOI 必须与证据一致**。请求体里的 DOI 与证据 `source_ref.doi` 不一致时
   直接拒绝，不允许"给一条没有 DOI 的证据挂一个 DOI"。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    AccessContext,
    check_revocation,
    require_capability,
)
from ..domain.errors import ObjectScopeMissing, ProjectMismatch, TenantMismatch
from .csl import CslItem, from_csl_json, normalize_doi, to_csl_json
from .errors import CitationContractError, CitationNotEligibleError
from .formats import to_bibtex, to_doi, to_ris
from .models import CitationRow
from .roundtrip import assert_no_silent_loss, roundtrip

__all__ = [
    "create_citation",
    "get_citation",
    "list_citations",
    "citation_public_dict",
    "render_formats",
    "canonical_hash",
    "citation_from_csl_json",
]

DERIVED_FORMATS: tuple[str, ...] = ("bibtex", "ris", "doi")


def canonical_hash(item: CslItem) -> str:
    blob = json.dumps(to_csl_json(item), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def citation_from_csl_json(payload: dict[str, Any]) -> CslItem:
    """入口解析：canonical 只认 CSL-JSON。"""
    if not isinstance(payload, dict) or not payload:
        raise CitationContractError("citation 必须提供非空 CSL-JSON")
    return from_csl_json(payload)


def render_formats(item: CslItem) -> tuple[dict[str, str], dict[str, list[str]]]:
    """渲染三种派生格式，并**逐个**做往返对账。

    对账不通过（静默丢失/编造）→ 抛错：宁可不导出，也不导出对不上账的引用。
    """
    rendered: dict[str, str] = {}
    lossy: dict[str, list[str]] = {}
    for fmt in DERIVED_FORMATS:
        try:
            rendered[fmt] = {
                "bibtex": to_bibtex,
                "ris": to_ris,
                "doi": to_doi,
            }[fmt](item)
        except CitationContractError as exc:
            # 缺 DOI 时 doi 视图不可渲染 —— 这是"不编造"的正常结果，不是失败。
            rendered[fmt] = ""
            lossy[fmt] = [f"unavailable:{exc}"]
            continue
        _parsed, report = roundtrip(item, fmt)
        assert_no_silent_loss(report)
        lossy[fmt] = list(report.unsupported_by_format)
    return rendered, lossy


def _load_card_for_citation(db: Session, ctx: AccessContext, card_id: uuid.UUID):
    from ..evidence.models import EvidenceCardV2Row
    from ..evidence.service import authorize_card

    row = db.get(EvidenceCardV2Row, card_id)
    if row is None:
        raise ObjectScopeMissing(f"证据卡不存在: {card_id}")
    authorize_card(db, ctx, row)
    return row


def create_citation(
    db: Session,
    ctx: AccessContext,
    item: CslItem,
    *,
    evidence_card_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
) -> tuple[CitationRow, bool]:
    """落一条引用（按 canonical hash 幂等）。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)

    # project 维度只能来自签名 scope（与 federation 路由同一条纪律）
    if project_id is not None and ctx.project_scope_ids and str(project_id) not in ctx.project_scope_ids:
        raise ProjectMismatch("引用的 project 不在 AccessContext 的 project scope 内")

    if evidence_card_id is not None:
        card = _load_card_for_citation(db, ctx, evidence_card_id)
        if not bool(card.citation_eligible):
            raise CitationNotEligibleError(
                f"证据类型 {card.evidence_type} 不允许作为文献引用来源"
                "（教材与 AI 派生证据不得被引用为文献）"
            )
        source = dict(card.source_ref or {})
        source_doi = normalize_doi(str(source.get("doi") or ""))
        item_doi = normalize_doi(item.doi)
        if source_doi and item_doi and source_doi != item_doi:
            raise CitationContractError(
                f"引用 DOI 与证据 source_ref.doi 不一致（{item_doi} != {source_doi}）"
            )
        if source_doi and not item_doi:
            # 证据有 DOI 而引用没写：不是错误，但也不替它补上（不编造）。
            pass
        if str(card.tenant_id) != str(ctx.tenant_id):
            raise TenantMismatch("证据卡不属于当前 tenant")

    payload = to_csl_json(item)
    digest = canonical_hash(item)

    existing = db.execute(
        select(CitationRow).where(
            CitationRow.tenant_id == ctx.tenant_id,
            CitationRow.csl_hash == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    rendered, lossy = render_formats(item)
    row = CitationRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        session_id=session_id,
        evidence_card_id=evidence_card_id,
        csl_json=payload,
        csl_hash=digest,
        csl_type=item.csl_type,
        doi=normalize_doi(item.doi),
        bibtex=rendered.get("bibtex", ""),
        ris=rendered.get("ris", ""),
        lossy_fields=lossy,
    )
    db.add(row)
    db.flush()
    return row, True


def _authorize_read(db: Session, ctx: AccessContext, row: CitationRow) -> None:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    if str(row.tenant_id) != str(ctx.tenant_id):
        raise TenantMismatch("引用不属于当前 tenant")
    if row.project_id is not None:
        if ctx.project_scope_ids:
            if str(row.project_id) not in ctx.project_scope_ids:
                raise ProjectMismatch("引用不在当前 project scope 内")
        elif ctx.session_scope_ids:
            if str(row.session_id) not in ctx.session_scope_ids:
                raise ObjectScopeMissing("引用不在当前 session scope 内")


def get_citation(db: Session, ctx: AccessContext, citation_id: uuid.UUID) -> CitationRow:
    row = db.get(CitationRow, citation_id)
    if row is None:
        raise ObjectScopeMissing(f"引用不存在: {citation_id}")
    _authorize_read(db, ctx, row)
    return row


def list_citations(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[CitationRow]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    stmt = select(CitationRow).where(CitationRow.tenant_id == ctx.tenant_id)
    if project_id is not None:
        if ctx.project_scope_ids and str(project_id) not in ctx.project_scope_ids:
            raise ProjectMismatch("请求的 project 不在 AccessContext 的 project scope 内")
        stmt = stmt.where(CitationRow.project_id == project_id)
    elif ctx.project_scope_ids:
        stmt = stmt.where(CitationRow.project_id.in_([uuid.UUID(p) for p in ctx.project_scope_ids]))
    stmt = stmt.order_by(CitationRow.created_at).limit(max(1, min(limit, 500)))
    return list(db.execute(stmt).scalars().all())


def citation_public_dict(row: CitationRow) -> dict[str, Any]:
    """对外投影：canonical + 派生格式 + 明确的丢字段清单。"""
    return {
        "id": str(row.id),
        "evidence_card_id": str(row.evidence_card_id) if row.evidence_card_id else None,
        "csl": dict(row.csl_json or {}),
        "csl_hash": row.csl_hash,
        "csl_type": row.csl_type,
        "doi": row.doi,
        "formats": {
            "bibtex": row.bibtex,
            "ris": row.ris,
        },
        # 派生格式**结构上**无法承载的字段：显式告知，避免"导出了就以为全带上了"
        "unsupported_by_format": dict(row.lossy_fields or {}),
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
