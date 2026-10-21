"""R20 版本化 ClassLearningProfile 业务 schema（8100 事实 body 契约）。

- `VersionedClassLearningProfile`：profile 事实 body（tp_class_learning_profiles.body 权威 JSON）。
- 聚合复用 R13 跨端契约 DTO（ProfileAggregation/ProfileScope/ComputationInfo），
  保证「六公开契约」名不形成第二套事实；本 schema 只叠加版本化/时效/证据质量字段。
- 时效性：`freshness.stale` 由 `expires_at` 确定性判定（旧 Profile 必须显式 warning，
  不允许静默沿用）。
- 证据质量：`evidence.low_coverage` / `evidence.conflicts` → `warnings`
  （低覆盖 / 证据冲突，R20 修订提案必须保留）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.teacher_prep_v2.contracts.models import (
    ComputationInfo,
    ProfileAggregation,
    ProfileScope,
)

# 契约版本（内容语义断裂必须 bump；reader fail closed）
PROFILE_SCHEMA_VERSION = "1.0.0"

# 时效窗口：聚合后超过该天数视为旧 Profile（stale）
PROFILE_VALIDITY_DAYS = 7
# 低覆盖阈值：coverage_ratio 低于此值 → low_profile_coverage warning
LOW_COVERAGE_THRESHOLD = 0.5

# warning 码（与前端 allowlist 渲染一致的稳定枚举；R19 新增码 additive 追加）
ProfileWarningCode = Literal[
    # R20 既有
    "low_profile_coverage",
    "stale_profile",
    "evidence_conflict",
    "low_confidence",
    "missing_locator",
    "missing_score",
    "scope_start",
    "provenance",
    "authority",
    # R19 新增（无数据/低样本/口径缺失/观测异常）
    "no_data",
    "low_sample",
    "membership_changed",
    "missing_confidence",
    "missing_answers",
    "duplicates_dropped",
    "outliers_excluded",
    "pending_review",
    "attempt_policy_unknown",
    "missing_item_kp_map",
    "late_submissions",
    "resubmissions",
]

# evidence conflict 码
ConflictCode = Literal[
    "evidence_conflict",
    "low_confidence",
    "missing_locator",
    "missing_score",
    "grading_conflict",
]


class EvidenceConflict(BaseModel):
    """一条证据冲突/质量缺口（不得静默吞没）。"""

    model_config = ConfigDict(extra="forbid")

    code: ConflictCode  # type: ignore[valid-type]
    detail: str = Field(min_length=1, max_length=500)
    ref: str | None = Field(default=None, max_length=512)


class ProfileEvidenceSummary(BaseModel):
    """证据质量摘要（确定性计算；驱动低覆盖/证据冲突 warning）。"""

    model_config = ConfigDict(extra="forbid")

    submission_count: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)
    coverage_ratio: float = Field(default=0.0, ge=0, le=1)  # evidence_count / expected
    expected_evidence: int = Field(default=0, ge=0)  # 期望证据数（提交数代理）
    low_coverage: bool = False
    conflicts: list[EvidenceConflict] = Field(default_factory=list, max_length=20)


class ProfileFreshness(BaseModel):
    """时效窗口（旧 Profile 判定唯一真源）。"""

    model_config = ConfigDict(extra="forbid")

    computed_at: datetime
    expires_at: datetime | None = None  # None = 未设时效上限
    stale: bool = False
    stale_reason: str | None = Field(default=None, max_length=200)


# ── R19 新增：确定性统计子模型（additive optional；旧 body 仍可读）─────────────


class TaskScopeInfo(BaseModel):
    """画像归属：class/session/task（typing refs，不复制任务内容）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    task_kind: Literal["PRE_CLASS_DIAGNOSTIC", "CLASSROOM"] | None = None
    task_assignment_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    assignment_external_id: str | None = Field(default=None, max_length=64)
    assignment_title: str | None = Field(default=None, max_length=200)


class TimeWindowInfo(BaseModel):
    """证据时间窗（task window ∪ 提交时间范围；均缺失 → unknown）。"""

    model_config = ConfigDict(extra="forbid")

    open_at: datetime | None = None
    due_at: datetime | None = None
    evidence_start: datetime | None = None  # 最早提交时间
    evidence_end: datetime | None = None  # 最晚提交时间


class CoverageMetrics(BaseModel):
    """eligible/answered 覆盖（样本口径；缺 eligible → 全 unknown）。"""

    model_config = ConfigDict(extra="forbid")

    eligible: int = Field(default=0, ge=0)
    answered: int = Field(default=0, ge=0)
    not_submitted: int = Field(default=0, ge=0)
    submission_rate: float | None = Field(default=None, ge=0, le=1)  # None = eligible 未知/为 0
    low_coverage: bool = False
    eligible_changed: bool | None = None  # 与上一 revision 对比；None=无先例


