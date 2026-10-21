"""跨端契约状态机（R13）。

状态是契约语义的一部分：非法迁移必须在纯函数层拒绝（proposal/LLM 只产生候选，
状态推进由确定性规则执行）。每个契约一张迁移表 + 可选跨字段不变式。

铁律：
- 表驱动迁移，禁止散落的 if/else 状态分支；
- LLM 只能建议目标状态，落地必须过 `assert_transition`；乱跳即 ContractStateInvalid。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .errors import ContractStateTransitionInvalid

# ── 任务生命周期（PreClassDiagnosticTask / ClassroomTask 共用）──────────────
DRAFT, PUBLISHED, CLOSED, CANCELLED, ARCHIVED = "draft", "published", "closed", "cancelled", "archived"

TASK_LIFECYCLE: dict[str, tuple[str, ...]] = {
    DRAFT: (PUBLISHED, CANCELLED),
    PUBLISHED: (CLOSED,),
    CLOSED: (ARCHIVED,),
    CANCELLED: (ARCHIVED,),
    ARCHIVED: (),
}

# ── TaskAssignment 下发生命周期 ──────────────────────────────────────────────
PENDING, PROVISIONED, ACTIVE, DONE, ASSIGN_CLOSED, ASSIGN_CANCELLED = (
    "pending", "provisioned", "active", "done", "closed", "cancelled",
)
ASSIGNMENT_LIFECYCLE: dict[str, tuple[str, ...]] = {
    PENDING: (PROVISIONED, ASSIGN_CANCELLED),
    PROVISIONED: (ACTIVE, ASSIGN_CANCELLED),
    ACTIVE: (DONE, ASSIGN_CLOSED, ASSIGN_CANCELLED),
    DONE: (ASSIGN_CLOSED,),
    ASSIGN_CLOSED: (ARCHIVED,),
    ASSIGN_CANCELLED: (ARCHIVED,),
}

# ── StudentSubmission 提交生命周期 ───────────────────────────────────────────
DRAFTED, SUBMITTED, LATE, RETURNED, RESUBMITTED, GRADING, GRADED, SUB_ARCHIVED = (
    "drafted", "submitted", "late", "returned", "resubmitted", "grading", "graded", "archived",
)
SUBMISSION_LIFECYCLE: dict[str, tuple[str, ...]] = {
    DRAFTED: (SUBMITTED,),
    SUBMITTED: (LATE, RETURNED, GRADING, SUB_ARCHIVED),
    LATE: (RETURNED, GRADING, SUB_ARCHIVED),
    RETURNED: (RESUBMITTED, SUB_ARCHIVED),
    RESUBMITTED: (LATE, RETURNED, GRADING, SUB_ARCHIVED),
    GRADING: (GRADED, RETURNED, SUB_ARCHIVED),
    GRADED: (RETURNED, SUB_ARCHIVED),
    SUB_ARCHIVED: (),
}

# ── ClassLearningProfile 画像生命周期 ────────────────────────────────────────
COLLECTING, PREVIEW, PROF_PUBLISHED, PROF_ARCHIVED = "collecting", "preview", "published", "archived"
PROFILE_LIFECYCLE: dict[str, tuple[str, ...]] = {
    COLLECTING: (PREVIEW, PROF_ARCHIVED),
    PREVIEW: (PROF_PUBLISHED, COLLECTING, PROF_ARCHIVED),
    PROF_PUBLISHED: (COLLECTING, PROF_ARCHIVED),
    PROF_ARCHIVED: (),
}

# 契约名 → 迁移表（reader/adapter 统一入口）
_STATE_TABLES: dict[str, dict[str, tuple[str, ...]]] = {
    "PreClassDiagnosticTask": TASK_LIFECYCLE,
    "ClassroomTask": TASK_LIFECYCLE,
    "TaskAssignment": ASSIGNMENT_LIFECYCLE,
    "StudentSubmission": SUBMISSION_LIFECYCLE,
    "Evidence": None,  # Evidence 无业务迁移（追加写），状态仅 draft/persisted 记录
    "ClassLearningProfile": PROFILE_LIFECYCLE,
}


def validate_state(contract_kind: str, state: str) -> None:
    table = _STATE_TABLES.get(contract_kind)
    if table is not None and state not in table:
        raise ContractStateTransitionInvalid(contract_kind, state, "<none>")


def assert_transition(contract_kind: str, current: str, target: str) -> None:
    """校验合法迁移；非法 → ContractStateTransitionInvalid（40901）。"""
    table = _STATE_TABLES.get(contract_kind)
    if table is None:
        return  # 无迁移表的契约（Evidence）不设状态守卫
    if current not in table:
        raise ContractStateTransitionInvalid(contract_kind, current, target)
    allowed = table[current]
    if target not in allowed and current != target:
        raise ContractStateTransitionInvalid(contract_kind, current, target)


@dataclass(frozen=True)
class SubmissionRule:
    """StudentSubmission 跨字段不变式（attempt/revision 单调性由调用方保证，规则给出约束）。"""

    contract_kind: str = "StudentSubmission"


SubmissionTransitionGuard: Callable[[str, str, str], None] = assert_transition
