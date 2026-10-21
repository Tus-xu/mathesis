"""稳定映射 + 纯映射函数（R13）：契约 ⇄ 8000 Assignment/Submission。

重试/重复提交/迟交重交/ID 稳定的核心规则：
1. 稳定映射键 = sha256(canonical(命名空间 + 语义指纹))；
   - 任务下发：fingerprint(tenant, class, owner, task_ref, target, due, attempts)
   - 提交：fingerprint(8000 submission id) —— 同一行永远映射同一契约 id（不重建）；
2. 幂等提交：client_assignment_id / client_submit_id 为 8000 侧既有幂等键，
   重试必须复用同一键（同 key 同 payload → 首响应重放；同 key 异 payload → 409）；
3. 迟交：late_seconds = submitted_at - due_at（>0 才标记 late，负值钳 0 不算）；
4. 重交：attempt_number +1 且 revision +1，状态经 RETURNED → RESUBMITTED，
   必须过 state_machines.assert_transition（LLM 只给建议，规则定状态）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.teacher_prep_v2.adapters.dto import (
    ApiAssignmentCreate,
    ApiAssignmentRow,
    ApiSubmissionItem,
    ApiSubmissionRow,
)
from app.teacher_prep_v2.contracts.errors import ContractIdempotencyConflict, ContractMappingMissing
from app.teacher_prep_v2.contracts.models import StudentSubmission, TaskAssignment
from app.teacher_prep_v2.contracts.state_machines import assert_transition

# ── 稳定映射（scope + key → external id）──────────────────────────────────────


@dataclass(frozen=True)
class MappedRef:
    scope: str
    key: str
    external_id: str
    mapping_version: int = 1


class MappingRegistry(Protocol):
    """契约 id ↔ 8000 id 稳定映射仓库（生产实现 = 8100 projection 表 + 事务唯一约束）。"""

    async def resolve(self, scope: str, key: str) -> MappedRef | None: ...

    async def bind(self, scope: str, key: str, external_id: str, *, mapping_version: int = 1) -> MappedRef: ...

    async def ensure(self, scope: str, key: str, external_id: str, *, mapping_version: int = 1) -> MappedRef: ...


class InMemoryMappingRegistry:
    """内存实现（R13 测试/dev；生产换 DB projection，接口不变）。"""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], MappedRef] = {}

    async def resolve(self, scope: str, key: str) -> MappedRef | None:
        return self._store.get((scope, key))

    async def bind(self, scope: str, key: str, external_id: str, *, mapping_version: int = 1) -> MappedRef:
        ref = MappedRef(scope=scope, key=key, external_id=str(external_id), mapping_version=mapping_version)
        self._store[(scope, key)] = ref
        return ref

    async def ensure(self, scope: str, key: str, external_id: str, *, mapping_version: int = 1) -> MappedRef:
        """幂等绑定：并发下同一 key 只允许同一 external_id；不同 → 409。"""
        existing = self._store.get((scope, key))
        if existing is not None:
            if existing.external_id != str(external_id):
                raise ContractIdempotencyConflict(
                    f"映射键 {scope}/{key} 已被占用（已绑 {existing.external_id}，收到 {external_id}）"
                )
            return existing
        return await self.bind(scope, key, external_id, mapping_version=mapping_version)


# ── 规范指纹 ──────────────────────────────────────────────────────────────────


def _canonical(obj: Any) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + _canonical(v)
            for k, v in sorted(obj.items(), key=lambda kv: json.dumps(kv[0], ensure_ascii=False))
        ) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_canonical(v) for v in obj) + "]"
    if isinstance(obj, datetime):
        return obj.isoformat()
    return json.dumps(obj, ensure_ascii=False, default=str)


def stable_digest(namespace: str, payload: Any) -> str:
    return hashlib.sha256(f"{namespace}\x00{_canonical(payload)}".encode()).hexdigest()


def assignment_mapping_key(a: TaskAssignment) -> str:
    """任务下发稳定键：同一语义下发内容永远映射到同一 8000 assignment。"""
    payload = {
        "tenant_id": a.tenant_id,
        "class_id": a.class_id,
        "owner_id": a.owner_id,
        "task_id": a.task_ref.task_id,
        "task_kind": a.task_ref.task_kind,
        "target": a.target.path,
        "student_ids": sorted(a.target.student_ids),
        "due_at": a.due_at.isoformat(),
        "attempts": a.attempts_allowed,
        "late_policy": {"allow_late": a.late_policy.allow_late, "grace_minutes": a.late_policy.grace_minutes},
    }
    return stable_digest("prep:assignment", payload)


def submission_mapping_key(external_submission_id: Any) -> str:
    """提交稳定键：以 8000 submissions.id 为锚，绝不因重试重建契约 id。"""
    return stable_digest("prep:submission", str(external_submission_id).lower())


def derive_contract_id(external_id: str, salt: str = "prep:v1") -> str:
    """由外部 id 派生稳定的 UUID v5 契约 id（同一外部 id 永远同 id）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{salt}:{external_id.lower()}"))


# ── 映射函数（纯；输入输出均为契约/DTO）────────────────────────────────────────


# tel T3/D2：8100 task_ref.kind → 8000 Assignment.type 语义映射。
# 落 8000 后类型不再丢失：学前诊断 → pre_class（学前作业），课堂任务 → quiz（课堂练习）。
TASK_KIND_TO_API_TYPE: dict[str, str] = {
    "PRE_CLASS_DIAGNOSTIC": "pre_class",
    "CLASSROOM": "quiz",
}


