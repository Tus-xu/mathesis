"""RV13 · PaperSpec 修订链服务：人工编辑 / AI patch 落库 / 撤销 / 恢复 / 绑定健康。

核心不变量
----------
- **append-only**：任何变更（含 undo/redo）都写**新的不可变修订行**，
  历史修订行永不改写 —— 撤销是"新建一个内容等于上一版"的修订，
  不是把库回滚回去；
- **canonical 存储**：落库前统一过 `validate_blocks` + 公式 canonical 重算 +
  引用快照回填；DOM/HTML/LaTeX 全文都不进库；
- **事实源健康在读取时如实报告**：citation 版本漂移、figure/table 锚点丢失、
  claim 状态漂移都以 `bindings` 通道返回，不静默指向新内容。
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..domain.errors import RevisionConflict
from .diff import diff_nodes
from .errors import PaperSchemaError, PaperStateError
from .nodes import validate_blocks
from .patch import apply_ops, validate_ops

ACTION_MANUAL = "MANUAL_EDIT"
ACTION_UNDO = "UNDO"
ACTION_REDO = "REDO"
ACTION_AI_PATCH = "AI_PATCH_ACCEPT"


# --------------------------------------------------------------------------
# DB 投影（谁可解析 / 什么状态）——保持 patch 模块纯函数
# --------------------------------------------------------------------------


def paper_resolvers(db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID) -> dict[str, Any]:
    """把 DB 真源投影成 patch/validate 需要的解析集合。

    只做**读**；每次调用都反映当前事实（证据失效、claim 重开、引用更新
    都会在下一次调用里体现）。
    """
    from ..db.models import CitationRow, ClaimRow
    from ..evidence.resolvable import resolvable_evidence_ids
    from ..parser.models import AssetAnchorRow

    evidence = resolvable_evidence_ids(db, session_id=session_id)

    claim_rows = (
        db.query(ClaimRow)
        .filter(ClaimRow.session_id == session_id)
        .order_by(ClaimRow.claim_key, ClaimRow.revision)
        .all()
    )
    latest_by_key: dict[str, ClaimRow] = {}
    for r in claim_rows:
        latest_by_key[str(r.claim_key or r.id)] = r  # revision 升序 → 后者覆盖
    resolvable_claims = {
        k
        for k, r in latest_by_key.items()
        if r.published_at is not None and not bool(r.requires_review)
    }
    claim_status = {k: str(r.status) for k, r in latest_by_key.items()}

    resolvable_citations: set[str] = set()
    citation_hashes: dict[str, str] = {}
    citation_meta: dict[str, dict[str, str]] = {}
    for c in (
        db.query(CitationRow)
        .filter(CitationRow.tenant_id == tenant_id)
        .all()
    ):
        if c.session_id is not None and str(c.session_id) != str(session_id):
            continue
        cid = str(c.id)
        resolvable_citations.add(cid)
        citation_hashes[cid] = str(c.csl_hash or "")
        citation_meta[cid] = {
            "cslHash": str(c.csl_hash or ""),
            "doi": str(c.doi or ""),
            "title": str((c.csl_json or {}).get("title") or ""),
        }

    anchors = db.query(AssetAnchorRow).filter(AssetAnchorRow.tenant_id == tenant_id).all()
    resolvable_anchors = {a.anchor_id for a in anchors}
    anchor_details = {
        a.anchor_id: {
            "assetId": str(a.asset_id),
            "assetVersion": int(a.asset_version),
            "contentHash": str(a.content_hash or ""),
        }
        for a in anchors
    }

    return {
        "resolvable_evidence": evidence,
        "resolvable_claims": resolvable_claims,
        "claim_status": claim_status,
        "resolvable_citations": resolvable_citations,
        "citation_hashes": citation_hashes,
        "citation_meta": citation_meta,
        "resolvable_anchors": resolvable_anchors,
        "anchor_details": anchor_details,
    }


# --------------------------------------------------------------------------
# 落库前的 canonical 化（公式 canonical + 引用快照回填）
# --------------------------------------------------------------------------


def enrich_blocks(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    blocks: list[dict[str, Any]],
    is_ai: bool = False,
) -> list[dict[str, Any]]:
    """对 validated blocks 回填服务端权威字段：canonical/astHash 与三类快照。"""
    from .formula import FormulaError, ast_hash, canonical, parse_latex

    r = paper_resolvers(db, session_id=session_id, tenant_id=tenant_id)
    out: list[dict[str, Any]] = []
    for node in blocks:
        node = dict(node)
        if node.get("type") == "equation":
            latex = str(node.get("latex") or node.get("text") or "").strip()
            try:
                ast = parse_latex(latex)
            except FormulaError as e:
                raise PaperSchemaError("PAPER_FORMULA_UNPARSABLE", f"公式无法解析: {e}") from e
            node["latex"] = latex
            node.pop("text", None)
            node.setdefault("display", False)
            node["canonical"] = canonical(ast)
            node["astHash"] = ast_hash(ast)
        claim_ref = str(node.get("claimRef") or "").strip()
        if claim_ref:
            if claim_ref not in r["resolvable_claims"]:
                status = r["claim_status"].get(claim_ref, "")
                raise PaperSchemaError(
                    "PAPER_CLAIM_REF_UNRESOLVED"
                    if not status
                    else "PAPER_CLAIM_REF_UNPUBLISHED",
                    f"claimRef 不可解析或未发布: {claim_ref}（当前 {status or '不存在'}）",
                    claimId=claim_ref,
                    status=status,
                )
            status = str(r["claim_status"].get(claim_ref, ""))
            from ..claim.types import STATUS_LABELS

            node["claimSnapshot"] = {
                "claimId": claim_ref,
                "status": status,
                "statusLabel": STATUS_LABELS.get(status, status),
                "published": True,
            }
        if node.get("type") == "citation":
            snaps: dict[str, dict[str, str]] = {}
            for ref in node.get("refs") or []:
                if ref not in r["resolvable_citations"]:
                    raise PaperSchemaError(
                        "PAPER_CITATION_UNRESOLVED",
                        f"citation 指向不存在的引用: {ref}",
                        citationId=ref,
                    )
                snaps[str(ref)] = dict(r["citation_meta"].get(ref) or {"cslHash": ""})
            node["citationSnapshot"] = snaps
        if node.get("type") in {"figure", "table"}:
            lineage: dict[str, dict[str, Any]] = {}
            for ref in node.get("refs") or []:
                if ref not in r["resolvable_anchors"]:
                    raise PaperSchemaError(
                        "PAPER_FIGURE_LOCATOR_UNRESOLVED",
                        f"figure/table locator 指向不存在的锚点: {ref}",
                        anchorId=ref,
                    )
                lineage[str(ref)] = dict(r["anchor_details"][ref])
            node["lineage"] = lineage
        out.append(node)
    return out


# --------------------------------------------------------------------------
# 修订行
# --------------------------------------------------------------------------


def latest_paper_row(db: Session, session_id: uuid.UUID):
    from ..db.models import PaperSpecRow

    return (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id)
        .order_by(PaperSpecRow.revision.desc())
        .first()
    )


def _append_revision(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    trace_id: str,
    content: dict[str, Any],
    action: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..db.models import PaperEditAuditRow, PaperSpecRow

    prev = latest_paper_row(db, session_id)
    revision = int(prev.revision or 0) + 1 if prev is not None else 1
    now = datetime.now(timezone.utc)
    content_hash = _canonical_hash(content)
    row = PaperSpecRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        revision=revision,
        content=content,
        content_hash=content_hash,
        client_request_id=f"paper:{action.lower()}:{uuid.uuid4().hex[:8]}",
        confirmed_at=now,
        confirmed_by=actor_id,
        trace_id=trace_id,
        created_at=now,
    )
    db.add(row)
    db.add(
        PaperEditAuditRow(
            id=uuid.uuid4(),
            session_id=session_id,
            tenant_id=tenant_id,
            paper_revision=revision,
            action=action,
            actor_id=actor_id,
            trace_id=trace_id,
            detail=dict(detail or {}),
            created_at=now,
        )
    )
    db.flush()
    return {"paperId": str(row.id), "revision": revision, "action": action}


def _canonical_hash(content: dict[str, Any]) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _emit_paper_revised(
    db: Session,
    *,
    session_id: uuid.UUID,
    revision: int,
    action: str,
    actor_id: uuid.UUID | None,
    trace_id: str,
) -> None:
    from ..db.models import DomainEventRow
    from ..domain.events import DomainEvent

    ev = DomainEvent.create(
        event_type="paper.revised",
        session_id=session_id,
        revision=revision,
        payload={"action": action, "paperRevision": revision},
        trace_id=trace_id,
        actor_id=actor_id,
    )
    db.add(
        DomainEventRow(
            event_id=ev.event_id,
            session_id=session_id,
            event_type=ev.event_type,
            schema_version=ev.schema_version,
            revision=revision,
            occurred_at=ev.occurred_at,
            trace_id=trace_id,
            payload=ev.payload,
            actor_id=actor_id,
        )
    )


# --------------------------------------------------------------------------
# 读：paper + 绑定健康
# --------------------------------------------------------------------------


def read_paper(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    from ..db.models import PaperSpecRow

    row = latest_paper_row(db, session_id)
    if row is None:
        return {
            "exists": False,
            "paperId": None,
            "revision": 0,
            "content": {"blocks": []},
            "bindings": {},
        }
    db.refresh(row)
    blocks = validate_blocks(list((row.content or {}).get("blocks") or []), stored=True)
    tenant_id = row.tenant_id
    return {
        "exists": True,
        "paperId": str(row.id),
        "revision": int(row.revision),
        "contentHash": row.content_hash,
        "content": {"blocks": blocks},
        "bindings": _binding_health(db, session_id=session_id, tenant_id=tenant_id, blocks=blocks),
    }


def _binding_health(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    blocks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """读取时如实报告引用健康：ok / stale / missing / unbound。"""
    from ..claim.ledger import get_claim_row
    from ..db.models import CitationRow
    from ..parser.models import AssetAnchorRow

    out: dict[str, dict[str, Any]] = {}

    def _uuid(value: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    for node in blocks:
        nid = str(node.get("id"))
        ntype = node.get("type")
        if ntype == "citation":
            snaps = dict(node.get("citationSnapshot") or {})
            if not snaps:
                out[nid] = {"state": "unbound", "kind": "citation"}
                continue
            missing = False
            stale = False
            detail: dict[str, Any] = {"kind": "citation", "refs": {}}
            for ref in node.get("refs") or []:
                row = db.get(CitationRow, _uuid(ref)) if _uuid(ref) else None
                bound = str((snaps.get(str(ref)) or {}).get("cslHash") or "")
                if row is None:
                    detail["refs"][str(ref)] = {"state": "missing"}
                    missing = True
                    continue
                current = str(row.csl_hash or "")
                if bound and bound != current:
                    detail["refs"][str(ref)] = {
                        "state": "stale", "boundCslHash": bound, "currentCslHash": current,
                    }
                    stale = True
                else:
                    detail["refs"][str(ref)] = {"state": "ok", "cslHash": current}
            out[nid] = {
                "state": "missing" if missing else ("stale" if stale else "ok"),
                **detail,
            }
        elif ntype in {"figure", "table"}:
            lineage = dict(node.get("lineage") or {})
            if not lineage:
                out[nid] = {"state": "unbound", "kind": str(ntype)}
                continue
            missing = False
            stale = False
            detail = {"kind": str(ntype), "refs": {}}
            for ref in node.get("refs") or []:
                anchor = (
                    db.query(AssetAnchorRow)
                    .filter(AssetAnchorRow.anchor_id == str(ref))
                    .first()
                )
                bound = dict(lineage.get(str(ref)) or {})
                if anchor is None:
                    detail["refs"][str(ref)] = {"state": "missing"}
                    missing = True
                    continue
                if (
                    bound.get("assetVersion") is not None
                    and int(bound.get("assetVersion") or 0) != int(anchor.asset_version)
                ) or (
                    bound.get("contentHash")
                    and str(bound.get("contentHash")) != str(anchor.content_hash or "")
                ):
                    detail["refs"][str(ref)] = {
                        "state": "stale",
                        "boundAssetVersion": bound.get("assetVersion"),
                        "currentAssetVersion": int(anchor.asset_version),
                    }
                    stale = True
                else:
                    detail["refs"][str(ref)] = {"state": "ok"}
            out[nid] = {
                "state": "missing" if missing else ("stale" if stale else "ok"),
                **detail,
            }
        elif node.get("claimRef") or ntype == "claim_ref":
            ref = str(node.get("claimRef") or (node.get("refs") or [""])[0])
            key = _uuid(ref)
            row = get_claim_row(db, key) if key else None
            snap = dict(node.get("claimSnapshot") or {})
            if row is None:
                out[nid] = {"state": "missing", "kind": "claim", "claimId": ref}
                continue
            current_status = str(row.status)
            bound_status = str(snap.get("status") or "")
            if bound_status and bound_status != current_status:
                out[nid] = {
                    "state": "stale", "kind": "claim", "claimId": ref,
                    "boundStatus": bound_status, "currentStatus": current_status,
                }
            else:
                out[nid] = {"state": "ok", "kind": "claim", "claimId": ref,
                            "currentStatus": current_status}
    return out


# --------------------------------------------------------------------------
# 写：人工编辑 / AI patch / undo / redo
# --------------------------------------------------------------------------


def _conflict_payload(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    current_row,
    incoming_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    from ..domain.revision_diff import diff_content

    current_blocks = list((current_row.content or {}).get("blocks") or [])
    current_stored = validate_blocks(current_blocks, stored=True) if current_blocks else []
    incoming = validate_blocks(incoming_blocks, stored=True) if incoming_blocks else []
    return {
        "objectRef": "paper_spec",
        "current": (
            {
                "revision": int(current_row.revision),
                "objectId": str(current_row.id),
                "contentHash": current_row.content_hash,
                "content": {"blocks": current_stored},
            }
            if current_row is not None
            else None
        ),
        "incoming": {"revision": None, "content": {"blocks": incoming}},
        "diff": diff_content(
            {"blocks": current_stored}, {"blocks": incoming}
        ).as_dict(),
        "nodeDiff": diff_nodes(current_stored, incoming),
        "resolvable": True,
    }


def manual_edit(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
    base_revision: int,
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """人工编辑：base_revision 必须等于当前修订，否则 409 + nodeDiff。

    人工编辑回显的是**当前文档**（客户端会带着服务端回填的快照字段提交），
    因此按 stored 模式校验；所有快照随后由 `enrich_blocks` **重算覆盖** ——
    客户端带的快照永远不会被信任，只是不会被拒绝。
    """
    row = latest_paper_row(db, session_id)
    current_rev = int(row.revision or 0) if row is not None else 0
    if int(base_revision) != current_rev:
        raise RevisionConflict(
            int(base_revision),
            current_rev,
            "",
            conflict=_conflict_payload(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                current_row=row,
                incoming_blocks=blocks,
            ),
        )
    validated = validate_blocks(blocks, stored=True)
    enriched = enrich_blocks(
        db, session_id=session_id, tenant_id=tenant_id, blocks=validated, is_ai=False
    )
    result = _append_revision(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        trace_id=trace_id,
        content={"blocks": enriched},
        action=ACTION_MANUAL,
        detail={"baseRevision": int(base_revision)},
    )
    _emit_paper_revised(
        db, session_id=session_id, revision=result["revision"], action=ACTION_MANUAL,
        actor_id=actor_id, trace_id=trace_id,
    )
    return result


def apply_confirmed_patch(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """人工确认后的 AI patch 落库（由 `proposal_handlers.apply` 调用）。

    **确认时复检**：用当前事实（证据/claim/引用/锚点都可能已变）重新过四道门。
    """
    from ..domain.proposal_handlers import ProposalRejected

    ops = [o for o in (content.get("ops") or []) if isinstance(o, dict)]
    row = latest_paper_row(db, session_id)
    current_rev = int(row.revision or 0) if row is not None else 0
    base_revision = int(content.get("baseRevision") or 0)
    current_blocks = (
        validate_blocks(list((row.content or {}).get("blocks") or []), stored=True)
        if row is not None
        else []
    )

    if ops:
        if base_revision != current_rev:
            raise ProposalRejected(
                "PAPER_BASE_REVISION_STALE",
                f"patch 基于 revision {base_revision}，当前 {current_rev}：请重新生成提案",
            )
        r = paper_resolvers(db, session_id=session_id, tenant_id=tenant_id)
        for o in ops:
            o.pop("_enrich", None)  # 提案期快照一律作废，确认时重算
        try:
            validated = validate_ops(
                ops,
                current_blocks=current_blocks,
                resolvable_evidence=r["resolvable_evidence"],
                resolvable_claims=r["resolvable_claims"],
                claim_status=r["claim_status"],
                resolvable_citations=r["resolvable_citations"],
                resolvable_anchors=r["resolvable_anchors"],
                citation_hashes=r["citation_hashes"],
                anchor_details=r["anchor_details"],
                is_ai=True,
            )
            new_blocks = apply_ops(current_blocks, validated)
        except PaperSchemaError as e:
            raise ProposalRejected(e.code, e.message) from e
        result = _append_revision(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            trace_id=trace_id,
            content={
                "blocks": new_blocks,
                "baseRevision": base_revision,
                "rationale": str(content.get("rationale") or ""),
            },
            action=ACTION_AI_PATCH,
            detail={"opCount": len(validated), "rationale": str(content.get("rationale") or "")},
        )
        _emit_paper_revised(
            db, session_id=session_id, revision=result["revision"], action=ACTION_AI_PATCH,
            actor_id=actor_id, trace_id=trace_id,
        )
        return result

    # legacy（RV12 时期）blocks 形状：整树替换
    blocks = [b for b in (content.get("blocks") or []) if isinstance(b, dict)]
    validated = validate_blocks(blocks)
    enriched = enrich_blocks(
        db, session_id=session_id, tenant_id=tenant_id, blocks=validated, is_ai=False
    )
    result = _append_revision(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        trace_id=trace_id,
        content={"blocks": enriched},
        action=ACTION_AI_PATCH,
        detail={"legacyBlocks": True},
    )
    return result


def undo(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
) -> dict[str, Any]:
    """撤销：新建一个修订，内容等于**上一版**（线性回退，append-only）。"""
    from ..db.models import PaperSpecRow

    rows = (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id)
        .order_by(PaperSpecRow.revision.desc())
        .limit(2)
        .all()
    )
    if len(rows) < 2:
        raise PaperStateError("PAPER_NOTHING_TO_UNDO", "没有可撤销的修订")
    latest, prev = rows[0], rows[1]
    result = _append_revision(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        trace_id=trace_id,
        content=copy.deepcopy(dict(prev.content or {})),
        action=ACTION_UNDO,
        detail={"undoneRevision": int(latest.revision), "restoredRevision": int(prev.revision)},
    )
    _emit_paper_revised(
        db, session_id=session_id, revision=result["revision"], action=ACTION_UNDO,
        actor_id=actor_id, trace_id=trace_id,
    )
    return result


def redo(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
) -> dict[str, Any]:
    """恢复：仅当最后一步审计是 UNDO 时，恢复被那一刀撤掉的修订内容。"""
    from ..db.models import PaperEditAuditRow, PaperSpecRow

    last_audit = (
        db.query(PaperEditAuditRow)
        .filter(PaperEditAuditRow.session_id == session_id)
        .order_by(PaperEditAuditRow.paper_revision.desc())
        .first()
    )
    if last_audit is None or last_audit.action != ACTION_UNDO:
        raise PaperStateError("PAPER_NOTHING_TO_REDO", "没有可恢复的修订（先撤销才能恢复）")
    undone_rev = int((last_audit.detail or {}).get("undoneRevision") or 0)
    target = (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id, PaperSpecRow.revision == undone_rev)
        .first()
    )
    if target is None:
        raise PaperStateError("PAPER_NOTHING_TO_REDO", f"被撤销的修订 {undone_rev} 不存在")
    result = _append_revision(
        db,
        session_id=session_id,
        tenant_id=tenant_id,
        actor_id=actor_id,
        trace_id=trace_id,
        content=copy.deepcopy(dict(target.content or {})),
        action=ACTION_REDO,
        detail={"ofRevision": undone_rev},
    )
    _emit_paper_revised(
        db, session_id=session_id, revision=result["revision"], action=ACTION_REDO,
        actor_id=actor_id, trace_id=trace_id,
    )
    return result


def propose_patch(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
    content: dict[str, Any],
) -> str:
    """AI（或人）提出一个 paper patch 提案 → PendingAction（**不**写正式状态）。

    与消息路径共用同一组校验器（`proposal_handlers.VALIDATORS`）——
    HTTP 直提只是**入口不同**，四道门一道不少。
    返回 pending action id。
    """
    from datetime import timedelta

    from ..db.models import PendingActionAuditRow, PendingActionRow
    from ..domain.proposal_handlers import VALIDATORS

    validator = VALIDATORS["paper_patch"][1]
    validated = validator(content, session_id=session_id, db=db)  # ProposalRejected 直接上抛
    content_hash = _canonical_hash(validated)
    now = datetime.now(timezone.utc)
    pa = PendingActionRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        action_type="confirm_paper_spec",
        payload={
            "proposal_kind": "paper_patch",
            "object_ref": "paper_spec",
            "content": validated,
            "content_hash": content_hash,
            "assistant_text": str(content.get("assistantText") or ""),
        },
        status="PENDING",
        revision=_session_revision(db, session_id),
        required_capability="research.session.write",
        object_ref="paper_spec",
        source_message_id=None,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(pa)
    db.flush()
    db.add(
        PendingActionAuditRow(
            pending_action_id=pa.id,
            action="CREATED",
            actor_id=actor_id,
            trace_id=trace_id,
            at=now,
            detail={"object_ref": "paper_spec", "kind": "paper_patch", "via": "paper:propose"},
        )
    )
    return str(pa.id)


def _session_revision(db: Session, session_id: uuid.UUID) -> int:
    from ..db.models import ResearchSessionRow

    row = db.get(ResearchSessionRow, session_id)
    return int(row.revision or 0) if row is not None else 0


def revision_history(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    from ..db.models import PaperEditAuditRow, PaperSpecRow

    rows = (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id)
        .order_by(PaperSpecRow.revision)
        .all()
    )
    audits = {
        int(a.paper_revision): a
        for a in db.query(PaperEditAuditRow)
        .filter(PaperEditAuditRow.session_id == session_id)
        .all()
    }
    out: list[dict[str, Any]] = []
    for r in rows:
        a = audits.get(int(r.revision))
        out.append(
            {
                "revision": int(r.revision),
                "action": a.action if a is not None else ACTION_AI_PATCH,
                "contentHash": r.content_hash,
                "at": (a.created_at if a is not None else r.created_at).isoformat()
                if (a is not None or r.created_at is not None)
                else "",
                "detail": dict(a.detail or {}) if a is not None else {},
            }
        )
    return out


def validate_only(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """纯校验（round-trip 报告）：不落库。"""
    issues: list[dict[str, Any]] = []
    ok = True
    try:
        validated = validate_blocks(list(blocks or []))
    except PaperSchemaError as e:
        return {"ok": False, "issues": [{"code": e.code, "message": e.message}]}
    from .formula import FormulaError, roundtrip

    for node in validated:
        if node.get("type") == "equation":
            rt = roundtrip(str(node.get("latex") or ""))
            if not rt["ok"]:
                ok = False
                issues.append(
                    {"code": "PAPER_FORMULA_UNPARSABLE", "nodeId": node["id"],
                     "message": rt["error"]}
                )
    return {"ok": ok and not issues, "issues": issues}
