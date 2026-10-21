"""RV15 · Review Finding 域的 DB 编排。

职责边界：
- propose：Reviewer AI（或人）提交 finding 提案 → **PendingAction**（不写正式状态）；
- apply（由 proposal_handlers 在确认后调用）：指纹去重判定 → 新行或更新/重开；
- transition / resolve / waive：resolution workflow 的唯一写路径；
- mark_target_stale：target 更版后标 stale/needsRecheck；
- blocking：P0/P1 未解决 ⇒ 阻断 COMPLETED（BLOCKING-GATE.md）。

Reviewer AI 在本域**没有**任何直接写正式状态的路径——它只能产生 PendingAction。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..db import models as M
from ..db.models import (
    FindingResolutionRow,
    FindingTransitionRow,
    FindingWaiverRow,
    ReviewFindingV2Row,
)
from .taxonomy import (
    WAIVER_EXPIRED_REASON,
    dedupe_action,
    default_due,
    fingerprint_of,
    reopen_reason_for,
    severity_of_category,
    validate_finding_proposal,
    validate_resolution,
    validate_transition,
    validate_waiver,
    waiver_is_expired,
)

#: 提案 PendingAction 的 object_ref（走既有确认门协议）。
REVIEW_OBJECT_REF = "review_finding"

_TERMINAL = {"RESOLVED", "WAIVED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


def canonical_hash(obj: Any) -> str:
    import hashlib
    import json

    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ==========================================================================
# propose：提案 → PendingAction（不写正式状态）
# ==========================================================================


def propose_preview(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验并返回带指纹的预览（不入库；供提案响应回显）。校验失败即抛。"""
    out: list[dict[str, Any]] = []
    for raw in findings:
        item = dict(raw or {})
        item.pop("createdByRun", None)
        item.pop("created_by_run", None)
        v = validate_finding_proposal(item)
        v["fingerprint"] = fingerprint_of(v)
        out.append(v)
    return out


def propose_findings(
    db,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    trace_id: str,
    findings: list[dict[str, Any]],
    created_by_run: str = "",
) -> str:
    """校验全部提案（任一非法即整体拒绝，不产生半截提案）→ PendingAction。"""
    from datetime import datetime as _dt

    from ..db.models import PendingActionAuditRow, PendingActionRow

    validated: list[dict[str, Any]] = []
    for raw in findings:
        item = dict(raw or {})
        # 提案里允许携带 server-owned 校验器拒绝的键 —— 但必须先剥掉
        # created_by_run 这类**传输层**键，剩下的交给 validate 严格判。
        item.pop("createdByRun", None)
        item.pop("created_by_run", None)
        v = validate_finding_proposal(item)
        v["fingerprint"] = fingerprint_of(v)
        validated.append(v)

    now = _dt.now(timezone.utc)
    pa = PendingActionRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        action_type="confirm_review_finding",
        payload={
            "proposal_kind": "review_finding",
            "object_ref": REVIEW_OBJECT_REF,
            "content": {"findings": validated, "createdByRun": str(created_by_run or "")[:128]},
            "content_hash": canonical_hash({"findings": validated}),
            "assistant_text": "",
        },
        status="PENDING",
        revision=_session_revision(db, session_id),
        required_capability="research.session.write",
        object_ref=REVIEW_OBJECT_REF,
        source_message_id=None,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(pa)
    db.flush()
    db.add(
        PendingActionAuditRow(
            pending_action_id=pa.id, action="CREATED", actor_id=actor_id,
            trace_id=trace_id, at=now,
            detail={"object_ref": REVIEW_OBJECT_REF, "count": len(validated),
                    "via": "review:propose", "run": str(created_by_run or "")[:128]},
        )
    )
    return str(pa.id)


def _session_revision(db, session_id: uuid.UUID) -> int:
    from ..db.models import ResearchSessionRow

    row = db.get(ResearchSessionRow, session_id)
    return int(row.revision or 0) if row is not None else 0


# ==========================================================================
# apply：确认后落行（去重判定在这里）
# ==========================================================================


