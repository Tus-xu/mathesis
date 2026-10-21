"""socratic_solver 引导式解题测试（ADR-030/033 solver-then-guide）

覆盖：
1. extract_boxed 任意嵌套 / [[STEP]] 解析健壮性（缺前缀/变体/单步塌缩）
2. solver self-consistency：一致→verified、仲裁取多数、不一致→诚实降级、解析失败重试→降级
3. 全旅程状态机：答错自动升提示 → hint 动作升级/防滥用 → 答对推进 → 完成（independent/guided）
4. answer/answer_confirm 揭示流程与 answer_requests 计数
5. 防泄题检查：含终答文本被拦截（重生成→兜底模板，leak_blocked 计数）
6. judge JSON 解析降级；sympy 快速通道
7. agent_router 会话粘连：活跃会话消息不走意图路由 + tutor_action 透传 + done.meta 观察信息

需要 PostgreSQL 运行中（与 test_student_pipeline 同环境）。
DB 会话在测试函数内创建（fixture 与测试不同事件循环，不能跨 fixture 持有连接）。
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.conversation import Conversation
from app.models.database import get_db
from app.models.role_binding import RoleBinding
from app.models.tutor_session import TutorSession
from app.models.user import User
from app.skills.base import SkillContext
from app.skills.socratic_solver import parsing, prompts
from app.skills.socratic_solver.main import SocraticSolverExecutor

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ========== 测试物料 ==========

SOL_A = (
    "[[STEP]]\n断言：$x^2-2x-3=0$ 可分解为 $(x-3)(x+1)=0$\n原因：十字相乘法\n"
    "[[STEP]]\n断言：$x=3$ 或 $x=-1$\n原因：零乘积性质\n"
    "最终答案：\\boxed{x=3}"
)
SOL_B = (
    "[[STEP]]\n断言：方程 $x^2-2x-3=0$ 的两根为 $3$ 与 $-1$\n原因：求根公式\n"
    "最终答案：\\boxed{x=3}"
)
SOL_WRONG_ANSWER = (
    "[[STEP]]\n断言：$x^2-2x-3=0$ 可分解为 $(x-2)(x+1)=0$\n原因：十字相乘法\n"
    "最终答案：\\boxed{x=2}"
)
GARBAGE = "这道题很难，我随便说说但没有分步也没有答案。"

QUESTION = "解方程 $x^2-2x-3=0$"


def _plan(steps=None, final_answer="x=3", verified=True):
    return {
        "steps": steps
        or [
            {"assertion": "$x^2-2x-3=0$ 可分解为 $(x-3)(x+1)=0$", "reason": "十字相乘法"},
            {"assertion": "$x=3$ 或 $x=-1$", "reason": "零乘积性质"},
        ],
        "final_answer": final_answer,
        "verified": verified,
        "solve_attempts": 2,
        "consistency": True,
        "leak_blocked": 0,
    }


class MockLLM:
    """模拟 ModelRouter：chat 按 scene 队列返回；chat_stream 按 scene 队列返回（M2 重构）

    M2 重构：solver 改为单路流式调用（chat_stream），因此 chat_stream 也支持
    stream_by_scene 队列（与 chat_by_scene 同手法）。无队列时回退 stream_text 固定文本。
    """

    intended_provider = "mock"

    def __init__(
        self,
        chat_by_scene: dict | None = None,
        stream_text: str = "",
        stream_by_scene: dict | None = None,
    ):
        self.chat_by_scene = {k: list(v) for k, v in (chat_by_scene or {}).items()}
        self.stream_by_scene = {k: list(v) for k, v in (stream_by_scene or {}).items()}
        self.stream_text = stream_text
        self.chat_calls: list[dict] = []  # kwargs 记录（scene/temperature/thinking/max_tokens）
        self.stream_calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        queue = self.chat_by_scene.get(kwargs.get("scene")) or []
        content = queue.pop(0) if queue else "{}"
        return {"content": content}

    def chat_stream(self, messages, **kwargs):
        self.stream_calls.append(kwargs)
        queue = self.stream_by_scene.get(kwargs.get("scene")) or []
        text = queue.pop(0) if queue else self.stream_text

        async def _gen():
            yield {"_provider": "mock"}
            if text:
                yield {"token": text}
            yield {"_usage": {}}

        return _gen()

    def scenes(self, scene: str) -> list[dict]:
        return [c for c in self.chat_calls if c.get("scene") == scene]

    def stream_scenes(self, scene: str) -> list[dict]:
        return [c for c in self.stream_calls if c.get("scene") == scene]


def _judge_json(verdict: str, misconception: str | None = None, hint: str | None = None) -> str:
    return json.dumps(
        {
            "analysis": "分析",
            "verdict": verdict,
            "misconception": misconception,
            "feedback_hint": hint,
        },
        ensure_ascii=False,
    )


# ========== 辅助 ==========


@asynccontextmanager
async def _db():
    """函数级 DB 会话：结束 rollback 保持库干净"""
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def _make_user(session) -> User:
    user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
    session.add(user)
    await session.flush()
    return user


async def _make_conv(session, user: User) -> Conversation:
    conv = Conversation(user_id=user.id, active_role="student", title="新对话")
    session.add(conv)
    await session.flush()
    return conv


async def _make_tutor_session(session, user: User, conv: Conversation, **overrides) -> TutorSession:
    fields = {
        "user_id": user.id,
        "conversation_id": conv.id,
        "question_text": QUESTION,
        "status": "active",
        "plan": _plan(),
        "current_step": 1,
        "hint_level": 0,
        "attempts_on_step": 0,
        "hint_counts": {"point": 0, "teach": 0, "bottom_out": 0},
        "answer_requests": 0,
        "awaiting_attempt": False,
    }
    fields.update(overrides)
    ts = TutorSession(**fields)
    session.add(ts)
    await session.flush()
    return ts


def _ctx(db, llm: MockLLM, user: User, conv: Conversation) -> SkillContext:
    return SkillContext(
        user_id=str(user.id),
        user_role="student",
        conversation_id=str(conv.id),
        request_id="test-req",
        db=db,
        llm=llm,
    )


async def _run(params: dict, ctx: SkillContext) -> list[dict]:
    events = []
    async for ev in SocraticSolverExecutor().run(params, ctx):
        events.append(ev)
    return events


def _cards(events: list[dict], card_type: str | None = None) -> list[dict]:
    cards = [e["data"] for e in events if e.get("type") == "card"]
    if card_type:
        cards = [c for c in cards if c.get("card_type") == card_type]
    return cards


def _tokens(events: list[dict]) -> str:
    return "".join(e["data"].get("text", "") for e in events if e.get("type") == "token")


def _meta(events: list[dict]) -> dict:
    metas = [e["data"] for e in events if e.get("type") == "_result_meta"]
    assert len(metas) == 1, f"_result_meta 应只发一次，实际 {len(metas)}"
    return metas[0]


# ========== 1. 解析层 ==========


class TestParsing:
    def test_extract_boxed_nested(self):
        assert parsing.extract_boxed(r"\boxed{x=\frac{1+\sqrt{5}}{2}}") == r"x=\frac{1+\sqrt{5}}{2}"
        assert parsing.extract_boxed(r"\boxed{\text{见上述推导}}") == r"\text{见上述推导}"
        assert parsing.extract_boxed(r"前文 \boxed{x\in\{1,2\}} 后文") == r"x\in\{1,2\}"
        assert parsing.extract_boxed(r"\boxed{a} 再 \boxed{b}") == "b"  # 取最后一个
        assert parsing.extract_boxed(r"\boxed{未闭合") is None
        assert parsing.extract_boxed("没有答案") is None

    def test_parse_steps_robust(self):
        # 标准两步 + 纯终答段丢弃
        steps = parsing.parse_steps(SOL_A)
        assert len(steps) == 2
        assert steps[0]["assertion"].startswith("$x^2")
        assert steps[0]["reason"] == "十字相乘法"

        # 变体：**断言**：/【原因】/半角冒号/小写 [[step]]
        variant = "[[step]]\n**断言**：$1+1=2$\n【原因】: 加法定义"
        steps = parsing.parse_steps(variant)
        assert len(steps) == 1
        assert steps[0] == {"assertion": "$1+1=2$", "reason": "加法定义"}

        # 缺前缀 → 断言兜底为整段
        no_prefix = parsing.parse_steps("[[STEP]]\n直接写出 $y=2x$")
        assert no_prefix == [{"assertion": "直接写出 $y=2x$", "reason": ""}]

        # 单步塌缩：无 [[STEP]] 时整体按单步解析
        collapsed = parsing.parse_steps("断言：$x>0$\n原因：题设")
        assert collapsed == [{"assertion": "$x>0$", "reason": "题设"}]

    def test_validate_solution_contract(self):
        steps, final, err = parsing.validate_solution(SOL_A)
        assert err is None and len(steps) == 2 and final == "x=3"

        _, _, err = parsing.validate_solution("没有分步标记的一段话")
        assert "[[STEP]]" in err

        _, _, err = parsing.validate_solution("[[STEP]]\n断言：$x=1$\n原因：t")
        assert "boxed" in err

        _, _, err = parsing.validate_solution("[[STEP]]\n   \n[[STEP]]\n  ")
        assert "步骤" in err

    def test_find_leak(self):
        plan = _plan()
        # 终答原文与归一化形式
        assert parsing.find_leak("答案是 x=3", plan, 1) == "final_answer"
        assert parsing.find_leak("所以 ${x} = {3}$", plan, 1) == "final_answer"
        # 后续步骤断言（用与终答不重叠的 plan，隔离 future_step 判定）
        plan2 = _plan(
            steps=[
                {"assertion": "设面积为 $S=6x$", "reason": "题设"},
                {"assertion": "对 $S=6x$ 求导得 $S'=6$", "reason": "幂函数求导"},
            ],
            final_answer="42",
        )
        assert parsing.find_leak("下一步对 $S=6x$ 求导得 $S'=6$", plan2, 1) == "future_step"
        # 当前步断言允许（按级别提示）
        assert (
            parsing.find_leak("想想 $x^2-2x-3=0$ 可分解为 $(x-3)(x+1)=0$ 怎么用？", plan, 1) is None
        )
        # 无关文本
        assert parsing.find_leak("你觉得这题考什么？", plan, 1) is None


# ========== 2. 新题入口：solver + verify ==========


class TestNewProblem:
    async def test_new_problem_success(self):
        """M2 重构：单路流式求解成功 → 落会话，发 start 卡 + 首步引导"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(
                stream_by_scene={"socratic_solver": [SOL_A]},
                stream_text="你觉得这道题考的是哪个知识点？",
            )
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            # 事件序列：status(solving) → card(start) → status(guiding) → token(引导) → _result_meta
            assert events[0]["type"] == "status" and events[0]["data"]["stage"] == "solving"
            start = _cards(events, "socratic_start")
            assert len(start) == 1
            assert start[0]["steps_count"] == 2
            assert "你觉得这道题考的是哪个知识点？" in _tokens(events)
            meta = _meta(events)
            assert meta["steps_count"] == 2
            assert meta["session_id"] == start[0]["session_id"]

            # solver 调用参数契约（迭代15 B7a 延迟治理）：默认快速模式——thinking/emit_thinking 均关，
            # 仅前端显式开「思考模式」时才 True
            solver_calls = llm.stream_scenes("socratic_solver")
            assert len(solver_calls) == 1
            assert solver_calls[0]["thinking"] is False
            assert solver_calls[0]["emit_thinking"] is False

            # 会话落库：plan 完整、状态机初始值
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "active"
            assert ts.current_step == 1 and ts.hint_level == 0
            assert ts.plan["consistency"] is True
            assert len(ts.plan["steps"]) == 2
            assert ts.hint_counts == {"point": 0, "teach": 0, "bottom_out": 0}

    async def test_alt_solution_stored_in_plan(self):
        """M2 重构：模型给出自然另解 → alt_solution 落 plan（可选，非强制）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(
                stream_by_scene={"socratic_solver": [SOL_A + "\n另解：用求根公式直接求解"]},
                stream_text="引导语？",
            )
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            assert len(_cards(events, "socratic_start")) == 1
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "active"
            assert "求根公式" in ts.plan["alt_solution"]

    async def test_llm_failure_degrades(self):
        """M2 重构：LLM 流式失败 → 诚实降级（llm_unavailable），绝不强行引导"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)

            class _FailLLM(MockLLM):
                def chat_stream(self, messages, **kwargs):
                    async def _gen():
                        raise ConnectionError("llm down")
                        yield  # pragma: no cover

                    return _gen()

            llm = _FailLLM(stream_text="不应出现")
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            # 降级话术 + degraded 卡；绝不发 start 卡/引导
            assert len(_cards(events, "socratic_degraded")) == 1
            assert len(_cards(events, "socratic_start")) == 0
            assert "不应出现" not in _tokens(events)
            assert _meta(events)["degraded"] is True

            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "degraded"
            assert ts.plan["verified"] is False

    async def test_parse_failure_retries_then_degrades(self):
        """输出不合契约 → 带反馈重试（≤3 次调用），仍失败 → 诚实降级"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(stream_by_scene={"socratic_solver": [GARBAGE] * 3})
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            assert len(_cards(events, "socratic_degraded")) == 1
            # 1 + 2 次重试 = 3 次 solver 调用
            assert len(llm.stream_scenes("socratic_solver")) == 3
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "degraded"


# ========== 2b. v1.3 截断续写与思考闩锁 ==========

# 截断两半的解答：PART1 缺终答（模拟 length 截断），PART2 续写补完
SOL_PART1 = (
    "[[STEP]]\n断言：$x^2-2x-3=0$ 可分解为 $(x-3)(x+1)=0$\n原因：十字相乘法\n"
    "[[STEP]]\n断言：$x=3$ 或 $x=-1$\n原因：零乘积性质"
)
SOL_PART2 = "最终答案：\\boxed{x=3}"


class _ScriptedStreamLLM(MockLLM):
    """v1.3 续写测试专用：socratic_solver 场景按调用顺序脚本化 (text, finish_reason)，
    其他场景（引导/摘要）走 fallback 文本。每轮脚本 = (正文, finish_reason)，
    流末固定 yield {"_finish": reason}（与真实 provider 对齐）。"""

    def __init__(self, script: list[tuple[str, str]], fallback_text: str = "引导语"):
        super().__init__()
        self.script = list(script)
        self.fallback_text = fallback_text
        self.solver_messages: list[list[dict]] = []

    def chat_stream(self, messages, **kwargs):
        self.stream_calls.append(kwargs)
        if kwargs.get("scene") == "socratic_solver":
            self.solver_messages.append(messages)
            text, finish = self.script.pop(0) if self.script else ("", "stop")
        else:
            text, finish = self.fallback_text, "stop"

        async def _gen():
            yield {"_provider": "mock"}
            if text:
                yield {"token": text}
            yield {"_finish": finish}
            yield {"_usage": {}}

        return _gen()


def _status_texts(events: list[dict]) -> list[str]:
    return [e["data"].get("text", "") for e in events if e.get("type") == "status"]


class TestSolveContinuation:
    async def test_truncated_solution_continues_and_parses(self):
        """正文被 length 截断 → 断点续写（assistant 部分回复+续写指令）→ 拼接后契约解析成功"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = _ScriptedStreamLLM([(SOL_PART1, "length"), (SOL_PART2, "stop")])
            # 显式开思考：保留「思考开截断 → 续写关思考」的原始场景（默认已改为关，见 B7a）
            events = await _run({"question": QUESTION, "thinking": True}, _ctx(db, llm, user, conv))

            # 成功发卡，无降级
            assert len(_cards(events, "socratic_start")) == 1
            assert len(_cards(events, "socratic_degraded")) == 0
            # 出现续写状态提示
            assert "解答篇幅较长，正在续写…" in _status_texts(events)
            # 两次 solver 调用：首调 + 一次续写；续写关思考
            solver_calls = llm.stream_scenes("socratic_solver")
            assert len(solver_calls) == 2
            assert solver_calls[1]["thinking"] is False
            # 续写消息 = 原消息 + assistant(已写部分) + CONTINUATION_PROMPT
            cont_msgs = llm.solver_messages[1]
            assert cont_msgs[-2]["role"] == "assistant" and SOL_PART1 in cont_msgs[-2]["content"]
            assert cont_msgs[-1]["role"] == "user"
            assert "截断" in cont_msgs[-1]["content"]
            # 拼接后的 plan 完整
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "active"
            assert len(ts.plan["steps"]) == 2
            assert ts.plan["final_answer"] == "x=3"

    async def test_thinking_empty_latches_off_then_continues(self):
        """思考吃光预算（正文为空+length）→ 闩锁关思考重试 → 直解仍截断 → 续写成稿"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = _ScriptedStreamLLM(
                [("", "length"), (SOL_PART1, "length"), (SOL_PART2, "stop")]
            )
            # 显式开思考：闩锁场景以思考开为起点（默认已改为关，见 B7a）
            events = await _run({"question": QUESTION, "thinking": True}, _ctx(db, llm, user, conv))

            assert len(_cards(events, "socratic_start")) == 1
            statuses = _status_texts(events)
            assert "这道题比较复杂，换个更直接的方式继续解…" in statuses
            assert "解答篇幅较长，正在续写…" in statuses
            # 三次调用：思考开（空）→ 闩锁关思考（截断）→ 续写（关思考）
            solver_calls = llm.stream_scenes("socratic_solver")
            assert len(solver_calls) == 3
            assert solver_calls[0]["thinking"] is True
            assert solver_calls[1]["thinking"] is False
            assert solver_calls[2]["thinking"] is False

    async def test_thinking_latch_holds_on_parse_retry(self):
        """闩锁持续性：思考吃光后关思考，解析失败重试也不再重开思考（修 v1.3 三次重想空转）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = _ScriptedStreamLLM([("", "length"), (GARBAGE, "stop"), (SOL_A, "stop")])
            # 显式开思考：闩锁持续性场景以思考开为起点（默认已改为关，见 B7a）
            events = await _run({"question": QUESTION, "thinking": True}, _ctx(db, llm, user, conv))

            assert len(_cards(events, "socratic_start")) == 1
            solver_calls = llm.stream_scenes("socratic_solver")
            # 思考开（空）→ 闩锁关思考（垃圾）→ 解析重试仍关思考（成稿）
            assert len(solver_calls) == 3
            assert solver_calls[0]["thinking"] is True
            assert all(c["thinking"] is False for c in solver_calls[1:])
            # 思考预算提示只出现一次（闩锁后不再重复）
            assert _status_texts(events).count("这道题比较复杂，换个更直接的方式继续解…") == 1

    async def test_continuation_exhausted_degrades_solution_incomplete(self):
        """续写次数用尽仍无终答 + 反馈重试也失败 → 诚实降级 solution_incomplete（非不可用）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = _ScriptedStreamLLM(
                [(SOL_PART1, "length")] * 4  # 首调 + 3 次续写全部截断
                + [(GARBAGE, "stop")] * 2  # 两次解析反馈重试也不合契约
            )
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            degraded = _cards(events, "socratic_degraded")
            assert len(degraded) == 1
            assert degraded[0]["reason"] == "solution_incomplete"
            # 续写状态提示恰好 3 次（续写上限）
            assert _status_texts(events).count("解答篇幅较长，正在续写…") == 3
            # 降级话术是 solution_incomplete 的拆问建议
            assert "拆成小问" in _tokens(events)
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.status == "degraded"
            assert ts.plan["degrade_reason"] == "solution_incomplete"


# ========== 3. 多轮状态机 ==========


class TestFollowup:
    async def test_wrong_answer_auto_escalates(self):
        """答错 1 次 → Point 级纠偏 + hint 卡 + attempts 计数"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_judge": [_judge_json("wrong", "calculation", "符号处理错了")]
                },
                stream_text="看看移项时的符号，是不是哪里变了？",
            )
            with patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(return_value={"verdict": "wrong", "method": "symbolic_diff"}),
            ):
                events = await _run({"question": "x=2 吧"}, _ctx(db, llm, user, conv))

            hint = _cards(events, "socratic_hint")
            assert len(hint) == 1 and hint[0]["level"] == 1 and hint[0]["level_name"] == "point"
            progress = _cards(events, "socratic_progress")
            assert len(progress) == 1 and progress[0]["verdict"] == "wrong"
            assert "符号" in _tokens(events)

            await db.refresh(ts)
            assert ts.attempts_on_step == 1
            assert ts.hint_level == 1
            assert ts.hint_counts == {"point": 1, "teach": 0, "bottom_out": 0}
            assert ts.awaiting_attempt is True
            # judge 低延迟：thinking=False
            assert llm.scenes("socratic_judge")[0]["thinking"] is False

    async def test_full_journey(self):
        """答错×2 自动升级 → 答对推进 → hint 动作升级 → 答对完成（guided）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_judge": [
                        _judge_json("wrong", "concept", "概念不清"),
                        _judge_json("wrong", "formula", "公式用错"),
                        _judge_json("correct"),
                        _judge_json("correct"),
                    ]
                },
                stream_text="继续想想？",
            )
            ctx = _ctx(db, llm, user, conv)
            with patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(return_value={"verdict": "pending_review", "method": "parse_error"}),
            ):
                # 第 1 次错 → point
                ev1 = await _run({"question": "随便答一下"}, ctx)
                assert _cards(ev1, "socratic_hint")[0]["level"] == 1
                # 第 2 次错 → teach 自动升级
                ev2 = await _run({"question": "还是不对"}, ctx)
                assert _cards(ev2, "socratic_hint")[0]["level"] == 2
                await db.refresh(ts)
                assert ts.hint_level == 2 and ts.attempts_on_step == 2
                assert ts.hint_counts == {"point": 1, "teach": 1, "bottom_out": 0}

                # 防滥用：未作答直接再要提示 → 只反问不升级
                ev_block = await _run({"question": "给点提示", "tutor_action": "hint"}, ctx)
                assert "想到哪一步了" in _tokens(ev_block)
                assert len(_cards(ev_block, "socratic_hint")) == 0
                await db.refresh(ts)
                assert ts.hint_level == 2  # 未升级
                assert _meta(ev_block).get("hint_blocked") is True

                # 答对第 1 步 → 推进，级别回落
                ev3 = await _run({"question": "分解为 (x-3)(x+1)=0"}, ctx)
                await db.refresh(ts)
                assert ts.current_step == 2
                assert ts.hint_level == 0 and ts.attempts_on_step == 0
                assert ts.awaiting_attempt is False
                assert _cards(ev3, "socratic_progress")[0]["verdict"] == "correct"

                # hint 动作 → 重新从 point 起步
                ev4 = await _run({"question": "提示一下", "tutor_action": "hint"}, ctx)
                hint4 = _cards(ev4, "socratic_hint")
                assert hint4[0]["level"] == 1 and hint4[0]["level_name"] == "point"
                await db.refresh(ts)
                assert ts.hint_counts["point"] == 2 and ts.awaiting_attempt is True

                # 答对最后一步 → 完成（guided：用过提示）
                ev5 = await _run({"question": "x=3 或 x=-1"}, ctx)
                complete = _cards(ev5, "socratic_complete")
                assert len(complete) == 1
                assert complete[0]["outcome"] == "guided"
                assert complete[0]["final_answer"] == "x=3"
                assert complete[0]["hint_stats"]["total"] == 3
                assert _cards(ev5, "socratic_progress")[0]["current_step"] == 2
                await db.refresh(ts)
                assert ts.status == "completed" and ts.completed_at is not None

    async def test_complete_independent_via_sympy(self):
        """零提示零求答 + sympy 快速通道判对 → independent，不调 judge LLM"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            one_step = [{"assertion": "$x=3$", "reason": "直接求解"}]
            ts = await _make_tutor_session(
                db, user, conv, plan=_plan(steps=one_step), current_step=1
            )
            llm = MockLLM(stream_text="全靠自己做完，太棒了！")
            with patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(return_value={"verdict": "correct", "method": "exact_match"}),
            ):
                events = await _run({"question": "$x=3$"}, _ctx(db, llm, user, conv))

            complete = _cards(events, "socratic_complete")
            assert complete[0]["outcome"] == "independent"
            assert "太棒了" in _tokens(events)
            assert not llm.scenes("socratic_judge")  # sympy 快速通道命中，未走 LLM judge
            await db.refresh(ts)
            assert ts.status == "completed"

    async def test_answer_confirm_reveals(self):
        """answer → 二次确认卡；answer_confirm → 揭示完整解答 + revealed"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(stream_text="这是完整解答，对照看看关键在哪一步。")
            ctx = _ctx(db, llm, user, conv)

            ev1 = await _run({"question": "直接告诉我答案", "tutor_action": "answer"}, ctx)
            assert len(_cards(ev1, "socratic_confirm_answer")) == 1
            assert "确定要直接看完整解答吗" in _tokens(ev1)
            await db.refresh(ts)
            assert ts.answer_requests == 1
            assert ts.status == "active"  # 未确认前不揭示
            assert len(_cards(ev1, "socratic_complete")) == 0

            ev2 = await _run({"question": "确认，给我看", "tutor_action": "answer_confirm"}, ctx)
            complete = _cards(ev2, "socratic_complete")
            assert complete[0]["outcome"] == "revealed"
            assert len(complete[0]["steps"]) == 2
            assert complete[0]["final_answer"] == "x=3"
            assert complete[0]["hint_stats"]["answer_requests"] == 1
            await db.refresh(ts)
            assert ts.status == "revealed" and ts.completed_at is not None

    async def test_new_problem_abandons_old(self):
        """判答识别出新题 → 旧会话 abandoned，按新题入口重走（M2：solver 单路流式）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            old = await _make_tutor_session(db, user, conv)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_judge": [_judge_json("new_problem")],
                },
                stream_by_scene={"socratic_solver": [SOL_A]},
                stream_text="新题引导？",
            )

            async def _eq(a, b, **_kw):
                # 判答快速通道（新题表达式≠当前步断言）→ wrong 落到 LLM judge
                if a == b:
                    return {"verdict": "correct", "method": "exact_match"}
                return {"verdict": "wrong", "method": "symbolic_diff"}

            with patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(side_effect=_eq),
            ):
                events = await _run(
                    {"question": "改做这题：求 $2x+1=5$"}, _ctx(db, llm, user, conv)
                )

            assert len(_cards(events, "socratic_start")) == 1
            await db.refresh(old)
            assert old.status == "abandoned"
            all_sessions = (
                (await db.execute(select(TutorSession).where(TutorSession.user_id == user.id)))
                .scalars()
                .all()
            )
            assert len(all_sessions) == 2
            new = [s for s in all_sessions if s.id != old.id][0]
            assert new.status == "active" and new.question_text == "改做这题：求 $2x+1=5$"

    async def test_off_topic_pulls_back(self):
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(chat_by_scene={"socratic_judge": [_judge_json("off_topic")]})
            events = await _run({"question": "今天天气真好"}, _ctx(db, llm, user, conv))

            # v3.1 反复读改造：拉回文案不固定，但必须把学生带回本题且不计作答
            assert "回到这道题" in _tokens(events)
            assert _cards(events, "socratic_progress")[0]["verdict"] == "off_topic"
            await db.refresh(ts)
            assert ts.attempts_on_step == 0 and ts.awaiting_attempt is False

    async def test_challenge_feedback_is_not_reclassified_as_off_topic(self):
        """学生质疑上一轮判断时，必须进入澄清分支而不是重复跑题回退。"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(
                chat_by_scene={"socratic_judge": [_judge_json("clarification")]},
                stream_text="你是在追问刚才的判断。我们先核对这一步所依据的条件。",
            )

            events = await _run({"question": "难道不对吗"}, _ctx(db, llm, user, conv))

            assert prompts.OFF_TOPIC_TEXT not in _tokens(events)
            assert _cards(events, "socratic_progress")[0]["verdict"] == "clarification"
            await db.refresh(ts)
            assert ts.current_step == 1
            assert ts.attempts_on_step == 0
            assert ts.awaiting_attempt is False

    async def test_short_challenge_short_circuits_without_llm_judge(self):
        """「难道不对吗」这类短质疑必须确定性判 clarification，不经 LLM 判答
        （2026-08 截图事故防复发：LLM 误判 off_topic 后复读固定话术）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(stream_text="我们先核对这一步所依据的条件。")

            events = await _run({"question": "难道不对吗"}, _ctx(db, llm, user, conv))

            assert llm.scenes("socratic_judge") == []  # 判答 LLM 未被调用（确定性短路）
            assert "放一放" not in _tokens(events)
            assert _cards(events, "socratic_progress")[0]["verdict"] == "clarification"
            await db.refresh(ts)
            assert ts.attempts_on_step == 0 and ts.awaiting_attempt is False

    async def test_judge_sees_tutor_last_question_and_misconception_corrects(self):
        """judge prompt 必须带上老师上一句；wrong+concept 走针对性纠错分支而非跑题回退
        （截图场景：学生答「因为导数为零可以取到极值」被拉回题目）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            ts.plan = {
                **_plan(),
                "recent_tutor_msgs": ["为什么分子在 $x=1$ 时为零会影响极限存在呢？"],
            }
            await db.flush()
            llm = MockLLM(
                chat_by_scene={
                    "socratic_judge": [
                        _judge_json(
                            "wrong",
                            misconception="concept",
                            hint="点名导数极值条件与极限存在条件是两个不同判断对象",
                        )
                    ]
                },
                stream_text="你把函数极值的导数条件套到了极限存在上——这两个判断的对象不同。",
            )

            events = await _run(
                {"question": "因为导数为零可以取到极值"}, _ctx(db, llm, user, conv)
            )

            judge_calls = llm.scenes("socratic_judge")
            assert judge_calls, "judge 应被调用"
            user_content = judge_calls[0]["messages"][-1]["content"]
            assert "为什么分子在 $x=1$ 时为零会影响极限存在呢" in user_content, (
                "judge 必须看到老师上一问才能识别学生在回应"
            )
            assert "因为导数为零可以取到极值" in user_content
            assert "放一放" not in _tokens(events)
            assert _cards(events, "socratic_progress")[0]["verdict"] == "wrong"
            await db.refresh(ts)
            assert ts.attempts_on_step == 1  # 计入作答，而不是被当成跑题丢弃

    async def test_off_topic_reply_never_repeats(self):
        """连续跑题：回复必须轮换，不得连续重复同一句，也不得复读旧固定话术"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_judge": [
                        _judge_json("off_topic"),
                        _judge_json("off_topic"),
                        _judge_json("off_topic"),
                    ],
                    "socratic_reanchor": ["我们先看这一步：分母趋于 0 时分子要满足什么条件？"],
                },
            )
            ctx = _ctx(db, llm, user, conv)
            ev1 = await _run({"question": "老师你几岁了"}, ctx)
            ev2 = await _run({"question": "嘿嘿"}, ctx)
            ev3 = await _run({"question": "嘿嘿嘿"}, ctx)

            t1, t2, t3 = (_tokens(e).strip() for e in (ev1, ev2, ev3))
            assert t1 and t2 and t3
            assert t1 != t2 != t3 and t1 != t3  # 三次回复两两不同
            assert "放一放" not in t1  # 不再复读旧固定话术
            from app.skills.socratic_solver.prompts import OFF_TOPIC_REANCHOR_VARIANTS

            assert t2 in OFF_TOPIC_REANCHOR_VARIANTS  # 第二次起走确定性轮换文案
            assert t3 in OFF_TOPIC_REANCHOR_VARIANTS and t3 != t2
            for ev in (ev1, ev2, ev3):
                assert _cards(ev, "socratic_progress")[0]["verdict"] == "off_topic"
            await db.refresh(ts)
            assert ts.attempts_on_step == 0


