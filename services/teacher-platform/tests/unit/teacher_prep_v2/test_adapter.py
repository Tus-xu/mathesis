"""R13 Adapter 测试：稳定映射 / 重试 / 重复提交 / 迟交重交 / membership 拒绝。

8000 权威以 FakeApiTransport 模拟（不改 8000 任何路径；8000 零改动回归验证见
FINAL-GATE 证据：services/api 目录 git 零 diff）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.teacher_prep_v2.adapters.api8000 import (
    Api8000Adapter,
    FakeApiTransport,
)
from app.teacher_prep_v2.adapters.errors import requires_retry
from app.teacher_prep_v2.adapters.mapping import (
    InMemoryMappingRegistry,
    api_submission_to_contract,
    apply_resubmit,
    assignment_mapping_key,
    compute_late_seconds,
    derive_contract_id,
    submission_mapping_key,
)
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractIdempotencyConflict,
    ContractMappingMissing,
    ContractMembershipDenied,
    ContractStateTransitionInvalid,
)
from app.teacher_prep_v2.contracts.models import StudentSubmission, TaskAssignment

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "teacher_prep_v2" / "contracts" / "fixtures"
T = UTC


def _load(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / kind / "valid" / name).read_text(encoding="utf-8"))


def _assignment() -> TaskAssignment:
    return TaskAssignment.model_validate(_load("TaskAssignment", "assignment_provisioned.json"))


def _submission_due_late() -> StudentSubmission:
    data = _load("StudentSubmission", "submission_graded.json")
    data["id"] = "cccccccc-cccc-4ccc-8ccc-cccccccccc08"
    data["status"] = "graded"
    return StudentSubmission.model_validate(data)


# ── 稳定映射（ID 永不重建） ────────────────────────────────────────────────────


def test_contract_id_derivation_is_stable_unique() -> None:
    external = "c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0c101"
    first = derive_contract_id(external)
    second = derive_contract_id(external)
    assert first == second
    assert len(first.split("-")) == 5
    assert derive_contract_id(external) != derive_contract_id("c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0c102")


def test_assignment_mapping_key_stable_across_revision() -> None:
    a1 = _assignment()
    a2 = _assignment()
    assert assignment_mapping_key(a1) == assignment_mapping_key(a2)
    # 语义变化（due_at）→ 键变化（新下发，必须新 8000 assignment）
    a3 = a2.model_copy(update={"due_at": a2.due_at + timedelta(days=1)})
    assert assignment_mapping_key(a1) != assignment_mapping_key(a3)


def test_submission_mapping_key_stable() -> None:
    k1 = submission_mapping_key("C0C0C0C0-C0C0-4C0C-8C0C-C0C0C0C0C101")
    k2 = submission_mapping_key("c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0c101")
    assert k1 == k2  # 大小写不敏感


# ── 重试 / 重复提交（幂等下发） ────────────────────────────────────────────────


async def test_create_assignment_retry_reuses_same_external_id() -> None:
    registry = InMemoryMappingRegistry()
    calls = {"n": 0}
    external_id = "c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0001"

    def handler(method, path, token, json_body):
        calls["n"] += 1
        assert json_body["client_assignment_id"]  # 幂等键必传
        return (200, {"id": external_id})

    transport = FakeApiTransport()
    transport.handler = handler
    adapter = Api8000Adapter(transport, registry)

    a = _assignment()
    ref1 = await adapter.create_assignment(token="tok-1", assignment=a, creator_id=a.owner_id)
    ref2 = await adapter.create_assignment(token="tok-1", assignment=a, creator_id=a.owner_id)
    assert ref1.external_id == ref2.external_id == external_id
    assert calls["n"] == 1  # 第二次命中映射，不再打 8000
    # 幂等键稳定（与 mapping key 一致）
    assert ref1.key == assignment_mapping_key(a)


async def test_create_assignment_conflict_reads_back_existing() -> None:
    registry = InMemoryMappingRegistry()
    external_id = "c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0aa02"
    posted_key: dict[str, str] = {}

    def handler(method, path, token, json_body):
        if method == "POST":
            posted_key["key"] = json_body["client_assignment_id"]
            return (409, {"detail": "client_assignment_id 已存在"})
        return (200, {
            "items": [{
                "id": external_id,
                "class_id": "22222222-2222-4222-8222-222222222222",
                "creator_id": "44444444-4444-4444-8444-444444444444",
                "title": "对账读回",
                "type": "quiz",
                "client_assignment_id": posted_key.get("key"),
            }]
        })

    transport = FakeApiTransport()
    transport.handler = handler
    adapter = Api8000Adapter(transport, registry)
    ref = await adapter.create_assignment(token="tok", assignment=_assignment(), creator_id="44444444-4444-4444-8444-444444444444")
    assert ref.external_id == external_id


async def test_registry_rejects_conflicting_external_id() -> None:
    registry = InMemoryMappingRegistry()
    await registry.bind("s", "k", "ext-1")
    with pytest.raises(ContractIdempotencyConflict):
        await registry.ensure("s", "k", "ext-2")


# ── 迟交 / 重交 ────────────────────────────────────────────────────────────────


def test_late_seconds_only_positive() -> None:
    due = datetime(2026, 9, 11, 8, 0, tzinfo=T)
    assert compute_late_seconds(datetime(2026, 9, 11, 9, 0, tzinfo=T), due) == 3600
    assert compute_late_seconds(datetime(2026, 9, 11, 7, 0, tzinfo=T), due) is None  # 提前不算
    assert compute_late_seconds(datetime(2026, 9, 11, 8, 0, tzinfo=T), None) is None  # 无 due 不算


def test_resubmit_increments_attempt_and_revision() -> None:
    sub = _submission_due_late()
    sub = sub.model_copy(update={"status": "returned", "attempt_number": 1})
    resubmitted = apply_resubmit(sub, max_attempts=2)
    assert resubmitted.attempt_number == 2
    assert resubmitted.revision == sub.revision + 1
    assert resubmitted.status == "resubmitted"


def test_resubmit_exceeding_max_attempts_rejected() -> None:
    sub = _submission_due_late().model_copy(update={"status": "returned", "attempt_number": 2})
    with pytest.raises(ContractIdempotencyConflict, match="重交次数"):
        apply_resubmit(sub, max_attempts=2)


def test_illegal_transition_rejected_by_adapter_rule() -> None:
    sub = _submission_due_late().model_copy(update={"status": "graded"})
    with pytest.raises(ContractStateTransitionInvalid):
        apply_resubmit(sub, max_attempts=2)  # graded → resubmitted 非法


def test_mark_late() -> None:
    sub = _submission_due_late().model_copy(update={"status": "submitted"})
    late = sub.model_copy(update={"status": "late", "late_seconds": 60})
    assert late.status == "late"
    assert late.late_seconds == 60


# ── membership：跨班访问一律拒绝（8000 当前成员关系裁决） ──────────────────────


async def test_membership_denied_for_non_member() -> None:
    transport = FakeApiTransport()

    def handler(method, path, token, json_body):
        return (403, {"detail": "not a member"})

    transport.handler = handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    with pytest.raises(ContractMembershipDenied):
        await adapter.require_class_membership(token="tok", class_id="22222222-2222-4222-8222-222222222222")


async def test_membership_denied_for_inactive_or_student_role() -> None:
    transport = FakeApiTransport()

    def handler(method, path, token, json_body):
        if token == "tok-student":
            return (200, {"membership": {"user_id": "55555555-5555-4555-8555-555555555555",
                                         "member_role": "student", "confirmed": True, "deleted": False}})
        return (200, {"membership": {"user_id": "55555555-5555-4555-8555-555555555555",
                                     "member_role": "teacher", "confirmed": False, "deleted": True}})

    transport.handler = handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    with pytest.raises(ContractMembershipDenied, match="角色"):
        await adapter.require_class_membership(token="tok-student", class_id="22222222-2222-4222-8222-222222222222")
    with pytest.raises(ContractMembershipDenied, match="非当前有效成员"):
        await adapter.require_class_membership(token="tok-teacher", class_id="22222222-2222-4222-8222-222222222222")


async def test_authority_unreachable_fails_closed_and_is_retryable() -> None:
    transport = FakeApiTransport()

    def handler(method, path, token, json_body):
        return (503, None)  # 8000 过载：fail closed，不伪造成功

    transport.handler = handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    with pytest.raises(ContractAuthorityUnreachable):
        await adapter.create_assignment(token="tok", assignment=_assignment(), creator_id="44444444-4444-4444-8444-444444444444")
    assert requires_retry(ContractAuthorityUnreachable("x"))
    assert not requires_retry(ContractMembershipDenied("x"))


# ── 8000 Submission 行 → 契约（unknown 语义 + 稳定 ID） ───────────────────────


def test_api_submission_to_contract_stable_and_unknown() -> None:
    from app.teacher_prep_v2.adapters.dto import ApiSubmissionItem, ApiSubmissionRow

    row = ApiSubmissionRow(
        id="c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c0c201",
        user_id="55555555-5555-4555-8555-555555555555",
        client_submit_id="sub-8000-row-0001",
        status="pending_review",
        created_at=datetime(2026, 9, 11, 7, 55, tzinfo=T),
        items=[ApiSubmissionItem(item_no=1, answer_text="x²+1", verdict="pending_review", score=None)],
    )
    task_ref = {"task_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "task_kind": "PRE_CLASS_DIAGNOSTIC"}
    ctx = dict(
        tenant_id="11111111-1111-4111-8111-111111111111",
        class_id="22222222-2222-4222-8222-222222222222",
        session_id=None,
        task_ref=task_ref,
        task_assignment_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbb001",
    )
    contract = api_submission_to_contract(row, **ctx)
    assert contract.authority.external_id == str(row.id)
    # 同一行两次转换 → 同一契约 id（双读稳定性）
    second = api_submission_to_contract(row, **ctx)
    assert second.id == contract.id
    # 缺失评分/置信/尝试/来源 → 显式 unknown
    assert contract.attempt_number is None
    assert contract.provenance is None
    assert "attempt" in contract.unknown_flags
    assert "provenance" in contract.unknown_flags
    assert "score" in contract.unknown_flags


def test_mapping_missing_on_unstable_external() -> None:
    from app.teacher_prep_v2.adapters.mapping import MappedRef, ensure_same_external

    ref = MappedRef(scope="s", key="k", external_id="ext-a")
    ensure_same_external(ref, "ext-a")  # 一致放行
    with pytest.raises(ContractMappingMissing):
        ensure_same_external(ref, "ext-b")
