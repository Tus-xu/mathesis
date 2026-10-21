"""F13 socratic 可视化讲解测试（planner 解析/门控/发射策略/全旅程）。

覆盖：
1. should_plan_figures 主题门控；extract_json_array 健壮性；parse_figure_plan 校验/去重/封顶；
2. merge_figures_into_plan 步骤合并；_figure_events 限帧与异常静默；
3. 全旅程（MockLLM）：新题开场构图帧 → 答对（完成步全帧 + 下一步构图帧）→
   hint 构图帧 → 揭示总结全帧；图形失败不阻断 token 流。

需要 PostgreSQL 运行中（与 test_socratic_solver 同环境）。
"""

import json
import uuid
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.conversation import Conversation
from app.models.tutor_session import TutorSession
from app.models.user import User
from app.skills.base import SkillContext
from app.skills.socratic_solver.figures import (
    extract_json_array,
    merge_figures_into_plan,
    parse_figure_plan,
    should_plan_figures,
)
from app.skills.socratic_solver.main import SocraticSolverExecutor

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


# ========== 测试物料 ==========

FUNCTION_FIG = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x^2-2x-3"}],
        "x_range": [-3, 5],
        "y_range": [-5, 6],
        "points": [{"x": 1, "y": -4, "label": "(1,-4)"}],
    },
}

FUNCTION_FIG_2 = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x^2-2x-3"}],
        "x_range": [-3, 5],
        "y_range": [-5, 6],
        "points": [{"x": -1, "y": 0, "label": "(-1,0)"}, {"x": 3, "y": 0, "label": "(3,0)"}],
    },
}

PLAN_JSON = json.dumps(
    [
        {"step": 1, "caption": "观察抛物线的开口与顶点", "figure": FUNCTION_FIG},
        {"step": 2, "caption": "找到图像与 x 轴的交点", "figure": FUNCTION_FIG_2},
    ],
    ensure_ascii=False,
)

QUESTION = "作出函数 $y=x^2-2x-3$ 的图像，并求图像与 $x$ 轴交点的坐标"

SOLVER_OUT = (
    "[[STEP]]\n断言：$y=x^2-2x-3$ 的图像是开口向上的抛物线，顶点为 $(1,-4)$\n"
    "原因：配方得 $(x-1)^2-4$\n"
    "[[STEP]]\n断言：图像与 $x$ 轴交点横坐标为 $-1$ 与 $3$\n"
    "原因：解方程 $x^2-2x-3=0$ 得 $(x-3)(x+1)=0$\n"
    "[[STEP]]\n断言：交点坐标为 $(-1,0)$ 与 $(3,0)$\n"
    "原因：将横坐标代入 $y=0$\n"
    "最终答案：\\boxed{(-1,0),(3,0)}\n难度：easy"
)

GUIDE_TEXTS = [
    "先看看这条抛物线的开口方向和顶点位置，你觉得它和 x 轴会有几个交点？",
    "答得漂亮！下一步看图找交点，你觉得图像在哪些地方穿过 x 轴？",
]
HINT_TEXT = "看图观察：抛物线最低点在哪里？开口朝哪个方向？"
SUMMARY_TEXT = "这道题我们就走完了。以后遇到二次函数求交点，先配方或分解因式，再对照图像验证。"
JUDGE_CORRECT = json.dumps(
    {"analysis": "正确", "verdict": "correct", "misconception": None, "feedback_hint": None},
    ensure_ascii=False,
)


class MockLLM:
    """按 scene 队列返回的模拟 ModelRouter（与 test_socratic_solver.MockLLM 同手法）。"""

    intended_provider = "mock"

    def __init__(self, chat_by_scene=None, stream_by_scene=None):
        self.chat_by_scene = {k: list(v) for k, v in (chat_by_scene or {}).items()}
        self.stream_by_scene = {k: list(v) for k, v in (stream_by_scene or {}).items()}
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.chat_calls.append(kwargs)
        queue = self.chat_by_scene.get(kwargs.get("scene")) or []
        content = queue.pop(0) if queue else "{}"
        return {"content": content}

    def chat_stream(self, messages, **kwargs):
        self.stream_calls.append(kwargs)
        queue = self.stream_by_scene.get(kwargs.get("scene")) or []
        text = queue.pop(0) if queue else ""

        async def _gen():
            yield {"_provider": "mock"}
            if text:
                yield {"token": text}
            yield {"_usage": {}}

        return _gen()


