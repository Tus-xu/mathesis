"""Butler Kernel v2 阶段 4.1c：统一 16 工具 Registry（build_m2_registry）

覆盖：
- 精确 16 项（9 本地领域 + 7 星辰远程），无重复名称；
- F14 / wf_intent_router / wf_socratic_chat / lean.* 缺席；
- 学生可见工具按 scene 正确过滤（教师/科研/管理角色不可见）；
- Planner prompt 能看到对应 scene 的名称、描述、输入字段；
- 所有 ToolDefinition.timeout_s ∈ (0, 20]（20 秒总预算护栏）。
"""

import uuid

import pytest

from app.butler.contracts import ActorContext, ActorRole, ButlerRequest
from app.butler.model_adapter import build_planning_prompt
from app.butler.registry import ToolRegistry, build_m2_registry

DOMAIN_TOOL_NAMES = [
    "student.profile.read",
    "student.errors.due.read",
    "student.mastery.weak_points.read",
    "knowledge.prerequisites.read",
    "practice.variants.create",
    "learning.path.recommend",
    "ui.route.build",
    "assignment.list",
    "review.plan.read",
]

WORKFLOW_TOOL_NAMES = [
    "xingchen.document_understand",
    "xingchen.speech_to_latex",
    "xingchen.web_search",
    "xingchen.smart_quiz",
    "xingchen.solution_pregrade",
    "xingchen.error_analysis",
    "xingchen.course_preprocess",
]

ALL_16 = DOMAIN_TOOL_NAMES + WORKFLOW_TOOL_NAMES


def _request(scene: str) -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=uuid.uuid4(), role=ActorRole.STUDENT),
        message="test",
        scene=scene,
        client_request_id="crid-1",
    )


@pytest.fixture(scope="module")
def m2_registry() -> ToolRegistry:
    return build_m2_registry()


def test_m2_registry_exact_16_tools(m2_registry: ToolRegistry):
    assert len(m2_registry.names()) == 16
    assert set(m2_registry.names()) == set(ALL_16)


def test_m2_registry_no_duplicates(m2_registry: ToolRegistry):
    names = list(m2_registry.names())
    assert len(names) == len(set(names))


def test_m2_registry_no_forbidden_tools(m2_registry: ToolRegistry):
    names = m2_registry.names()
    for forbidden in (
        "wf_verify_derivation",
        "research.verify_derivation",
        "wf_intent_router",
        "wf_socratic_chat",
    ):
        assert forbidden not in names
    assert not any(n.startswith("lean.") for n in names)
    assert not any(n.startswith("wf_") for n in names)
    assert not any(n.startswith("research.") for n in names)


def test_m2_registry_scene_filtering(m2_registry: ToolRegistry):
    # dashboard 场景：本地 dashboard 工具 + web_search 可见
    dashboard = m2_registry.visible_to(ActorRole.STUDENT, "student.dashboard")
    assert "student.profile.read" in dashboard
    assert "review.plan.read" in dashboard
    assert "xingchen.web_search" in dashboard
    # 非学生角色看不到任何工具
    for role in (ActorRole.TEACHER, ActorRole.RESEARCHER, ActorRole.ADMIN):
        assert m2_registry.visible_to(role, "student.dashboard") == ()
    # 每个工具至少在其一个声明场景对 student 可见
    for name in ALL_16:
        tool = m2_registry.get(name)
        assert any(
            name in m2_registry.visible_to(ActorRole.STUDENT, scene)
            for scene in tool.allowed_scenes
        )


def test_m2_registry_planner_prompt_sees_scene_tools(m2_registry: ToolRegistry):
    """Planner prompt 能看到对应 scene 的工具名称、描述、输入字段。"""
    prompt = build_planning_prompt(_request("student.practice"), None, m2_registry)
    # practice 场景可见工具：名称 + 描述 + 输入字段名
    for name in ("practice.variants.create", "xingchen.smart_quiz", "xingchen.solution_pregrade"):
        tool = m2_registry.get(name)
        assert name in prompt
        assert tool.description[:80] in prompt
        for field in list(tool.input_model.model_fields)[:8]:
            assert field in prompt
    # 不可见工具不出现（profile.read 不在 practice 场景）
    assert "student.profile.read" not in prompt
    assert "assignment.list" not in prompt


def test_m2_registry_all_timeouts_within_budget(m2_registry: ToolRegistry):
    """所有 Butler ToolDefinition.timeout_s ∈ (0, 20]（20 秒总预算护栏）。"""
    for name in m2_registry.names():
        tool = m2_registry.get(name)
        assert 0 < tool.timeout_s <= 20, f"{name} timeout_s={tool.timeout_s}"
