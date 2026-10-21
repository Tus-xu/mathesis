"""Butler Kernel v2 阶段 4B：7 个星辰远程工具边界（Task 9）

参数化测试覆盖：
- 7 个精确名称全部注册（不多不少），全部 ToolRisk.EXTERNAL、学生可见；
- 每个工具 Pydantic 输入输出可验证；角色/场景 allowlist 正确；
- 学生不能看到教师/科研/管理能力；
- handler 确实调用 run_workflow（远程路径），uid 用 context user_id（不信任模型传入）；
- 不 commit/rollback；
- 故障映射：disabled / missing credentials / missing flow_id / timeout /
  rate limit / concurrency / unavailable / invalid JSON / Schema mismatch /
  provider 未知异常 → 稳定错误码；
- 本地降级：smart_quiz→supply_variants、error_analysis→classify_subtype、
  speech_to_latex→_local_spark_to_latex、solution_pregrade→_ai_pregrade_solution；
- 本地降级也失败 → 明确 unavailable；
- web_search 三种授权路径调用次数（未授权→Policy 拒绝远程 0 次；显式开启/本地拒答→远程 1 次）；
- 未泄漏密钥；未修改 YAML 契约（parameters 键与 YAML 输入名一致）；
- F14 / lean.* / wf_intent_router / wf_socratic_chat 不在 Registry，注册尝试被拒；
- 核心学生流不抛 500（executor 层 ToolResult 稳定）。
"""

import asyncio
import json
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
from app.butler.executor import ButlerExecutor, ToolExecutionContext, WebSearchAuthorization
from app.butler.policy import PolicyGate
from app.butler.registry import ToolDefinition, ToolForbiddenError, ToolRegistry
from app.butler.runtime import _invocation_status
from app.butler.workflow_tools import (
    ERROR_CONCURRENCY,
    ERROR_DISABLED,
    ERROR_INVALID_JSON,
    ERROR_MISSING_CREDENTIALS,
    ERROR_MISSING_FLOW_ID,
    ERROR_RATE_LIMITED,
    ERROR_SCHEMA_MISMATCH,
    ERROR_TIMEOUT,
    ERROR_UNAVAILABLE,
    ERROR_UNKNOWN,
    build_workflow_registry,
)
from app.providers.xingchen import (
    XingchenConcurrencyError,
    XingchenConfig,
    XingchenError,
    XingchenRateLimitError,
    XingchenTimeoutError,
)

WORKFLOW_TOOL_NAMES = [
    "xingchen.document_understand",
    "xingchen.speech_to_latex",
    "xingchen.web_search",
    "xingchen.smart_quiz",
    "xingchen.solution_pregrade",
    "xingchen.error_analysis",
    "xingchen.course_preprocess",
]

TOOL_FLOW = {
    "xingchen.document_understand": "wf_doc_understand",
    "xingchen.speech_to_latex": "wf_speech_to_latex",
    "xingchen.web_search": "wf_web_search",
    "xingchen.smart_quiz": "wf_smart_quiz",
    "xingchen.solution_pregrade": "wf_solution_pregrade",
    "xingchen.error_analysis": "wf_error_analysis",
    "xingchen.course_preprocess": "wf_course_preprocess",
}

VALID_INPUTS = {
    "xingchen.document_understand": {
        "image_url": "https://example.com/q.png",
        "task": "extract_question",
    },
    "xingchen.speech_to_latex": {"asr_text": "x 的平方加 2 等于 1"},
    "xingchen.web_search": {"query": "导数应用", "max_results": 3},
    "xingchen.smart_quiz": {
        "kp_name": "导数应用",
        "kp_code": "MATH-1",
        "difficulty": "easy",
        "q_type": "choice",
    },
    "xingchen.solution_pregrade": {
        "question": "求 f(x)=x^2 的导数",
        "reference": "f'=2x",
        "student_answer": "f'=x",
        "max_score": 10.0,
    },
    "xingchen.error_analysis": {
        "question_text": "求 f(x)=x^2 的导数",
        "answer_text": "2x",
        "student_answer": "x",
        "context_kp": "MATH-1",
    },
    "xingchen.course_preprocess": {
        "transcript": "今天讲导数",
        "course_title": "导数",
        "kp_hint": ["MATH-1"],
    },
}

