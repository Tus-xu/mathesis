"""Butler Kernel v2 阶段 4A：9 个本地领域工具（Task 7）

参数化红测试覆盖：
- 9 个精确名称全部注册（不多不少）；
- 每个工具 Pydantic 输入输出可验证；
- 角色/场景 allowlist 正确（仅 student，场景 student.*）；
- 学生不能看到教师/科研/管理能力；
- handler 确实调用现有领域服务（mock 断言）；
- 不传模型提供的 user_id（handler 只用 context user_id）；
- 不 commit/rollback（mock db 断言）；
- 异常转稳定结果（db=None / 领域异常 → 稳定 ToolResult，不抛 500）；
- 重复题不出现（supply_variants 走 question_supply 去重链）；
- Registry 中无 F14、lean.*、直接判分/掌握度写工具。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import BaseModel

from app.butler.contracts import (
    ActorContext,
    ActorRole,
    ButlerBudget,
    ButlerRequest,
    PlannedAction,
    ToolRisk,
)
from app.butler.executor import ButlerExecutor, ToolExecutionContext
from app.butler.policy import PolicyGate
from app.butler.registry import ToolRegistry
from app.butler.tools import build_domain_registry, supply_variants

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

EXPECTED_RISK = {
    "student.profile.read": ToolRisk.READ,
    "student.errors.due.read": ToolRisk.READ,
    "student.mastery.weak_points.read": ToolRisk.READ,
    "knowledge.prerequisites.read": ToolRisk.READ,
    "practice.variants.create": ToolRisk.LEARNING_ACTION,
    "learning.path.recommend": ToolRisk.READ,
    "ui.route.build": ToolRisk.READ,
    "assignment.list": ToolRisk.READ,
    "review.plan.read": ToolRisk.READ,
}

# 每个工具实际调用的领域函数（app.butler.tools 命名空间）
DOMAIN_FN = {
    "student.profile.read": "query_profile",
    "student.errors.due.read": "query_due_errors",
    "student.mastery.weak_points.read": "query_weak_points",
    "knowledge.prerequisites.read": "query_kp_deps",
    "practice.variants.create": "supply_variants",
    "learning.path.recommend": "recommend_path",
    "ui.route.build": "build_route",
    "assignment.list": "query_assignments",
    "review.plan.read": "build_review_plan",
}

# 每个工具的合法输出（供 handler 输出校验通过）
VALID_OUTPUTS = {
    "student.profile.read": {
        "composite_score": 60,
        "avg_mastery": 0.5,
        "hint_dependency": 0.2,
        "streak_days": 3,
        "mastery_count": 2,
        "weak_points": [{"kp_code": "MATH-1", "kp_name": "函数", "mastery": 0.4}],
        "error_total": 5,
        "error_due": 2,
    },
    "student.errors.due.read": {
        "items": [
            {
                "record_id": str(uuid.uuid4()),
                "kp_code": "MATH-1",
                "retrievability": 0.3,
                "level": 2,
                "hours_to_forget": 5.0,
            }
        ]
    },
    "student.mastery.weak_points.read": {
        "items": [
            {
                "kp_code": "MATH-1",
                "kp_name": "函数",
                "mastery": 0.4,
                "practice_count": 3,
                "correct_count": 1,
            }
        ]
    },
    "knowledge.prerequisites.read": {
        "kp_code": "MATH-1",
        "kp_name": "函数",
        "prereqs": ["MATH-0"],
    },
    "practice.variants.create": {
        "items": [
            {
                "hash": "abc123",
                "q_type": "choice",
                "stem": "1+1=?",
                "difficulty": "easy",
                "kp_codes": ["MATH-1"],
                "source": "2023卷",
            }
        ]
    },
    "learning.path.recommend": {
        "items": [
            {
                "kp_code": "MATH-1",
                "kp_name": "函数",
                "mastery": 0.4,
                "prereqs": [],
                "type": "专练",
            }
        ]
    },
    "ui.route.build": {"type": "route", "route": "/errors", "label": "错题本"},
    "assignment.list": {
        "total": 1,
        "items": [
            {
                "assignment_id": str(uuid.uuid4()),
                "title": "作业",
                "type": "quiz",
                "deadline": None,
                "status": "published",
                "progress": {"done": 0, "total": 3},
                "overdue": False,
            }
        ],
    },
    "review.plan.read": {
        "tasks": [
            {
                "key": "review_errors",
                "title": "复习 2 道到期错题",
                "why": "2 道错题进入遗忘危险区",
                "est_minutes": 10,
                "benefit": "稳住记忆曲线",
                "route": "/errors",
            }
        ],
        "due_count": 2,
        "streak_days": 3,
    },
}

# 每个工具的合法输入（handler 直接调用用）
VALID_INPUTS = {
    "student.profile.read": {},
    "student.errors.due.read": {"limit": 5},
    "student.mastery.weak_points.read": {"limit": 3},
    "knowledge.prerequisites.read": {"kp_code": "MATH-1"},
    "practice.variants.create": {"kp_code": "MATH-1", "difficulties": ["easy"]},
    "learning.path.recommend": {"top_n": 2},
    "ui.route.build": {"action": "errors"},
    "assignment.list": {"status": "todo"},
    "review.plan.read": {},
}


@pytest.fixture(scope="module")
def domain_registry() -> ToolRegistry:
    return build_domain_registry()


# 领域函数原始返回（mock 返回值）；handler 再包装为最终输出
DOMAIN_RETURNS = {
    "student.profile.read": VALID_OUTPUTS["student.profile.read"],
    "student.errors.due.read": VALID_OUTPUTS["student.errors.due.read"]["items"],
    "student.mastery.weak_points.read": VALID_OUTPUTS["student.mastery.weak_points.read"]["items"],
    "knowledge.prerequisites.read": VALID_OUTPUTS["knowledge.prerequisites.read"],
    "practice.variants.create": VALID_OUTPUTS["practice.variants.create"]["items"],
    "learning.path.recommend": VALID_OUTPUTS["learning.path.recommend"]["items"],
    "ui.route.build": VALID_OUTPUTS["ui.route.build"],
    "assignment.list": VALID_OUTPUTS["assignment.list"],
    "review.plan.read": VALID_OUTPUTS["review.plan.read"],
}


def _request(user_id: uuid.UUID | None = None, scene: str = "student.dashboard") -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(user_id=user_id or uuid.uuid4(), role=ActorRole.STUDENT),
        message="test",
        scene=scene,
        client_request_id="crid-1",
    )


def _ctx(db=None, user_id: uuid.UUID | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=uuid.uuid4(),
        request=_request(user_id),
        db=db,
        idempotency_key="",
    )


def _mock_for(name: str):
    """ui.route.build 走同步 build_route；其余走 async 领域函数。"""
    if name == "ui.route.build":
        return Mock(return_value=DOMAIN_RETURNS[name])
    return AsyncMock(return_value=DOMAIN_RETURNS[name])


# 需要 db 的工具（ui.route.build 为纯路由，不依赖 db）
DB_REQUIRED_TOOLS = [n for n in DOMAIN_TOOL_NAMES if n != "ui.route.build"]


# ---------- 注册 ----------


def test_domain_registry_exact_tool_set(domain_registry: ToolRegistry):
    assert set(domain_registry.names()) == set(DOMAIN_TOOL_NAMES)


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
def test_domain_tool_names_registered(domain_registry: ToolRegistry, name: str):
    assert name in domain_registry.names()


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
def test_domain_tool_io_models_validatable(domain_registry: ToolRegistry, name: str):
    tool = domain_registry.get(name)
    assert issubclass(tool.input_model, BaseModel)
    assert issubclass(tool.output_model, BaseModel)
    # 输入模型可校验合法输入
    tool.input_model.model_validate(VALID_INPUTS[name])
    # 输出模型可校验合法输出
    tool.output_model.model_validate(VALID_OUTPUTS[name])


# ---------- 角色/场景 allowlist ----------


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
def test_domain_tool_role_scene_allowlist(domain_registry: ToolRegistry, name: str):
    tool = domain_registry.get(name)
    assert tool.allowed_roles == frozenset({ActorRole.STUDENT})
    assert all(s.startswith("student.") for s in tool.allowed_scenes)
    assert tool.risk == EXPECTED_RISK[name]
    # 每个工具至少在其一个声明场景对 student 可见
    assert any(
        name in domain_registry.visible_to(ActorRole.STUDENT, scene)
        for scene in tool.allowed_scenes
    )


def test_student_cannot_see_teacher_research_admin_tools(domain_registry: ToolRegistry):
    for name in domain_registry.names():
        assert not name.startswith(("teacher.", "research.", "admin."))
    # 教师/科研/管理角色看不到任何领域工具
    for role in (ActorRole.TEACHER, ActorRole.RESEARCHER, ActorRole.ADMIN):
        assert domain_registry.visible_to(role, "student.dashboard") == ()


# ---------- handler 调用现有领域服务 ----------


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
async def test_handler_calls_domain_service(domain_registry: ToolRegistry, name: str):
    tool = domain_registry.get(name)
    fn_name = DOMAIN_FN[name]
    with patch(f"app.butler.tools.{fn_name}", new=_mock_for(name)) as mock_fn:
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS[name])
    assert mock_fn.call_count == 1
    assert result == VALID_OUTPUTS[name]


# ---------- 不传模型提供的 user_id ----------


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
async def test_handler_ignores_model_provided_user_id(domain_registry: ToolRegistry, name: str):
    """validated_input 携带恶意 user_id 时 handler 必须忽略，只用 context user_id。"""
    tool = domain_registry.get(name)
    fn_name = DOMAIN_FN[name]
    user_id = uuid.uuid4()
    evil = uuid.uuid4()
    with patch(f"app.butler.tools.{fn_name}", new=_mock_for(name)) as mock_fn:
        await tool.handler(_ctx(db=AsyncMock(), user_id=user_id), {**VALID_INPUTS[name], "user_id": evil})
    args = mock_fn.call_args.args
    # 恶意 user_id 绝不进入领域函数
    assert evil not in args
    # 需要 user_id 的工具必须使用 context user_id（kp_deps/route 不消费 user_id）
    if name not in ("knowledge.prerequisites.read", "ui.route.build"):
        assert user_id in args


# ---------- 不 commit / rollback ----------


@pytest.mark.parametrize("name", DOMAIN_TOOL_NAMES)
async def test_handler_does_not_commit_or_rollback(domain_registry: ToolRegistry, name: str):
    tool = domain_registry.get(name)
    fn_name = DOMAIN_FN[name]
    db = AsyncMock()
    with patch(f"app.butler.tools.{fn_name}", new=_mock_for(name)):
        await tool.handler(_ctx(db=db), VALID_INPUTS[name])
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


# ---------- 异常转稳定结果 ----------


@pytest.mark.parametrize("name", DB_REQUIRED_TOOLS)
async def test_db_none_returns_stable_result(domain_registry: ToolRegistry, name: str):
    """db=None → 稳定 ToolResult（不抛 AttributeError / 不 500）。"""
    policy = PolicyGate(domain_registry)
    ex = ButlerExecutor(domain_registry, policy, budget=ButlerBudget())
    scene = next(iter(domain_registry.get(name).allowed_scenes))
    action = PlannedAction(tool_name=name, arguments=VALID_INPUTS[name], reason="test")
    result = await ex.invoke(uuid.uuid4(), _request(scene=scene), action, None)
    assert result.ok is False
    assert result.error_code == "tool_error"
    assert "AttributeError" not in (result.user_message or "")
    assert "Traceback" not in (result.user_message or "")


@pytest.mark.parametrize("name", DB_REQUIRED_TOOLS)
async def test_domain_exception_to_stable_result(domain_registry: ToolRegistry, name: str):
    """领域函数抛异常 → 稳定 ToolResult，不泄漏内部细节。"""
    fn_name = DOMAIN_FN[name]
    policy = PolicyGate(domain_registry)
    ex = ButlerExecutor(domain_registry, policy, budget=ButlerBudget())
    scene = next(iter(domain_registry.get(name).allowed_scenes))
    action = PlannedAction(tool_name=name, arguments=VALID_INPUTS[name], reason="test")
    with patch(
        f"app.butler.tools.{fn_name}",
        new=AsyncMock(side_effect=RuntimeError("db error at D:/secret/path.sql: key=sk-999")),
    ):
        result = await ex.invoke(uuid.uuid4(), _request(scene=scene), action, AsyncMock())
    assert result.ok is False
    assert result.error_code == "tool_error"
    assert "D:/secret/path.sql" not in (result.user_message or "")
    assert "sk-999" not in (result.user_message or "")
    assert "Traceback" not in (result.user_message or "")


# ---------- 重复题不出现 ----------


async def test_supply_variants_passes_exclude_hashes():
    """supply_variants 逐难度传 exclude_hashes：后续难度排除已选 hash（question_supply 去重链）。"""
    captured: list[tuple[str, set]] = []

    async def fake_supply(db, *, kp_codes, q_type, difficulty, count, exclude_hashes):
        captured.append((difficulty, set(exclude_hashes)))
        if difficulty == "easy":
            return [
                SimpleNamespace(hash="h1", q_type="choice", stem="a", difficulty="easy",
                                kp_codes=["MATH-1"], source="s1")
            ]
        return [
            SimpleNamespace(hash="h2", q_type="blank", stem="b", difficulty="medium",
                            kp_codes=["MATH-1"], source="s2")
        ]

    with patch("app.skills.question_supply.supply_questions", new=fake_supply):
        out = await supply_variants(AsyncMock(), uuid.uuid4(), "MATH-1", ("easy", "medium"))
    assert captured[0][1] == set()
    assert captured[1][1] == {"h1"}
    assert [v["hash"] for v in out] == ["h1", "h2"]


# ---------- Registry 无 F14 / lean.* / 判分写工具 ----------


def test_registry_no_f14_or_forbidden_tools(domain_registry: ToolRegistry):
    names = domain_registry.names()
    for forbidden in ("wf_verify_derivation", "research.verify_derivation"):
        assert forbidden not in names
    assert not any(n.startswith("lean.") for n in names)
    # 无直接判分/掌握度写工具
    for forbidden in ("practice.grade", "mastery.write", "student.grade", "error.write", "grade.write"):
        assert forbidden not in names
    # 本阶段无 WRITE 风险工具
    assert all(domain_registry.get(n).risk != ToolRisk.WRITE for n in names)
