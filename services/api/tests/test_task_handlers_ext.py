"""任务处理器扩展测试（S-B3 classroom.session / S-B4 socratic.autosolve）

仿 test_task_center.py：专用测试库（conftest 强制）+ fake 生成内核/技能，
不触发真实 LLM、不触网。覆盖：
- classroom：会话创建 + ready 终态 result.jump / 课程未 ready 自动链式预处理 /
  会话 failed → TaskPermanentError / 课程不存在快速失败
- socratic：对话落库（user/assistant 消息 + envelope）/ 图题 file_id 附件链路 /
  source_error_id 关联 / 技能 error 事件 → 失败 / 缺参快速失败
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.classroom import ClassroomSession
from app.models.conversation import Conversation
from app.models.course import Course
from app.models.file import File, FileAsset
from app.models.message import Message
from app.models.task import Task
from app.models.user import User
from app.services import (
    task_handlers_classroom,  # noqa: F401  （import 副作用：注册处理器）
    task_handlers_socratic,  # noqa: F401
    task_runner,
)


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _ensure_user(user_id: uuid.UUID, phone: str) -> None:
    """插入真实 users 行满足 tasks.user_id 外键（幂等）。"""
    async with _test_session_factory() as db:
        exists = await db.get(User, user_id)
        if exists is None:
            db.add(User(id=user_id, phone=phone))
            await db.commit()


async def _wait_terminal(task_id: uuid.UUID, timeout_s: float = 8.0) -> Task:
    """轮询等待任务到终态（runner 在事件循环内异步执行）。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        async with _test_session_factory() as db:
            task = await db.get(Task, task_id)
            if task is not None and task.status in ("succeeded", "failed", "cancelled"):
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout_s}s 内到达终态")


async def _create_task(user_id: uuid.UUID, kind: str, payload: dict) -> Task:
    async with _test_session_factory() as db:
        task, _ = await task_runner.create_task(
            db, user_id=user_id, role="student", kind=kind,
            payload=payload, idempotency_key=f"it-ext-{uuid.uuid4()}",
        )
        await db.commit()
        task_runner.spawn(task)
        return task


def _fake_generation(status: str, *, slides=None, error=None, calls=None):
    """替代 stage_router._run_generation：直接改会话行状态（模拟生成内核终态）。"""

    async def fake_gen(session_id: str) -> None:
        if calls is not None:
            calls.append(session_id)
        async with _test_session_factory() as db:
            s = await db.get(ClassroomSession, uuid.UUID(session_id))
            if s is None:
                return
            s.status = status
            if slides is not None:
                s.slides = slides
            if error is not None:
                s.error = error
            await db.commit()

    return fake_gen


@pytest.fixture
def fast_poll(monkeypatch):
    """把课堂轮询间隔压到 50ms（测试不等待真实 2s 节拍）。"""
    monkeypatch.setattr(task_handlers_classroom, "CLASSROOM_POLL_INTERVAL_S", 0.05)


# ========== S-B3 classroom.session ==========


async def test_classroom_ready_course_creates_session(fast_poll, monkeypatch):
    """课程已 ready：建会话 → fake 生成置 ready → result.jump/page_count 正确。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        course = Course(
            user_id=user_id, title="高一函数导学", transcript="字幕全文", status="ready"
        )
        db.add(course)
        await db.commit()
        course_id = course.id

    calls: list[str] = []
    monkeypatch.setattr(
        task_handlers_classroom,
        "_run_generation",
        _fake_generation("ready", slides=[{"order": 1}, {"order": 2}, {"order": 3}], calls=calls),
    )

    async def _boom(cid):  # ready 课程不应触发预处理
        raise AssertionError("ready 课程不应触发链式预处理")

    monkeypatch.setattr(task_handlers_classroom, "_run_course_preprocess", _boom)

    task = await _create_task(
        user_id, "classroom.session",
        {"course_id": str(course_id), "title": "管家专属课堂", "outline_mode": "standard"},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    sid = final.result["session_id"]
    assert final.result["artifact_type"] == "classroom"
    assert final.result["course_id"] == str(course_id)
    assert final.result["page_count"] == 3
    assert final.result["jump"] == f"/dual/{sid}"
    assert calls == [sid], "生成内核应以端点同款方式被调度一次"

    async with _test_session_factory() as db:
        s = await db.get(ClassroomSession, uuid.UUID(sid))
        assert s is not None
        assert s.course_id == course_id and str(s.user_id) == str(user_id)
        assert s.title == "管家专属课堂" and s.status == "ready"


async def test_classroom_chains_course_preprocess(fast_poll, monkeypatch):
    """课程未 ready：先直接 await 课程预处理内核（S-B3 自动链式），再建会话。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        course = Course(
            user_id=user_id, title="待处理课", transcript="字幕全文", status="pending"
        )
        db.add(course)
        await db.commit()
        course_id = course.id

    pre_calls: list[str] = []

    async def fake_preprocess(cid: str) -> None:
        pre_calls.append(cid)
        async with _test_session_factory() as db:
            c = await db.get(Course, uuid.UUID(cid))
            c.status = "ready"
            await db.commit()

    monkeypatch.setattr(task_handlers_classroom, "_run_course_preprocess", fake_preprocess)
    monkeypatch.setattr(
        task_handlers_classroom, "_run_generation", _fake_generation("ready", slides=[{"order": 1}])
    )

    task = await _create_task(user_id, "classroom.session", {"course_id": str(course_id)})
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert pre_calls == [str(course_id)]
    # 标题回退：payload 无 title → 取课程标题
    async with _test_session_factory() as db:
        s = await db.get(ClassroomSession, uuid.UUID(final.result["session_id"]))
        assert s.title == "待处理课"


