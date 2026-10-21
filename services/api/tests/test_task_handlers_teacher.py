"""教师端任务处理器测试（T-B2 teacher.quiz.create / T-B3 teacher.materials）

仿 test_task_handlers_ext.py：专用测试库（conftest 强制）+ monkeypatch 底层函数，
不触发真实 LLM、不触网。覆盖：
- quiz.create：题库命中 → quiz_set 产物落库 / 部分命中 → qualified/pending 标注 +
  任务仍 succeeded / 全部被拦 → TaskPermanentError 且产物不残留 / 参数非法快速失败
- materials：作业未批改完 → 前置校验人话失败（T3-1）/ 批完 → mock LLM 生成
  lecture_review 产物 / 教案复用 adapt_lesson / 课件要求已确认教案 / 未知类型快速失败
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.domains.teacher import assessment
from app.models.class_ import Class
from app.models.coursework import Assignment, Submission, SubmissionItem
from app.models.task import Task
from app.models.teacher import TeachingArtifact
from app.models.user import User
from app.services import task_handlers_teacher, task_runner  # noqa: F401 （import 即注册）


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _ensure_user(user_id: uuid.UUID, phone: str) -> None:
    """插入真实 users 行满足外键（幂等）。"""
    async with _test_session_factory() as db:
        exists = await db.get(User, user_id)
        if exists is None:
            db.add(User(id=user_id, phone=phone))
            await db.commit()


async def _ensure_teacher_with_class() -> tuple[uuid.UUID, uuid.UUID]:
    """教师（班级 owner）+ 班级：满足 assert_teacher_in_class 的 owner 通道。"""
    teacher_id = uuid.uuid4()
    await _ensure_user(teacher_id, f"139{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        clazz = Class(
            name=f"测试班{uuid.uuid4().hex[:6]}",
            invite_code=uuid.uuid4().hex[:8],
            owner_id=teacher_id,
        )
        db.add(clazz)
        await db.commit()
        return teacher_id, clazz.id


async def _wait_terminal(task_id: uuid.UUID, timeout_s: float = 8.0) -> Task:
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
            db, user_id=user_id, role="teacher", kind=kind,
            payload=payload, idempotency_key=f"it-teacher-{uuid.uuid4()}",
        )
        await db.commit()
        task_runner.spawn(task)
        return task


def _fake_bank_row(kp: str) -> SimpleNamespace:
    """题库行替身：generate_quiz/_item_from_row 只访问这些属性。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        hash=uuid.uuid4().hex,
        stem=f"已知函数 f(x)=x^2-{uuid.uuid4().int % 9 + 1}，求最小值",
        q_type="choice",
        options={"A": "0", "B": "1", "C": "2", "D": "3"},
        answer="A",
        analysis="配方后由二次函数图象可得最小值。",
        difficulty="medium",
        kp_codes=[kp],
        source="unit_test",
        annotate_meta=None,
    )


def _install_supply(monkeypatch, per_call: int) -> list[dict]:
    """替 assessment.supply_questions：每次调用返回 per_call 行（不触真实题库）。"""
    calls: list[dict] = []

    async def fake_supply(db, **kwargs):
        calls.append(kwargs)
        return [_fake_bank_row("TST-MATH-KP") for _ in range(min(per_call, int(kwargs.get("count") or 0)))]

    monkeypatch.setattr(assessment, "supply_questions", fake_supply)
    return calls


class _FakeModelRouter:
    """替 get_model_router_for_user：回放固定讲评建议文本（不触网）。"""

    def __init__(self, content: str = "1. 聚焦第 1 题的符号讨论，先让学生板演分类过程。"):
        self.content = content
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self.content, "provider": "spark", "model": "fake",
                "input_tokens": 10, "output_tokens": 10, "latency_ms": 1, "tool_calls": None}


@pytest.fixture
def fake_review_llm(monkeypatch):
    router = _FakeModelRouter()

    async def fake_router_for_user(user_id: str, db):
        return router

    monkeypatch.setattr(task_handlers_teacher, "get_model_router_for_user", fake_router_for_user)
    return router


# ========== T-B2 teacher.quiz.create ==========


