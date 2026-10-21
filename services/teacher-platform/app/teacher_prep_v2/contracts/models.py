"""六个唯一公开跨端契约（R13，Pydantic 单一权威源）。

命名铁律（DECISIONS §6）：本文件只出现
  PreClassDiagnosticTask / ClassroomTask / TaskAssignment / StudentSubmission /
  Evidence / ClassLearningProfile
旧名 TaskContract / Submission 仅存在于 reader 的 ALIAS_KINDS（内部兼容别名），
不得在本文件或任何新 writer 中形成第二套事实名。

"缺失即显式 unknown"：confidence / attempt / provenance / source_locator / score
任一缺席（旧数据、未评分、无图源）都不得伪造默认值——模型把缺席项如实写入
`unknown_flags`，消费方按 unknown 处理（见 BREAKING-CHANGE-RULES.md §4）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import (
    PREP_CONTRACTS_SCHEMA_VERSION,
    ClassId,
    DataRetention,
    IdempotencyKey,
    OpaqueRef,
    OwnerId,
    Provenance,
    SessionId,
    StudentId,
    TenantId,
    UuidStr,
    _ContractModel,
)
from .sourcelocator import SourceLocatorRef

TASK_KIND = Literal["PRE_CLASS_DIAGNOSTIC", "CLASSROOM"]

# ─────────────────────────────────────────────────────────────────────────────
# 共享子模型
# ─────────────────────────────────────────────────────────────────────────────


class TaskRef(_ContractModel):
    """指向 PreClassDiagnosticTask | ClassroomTask 的稳定引用。"""

    task_id: UuidStr
    task_kind: TASK_KIND  # type: ignore[valid-type]


class AssignmentRef(_ContractModel):
    """指向 TaskAssignment 的稳定引用。"""

    task_assignment_id: UuidStr


class DiagnosticItem(_ContractModel):
    """诊断/课堂任务的内容项（题目引用或自由问题）。"""

    item_no: int = Field(ge=1)
    problem_locator: SourceLocatorRef | None = None
    prompt: str | None = Field(default=None, max_length=2000)
    max_score: float | None = Field(default=None, ge=0)


class AnswerItem(_ContractModel):
    """学生单题作答（answer 文本/图证据均属于 UNTRUSTED DATA）。"""

    item_no: int = Field(ge=1)
    answer_text: str | None = None
    source_locator: SourceLocatorRef | None = None  # 图题 OCR → chunk 定位（可选）
    confidence: float | None = Field(default=None, ge=0, le=1)  # 识别置信度（缺失=unknown）


class AuthorityRef(_ContractModel):
    """事实权威锚点：8000 submissions.id（学生提交事实表法定在 8000，8100 不复制）。"""

    service: Literal["api"] = "api"
    external_id: UuidStr


class SubmissionRef(_ContractModel):
    """Evidence → Submission 的引用（外键方向由学生端事实决定，至少含一项）。"""

    external_submission_id: UuidStr | None = None
    contract_submission_id: UuidStr | None = None


class ObjectRef(_ContractModel):
    """证据对象存储引用（服务端负责 presign，契约层不落 URL/密钥）。"""

    service: Literal["minio"] = "minio"
    bucket: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=1, max_length=512)


class WindowRef(_ContractModel):
    """任务开放/截止窗口。"""

    open_at: datetime
    due_at: datetime


class LatePolicy(_ContractModel):
    allow_late: bool = False
    grace_minutes: int | None = Field(default=None, ge=0)


class AssignmentTarget(_ContractModel):
    """下发范围：整班或指定学生清单（指定时 student_ids 非空且唯一）。"""

    path: Literal["WHOLE_CLASS", "SPECIFIC_STUDENTS"] = "WHOLE_CLASS"
    student_ids: list[StudentId] = Field(default_factory=list)


class AssignmentProvision(_ContractModel):
    """下发事实记录：8000 assignments.id + 映射版本（稳定重试/对账锚点）。"""

    source: Literal["8000_ASSIGNMENT"] = "8000_ASSIGNMENT"
    external_id: UuidStr | None = None
    mapping_version: int = Field(default=1, ge=1)
    synced_at: datetime | None = None


class SubmissionGrading(_ContractModel):
    """评分快照（AI 建议与教师终评分离；缺失 score/confidence = 未知，不推断）。"""

    verdict: Literal["correct", "wrong", "pending_review", "unknown"] = "pending_review"
    score: float | None = Field(default=None, ge=0)
    ai_pregraded: bool = False
    suggested_score: float | None = Field(default=None, ge=0)
    teacher_final_score: float | None = Field(default=None, ge=0)
    needs_review: bool = False
    grader_provenance: Provenance | None = None


class ProfileScope(_ContractModel):
    kind: Literal["SESSION", "COURSE", "PERIOD"] = "COURSE"
    start: date | None = None
    end: date | None = None


class ConfidenceDistribution(_ContractModel):
    low: int = Field(default=0, ge=0)
    mid: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    unknown: int = Field(default=0, ge=0)


class KpMastery(_ContractModel):
    kp_code: str = Field(min_length=1, max_length=64)
    mastery: float | None = Field(default=None, ge=0, le=1)
    sample_size: int = Field(default=0, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Misconception(_ContractModel):
    kp_code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    count: int = Field(default=0, ge=0)


class Participation(_ContractModel):
    submitted: int = Field(default=0, ge=0)
    not_submitted: int = Field(default=0, ge=0)


class ProfileAggregation(_ContractModel):
    submission_count: int = Field(default=0, ge=0)
    active_student_count: int = Field(default=0, ge=0)
    confidence_distribution: ConfidenceDistribution = Field(default_factory=ConfidenceDistribution)
    mastery_by_kp: list[KpMastery] = Field(default_factory=list)
    top_misconceptions: list[Misconception] = Field(default_factory=list)
    participation: Participation = Field(default_factory=Participation)


class ComputationInfo(_ContractModel):
    """聚合运算血缘：run_id + provider_version + 输入引用（可复算/可审计）。"""

    run_id: OpaqueRef
    provider_version: str = Field(min_length=1, max_length=32)
    input_refs: list[OpaqueRef] = Field(default_factory=list)
    computed_at: datetime
    note: str | None = Field(default=None, max_length=512)


# ─────────────────────────────────────────────────────────────────────────────
# 六个公开契约
# ─────────────────────────────────────────────────────────────────────────────


class PreClassDiagnosticTask(_ContractModel):
    """学前诊断任务（教师创建、经 TaskAssignment 下发给学生；蓝本建议源自 TeachingBlueprint）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["PRE_CLASS_DIAGNOSTIC"] = "PRE_CLASS_DIAGNOSTIC"
    id: UuidStr
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId | None = None
    owner_id: OwnerId
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    window: WindowRef
    attempts_allowed: int = Field(default=1, ge=1)
    resubmit_allowed: bool = False
    items: list[DiagnosticItem] = Field(default_factory=list)
    status: Literal["draft", "published", "closed", "cancelled", "archived"] = "draft"
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> PreClassDiagnosticTask:
        _check_window(self.window)
        _check_item_nos(self.items)
        self.unknown_flags = _missing_unknown(self)
        return self