async def test_classroom_failed_session_maps_to_task_failure(fast_poll, monkeypatch):
    """生成内核置 failed（会话行已是 failed）→ 任务失败且错误为人话文案。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        course = Course(
            user_id=user_id, title="会失败的课", transcript="字幕全文", status="ready"
        )
        db.add(course)
        await db.commit()
        course_id = course.id

    monkeypatch.setattr(
        task_handlers_classroom,
        "_run_generation",
        _fake_generation("failed", error="AI 生成通道异常"),
    )

    task = await _create_task(user_id, "classroom.session", {"course_id": str(course_id)})
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "课堂生成失败" in (final.error or "")

    # 会话行状态 = failed（生成内核语义，不由任务改写）
    async with _test_session_factory() as db:
        s = await db.get(ClassroomSession, uuid.UUID(final.payload["_session_id"]))
        assert s is not None and s.status == "failed"


async def test_classroom_missing_course_fails_fast():
    """课程不存在 → TaskPermanentError（不建会话、不调度生成）。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    task = await _create_task(
        user_id, "classroom.session", {"course_id": str(uuid.uuid4())}
    )
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "课程不存在" in (final.error or "")
    assert (final.result or {}).get("session_id") is None


# ========== S-B4 socratic.autosolve ==========


class _FakeSocraticSkill:
    """替身技能：回放固定事件流并记录 params（不触网）。"""

    manifest = {"id": "socratic_solver", "name": "引导式解题(测试替身)"}

    def __init__(self, events):
        self.events = events
        self.seen_params: list[dict] = []

    @property
    def skill_id(self) -> str:
        return self.manifest.get("id", "unknown")

    @property
    def skill_name(self) -> str:
        return self.manifest.get("name", "Unknown")

    async def run(self, params: dict, ctx):
        self.seen_params.append(dict(params))
        for ev in self.events:
            yield ev


@pytest.fixture
def install_socratic_skill(monkeypatch):
    """把注册表中的 socratic_solver 换成测试替身，用后恢复内置实现。"""
    from app.skills.registry import get_skill_registry
    from app.skills.socratic_solver.main import SocraticSolverExecutor

    registry = get_skill_registry()
    original = registry.get("socratic_solver")
    installed: list[_FakeSocraticSkill] = []

    def _install(events) -> _FakeSocraticSkill:
        skill = _FakeSocraticSkill(events)
        installed.append(skill)
        registry.register(skill)  # 同 id 覆盖
        return skill

    yield _install
    if original is not None:
        registry.register(original)
    else:
        registry.register(SocraticSolverExecutor())


_OK_EVENTS = [
    {"type": "status", "data": {"stage": "solving", "text": "正在分析题目并求解…"}},
    {"type": "token", "data": {"text": "我们先看已知条件："}},
    {"type": "token", "data": {"text": "x 的取值范围是什么？"}},
    {"type": "card", "data": {"card_type": "socratic_start", "session_id": "fake-sid", "steps_count": 3}},
    {"type": "_result_meta", "data": {"skill": "socratic_solver", "confidence": 0.9, "session_id": "fake-sid"}},
]


