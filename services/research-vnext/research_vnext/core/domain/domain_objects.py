"""RV09–RV13 领域对象的投影与 proposal 应用（本文件在 PHASE 3 逐步长齐）。

当前状态（诚实标注）：
- `evidence_cards` / `build_artifact_focus` 已实现为**真实投影**，但只有
  `claim` / `routes` / `blueprint` 三类有数据源；其余对象族仍在 PHASE 3 落地。
- 未落地的对象族在 `artifactFocus.objects` 下**不出现**（而不是给一个空壳假装存在）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from .snapshot import VALID_CAPABILITY_STATES  # noqa: F401  (re-export for callers)


def evidence_cards(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """本会话的 EvidenceCard 投影。

    证据本体（原文/切片/资产版本）属平台资产层；本服务只持有**引用与授权位**。
    没有证据时返回 `[]`。
    """
    from ..db.models import EvidenceCardRow  # 延迟 import

    rows = (
        db.query(EvidenceCardRow)
        .filter(EvidenceCardRow.session_id == session_id)
        .order_by(EvidenceCardRow.created_at)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "source": r.source or "",
            "label": r.label or "",
            "locator": r.locator or "",
            "snippet": r.snippet or "",
            "assetVersion": r.asset_version or "",
            "authorized": bool(r.authorized),
        }
        for r in rows
    ]


def build_artifact_focus(
    db: Session, session_id: uuid.UUID, revision: int
) -> tuple[dict[str, Any] | None, bool]:
    """`artifactFocus`（ADR-RV-003 §3.4）+ 是否"存在可用产物"。

    RV09 起把 **ProblemMap / ResearchMap** 也算作产物：它们是这条研究链
    最早的正式产物，若不算，用户在"已确认问题地图"之后仍然会看到
    "尚无已确认的研究产物"，从而打不开地图画布。
    """
    from ..db.models import (  # 延迟 import
        ClaimRow,
        ModelSpecRow,
        ProblemMapRow,
        ResearchBlueprintRow,
        ResearchMapRow,
        RouteArenaRow,
    )

    claims = (
        db.query(ClaimRow)
        .filter(ClaimRow.session_id == session_id)
        .order_by(ClaimRow.created_at)
        .all()
    )
    routes = (
        db.query(ModelSpecRow)
        .filter(ModelSpecRow.session_id == session_id)
        .order_by(ModelSpecRow.created_at)
        .all()
    )
    blueprints = (
        db.query(ResearchBlueprintRow)
        .filter(ResearchBlueprintRow.session_id == session_id)
        .order_by(ResearchBlueprintRow.revision)
        .all()
    )
    arenas = (
        db.query(RouteArenaRow)
        .filter(RouteArenaRow.session_id == session_id)
        .order_by(RouteArenaRow.revision)
        .all()
    )
    problem_maps = (
        db.query(ProblemMapRow)
        .filter(ProblemMapRow.session_id == session_id)
        .order_by(ProblemMapRow.revision)
        .all()
    )
    research_maps = (
        db.query(ResearchMapRow)
        .filter(ResearchMapRow.session_id == session_id)
        .order_by(ResearchMapRow.revision)
        .all()
    )

    if not claims and not routes and not blueprints and not problem_maps and not research_maps and not arenas:
        return None, False

    latest_bp = blueprints[-1] if blueprints else None
    content = (latest_bp.content or {}) if latest_bp else {}
    latest_pm = problem_maps[-1] if problem_maps else None
    latest_rm = research_maps[-1] if research_maps else None
    latest_arena = arenas[-1] if arenas else None

    # 聚焦键的优先级：下游离交付物越近的越优先（claims > blueprint > arena > map）。
    # 地图只在**没有**更下游产物时成为默认焦点 —— 否则用户确认了 claim 之后，
    # 焦点会莫名其妙退回地图。
    if claims and not blueprints:
        focus_key = "claims"
    elif blueprints:
        focus_key = "blueprint"
    elif arenas:
        focus_key = "routeArena"
    elif routes:
        focus_key = "route"
    elif research_maps:
        focus_key = "researchMap"
    else:
        focus_key = "problemMap"

    focus: dict[str, Any] = {
        "key": focus_key,
        "label": "研究产物",
        "state": "available",
        "revision": latest_bp.revision if latest_bp else revision,
        "routes": [
            {
                "id": str(r.id),
                "label": r.label,
                "constraints": list(r.constraints or []),
                "evidenceCoverage": list(r.evidence_coverage or []),
                "gaps": list(r.gaps or []),
            }
            for r in routes
        ],
        "blueprint": _blueprint_projection(latest_bp),
        "runStatus": None,
        "run": None,
        "claims": [
            {
                "id": str(c.id),
                "text": c.text,
                "status": c.status,
                "evidenceRefs": list(c.evidence_refs or []),
                "relation": c.relation or "unknown",
            }
            for c in claims
        ],
    }
    # RV09 · 地图直接在顶层给出一等投影（而不是只塞进 `objects`）：
    # 前端画布需要"当前修订号 + 内容"成对出现，才能把编辑请求标成
    # `expected_revision` 并在冲突时显示 diff。
    focus["problemMap"] = (
        {
            "revision": int(latest_pm.revision),
            "objectId": str(latest_pm.id),
            "contentHash": latest_pm.content_hash or "",
            "confirmedAt": latest_pm.confirmed_at.isoformat() if latest_pm.confirmed_at else None,
            "content": dict(latest_pm.content or {}),
        }
        if latest_pm is not None
        else None
    )
    focus["researchMap"] = (
        {
            "revision": int(latest_rm.revision),
            "objectId": str(latest_rm.id),
            "contentHash": latest_rm.content_hash or "",
            "confirmedAt": latest_rm.confirmed_at.isoformat() if latest_rm.confirmed_at else None,
            "deprecated": bool((latest_rm.content or {}).get("deprecated", False)),
            "content": dict(latest_rm.content or {}),
        }
        if latest_rm is not None
        else None
    )
    # RV10 · 路线竞技场一等投影：比较矩阵 + 各路线声明 + 被拒原因，
    # 前端据此渲染路线比较 artifact（rubric 表 + trade-off/unknown + 证据 chips）。
    focus["routeArena"] = (
        {
            "revision": int(latest_arena.revision),
            "objectId": str(latest_arena.id),
            "contentHash": latest_arena.content_hash or "",
            "confirmedAt": latest_arena.confirmed_at.isoformat() if latest_arena.confirmed_at else None,
            "content": dict(latest_arena.content or {}),
        }
        if latest_arena is not None
        else None
    )
    # 额外对象族（加法式）：前端未渲染时不产生影响，但已渲染的面板可增量消费。
    focus["objects"] = _object_families(db, session_id)
    return focus, True


def _blueprint_projection(latest_bp) -> dict[str, Any] | None:
    """blueprint 投影：兼容旧形状（problem/objective/route 字符串）与 RV10 形状
    （binds/route/modelSpec/experimentPlan/...）。旧键原样保留，新键加法式追加。"""
    if latest_bp is None:
        return None
    content = dict(latest_bp.content or {})
    out: dict[str, Any] = {
        "revision": int(latest_bp.revision),
        "objectId": str(latest_bp.id),
        "contentHash": latest_bp.content_hash or "",
        "confirmedAt": latest_bp.confirmed_at.isoformat() if latest_bp.confirmed_at else None,
        "confirmationState": "confirmed" if latest_bp.confirmed_at else "pending",
        # 旧形状键（legacy blueprint 仍有这些）
        "problem": str(content.get("problem", "")),
        "objective": str(content.get("objective", "")),
        "route": str(content.get("route", "")),
        "evidenceNodes": list(content.get("evidenceNodes", []) or []),
        "assumptions": list(content.get("assumptions", []) or []),
        "lineage": list(content.get("lineage", []) or []),
    }
    # RV10 形状加法式键
    for key in (
        "schemaVersion",
        "routeId",
        "binds",
        "routeDetail",
        "modelSpec",
        "experimentPlan",
        "evidenceRequirements",
        "verificationGates",
        "budget",
        "deadline",
        "stopConditions",
        "insufficiencies",
    ):
        if key in content:
            out[key] = content[key]
    # routeDetail：RV10 的选定路线对象（避免与旧形状的 route 字符串撞名）
    if isinstance(content.get("route"), dict):
        out["routeDetail"] = content["route"]
    return out


def _object_families(db: Session, session_id: uuid.UUID) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from ..db.models import (
            ExperimentPlanRow,
            ExperimentRunRow,
            PaperSpecRow,
            ProblemMapRow,
            ResearchIntakeRow,
            ResearchMapRow,
            ReviewFindingRow,
            VerificationRecordRow,
        )
    except ImportError:  # pragma: no cover - PHASE 3 未落地前的过渡态
        return out

    intake = (
        db.query(ResearchIntakeRow)
        .filter(ResearchIntakeRow.session_id == session_id)
        .order_by(ResearchIntakeRow.revision)
        .all()
    )
    if intake:
        out["intake"] = intake[-1].content or {}

    pmaps = (
        db.query(ProblemMapRow)
        .filter(ProblemMapRow.session_id == session_id)
        .order_by(ProblemMapRow.revision)
        .all()
    )
    if pmaps:
        out["problemMap"] = pmaps[-1].content or {}

    rmap = (
        db.query(ResearchMapRow)
        .filter(ResearchMapRow.session_id == session_id)
        .order_by(ResearchMapRow.revision)
        .all()
    )
    if rmap:
        out["researchMap"] = rmap[-1].content or {}

    plans = (
        db.query(ExperimentPlanRow)
        .filter(ExperimentPlanRow.session_id == session_id)
        .order_by(ExperimentPlanRow.revision)
        .all()
    )
    if plans:
        out["experimentPlan"] = plans[-1].content or {}

    runs = (
        db.query(ExperimentRunRow)
        .filter(ExperimentRunRow.session_id == session_id)
        .order_by(ExperimentRunRow.created_at)
        .all()
    )
    if runs:
        out["experimentRuns"] = [
            {
                "id": str(r.id),
                "status": r.status,
                "exitCode": r.exit_code,
                "claimHolds": r.claim_holds,
                "metrics": r.metrics or {},
                "reproducible": bool((r.repro or {}).get("imageDigest")),
                "notReproducibleReason": r.not_reproducible_reason or "",
            }
            for r in runs
        ]

    verifs = (
        db.query(VerificationRecordRow)
        .filter(VerificationRecordRow.session_id == session_id)
        .order_by(VerificationRecordRow.created_at)
        .all()
    )
    if verifs:
        out["verifications"] = [
            {
                "id": str(v.id),
                "result": v.result,
                "claimId": str(v.claim_id) if v.claim_id else None,
                "evidenceRefs": list(v.evidence_refs or []),
            }
            for v in verifs
        ]

    papers = (
        db.query(PaperSpecRow)
        .filter(PaperSpecRow.session_id == session_id)
        .order_by(PaperSpecRow.revision)
        .all()
    )
    if papers:
        out["paperSpec"] = papers[-1].content or {}

    findings = (
        db.query(ReviewFindingRow)
        .filter(ReviewFindingRow.session_id == session_id)
        .order_by(ReviewFindingRow.created_at)
        .all()
    )
    if findings:
        out["reviewFindings"] = [
            {
                "id": str(f.id),
                "category": f.category,
                "severity": f.severity,
                "location": f.location,
                "status": f.status,
                "suggestion": f.suggestion,
            }
            for f in findings
        ]
    return out