# ========== 4. 防泄题 ==========


class TestLeakGuard:
    async def test_leaky_guide_regen_still_leaks_fallback(self):
        """首步引导含终答 → 严指令重生成仍含 → 零泄露兜底模板，leak_blocked=2"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_guide": ["别绕了，答案就是 x=3"],  # 重生成仍泄露
                },
                stream_by_scene={"socratic_solver": [SOL_A]},
                stream_text="这题答案是 x=3，记住了吗？",
            )
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            assert _tokens(events) == prompts.FALLBACK_GUIDE_TEXT
            assert "x=3" not in _tokens(events)
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.plan["leak_blocked"] == 2

    async def test_leaky_guide_regen_clean(self):
        """首次泄露 → 重生成干净 → 用重生成文本，leak_blocked=1"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(
                chat_by_scene={
                    "socratic_guide": ["你觉得这个方程能化成什么形式？"],
                },
                stream_by_scene={"socratic_solver": [SOL_A]},
                stream_text="答案是 x=3 哦",  # 首次流式泄露
            )
            events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            assert _tokens(events) == "你觉得这个方程能化成什么形式？"
            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.plan["leak_blocked"] == 1


# ========== 5. judge 解析 ==========


class TestJudgeParse:
    def test_parse_variants(self):
        executor = SocraticSolverExecutor()
        verdict, judge = executor._parse_judge(_judge_json("wrong", "formula", "检查公式"))
        assert verdict == "wrong" and judge["misconception"] == "formula"

        # 散文包裹 JSON 可提取
        verdict, _ = executor._parse_judge(
            '好的。\n{"verdict": "correct", "misconception": null}\n以上。'
        )
        assert verdict == "correct"

        # 非法 verdict / 非法 misconception / 纯垃圾 → partial 安全降级
        verdict, judge = executor._parse_judge('{"verdict": "maybe"}')
        assert verdict == "partial"
        verdict, judge = executor._parse_judge(_judge_json("wrong", "weird"))
        assert verdict == "wrong" and judge["misconception"] is None
        verdict, _ = executor._parse_judge("无法判断")
        assert verdict == "partial"
        verdict, _ = executor._parse_judge(None)
        assert verdict == "partial"