def _make_mock() -> MockLLM:
    return MockLLM(
        chat_by_scene={
            "socratic_figure_plan": [PLAN_JSON],
            "socratic_judge": [JUDGE_CORRECT, JUDGE_CORRECT],
        },
        stream_by_scene={
            "socratic_solver": [SOLVER_OUT],
            "socratic_guide": list(GUIDE_TEXTS),
            "socratic_hint": [HINT_TEXT],
            "socratic_summary": [SUMMARY_TEXT],
        },
    )


@asynccontextmanager
async def _db():
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _make_user_conv(session):
    user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
    session.add(user)
    await session.flush()  # 先落 user 拿 id，再建 conv（FK 依赖）
    conv = Conversation(user_id=user.id, active_role="student", title="新对话")
    session.add(conv)
    await session.flush()
    return user, conv


def _ctx(db, llm, user, conv) -> SkillContext:
    return SkillContext(
        user_id=str(user.id),
        user_role="student",
        conversation_id=str(conv.id),
        request_id="test-req-f13",
        db=db,
        llm=llm,
    )


async def _run(params: dict, ctx: SkillContext) -> list[dict]:
    events = []
    async for ev in SocraticSolverExecutor().run(params, ctx):
        events.append(ev)
    return events


def _figures(events: list[dict]) -> list[dict]:
    return [e["data"] for e in events if e.get("type") == "figure"]


def _tokens(events: list[dict]) -> str:
    return "".join(e["data"].get("text", "") for e in events if e.get("type") == "token")


# ========== 1. 门控与解析（纯函数） ==========


class TestPlanning:
    def test_should_plan_figures(self):
        assert should_plan_figures(QUESTION)
        assert should_plan_figures("三棱锥 P-ABC 中，PA 垂直底面，求体积")
        assert should_plan_figures("过点(1,2)作圆 x²+y²=5 的切线")
        assert not should_plan_figures("计算 12×13-7 等于多少")
        assert not should_plan_figures("今天天气怎么样")

    def test_extract_json_array(self):
        assert extract_json_array('[{"a": 1}]') == [{"a": 1}]
        assert extract_json_array("好的，以下是我为你规划的图形：\n```json\n[{\"a\": 1}]\n```\n请查收") == [{"a": 1}]
        assert extract_json_array('前缀 [{"s": "含]与[的字符串"}] 后缀') == [{"s": "含]与[的字符串"}]
        assert extract_json_array("完全没有数组") is None
        assert extract_json_array("[未闭合") is None
        assert extract_json_array("[1, 2") is None
        assert extract_json_array('{"a": 1}') is None  # 对象不是数组

    def test_parse_plan_valid(self):
        items, error = parse_figure_plan(PLAN_JSON, steps_count=3)
        assert error is None
        assert [it["step"] for it in items] == [1, 2]
        assert items[0]["caption"] == "观察抛物线的开口与顶点"
        assert items[0]["figure"]["type"] == "function"

    def test_parse_plan_empty_output_means_no_figure(self):
        assert parse_figure_plan("", 3) == ([], None)
        assert parse_figure_plan("[]", 3) == ([], None)

    def test_parse_plan_non_array_error(self):
        items, error = parse_figure_plan("这些步骤都挺直观的，不需要图。", 3)
        assert items == []
        assert error is not None and "JSON 数组" in error

    def test_parse_plan_step_out_of_range_error(self):
        raw = json.dumps([{"step": 9, "figure": FUNCTION_FIG}], ensure_ascii=False)
        items, error = parse_figure_plan(raw, 3)
        assert items == []
        assert error is not None and "step 非法" in error

    def test_parse_plan_partial_valid_kept(self):
        raw = json.dumps(
            [
                {"step": 1, "figure": FUNCTION_FIG},
                {"step": 2, "figure": {"type": "nonsense", "params": {}}},
            ],
            ensure_ascii=False,
        )
        items, error = parse_figure_plan(raw, 3)
        assert error is None, f"部分有效应采信，不应整体报错: {error}"
        assert [it["step"] for it in items] == [1]

    def test_parse_plan_duplicate_step_keeps_first(self):
        raw = json.dumps(
            [
                {"step": 1, "figure": FUNCTION_FIG},
                {"step": 1, "figure": FUNCTION_FIG_2},
            ],
            ensure_ascii=False,
        )
        items, error = parse_figure_plan(raw, 3)
        assert error is None
        assert len(items) == 1
        assert items[0]["figure"] == FUNCTION_FIG

    def test_parse_plan_capped_at_three(self):
        raw = json.dumps(
            [{"step": i, "figure": FUNCTION_FIG} for i in (1, 2, 3, 4)],
            ensure_ascii=False,
        )
        items, _ = parse_figure_plan(raw, 4)
        assert len(items) == 3

    def test_parse_plan_caption_truncated(self):
        raw = json.dumps(
            [{"step": 1, "caption": "长" * 200, "figure": FUNCTION_FIG}],
            ensure_ascii=False,
        )
        items, _ = parse_figure_plan(raw, 3)
        assert len(items[0]["caption"]) <= 80

    def test_merge_into_plan(self):
        steps = [
            {"assertion": "a", "reason": "r"},
            {"assertion": "b", "reason": "r"},
            {"assertion": "c", "reason": "r"},
        ]
        items, _ = parse_figure_plan(PLAN_JSON, 3)
        merge_figures_into_plan(steps, items)
        assert steps[0]["figure"]["caption"] == "观察抛物线的开口与顶点"
        assert steps[1]["figure"]["params"]["type"] == "function"
        assert "figure" not in steps[2]