async def test_socratic_persists_conversation_and_messages(install_socratic_skill):
    """question_text 输入：技能跑通 → conversation/user+assistant 消息落库 + result。"""
    skill = install_socratic_skill(list(_OK_EVENTS))
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")

    task = await _create_task(
        user_id, "socratic.autosolve", {"question_text": "解方程 x^2-2x-3=0"}
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert final.result["artifact_type"] == "socratic"
    assert final.result["jump"] == "/chat"

    conv_id = uuid.UUID(final.result["conversation_id"])
    msg_id = uuid.UUID(final.result["message_id"])
    assert skill.seen_params and skill.seen_params[0]["question"] == "解方程 x^2-2x-3=0"

    async with _test_session_factory() as db:
        conv = await db.get(Conversation, conv_id)
        assert conv is not None and conv.message_count == 2
        rows = (
            await db.execute(select(Message).where(Message.conversation_id == conv_id))
        ).scalars().all()
        assert len(rows) == 2
        # 同事务 now() 时间戳相同，按角色取（user 在前为业务语义）
        user_msg = next(m for m in rows if m.role == "user")
        assistant_msg = next(m for m in rows if m.role == "assistant")
        assert user_msg.role == "user" and user_msg.content == "解方程 x^2-2x-3=0"
        assert assistant_msg.id == msg_id
        assert assistant_msg.role == "assistant"
        assert assistant_msg.skill_id == "socratic_solver"
        assert assistant_msg.parent_id == user_msg.id
        assert assistant_msg.content == "我们先看已知条件：x 的取值范围是什么？"
        blocks = assistant_msg.envelope["blocks"]
        assert blocks[0] == {"type": "markdown", "content": assistant_msg.content}
        assert any(b.get("type") == "card" and b["data"]["card_type"] == "socratic_start" for b in blocks)
        assert assistant_msg.envelope["meta"]["extra"]["autosolve"] is True


async def test_socratic_image_file_and_error_source(install_socratic_skill):
    """file_id 图题：附件元数据 + OCR 文本注入 + image_file_ids；source_error_id 关联信封。"""
    skill = install_socratic_skill(list(_OK_EVENTS))
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    error_id = uuid.uuid4()

    async with _test_session_factory() as db:
        f = File(
            user_id=user_id, filename="题目.png", mime="image/png", size_bytes=1024,
            sha256=uuid.uuid4().hex, file_type="image", status="parsed",
        )
        db.add(f)
        await db.flush()
        db.add(FileAsset(file_id=f.id, asset_type="markdown", page_no=1, content="如图，四棱锥 P-ABCD 中 PA⊥底面。"))
        await db.commit()
        file_id = f.id

    task = await _create_task(
        user_id, "socratic.autosolve",
        {"file_id": str(file_id), "source_error_id": str(error_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error

    params = skill.seen_params[0]
    assert params["image_file_ids"] == [str(file_id)]
    assert "四棱锥" in params["attachment_context"]
    assert "请解这道题" in params["question"]

    async with _test_session_factory() as db:
        conv_id = uuid.UUID(final.result["conversation_id"])
        rows = (
            await db.execute(select(Message).where(Message.conversation_id == conv_id))
        ).scalars().all()
        user_msg = next(m for m in rows if m.role == "user")
        assistant_msg = next(m for m in rows if m.role == "assistant")
        assert user_msg.content == "请解这道题"
        assert user_msg.attachments[0]["file_id"] == str(file_id)
        assert user_msg.attachments[0]["kind"] == "image"
        assert user_msg.envelope["meta"]["source_error_id"] == str(error_id)
        assert assistant_msg.envelope["meta"]["extra"]["source_error_id"] == str(error_id)


async def test_socratic_skill_error_event_fails_task(install_socratic_skill):
    """技能 error 事件 → 任务失败且错误人话透出。"""
    install_socratic_skill([
        {"type": "status", "data": {"stage": "solving", "text": "正在求解…"}},
        {"type": "error", "data": {"code": 50201, "message": "模型服务暂不可用", "recoverable": True}},
    ])
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    task = await _create_task(
        user_id, "socratic.autosolve", {"question_text": "证明素数有无穷多个"}
    )
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "引导解题失败" in (final.error or "") and "模型服务暂不可用" in (final.error or "")


async def test_socratic_missing_input_fails_fast():
    """question_text 与 file_id 均缺省 → 快速失败（不建对话不调技能）。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    task = await _create_task(user_id, "socratic.autosolve", {})
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "题目文字或题目图片" in (final.error or "")
    assert (final.result or {}).get("conversation_id") is None