def task_assignment_to_api_create(
    a: TaskAssignment,
    *,
    creator_id: str,
    client_assignment_id: str,
    title: str | None = None,
    assignment_type: str | None = None,
    artifact_id: str | None = None,
    quiz_items: list[dict[str, Any]] | None = None,
) -> ApiAssignmentCreate:
    """TaskAssignment → 8000 assignments 创建载荷（tel T3：对齐 CreateAssignmentRequest）。

    - type：默认由 ``task_ref.task_kind`` 映射（PRE_CLASS_DIAGNOSTIC→pre_class、
      CLASSROOM→quiz）；显式 ``assignment_type`` 仅作调用方覆盖；
    - ``creator_id``：8000 从教师 JWT 裁决，**不进入载荷**（extra=forbid）；
    - ``artifact_id`` / ``quiz_items``：tel F1 起二选一——后者为备课诊断内联题组直发
      （8000 直接物化 Quiz+QuizItems）；两者皆缺 8000 422 拒绝（fail-closed）；
    - ``deadline``：a.due_at（8000 semantics：deadline 截止）；
    - ``client_assignment_id``：稳定幂等键（重试复用，绝不换键）；
    - ``status``/``quiz_id``/``lesson_id``/``source_artifact_id``：由 8000 自行物化/回填，不发。
    """
    if assignment_type is None:
        assignment_type = TASK_KIND_TO_API_TYPE.get(a.task_ref.task_kind, "quiz")
    return ApiAssignmentCreate(
        class_id=a.class_id,
        title=(title or a.id)[:200],
        artifact_id=artifact_id,
        quiz_items=quiz_items,
        deadline=a.due_at,
        type=assignment_type,  # type: ignore[arg-type]
        client_assignment_id=client_assignment_id,
    )


def api_assignment_ref_to_contract_id(row: ApiAssignmentRow) -> str:
    """8000 assignments.id → 稳定契约 id（UUID v5 派生，永不重建）。"""
    return derive_contract_id(str(row.id), salt="prep:assignment")


def compute_late_seconds(submitted_at: datetime, due_at: datetime | None) -> int | None:
    """迟交秒数：due 缺失或未超时 → None（不算晚交，不得以 0 伪造 late）。"""
    if due_at is None:
        return None
    delta = (submitted_at - due_at).total_seconds()
    return int(delta) if delta > 0 else None


def apply_resubmit(current: StudentSubmission, *, max_attempts: int) -> StudentSubmission:
    """重交：状态 RETURNED → RESUBMITTED，attempt_number +1、revision +1。"""
    assert_transition("StudentSubmission", current.status, "resubmitted")
    next_attempt = (current.attempt_number or 1) + 1
    if next_attempt > max_attempts:
        raise ContractIdempotencyConflict(
            f"超过允许重交次数（max={max_attempts}，当前 attempt={current.attempt_number}）"
        )
    return current.model_copy(
        update={
            "status": "resubmitted",
            "attempt_number": next_attempt,
            "revision": current.revision + 1,
        }
    )


def mark_late(current: StudentSubmission) -> StudentSubmission:
    """提交转迟交：SUBMITTED/RESUBMITTED → LATE（晚于 due 时由入口调用）。"""
    assert_transition("StudentSubmission", current.status, "late")
    return current.model_copy(update={"status": "late", "revision": current.revision + 1})


def api_submission_to_contract(
    row: ApiSubmissionRow,
    *,
    tenant_id: str,
    class_id: str,
    task_ref: dict[str, Any],
    task_assignment_id: str,
    session_id: str | None = None,
) -> StudentSubmission:
    """8000 submissions 行 + 明细 → StudentSubmission 契约（ID 稳定派生）。

    unknown 语义（BREAKING-CHANGE-RULES §4）：
    - attempt_number：行内无则 None → unknown_flags 含 attempt；
    - grading.score/confidence：未评分/缺置信 → None → unknown_flags 含 score/confidence；
    - provenance：行来源非 8000 学生入口时缺省 None → unknown_flags 含 provenance。
    """
    late = compute_late_seconds(row.created_at or datetime.now(), None)  # 无 due → 非晚交（unknown）
    return StudentSubmission(
        id=derive_contract_id(str(row.id), salt="prep:submission"),
        authority={"service": "api", "external_id": str(row.id)},
        tenant_id=tenant_id,
        class_id=class_id,
        session_id=session_id,
        student_id=str(row.user_id),
        task_ref=task_ref,
        assignment_ref={"task_assignment_id": task_assignment_id},
        attempt_number=None,
        client_submit_id=row.client_submit_id,
        submitted_at=row.created_at or datetime.now(),
        due_at=None,
        late_seconds=late,
        status="graded" if row.status == "graded" else "submitted",
        answers=[],
        grading=None,
        evidence_refs=[],
        revision=0,
        provenance=None,
        data_retention={"policy": "TERM"},
    )


def map_items_to_answers(items: list[ApiSubmissionItem]) -> list[dict[str, Any]]:
    """8000 submission_items → 契约 AnswerItem（置信缺失 → None，unknown 如实）。"""
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "item_no": it.item_no,
                "answer_text": it.answer_text,
                "source_locator": None,
                "confidence": None,
            }
        )
    return out


def ensure_same_external(existing: MappedRef, external_id: str) -> None:
    if existing.external_id != str(external_id):
        raise ContractMappingMissing(
            f"映射不稳定：scope={existing.scope} key={existing.key} "
            f"已绑 {existing.external_id}，又收到 {external_id}"
        )


__all__ = [
    "MappedRef",
    "MappingRegistry",
    "InMemoryMappingRegistry",
    "assignment_mapping_key",
    "submission_mapping_key",
    "derive_contract_id",
    "task_assignment_to_api_create",
    "api_assignment_ref_to_contract_id",
    "compute_late_seconds",
    "apply_resubmit",
    "mark_late",
    "api_submission_to_contract",
    "map_items_to_answers",
    "ensure_same_external",
    "stable_digest",
]