# ========== 2. _figure_events 发射策略（不碰 DB） ==========


def _session_with_plan(plan_steps: list[dict]) -> TutorSession:
    return TutorSession(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        question_text=QUESTION,
        status="active",
        plan={"steps": plan_steps, "final_answer": "x", "verified": False},
        current_step=1,
        hint_level=0,
        attempts_on_step=0,
        hint_counts={},
        answer_requests=0,
        awaiting_attempt=False,
    )


class TestFigureEvents:
    async def test_full_frames_when_no_limit(self):
        session = _session_with_plan([{"assertion": "a", "figure": {"params": FUNCTION_FIG, "caption": "看图"}}])
        events = [e async for e in SocraticSolverExecutor()._figure_events(session, 1)]
        assert len(events) == 1
        data = events[0]["data"]
        assert data["step_no"] == 1
        assert data["caption"] == "看图"
        assert len(data["frames"]) == 2

    async def test_frame_limit_one(self):
        session = _session_with_plan([{"assertion": "a", "figure": {"params": FUNCTION_FIG, "caption": ""}}])
        events = [e async for e in SocraticSolverExecutor()._figure_events(session, 1, frame_limit=1)]
        assert len(events[0]["data"]["frames"]) == 1
        assert events[0]["data"]["frames"][0]["label"] == "坐标系与曲线"

    async def test_no_figure_no_events(self):
        session = _session_with_plan([{"assertion": "a"}])
        events = [e async for e in SocraticSolverExecutor()._figure_events(session, 1)]
        assert events == []

    async def test_step_out_of_range_no_events(self):
        session = _session_with_plan([{"assertion": "a"}])
        events = [e async for e in SocraticSolverExecutor()._figure_events(session, 2)]
        assert events == []

    async def test_bad_figure_params_swallowed(self):
        """渲染失败只丢图不炸流（异常静默 + 无事件）。"""
        session = _session_with_plan(
            [{"assertion": "a", "figure": {"params": {"type": "nonsense", "params": {}}, "caption": ""}}]
        )
        events = [e async for e in SocraticSolverExecutor()._figure_events(session, 1)]
        assert events == []


# ========== 3. 全旅程（需要 PostgreSQL） ==========


