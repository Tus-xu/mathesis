"""Grading V2 workspace contract tests.

The first tests protect persisted question-scoring facts. A V2 rubric may be
missing, but neither the API nor the client may manufacture it from a generic
score form.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.coursework import Assignment, Quiz, QuizItem, Submission, SubmissionItem
from app.models.database import async_session_factory
from app.models.teacher import TeacherAction
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def test_quiz_item_declares_nullable_score_point_contract():
    mapper_fields = QuizItem.__mapper__.attrs

    assert "max_score" in mapper_fields
    assert "grading_rubric" in mapper_fields
    assert QuizItem.__table__.c.max_score.nullable is True
    assert QuizItem.__table__.c.grading_rubric.nullable is True


def test_grading_v2_migration_keeps_single_linear_head():
    """Grading V2 workspace appends to the current single-head chain (om2 -> m3_003)."""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    # 门禁同步（沿用迭代20 先例）：head 随迁移链前进（trial_001_feedback ->
    # stu_002_learning_ledger_tables -> tel_001_two_end_link，tel_001 的 down_revision
    # 即 stu_002），仍为单一线性 head，契约不变；追加迁移时需同步本常量
    assert script.get_heads() == ["tel_001_two_end_link"]
    workspace = script.get_revision("m3_003_grading_v2_workspace")
    assert workspace is not None
    assert workspace.down_revision == "om2_openmaic_document"


def test_grading_rubric_preserves_high_school_math_score_points():
    rubric = [
        {
            "id": "derivative",
            "criterion": "正确求导",
            "points": 3,
            "evidence_hint": "写出 f'(x)=3x²-3",
        },
        {
            "id": "critical",
            "criterion": "确定分界点",
            "points": 3,
            "evidence_hint": "x=-1,1",
        },
        {
            "id": "interval",
            "criterion": "写出单调区间",
            "points": 4,
            "evidence_hint": "给出增减区间",
        },
    ]

    assert sum(item["points"] for item in rubric) == 10
    assert rubric[2]["criterion"] == "写出单调区间"


@dataclass
class WorkspaceSeed:
    teacher_id: uuid.UUID
    class_id: uuid.UUID
    assignment_id: uuid.UUID
    first_item_id: uuid.UUID
    second_item_id: uuid.UUID
    third_item_id: uuid.UUID


async def _seed_three_derivative_submissions() -> WorkspaceSeed:
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        class_id = await make_class(db, teacher_id)
        quiz = Quiz(
            user_id=teacher_id,
            source="assignment",
            title="函数的单调性",
            kp_codes=["MATH-FUNCTION-MONOTONICITY"],
        )
        db.add(quiz)
        await db.flush()
        db.add(
            QuizItem(
                quiz_id=quiz.id,
                item_no=1,
                q_type="solution",
                question_text="已知 f(x)=x^3-3x，讨论函数的单调性。",
                options=None,
                answer="f'(x)=3x²-3；在 (-∞,-1)∪(1,+∞) 单调递增，在 (-1,1) 单调递减。",
                answer_analysis="先求导并比较导数在临界点两侧的符号。",
                kp_code="MATH-FUNCTION-MONOTONICITY",
                max_score=10,
                grading_rubric=[
                    {
                        "id": "derivative",
                        "criterion": "正确求导",
                        "points": 3,
                        "evidence_hint": "写出 f'(x)=3x²-3",
                    },
                    {
                        "id": "critical",
                        "criterion": "确定分界点",
                        "points": 3,
                        "evidence_hint": "x=-1,1",
                    },
                    {
                        "id": "interval",
                        "criterion": "写出单调区间",
                        "points": 4,
                        "evidence_hint": "给出增减区间",
                    },
                ],
            )
        )
        assignment = Assignment(
            class_id=class_id,
            creator_id=teacher_id,
            title="函数的单调性",
            type="quiz",
            quiz_id=quiz.id,
            status="published",
        )
        db.add(assignment)
        await db.flush()
        answers = [
            "f'(x)=3x²-3，令 f'(x)=0 得 x=±1。",
            "f'(x)=3x²-3，x=-1,1；据此判断区间。",
            "导数符号表如下，增区间与减区间已列出。",
        ]
        item_ids: list[uuid.UUID] = []
        submitted_at = datetime.now(UTC)
        for index, answer in enumerate(answers, start=1):
            submission = Submission(
                user_id=teacher_id,
                quiz_id=quiz.id,
                assignment_id=assignment.id,
                client_submit_id=f"monotonicity-{index}",
                status="pending_review",
                created_at=submitted_at + timedelta(seconds=index),
            )
            db.add(submission)
            await db.flush()
            item = SubmissionItem(
                submission_id=submission.id,
                item_no=1,
                q_type="solution",
                verdict="pending_review",
                answer_text=answer,
            )
            db.add(item)
            await db.flush()
            item_ids.append(item.id)
        await db.commit()
        return WorkspaceSeed(
            teacher_id=teacher_id,
            class_id=class_id,
            assignment_id=assignment.id,
            first_item_id=item_ids[0],
            second_item_id=item_ids[1],
            third_item_id=item_ids[2],
        )


async def _load_submission_item(item_id: uuid.UUID) -> SubmissionItem:
    async with async_session_factory() as db:
        item = await db.get(SubmissionItem, item_id)
        assert item is not None
        return item


@pytest.mark.asyncio
async def test_workspace_is_question_focused_and_next_is_server_derived(client):
    seeded = await _seed_three_derivative_submissions()

    response = await client.get(
        "/api/teacher/grading/workspace",
        params={
            "class_id": str(seeded.class_id),
            "assignment_id": str(seeded.assignment_id),
            "item_no": 1,
            "submission_item_id": str(seeded.second_item_id),
        },
        headers=_auth(token(seeded.teacher_id, "teacher")),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["context"]["question"]["item_no"] == 1
    assert data["context"]["question"]["max_score"] == 10.0
    assert data["queue"][0]["anonymous_label"] == "第 1 份作答"
    assert data["selected"]["submission_item_id"] == str(seeded.second_item_id)
    assert data["selected"]["scoring"]["rubric_status"] == "ready"
    assert len(data["selected"]["scoring"]["rubric_items"]) == 3
    assert data["navigation"]["next_ungraded_id"] == str(seeded.third_item_id)
    assert "confidence" not in data["selected"]["suggestion"]


@pytest.mark.asyncio
async def test_workspace_defaults_to_nearest_assignment_with_answers_not_newest_empty(client):
    """无 assignment_id 时：批改台默认进入「最近一份有作答」的作业，
    而不是最新（可能无提交）的作业，避免空队列与今日待批数量矛盾。"""
    seeded = await _seed_three_derivative_submissions()
    async with async_session_factory() as db:
        newer_empty = Assignment(
            class_id=seeded.class_id,
            creator_id=seeded.teacher_id,
            title="最新但无人作答",
            type="quiz",
            quiz_id=None,
            status="published",
        )
        db.add(newer_empty)
        await db.commit()

    response = await client.get(
        "/api/teacher/grading/workspace",
        params={"class_id": str(seeded.class_id)},
        headers=_auth(token(seeded.teacher_id, "teacher")),
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["context"]["assignment"]["assignment_id"] == str(seeded.assignment_id)
    assert data["context"]["assignment"]["title"] == "函数的单调性"
    assert len(data["queue"]) == 3
    assert data["selected"] is not None


@pytest.mark.asyncio
async def test_manual_review_is_idempotent_and_does_not_write_score_or_mastery(client):
    seeded = await _seed_three_derivative_submissions()
    body = {
        "state": "pending",
        "note": "核对 a=0 的分类讨论边界",
        "client_request_id": "review-derivative-1",
    }
    headers = {
        **_auth(token(seeded.teacher_id, "teacher")),
        "Idempotency-Key": "review-idempotency-1",
    }

    first = await client.post(
        f"/api/teacher/grading/{seeded.first_item_id}/review",
        json=body,
        headers=headers,
    )
    replay = await client.post(
        f"/api/teacher/grading/{seeded.first_item_id}/review",
        json=body,
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert first.json()["data"]["state"] == "pending"
    assert replay.json()["data"]["replayed"] is True
    item = await _load_submission_item(seeded.first_item_id)
    assert item.score is None
    assert item.teacher_final_score is None

    async with async_session_factory() as db:
        actions = (
            (
                await db.execute(
                    TeacherAction.__table__.select().where(
                        TeacherAction.teacher_id == seeded.teacher_id,
                        TeacherAction.action_type == "grading.review.set",
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(actions) == 1
    assert actions[0]["details"]["submission_item_id"] == str(seeded.first_item_id)
    assert "note" not in actions[0]["details"]
    assert isinstance(actions[0]["details"]["review_note_digest"], str)


@pytest.mark.asyncio
async def test_confirm_override_rejects_score_above_persisted_question_maximum(client):
    """A teacher decision remains explicit, but cannot exceed the reviewed full mark."""
    seeded = await _seed_three_derivative_submissions()
    headers = _auth(token(seeded.teacher_id, "teacher"))
    suggested = await client.post(
        f"/api/teacher/grading/{seeded.first_item_id}/suggest",
        json={"class_id": str(seeded.class_id), "client_request_id": "derivative-bound-suggest"},
        headers=headers,
    )
    assert suggested.status_code == 200, suggested.text
    suggestion = suggested.json()["data"]

    rejected = await client.post(
        f"/api/teacher/grading/{seeded.first_item_id}/confirm",
        json={
            "suggestion_id": suggestion["suggestion_id"],
            "decision": "override",
            "final_score": 10.5,
            "teacher_feedback": "步骤完整，但测试越界保护。",
            "version": suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "derivative-bound-confirm"},
    )
    assert rejected.status_code == 422, rejected.text
    item = await _load_submission_item(seeded.first_item_id)
    assert item.score is None
    assert item.teacher_final_score is None