VALID_WORKFLOW_OUTPUTS = {
    "xingchen.document_understand": {
        "question_text": "求 f(x)=x^2 的导数",
        "latex_fragments": [],
        "has_figure": False,
        "question_type": "choice",
        "confidence": 0.9,
    },
    "xingchen.speech_to_latex": {
        "latex": "x^2",
        "normalized_text": "x 的平方",
        "ambiguous": False,
    },
    "xingchen.web_search": {
        "answer": "导数应用是…",
        "sources": [{"title": "t", "url": "https://x.com/a", "snippet": "s"}],
        "badge": "web_supplement",
    },
    "xingchen.smart_quiz": {
        "question_text": "求 f'(x)",
        "options": ["A", "B"],
        "answer": "A",
        "explanation": "因为…",
        "kp_code": "MATH-1",
        "difficulty": "easy",
    },
    "xingchen.solution_pregrade": {
        "score": 7.0,
        "error_type": "calculation",
        "step_comments": [],
        "summary": "步骤略跳",
    },
    "xingchen.error_analysis": {
        "error_type": "formula",
        "kp_code": "MATH-1",
        "confidence": 0.8,
    },
    "xingchen.course_preprocess": {
        "chapters": [{"title": "ch1"}],
        "kp_codes": ["MATH-1"],
        "knowledge_cards": [],
    },
}

# 无本地等价能力 → 远程失败返回明确 unavailable
NO_FALLBACK_TOOLS = ["xingchen.document_understand", "xingchen.course_preprocess"]

# 有本地等价能力 → 远程失败本地降级
LOCAL_FALLBACK_TOOLS = [
    "xingchen.smart_quiz",
    "xingchen.error_analysis",
    "xingchen.speech_to_latex",
    "xingchen.solution_pregrade",
]


def _cfg(enabled=True, key="key", secret="secret", flow_ids=None) -> XingchenConfig:
    if flow_ids is None:
        flow_ids = {f: f"fid-{f}" for f in set(TOOL_FLOW.values())}
    return XingchenConfig(enabled=enabled, api_key=key, api_secret=secret, flow_ids=flow_ids)


def _request(user_id: uuid.UUID | None = None, scene: str = "student.practice") -> ButlerRequest:
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


@pytest.fixture(scope="module")
def workflow_registry() -> ToolRegistry:
    return build_workflow_registry()


# ---------- 注册 ----------


def test_workflow_registry_exact_tool_set(workflow_registry: ToolRegistry):
    assert set(workflow_registry.names()) == set(WORKFLOW_TOOL_NAMES)


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
def test_workflow_tool_names_registered(workflow_registry: ToolRegistry, name: str):
    assert name in workflow_registry.names()


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
def test_workflow_tool_io_models_validatable(workflow_registry: ToolRegistry, name: str):
    tool = workflow_registry.get(name)
    assert issubclass(tool.input_model, BaseModel)
    assert issubclass(tool.output_model, BaseModel)
    tool.input_model.model_validate(VALID_INPUTS[name])
    tool.output_model.model_validate(
        {
            "available": True,
            "source": "xingchen",
            "degraded": False,
            "error_code": None,
            "data": VALID_WORKFLOW_OUTPUTS[name],
        }
    )
    # 降级包装同样 Schema 合法
    tool.output_model.model_validate(
        {
            "available": False,
            "source": "none",
            "degraded": True,
            "error_code": ERROR_UNAVAILABLE,
            "data": {},
        }
    )


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
def test_workflow_tool_role_scene_allowlist(workflow_registry: ToolRegistry, name: str):
    tool = workflow_registry.get(name)
    assert tool.allowed_roles == frozenset({ActorRole.STUDENT})
    assert all(s.startswith("student.") for s in tool.allowed_scenes)
    assert tool.risk == ToolRisk.EXTERNAL
    assert tool.timeout_s > 0
    assert tool.idempotency_required is False
    assert any(
        name in workflow_registry.visible_to(ActorRole.STUDENT, scene)
        for scene in tool.allowed_scenes
    )


def test_student_cannot_see_teacher_research_admin_tools(workflow_registry: ToolRegistry):
    for name in workflow_registry.names():
        assert not name.startswith(("teacher.", "research.", "admin."))
    for role in (ActorRole.TEACHER, ActorRole.RESEARCHER, ActorRole.ADMIN):
        assert workflow_registry.visible_to(role, "student.practice") == ()


# ---------- F14 / lean / 编排器 / 多轮主状态 缺席 ----------


def test_registry_no_f14_or_forbidden_tools(workflow_registry: ToolRegistry):
    names = workflow_registry.names()
    for forbidden in ("wf_verify_derivation", "research.verify_derivation"):
        assert forbidden not in names
    assert not any(n.startswith("lean.") for n in names)
    # 明确不注册：wf_intent_router / wf_socratic_chat
    for forbidden in (
        "xingchen.intent_router",
        "xingchen.socratic_chat",
        "wf_intent_router",
        "wf_socratic_chat",
    ):
        assert forbidden not in names


