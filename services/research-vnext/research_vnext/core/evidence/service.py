"""RV07 · Evidence 服务：propose → 校验 → 入库；授权读取；回源；充分性判定。

**AI 只能 propose。** 入库路径是：
`EvidenceProposal`（模型产出）→ `materialize_card`（schema/locator/rights 校验 +
权利裁剪）→ `EvidenceCard`（事实）→ 行。

幂等键是 `proposal_hash`（proposal 的确定性序列化）：同一条证据被模型重复提议
只落一行，不会出现"同一句证据挂两张卡"。
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
from .contracts import (
    Confidence,
    EvidenceACL,
    EvidenceCard,
    EvidenceProposal,
    LocatorBinding,
    Provenance,
    RightsInfo,
    SourceRef,
    StructuredFact,
    materialize_card,
    rights_policy_for,
)
from .errors import EvidenceLocatorError
from .locator import ResolvedLocator, resolve_locator
from .models import EvidenceCardV2Row
from .no_evidence import Sufficiency, assess
from .types import ClaimKind, EvidenceStatus, LocatorKind, StaleState

__all__ = [
    "authorize_card",
    "propose_card",
    "list_cards",
    "get_card",
    "resolve_card_locator",
    "row_to_card",
    "card_public_dict",
    "grounded_card_ids_for_session",
    "assess_session_evidence",
]


# --------------------------------------------------------------------------
# 授权
# --------------------------------------------------------------------------


def _check_project_scope(ctx: AccessContext, acl: EvidenceACL) -> None:
    """证据的归属范围必须落在 AccessContext 的签名 scope 内。"""
    if acl.session_id and ctx.session_scope_ids:
        if acl.session_id not in ctx.session_scope_ids and acl.project_id not in ctx.project_scope_ids:
            raise ObjectScopeMissing("证据的 session 不在 AccessContext 的 session scope 内")
    if acl.project_id:
        if ctx.project_scope_ids:
            if acl.project_id not in ctx.project_scope_ids:
                raise ProjectMismatch("证据的 project 不在 AccessContext 的 project scope 内")
        elif acl.owner_id != str(ctx.actor_id):
            raise ObjectScopeMissing("缺少对象级 scope（禁止仅凭 identity 写入证据）")


def authorize_card(db: Session, ctx: AccessContext, row: EvidenceCardV2Row) -> None:
    """对象级鉴权：tenant → visibility → scope → capability → 撤权 floor。"""
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    if str(row.tenant_id) != str(ctx.tenant_id):
        raise TenantMismatch(
            "证据卡不属于当前 tenant",
            card_tenant=str(row.tenant_id),
            actor_tenant=str(ctx.tenant_id),
        )
    acl = dict(row.acl or {})
    visibility = str(acl.get("visibility") or "project")
    if visibility == "tenant":
        return
    if visibility == "private":
        if str(row.owner_id) != str(ctx.actor_id):
            raise ObjectScopeMissing("private 证据只能由 owner 读取")
        return
    if visibility == "session":
        if str(row.session_id) in ctx.session_scope_ids or str(row.owner_id) == str(ctx.actor_id):
            return
        raise ObjectScopeMissing("证据不在当前 session scope 内")
    # project（默认）
    if row.project_id is not None and str(row.project_id) in ctx.project_scope_ids:
        return
    if str(row.owner_id) == str(ctx.actor_id):
        return
    raise ObjectScopeMissing("证据不在当前 project scope 内")


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------


def propose_card(
    db: Session, ctx: AccessContext, proposal: EvidenceProposal
) -> tuple[EvidenceCardV2Row, bool]:
    """把模型提议落成事实卡。返回 `(row, created)`。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)

    acl = proposal.acl
    if acl.tenant_id != str(ctx.tenant_id):
        raise TenantMismatch(
            "证据 ACL 的 tenant 与 AccessContext 不一致",
            acl_tenant=acl.tenant_id,
            actor_tenant=str(ctx.tenant_id),
        )
    _check_project_scope(ctx, acl)

    # 幂等：先看是否已有同一 proposal
    digest = proposal.proposal_hash
    existing = db.execute(
        select(EvidenceCardV2Row).where(
            EvidenceCardV2Row.tenant_id == ctx.tenant_id,
            EvidenceCardV2Row.proposal_hash == digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    # 校验（schema / locator / rights / ACL）+ 权利裁剪，任一不过即异常
    card = materialize_card(proposal, card_id=str(uuid.uuid4()))
    row = _card_to_row(card, tenant_id=ctx.tenant_id, proposal_hash=digest)
    db.add(row)
    db.flush()
    return row, True


def _card_to_row(
    card: EvidenceCard, *, tenant_id: uuid.UUID, proposal_hash: str
) -> EvidenceCardV2Row:
    acl = card.acl
    return EvidenceCardV2Row(
        id=uuid.UUID(card.card_id),
        tenant_id=tenant_id,
        project_id=uuid.UUID(acl.project_id) if acl.project_id else None,
        session_id=uuid.UUID(acl.session_id) if acl.session_id else None,
        owner_id=uuid.UUID(acl.owner_id) if acl.owner_id else None,
        evidence_type=card.evidence_type.value,
        source_ref=card.source_ref.as_dict(),
        locator=card.locator.as_dict(),
        locator_kind=card.locator.kind.value,
        snippet=card.snippet,
        snippet_hash=card.snippet_hash,
        structured_fact=card.structured_fact.as_dict() if card.structured_fact else None,
        provenance=card.provenance.as_dict(),
        rights=card.rights.as_dict(),
        rights_status=card.rights.rights_status.value,
        retrieved_at=card.retrieved_at,
        created_by_run=card.created_by_run,
        confidence=card.confidence.as_dict(),
        limitations=list(card.limitations),
        status=card.status.value,
        version=card.version,
        acl=acl.as_dict(),
        stale_state=card.stale_state.value,
        stale_reason=card.stale_reason,
        content_hash=card.provenance.source_hash,
        citation_eligible=card.citation_eligible,
        labels=list(card.labels),
        warnings=list(card.warnings),
        proposal_hash=proposal_hash,
    )


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------


def row_to_card(row: EvidenceCardV2Row) -> EvidenceCard:
    """行 → 事实对象（重新过一遍构造期校验，防止库里的脏数据被当成合法证据）。"""
    locator_dict = dict(row.locator or {})
    kind = locator_dict.pop("kind", row.locator_kind or "EXTERNAL")
    return EvidenceCard(
        card_id=str(row.id),
        evidence_type=row.evidence_type,
        source_ref=SourceRef(**dict(row.source_ref or {})),
        locator=LocatorBinding(kind=LocatorKind(kind), payload=locator_dict),
        snippet=row.snippet or "",
        structured_fact=StructuredFact(**dict(row.structured_fact)) if row.structured_fact else None,
        snippet_hash=row.snippet_hash,
        provenance=Provenance(**dict(row.provenance or {})),
        rights=RightsInfo(**dict(row.rights or {})),
        retrieved_at=row.retrieved_at,
        created_by_run=row.created_by_run or "",
        confidence=Confidence(**dict(row.confidence or {})),
        limitations=tuple(row.limitations or []),
        status=EvidenceStatus(row.status),
        version=int(row.version),
        acl=EvidenceACL(**dict(row.acl or {})),
        stale_state=StaleState(row.stale_state or "FRESH"),
        stale_reason=row.stale_reason or "",
        labels=tuple(row.labels or []),
        warnings=tuple(row.warnings or []),
    )


def get_card(db: Session, ctx: AccessContext, card_id: uuid.UUID) -> EvidenceCardV2Row:
    row = db.get(EvidenceCardV2Row, card_id)
    if row is None:
        raise ObjectScopeMissing(f"证据卡不存在: {card_id}")
    authorize_card(db, ctx, row)
    return row


def list_cards(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[EvidenceCardV2Row]:
    check_revocation(db, ctx)
    require_capability(ctx, READ_CAPABILITY)
    stmt = select(EvidenceCardV2Row).where(EvidenceCardV2Row.tenant_id == ctx.tenant_id)
    if session_id is not None:
        stmt = stmt.where(EvidenceCardV2Row.session_id == session_id)
    if project_id is not None:
        if ctx.project_scope_ids and str(project_id) not in ctx.project_scope_ids:
            raise ProjectMismatch("请求的 project 不在 AccessContext 的 project scope 内")
        stmt = stmt.where(EvidenceCardV2Row.project_id == project_id)
    elif ctx.project_scope_ids:
        stmt = stmt.where(
            EvidenceCardV2Row.project_id.in_([uuid.UUID(p) for p in ctx.project_scope_ids])
        )
    stmt = stmt.order_by(EvidenceCardV2Row.created_at).limit(max(1, min(limit, 500)))
    return list(db.execute(stmt).scalars().all())


def card_public_dict(row: EvidenceCardV2Row) -> dict[str, Any]:
    """对外投影。**原文可见性由 rights 决定**：受限卡只出指纹与结构化事实。"""
    card = row_to_card(row)
    policy = rights_policy_for(card.rights)
    payload = card.to_dict()
    if not policy.reader_fulltext_visible:
        payload["snippet"] = ""
        payload["snippet_redacted"] = True
    else:
        payload["snippet_redacted"] = False
    payload["rights_policy"] = policy.as_dict()
    payload["updated_at"] = row.updated_at.isoformat() if row.updated_at else ""
    return payload


def grounded_card_ids_for_session(
    db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID | None = None
) -> set[str]:
    """会话内**可作为 `origin=SOURCE` 依据**的卡片 id 集合（RV08 接口对齐）。

    供 `origin_grounding.ground_content_origins()` 作 `grounded_ids` 入参：
    只有 `status=ACTIVE` 且非 stale 的卡才承认"这条内容有来源背书"。

    `tenant_id` 可选但建议传：不传时仅按 `session_id` 过滤
    （session 本身已在上游授权，且一个 session 只属于一个 tenant）。

    **只取裁决所需的 3 列**（`id` / `status` / `stale_state`）：
    本函数挂在**每条提案**的准备路径上（`proposal_handlers.prepare`），
    原先 `select(EvidenceCardV2Row)` 会把 `snippet`(Text) 与
    `locator`/`source_ref`/`provenance`/`rights`/`confidence` 等一堆 JSONB
    全量拉回，再逐行跑 `row_to_card` + `rights_policy_for` + `to_dict`
    —— 只为读两个字段。

    裁决**仍复用** `origin_grounding.is_grounded_card`，不在 SQL 里重写规则：
    判据写两遍必然漂移（一处改了另一处忘）。
    """
    from .origin_grounding import is_grounded_card

    stmt = select(
        EvidenceCardV2Row.id,
        EvidenceCardV2Row.status,
        EvidenceCardV2Row.stale_state,
    ).where(EvidenceCardV2Row.session_id == session_id)
    if tenant_id is not None:
        stmt = stmt.where(EvidenceCardV2Row.tenant_id == tenant_id)

    return {
        str(row.id)
        for row in db.execute(stmt).all()
        if is_grounded_card({"status": row.status, "stale_state": row.stale_state})
    }


# --------------------------------------------------------------------------
# 回源
# --------------------------------------------------------------------------


def resolve_card_locator(db: Session, ctx: AccessContext, card_id: uuid.UUID) -> ResolvedLocator:
    """点击 Evidence 的回源入口（**经授权**）。"""
    row = get_card(db, ctx, card_id)
    locator_dict = dict(row.locator or {})
    kind = locator_dict.pop("kind", row.locator_kind or "EXTERNAL")
    locator = LocatorBinding(kind=LocatorKind(kind), payload=locator_dict)

    if locator.kind is not LocatorKind.ANCHOR:
        return resolve_locator(locator)

    from ..assets.models import ResearchAssetRow
    from ..parser.models import AssetAnchorRow

    anchor_id = str(locator.payload.get("anchor_id"))
    anchor = db.execute(
        select(AssetAnchorRow).where(AssetAnchorRow.anchor_id == anchor_id)
    ).scalar_one_or_none()
    if anchor is not None and str(anchor.tenant_id) != str(ctx.tenant_id):
        # 跨租户 anchor 一律视为"定位不可用"，不泄露其存在性细节
        raise EvidenceLocatorError("anchor 不属于当前 tenant")

    asset_id_raw = str(locator.payload.get("asset_id") or "")
    asset = None
    if asset_id_raw:
        try:
            asset = db.get(ResearchAssetRow, uuid.UUID(asset_id_raw))
        except ValueError:
            asset = None
    if asset is not None and str(asset.tenant_id) != str(ctx.tenant_id):
        raise EvidenceLocatorError("资产不属于当前 tenant")

    if asset is None or asset.revoked_at is not None:
        current_version, current_hash = None, None
    else:
        current_version, current_hash = int(asset.version), str(asset.content_hash)

    return resolve_locator(
        locator,
        anchor=anchor,
        current_asset_version=current_version,
        current_content_hash=current_hash,
    )


# --------------------------------------------------------------------------
# 充分性
# --------------------------------------------------------------------------


def assess_session_evidence(
    db: Session,
    ctx: AccessContext,
    *,
    session_id: uuid.UUID,
    claim_kind: ClaimKind,
) -> Sufficiency:
    rows = list_cards(db, ctx, session_id=session_id, limit=500)
    cards = [row_to_card(r) for r in rows]
    return assess(cards, claim_kind=claim_kind)


def utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