# ========== 6. agent_router 会话粘连 ==========


def _parse_sse(text: str) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    current_event = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
            events.setdefault(current_event, [])
        elif line.startswith("data: ") and current_event:
            events[current_event].append(json.loads(line[6:]))
    return events


class TestRouterSticky:
    async def _seed(self):
        """建用户 + 会话 + active tutor_session（committed，供 HTTP 链路读取）"""
        from app.skills.registry import get_skill_registry

        async with _test_session_factory() as session:
            await get_skill_registry().sync_to_db(session)  # skill_runs 外键依赖
            user = User(phone=f"138{uuid.uuid4().int % 100000000:08d}", nickname="")
            session.add(user)
            await session.flush()
            session.add(RoleBinding(user_id=user.id, role="student", verified=True))
            await session.flush()
            conv = Conversation(user_id=user.id, active_role="student", title="新对话")
            session.add(conv)
            await session.flush()
            ts = TutorSession(
                user_id=user.id,
                conversation_id=conv.id,
                question_text=QUESTION,
                status="active",
                plan=_plan(),
                current_step=1,
                hint_level=0,
                attempts_on_step=0,
                hint_counts={"point": 0, "teach": 0, "bottom_out": 0},
                answer_requests=0,
                awaiting_attempt=False,
            )
            session.add(ts)
            await session.commit()
            return user, conv, ts

    def _headers(self, user: User) -> dict:
        from app.gateway.jwt import create_token_with_role

        token = create_token_with_role(
            user_id=str(user.id), role="student", roles=["student"], verified=True
        )
        return {"Authorization": f"Bearer {token}"}

    async def test_active_session_skips_intent_router(self, client):
        """活跃 tutor_session → 跳过 LLM 意图路由直进 socratic_solver；done.meta 带观察信息"""
        user, conv, ts = await self._seed()
        mock_intent = MagicMock()
        mock_intent.route = AsyncMock(side_effect=AssertionError("不应调用意图路由"))
        llm = MockLLM(
            chat_by_scene={"socratic_judge": [_judge_json("correct")]},
            stream_text="做对了！下一步想想根是多少？",
        )
        client_msg_id = uuid.uuid4().hex[:20]

        with (
            patch("app.gateway.agent_router.get_intent_router", return_value=mock_intent),
            patch(
                "app.gateway.agent_router.get_model_router_for_user",
                new=AsyncMock(return_value=llm),
            ),
            patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(return_value={"verdict": "pending_review", "method": "parse_error"}),
            ),
            patch("app.gateway.agent_router._bg_summary", new=AsyncMock()),
            patch("app.gateway.agent_router._start_title_task", return_value=None),
        ):
            resp = await client.post(
                "/api/agent/chat",
                json={
                    "conversation_id": str(conv.id),
                    "message": "分解为 (x-3)(x+1)=0",
                    "context": {"client_msg_id": client_msg_id, "workspace": "student"},
                },
                headers=self._headers(user),
            )

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert "error" not in events, events.get("error")
        mock_intent.route.assert_not_called()
        # meta 直选 socratic_solver；done.meta 带 steps_count/session_id（_result_meta 不再被丢弃）
        assert events["meta"][0]["skill"] == "socratic_solver"
        done_meta = events["done"][0].get("meta", {})
        assert done_meta["session_id"] == str(ts.id)
        assert done_meta["steps_count"] == 2
        assert done_meta["verdict"] == "correct"
        # 进度卡透传
        assert any(c.get("card_type") == "socratic_progress" for c in events.get("card", []))

        # 落库信封 meta.extra 同步（幂等重放可还原）
        from app.models.message import Message

        async with _test_session_factory() as session:
            msg = (
                await session.execute(
                    select(Message).where(Message.client_msg_id == f"ai_{client_msg_id}")
                )
            ).scalar_one()
            extra = (msg.envelope or {}).get("meta", {}).get("extra", {})
            assert extra["session_id"] == str(ts.id)

    async def test_tutor_action_passthrough(self, client):
        """tutor_action=hint 经 params 透传 → 发 socratic_hint 卡"""
        user, conv, ts = await self._seed()
        llm = MockLLM(stream_text="回顾一下求根相关的知识点？")
        client_msg_id = uuid.uuid4().hex[:20]

        with (
            patch(
                "app.gateway.agent_router.get_model_router_for_user",
                new=AsyncMock(return_value=llm),
            ),
            patch("app.gateway.agent_router._bg_summary", new=AsyncMock()),
            patch("app.gateway.agent_router._start_title_task", return_value=None),
        ):
            resp = await client.post(
                "/api/agent/chat",
                json={
                    "conversation_id": str(conv.id),
                    "message": "给点提示",
                    "context": {
                        "client_msg_id": client_msg_id,
                        "workspace": "student",
                        "tutor_action": "hint",
                    },
                },
                headers=self._headers(user),
            )

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        hints = [c for c in events.get("card", []) if c.get("card_type") == "socratic_hint"]
        assert len(hints) == 1 and hints[0]["level"] == 1

        async with _test_session_factory() as session:
            ts2 = await session.get(TutorSession, ts.id)
            assert ts2.hint_level == 1 and ts2.awaiting_attempt is True
            assert ts2.hint_counts == {"point": 1, "teach": 0, "bottom_out": 0}


