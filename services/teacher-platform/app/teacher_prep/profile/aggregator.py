"""R20 学情证据确定性聚合（StudentSubmission/Evidence → 版本化 Profile）。

纯函数设计（可复算/可审计）：
- 不调用 LLM；输入已确认的跨端契约（R13），输出 `VersionedClassLearningProfile` body；
- 缺失即显式 unknown：confidence/score/locator 缺席 → unknown_flags / conflicts，
  不伪造默认值（与 R13「缺失即显式 unknown」同文）；
- 同输入 + 同 now → 同输出（确定性验收锚点）。

8000 侧真实聚合管线（按班拉取 submissions/evidences、KP 映射）留 R21+；
本模块提供输入装配的确定性骨架。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime

from app.teacher_prep_v2.contracts.models import (
    ComputationInfo,
    ConfidenceDistribution,
    Evidence,
    KpMastery,
    Misconception,
    Participation,
    ProfileAggregation,
    ProfileScope,
    StudentSubmission,
)

from .schema import (
    EvidenceConflict,
    ProfileEvidenceSummary,
    ProfileFreshness,
    VersionedClassLearningProfile,
    eval_freshness,
)

# confidence 分档（确定性阈值）
_LOW_BOUND, _MID_BOUND = 0.6, 0.85


def _confidence_bucket(confidence: float | None) -> str:
    if confidence is None:
        return "unknown"
    if confidence < _LOW_BOUND:
        return "low"
    if confidence < _MID_BOUND:
        return "mid"
    return "high"


def _aggregation(
    submissions: list[StudentSubmission],
    *,
    kp_mastery: list[KpMastery],
    misconceptions: list[Misconception],
) -> ProfileAggregation:
    conf_dist = Counter(_confidence_bucket(a.confidence) for s in submissions for a in s.answers)
    submitted_students = {str(s.student_id) for s in submissions}
    return ProfileAggregation(
        submission_count=len(submissions),
        active_student_count=len(submitted_students),
        confidence_distribution=ConfidenceDistribution(
            low=conf_dist["low"],
            mid=conf_dist["mid"],
            high=conf_dist["high"],
            unknown=conf_dist["unknown"],
        ),
        mastery_by_kp=kp_mastery,
        top_misconceptions=misconceptions,
        participation=Participation(submitted=len(submissions), not_submitted=0),
    )


def _evidence_summary(
    submissions: list[StudentSubmission],
    evidences: list[Evidence],
) -> ProfileEvidenceSummary:
    conflicts: list[EvidenceConflict] = []
    expected = len(submissions)
    evidence_count = len(evidences)

    for ev in evidences:
        for flag in ev.unknown_flags:
            if flag == "evidence_conflict":
                conflicts.append(
                    EvidenceConflict(code="evidence_conflict", detail=f"evidence {ev.id} 证据冲突", ref=str(ev.id))
                )
            if flag == "low_confidence":
                conflicts.append(
                    EvidenceConflict(code="low_confidence", detail=f"evidence {ev.id} 识别置信度低", ref=str(ev.id))
                )
            if flag == "source_locator":
                conflicts.append(
                    EvidenceConflict(code="missing_locator", detail=f"evidence {ev.id} 无 SourceLocator", ref=str(ev.id))
                )
    for s in submissions:
        if s.grading is not None and s.grading.score is None and s.grading.verdict != "pending_review":
            conflicts.append(
                EvidenceConflict(
                    code="missing_score",
                    detail=f"submission {s.id} 判定 {s.grading.verdict} 但缺 score",
                    ref=str(s.id),
                )
            )
        if s.grading is not None and s.grading.ai_pregraded and s.grading.teacher_final_score is None:
            conflicts.append(
                EvidenceConflict(
                    code="grading_conflict",
                    detail=f"submission {s.id} AI 预判未获教师终评",
                    ref=str(s.id),
                )
            )

    coverage_ratio = 0.0
    if expected > 0:
        coverage_ratio = min(1.0, evidence_count / expected)
    return ProfileEvidenceSummary(
        submission_count=len(submissions),
        evidence_count=evidence_count,
        coverage_ratio=coverage_ratio,
        expected_evidence=expected,
        low_coverage=coverage_ratio < 0.5,
        conflicts=conflicts,
    )


def aggregate_class_profile(
    *,
    class_id: str,
    owner_id: str,
    session_id: str | None = None,
    scope: ProfileScope | None = None,
    submissions: list[StudentSubmission] | None = None,
    evidences: list[Evidence] | None = None,
    kp_mastery: list[KpMastery] | None = None,
    misconceptions: list[Misconception] | None = None,
    computed_from: ComputationInfo | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[VersionedClassLearningProfile, ProfileFreshness]:
    """确定性聚合：同输入 + 同 now → 同 Profile body（可复算验收）。

    返回 (profile, freshness) —— freshness 由 service 落库列（时效列），
    profile.body 内嵌 fresh 快照保持一致。
    """
    now = now or datetime.now(UTC)
    submissions = submissions or []
    evidences = evidences or []

    aggregation = _aggregation(submissions, kp_mastery=kp_mastery or [], misconceptions=misconceptions or [])
    evidence_summary = _evidence_summary(submissions, evidences)
    freshness = eval_freshness(now, expires_at, now=now)

    unknown_flags: list[str] = []
    if computed_from is None:
        unknown_flags.append("provenance")
    if session_id is None:
        unknown_flags.append("session_id")

    profile = VersionedClassLearningProfile(
        profile_id=str(uuid.uuid4()),
        class_id=class_id,
        session_id=session_id,
        owner_id=owner_id,
        scope=scope or ProfileScope(),
        status="published",
        revision=1,
        aggregation=aggregation,
        computed_from=computed_from,
        evidence=evidence_summary,
        freshness=freshness,
        unknown_flags=list(dict.fromkeys(unknown_flags)),
    )
    return profile, freshness


__all__ = [
    "aggregate_class_profile",
    "_aggregation",
    "_evidence_summary",
]