class SampleMetrics(BaseModel):
    """样本量与「是否足够下结论」（低样本不得下肯定结论的唯一判定点）。"""

    model_config = ConfigDict(extra="forbid")

    sample_size: int = Field(default=0, ge=0)  # 去重后有效提交人数
    sufficient: bool = False  # sample_size >= MIN_SAMPLE


class AttemptPolicyMetrics(BaseModel):
    """attempt policy 快照 + 观测计数（策略缺失 → known=False，不猜测）。"""

    model_config = ConfigDict(extra="forbid")

    known: bool = False
    attempts_allowed: int | None = Field(default=None, ge=1)
    resubmit_allowed: bool | None = None
    allow_late: bool | None = None
    late_count: int = Field(default=0, ge=0)
    resubmit_count: int = Field(default=0, ge=0)


class ObservedAnomalies(BaseModel):
    """数据质量观测计数（每项 >0 均产生对应 warning；只计数不具名）。"""

    model_config = ConfigDict(extra="forbid")

    duplicates_dropped: int = Field(default=0, ge=0)
    outliers_excluded: int = Field(default=0, ge=0)
    pending_review_count: int = Field(default=0, ge=0)
    missing_answers_count: int = Field(default=0, ge=0)
    non_member_submissions: int = Field(default=0, ge=0)  # 非当前成员提交（已从统计排除）


class ConfidenceMetrics(BaseModel):
    """识别/建议置信分布（0.6/0.85 分档；缺失计数显式）。"""

    model_config = ConfigDict(extra="forbid")

    mean: float | None = Field(default=None, ge=0, le=1)
    low: int = Field(default=0, ge=0)
    mid: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)
    missing_count: int = Field(default=0, ge=0)


class ItemDifficulty(BaseModel):
    """单题难度（correct_rate 分档；样本不足 → difficulty=None 显式 unknown）。"""

    model_config = ConfigDict(extra="forbid")

    item_no: int = Field(ge=1)
    answered: int = Field(default=0, ge=0)
    judged: int = Field(default=0, ge=0)  # correct+wrong（pending 不参与比率）
    correct: int = Field(default=0, ge=0)
    wrong: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)  # 待复核作答数（不参与比率，进 warning）
    correct_rate: float | None = Field(default=None, ge=0, le=1)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    outlier: bool = False  # 参与 IQR 离群剔除标记


class ProfileMetricsInfo(BaseModel):
    """R19 确定性统计块（calc_version 指纹可复算；None = 旧版本 body 无统计块）。"""

    model_config = ConfigDict(extra="forbid")

    calc_version: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=128)
    computed_at: datetime
    metrics_fingerprint: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    input_digest: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    coverage: CoverageMetrics = Field(default_factory=CoverageMetrics)
    sample: SampleMetrics = Field(default_factory=SampleMetrics)
    attempt_policy: AttemptPolicyMetrics = Field(default_factory=AttemptPolicyMetrics)
    observed: ObservedAnomalies = Field(default_factory=ObservedAnomalies)
    confidence: ConfidenceMetrics = Field(default_factory=ConfidenceMetrics)
    difficulty_by_item: list[ItemDifficulty] = Field(default_factory=list, max_length=200)
    class_mean_correct_rate: float | None = Field(default=None, ge=0, le=1)
    kp_map_provided: bool = True  # 题目→前置知识映射是否提供（缺失 → 不得给 mastery）


class EvidenceRefEntry(BaseModel):
    """typed ref：指向 8000 事实的稳定引用（不含 PII 行内容）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["submission", "submission_item", "assignment"]
    external_id: str | None = Field(default=None, max_length=64)  # 8000 事实行 id
    contract_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")  # UUIDv5 派生
    item_no: int | None = Field(default=None, ge=1)


class EvidenceTypedRef(BaseModel):
    """画像 → 教师侧证据记录（tp_teaching_evidence）的引用 + 佐证事实 refs。"""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    ev_kind: str = Field(min_length=1, max_length=40)  # coverage/mastery/misconception/difficulty/confidence/anomaly/interpretation
    metric_key: str = Field(min_length=1, max_length=120)
    refs: list[EvidenceRefEntry] = Field(default_factory=list, max_length=200)


class TeacherAction(BaseModel):
    """warning → 可采取的教师动作（确定性映射，见 PROFILE-METRICS.md）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=1, max_length=200)


