"""M3 教师端：预批改与正式计分（§13）。"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.coursework import (
    Assignment,
    MasteryRecord,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import async_session_factory
from app.models.file import File
from app.models.knowledge_point import KnowledgePoint
from app.models.teacher import TeacherAction
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _seed_item():
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        a = Assignment(class_id=cid, creator_id=tid, title="作业", type="quiz", status="published")
        db.add(a)
        await db.flush()
        s = Submission(user_id=tid, assignment_id=a.id, client_submit_id="s1", status="pending_review")
        db.add(s)
        await db.flush()
        it = SubmissionItem(submission_id=s.id, item_no=1, q_type="text", verdict="pending_review",
                            answer_text="作答")
        db.add(it)
        await db.commit()
        return tid, cid, it.id


async def _seed_objective_item(
    *,
    answer_text: str,
    standard_answer: str,
    include_quiz_item: bool = True,
    duplicate_quiz_item: bool = False,
    submission_q_type: str = "choice",
    quiz_q_type: str = "choice",
    kp_code: str = "MATH-003",
    max_score: float | None = None,
):
    """Persisted quiz context is the only evidence for objective scoring."""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        quiz = Quiz(user_id=tid, source="assignment", title="函数测验", kp_codes=["MATH-003"])
        db.add(quiz)
        await db.flush()
        if include_quiz_item:
            db.add(
                QuizItem(
                    quiz_id=quiz.id,
                    item_no=1,
                    q_type=quiz_q_type,
                    question_text="函数 f(x)=x^2 的最小值是？",
                    options={"A": "-1", "B": "0", "C": "1"},
                    answer=standard_answer,
                    answer_analysis="因为 x^2 ≥ 0，所以最小值为 0。",
                    kp_code=kp_code,
                    max_score=max_score,
                )
            )
        if duplicate_quiz_item:
            db.add(
                QuizItem(
                    quiz_id=quiz.id,
                    item_no=1,
                    q_type=quiz_q_type,
                    question_text="重复的二次函数题目",
                    options={"A": "-1", "B": "0", "C": "1"},
                    answer="A",
                    answer_analysis="异常重复记录，不可作为评分证据。",
                    kp_code=kp_code,
                )
            )
        assignment = Assignment(
            class_id=cid,
            creator_id=tid,
            title="二次函数随堂作业",
            type="quiz",
            quiz_id=quiz.id,
            status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=tid,
            quiz_id=quiz.id,
            assignment_id=assignment.id,
            client_submit_id=f"objective-{answer_text}-{standard_answer}",
            status="pending_review",
        )
        db.add(submission)
        await db.flush()
        item = SubmissionItem(
            submission_id=submission.id,
            item_no=1,
            q_type=submission_q_type,
            verdict="pending_review",
            answer_text=answer_text,
        )
        db.add(item)
        await db.commit()
        return tid, cid, item.id


async def _seed_two_objective_items():
    """Two separately gradeable answers owned by the same teacher and class."""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        quiz = Quiz(user_id=tid, source="assignment", title="双题测验", kp_codes=[])
        db.add(quiz)
        await db.flush()
        db.add_all(
            [
                QuizItem(
                    quiz_id=quiz.id,
                    item_no=item_no,
                    q_type="choice",
                    question_text=f"第 {item_no} 题",
                    options={"A": "错", "B": "对"},
                    answer="B",
                )
                for item_no in (1, 2)
            ]
        )
        assignment = Assignment(
            class_id=cid,
            creator_id=tid,
            title="双题作业",
            type="quiz",
            quiz_id=quiz.id,
            status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=tid,
            quiz_id=quiz.id,
            assignment_id=assignment.id,
            client_submit_id="two-objective-items",
            status="pending_review",
        )
        db.add(submission)
        await db.flush()
        items = [
            SubmissionItem(
                submission_id=submission.id,
                item_no=item_no,
                q_type="choice",
                verdict="pending_review",
                answer_text="B",
            )
            for item_no in (1, 2)
        ]
        db.add_all(items)
        await db.commit()
        return tid, cid, tuple(item.id for item in items)


@pytest.mark.asyncio
async def test_objective_suggestion_uses_persisted_standard_answer_and_exposes_context(client):
    teacher_id, class_id, wrong_item_id = await _seed_objective_item(
        answer_text="A", standard_answer="B"
    )
    correct_teacher_id, correct_class_id, correct_item_id = await _seed_objective_item(
        answer_text=" b ", standard_answer="B"
    )
    missing_teacher_id, missing_class_id, missing_answer_item_id = await _seed_objective_item(
        answer_text="B", standard_answer=""
    )
    no_context_teacher_id, no_context_class_id, no_context_item_id = await _seed_objective_item(
        answer_text="B", standard_answer="B", include_quiz_item=False
    )
    headers = _auth(token(teacher_id, "teacher"))

    wrong = await client.post(
        f"/api/teacher/grading/{wrong_item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": "wrong-choice"},
        headers=headers,
    )
    assert wrong.json()["data"]["suggestion_score"] == 0.0
    assert wrong.json()["data"]["confidence"] >= 0.9
    assert wrong.json()["data"]["review_needed"] is False

    correct = await client.post(
        f"/api/teacher/grading/{correct_item_id}/suggest",
        json={"class_id": str(correct_class_id), "client_request_id": "correct-choice"},
        headers=_auth(token(correct_teacher_id, "teacher")),
    )
    assert correct.json()["data"]["suggestion_score"] == 1.0
    assert correct.json()["data"]["confidence"] >= 0.9
    assert correct.json()["data"]["review_needed"] is False

    missing = await client.post(
        f"/api/teacher/grading/{missing_answer_item_id}/suggest",
        json={"class_id": str(missing_class_id), "client_request_id": "missing-standard"},
        headers=_auth(token(missing_teacher_id, "teacher")),
    )
    assert missing.json()["data"]["review_needed"] is True
    assert "缺少标准答案" in missing.json()["data"]["evidence"]

    no_context = await client.post(
        f"/api/teacher/grading/{no_context_item_id}/suggest",
        json={"class_id": str(no_context_class_id), "client_request_id": "missing-context"},
        headers=_auth(token(no_context_teacher_id, "teacher")),
    )
    assert no_context.json()["data"]["review_needed"] is True
    assert "缺少已持久化题目上下文" in no_context.json()["data"]["evidence"]

    detail = await client.get(
        f"/api/teacher/grading/{wrong_item_id}?class_id={class_id}", headers=headers
    )
    data = detail.json()["data"]
    assert data["assignment_title"] == "二次函数随堂作业"
    assert data["question_text"] == "函数 f(x)=x^2 的最小值是？"
    assert data["question_type"] == "choice"
    assert data["options"] == {"A": "-1", "B": "0", "C": "1"}
    assert data["standard_answer"] == "B"
    assert data["answer_analysis"] == "因为 x^2 ≥ 0，所以最小值为 0。"


@pytest.mark.asyncio
async def test_objective_accept_uses_persisted_question_maximum(client):
    """A correct 0.5-point objective item confirms at 0.5, never a hard-coded 1."""
    teacher_id, class_id, item_id = await _seed_objective_item(
        answer_text="B",
        standard_answer="B",
        max_score=0.5,
    )
    headers = _auth(token(teacher_id, "teacher"))
    suggested = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": "half-point-suggest"},
        headers=headers,
    )
    assert suggested.status_code == 200, suggested.text
    suggestion = suggested.json()["data"]
    assert suggestion["suggestion_score"] == 0.5

    confirmed = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion["suggestion_id"],
            "decision": "accept",
            "version": suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "half-point-confirm"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["teacher_final_score"] == 0.5


@pytest.mark.asyncio
async def test_legacy_objective_override_rejects_score_above_one_point_fallback(client):
    """Legacy objective items without max_score retain a safe 1-point full-mark bound."""
    teacher_id, class_id, item_id = await _seed_objective_item(
        answer_text="B",
        standard_answer="B",
        max_score=None,
    )
    headers = _auth(token(teacher_id, "teacher"))
    suggested = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": "legacy-objective-suggest"},
        headers=headers,
    )
    assert suggested.status_code == 200, suggested.text
    suggestion = suggested.json()["data"]
    assert suggestion["suggestion_score"] == 1.0

    rejected = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion["suggestion_id"],
            "decision": "override",
            "final_score": 2.0,
            "version": suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "legacy-objective-over-full"},
    )
    assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
async def test_duplicate_persisted_quiz_items_require_manual_review_without_context(client):
    teacher_id, class_id, item_id = await _seed_objective_item(
        answer_text="B", standard_answer="B", duplicate_quiz_item=True
    )
    headers = _auth(token(teacher_id, "teacher"))

    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": "duplicate-context"},
        headers=headers,
    )
    assert suggestion.status_code == 200, suggestion.text
    suggestion_data = suggestion.json()["data"]
    assert suggestion_data["suggestion_score"] == 0.0
    assert suggestion_data["review_needed"] is True
    assert "重复" in suggestion_data["evidence"]
    assert "已依据已持久化标准答案" not in suggestion_data["evidence"]

    detail = await client.get(
        f"/api/teacher/grading/{item_id}?class_id={class_id}", headers=headers
    )
    assert detail.status_code == 200, detail.text
    detail_data = detail.json()["data"]
    assert detail_data["question_text"] is None
    assert detail_data["standard_answer"] is None


@pytest.mark.asyncio
async def test_suggest_does_not_write_final_score(client):
    tid, cid, item_id = await _seed_item()
    tok = token(tid, "teacher")
    r = await client.post(f"/api/teacher/grading/{item_id}/suggest",
                          json={"class_id": str(cid), "client_request_id": "sg"},
                          headers=_auth(tok))
    assert r.json()["code"] == 0
    data = r.json()["data"]
    assert data["decision"] == "draft"
    assert data["evidence"] == "主观题转人工复核"
    assert "=" not in data["evidence"]
    # 建议不写正式分：teacher_final_score 仍为空
    assert data["teacher_final_score"] is None
    detail = await client.get(f"/api/teacher/grading/{item_id}?class_id={cid}", headers=_auth(tok))
    body = detail.json()["data"]
    assert body["suggestion"]["suggestion_score"] is not None
    assert body["teacher_final_score"] is None


@pytest.mark.asyncio
async def test_detail_auto_suggestion_can_be_confirmed(client):
    tid, cid, item_id = await _seed_item()
    tok = token(tid, "teacher")

    detail = await client.get(
        f"/api/teacher/grading/{item_id}?class_id={cid}", headers=_auth(tok)
    )
    assert detail.json()["code"] == 0, detail.text
    suggestion = detail.json()["data"]["suggestion"]
    assert suggestion["suggestion_id"]

    confirmed = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion["suggestion_id"],
            "decision": "override",
            "final_score": 0.0,
            "teacher_feedback": "已复核",
            "version": suggestion["version"],
        },
        headers={**_auth(tok), "Idempotency-Key": f"auto-confirm:{item_id}"},
    )
    assert confirmed.json()["code"] == 0, confirmed.text
    assert confirmed.json()["data"]["decision"] == "overridden"


@pytest.mark.asyncio
async def test_teacher_can_view_photo_for_scoped_grading_item(client):
    from app.domains.files.router import _local_file_path

    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        assignment = Assignment(class_id=cid, creator_id=tid, title="拍照作业", type="quiz", status="published")
        db.add(assignment)
        await db.flush()
        submission = Submission(user_id=tid, assignment_id=assignment.id, client_submit_id="photo-grade")
        db.add(submission)
        photo = File(
            user_id=tid,
            filename="solution.png",
            file_type="image",
            mime="image/png",
            size_bytes=12,
            sha256="3" * 64,
            storage_uri="local:test-photo-token",
            status="parsed",
        )
        db.add(photo)
        await db.flush()
        item = SubmissionItem(
            submission_id=submission.id,
            item_no=1,
            q_type="solution",
            verdict="pending_review",
            file_id=photo.id,
        )
        db.add(item)
        await db.commit()
        path = _local_file_path(photo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"photo-bytes!")
        item_id = item.id

    response = await client.get(
        f"/api/teacher/grading/{item_id}/file",
        headers=_auth(token(tid, "teacher")),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == b"photo-bytes!"


@pytest.mark.asyncio
async def test_confirm_accept_sets_final_and_idempotent(client):
    tid, cid, item_id = await _seed_objective_item(answer_text="B", standard_answer="B")
    tok = token(tid, "teacher")
    sg = await client.post(f"/api/teacher/grading/{item_id}/suggest",
                           json={"class_id": str(cid), "client_request_id": "sg"},
                           headers=_auth(tok))
    suggestion_id = sg.json()["data"]["suggestion_id"]
    r1 = await client.post(f"/api/teacher/grading/{item_id}/confirm",
                           json={"suggestion_id": suggestion_id, "decision": "accept",
                                 "teacher_feedback": "ok", "version": 1},
                           headers={**_auth(tok), "Idempotency-Key": "gk"},
                           )
    assert r1.json()["code"] == 0, r1.text
    assert r1.json()["data"]["decision"] == "accepted"
    r2 = await client.post(f"/api/teacher/grading/{item_id}/confirm",
                           json={"suggestion_id": suggestion_id, "decision": "accept",
                                 "teacher_feedback": "ok", "version": 1},
                           headers={**_auth(tok), "Idempotency-Key": "gk"},
                           )
    assert r2.json()["data"]["replayed"] is True


@pytest.mark.asyncio
async def test_confirm_override_saves_both(client):
    tid, cid, item_id = await _seed_item()
    tok = token(tid, "teacher")
    sg = await client.post(f"/api/teacher/grading/{item_id}/suggest",
                           json={"class_id": str(cid), "client_request_id": "sg"},
                           headers=_auth(tok))
    suggestion_id = sg.json()["data"]["suggestion_id"]
    r = await client.post(f"/api/teacher/grading/{item_id}/confirm",
                          json={"suggestion_id": suggestion_id, "decision": "override",
                                "final_score": 7.5, "teacher_feedback": "补充分", "version": 1},
                          headers={**_auth(tok), "Idempotency-Key": "gk2"},
                          )
    data = r.json()["data"]
    assert data["teacher_final_score"] == 7.5
    assert data["decision"] == "overridden"
    detail = await client.get(f"/api/teacher/grading/{item_id}?class_id={cid}", headers=_auth(tok))
    assert detail.json()["data"]["suggestion"]["decision"] == "overridden"


@pytest.mark.asyncio
async def test_confirm_requires_teacher_in_class(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, tid)
        a = Assignment(class_id=cid, creator_id=tid, title="作业", type="quiz", status="published")
        db.add(a)
        await db.flush()
        s = Submission(user_id=tid, assignment_id=a.id, client_submit_id="s2")
        db.add(s)
        await db.flush()
        it = SubmissionItem(submission_id=s.id, item_no=1, q_type="text", verdict="pending_review")
        db.add(it)
        await db.commit()
        item_id = it.id
    r = await client.post(f"/api/teacher/grading/{item_id}/suggest",
                          json={"class_id": str(cid), "client_request_id": "sg"},
                          headers=_auth(token(other, "teacher")))
    assert r.status_code == 403
    assert r.json()["code"] == 40302


@pytest.mark.asyncio
@pytest.mark.parametrize("assignment_state", ["missing", "soft_deleted"])
async def test_grading_endpoints_fail_closed_when_assignment_is_unavailable(
    client, assignment_state
):
    """Removing an assignment must revoke every grading entry point, including files."""
    from app.domains.files.router import _local_file_path

    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        assignment = Assignment(
            class_id=cid,
            creator_id=tid,
            title="不可见作业",
            type="quiz",
            status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=tid,
            assignment_id=None if assignment_state == "missing" else assignment.id,
            client_submit_id=f"hidden-{assignment_state}",
        )
        photo = File(
            user_id=tid,
            filename="private.png",
            file_type="image",
            mime="image/png",
            size_bytes=12,
            sha256="4" * 64,
            storage_uri=f"local:hidden-{assignment_state}",
            status="parsed",
        )
        db.add_all([submission, photo])
        await db.flush()
        item = SubmissionItem(
            submission_id=submission.id,
            item_no=1,
            q_type="solution",
            verdict="pending_review",
            answer_text="不能泄露的答案",
            file_id=photo.id,
        )
        db.add(item)
        if assignment_state == "soft_deleted":
            assignment.deleted_at = datetime.now(UTC)
        await db.commit()
        path = _local_file_path(photo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private-photo")
        item_id = item.id

    headers = _auth(token(tid, "teacher"))
    responses = [
        await client.get(f"/api/teacher/grading/{item_id}", headers=headers),
        await client.post(
            f"/api/teacher/grading/{item_id}/suggest",
            json={"class_id": str(cid), "client_request_id": f"hidden-{assignment_state}"},
            headers=headers,
        ),
        await client.get(f"/api/teacher/grading/{item_id}/file", headers=headers),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json()["code"] == 40400
    assert responses[-1].content != b"private-photo"


@pytest.mark.asyncio
async def test_detail_downgrades_old_suggestion_when_context_becomes_ambiguous(client):
    """A stale high-confidence artifact must not survive a duplicate persisted question."""
    tid, cid, item_id = await _seed_objective_item(answer_text="B", standard_answer="B")
    headers = _auth(token(tid, "teacher"))
    first = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(cid), "client_request_id": "trusted-first"},
        headers=headers,
    )
    assert first.json()["data"]["confidence"] >= 0.9

    async with async_session_factory() as db:
        item = await db.get(SubmissionItem, item_id)
        submission = await db.get(Submission, item.submission_id)
        db.add(
            QuizItem(
                quiz_id=submission.quiz_id,
                item_no=item.item_no,
                q_type="choice",
                question_text="后来写入的重复题",
                options={"A": "-1", "B": "0"},
                answer="A",
                kp_code="MATH-003",
            )
        )
        await db.commit()

    detail = await client.get(f"/api/teacher/grading/{item_id}", headers=headers)
    data = detail.json()["data"]
    assert data["suggestion"]["review_needed"] is True
    assert data["suggestion"]["confidence"] == 0.0
    assert "重复" in data["suggestion"]["evidence"]
    assert data["teacher_final_score"] is None


@pytest.mark.asyncio
async def test_q_type_mismatch_never_uses_standard_answer_for_scoring(client):
    """A submission marked choice cannot be auto-scored by a persisted solution item."""
    tid, cid, item_id = await _seed_objective_item(
        answer_text="B",
        standard_answer="B",
        submission_q_type="choice",
        quiz_q_type="solution",
    )
    response = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(cid), "client_request_id": "q-type-mismatch"},
        headers=_auth(token(tid, "teacher")),
    )
    data = response.json()["data"]
    assert data["suggestion_score"] == 0.0
    assert data["confidence"] == 0.0
    assert data["review_needed"] is True
    assert "题型" in data["evidence"]


@pytest.mark.asyncio
async def test_confirmation_updates_mastery_only_for_unique_trusted_context(client):
    """A duplicate question may be explicitly graded but cannot pick an arbitrary KP."""
    unique_kp = f"TSTGU{uuid.uuid4().hex[:20]}"
    duplicate_kp = f"TSTGD{uuid.uuid4().hex[:20]}"
    async with async_session_factory() as db:
        db.add_all(
            [
                KnowledgePoint(code=unique_kp, name="唯一评分知识点"),
                KnowledgePoint(code=duplicate_kp, name="重复评分知识点"),
            ]
        )
        await db.commit()

    trusted_tid, trusted_cid, trusted_item_id = await _seed_objective_item(
        answer_text="B", standard_answer="B", kp_code=unique_kp
    )
    duplicate_tid, duplicate_cid, duplicate_item_id = await _seed_objective_item(
        answer_text="B",
        standard_answer="B",
        duplicate_quiz_item=True,
        kp_code=duplicate_kp,
    )

    async def suggest_and_confirm(tid, cid, item_id, key, *, decision="accept"):
        headers = _auth(token(tid, "teacher"))
        suggestion = await client.post(
            f"/api/teacher/grading/{item_id}/suggest",
            json={"class_id": str(cid), "client_request_id": f"suggest-{key}"},
            headers=headers,
        )
        data = suggestion.json()["data"]
        confirmed = await client.post(
            f"/api/teacher/grading/{item_id}/confirm",
            json={
                "suggestion_id": data["suggestion_id"],
                "decision": decision,
                **({"final_score": 1.0} if decision == "override" else {}),
                "version": data["version"],
            },
            headers={**headers, "Idempotency-Key": f"mastery-{key}"},
        )
        assert confirmed.status_code == 200, confirmed.text

    await suggest_and_confirm(trusted_tid, trusted_cid, trusted_item_id, "trusted")
    await suggest_and_confirm(
        duplicate_tid,
        duplicate_cid,
        duplicate_item_id,
        "duplicate",
        decision="override",
    )

    async with async_session_factory() as db:
        trusted_kp_obj = await db.scalar(
            select(KnowledgePoint).where(KnowledgePoint.code == unique_kp)
        )
        duplicate_kp_obj = await db.scalar(
            select(KnowledgePoint).where(KnowledgePoint.code == duplicate_kp)
        )
        trusted_mastery = await db.get(MasteryRecord, (trusted_tid, trusted_kp_obj.id))
        duplicate_mastery = await db.get(MasteryRecord, (duplicate_tid, duplicate_kp_obj.id))
    assert trusted_mastery is not None
    assert trusted_mastery.practice_count == 1
    assert trusted_mastery.correct_count == 1
    assert duplicate_mastery is None


@pytest.mark.asyncio
@pytest.mark.parametrize("context_break", ["ambiguous", "missing", "q_type_mismatch"])
async def test_confirm_rechecks_context_and_requires_explicit_override_when_untrusted(
    client, context_break
):
    """Untrusted current context invalidates accept, but an authorized teacher may override."""
    tid, cid, item_id = await _seed_objective_item(answer_text="B", standard_answer="B")
    headers = _auth(token(tid, "teacher"))
    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(cid), "client_request_id": "before-context-break"},
        headers=headers,
    )
    suggestion_data = suggestion.json()["data"]
    async with async_session_factory() as db:
        item = await db.get(SubmissionItem, item_id)
        sub = await db.get(Submission, item.submission_id)
        if context_break == "ambiguous":
            db.add(
                QuizItem(
                    quiz_id=sub.quiz_id,
                    item_no=item.item_no,
                    q_type="choice",
                    question_text="新写入的重复题",
                    options={"A": "错", "B": "对"},
                    answer="A",
                )
            )
        else:
            persisted = await db.scalar(
                select(QuizItem).where(
                    QuizItem.quiz_id == sub.quiz_id,
                    QuizItem.item_no == item.item_no,
                )
            )
            if context_break == "missing":
                persisted.deleted_at = datetime.now(UTC)
            else:
                persisted.q_type = "solution"
        await db.commit()

    accept = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion_data["suggestion_id"],
            "decision": "accept",
            "version": suggestion_data["version"],
        },
        headers={**headers, "Idempotency-Key": f"context-break-accept-{context_break}"},
    )
    assert accept.status_code == 422
    assert accept.json()["code"] == 42210

    override = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion_data["suggestion_id"],
            "decision": "override",
            "final_score": 0.25,
            "version": suggestion_data["version"],
        },
        headers={**headers, "Idempotency-Key": f"context-break-override-{context_break}"},
    )
    assert override.status_code == 200, override.text
    assert override.json()["data"]["teacher_final_score"] == 0.25


@pytest.mark.asyncio
async def test_confirm_binds_artifact_and_idempotency_to_exact_grading_request(client):
    """Artifact and idempotency reuse across answers or suggestions must conflict."""
    tid, cid, (first_item_id, second_item_id) = await _seed_two_objective_items()
    headers = _auth(token(tid, "teacher"))

    async def suggest(item_id, key):
        response = await client.post(
            f"/api/teacher/grading/{item_id}/suggest",
            json={"class_id": str(cid), "client_request_id": key},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    first_suggestion = await suggest(first_item_id, "first-suggestion")
    replacement_suggestion = await suggest(first_item_id, "replacement-suggestion")
    second_suggestion = await suggest(second_item_id, "second-suggestion")

    cross_artifact = await client.post(
        f"/api/teacher/grading/{second_item_id}/confirm",
        json={
            "suggestion_id": first_suggestion["suggestion_id"],
            "decision": "accept",
            "version": first_suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "cross-artifact"},
    )
    assert cross_artifact.status_code == 422
    assert cross_artifact.json()["code"] == 40001

    first_confirm = await client.post(
        f"/api/teacher/grading/{first_item_id}/confirm",
        json={
            "suggestion_id": first_suggestion["suggestion_id"],
            "decision": "accept",
            "version": first_suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "shared-confirm-key"},
    )
    assert first_confirm.status_code == 200, first_confirm.text

    replay_with_second_key = await client.post(
        f"/api/teacher/grading/{first_item_id}/confirm",
        json={
            "suggestion_id": first_suggestion["suggestion_id"],
            "decision": "accept",
            "version": first_suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "second-confirm-key"},
    )
    assert replay_with_second_key.status_code == 200, replay_with_second_key.text
    assert replay_with_second_key.json()["data"]["replayed"] is True

    cross_item_key = await client.post(
        f"/api/teacher/grading/{second_item_id}/confirm",
        json={
            "suggestion_id": second_suggestion["suggestion_id"],
            "decision": "accept",
            "version": second_suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "shared-confirm-key"},
    )
    assert cross_item_key.status_code == 409
    assert cross_item_key.json()["code"] == 40902

    cross_suggestion_key = await client.post(
        f"/api/teacher/grading/{first_item_id}/confirm",
        json={
            "suggestion_id": replacement_suggestion["suggestion_id"],
            "decision": "accept",
            "version": replacement_suggestion["version"],
        },
        headers={**headers, "Idempotency-Key": "shared-confirm-key"},
    )
    assert cross_suggestion_key.status_code == 409
    assert cross_suggestion_key.json()["code"] == 40902

    async with async_session_factory() as db:
        actions = (
            await db.execute(
                select(TeacherAction).where(
                    TeacherAction.action_type == "grade.confirm",
                    TeacherAction.teacher_id == tid,
                )
            )
        ).scalars().all()
        first_item = await db.get(SubmissionItem, first_item_id)
    assert len(actions) == 2  # one transition and one different-key semantic replay
    assert first_item.confirmed_by == tid
    for action in actions:
        binding = action.details["binding"]
        assert binding["teacher_id"] == str(tid)
        assert binding["action"] == "grade.confirm"
        assert binding["class_id"] == str(cid)
        assert binding["submission_item_id"] == str(first_item_id)
        assert binding["suggestion_id"] == first_suggestion["suggestion_id"]
        assert binding["request_fingerprint"]


@pytest.mark.asyncio
@pytest.mark.parametrize("current_answer", ["A", ""])
async def test_accept_recomputes_immutable_quiz_evidence_before_confirming(
    client, current_answer
):
    """Changed or unscoreable standard answers must require an explicit override."""
    tid, cid, item_id = await _seed_objective_item(answer_text="B", standard_answer="B")
    headers = _auth(token(tid, "teacher"))
    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(cid), "client_request_id": f"evidence-{current_answer or 'empty'}"},
        headers=headers,
    )
    data = suggestion.json()["data"]
    async with async_session_factory() as db:
        item = await db.get(SubmissionItem, item_id)
        submission = await db.get(Submission, item.submission_id)
        persisted = await db.scalar(
            select(QuizItem).where(
                QuizItem.quiz_id == submission.quiz_id,
                QuizItem.item_no == item.item_no,
            )
        )
        persisted.answer = current_answer
        await db.commit()

    accept = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": data["suggestion_id"],
            "decision": "accept",
            "version": data["version"],
        },
        headers={**headers, "Idempotency-Key": f"evidence-accept-{current_answer or 'empty'}"},
    )
    assert accept.status_code == 422
    assert accept.json()["code"] == 42210

    override = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": data["suggestion_id"],
            "decision": "override",
            "final_score": 0.5,
            "version": data["version"],
        },
        headers={**headers, "Idempotency-Key": f"evidence-override-{current_answer or 'empty'}"},
    )
    assert override.status_code == 200, override.text


@pytest.mark.asyncio
async def test_identical_manual_override_replays_before_mutable_suggestion_validation(client):
    """A valid same-key override replay cannot be rejected by later draft mutation."""
    tid, cid, item_id = await _seed_item()
    headers = _auth(token(tid, "teacher"))
    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(cid), "client_request_id": "manual-override"},
        headers=headers,
    )
    data = suggestion.json()["data"]
    payload = {
        "suggestion_id": data["suggestion_id"],
        "decision": "override",
        "final_score": 4.0,
        "teacher_feedback": "人工确认",
        "version": data["version"],
    }
    first = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json=payload,
        headers={**headers, "Idempotency-Key": "manual-override-replay"},
    )
    assert first.status_code == 200, first.text
    async with async_session_factory() as db:
        item = await db.get(SubmissionItem, item_id)
        item.suggestion_rationale = {"type": "late-mutation"}
        await db.commit()

    replay = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json=payload,
        headers={**headers, "Idempotency-Key": "manual-override-replay"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["replayed"] is True


@pytest.mark.asyncio
async def test_reverse_order_batch_confirms_complete_without_deadlock(client):
    """Two concurrent reverse batches keep per-request result ordering and persist once."""
    tid, cid, (first_item_id, second_item_id) = await _seed_two_objective_items()
    headers = _auth(token(tid, "teacher"))

    async def suggest(item_id, request_id):
        response = await client.post(
            f"/api/teacher/grading/{item_id}/suggest",
            json={"class_id": str(cid), "client_request_id": request_id},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    first_suggestion = await suggest(first_item_id, "batch-first")
    second_suggestion = await suggest(second_item_id, "batch-second")
    first = {
        "submission_item_id": str(first_item_id),
        "suggestion_id": first_suggestion["suggestion_id"],
        "decision": "accept",
        "version": first_suggestion["version"],
    }
    second = {
        "submission_item_id": str(second_item_id),
        "suggestion_id": second_suggestion["suggestion_id"],
        "decision": "accept",
        "version": second_suggestion["version"],
    }
    responses = await asyncio.wait_for(
        asyncio.gather(
            client.post("/api/teacher/grading/batch-confirm", json={"items": [first, second]}, headers=headers),
            client.post("/api/teacher/grading/batch-confirm", json={"items": [second, first]}, headers=headers),
        ),
        timeout=5,
    )
    for response, expected in zip(responses, ([first, second], [second, first]), strict=True):
        assert response.status_code == 200, response.text
        results = response.json()["data"]["results"]
        assert [result["submission_item_id"] for result in results] == [
            entry["submission_item_id"] for entry in expected
        ]
        assert all(result["ok"] is True for result in results)

    async with async_session_factory() as db:
        items = [await db.get(SubmissionItem, item_id) for item_id in (first_item_id, second_item_id)]
        actions = (
            await db.execute(
                select(TeacherAction).where(
                    TeacherAction.teacher_id == tid,
                    TeacherAction.action_type == "grade.confirm",
                )
            )
        ).scalars().all()
    assert all(item.confirmed_at is not None for item in items)
    assert sum(not action.details["replayed"] for action in actions) == 2
