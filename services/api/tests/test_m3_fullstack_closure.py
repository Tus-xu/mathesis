"""M3 教师端全栈闭环回归测试。"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models import Base
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    Quiz,
    QuizItem,
    Submission,
)
from app.models.database import async_session_factory
from app.models.file import File, FileAsset
from tests._m3_helpers import add_member, make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_fullstack_schema_contains_daily_user_and_classroom_mode():
    assert "user_id" in Base.metadata.tables["daily_questions"].columns
    assert "classroom_modes" in Base.metadata.tables


@pytest.mark.asyncio
async def test_classroom_mode_survives_process_cache_reset(client, monkeypatch):
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id)
        await add_member(db, class_id, student_id, confirmed=True)
        await db.commit()

    response = await client.post(
        f"/api/teacher/classes/{class_id}/classroom-mode",
        json={
            "enabled": True,
            "lesson_id": None,
            "duration_minutes": 30,
            "client_request_id": f"persist-{class_id}",
            "idempotency_key": f"persist-{class_id}",
        },
        headers=_auth(token(teacher_id, "teacher")),
    )
    assert response.json()["code"] == 0

    import app.domains.teacher.classroom as classroom

    monkeypatch.setattr(classroom, "_CLASSROOM_STATE", {}, raising=False)
    persisted = await client.get(
        f"/api/classes/{class_id}/classroom-mode",
        headers=_auth(token(student_id, "student")),
    )
    assert persisted.json()["code"] == 0
    assert persisted.json()["data"]["enabled"] is True


@pytest.mark.asyncio
async def test_published_assignment_student_submit_teacher_grade_student_result(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="闭环教师")
        student_id = await make_user(db, nickname="闭环学生")
        class_id = await make_class(db, teacher_id, name="闭环班")
        await add_member(db, class_id, student_id, confirmed=True)
        quiz = Quiz(
            user_id=teacher_id,
            source="assignment",
            title="函数作业",
            kp_codes=["MATH-002"],
            status="active",
        )
        db.add(quiz)
        await db.flush()
        db.add(
            QuizItem(
                quiz_id=quiz.id,
                item_no=1,
                q_type="solution",
                question_text="求函数 f(x)=x² 的导数。",
                answer="2x",
                answer_analysis="使用幂函数求导公式。",
                kp_code="MATH-002",
                difficulty="easy",
                ai_generated=False,
            )
        )
        assignment = Assignment(
            class_id=class_id,
            creator_id=teacher_id,
            title="函数作业",
            type="quiz",
            quiz_id=quiz.id,
            status="published",
        )
        db.add(assignment)
        await db.flush()
        db.add(
            AssignmentTarget(
                assignment_id=assignment.id,
                target_type="class",
                target_id=class_id,
            )
        )
        student_photo = File(
            user_id=student_id,
            filename="solution.png",
            mime="image/png",
            size_bytes=128,
            sha256="1" * 64,
            storage_uri="tests/solution.png",
            file_type="image",
            status="parsed",
        )
        foreign_photo = File(
            user_id=teacher_id,
            filename="teacher.png",
            mime="image/png",
            size_bytes=128,
            sha256="2" * 64,
            storage_uri="tests/teacher.png",
            file_type="image",
            status="parsed",
        )
        db.add_all([student_photo, foreign_photo])
        await db.flush()
        db.add(
            FileAsset(
                file_id=student_photo.id,
                asset_type="text",
                page_no=1,
                content="f'(x)=2x",
                meta={"engine": "test_ocr"},
            )
        )
        await db.commit()
        assignment_id = assignment.id
        quiz_id = quiz.id
        student_photo_id = student_photo.id
        foreign_photo_id = foreign_photo.id

    student_auth = _auth(token(student_id, "student"))
    teacher_auth = _auth(token(teacher_id, "teacher"))
    visible = await client.get("/api/student/assignments?status=todo", headers=student_auth)
    assert visible.json()["code"] == 0
    assert any(x["assignment_id"] == str(assignment_id) for x in visible.json()["data"]["items"])

    detail = await client.get(f"/api/student/assignments/{assignment_id}", headers=student_auth)
    assert detail.json()["code"] == 0
    assert detail.json()["data"]["quiz_id"] == str(quiz_id)
    assert detail.json()["data"]["items"][0]["question_text"] == "求函数 f(x)=x² 的导数。"
    assert "answer" not in detail.json()["data"]["items"][0]

    forged = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"forged-{assignment_id}",
            "items": [
                {"item_no": 1, "q_type": "solution", "file_id": str(foreign_photo_id)}
            ],
        },
        headers=student_auth,
    )
    assert forged.json()["code"] == 40400

    submitted = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"closure-{assignment_id}",
            "items": [
                {"item_no": 1, "q_type": "solution", "file_id": str(student_photo_id)}
            ],
        },
        headers=student_auth,
    )
    assert submitted.json()["code"] == 0, submitted.text
    assert submitted.json()["data"]["results"][0]["verdict"] == "pending_review"
    assert submitted.json()["data"]["results"][0]["score"] is None
    submission_id = submitted.json()["data"]["submission_id"]
    async with async_session_factory() as db:
        stored = await db.get(Submission, submission_id)
        assert stored is not None
        assert stored.user_id == student_id
        assert stored.quiz_id == quiz_id

    queue = await client.get(
        f"/api/teacher/grading/queue?class_id={class_id}", headers=teacher_auth
    )
    queue_items = queue.json()["data"]["queue"]
    assert len(queue_items) == 1
    item_id = queue_items[0]["submission_item_id"]
    grading_detail = await client.get(
        f"/api/teacher/grading/{item_id}?class_id={class_id}", headers=teacher_auth
    )
    assert grading_detail.json()["data"]["file_id"] == str(student_photo_id)
    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": f"suggest-{item_id}"},
        headers=teacher_auth,
    )
    suggestion_id = suggestion.json()["data"]["suggestion_id"]
    confirmed = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion_id,
            "decision": "override",
            "final_score": 8.5,
            "teacher_feedback": "步骤正确，书写可更完整。",
            "version": 1,
        },
        headers={**teacher_auth, "Idempotency-Key": f"confirm-{item_id}"},
    )
    assert confirmed.json()["code"] == 0, confirmed.text

    result = await client.get(
        f"/api/student/assignments/{assignment_id}/result", headers=student_auth
    )
    assert result.json()["code"] == 0
    assert result.json()["data"]["submission_id"] == submission_id
    assert result.json()["data"]["status"] == "graded"
    assert result.json()["data"]["total_score"] == 8.5
    assert result.json()["data"]["items"][0]["teacher_feedback"] == "步骤正确，书写可更完整。"