class VersionedClassLearningProfile(BaseModel):
    """版本化班级学习画像事实 body（tp_class_learning_profiles.body 权威 JSON）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["CLASS_LEARNING_PROFILE"] = "CLASS_LEARNING_PROFILE"
    profile_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    class_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    owner_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    scope: ProfileScope = Field(default_factory=ProfileScope)
    status: Literal["collecting", "preview", "published", "archived"] = "published"
    revision: int = Field(default=1, ge=1)
    aggregation: ProfileAggregation = Field(default_factory=ProfileAggregation)
    computed_from: ComputationInfo | None = None
    evidence: ProfileEvidenceSummary = Field(default_factory=ProfileEvidenceSummary)
    freshness: ProfileFreshness
    warnings: list[ProfileWarningCode] = Field(default_factory=list, max_length=16)  # type: ignore[valid-type]
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)
    # ── R19 additive（None = 旧版本 body，不参与新 warning 推导）────────────────
    task_scope: TaskScopeInfo | None = None
    time_window: TimeWindowInfo | None = None
    metrics: ProfileMetricsInfo | None = None
    evidence_refs: list[EvidenceTypedRef] = Field(default_factory=list, max_length=64)
    teacher_actions: list[TeacherAction] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _recompute_warnings(self) -> VersionedClassLearningProfile:
        """warnings 必须与 body 内容一致（确定性推导，防漂移；R19 扩展见 _derive_metric_warnings）。"""
        expected: list[str] = []
        if self.evidence.low_coverage:
            expected.append("low_profile_coverage")
        if self.freshness.stale:
            expected.append("stale_profile")
        if self.evidence.conflicts:
            expected.append("evidence_conflict")
        for flag in self.unknown_flags:
            if flag in ("provenance", "authority", "scope_start"):
                expected.append(flag)
        if self.computed_from is None:
            expected.append("provenance")
        if self.metrics is not None:
            expected.extend(_derive_metric_warnings(self.metrics))
        self.warnings = list(dict.fromkeys(expected))
        return self


def _derive_metric_warnings(metrics: ProfileMetricsInfo) -> list[str]:
    """R19 统计块 → warning 码（确定性推导；与 PROFILE-METRICS.md §阈值一致）。

    顺序即 canonical 输出顺序；与 R20 既有推导合并去重由调用方完成。
    """
    out: list[str] = []
    if metrics.sample.sample_size == 0:
        out.append("no_data")
    elif not metrics.sample.sufficient:
        out.append("low_sample")
    if metrics.coverage.low_coverage:
        out.append("low_profile_coverage")
    if metrics.coverage.eligible_changed:
        out.append("membership_changed")
    if metrics.confidence.missing_count > 0:
        out.append("missing_confidence")
    if metrics.observed.missing_answers_count > 0:
        out.append("missing_answers")
    if metrics.observed.duplicates_dropped > 0:
        out.append("duplicates_dropped")
    if metrics.observed.outliers_excluded > 0:
        out.append("outliers_excluded")
    if metrics.observed.pending_review_count > 0:
        out.append("pending_review")
    if not metrics.attempt_policy.known:
        out.append("attempt_policy_unknown")
    if not metrics.kp_map_provided:
        out.append("missing_item_kp_map")
    if metrics.attempt_policy.late_count > 0:
        out.append("late_submissions")
    if metrics.attempt_policy.resubmit_count > 0:
        out.append("resubmissions")
    return out


def eval_freshness(computed_at: datetime, expires_at: datetime | None, *, now: datetime) -> ProfileFreshness:
    """确定性时效判定：expires_at 过点 或 超出有效期 → stale（含原因）。"""
    if expires_at is not None and now > expires_at:
        return ProfileFreshness(
            computed_at=computed_at,
            expires_at=expires_at,
            stale=True,
            stale_reason=f"expires_at={expires_at.isoformat()} 已过（now={now.isoformat()}）",
        )
    if expires_at is None:
        limit = computed_at + timedelta(days=PROFILE_VALIDITY_DAYS)
        if now > limit:
            return ProfileFreshness(
                computed_at=computed_at,
                expires_at=None,
                stale=True,
                stale_reason=(
                    f"超出有效期 {PROFILE_VALIDITY_DAYS} 天（computed_at={computed_at.isoformat()}）"
                ),
            )
    return ProfileFreshness(computed_at=computed_at, expires_at=expires_at, stale=False)


__all__ = [
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_VALIDITY_DAYS",
    "LOW_COVERAGE_THRESHOLD",
    "VersionedClassLearningProfile",
    "ProfileEvidenceSummary",
    "ProfileFreshness",
    "EvidenceConflict",
    "eval_freshness",
    # R19
    "TaskScopeInfo",
    "TimeWindowInfo",
    "CoverageMetrics",
    "SampleMetrics",
    "AttemptPolicyMetrics",
    "ObservedAnomalies",
    "ConfidenceMetrics",
    "ItemDifficulty",
    "ProfileMetricsInfo",
    "EvidenceRefEntry",
    "EvidenceTypedRef",
    "TeacherAction",
]