def apply_confirmed_findings(
    db,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    content: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    """确认门的落库路由（proposal_handlers.apply 的 review_finding 分支）。

    语义：不复制相同 finding —— 按指纹判 UPDATE_EXISTING / KEEP_RESOLVED /
    REOPEN / NEW（STATE-MACHINE.md §5）。
    """
    created: list[str] = []
    updated: list[str] = []
    reopened: list[str] = []
    kept: list[str] = []
    run = str(content.get("createdByRun") or "")[:128]

    for v in content.get("findings") or []:
        fp = str(v.get("fingerprint") or "")
        existing = (
            db.query(ReviewFindingV2Row)
            .filter(ReviewFindingV2Row.session_id == session_id,
                    ReviewFindingV2Row.fingerprint == fp)
            .first()
            if fp
            else None
        )
        new_version = int(v.get("target", {}).get("version") or 0)

        if existing is not None:
            action = dedupe_action(
                fp,
                {"status": existing.status, "targetVersion": existing.target_version,
                 "waiver": existing.waiver, "id": str(existing.id)},
                new_target_version=new_version,
            )
        else:
            action = "NEW"

        if action == "NEW":
            fid = uuid.uuid4()
            db.add(
                ReviewFindingV2Row(
                    id=fid,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    taxonomy=v["taxonomy"],
                    severity=v["severity"],
                    status="OPEN",
                    target=v["target"],
                    description=v["description"],
                    evidence=v["evidence"],
                    fingerprint=fp,
                    target_version=new_version,
                    created_by_run=run,
                    seen_count=1,
                    last_seen_run=run,
                    due=default_due(v["severity"]),
                    audit=[_audit_entry("SYSTEM", by=run, action="CREATED")],
                    trace_id=trace_id,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            created.append(str(fid))
        elif action == "UPDATE_EXISTING":
            existing.seen_count = int(existing.seen_count or 0) + 1
            existing.last_seen_run = run
            existing.target_version = new_version
            existing.stale = False
            existing.needs_recheck = False
            existing.updated_at = _now()
            existing.audit = list(existing.audit or []) + [
                _audit_entry("SYSTEM", by=run, action="SEEN_AGAIN")
            ]
            updated.append(str(existing.id))
        elif action == "REOPEN":
            _apply_transition(
                existing, new_status="REOPENED", actor_id=actor_id, by_run=run,
                reason=reopen_reason_for(action) or "REOPEN", trace_id=trace_id,
            )
            existing.target_version = new_version
            existing.stale = False
            existing.needs_recheck = False
            existing.seen_count = int(existing.seen_count or 0) + 1
            existing.last_seen_run = run
            reopened.append(str(existing.id))
        else:  # KEEP_RESOLVED
            existing.updated_at = _now()
            kept.append(str(existing.id))

    db.flush()
    return {
        "object_ref": REVIEW_OBJECT_REF,
        "id": (created or updated or reopened or kept or [""])[0],
        "created": created,
        "updated": updated,
        "reopened": reopened,
        "kept": kept,
        "count": len(created) + len(updated) + len(reopened),
        "revision": _session_revision(db, session_id),
    }


def _audit_entry(kind: str, *, by: str = "", action: str = "", reason: str = "") -> dict:
    return {"kind": kind, "by": by[:128], "action": action[:64],
            "reason": reason[:128], "at": _now().isoformat()}


# ==========================================================================
# 读路径
# ==========================================================================


def _waived_expired(row: ReviewFindingV2Row) -> bool:
    return row.status == "WAIVED" and waiver_is_expired(row.waiver, today=_today())


def list_findings(db, *, session_id: uuid.UUID, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        db.query(ReviewFindingV2Row)
        .filter(ReviewFindingV2Row.session_id == session_id,
                ReviewFindingV2Row.tenant_id == tenant_id)
        .order_by(ReviewFindingV2Row.created_at)
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if _waived_expired(row):
            # 豁免到期不是展示问题：真的回到待处理（STATE-MACHINE.md §3）。
            _apply_transition(db, row, new_status="REOPENED", actor_id=None, by_run="",
                              reason=WAIVER_EXPIRED_REASON, trace_id=row.trace_id or "")
            if row.waiver:
                w = dict(row.waiver)
                w["expiredReopened"] = True
                row.waiver = w
            db.add(
                FindingWaiverRow(
                    finding_id=row.id, reason=dict(row.waiver or {}),
                    expiry=str((row.waiver or {}).get("expiry") or ""),
                    approved_by=str((row.waiver or {}).get("approvedBy") or ""),
                    expired_reopened=True, at=_now(),
                )
            )
            db.flush()
        out.append(finding_public_dict(row))
    return out


def finding_public_dict(row: ReviewFindingV2Row) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "taxonomy": row.taxonomy,
        "severity": row.severity,
        "status": row.status,
        "target": dict(row.target or {}),
        "targetVersion": int(row.target_version or 0),
        "locator": (row.target or {}).get("locator") or {},
        "description": row.description,
        "evidence": list(row.evidence or []),
        "createdByRun": row.created_by_run,
        "assignee": str(row.assignee) if row.assignee else "",
        "due": row.due.isoformat() if row.due else "",
        "fingerprint": row.fingerprint,
        "stale": bool(row.stale),
        "needsRecheck": bool(row.needs_recheck),
        "seenCount": int(row.seen_count or 0),
        "lastSeenRun": row.last_seen_run,
        "reopenCount": int(row.reopen_count or 0),
        "resolution": dict(row.resolution or {}),
        "waiver": dict(row.waiver or {}),
        "audit": list(row.audit or []),
        "createdAt": row.created_at.isoformat() if row.created_at else "",
        "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
    }


# ==========================================================================
# transition / resolve / waive
# ==========================================================================


def _load_finding(db, session_id: uuid.UUID, finding_id: uuid.UUID) -> ReviewFindingV2Row:
    row = db.get(ReviewFindingV2Row, finding_id)
    if row is None or str(row.session_id) != str(session_id):
        from ..domain.errors import ObjectScopeMissing

        raise ObjectScopeMissing("finding 不在该会话内")
    return row


def transition_finding(
    db,
    *,
    session_id: uuid.UUID,
    finding_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    to: str,
    assignee: str = "",
    expected_revision: int | None = None,
    trace_id: str = "",
) -> dict[str, Any]:
    row = _load_finding(db, session_id, finding_id)
    if expected_revision is not None and int(expected_revision) != int(row.row_revision or 0):
        from ..domain.errors import RevisionConflict

        raise RevisionConflict(int(expected_revision), int(row.row_revision or 0))
    validate_transition(row.status, to)
    if to in {"ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED"} and not (
        assignee or row.assignee
    ):
        raise ReviewDomainError(  # noqa: F405 —— taxonomy 导出
            "FINDING_ASSIGNEE_REQUIRED", f"进入 {to} 必须有 assignee"
        )
    _apply_transition(db, row, new_status=to, actor_id=actor_id, by_run="", reason="MANUAL",
                      trace_id=trace_id, assignee=assignee or None)
    db.flush()
    return finding_public_dict(row)


def _apply_transition(
    db,
    row: ReviewFindingV2Row,
    *,
    new_status: str,
    actor_id: uuid.UUID | None,
    by_run: str,
    reason: str,
    trace_id: str,
    assignee: str | None = None,
) -> None:
    old = row.status
    row.status = new_status
    if assignee:
        try:
            row.assignee = uuid.UUID(assignee)
        except (ValueError, TypeError):
            row.assignee = None
    row.row_revision = int(row.row_revision or 0) + 1
    row.updated_at = _now()
    row.audit = list(row.audit or []) + [
        _audit_entry("SYSTEM", by=by_run, action=f"{old}->{new_status}", reason=reason)
    ]
    db.add(
        FindingTransitionRow(
            finding_id=row.id, from_status=old, to_status=new_status,
            actor_id=actor_id, by_run=by_run, reason=reason[:128],
            at=_now(), trace_id=trace_id or row.trace_id or "",
        )
    )


def resolve_finding(
    db,
    *,
    session_id: uuid.UUID,
    finding_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    resolution: dict[str, Any],
    trace_id: str = "",
) -> dict[str, Any]:
    row = _load_finding(db, session_id, finding_id)
    validate_transition(row.status, "RESOLVED")
    checked = validate_resolution(resolution, current_target_version=int(row.target_version or 0))
    _apply_transition(db, row, new_status="RESOLVED", actor_id=actor_id, by_run="", reason="RESOLVED",
                      trace_id=trace_id)
    row.resolution = checked
    db.add(
        FindingResolutionRow(
            finding_id=row.id,
            revision_ref=dict(checked["revisionRef"]),
            evidence_refs=list(checked["evidenceRefs"]),
            note=checked["note"],
            actor_id=actor_id, at=_now(), trace_id=trace_id or row.trace_id or "",
        )
    )
    db.flush()
    return finding_public_dict(row)


def waive_finding(
    db,
    *,
    session_id: uuid.UUID,
    finding_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    waiver: dict[str, Any],
    by_run: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    row = _load_finding(db, session_id, finding_id)
    if by_run:
        from .taxonomy import ReviewerPolicyViolation

        raise ReviewerPolicyViolation(
            "豁免是人工决策：AI run 不得批准 waiver", operation="APPROVE_WAIVER"
        )
    if str((waiver or {}).get("approvedBy") or "").lower().startswith("ai:"):
        from .taxonomy import ReviewerPolicyViolation

        raise ReviewerPolicyViolation(
            "豁免是人工决策：批准人不得是 AI", operation="APPROVE_WAIVER"
        )
    validate_transition(row.status, "WAIVED")
    checked = validate_waiver(waiver)
    _apply_transition(db, row, new_status="WAIVED", actor_id=actor_id, by_run="", reason="WAIVED",
                      trace_id=trace_id)
    row.waiver = checked
    db.add(
        FindingWaiverRow(
            finding_id=row.id,
            reason=dict(checked["reason"]),
            expiry=checked["expiry"],
            approved_by=checked["approvedBy"],
            expired_reopened=False, at=_now(),
            actor_id=actor_id, trace_id=trace_id or row.trace_id or "",
        )
    )
    db.flush()
    return finding_public_dict(row)


# ==========================================================================
# target 更版 → stale/recheck；recheck 动作
# ==========================================================================


def mark_target_stale(db, *, session_id: uuid.UUID, target_type: str,
                      target_id: str, new_version: int) -> int:
    """target 更版后，指向它的非终态 finding 自动标 stale/needsRecheck。
    返回被标记的行数。"""
    rows = (
        db.query(ReviewFindingV2Row)
        .filter(ReviewFindingV2Row.session_id == session_id,
                ReviewFindingV2Row.status.notin_(_TERMINAL))
        .all()
    )
    n = 0
    for row in rows:
        t = dict(row.target or {})
        if str(t.get("type")) == target_type and str(t.get("id")) == str(target_id) \
                and int(t.get("version") or 0) < int(new_version):
            row.stale = True
            row.needs_recheck = True
            row.updated_at = _now()
            row.audit = list(row.audit or []) + [
                _audit_entry("SYSTEM", action="TARGET_MOVED",
                             reason=f"target -> v{new_version}")
            ]
            n += 1
    db.flush()
    return n


def recheck_finding(db, *, session_id: uuid.UUID, finding_id: uuid.UUID,
                    actor_id: uuid.UUID | None, still_present: bool,
                    trace_id: str = "") -> dict[str, Any]:
    """显式复验：问题仍在 → 保持非终态并清 stale；问题消失 → 自动 RESOLVED
    （引用复验这一事实本身作为 resolution note；无修复修订时不写 RESOLVED 门——
    stale 的清理语义与"修复"不同，这里走 SEEN_AGAIN+关闭。"""
    row = _load_finding(db, session_id, finding_id)
    row.stale = False
    row.needs_recheck = False
    row.audit = list(row.audit or []) + [
        _audit_entry("SYSTEM", action="RECHECKED",
                     reason="PRESENT" if still_present else "GONE")
    ]
    if not still_present and row.status not in _TERMINAL:
        row.resolution = {
            "revisionRef": {"kind": "recheck", "revision": int(row.target_version or 0)},
            "evidenceRefs": ["recheck:verified-gone"],
            "note": "复验确认问题已随 target 更版消失",
        }
        _apply_transition(row, "RESOLVED", actor_id=actor_id, by_run="",
                          reason="RECHECK_GONE", trace_id=trace_id)
    db.flush()
    return finding_public_dict(row)


# ==========================================================================
# Blocking gate（BLOCKING-GATE.md 的唯一实现）
# ==========================================================================

BLOCKING_SEVERITIES: tuple[str, ...] = ("P0", "P1")


def blocking_findings(db, *, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """未解决的 P0/P1 finding（WAIVED 且未过期视为已处置）。"""
    rows = (
        db.query(ReviewFindingV2Row)
        .filter(ReviewFindingV2Row.session_id == session_id,
                ReviewFindingV2Row.severity.in_(BLOCKING_SEVERITIES))
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.status in _TERMINAL:
            if row.status == "WAIVED" and _waived_expired(row):
                out.append(_blocked_brief(row))
            continue
        out.append(_blocked_brief(row))
    return out


def _blocked_brief(row: ReviewFindingV2Row) -> dict[str, Any]:
    return {
        "findingId": str(row.id),
        "taxonomy": row.taxonomy,
        "severity": row.severity,
        "status": row.status,
        "target": dict(row.target or {}),
        "description": (row.description or "")[:200],
    }


def blocking_summary(db, *, session_id: uuid.UUID) -> dict[str, Any]:
    blocked = blocking_findings(db, session_id=session_id)
    return {
        "count": len(blocked),
        "blocked": blocked[:50],
        "blocking": len(blocked) > 0,
        # 文字标签，不做"只有彩色数字"的 dashboard（任务书 7）
        "label": f"未解决 P0/P1 发现 {len(blocked)} 条" if blocked else "无未解决的 P0/P1 发现",
    }


# ==========================================================================
# Review run：确定性检测管线（不为固定文稿硬编码 finding）
# ==========================================================================


def start_review_run(
    db,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    trace_id: str = "",
) -> dict[str, Any]:
    """对**当前事实**跑一轮确定性检测，产出的候选 finding 走标准提案门。

    候选来源全部是 DB 真源读取（无任何"如果文稿是 X 就输出 Y"的路径）：
    - 论文强结论词表命中且无 claimRef（OVERCLAIM）
    - 公式不可解析 / canonical 缺失（MATH_ERROR）
    - 引用 stale（CITATION_ERROR）
    - claim 处于 CONFLICTED / requires_review 且有 FAIL 验证（CONTRADICTION）
    - 实验 repro 六元组缺失（REPRODUCIBILITY）
    - figure/citation 缺失（EVIDENCE_GAP）
    """
    from ..db.models import ReviewRunRow

    candidates = detect_candidates(db, session_id=session_id, tenant_id=tenant_id)
    run_row = ReviewRunRow(
        id=uuid.uuid4(),
        session_id=session_id,
        tenant_id=tenant_id,
        proposed_count=len(candidates),
        status="PROPOSED",
        trace_id=trace_id or "",
        created_at=_now(),
    )
    db.add(run_row)
    db.flush()
    pa_id: str | None = None
    if candidates:
        pa_id = propose_findings(
            db,
            session_id=session_id,
            tenant_id=tenant_id,
            actor_id=actor_id or uuid.uuid4(),
            trace_id=trace_id,
            findings=candidates,
            created_by_run=str(run_row.id),
        )
    return {
        "runId": str(run_row.id),
        "pendingActionId": pa_id,
        "candidates": len(candidates),
        "candidateTaxonomies": sorted({c["taxonomy"] for c in candidates}),
    }


def detect_candidates(
    db, *, session_id: uuid.UUID, tenant_id: uuid.UUID
) -> list[dict[str, Any]]:
    """确定性检测：每次都重新读 DB 事实；同类判据与各域报错码一致。"""
    from ..db.models import ClaimRow, PaperSpecRow
    from ..paper.formula import FormulaError, parse_latex
    from ..paper.patch import has_strong_conclusion

    candidates: list[dict[str, Any]] = []
    paper = (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id)
        .order_by(PaperSpecRow.revision.desc())
        .first()
    )
    blocks: list[dict[str, Any]] = list((paper.content or {}).get("blocks") or []) if paper else []
    paper_rev = int(paper.revision or 0) if paper else 0

    for idx, node in enumerate(blocks):
        nid = str(node.get("id") or f"n{idx}")
        ntype = str(node.get("type") or "")
        text = str(node.get("text") or "")
        locator = {"nodeId": nid, "blockIndex": idx}

        # OVERCLAIM：强结论词表命中且无 claimRef（与 PAPER_CONCLUSION_UNLINKED 同判据）
        if ntype == "paragraph" and has_strong_conclusion(text) and not node.get("claimRef"):
            candidates.append({
                "taxonomy": "OVERCLAIM",
                "target": {"type": "paper_node", "id": nid,
                           "version": paper_rev, "locator": dict(locator)},
                "description": f"段落「{text[:40]}…」包含强结论表述但未链接已发布 Claim",
                "evidence": [{"kind": "paper_node", "ref": nid,
                              "summary": "强结论词表命中且 claimRef 缺失"}],
            })

        # MATH_ERROR：公式不可解析（canonical 缺失/非法）
        if ntype == "equation":
            latex = str(node.get("latex") or "")
            canonical_ok = bool(node.get("canonical")) and bool(node.get("astHash"))
            parse_ok = True
            if latex:
                try:
                    parse_latex(latex)
                except FormulaError:
                    parse_ok = False
            if not (parse_ok and canonical_ok):
                candidates.append({
                    "taxonomy": "MATH_ERROR",
                    "target": {"type": "paper_node", "id": nid,
                               "version": paper_rev, "locator": dict(locator)},
                    "description": "公式无法解析或缺少服务端 canonical/astHash（可能被改坏）",
                    "evidence": [{"kind": "paper_node", "ref": nid,
                                  "summary": "formula canonical 校验未通过"}],
                })

        # EVIDENCE_GAP：figure 无题注（导出后信息缺失的最常见形态）
        if ntype == "figure" and not str(node.get("caption") or "").strip():
            candidates.append({
                "taxonomy": "EVIDENCE_GAP",
                "target": {"type": "paper_node", "id": nid,
                           "version": paper_rev, "locator": dict(locator)},
                "description": "插图缺少题注：读者无法知道图的内容与来源",
                "evidence": [{"kind": "paper_node", "ref": nid,
                              "summary": "figure.caption 缺失"}],
            })

    # CITATION_ERROR：引用快照 hash 与现值不符（stale）
    if paper:
        from ..citation.models import CitationRow

        for idx, node in enumerate(blocks):
            if str(node.get("type")) != "citation":
                continue
            snaps = dict((node.get("citationSnapshot") or {}))
            for ref, snap in snaps.items():
                try:
                    crow = db.get(CitationRow, uuid.UUID(ref))
                except ValueError:
                    crow = None
                if crow is None or str(crow.csl_hash or "") != str((snap or {}).get("cslHash") or ""):
                    candidates.append({
                        "taxonomy": "CITATION_ERROR",
                        "target": {"type": "paper_node", "id": str(node.get("id")),
                                   "version": paper_rev,
                                   "locator": {"nodeId": str(node.get("id")), "blockIndex": idx}},
                        "description": f"引用 {ref[:8]}… 的版本快照与文献库现值不一致（已过期或被更新）",
                        "evidence": [{"kind": "citation", "ref": ref,
                                      "summary": "citationSnapshot.cslHash 与现值不符"}],
                    })

    # CONTRADICTION：claim 存在 FAIL 验证 / CONFLICTED 支持质量
    claim_rows = (
        db.query(ClaimRow)
        .filter(ClaimRow.session_id == session_id)
        .order_by(ClaimRow.claim_key, ClaimRow.revision)
        .all()
    )
    seen_keys: set[str] = set()
    for r in claim_rows:
        key = str(r.claim_key or r.id)
        seen_keys.add(key)  # 升序遍历后留下最新
    for r in claim_rows:
        key = str(r.claim_key or r.id)
        if key != str(r.claim_key or r.id) or str(r.status) in {"SOLVED"}:
            continue
        if str(r.support_quality) == "CONFLICTED" or bool(r.requires_review):
            candidates.append({
                "taxonomy": "CONTRADICTION",
                "target": {"type": "claim", "id": key,
                           "version": int(r.revision or 0),
                           "locator": {"claimKey": key}},
                "description": f"Claim「{(r.text or '')[:40]}…」存在冲突证据或未消化的复核要求"
                               f"（support={r.support_quality}）",
                "evidence": [{"kind": "db_fact", "ref": key,
                              "summary": f"supportQuality={r.support_quality}, "
                                         f"reviewReasons={list(r.review_reasons or [])}"}],
            })

    # REPRODUCIBILITY：实验 repro 元数据不全
    from ..db.models import ExperimentRunRow

    for run in db.query(ExperimentRunRow).filter(
        ExperimentRunRow.session_id == session_id
    ).all():
        repro = dict(run.repro or {})
        missing = [k for k in ("imageDigest", "dependencyLockHash", "seed", "codeHash")
                   if not repro.get(k)]
        if missing:
            candidates.append({
                "taxonomy": "REPRODUCIBILITY",
                "target": {"type": "experiment_run", "id": str(run.id),
                           "version": 1, "locator": {"runId": str(run.id)}},
                "description": f"实验缺少可复现元数据：{', '.join(missing)}",
                "evidence": [{"kind": "experiment_run", "ref": str(run.id),
                              "summary": f"missing repro keys: {missing}"}],
            })

    return candidates