def test_f14_registration_attempt_rejected():
    """F14 / lean.* 注册尝试 → ToolForbiddenError（注册层拒绝）。"""
    reg = ToolRegistry()
    from app.butler.registry import ToolDefinition

    for bad_name in (
        "wf_verify_derivation",
        "research.verify_derivation",
        "lean.verify",
        "lean.prove",
    ):
        with pytest.raises(ToolForbiddenError):
            reg.register(
                ToolDefinition(
                    name=bad_name,
                    version="1.0.0",
                    description="x",
                    input_model=BaseModel,
                    output_model=BaseModel,
                    risk=ToolRisk.EXTERNAL,
                    allowed_roles=frozenset({ActorRole.STUDENT}),
                    allowed_scenes=frozenset({"student.practice"}),
                    handler=AsyncMock(),
                )
            )


# ---------- handler 远程路径 ----------


def _patch_remote(name: str, cfg: XingchenConfig, wf_out: dict):
    """设置远程 mock：config + run_workflow（web_search 额外 mock 本地拒答）。"""
    patches = [
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=cfg),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock(return_value=wf_out)),
    ]
    if name == "xingchen.web_search":
        patches.append(
            patch(
                "app.butler.workflow_tools._local_kb_search",
                new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
            )
        )
    return patches


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
async def test_handler_calls_run_workflow(workflow_registry: ToolRegistry, name: str):
    tool = workflow_registry.get(name)
    with ExitStack() as stack:
        for p in _patch_remote(name, _cfg(), VALID_WORKFLOW_OUTPUTS[name]):
            stack.enter_context(p)
        m = stack.enter_context(
            patch(
                "app.butler.workflow_tools.run_workflow",
                new=AsyncMock(return_value=VALID_WORKFLOW_OUTPUTS[name]),
            )
        )
        result = await tool.handler(_remote_ctx(name, db=AsyncMock()), VALID_INPUTS[name])
    assert m.await_count == 1
    assert result["available"] is True
    assert result["source"] == "xingchen"
    assert result["degraded"] is False
    assert result["error_code"] is None
    if name == "xingchen.web_search":
        # web_search 会规范化 sources（补 retrieved_at/截断/URL 校验）
        assert result["data"]["answer"] == VALID_WORKFLOW_OUTPUTS[name]["answer"]
        assert result["data"]["badge"] == VALID_WORKFLOW_OUTPUTS[name]["badge"]
        for src in result["data"]["sources"]:
            assert set(src.keys()) == {"title", "url", "snippet", "retrieved_at"}
    else:
        assert result["data"] == VALID_WORKFLOW_OUTPUTS[name]


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
async def test_handler_uses_context_user_id(workflow_registry: ToolRegistry, name: str):
    """run_workflow 的 uid 必须是 context user_id；模型传入 user_id 被忽略。"""
    tool = workflow_registry.get(name)
    user_id = uuid.uuid4()
    evil = uuid.uuid4()
    with ExitStack() as stack:
        for p in _patch_remote(name, _cfg(), VALID_WORKFLOW_OUTPUTS[name]):
            stack.enter_context(p)
        m = stack.enter_context(
            patch(
                "app.butler.workflow_tools.run_workflow",
                new=AsyncMock(return_value=VALID_WORKFLOW_OUTPUTS[name]),
            )
        )
        await tool.handler(
            _remote_ctx(name, db=AsyncMock(), user_id=user_id),
            {**VALID_INPUTS[name], "user_id": evil},
        )
    assert m.await_count == 1
    assert m.await_args.kwargs["uid"] == str(user_id)
    assert m.await_args.kwargs["uid"] != str(evil)


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
async def test_handler_does_not_commit_or_rollback(workflow_registry: ToolRegistry, name: str):
    tool = workflow_registry.get(name)
    db = AsyncMock()
    with ExitStack() as stack:
        for p in _patch_remote(name, _cfg(), VALID_WORKFLOW_OUTPUTS[name]):
            stack.enter_context(p)
        await tool.handler(_ctx(db=db), VALID_INPUTS[name])
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


# ---------- YAML 契约（parameters 键与工作流输入名一致） ----------