# ========== 8. 迭代02：解析层 v2（难度行 / TIR 代码块 / 步骤验证 / 关键量防泄题） ==========


class TestParsingV2:
    def test_extract_difficulty(self):
        assert parsing.extract_difficulty("难度：hard\n[[STEP]]") == "hard"
        assert parsing.extract_difficulty("难度: 基础") == "easy"
        assert parsing.extract_difficulty("难度：压轴") == "hard"
        assert parsing.extract_difficulty("没有难度行") == "medium"
        assert parsing.extract_difficulty("") == "medium"

    def test_extract_and_strip_code_blocks(self):
        raw = "先算一下：\n```python\nprint(1+1)\n```\n[[STEP]]\n断言：A\n原因：B\n最终答案：\\boxed{x=3}"
        blocks = parsing.extract_code_blocks(raw)
        assert blocks == ["print(1+1)"]
        stripped = parsing.strip_code_blocks(raw)
        assert "print(1+1)" not in stripped
        assert "[[STEP]]" in stripped

    def test_parse_solver_output_strips_code_and_reads_difficulty(self):
        raw = (
            "难度：medium\n```python\nprint(3*3)\n```\n"
            "[[STEP]]\n断言：$x=3$\n原因：计算\n最终答案：\\boxed{x=3}"
        )
        steps, final_answer, difficulty, alt_solution, error = parsing.parse_solver_output(raw)
        assert error is None
        assert steps and final_answer == "x=3" and difficulty == "medium"
        assert alt_solution == ""  # 无「另解：」段时为空字符串

    def test_parse_solver_output_reads_alt_solution(self):
        """M2 重构：可选「另解：」段解析"""
        raw = (
            "[[STEP]]\n断言：$x=3$\n原因：计算\n最终答案：\\boxed{x=3}\n"
            "另解：用判别式法，将方程看作关于 y 的二次方程"
        )
        steps, final_answer, difficulty, alt_solution, error = parsing.parse_solver_output(raw)
        assert error is None
        assert alt_solution and "判别式" in alt_solution

    def test_find_leak_key_quantity_variant(self):
        """终答等式右值的 LaTeX 变体（关键量归一化包含）应判为泄题"""
        plan = _plan(final_answer="x=\\frac{1+\\sqrt{5}}{2}")
        leak = parsing.find_leak("所以结果是 $\\frac{1+\\sqrt{5}}{2}$ 哦", plan, 1)
        assert leak == "final_answer"
        # 不含关键量的正常引导不拦截
        assert parsing.find_leak("你想想判别式应该怎么算？", plan, 1) is None