async def test_quiz_create_success_persists_quiz_set(monkeypatch):
    """题库足量命中：quiz_set 产物落库 + result 完整 + _artifact_id 写回。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    _install_supply(monkeypatch, per_call=3)

    task = await _create_task(
        teacher_id, "teacher.quiz.create",
        {"kp_codes": ["TST-MATH-KP"], "count": 3, "difficulty": "medium",
         "class_id": str(class_id), "title": "周测 A 卷"},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert final.result["artifact_type"] == "teacher_quiz"
    assert final.result["qualified_count"] == 3
    assert final.result["pending_count"] == 0
    assert final.result["jump"] == "/teacher/resources"
    artifact_id = final.result["artifact_id"]
    assert final.payload["_artifact_id"] == artifact_id

    async with _test_session_factory() as db:
        a = await db.get(TeachingArtifact, uuid.UUID(artifact_id))
        assert a is not None
        assert a.artifact_type == "quiz_set" and a.status == "draft"
        assert str(a.owner_id) == str(teacher_id) and str(a.class_id) == str(class_id)
        assert len((a.payload or {}).get("items") or []) == 3
        # 全量达标：不额外标注 qualified/pending
        assert "qualified_count" not in (a.payload or {})
        # 指定标题写入产物
        assert (a.payload or {}).get("title") == "周测 A 卷"


async def test_quiz_create_partial_success_marks_qualified_and_pending(monkeypatch):
    """题库部分命中：任务仍 succeeded，产物与 result 标注达标/待重生成题数。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    _install_supply(monkeypatch, per_call=1)

    task = await _create_task(
        teacher_id, "teacher.quiz.create",
        {"kp_codes": ["TST-MATH-KP"], "count": 3, "class_id": str(class_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert final.result["qualified_count"] == 2
    assert final.result["pending_count"] == 1

    async with _test_session_factory() as db:
        a = await db.get(TeachingArtifact, uuid.UUID(final.result["artifact_id"]))
        assert a is not None
        assert len((a.payload or {}).get("items") or []) == 2
        assert (a.payload or {}).get("qualified_count") == 2
        assert (a.payload or {}).get("pending_count") == 1
        # 既有结构同样保留不足标注
        assert (a.validation or {}).get("available_count") == 2
        assert (a.payload or {}).get("insufficient") is True
        assert a.warnings


async def test_quiz_create_all_blocked_fails_without_artifact(monkeypatch):
    """全部题被拦（0 命中）：TaskPermanentError 人话失败，且不残留产物行。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    _install_supply(monkeypatch, per_call=0)

    task = await _create_task(
        teacher_id, "teacher.quiz.create",
        {"kp_codes": ["TST-MATH-KP"], "count": 3, "class_id": str(class_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "0/3" in (final.error or "") and "组卷失败" in (final.error or "")

    async with _test_session_factory() as db:
        rows = (
            await db.execute(
                select(TeachingArtifact).where(TeachingArtifact.owner_id == teacher_id)
            )
        ).scalars().all()
        assert rows == [], "全拦失败不应残留空产物（随事务回滚）"


async def test_quiz_create_param_validation_fails_fast():
    """缺知识点/缺班级/题量越界 → 快速人话失败（不触题库不触 LLM）。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    cases = [
        ({"count": 5, "class_id": str(class_id)}, "知识点"),
        ({"kp_codes": ["TST-MATH-KP"], "count": 5}, "班级"),
        ({"kp_codes": ["TST-MATH-KP"], "count": 25, "class_id": str(class_id)}, "3~20"),
    ]
    for payload, expect in cases:
        task = await _create_task(teacher_id, "teacher.quiz.create", payload)
        final = await _wait_terminal(task.id)
        assert final.status == "failed", (payload, final.error)
        assert expect in (final.error or ""), (payload, final.error)


# ========== T-B3 teacher.materials ==========


async def _ensure_graded_assignment(
    teacher_id: uuid.UUID, class_id: uuid.UUID, *, graded: bool
) -> uuid.UUID:
    """造一份作业 + 一个学生提交（graded=False 时留 1 份待批改作答）。"""
    student_id = uuid.uuid4()
    await _ensure_user(student_id, f"137{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        assignment = Assignment(
            class_id=class_id, creator_id=teacher_id, title="周测一",
            type="quiz", status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=student_id, assignment_id=assignment.id,
            client_submit_id=uuid.uuid4().hex,
        )
        db.add(submission)
        await db.flush()
        verdicts = ["correct", "wrong"] if graded else ["pending_review", "correct"]
        for item_no, verdict in enumerate(verdicts, start=1):
            db.add(
                SubmissionItem(
                    submission_id=submission.id, item_no=item_no, q_type="choice",
                    verdict=verdict, needs_review=False,
                )
            )
        await db.commit()
        return assignment.id


async def test_materials_class_review_blocked_by_ungraded_assignment():
    """T3-1：作业未批改完 → 前置校验人话失败，不产生产物。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    assignment_id = await _ensure_graded_assignment(teacher_id, class_id, graded=False)

    task = await _create_task(
        teacher_id, "teacher.materials",
        {"material_type": "class_review", "class_id": str(class_id),
         "assignment_id": str(assignment_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert (final.error or "") == "该作业还有 1 份未批改完成，请先完成批改再生成讲评材料"

    async with _test_session_factory() as db:
        rows = (
            await db.execute(
                select(TeachingArtifact).where(TeachingArtifact.owner_id == teacher_id)
            )
        ).scalars().all()
        assert rows == []


async def test_materials_class_review_generates_lecture_review(fake_review_llm):
    """作业已批完：复用洞察/错题聚类 + mock LLM → lecture_review 产物落库。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    assignment_id = await _ensure_graded_assignment(teacher_id, class_id, graded=True)

    task = await _create_task(
        teacher_id, "teacher.materials",
        {"material_type": "class_review", "class_id": str(class_id),
         "assignment_id": str(assignment_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert final.result["artifact_type"] == "teacher_materials"
    assert final.result["material_type"] == "class_review"
    assert final.result["assignment_id"] == str(assignment_id)
    assert final.result["jump"] == "/teacher/resources"

    # LLM 调用口径：确定性数据入 prompt，场景与请求 id 可审计
    assert fake_review_llm.calls, "讲评建议必须经模型路由生成"
    call = fake_review_llm.calls[0]
    assert call["scene"] == "teacher_materials"
    assert "周测一" in call["messages"][1]["content"]

    async with _test_session_factory() as db:
        a = await db.get(TeachingArtifact, uuid.UUID(final.result["artifact_id"]))
        assert a is not None
        assert a.artifact_type == "lecture_review" and a.status == "draft"
        assert str(a.class_id) == str(class_id) and a.engine == "model_router"
        assert (a.payload or {}).get("suggestions") == fake_review_llm.content
        assert (a.payload or {}).get("assignment_id") == str(assignment_id)


async def test_materials_lesson_plan_reuses_adapt_lesson(monkeypatch):
    """lesson_plan：复用 teacher.lesson.adapt 底层函数（monkeypatch 验证入参）。"""
    from app.domains.teacher import lessons as teacher_lessons
    from app.domains.teacher.artifacts import _serialize_artifact, create_artifact

    teacher_id, class_id = await _ensure_teacher_with_class()
    calls: list[dict] = []

    async def fake_adapt(db, t_id, c_id, **kwargs):
        calls.append({"teacher_id": t_id, "class_id": c_id, **kwargs})
        a = await create_artifact(
            db, owner_id=t_id, artifact_type="lesson_plan", scene="teacher.prep",
            class_id=c_id, payload={"topic": kwargs.get("topic")}, engine="local",
        )
        db.add(a)
        await db.flush()
        return _serialize_artifact(a)

    monkeypatch.setattr(teacher_lessons, "adapt_lesson", fake_adapt)

    task = await _create_task(
        teacher_id, "teacher.materials",
        {"material_type": "lesson_plan", "class_id": str(class_id),
         "topic": "一元二次不等式"},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded", final.error
    assert final.result["material_type"] == "lesson_plan"
    assert final.result["jump"] == "/teacher/resources"
    assert len(calls) == 1
    assert calls[0]["topic"] == "一元二次不等式"
    assert str(calls[0]["class_id"]) == str(class_id)

    async with _test_session_factory() as db:
        a = await db.get(TeachingArtifact, uuid.UUID(final.result["artifact_id"]))
        assert a is not None and a.artifact_type == "lesson_plan"


async def test_materials_slides_requires_confirmed_lesson(monkeypatch):
    """slides：无已确认教案 → 人话失败；有 → 复用 create_slides 底层函数。"""
    from app.domains.teacher import lessons as teacher_lessons
    from app.domains.teacher.artifacts import _serialize_artifact, create_artifact

    teacher_id, class_id = await _ensure_teacher_with_class()
    payload = {"material_type": "slides", "class_id": str(class_id)}

    task = await _create_task(teacher_id, "teacher.materials", payload)
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "已确认的教案" in (final.error or "")

    async with _test_session_factory() as db:
        lesson = TeachingArtifact(
            logical_id=uuid.uuid4(), artifact_type="lesson_plan",
            owner_id=teacher_id, class_id=class_id, scene="teacher.prep",
            version=1, status="confirmed", payload={"topic": "函数单调性"},
        )
        db.add(lesson)
        await db.commit()
        lesson_id = lesson.id

    calls: list[dict] = []

    async def fake_slides(db, t_id, lid, *, version, style, requirements):
        calls.append({"lesson_id": lid, "version": version})
        a = await create_artifact(
            db, owner_id=t_id, artifact_type="slide_deck", scene="teacher.prep",
            class_id=class_id, payload={"slides": [{"page": 1, "title": "课堂"}]},
            engine="local", parent_artifact_id=lid,
        )
        db.add(a)
        await db.flush()
        return _serialize_artifact(a)

    monkeypatch.setattr(teacher_lessons, "create_slides", fake_slides)

    task2 = await _create_task(teacher_id, "teacher.materials", payload)
    final2 = await _wait_terminal(task2.id)
    assert final2.status == "succeeded", final2.error
    assert final2.result["material_type"] == "slides"
    assert calls == [{"lesson_id": lesson_id, "version": 1}]

    async with _test_session_factory() as db:
        a = await db.get(TeachingArtifact, uuid.UUID(final2.result["artifact_id"]))
        assert a is not None and a.artifact_type == "slide_deck"


async def test_materials_unknown_type_fails_fast():
    """未知 material_type → 快速人话失败。"""
    teacher_id, class_id = await _ensure_teacher_with_class()
    task = await _create_task(
        teacher_id, "teacher.materials",
        {"material_type": "poster", "class_id": str(class_id)},
    )
    final = await _wait_terminal(task.id)
    assert final.status == "failed"
    assert "未知的教学材料类型" in (final.error or "")