class ClassroomTask(_ContractModel):
    """课堂任务（必须挂 TeachingSession；session_id 必填）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["CLASSROOM"] = "CLASSROOM"
    id: UuidStr
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId
    owner_id: OwnerId
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    class_mode: Literal["interactive", "presentation", "practice"] = "interactive"
    duration_minutes: int | None = Field(default=None, ge=5)
    window: WindowRef
    attempts_allowed: int = Field(default=1, ge=1)
    resubmit_allowed: bool = False
    items: list[DiagnosticItem] = Field(default_factory=list)
    status: Literal["draft", "published", "closed", "cancelled", "archived"] = "draft"
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> ClassroomTask:
        _check_window(self.window)
        _check_item_nos(self.items)
        self.unknown_flags = _missing_unknown(self)
        return self


class TaskAssignment(_ContractModel):
    """任务下发（8100 侧下发事实 + provision 承载 8000 assignments.id 权威映射）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["TASK_ASSIGNMENT"] = "TASK_ASSIGNMENT"
    id: UuidStr
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId | None = None
    owner_id: OwnerId
    task_ref: TaskRef
    target: AssignmentTarget = Field(default_factory=AssignmentTarget)
    due_at: datetime
    late_policy: LatePolicy = Field(default_factory=LatePolicy)
    attempts_allowed: int = Field(default=1, ge=1)
    provision: AssignmentProvision | None = None
    status: Literal["pending", "provisioned", "active", "done", "closed", "cancelled", "archived"] = "pending"
    idempotency_key: IdempotencyKey
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> TaskAssignment:
        if self.target.path == "SPECIFIC_STUDENTS" and not self.target.student_ids:
            raise ValueError("SPECIFIC_STUDENTS 目标必须提供非空 student_ids")
        seen: set[str] = set()
        for sid in self.target.student_ids:
            if sid in seen:
                raise ValueError(f"student_ids 含重复: {sid}")
            seen.add(sid)
        if self.provision is not None and self.provision.source != "8000_ASSIGNMENT":
            raise ValueError("provision.source 只允许 8000_ASSIGNMENT")
        self.unknown_flags = _missing_unknown(self)
        return self