# 每个工具传给 run_workflow 的 parameters 键（与 D:\\工作流搭建情况 YAML 开始节点输入名一致）
EXPECTED_PARAM_KEYS = {
    "xingchen.document_understand": {"AGENT_USER_INPUT", "image_url", "task", "grade_hint"},
    "xingchen.speech_to_latex": {"AGENT_USER_INPUT", "asr_text", "context_kp"},
    "xingchen.web_search": {"query", "max_results"},
    "xingchen.smart_quiz": {"AGENT_USER_INPUT", "kp_name", "kp_code", "difficulty", "q_type"},
    "xingchen.solution_pregrade": {
        "AGENT_USER_INPUT",
        "question",
        "reference",
        "student_answer",
        "max_score",
    },
    "xingchen.error_analysis": {
        "AGENT_USER_INPUT",
        "question_text",
        "answer_text",
        "student_answer",
        "context_kp",
    },
    "xingchen.course_preprocess": {"AGENT_USER_INPUT", "transcript", "course_title", "kp_hint"},
}


@pytest.mark.parametrize("name", WORKFLOW_TOOL_NAMES)
async def test_parameters_match_yaml_contract(workflow_registry: ToolRegistry, name: str):
    """parameters 键与工作流 YAML 输入名一致（不改 YAML 契约）。"""
    tool = workflow_registry.get(name)
    with ExitStack() as stack:
        for p in _patch_remote(name, _cfg(), VALID_WORKFLOW_OUTPUTS[name]):
            stack.enter_context(p)
        m = stack.enter_context(
            patch(
                "app.butler.workflow_tools.run_workflow",
                new=AsyncMock(return_value=VALID_WORKFLOW_OUTPUTS[name]),
            )
        )
        await tool.handler(_remote_ctx(name, db=AsyncMock()), VALID_INPUTS[name])
    params = m.await_args.kwargs["parameters"]
    assert set(params.keys()) == EXPECTED_PARAM_KEYS[name]
    # run_workflow(flow, *, uid, parameters, config)：flow 为位置参数
    assert m.await_args.args[0] == TOOL_FLOW[name]


# ---------- 故障映射 ----------

FAULT_CASES = [
    ("disabled", _cfg(enabled=False), None, ERROR_DISABLED),
    ("missing_credentials", _cfg(key="", secret=""), None, ERROR_MISSING_CREDENTIALS),
    ("missing_flow_id", _cfg(flow_ids={}), None, ERROR_MISSING_FLOW_ID),
    ("timeout", _cfg(), XingchenTimeoutError(20804, "read_timeout"), ERROR_TIMEOUT),
    ("rate_limit", _cfg(), XingchenRateLimitError(20375, "rate"), ERROR_RATE_LIMITED),
    ("concurrency", _cfg(), XingchenConcurrencyError(20357, "busy"), ERROR_CONCURRENCY),
    ("unavailable", _cfg(), XingchenError(500, "HTTP 500"), ERROR_UNAVAILABLE),
    ("invalid_json", _cfg(), XingchenError(-2, "工作流 wf 输出非法 JSON: x"), ERROR_INVALID_JSON),
    (
        "schema_mismatch",
        _cfg(),
        XingchenError(-2, "工作流 wf 输出 schema 校验失败: x"),
        ERROR_SCHEMA_MISMATCH,
    ),
    ("unknown", _cfg(), ValueError("boom"), ERROR_UNKNOWN),
]


@pytest.mark.parametrize("name", NO_FALLBACK_TOOLS)
@pytest.mark.parametrize("case_name,cfg,exc,expected", FAULT_CASES, ids=[c[0] for c in FAULT_CASES])
async def test_fault_no_fallback(
    workflow_registry: ToolRegistry, name: str, case_name: str, cfg, exc, expected: str
):
    """无本地等价能力：故障 → 明确 unavailable，错误码稳定。"""
    tool = workflow_registry.get(name)
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=cfg),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock(side_effect=exc)) as m,
    ):
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS[name])
    assert result["available"] is False
    assert result["source"] == "none"
    assert result["degraded"] is True
    assert result["error_code"] == expected
    assert result["data"] == {}
    # 配置未启用/缺凭证/缺 flow_id 时远程调用 0 次；其余故障远程调用 1 次
    if case_name in ("disabled", "missing_credentials", "missing_flow_id"):
        assert m.await_count == 0
    else:
        assert m.await_count == 1