class TestFigureJourney:
    async def test_full_journey(self):
        async with _db() as db:
            user, conv = await _make_user_conv(db)
            ctx = _ctx(db, _make_mock(), user, conv)

            # ① 新题：solver + planner + 开场引导 → 第 1 步构图帧（限帧防泄题）
            events = await _run({"question": QUESTION}, ctx)
            figs = _figures(events)
            assert len(figs) == 1, f"开场应只有 1 个 figure 事件: {len(figs)}"
            assert figs[0]["step_no"] == 1
            assert len(figs[0]["frames"]) == 1  # 仅构图帧，不含 (1,-4) 等答案标注
            import base64 as _b64

            def _decode(uri: str) -> str:
                return _b64.b64decode(uri.split(",", 1)[1]).decode("utf-8")

            assert "(1,-4)" not in _decode(figs[0]["frames"][0]["data_uri"])
            meta = [e["data"] for e in events if e.get("type") == "_result_meta"][0]
            assert meta["figures"] == 2
            # figure 事件在开场引导 token 之后（图文顺序 = 讲解顺序）
            idx_token = next(i for i, e in enumerate(events) if e.get("type") == "token")
            idx_fig = next(i for i, e in enumerate(events) if e.get("type") == "figure")
            assert idx_fig > idx_token

            # ② 学生答对第 1 步：完成步全帧（视觉确认）+ 下一步构图帧
            events = await _run({"question": "顶点在 (1,-4)，开口向上"}, ctx)
            figs = _figures(events)
            assert [f["step_no"] for f in figs] == [1, 2]
            assert len(figs[0]["frames"]) == 2  # 完成步全帧（含答案标注）
            assert len(figs[1]["frames"]) == 1  # 下一步仅构图帧
            assert any("(1,-4)" in _decode(f["data_uri"]) for f in figs[0]["frames"])
            # 下一步构图帧不含交点标注（防泄题）
            assert not any(
                "(-1,0)" in _decode(f["data_uri"]) for f in figs[1]["frames"]
            )

            # ③ 主动要提示：当前步构图帧
            events = await _run({"tutor_action": "hint", "question": ""}, ctx)
            figs = _figures(events)
            assert len(figs) == 1
            assert figs[0]["step_no"] == 2
            assert len(figs[0]["frames"]) == 1

            # ④ 直接看答案（两次确认）→ 总结全帧（无泄题约束）
            events = await _run({"tutor_action": "answer", "question": ""}, ctx)
            assert _figures(events) == []
            events = await _run({"tutor_action": "answer_confirm", "question": ""}, ctx)
            figs = _figures(events)
            assert [f["step_no"] for f in figs] == [1, 2]
            assert all(len(f["frames"]) == 2 for f in figs)
            assert _tokens(events)  # 总结文本不受图形影响

    async def test_no_topic_no_planner_call(self):
        """主题门控：非图形题不调用 planner（零额外 LLM 调用），行为与现状一致。"""
        async with _db() as db:
            user, conv = await _make_user_conv(db)
            llm = _make_mock()
            llm.stream_by_scene["socratic_solver"] = [
                "[[STEP]]\n断言：$12\\times13-7=149$\n原因：乘法分配律\n"
                "最终答案：\\boxed{149}\n难度：easy"
            ]
            llm.stream_by_scene["socratic_guide"] = ["先算乘法部分，你会得到多少？"]
            ctx = _ctx(db, llm, user, conv)
            events = await _run({"question": "计算 12×13-7 等于多少"}, ctx)
            assert _figures(events) == []
            assert not [c for c in llm.chat_calls if c.get("scene") == "socratic_figure_plan"]
            meta = [e["data"] for e in events if e.get("type") == "_result_meta"][0]
            assert meta["figures"] == 0

    async def test_planner_failure_degrades_to_text_only(self):
        """planner 输出非法（两次重试仍非法）→ 纯文字讲解，流程不受影响。"""
        async with _db() as db:
            user, conv = await _make_user_conv(db)
            llm = _make_mock()
            llm.chat_by_scene["socratic_figure_plan"] = ["这不是数组", "还是不对 {bad"]
            ctx = _ctx(db, llm, user, conv)
            events = await _run({"question": QUESTION}, ctx)
            assert _figures(events) == []
            assert _tokens(events)  # 开场引导文本正常
            plan_calls = [c for c in llm.chat_calls if c.get("scene") == "socratic_figure_plan"]
            assert len(plan_calls) == 2  # 重试一次