class StudentSubmission(_ContractModel):
    """学生提交（跨端 DTO + 权威锚点；事实行在 8000 submissions，ID 映射稳定勿重建）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["STUDENT_SUBMISSION"] = "STUDENT_SUBMISSION"
    id: UuidStr
    authority: AuthorityRef | None = None  # None = 旧数据无 8000 锚点（unknown_flags 标记）
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId | None = None
    student_id: StudentId
    task_ref: TaskRef
    assignment_ref: AssignmentRef
    attempt_number: int | None = Field(default=None, ge=1)  # None = 未知尝试序（旧数据）
    client_submit_id: str = Field(min_length=1, max_length=64)
    submitted_at: datetime
    due_at: datetime | None = None
    late_seconds: int | None = Field(default=None, ge=0)
    status: Literal["drafted", "submitted", "late", "returned", "resubmitted", "grading", "graded", "archived"] = "submitted"
    answers: list[AnswerItem] = Field(default_factory=list)
    grading: SubmissionGrading | None = None
    evidence_refs: list[OpaqueRef] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> StudentSubmission:
        if self.due_at is not None and self.submitted_at > self.due_at and self.late_seconds is None:
            self.late_seconds = int((self.submitted_at - self.due_at).total_seconds())
        if self.status == "late" and self.late_seconds is None:
            raise ValueError("late 状态必须可计算 late_seconds（due_at 缺失时须显式提供）")
        self.unknown_flags = _missing_unknown(self)
        return self

    @field_validator("evidence_refs")
    @classmethod
    def _no_dup_evidence(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("evidence_refs 含重复引用")
        return v


class Evidence(_ContractModel):
    """学生作答证据（拍照/OCR/附件；可能含手写=PII）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["EVIDENCE"] = "EVIDENCE"
    id: UuidStr
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId | None = None
    student_id: StudentId
    submission_ref: SubmissionRef
    ev_kind: Literal["PHOTO", "OCR", "ATTACHMENT", "AUDIO", "MATH_RENDER"]
    mime_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(default=0, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    source_locator: SourceLocatorRef | None = None
    object_ref: ObjectRef | None = None
    captured_at: datetime | None = None
    device: str | None = Field(default=None, max_length=200)
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> Evidence:
        if self.submission_ref.external_submission_id is None and self.submission_ref.contract_submission_id is None:
            raise ValueError("submission_ref 至少需要一个 id（external 或 contract）")
        flags = _missing_unknown(self)
        if self.source_locator is None:
            flags.append("source_locator")
        self.unknown_flags = _dedup(flags)
        return self


class ClassLearningProfile(_ContractModel):
    """班级学习画像（8100 事实权威；聚合自已确认的 StudentSubmission/Evidence 输入）。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["CLASS_LEARNING_PROFILE"] = "CLASS_LEARNING_PROFILE"
    id: UuidStr
    tenant_id: TenantId
    class_id: ClassId
    session_id: SessionId | None = None
    owner_id: OwnerId
    scope: ProfileScope = Field(default_factory=ProfileScope)
    status: Literal["collecting", "preview", "published", "archived"] = "collecting"
    aggregation: ProfileAggregation = Field(default_factory=ProfileAggregation)
    computed_from: ComputationInfo | None = None
    revision: int = Field(default=0, ge=0)
    provenance: Provenance | None = None
    data_retention: DataRetention | None = None
    unknown_flags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _check(self) -> ClassLearningProfile:
        self.unknown_flags = _missing_unknown(self)
        if self.computed_from is not None and self.scope.start is None:
            # 画像计算必须有可对齐的时间范围；缺失则视为尚未算成（unknown）
            if not self.unknown_flags:
                self.unknown_flags.append("scope_start")
        return self


# ── 共享校验助手 ──────────────────────────────────────────────────────────────


def _check_window(window: WindowRef) -> None:
    if window.due_at <= window.open_at:
        raise ValueError("window.due_at 必须晚于 window.open_at")


def _check_item_nos(items: list[DiagnosticItem]) -> None:
    seen: set[int] = set()
    for item in items:
        if item.item_no in seen:
            raise ValueError(f"items.item_no 重复: {item.item_no}")
        if item.problem_locator is None and not (item.prompt or "").strip():
            raise ValueError(f"items[{item.item_no}] 必须提供 problem_locator 或 prompt 之一")
        seen.add(item.item_no)


def _missing_unknown(model: _ContractModel) -> list[str]:
    """显式 unknown 审计：confidence/attempt/provenance/score 缺席 → 如实标记。"""
    flags: list[str] = []
    if getattr(model, "provenance", None) is None:
        flags.append("provenance")
    if getattr(model, "authority", ...) is None and hasattr(model, "authority"):
        flags.append("authority")

    def _has(name: str) -> bool:
        return getattr(model, name, None) is not None

    if hasattr(model, "answers"):
        for ans in model.answers:  # type: ignore[attr-defined]
            if ans.confidence is None and "confidence" not in flags:
                flags.append("confidence")
            if ans.source_locator is None and "source_locator" not in flags:
                flags.append("source_locator")
    if _has("attempt_number") is False and hasattr(model, "attempt_number"):
        flags.append("attempt")
    grading = getattr(model, "grading", None)
    if hasattr(model, "grading") and grading is None:
        flags.append("score")  # 完全未评分 → score 显式 unknown
    elif grading is not None and grading.score is None:
        flags.append("score")
    return _dedup(flags)


def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# 导出供 catalog/测试引用
__all__ = [
    "PreClassDiagnosticTask",
    "ClassroomTask",
    "TaskAssignment",
    "StudentSubmission",
    "Evidence",
    "ClassLearningProfile",
    "PREP_CONTRACTS_SCHEMA_VERSION",
]