def _local_fallback_patches(name: str):
    """本地降级 mock：返回 (patches, expected_local_data)。"""
    if name == "xingchen.smart_quiz":
        return [
            patch(
                "app.butler.workflow_tools.supply_variants",
                new=AsyncMock(
                    return_value=[
                        {
                            "hash": "h1",
                            "q_type": "choice",
                            "stem": "s",
                            "difficulty": "easy",
                            "kp_codes": ["MATH-1"],
                            "source": "bank",
                        }
                    ]
                ),
            )
        ], {
            "items": [
                {
                    "hash": "h1",
                    "q_type": "choice",
                    "stem": "s",
                    "difficulty": "easy",
                    "kp_codes": ["MATH-1"],
                    "source": "bank",
                }
            ],
            "degraded_from": "xingchen",
        }
    if name == "xingchen.error_analysis":
        return [], {
            "error_type": "logic_jump",
            "kp_code": "MATH-1",
            "confidence": 0.0,
            "subtype_zh": "推理跳步",
        }
    if name == "xingchen.speech_to_latex":
        return [
            patch(
                "app.gateway.speech_router._local_spark_to_latex",
                new=AsyncMock(return_value="x^2"),
            )
        ], {"latex": "x^2", "normalized_text": "x 的平方加 2 等于 1", "ambiguous": False}
    if name == "xingchen.solution_pregrade":
        return [
            patch(
                "app.gateway.student_router._ai_pregrade_solution",
                new=AsyncMock(
                    return_value=("pending_review", 5.0, {"ai_pregraded": True, "comment": "c"})
                ),
            )
        ], {
            "verdict": "pending_review",
            "score": 5.0,
            "max_score": 10.0,
            "ai_pregraded": True,
            "comment": "c",
            "error_type": None,
            "degraded": None,
        }
    raise AssertionError(f"unknown local fallback tool: {name}")


@pytest.mark.parametrize("name", LOCAL_FALLBACK_TOOLS)
@pytest.mark.parametrize("case_name,cfg,exc,expected", FAULT_CASES, ids=[c[0] for c in FAULT_CASES])
async def test_fault_local_fallback(
    workflow_registry: ToolRegistry, name: str, case_name: str, cfg, exc, expected: str
):
    """有本地等价能力：故障 → 本地降级成功，source=local，错误码保留。"""
    tool = workflow_registry.get(name)
    local_patches, expected_data = _local_fallback_patches(name)
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=cfg),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock(side_effect=exc)) as m,
        ExitStack() as stack,
    ):
        for p in local_patches:
            stack.enter_context(p)
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS[name])
    assert result["available"] is True
    assert result["source"] == "local"
    assert result["degraded"] is True
    assert result["error_code"] == expected
    assert result["data"] == expected_data
    if case_name in ("disabled", "missing_credentials", "missing_flow_id"):
        assert m.await_count == 0
    else:
        assert m.await_count == 1


