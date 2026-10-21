"""R19 画像 body 与教师侧证据行构建器（确定性装配；service 只编排）。

- `build_profile_body`：MetricsResult + 上下文 → VersionedClassLearningProfile
  （R19 全字段：task_scope/time_window/metrics/evidence_refs/teacher_actions）；
- `build_evidence_rows`：MetricsResult → tp_teaching_evidence 行组
  （每指标族一行；value=聚合数字，refs=typed refs；零 PII 行内容）；
- 两函数均为纯装配（无 IO、无时钟、无随机）；warnings 由 schema 校验器推导。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.teacher_prep.profile.evidence import TeachingEvidenceRow
from app.teacher_prep.profile.metrics import CALC_VERSION, MetricsResult, teacher_actions_for
from app.teacher_prep.profile.schema import (
    EvidenceTypedRef,
    ProfileEvidenceSummary,
    TaskScopeInfo,
    TimeWindowInfo,
    VersionedClassLearningProfile,
    eval_freshness,
)
from app.teacher_prep_v2.contracts.models import (
    ComputationInfo,
    ConfidenceDistribution,
    Participation,
    ProfileAggregation,
    ProfileScope,
)

# computed_from.input_refs 上限（bounded；完整血缘由 input_digest 指纹承载）
_MAX_INPUT_REFS = 8
# 单指标 typed refs 上限（避免画像 body 无界膨胀；计数保持完整）
_MAX_REFS_PER_METRIC = 50


def build_profile_body(
    *,
    profile_id: str,
    class_id: str,
    session_id: str,
    owner_id: str,
    result: MetricsResult,
    assignment_id: str | None,
    assignment_title: str | None,
    due_at: datetime | None,
    task_scope: TaskScopeInfo | None,
    expires_at: datetime | None,
    now: datetime,
    revision: int = 1,
    evidence_count: int = 0,
    evidence_refs: list[EvidenceTypedRef] | None = None,
) -> VersionedClassLearningProfile:
    """MetricsResult → 版本化画像 body（全部统计来自确定性 result）。"""
    m = result.metrics
    evidence_start = result.evidence_start
    evidence_end = result.evidence_end
    body = VersionedClassLearningProfile(
        profile_id=profile_id,
        class_id=class_id,
        session_id=session_id,
        owner_id=owner_id,
        scope=ProfileScope(
            kind="SESSION",
            start=evidence_start.date() if evidence_start else None,
            end=evidence_end.date() if evidence_end else None,
        ),
        status="published",
        revision=revision,
        aggregation=ProfileAggregation(
            submission_count=result.deduped_submission_count,
            active_student_count=m.sample.sample_size,
            confidence_distribution=ConfidenceDistribution(
                low=m.confidence.low,
                mid=m.confidence.mid,
                high=m.confidence.high,
                unknown=m.confidence.missing_count,
            ),
            mastery_by_kp=result.mastery_by_kp,
            top_misconceptions=result.misconceptions,
            participation=Participation(
                submitted=m.coverage.answered, not_submitted=m.coverage.not_submitted
            ),
        ),
        computed_from=ComputationInfo(
            run_id=m.run_id,
            provider_version=CALC_VERSION,
            input_refs=_input_refs(assignment_id, result),
            computed_at=now,
            note=f"input_digest={result.input_digest}",
        ),
        evidence=ProfileEvidenceSummary(
            submission_count=result.deduped_submission_count,
            evidence_count=evidence_count,
            coverage_ratio=m.coverage.submission_rate or 0.0,
            expected_evidence=m.coverage.eligible,
            low_coverage=m.coverage.low_coverage,
            conflicts=[],
        ),
        freshness=eval_freshness(now, expires_at, now=now),
        task_scope=task_scope
        or TaskScopeInfo(assignment_external_id=assignment_id, assignment_title=assignment_title),
        time_window=TimeWindowInfo(
            due_at=due_at,
            evidence_start=evidence_start,
            evidence_end=evidence_end,
        ),
        metrics=m,
        evidence_refs=evidence_refs or [],
    )
    # teacher_actions = f(warnings)（warnings 已由校验器确定性推导）
    actions = teacher_actions_for(list(body.warnings))
    return body.model_copy(update={"teacher_actions": actions})


def _input_refs(assignment_id: str | None, result: MetricsResult) -> list[str]:
    """计算输入引用（OpaqueRef 语法：仅字母数字._/-；完整血缘由 input_digest 承载）。"""
    aid = assignment_id or "unknown"
    refs = [f"prep/submission-facts/{aid}"]
    for key in sorted(result.facts_refs)[: _MAX_INPUT_REFS - 1]:
        no = str(key).split(":")[-1]  # facts_refs 键形如 item:3
        refs.append(f"prep/item-observations/item-{no}")
    return refs


def build_evidence_rows(
    *,
    result: MetricsResult,
    class_id: str,
    session_id: str,
    profile_id: str | None,
    assignment_id: str | None,
    actor_id: str,
) -> list[TeachingEvidenceRow]:
    """MetricsResult → 证据行组（bounded；typed refs 指向 8000 事实行）。"""
    m = result.metrics
    common: dict[str, Any] = {
        "class_id": uuid.UUID(class_id),
        "session_id": uuid.UUID(session_id),
        "profile_id": uuid.UUID(profile_id) if profile_id else None,
        "assignment_external_id": assignment_id,
        "calc_version": m.calc_version,
        "metrics_fingerprint": m.metrics_fingerprint,
        "computed_at": m.computed_at,
        "actor_id": uuid.UUID(actor_id),
        "source": "system",
        "status": "valid",
    }
    rows: list[TeachingEvidenceRow] = []

    def _row(ev_kind: str, metric_key: str, value: dict[str, Any], refs: list[dict[str, Any]], sample: int) -> TeachingEvidenceRow:
        return TeachingEvidenceRow(
            id=uuid.uuid4(),
            ev_kind=ev_kind,
            metric_key=metric_key,
            value=_jsonable(value),
            refs=_jsonable(refs[:_MAX_REFS_PER_METRIC]),
            sample_size=sample,
            **common,
        )

    # 1) coverage（1 行）
    rows.append(
        _row(
            "coverage",
            "coverage",
            {
                "eligible": m.coverage.eligible,
                "answered": m.coverage.answered,
                "not_submitted": m.coverage.not_submitted,
                "submission_rate": m.coverage.submission_rate,
                "low_coverage": m.coverage.low_coverage,
                "eligible_changed": m.coverage.eligible_changed,
            },
            [],
            m.coverage.answered,
        )
    )
    # 2) confidence（1 行）
    rows.append(
        _row(
            "confidence",
            "confidence",
            m.confidence.model_dump(mode="json"),
            [],
            m.confidence.low + m.confidence.mid + m.confidence.high,
        )
    )
    # 3) anomaly（1 行）
    rows.append(_row("anomaly", "anomaly", m.observed.model_dump(mode="json"), [], 0))
    # 4) 每题 difficulty
    for d in m.difficulty_by_item:
        rows.append(
            _row(
                "difficulty",
                f"item:{d.item_no}:difficulty",
                d.model_dump(mode="json"),
                result.facts_refs.get(f"item:{d.item_no}", []),
                d.judged,
            )
        )
    # 5/6) 每 KP mastery/misconception
    for km in result.mastery_by_kp:
        refs: list[dict[str, Any]] = []
        for no in result.kp_item_nos.get(km.kp_code, []):
            refs.extend(result.facts_refs.get(f"item:{no}", []))
        rows.append(
            _row(
                "mastery",
                f"kp:{km.kp_code}:mastery",
                {"mastery": km.mastery, "sample_size": km.sample_size},
                refs,
                km.sample_size,
            )
        )
    for mc in result.misconceptions:
        refs = []
        for no in result.kp_item_nos.get(mc.kp_code, []):
            refs.extend(result.facts_refs.get(f"item:{no}", []))
        rows.append(
            _row(
                "misconception",
                f"kp:{mc.kp_code}:misconception",
                {"label": mc.label, "count": mc.count},
                refs,
                mc.count,
            )
        )
    return rows


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def evidence_refs_of(rows: list[TeachingEvidenceRow]) -> list[EvidenceTypedRef]:
    """证据行 → 画像 body.evidence_refs（typed refs 引用集）。"""
    out: list[EvidenceTypedRef] = []
    for row in rows:
        out.append(
            EvidenceTypedRef(
                evidence_id=str(row.id),
                ev_kind=row.ev_kind,
                metric_key=row.metric_key,
                refs=[r for r in row.refs if isinstance(r, dict)][:_MAX_REFS_PER_METRIC],
            )
        )
    return out


__all__ = [
    "build_profile_body",
    "build_evidence_rows",
    "evidence_refs_of",
]