# ========== 9. 迭代02：TIR 回填 / 步骤级验证 / 修复与降级 ==========

SOL_WITH_CODE = "我先用程序验证一下判别式：\n```python\nprint(4 + 12)\n```\n"


class TestSolverTIR:
    async def test_tir_code_execution_feedback(self):
        """solver 中途输出代码块 → 沙箱执行 → 结果回填后续解；tool_calls 落 plan

        （M2 重构保留 TIR：SymPy 作"计算器"而非"裁判"——只回填真实计算结果，
        不再对答案做真假裁决）
        """
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(
                stream_by_scene={"socratic_solver": [SOL_WITH_CODE, SOL_A]},
                stream_text="引导语？",
            )
            sandbox = AsyncMock(
                return_value={
                    "exec_status": "pass",
                    "stdout": "16\n",
                    "result_repr": "",
                    "error": "",
                }
            )
            with patch("app.skills.socratic_solver.main.run_sandbox", new=sandbox):
                events = await _run({"question": QUESTION}, _ctx(db, llm, user, conv))

            sandbox.assert_awaited_once()
            assert "print(4 + 12)" in sandbox.await_args.args[0]
            assert len(_cards(events, "socratic_start")) == 1
            stages = [e["data"]["stage"] for e in events if e.get("type") == "status"]
            assert "verify_compute" in stages

            ts = (
                await db.execute(select(TutorSession).where(TutorSession.user_id == user.id))
            ).scalar_one()
            assert ts.plan["tool_calls"] and ts.plan["tool_calls"][0]["exec_status"] == "pass"