async def test_local_fallback_also_fails():
    """本地降级也失败 → 明确 unavailable（不抛 500，不泄漏细节）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.smart_quiz")
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg(enabled=False)),
        ),
        patch(
            "app.butler.workflow_tools.supply_variants",
            new=AsyncMock(side_effect=RuntimeError("D:/secret/path.sql: sk-999")),
        ),
    ):
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS["xingchen.smart_quiz"])
    assert result["available"] is False
    assert result["source"] == "none"
    assert result["degraded"] is True
    assert result["error_code"] == ERROR_DISABLED
    assert "D:/secret" not in json.dumps(result)
    assert "sk-999" not in json.dumps(result)


async def test_solution_pregrade_timeout_remote_call_count_is_one():
    """星辰超时后整个调用链 run_workflow 总调用次数严格 = 1（本地降级不再进星辰）。

    不整体 mock _ai_pregrade_solution：真实走本地降级路径，断言
    _ai_pregrade_solution 内部不再二次调用 run_workflow（allow_xingchen=False）。
    """
    reg = build_workflow_registry()
    tool = reg.get("xingchen.solution_pregrade")
    fake_router = SimpleNamespace(
        chat=AsyncMock(
            return_value={
                "content": '{"score": 5, "max_score": 10, "comment": "步骤略跳", "error_type": null}'
            }
        )
    )
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.butler.workflow_tools.run_workflow",
            new=AsyncMock(side_effect=XingchenTimeoutError(20804, "read_timeout")),
        ) as wrapper_m,
        patch(
            "app.providers.xingchen.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.providers.xingchen.run_workflow",
            new=AsyncMock(),
        ) as provider_m,
        patch(
            # tel D9 抽取后：本地降级模型调用点在共享管线模块（student_router 仅剩门面）
            "app.services.solution_pregrade.get_model_router",
            return_value=fake_router,
        ) as router_m,
    ):
        result = await tool.handler(
            _ctx(db=AsyncMock()), VALID_INPUTS["xingchen.solution_pregrade"]
        )
    # 整个调用链 run_workflow 总调用次数严格 = 1（仅 wrapper 第一次星辰调用）
    assert wrapper_m.await_count == 1
    assert provider_m.await_count == 0
    # 本地模型最多一次调用
    assert router_m.call_count == 1
    assert result["available"] is True
    assert result["source"] == "local"
    assert result["degraded"] is True
    assert result["error_code"] == ERROR_TIMEOUT
    assert result["data"]["verdict"] == "pending_review"
    assert result["data"]["score"] == 5.0


# ---------- executor 层：核心学生流不抛 500 ----------


@pytest.mark.parametrize("name", NO_FALLBACK_TOOLS)
@pytest.mark.parametrize("case_name,cfg,exc,expected", FAULT_CASES, ids=[c[0] for c in FAULT_CASES])
async def test_executor_no_500_on_fault(name: str, case_name: str, cfg, exc, expected: str):
    """executor 层：故障工具返回稳定 ToolResult（ok=True + 降级 data），不抛 500。"""
    reg = build_workflow_registry()
    policy = PolicyGate(reg)
    ex = ButlerExecutor(reg, policy, budget=ButlerBudget())
    scene = next(iter(reg.get(name).allowed_scenes))
    action = PlannedAction(tool_name=name, arguments=VALID_INPUTS[name], reason="test")
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=cfg),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock(side_effect=exc)),
    ):
        result = await ex.invoke(
            uuid.uuid4(), _request(scene=scene), action, AsyncMock(), external_allowed=True
        )
    assert result.ok is True  # 工具返回 Schema 合法包装，不抛 500
    assert result.execution_status == "executed"
    assert _invocation_status(result) == "executed"
    assert result.data["available"] is False
    assert result.data["error_code"] == expected
    assert result.data["degraded"] is True
    # 密钥/路径不进入 ToolResult
    assert "sk-" not in json.dumps(result.model_dump())
    assert "secret" not in json.dumps(result.model_dump()).lower()
    assert "D:/" not in json.dumps(result.model_dump())


# ---------- web_search 授权路径 ----------


async def test_web_search_policy_rejects_without_optin():
    """全局能力关闭：Policy 拒绝，远程调用 0 次。"""
    reg = build_workflow_registry()
    policy = PolicyGate(reg)
    ex = ButlerExecutor(reg, policy, budget=ButlerBudget())
    action = PlannedAction(
        tool_name="xingchen.web_search",
        arguments=VALID_INPUTS["xingchen.web_search"],
        reason="test",
    )
    with patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m:
        result = await ex.invoke(
            uuid.uuid4(),
            _request(),
            action,
            AsyncMock(),
            external_allowed=True,
            web_search_enabled=False,
        )
    assert result.ok is False
    assert result.error_code == "confirmation_required"
    assert result.execution_status == "not_executed"
    m.assert_not_awaited()


async def test_web_search_local_answerable_no_remote():
    """本地知识库命中 → source=local，远程调用 0 次。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    local_data = {
        "answerable": True,
        "data": {
            "answer": "本地答案",
            "sources": [
                {
                    "title": "本地知识库",
                    "url": "",
                    "snippet": "s",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "badge": "local_kb",
        },
    }
    with (
        patch("app.butler.workflow_tools._local_kb_search", new=AsyncMock(return_value=local_data)),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS["xingchen.web_search"])
    assert result["available"] is True
    assert result["source"] == "local"
    assert result["degraded"] is False
    m.assert_not_awaited()


def _auth_ctx(
    db=None,
    *,
    global_enabled: bool,
    user_opt_in: bool,
    user_id: uuid.UUID | None = None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=uuid.uuid4(),
        request=_request(user_id),
        db=db,
        idempotency_key="",
        web_search_auth=WebSearchAuthorization(
            global_enabled=global_enabled,
            user_opt_in=user_opt_in,
        ),
    )


def _remote_ctx(name: str, db=None, user_id: uuid.UUID | None = None) -> ToolExecutionContext:
    """远程路径测试上下文：web_search 需授权上下文（fail-closed），其余工具无需。"""
    if name == "xingchen.web_search":
        return _auth_ctx(db=db, global_enabled=True, user_opt_in=True, user_id=user_id)
    return _ctx(db=db, user_id=user_id)


async def test_web_search_optin_remote_called():
    """用户 opt-in + 本地拒答 → 远程调用 1 次，sources 规范化。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.butler.workflow_tools.run_workflow",
            new=AsyncMock(return_value=VALID_WORKFLOW_OUTPUTS["xingchen.web_search"]),
        ) as m,
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=True),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is True
    assert result["source"] == "xingchen"
    assert m.await_count == 1
    # sources 每项严格含 title/url/snippet/retrieved_at
    for src in result["data"]["sources"]:
        assert set(src.keys()) == {"title", "url", "snippet", "retrieved_at"}
    assert result["data"]["sources"][0]["url"].startswith("https://")


async def test_web_search_no_optin_local_refused_remote_zero():
    """本地拒答但用户未 opt-in → fail-closed 拒绝远程（单条请求授权必需）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=False),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is False
    assert result["source"] == "none"
    assert result["degraded"] is True
    assert result["error_code"] == "confirmation_required"
    assert result["data"]["refuse_reason"] == "no kb"
    m.assert_not_awaited()


async def test_web_search_remote_fails_degraded():
    """本地拒答 + opt-in + 远程失败 → 可解释降级（保留本地拒答原因，不影响本地回答）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.butler.workflow_tools.run_workflow",
            new=AsyncMock(side_effect=XingchenTimeoutError(20804, "t")),
        ),
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=True),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is False
    assert result["degraded"] is True
    assert result["error_code"] == ERROR_TIMEOUT
    assert result["data"]["refuse_reason"] == "no kb"


# ---------- 阶段 5.1：授权语义闭环（全局能力 / 用户 opt-in / 运行时 local_refused） ----------


async def test_web_search_global_on_no_optin_local_answerable_remote_zero():
    """全局联网开、用户未 opt-in、本地可回答：远程 0 次。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    local_data = {
        "answerable": True,
        "data": {
            "answer": "本地答案",
            "sources": [
                {
                    "title": "本地知识库",
                    "url": "",
                    "snippet": "s",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "badge": "local_kb",
        },
    }
    with (
        patch("app.butler.workflow_tools._local_kb_search", new=AsyncMock(return_value=local_data)),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=False),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is True
    assert result["source"] == "local"
    assert result["degraded"] is False
    m.assert_not_awaited()


async def test_web_search_global_on_no_optin_local_refused_remote_zero():
    """全局联网开、用户未 opt-in、本地拒答：fail-closed 拒绝远程（远程 0 次）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=False),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is False
    assert result["source"] == "none"
    assert result["degraded"] is True
    assert result["error_code"] == "confirmation_required"
    m.assert_not_awaited()


async def test_web_search_concurrent_optin_isolation():
    """两个并发请求，一个 opt-in、一个未 opt-in，不得串扰。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    local_data = {
        "answerable": True,
        "data": {
            "answer": "本地答案",
            "sources": [
                {
                    "title": "本地知识库",
                    "url": "",
                    "snippet": "s",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "badge": "local_kb",
        },
    }

    async def _kb_side_effect(context, validated_input):
        # opt-in 请求本地拒答 → 触发远程；未 opt-in 请求本地可答 → 远程 0
        if context.web_search_auth and context.web_search_auth.user_opt_in:
            return {"answerable": False, "refuse_reason": "no kb"}
        return local_data

    with (
        patch(
            "app.butler.workflow_tools._local_kb_search", new=AsyncMock(side_effect=_kb_side_effect)
        ),
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.butler.workflow_tools.run_workflow",
            new=AsyncMock(return_value=VALID_WORKFLOW_OUTPUTS["xingchen.web_search"]),
        ) as m,
    ):
        optin_ctx = _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=True)
        no_optin_ctx = _auth_ctx(db=AsyncMock(), global_enabled=True, user_opt_in=False)
        r1, r2 = await asyncio.gather(
            tool.handler(optin_ctx, VALID_INPUTS["xingchen.web_search"]),
            tool.handler(no_optin_ctx, VALID_INPUTS["xingchen.web_search"]),
        )
    assert r1["available"] is True
    assert r1["source"] == "xingchen"  # opt-in 本地拒答 → 远程
    assert r2["available"] is True
    assert r2["source"] == "local"  # 未 opt-in 本地可答 → 远程 0
    assert m.await_count == 1  # 仅 opt-in 请求触发远程，未串扰


# ---------- 阶段 5.1a：fail-closed 收口 ----------


async def test_web_search_auth_none_local_refused_remote_zero():
    """auth=None + 本地拒答 → fail-closed：degraded/confirmation_required，远程 0 次。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS["xingchen.web_search"])
    assert result["available"] is False
    assert result["source"] == "none"
    assert result["degraded"] is True
    assert result["error_code"] == "confirmation_required"
    assert result["data"]["refuse_reason"] == "no kb"
    m.assert_not_awaited()


async def test_web_search_auth_none_local_answerable_returns_local():
    """auth=None + 本地可答 → 正常返回 local（本地优先不受授权上下文缺失影响）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    local_data = {
        "answerable": True,
        "data": {
            "answer": "本地答案",
            "sources": [
                {
                    "title": "本地知识库",
                    "url": "",
                    "snippet": "s",
                    "retrieved_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "badge": "local_kb",
        },
    }
    with (
        patch("app.butler.workflow_tools._local_kb_search", new=AsyncMock(return_value=local_data)),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(_ctx(db=AsyncMock()), VALID_INPUTS["xingchen.web_search"])
    assert result["available"] is True
    assert result["source"] == "local"
    assert result["degraded"] is False
    m.assert_not_awaited()


async def test_web_search_global_off_local_refused_remote_zero():
    """global_enabled=false + 本地拒答 → 远程 0 次（服务端能力是硬前置）。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.web_search")
    with (
        patch(
            "app.butler.workflow_tools._local_kb_search",
            new=AsyncMock(return_value={"answerable": False, "refuse_reason": "no kb"}),
        ),
        patch("app.butler.workflow_tools.run_workflow", new=AsyncMock()) as m,
    ):
        result = await tool.handler(
            _auth_ctx(db=AsyncMock(), global_enabled=False, user_opt_in=True),
            VALID_INPUTS["xingchen.web_search"],
        )
    assert result["available"] is False
    assert result["degraded"] is True
    assert result["error_code"] == "confirmation_required"
    m.assert_not_awaited()


class _CaptureInput(BaseModel):
    query: str


class _CaptureOutput(BaseModel):
    answer: str


async def test_web_search_optin_enters_execution_context():
    """ButlerRequest.web_search_opt_in 必须真实进入 ToolExecutionContext（经 ButlerExecutor）。"""
    captured: list[ToolExecutionContext] = []

    async def _capture_handler(context: ToolExecutionContext, validated_input: dict) -> dict:
        captured.append(context)
        return {"answer": "captured"}

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="test.capture",
            version="1.0.0",
            description="capture context",
            input_model=_CaptureInput,
            output_model=_CaptureOutput,
            risk=ToolRisk.READ,
            allowed_roles=frozenset({ActorRole.STUDENT}),
            allowed_scenes=frozenset({"student.practice"}),
            handler=_capture_handler,
        )
    )
    ex = ButlerExecutor(reg, PolicyGate(reg), budget=ButlerBudget())
    request = _request().model_copy(update={"web_search_opt_in": True})
    action = PlannedAction(tool_name="test.capture", arguments={"query": "x"}, reason="test")
    result = await ex.invoke(
        uuid.uuid4(),
        request,
        action,
        AsyncMock(),
        external_allowed=True,
        web_search_enabled=True,
    )
    assert result.ok is True
    assert len(captured) == 1
    assert captured[0].request.web_search_opt_in is True
    assert captured[0].web_search_auth is not None
    assert captured[0].web_search_auth.user_opt_in is True
    assert captured[0].web_search_auth.global_enabled is True


def test_web_search_sources_normalized_and_validated():
    """sources 规范化：非法 URL 置空、长度截断、字段严格。"""
    from app.butler.workflow_tools import _normalize_sources

    raw = [
        {"title": "t" * 200, "url": "javascript:alert(1)", "snippet": "s" * 500},
        {"title": "ok", "url": "https://example.com/a", "snippet": "snip"},
        "not-a-dict",
    ]
    out = _normalize_sources(raw, limit=5)
    assert len(out) == 2
    assert out[0]["title"] == "t" * 100
    assert out[0]["url"] == ""  # 非 http(s) URL 置空
    assert len(out[0]["snippet"]) == 300
    assert out[1]["url"] == "https://example.com/a"
    for src in out:
        assert set(src.keys()) == {"title", "url", "snippet", "retrieved_at"}


# ---------- 密钥不泄漏 ----------


async def test_no_secret_leak_in_fault_output():
    """故障输出/异常路径不泄漏密钥与原始路径。"""
    reg = build_workflow_registry()
    tool = reg.get("xingchen.document_understand")
    with (
        patch(
            "app.butler.workflow_tools.resolve_effective_xingchen_config",
            new=AsyncMock(return_value=_cfg()),
        ),
        patch(
            "app.butler.workflow_tools.run_workflow",
            new=AsyncMock(side_effect=XingchenError(500, "HTTP 500: Authorization: Bearer sk-999")),
        ),
    ):
        result = await tool.handler(
            _ctx(db=AsyncMock()), VALID_INPUTS["xingchen.document_understand"]
        )
    dumped = json.dumps(result)
    assert "sk-999" not in dumped
    assert "Bearer" not in dumped
    assert "Authorization" not in dumped
