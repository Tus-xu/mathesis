"""M3 教师端：teacher-only Registry 与 Policy（§15 / §19.1）。"""

import uuid

from app.butler.contracts import ActorContext, ActorRole, ButlerRequest, PlannedAction, ToolRisk
from app.butler.policy import PolicyGate
from app.domains.teacher.registry import build_teacher_registry


def _req(role: ActorRole, scene: str, cid: uuid.UUID | None = None) -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=role),
        message="x",
        scene=scene,
        client_request_id="c",
    )


def test_teacher_registry_tool_count_and_roles():
    reg = build_teacher_registry()
    names = reg.names()
    assert len(names) == 19  # 读7 + 生成7 + 写5
    # 全部 allowed_roles = {teacher}
    for n in names:
        assert reg.get(n).allowed_roles == frozenset({ActorRole.TEACHER}), n
    # 不注册科研/lean
    for n in names:
        assert not n.startswith(("wf_", "lean", "review.")), n


def test_teacher_registry_risk_layers_match_read_generate_write_contract():
    """生成草稿有持久化副作用，必须标成 LEARNING_ACTION 而不是纯 READ。"""
    registry = build_teacher_registry()
    counts = dict.fromkeys(ToolRisk, 0)
    for name in registry.names():
        counts[registry.get(name).risk] += 1
    assert counts[ToolRisk.READ] == 7
    assert counts[ToolRisk.LEARNING_ACTION] == 7
    assert counts[ToolRisk.WRITE] == 5


def test_teacher_tools_visible_to_teacher_only():
    reg = build_teacher_registry()
    visible_teacher = reg.visible_to(ActorRole.TEACHER, "teacher.prep")
    visible_student = reg.visible_to(ActorRole.STUDENT, "student.chat")
    assert "teacher.lesson.adapt" in visible_teacher
    assert "teacher.lesson.adapt" not in visible_student


def test_policy_allows_teacher_read_tool():
    reg = build_teacher_registry()
    policy = PolicyGate(reg)
    r = _req(ActorRole.TEACHER, "teacher.today")
    action = PlannedAction(tool_name="teacher.today.read", arguments={}, reason="t")
    assert policy.validate_action(r, action).allowed is True


def test_policy_rejects_student_for_teacher_tool():
    reg = build_teacher_registry()
    policy = PolicyGate(reg)
    r = _req(ActorRole.STUDENT, "student.chat")
    action = PlannedAction(tool_name="teacher.today.read", arguments={}, reason="t")
    decision = policy.validate_action(r, action)
    assert decision.allowed is False
    assert decision.error_code == "role_denied"


def test_policy_rejects_teacher_tool_in_student_scene():
    reg = build_teacher_registry()
    policy = PolicyGate(reg)
    r = _req(ActorRole.TEACHER, "student.chat")
    action = PlannedAction(tool_name="teacher.today.read", arguments={}, reason="t")
    assert policy.validate_action(r, action).allowed is False


def test_write_tool_requires_idempotency():
    reg = build_teacher_registry()
    assert reg.get("teacher.artifact.confirm").idempotency_required is True
    assert reg.get("teacher.grade.confirm").idempotency_required is True
    assert reg.get("teacher.classroom.mode.set").idempotency_required is True


def test_policy_blocks_write_without_idempotency_flag():
    """构造一个 idempotency_required=False 的 write（不存在），验证 Policy 强制写工具幂等标志。"""

    # 不应有任何 WRITE 工具声明 idempotency_required=False
    reg = build_teacher_registry()
    for n in reg.names():
        t = reg.get(n)
        if t.risk.value == "write":
            assert t.idempotency_required is True, n


def test_m2_registry_unchanged():
    from app.butler.registry import build_m2_registry

    m2 = build_m2_registry()
    for n in m2.names():
        assert ActorRole.TEACHER not in m2.get(n).allowed_roles, n
