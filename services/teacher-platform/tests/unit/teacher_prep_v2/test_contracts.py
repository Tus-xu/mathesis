"""R13 契约语义单元测试：六个契约 + 状态机 + 容忍读取器。

覆盖验收重点：ID 稳定 / 状态机非法迁移拒绝 / 缺失 confidence/attempt/provenance
显式 unknown / 新旧版本双读 / 旧 consumer 兼容（别名 TaskContract/Submission）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.teacher_prep_v2.contracts import reader as reader_mod
from app.teacher_prep_v2.contracts.catalog import CONTRACT_MODELS
from app.teacher_prep_v2.contracts.errors import (
    ContractLegacyAliasUnknown,
    ContractStateTransitionInvalid,
    ContractVersionUnsupported,
)
from app.teacher_prep_v2.contracts.models import (
    ClassLearningProfile,
    ClassroomTask,
    Evidence,
    PreClassDiagnosticTask,
    StudentSubmission,
    TaskAssignment,
)
from app.teacher_prep_v2.contracts.reader import ALIAS_KINDS, read_contract
from app.teacher_prep_v2.contracts.state_machines import assert_transition, validate_state

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "teacher_prep_v2" / "contracts" / "fixtures"

VALID = {
    "PreClassDiagnosticTask": "diagnostic_full.json",
    "ClassroomTask": "classroom_full.json",
    "TaskAssignment": "assignment_provisioned.json",
    "StudentSubmission": "submission_graded.json",
    "Evidence": "evidence_photo.json",
    "ClassLearningProfile": "profile_full.json",
}


def _load(kind: str, name: str) -> dict:
    path = FIXTURES / kind / "valid" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind,model", CONTRACT_MODELS.items())
def test_kind_enum_matches_registry(kind: str, model) -> None:
    assert model.model_fields["kind"].default in (
        "PRE_CLASS_DIAGNOSTIC", "CLASSROOM", "TASK_ASSIGNMENT", "STUDENT_SUBMISSION", "EVIDENCE",
        "CLASS_LEARNING_PROFILE",
    )
    assert model.model_fields["schema_version"].default == "1.0.0"


@pytest.mark.parametrize("kind,name", list(VALID.items()))
def test_valid_fixture_parses(kind: str, name: str) -> None:
    data = _load(kind, name)
    obj = CONTRACT_MODELS[kind].model_validate(data)
    assert obj.id  # 稳定 id 必现
    assert obj.schema_version == "1.0.0"
    dump = obj.model_dump()
    assert CONTRACT_MODELS[kind].model_validate(dump).id == obj.id  # round-trip 稳定


def test_task_derived_invariants() -> None:
    task = PreClassDiagnosticTask.model_validate(_load("PreClassDiagnosticTask", "diagnostic_full.json"))
    assert task.status == "published"
    assert task.window.due_at > task.window.open_at  # model_validator 保证

    bad = dict(_load("PreClassDiagnosticTask", "diagnostic_full.json"))
    bad["window"]["due_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(ValidationError, match="due_at"):
        PreClassDiagnosticTask.model_validate(bad)

    # 语义唯一性（JSON Schema 无法表达，Pydantic 层钉死）：item_no 重复 / 证据引用重复
    dup = _load("PreClassDiagnosticTask", "diagnostic_full.json")
    dup["id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa0d"
    dup["items"].append(dict(dup["items"][0]))
    with pytest.raises(ValidationError, match="item_no"):
        PreClassDiagnosticTask.model_validate(dup)

    dup_ev = _load("StudentSubmission", "submission_graded.json")
    dup_ev["id"] = "cccccccc-cccc-4ccc-8ccc-cccccccccc09"
    dup_ev["evidence_refs"] = ["dddddddd-dddd-4ddd-8ddd-dddddddddd01"] * 2
    with pytest.raises(ValidationError, match="重复引用"):
        StudentSubmission.model_validate(dup_ev)


def test_classroom_task_requires_session() -> None:
    data = _load("ClassroomTask", "classroom_full.json")
    data.pop("session_id")
    with pytest.raises(ValidationError, match="session_id"):
        ClassroomTask.model_validate(data)


def test_assignment_target_invariants() -> None:
    data = _load("TaskAssignment", "assignment_provisioned.json")
    data["target"] = {"path": "SPECIFIC_STUDENTS", "student_ids": []}
    with pytest.raises(ValidationError, match="student_ids"):
        TaskAssignment.model_validate(data)

    data["target"] = {"path": "SPECIFIC_STUDENTS", "student_ids": ["55555555-5555-4555-8555-555555555555"] * 2}
    with pytest.raises(ValidationError, match="重复"):
        TaskAssignment.model_validate(data)


def test_evidence_requires_submission_ref() -> None:
    data = _load("Evidence", "evidence_photo.json")
    data["submission_ref"] = {"external_submission_id": None, "contract_submission_id": None}
    with pytest.raises(ValidationError, match="submission_ref"):
        Evidence.model_validate(data)


@pytest.mark.parametrize(
    "kind,current,target,expect_ok",
    [
        ("PreClassDiagnosticTask", "draft", "published", True),
        ("PreClassDiagnosticTask", "published", "draft", False),   # 回退禁止
        ("ClassroomTask", "draft", "cancelled", True),
        ("TaskAssignment", "active", "done", True),
        ("TaskAssignment", "done", "active", False),
        ("StudentSubmission", "graded", "returned", True),
        ("StudentSubmission", "returned", "resubmitted", True),
        ("StudentSubmission", "returned", "graded", False),        # 跳过重交直接打分禁止
        ("StudentSubmission", "submitted", "late", True),
        ("ClassLearningProfile", "collecting", "preview", True),
        ("ClassLearningProfile", "archived", "published", False),
    ],
)
def test_state_machine_transitions(kind, current, target, expect_ok) -> None:
    validate_state(kind, current)
    if expect_ok:
        assert_transition(kind, current, target)
    else:
        with pytest.raises(ContractStateTransitionInvalid):
            assert_transition(kind, current, target)


def test_unknown_semantics_explicit() -> None:
    """缺失 provenance/attempt/confidence/score 必须显式 unknown，不得伪造。"""
    data = _load("StudentSubmission", "submission_unknown_semantics.json")
    sub = StudentSubmission.model_validate(data)
    assert sub.attempt_number is None
    assert sub.provenance is None
    assert sub.grading is None
    assert "provenance" in sub.unknown_flags
    assert "attempt" in sub.unknown_flags
    assert "score" in sub.unknown_flags
    assert "confidence" in sub.unknown_flags
    # 不缺时不得误标的相反面
    full = StudentSubmission.model_validate(_load("StudentSubmission", "submission_graded.json"))
    assert full.provenance is not None
    assert full.attempt_number == 1
    assert full.unknown_flags == [] or "attempt" not in full.unknown_flags


def test_evidence_unknown_source_locator() -> None:
    data = _load("Evidence", "evidence_ocr_unknown.json")
    ev = Evidence.model_validate(data)
    assert ev.source_locator is None
    assert "source_locator" in ev.unknown_flags


def test_late_status_requires_computable_late_seconds() -> None:
    data = _load("StudentSubmission", "submission_graded.json")
    data["id"] = "cccccccc-cccc-4ccc-8ccc-cccccccccc05"
    data["status"] = "late"
    data["due_at"] = None
    data["late_seconds"] = None
    with pytest.raises(ValidationError, match="late"):
        StudentSubmission.model_validate(data)


def test_auto_late_seconds_computed() -> None:
    data = _load("StudentSubmission", "submission_graded.json")
    data["id"] = "cccccccc-cccc-4ccc-8ccc-cccccccccc06"
    data["submitted_at"] = "2026-09-11T09:00:00+00:00"
    data["due_at"] = "2026-09-11T08:00:00+00:00"
    data["status"] = "late"
    data["late_seconds"] = None
    sub = StudentSubmission.model_validate(data)
    assert sub.late_seconds == 3600
    assert sub.status == "late"


# ── 容忍读取器：旧 consumer 兼容 + 新旧双读 ───────────────────────────────────


def test_reader_legacy_alias_submission() -> None:
    """旧名 Submission（无 schema_version / 无 provenance / 扁平 task_id）→ 归一 StudentSubmission。"""
    payload = {
        "kind": "STUDENT_SUBMISSION",
        "id": "cccccccc-cccc-4ccc-8ccc-cccccccccc07",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "student_id": "55555555-5555-4555-8555-555555555555",
        "class_id": "22222222-2222-4222-8222-222222222222",
        "task_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "assignment_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbb001",
        "client_submit_id": "legacy-sub-0001",
        "submitted_at": "2026-09-11T08:00:00+00:00",
    }
    result = read_contract("Submission", payload)
    assert result.legacy_alias is True
    assert isinstance(result.data, StudentSubmission)
    assert result.data.task_ref.task_id == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    assert "attempt" in result.data.unknown_flags
    assert "provenance" in result.data.unknown_flags
    assert "authority" in result.data.unknown_flags  # 旧数据无 8000 锚点 → 显式 unknown


def test_reader_legacy_alias_task_contract_discriminates() -> None:
    """旧名 TaskContract：依 payload.kind 判别 CLASSROOM / 默认诊断。"""
    tenant = "11111111-1111-4111-8111-111111111111"
    class_id = "22222222-2222-4222-8222-222222222222"
    owner = "44444444-4444-4444-8444-444444444444"
    window = {"open_at": "2026-09-10T09:00:00+00:00", "due_at": "2026-09-10T09:40:00+00:00"}
    classroom_payload = {
        "kind": "CLASSROOM",
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaacc05",
        "tenant_id": tenant,
        "class_id": class_id,
        "session_id": "33333333-3333-4333-8333-333333333333",
        "owner_id": owner,
        "title": "旧式课堂任务",
        "window": window,
    }
    res = read_contract("TaskContract", classroom_payload)
    assert isinstance(res.data, ClassroomTask)

    diag_payload = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaacc06",
        "tenant_id": tenant,
        "class_id": class_id,
        "owner_id": owner,
        "title": "旧式诊断",
        "window": window,
    }
    res2 = read_contract("TaskContract", diag_payload)
    assert isinstance(res2.data, PreClassDiagnosticTask)


def test_reader_unknown_kind_rejected() -> None:
    with pytest.raises(ContractLegacyAliasUnknown):
        read_contract("Nonsense", {"id": "x"})


def test_reader_future_major_fail_closed() -> None:
    payload = dict(_load("StudentSubmission", "submission_graded.json"))
    payload["schema_version"] = "3.0.0"
    with pytest.raises(ContractVersionUnsupported):
        read_contract("StudentSubmission", payload)


def test_reader_same_major_tolerant_of_minor_extra() -> None:
    """更高 minor + 未知字段 → 容忍（extra=ignore），不破坏旧读取。"""
    payload = dict(_load("Evidence", "evidence_photo.json"))
    payload["schema_version"] = "1.9.0"
    payload["future_field"] = {"a": 1}
    res = read_contract("Evidence", payload)
    assert res.data.id == payload["id"]


def test_dual_read_same_id() -> None:
    """新旧版本双读：同一提交新形状与旧别名形状读出同一稳定 id。"""
    new_payload = _load("StudentSubmission", "submission_graded.json")
    legacy_payload = {
        "kind": "STUDENT_SUBMISSION",
        "id": new_payload["id"],
        "tenant_id": new_payload["tenant_id"],
        "student_id": new_payload["student_id"],
        "class_id": new_payload["class_id"],
        "task_id": new_payload["task_ref"]["task_id"],
        "assignment_id": new_payload["assignment_ref"]["task_assignment_id"],
        "client_submit_id": new_payload["client_submit_id"],
        "submitted_at": new_payload["submitted_at"],
    }
    assert reader_mod.dual_read_identical("StudentSubmission", new_payload, legacy_payload)


def test_profile_aggregate_defaults() -> None:
    data = _load("ClassLearningProfile", "profile_minimal.json")
    profile = ClassLearningProfile.model_validate(data)
    assert profile.aggregation.submission_count == 0
    assert profile.status == "collecting"


def test_aliases_are_internal_only() -> None:
    """旧别名只能在 reader，不得成为第二套事实（不在注册目录中）。"""
    assert "TaskContract" not in CONTRACT_MODELS
    assert "Submission" not in CONTRACT_MODELS
    assert set(ALIAS_KINDS) == {"TaskContract", "Submission"}