# ========== 10. M2.2：regenerate/结束后动作 上下文恢复（幻觉修复回归） ==========


class TestTutorContextRecovery:
    """用户实测 bug：点「直接看答案」后再点重新生成，模型丢引导上下文幻觉自编题目。
    修复契约：
    - 结束后（revealed/completed）会话上的动作 → 幂等重发完整解答（纯读，不污染状态机）
    - 动作消息无会话 → 诚实兜底，绝不进新题入口
    - _regenerate 语义 → 重跑内容但会话状态快照恢复（不重复计数/不推进/不新建）
    - tutor_session_id 参数 → 精确恢复原会话（优先于 active 粘连）
    """

    async def test_action_on_revealed_session_reemits_solution(self):
        """revealed 会话上再点「直接看答案」→ 重发同一 plan 的完整解答，状态字段零变化"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(
                db, user, conv, status="revealed", answer_requests=1,
                completed_at=datetime.now(UTC),
            )
            completed_at_before = ts.completed_at
            llm = MockLLM(stream_text="这是完整解答，对照看看。")

            events = await _run(
                {"question": "直接看答案", "tutor_action": "answer"},
                _ctx(db, llm, user, conv),
            )

            complete = _cards(events, "socratic_complete")
            assert len(complete) == 1
            assert complete[0]["outcome"] == "revealed"
            assert complete[0]["final_answer"] == "x=3"  # 原 plan，不是新编的题
            assert len(_cards(events, "socratic_start")) == 0  # 绝不进新题入口
            assert not llm.stream_scenes("socratic_solver")  # 未重新求解
            await db.refresh(ts)
            assert ts.status == "revealed"
            assert ts.answer_requests == 1  # 不重复计数
            assert ts.completed_at == completed_at_before  # 用时不被覆盖

    async def test_action_on_completed_session_keeps_outcome_and_status(self):
        """completed（独立完成）会话上的动作 → 重发但 outcome/status 不被改写成 revealed"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(
                db, user, conv, status="completed", completed_at=datetime.now(UTC)
            )
            llm = MockLLM(stream_text="再对照一次完整解答。")

            events = await _run(
                {"question": "直接看答案", "tutor_action": "answer_confirm"},
                _ctx(db, llm, user, conv),
            )

            complete = _cards(events, "socratic_complete")
            assert len(complete) == 1
            assert complete[0]["outcome"] == "independent"  # 保持原 outcome
            await db.refresh(ts)
            assert ts.status == "completed"  # 不被 _on_reveal 改写

    async def test_action_without_session_honest_fallback(self):
        """动作消息到达但会话内无任何引导会话 → 诚实引导语，绝不幻觉出题"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            llm = MockLLM(stream_text="不该出现")

            events = await _run(
                {"question": "来点提示", "tutor_action": "hint"},
                _ctx(db, llm, user, conv),
            )

            assert prompts.NO_SESSION_ACTION_TEXT in _tokens(events)
            assert _meta(events)["confidence"] == 0.3
            assert len(_cards(events, "socratic_start")) == 0
            assert not llm.stream_scenes("socratic_solver")  # 未调 solver

    async def test_regenerate_answer_confirm_snapshot_restores(self):
        """regenerate 揭示消息 → 重发完整解答；answer_requests/completed_at 恢复原值"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(
                db, user, conv, status="revealed", answer_requests=1,
                completed_at=datetime.now(UTC),
            )
            completed_at_before = ts.completed_at
            llm = MockLLM(stream_text="重新生成：同一份完整解答。")

            events = await _run(
                {
                    "question": "确认查看完整解答",
                    "tutor_action": "answer_confirm",
                    "_regenerate": True,
                },
                _ctx(db, llm, user, conv),
            )

            assert len(_cards(events, "socratic_complete")) == 1
            await db.refresh(ts)
            assert ts.status == "revealed"
            assert ts.answer_requests == 1  # _on_reveal 路径计数被快照恢复
            assert ts.completed_at == completed_at_before

    async def test_regenerate_hint_no_level_bump(self):
        """regenerate 提示消息 → 提示重发；hint_level 回落原值（不连续升级）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv, hint_level=1)
            llm = MockLLM(stream_text="换个说法再提示一次。")

            events = await _run(
                {"question": "来点提示", "tutor_action": "hint", "_regenerate": True},
                _ctx(db, llm, user, conv),
            )

            assert len(_cards(events, "socratic_hint")) == 1  # 提示确实重发
            await db.refresh(ts)
            assert ts.hint_level == 1  # 不升级
            assert ts.hint_counts == {"point": 0, "teach": 0, "bottom_out": 0}  # 计数恢复
            assert ts.awaiting_attempt is False

    async def test_regenerate_attempt_no_double_advance(self):
        """regenerate 作答判对消息 → 反馈重发；current_step 不二次推进"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv, current_step=2)  # 已推进到第 2 步
            llm = MockLLM(stream_text="答得好，继续。")
            ctx = _ctx(db, llm, user, conv)
            with patch(
                "app.skills.socratic_solver.main.check_equivalence",
                new=AsyncMock(return_value={"verdict": "correct", "method": "exact_match"}),
            ):
                await _run(
                    {"question": "$x=3$ 或 $x=-1$", "_regenerate": True}, ctx
                )

            await db.refresh(ts)
            assert ts.current_step == 2  # 未被推进到 3/未完成
            assert ts.status == "active"
            assert ts.completed_at is None

    async def test_regenerate_new_problem_verdict_no_abandon(self):
        """regenerate 判成新题 → 按跑题处理：不放弃旧会话、不新建会话"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            ts = await _make_tutor_session(db, user, conv)
            llm = MockLLM(chat_by_scene={"socratic_judge": [_judge_json("new_problem")]})

            events = await _run(
                {"question": "另外这道题怎么做", "_regenerate": True},
                _ctx(db, llm, user, conv),
            )

            assert prompts.NEW_PROBLEM_REGEN_TEXT in _tokens(events)
            await db.refresh(ts)
            assert ts.status == "active"  # 未 abandoned
            count = (
                await db.execute(
                    select(TutorSession).where(TutorSession.conversation_id == conv.id)
                )
            ).scalars().all()
            assert len(count) == 1  # 未新建会话

    async def test_tutor_session_id_recovers_ended_session(self):
        """params.tutor_session_id 精确恢复指定会话（优先于 active 粘连）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            old = await _make_tutor_session(
                db, user, conv, status="revealed",
                plan=_plan(final_answer="x=old"), completed_at=datetime.now(UTC),
            )
            await _make_tutor_session(db, user, conv)  # 另一个 active 会话干扰
            llm = MockLLM(stream_text="重发旧题解答。")

            events = await _run(
                {
                    "question": "确认查看完整解答",
                    "tutor_action": "answer_confirm",
                    "tutor_session_id": str(old.id),
                    "_regenerate": True,
                },
                _ctx(db, llm, user, conv),
            )

            complete = _cards(events, "socratic_complete")
            assert len(complete) == 1
            assert complete[0]["final_answer"] == "x=old"  # 恢复的是指定旧会话
            assert _meta(events)["session_id"] == str(old.id)

    async def test_tutor_action_persisted_in_meta(self):
        """tutor_action 写入 _result_meta（随 envelope.meta.extra 落库，供 regenerate 恢复）"""
        async with _db() as db:
            user = await _make_user(db)
            conv = await _make_conv(db, user)
            await _make_tutor_session(db, user, conv)
            llm = MockLLM()

            events = await _run(
                {"question": "直接看答案", "tutor_action": "answer"},
                _ctx(db, llm, user, conv),
            )

            assert _meta(events)["tutor_action"] == "answer"
