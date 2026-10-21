"""两端贯通回归（tel：课堂任务 · 批改 · 学情 · 教材统一，PLAN §9.1 B1）。

覆盖链路（全部 stub 模式，零真实 LLM/外网）：
1.  建班/加入/确认 + /api/classes/mine confirmed 学生计数（A1）；
2.  学生任务列表过滤未确认成员（D3 口径修复回归）；
3.  任务列表 teacher_name/kp_labels/进度（A2，pre_class/homework 进度按 QuizItem 统计）；
4.  发布学前作业/课后习题 type 扩展（A3/D2）+ 4b 提交强制 quiz 绑定（A2 :778 门）；
5.  roster + learning-overview 确定性聚合（A4/A5，含空班级诚实空态）；
6.  主观题建议分走共享 AI 初批管线（A6/D9，monkeypatch stub；GET 自动路径不调 AI）；
7.  confirm 掌握度按持久化满分比值计对（A7/D8）；
8.  blank 拍照 → pending_review → 教师 confirm → 学生终分/掌握度（愿景 4 端到端）；
9.  M3 门控关闭时新教师端点不可达（对齐 test_m3_teacher_profile 契约）。
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    MasteryRecord,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import async_session_factory
from app.models.file import File
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import QuestionBank, stem_hash
from tests._m3_helpers import add_member, make_class, make_user, token

# 1x1 真实 PNG（拍照作答种子；测试只需「有图无 OCR 产物」语义）
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


async def _make_kp(db, name: str | None = None) -> tuple[str, str]:
    """测试知识点（TELKP 前缀不落入 conftest 清理命名空间，每用例唯一 code）。"""
    code = f"TELKP-{uuid.uuid4().hex[:10]}"
    kp_name = name or f"贯通知识点{code[-4:]}"
    db.add(KnowledgePoint(code=code, name=kp_name))
    await db.flush()
    return code, kp_name


async def _make_quiz(
    db,
    teacher_id,
    items: list[dict],
    *,
    title: str = "贯通题组",
) -> tuple[Quiz, list[QuizItem]]:
    quiz = Quiz(
        user_id=teacher_id,
        source="assignment",
        title=title,
        kp_codes=sorted({it.get("kp_code") for it in items if it.get("kp_code")}),
        status="active",
    )
    db.add(quiz)
    await db.flush()
    quiz_items = []
    for idx, spec in enumerate(items, start=1):
        row = QuizItem(
            quiz_id=quiz.id,
            item_no=spec.get("item_no", idx),
            q_type=spec["q_type"],
            question_text=spec["question_text"],
            options=spec.get("options"),
            answer=spec["answer"],
            answer_analysis=spec.get("analysis"),
            kp_code=spec.get("kp_code"),
            difficulty=spec.get("difficulty", "easy"),
            ai_generated=False,
            max_score=spec.get("max_score"),
        )
        db.add(row)
        quiz_items.append(row)
    await db.flush()
    return quiz, quiz_items


async def _make_assignment(
    db,
    teacher_id,
    class_id,
    quiz: Quiz,
    *,
    assignment_type: str = "pre_class",
    status: str = "published",
    title: str = "贯通作业",
) -> Assignment:
    a = Assignment(
        class_id=class_id,
        creator_id=teacher_id,
        title=title,
        type=assignment_type,
        quiz_id=quiz.id,
        status=status,
    )
    db.add(a)
    await db.flush()
    db.add(AssignmentTarget(assignment_id=a.id, target_type="class", target_id=class_id))
    await db.flush()
    return a


async def _make_photo_file(db, user_id) -> File:
    f = File(
        user_id=user_id,
        filename="handwritten.png",
        mime="image/png",
        size_bytes=len(_PNG_1PX),
        sha256=uuid.uuid4().hex,
        storage_uri=f"local:test-{uuid.uuid4().hex}",
        file_type="image",
        status="uploaded",  # 无 OCR 产物 → 拍照链路 pending_review 分支
    )
    db.add(f)
    await db.flush()
    return f


# ==================== 1. A1：/api/classes/mine confirmed 学生计数 ====================


@pytest.mark.asyncio
async def test_classes_mine_returns_confirmed_student_count(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="计数教师")
        await db.commit()
    teacher_auth = _auth(token(teacher_id, "teacher"))

    created = await client.post(
        "/api/classes", json={"name": "贯通计数班", "grade": "高一"}, headers=teacher_auth
    )
    assert created.json()["code"] == 0, created.text
    class_id = created.json()["data"]["id"]
    invite_code = created.json()["data"]["inviteCode"]

    student_a = None
    student_b = None
    async with async_session_factory() as db:
        student_a = await make_user(db, nickname="学生甲")
        student_b = await make_user(db, nickname="学生乙")
        await db.commit()

    for stu in (student_a, student_b):
        joined = await client.post(
            "/api/classes/join",
            json={"invite_code": invite_code, "nickname": "想进班"},
            headers=_auth(token(stu, "student")),
        )
        assert joined.json()["code"] == 0, joined.text

    # 教师只确认学生甲 → confirmed/pending 混合
    confirmed = await client.patch(
        f"/api/classes/{class_id}/members/{student_a}",
        json={"confirmed": True},
        headers=teacher_auth,
    )
    assert confirmed.json()["code"] == 0, confirmed.text

    mine = await client.get("/api/classes/mine", headers=teacher_auth)
    assert mine.json()["code"] == 0, mine.text
    item = next(
        x for x in mine.json()["data"]["items"] if x["id"] == class_id
    )
    assert item["students"] == 1  # 只计 confirmed；pending 学生乙不计入
    assert item["inviteCode"] == invite_code

    # 已确认学生视角同样拿到计数（8100 normalize 消费契约）
    mine_student = await client.get("/api/classes/mine", headers=_auth(token(student_a, "student")))
    item_s = next(
        x for x in mine_student.json()["data"]["items"] if x["id"] == class_id
    )
    assert item_s["students"] == 1


# ==================== 2. D3：任务列表过滤未确认成员 ====================


@pytest.mark.asyncio
async def test_student_assignment_list_filters_unconfirmed_member(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        confirmed_id = await make_user(db)
        pending_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通过滤班")
        await add_member(db, class_id, confirmed_id, confirmed=True)
        await add_member(db, class_id, pending_id, confirmed=False)
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "choice", "question_text": "1+1=?", "options": {"A": "2", "B": "3"}, "answer": "A"},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="homework", title="课后习题·过滤"
        )
        await db.commit()
        assignment_id = assignment.id

    seen = await client.get(
        "/api/student/assignments?status=all", headers=_auth(token(confirmed_id, "student"))
    )
    assert seen.json()["code"] == 0, seen.text
    assert any(x["assignment_id"] == str(assignment_id) for x in seen.json()["data"]["items"])

    hidden = await client.get(
        "/api/student/assignments?status=all", headers=_auth(token(pending_id, "student"))
    )
    assert hidden.json()["code"] == 0
    assert hidden.json()["data"]["items"] == []  # pending 学生不消费班级定向任务（D3）


# ==================== 3. A2：teacher_name / kp_labels / 进度 ====================


@pytest.mark.asyncio
async def test_assignment_list_exposes_teacher_name_kp_labels_and_progress(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="贯通教师甲")
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通展示班")
        await add_member(db, class_id, student_id, confirmed=True)
        code, kp_name = await _make_kp(db, "函数与导数")
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "choice", "question_text": "导数题1", "options": {"A": "a", "B": "b"}, "answer": "A", "kp_code": code},
                {"q_type": "choice", "question_text": "导数题2", "options": {"A": "a", "B": "b"}, "answer": "B", "kp_code": code},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="pre_class", title="学前作业·导数"
        )
        await db.commit()
        assignment_id = assignment.id

    resp = await client.get(
        "/api/student/assignments?status=all", headers=_auth(token(student_id, "student"))
    )
    assert resp.json()["code"] == 0, resp.text
    item = next(
        x for x in resp.json()["data"]["items"] if x["assignment_id"] == str(assignment_id)
    )
    assert item["type"] == "pre_class"
    assert item["teacher_name"] == "贯通教师甲"
    assert item["kp_labels"] == [kp_name]
    # 进度按本人 submission_items 数 vs QuizItem 总数统计（type 门扩展回归），
    # 不得落入 watch 分支的 {done:0,total:1}
    assert item["progress"] == {"done": 0, "total": 2}

    # 提交一题后进度推进（done 按 submission_items 计数）
    submit = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"tel-progress-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
        },
        headers=_auth(token(student_id, "student")),
    )
    assert submit.json()["code"] == 0, submit.text
    resp2 = await client.get(
        "/api/student/assignments?status=all", headers=_auth(token(student_id, "student"))
    )
    item2 = next(
        x for x in resp2.json()["data"]["items"] if x["assignment_id"] == str(assignment_id)
    )
    assert item2["progress"] == {"done": 1, "total": 2}
    assert item2["overdue"] is False


# ==================== 4. A3/D2：type 扩展（create/publish） ====================


async def _generate_and_confirm_quiz(client, teacher_auth, class_id) -> str:
    tag = uuid.uuid4().hex[:8]
    async with async_session_factory() as db:
        code = f"TELKP-GEN-{tag}"
        db.add(KnowledgePoint(code=code, name=f"生成知识点{tag}"))
        stems = [
            (QuestionBank(
                stem=f"{tag} 选择题干：下列正确的是", q_type="choice",
                options={"A": "选项甲", "B": "选项乙"}, answer="A",
                analysis="选 A", difficulty="easy", kp_codes=[code], scope="student",
                hash=stem_hash(f"{tag}-choice"),
            )),
            (QuestionBank(
                stem=f"{tag} 填空题干：f(x)=x 的导数为____", q_type="blank",
                answer="1", analysis="常数导", difficulty="easy", kp_codes=[code], scope="student",
                hash=stem_hash(f"{tag}-blank"),
            )),
        ]
        db.add_all(stems)
        await db.commit()

    g = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(class_id),
            "knowledge_points": [f"TELKP-GEN-{tag}"],
            "count": 2,
            "question_types": {"choice": 1, "blank": 1, "text": 0},
        },
        headers=teacher_auth,
    )
    assert g.json()["code"] == 0, g.text
    artifact_id = g.json()["data"]["artifact_id"]
    confirmed = await client.post(
        f"/api/teacher/artifacts/{artifact_id}/confirm",
        json={"client_request_id": f"tel-confirm-{tag}"},
        headers=teacher_auth,
    )
    assert confirmed.json()["code"] == 0, confirmed.text
    return artifact_id


@pytest.mark.asyncio
async def test_create_assignment_accepts_pre_class_and_homework_types(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="发布教师")
        class_id = await make_class(db, teacher_id, name="贯通发布班")
        await db.commit()
    teacher_auth = _auth(token(teacher_id, "teacher"))
    artifact_id = await _generate_and_confirm_quiz(client, teacher_auth, class_id)

    # type=pre_class
    r1 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(class_id),
            "title": "学前作业·发布",
            "artifact_id": artifact_id,
            "client_assignment_id": f"tel-pre-{uuid.uuid4().hex[:8]}",
            "type": "pre_class",
        },
        headers=teacher_auth,
    )
    assert r1.json()["code"] == 0, r1.text
    assert r1.json()["data"]["type"] == "pre_class"
    pub1 = await client.post(
        f"/api/teacher/assignments/{r1.json()['data']['assignment_id']}/publish",
        json={"client_request_id": f"tel-pub-pre-{uuid.uuid4().hex[:8]}"},
        headers=teacher_auth,
    )
    assert pub1.json()["code"] == 0 and pub1.json()["data"]["status"] == "published"

    # type=homework
    r2 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(class_id),
            "title": "课后习题·发布",
            "artifact_id": artifact_id,
            "client_assignment_id": f"tel-hw-{uuid.uuid4().hex[:8]}",
            "type": "homework",
        },
        headers=teacher_auth,
    )
    assert r2.json()["code"] == 0, r2.text
    assert r2.json()["data"]["type"] == "homework"

    # 缺省 type → 兼容 quiz
    r3 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(class_id),
            "title": "课堂练习·缺省",
            "artifact_id": artifact_id,
            "client_assignment_id": f"tel-quiz-{uuid.uuid4().hex[:8]}",
        },
        headers=teacher_auth,
    )
    assert r3.json()["code"] == 0, r3.text
    assert r3.json()["data"]["type"] == "quiz"

    # 非法 type → 422（schema Literal 校验）
    r4 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(class_id),
            "title": "非法类型",
            "artifact_id": artifact_id,
            "client_assignment_id": f"tel-bad-{uuid.uuid4().hex[:8]}",
            "type": "watch",
        },
        headers=teacher_auth,
    )
    assert r4.status_code == 422


# ==================== 4b. A2 :778 门：pre_class 提交强制 quiz 绑定 ====================


@pytest.mark.asyncio
async def test_pre_class_submit_forces_quiz_binding(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通绑定班")
        await add_member(db, class_id, student_id, confirmed=True)
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "choice", "question_text": "3-1=?", "options": {"A": "2", "B": "3"}, "answer": "A"},
                {"q_type": "blank", "question_text": "f(x)=3x 的导数", "answer": "3"},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="pre_class", title="学前作业·绑定"
        )
        foreign_quiz, _ = await _make_quiz(
            db,
            teacher_id,
            [{"q_type": "choice", "question_text": "干扰题组", "options": {"A": "x", "B": "y"}, "answer": "B"}],
            title="干扰题组",
        )
        await db.commit()
        assignment_id = assignment.id
        quiz_id = quiz.id
        foreign_quiz_id = foreign_quiz.id

    # ① 传错 quiz_id → 40001（type 门扩展后 pre_class 同样强制绑定校验）
    wrong_quiz = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "quiz_id": str(foreign_quiz_id),
            "client_submit_id": f"tel-bind-wrong-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
        },
        headers=_auth(token(student_id, "student")),
    )
    assert wrong_quiz.json()["code"] == 40001

    # ② 不传 quiz_id → 强制绑定 assignment.quiz_id；choice/blank 判分以 DB 标答为准，
    #    客户端 expected_answer 不得回退生效（学生作答均按 DB 标答给出，
    #    若回退客户端 expected_answer 则必然判不出/判错）
    forged_expectation = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"tel-bind-forged-{uuid.uuid4().hex[:8]}",
            "items": [
                {"item_no": 1, "q_type": "choice", "answer_text": "A", "expected_answer": "B"},
                {"item_no": 2, "q_type": "blank", "answer_text": "3", "expected_answer": "9"},
            ],
        },
        headers=_auth(token(student_id, "student")),
    )
    assert forged_expectation.json()["code"] == 0, forged_expectation.text
    results = {r["item_no"]: r for r in forged_expectation.json()["data"]["results"]}
    assert results[1]["verdict"] == "correct"  # DB 标答 A 权威，expected_answer="B" 被忽略
    assert results[2]["verdict"] == "correct"  # DB 标答 3 权威（精确层），expected_answer="9" 不放水

    async with async_session_factory() as db:
        sub = (
            await db.execute(
                select(Submission).where(
                    Submission.assignment_id == assignment_id,
                    Submission.user_id == student_id,
                    Submission.deleted_at.is_(None),
                )
            )
        ).scalars().first()
        assert sub is not None and str(sub.quiz_id) == str(quiz_id)  # 强制绑定落库


# ==================== 5. A4/A5：roster + learning-overview 确定性聚合 ====================


@pytest.mark.asyncio
async def test_class_roster_and_learning_overview_deterministic(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="学情教师")
        s1 = await make_user(db, nickname="学生一")
        s2 = await make_user(db, nickname="学生二")
        class_id = await make_class(db, teacher_id, name="贯通学情班")
        await add_member(db, class_id, s1, confirmed=True)
        await add_member(db, class_id, s2, confirmed=True)
        code, kp_name = await _make_kp(db, "数列求和")
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "choice", "question_text": "1+2+3=?", "options": {"A": "6", "B": "5"}, "answer": "A", "kp_code": code, "max_score": 10.0},
                {"q_type": "blank", "question_text": "答案是？", "answer": "42", "kp_code": code, "max_score": 10.0},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="homework", title="课后习题·学情"
        )
        photo = await _make_photo_file(db, s2)
        empty_class_id = await make_class(db, teacher_id, name="贯通空班")
        await db.commit()
        assignment_id = assignment.id
        photo_id = photo.id

    # S1：choice 判错（→ error_record + 掌握度 0.5*0.8=0.4）
    r1 = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"tel-roster-s1-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 1, "q_type": "choice", "answer_text": "B"}],
        },
        headers=_auth(token(s1, "student")),
    )
    assert r1.json()["code"] == 0, r1.text
    assert r1.json()["data"]["results"][0]["verdict"] == "wrong"

    # S2：blank 拍照（无 OCR）→ pending_review
    r2 = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"tel-roster-s2-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 2, "q_type": "blank", "file_id": str(photo_id)}],
        },
        headers=_auth(token(s2, "student")),
    )
    assert r2.json()["code"] == 0, r2.text
    assert r2.json()["data"]["results"][0]["verdict"] == "pending_review"

    teacher_auth = _auth(token(teacher_id, "teacher"))
    roster = await client.get(
        f"/api/teacher/classes/{class_id}/roster", headers=teacher_auth
    )
    assert roster.json()["code"] == 0, roster.text
    students = {s["user_id"]: s for s in roster.json()["data"]["students"]}
    assert len(students) == 2
    assert students[str(s1)]["submission_count"] == 1
    assert students[str(s1)]["pending_count"] == 0
    assert students[str(s1)]["avg_mastery"] is not None
    assert abs(float(students[str(s1)]["avg_mastery"]) - 0.4) < 1e-6
    assert students[str(s1)]["weak_kps"] == [{"code": code, "name": kp_name, "mastery": 0.4}]
    assert students[str(s2)]["submission_count"] == 1
    assert students[str(s2)]["pending_count"] == 1
    assert students[str(s2)]["avg_mastery"] is None
    assert students[str(s2)]["weak_kps"] == []
    assert all(s["confirmed"] is True for s in students.values())

    overview = await client.get(
        f"/api/teacher/classes/{class_id}/learning-overview", headers=teacher_auth
    )
    assert overview.json()["code"] == 0, overview.text
    data = overview.json()["data"]
    assert data["pending_review_count"] == 1
    assert data["avg_mastery"] is not None and abs(data["avg_mastery"] - 0.4) < 1e-6
    assert data["kp_heat"] == [{"code": code, "name": kp_name, "wrong_rate": 1.0}]
    assert data["error_clusters"] == [{"kp_code": code, "label": kp_name, "count": 1}]
    trend = {row["date"]: row for row in data["submission_trend"]}
    today = max(trend)  # 窗口最后一日 = 今天
    assert trend[today]["submitted"] == 2
    assert trend[today]["total"] == 2  # confirmed 学生数（提交率分母）
    assert data["window_days"] == 14

    # 空班级诚实空态（不编造）
    empty_roster = await client.get(
        f"/api/teacher/classes/{empty_class_id}/roster", headers=teacher_auth
    )
    assert empty_roster.json()["code"] == 0
    assert empty_roster.json()["data"]["students"] == []
    empty_overview = await client.get(
        f"/api/teacher/classes/{empty_class_id}/learning-overview", headers=teacher_auth
    )
    empty_data = empty_overview.json()["data"]
    assert empty_data["pending_review_count"] == 0
    assert empty_data["kp_heat"] == []
    assert empty_data["error_clusters"] == []
    assert empty_data["avg_mastery"] is None
    assert all(row["submitted"] == 0 for row in empty_data["submission_trend"])

    # 越权：非本班教师 403/404（assert_teacher_in_class 守卫）
    async with async_session_factory() as db:
        outsider_id = await make_user(db)
        await db.commit()
    outsider = await client.get(
        f"/api/teacher/classes/{class_id}/roster", headers=_auth(token(outsider_id, "teacher"))
    )
    assert outsider.status_code in (403, 404)


# ==================== 6. A6/D9：suggest 走共享 AI 初批管线（stub） ====================


@pytest.mark.asyncio
async def test_suggest_grade_calls_ai_pipeline_for_subjective(client, monkeypatch):
    import app.services.solution_pregrade as sp

    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="批改教师")
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通批改班")
        await add_member(db, class_id, student_id, confirmed=True)
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "solution", "question_text": "求 f(x)=x² 的导数", "answer": "2x", "analysis": "幂函数求导"},
                {"q_type": "solution", "question_text": "求 f(x)=x³ 的导数", "answer": "3x²", "analysis": "幂函数求导"},
                {"q_type": "solution", "question_text": "求 f(x)=x⁴ 的导数", "answer": "4x³", "analysis": "幂函数求导"},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="homework", title="课后习题·批改"
        )
        await db.commit()
        class_id_str = str(class_id)
        assignment_id = str(assignment.id)

    async def _submit(answer: str) -> str:
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "assignment_id": assignment_id,
                "client_submit_id": f"tel-ai-{uuid.uuid4().hex[:8]}",
                "items": [{"item_no": 1, "q_type": "solution", "answer_text": answer}],
            },
            headers=_auth(token(student_id, "student")),
        )
        assert resp.json()["code"] == 0, resp.text
        # 每次提交后作业内 item_no=1 已有作答，但提交不互斥（真实重交链路），
        # 后续 suggest 每次都取最新一条 solution 作答。
        return resp.json()["data"]["submission_id"]

    teacher_auth = _auth(token(teacher_id, "teacher"))

    # ── ① AI 正常返回 → suggested_score=stub 分，needs_review=True，rationale=ai_pregrade
    async def _stub_ok(quiz_item, answer_text, **kwargs):
        return "pending_review", 6.5, {
            "ai_pregraded": True,
            "comment": "步骤正确，书写完整",
            "error_type": None,
            "max_score": 10.0,
            "confidence": 0.7,
        }

    monkeypatch.setattr(sp, "pregrade_solution", _stub_ok)
    await _submit("f'(x)=2x")
    item1 = await _latest_solution_item(client, teacher_auth, class_id_str)
    sug1 = await client.post(
        f"/api/teacher/grading/{item1}/suggest",
        json={"class_id": class_id_str, "client_request_id": f"tel-sug-ok-{uuid.uuid4().hex[:8]}"},
        headers=teacher_auth,
    )
    assert sug1.json()["code"] == 0, sug1.text
    assert sug1.json()["data"]["suggestion_score"] == 6.5
    assert sug1.json()["data"]["review_needed"] is True
    assert "步骤正确" in (sug1.json()["data"]["evidence"] or "")
    async with async_session_factory() as db:
        row = await db.get(SubmissionItem, uuid.UUID(item1))
        assert row.suggestion_rationale["type"] == "ai_pregrade"
        assert row.needs_review is True
        assert float(row.suggested_score) == 6.5

    # ── ② AI 失败/超时/输出非法 → 降级 0.0/0.3/manual_review
    async def _stub_raise(quiz_item, answer_text, **kwargs):
        raise TimeoutError("ai_unavailable")

    monkeypatch.setattr(sp, "pregrade_solution", _stub_raise)
    await _submit("f'(x)=3x²")
    item2 = await _latest_solution_item(client, teacher_auth, class_id_str)
    sug2 = await client.post(
        f"/api/teacher/grading/{item2}/suggest",
        json={"class_id": class_id_str, "client_request_id": f"tel-sug-fail-{uuid.uuid4().hex[:8]}"},
        headers=teacher_auth,
    )
    assert sug2.json()["code"] == 0, sug2.text
    assert sug2.json()["data"]["suggestion_score"] == 0.0
    assert sug2.json()["data"]["confidence"] == 0.3
    assert sug2.json()["data"]["review_needed"] is True
    async with async_session_factory() as db:
        row2 = await db.get(SubmissionItem, uuid.UUID(item2))
        assert row2.suggestion_rationale["type"] == "manual_review"

    # ── ③ GET /grading/{item} 自动建议路径不隐式调 AI（stub 未被调用）
    calls: list[int] = []

    async def _stub_spy(quiz_item, answer_text, **kwargs):
        calls.append(1)
        return "pending_review", 9.9, {"ai_pregraded": True, "comment": "x", "confidence": 0.7}

    monkeypatch.setattr(sp, "pregrade_solution", _stub_spy)
    await _submit("f'(x)=4x³")
    item3 = await _latest_solution_item(client, teacher_auth, class_id_str)
    detail = await client.get(
        f"/api/teacher/grading/{item3}?class_id={class_id_str}", headers=teacher_auth
    )
    assert detail.json()["code"] == 0, detail.text
    assert calls == []  # 自动生成路径零 AI 调用
    assert detail.json()["data"]["suggestion"]["suggestion_score"] == 0.0
    assert detail.json()["data"]["suggestion"]["review_needed"] is True


async def _latest_solution_item(client, teacher_auth, class_id: str) -> str:
    queue = await client.get(
        f"/api/teacher/grading/queue?class_id={class_id}", headers=teacher_auth
    )
    assert queue.json()["code"] == 0, queue.text
    pending = [
        q for q in queue.json()["data"]["queue"] if q["status"] != "confirmed"
    ]
    assert pending, queue.text
    # 队列按 created_at desc 排列 → 首个即最新提交的未确认作答
    return pending[0]["submission_item_id"]


# ==================== 7. A7/D8：confirm 掌握度按持久化满分比值计对 ====================


@pytest.mark.asyncio
async def test_confirmed_mastery_uses_persisted_max_score(client, monkeypatch):
    import app.services.solution_pregrade as sp

    async def _stub_degrade(quiz_item, answer_text, **kwargs):
        return "pending_review", None, {"degraded": "llm_unavailable"}

    monkeypatch.setattr(sp, "pregrade_solution", _stub_degrade)

    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="口径教师")
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通口径班")
        await add_member(db, class_id, student_id, confirmed=True)
        code_a, _ = await _make_kp(db)
        code_b, _ = await _make_kp(db)
        quiz1, _ = await _make_quiz(
            db,
            teacher_id,
            [{"q_type": "solution", "question_text": "满分10题", "answer": "x=1", "kp_code": code_a, "max_score": 10.0}],
            title="满分题组",
        )
        quiz2, _ = await _make_quiz(
            db,
            teacher_id,
            [{"q_type": "solution", "question_text": "无满分题", "answer": "y=2", "kp_code": code_b, "max_score": None}],
            title="无满分题组",
        )
        a1 = await _make_assignment(db, teacher_id, class_id, quiz1, assignment_type="homework", title="作业·满分10")
        a2 = await _make_assignment(db, teacher_id, class_id, quiz2, assignment_type="homework", title="作业·无满分")
        await db.commit()
        a1_id, a2_id = str(a1.id), str(a2.id)

    teacher_auth = _auth(token(teacher_id, "teacher"))

    async def _confirm_flow(assignment_id: str, final_score: float) -> None:
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "assignment_id": assignment_id,
                "client_submit_id": f"tel-mastery-{uuid.uuid4().hex[:8]}",
                "items": [{"item_no": 1, "q_type": "solution", "answer_text": "作答"}],
            },
            headers=_auth(token(student_id, "student")),
        )
        assert resp.json()["code"] == 0, resp.text
        item_id = await _latest_solution_item(client, teacher_auth, str(class_id))
        sug = await client.post(
            f"/api/teacher/grading/{item_id}/suggest",
            json={"class_id": str(class_id), "client_request_id": f"tel-m-sug-{uuid.uuid4().hex[:8]}"},
            headers=teacher_auth,
        )
        assert sug.json()["code"] == 0, sug.text
        confirmed = await client.post(
            f"/api/teacher/grading/{item_id}/confirm",
            json={
                "suggestion_id": sug.json()["data"]["suggestion_id"],
                "decision": "override",
                "final_score": final_score,
                "teacher_feedback": "口径确认",
                "version": 1,
            },
            headers={**teacher_auth, "Idempotency-Key": f"tel-mastery-{item_id}"},
        )
        assert confirmed.json()["code"] == 0, confirmed.text

    await _confirm_flow(a1_id, 6.0)   # 6 >= 0.6*10 → 计对
    await _confirm_flow(a2_id, 0.5)   # 0.5 < 1.0（旧口径）→ 不计对

    async with async_session_factory() as db:
        async def _mastery(kp_code: str) -> MasteryRecord:
            kp = (
                await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
            ).scalars().one()
            return await db.get(MasteryRecord, (student_id, kp.id))

        rec_a = await _mastery(
            (await db.execute(select(QuizItem).where(QuizItem.question_text == "满分10题")))
            .scalars().one().kp_code
        )
        rec_b = await _mastery(
            (await db.execute(select(QuizItem).where(QuizItem.question_text == "无满分题")))
            .scalars().one().kp_code
        )
        assert rec_a is not None
        assert rec_a.practice_count == 1 and rec_a.correct_count == 1  # 6 ≥ 0.6*10
        assert rec_b is not None
        assert rec_b.practice_count == 1 and rec_b.correct_count == 0  # 0.5 < 1.0 旧口径


# ==================== 8. 愿景4 端到端：blank 拍照 → pending → confirm → 终分/掌握度 ====================


@pytest.mark.asyncio
async def test_photo_blank_fullflow_pending_review_then_confirm(client):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="拍照批改教师")
        student_id = await make_user(db, nickname="拍照学生")
        class_id = await make_class(db, teacher_id, name="贯通拍照班")
        await add_member(db, class_id, student_id, confirmed=True)
        code, _kp_name = await _make_kp(db)
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "blank", "question_text": "4+4=?", "answer": "8", "kp_code": code, "max_score": 10.0},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="homework", title="课后习题·拍照"
        )
        photo = await _make_photo_file(db, student_id)
        await db.commit()
        assignment_id = str(assignment.id)
        photo_id = str(photo.id)

    student_auth = _auth(token(student_id, "student"))
    teacher_auth = _auth(token(teacher_id, "teacher"))

    submitted = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": assignment_id,
            "client_submit_id": f"tel-photo-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 1, "q_type": "blank", "file_id": photo_id}],
        },
        headers=student_auth,
    )
    assert submitted.json()["code"] == 0, submitted.text
    assert submitted.json()["data"]["results"][0]["verdict"] == "pending_review"
    assert submitted.json()["data"]["results"][0]["score"] is None

    queue = await client.get(
        f"/api/teacher/grading/queue?class_id={class_id}", headers=teacher_auth
    )
    queue_items = [q for q in queue.json()["data"]["queue"] if q["status"] != "confirmed"]
    assert len(queue_items) == 1
    item_id = queue_items[0]["submission_item_id"]

    detail = await client.get(
        f"/api/teacher/grading/{item_id}?class_id={class_id}", headers=teacher_auth
    )
    assert detail.json()["data"]["file_id"] == photo_id  # 手写原图对教师可见

    # blank 建议走本地人工路径（0.0/0.3，不调 AI——收窄口径）
    suggestion = await client.post(
        f"/api/teacher/grading/{item_id}/suggest",
        json={"class_id": str(class_id), "client_request_id": f"tel-photo-sug-{uuid.uuid4().hex[:8]}"},
        headers=teacher_auth,
    )
    assert suggestion.json()["code"] == 0, suggestion.text
    assert suggestion.json()["data"]["suggestion_score"] == 0.0
    assert suggestion.json()["data"]["review_needed"] is True

    confirmed = await client.post(
        f"/api/teacher/grading/{item_id}/confirm",
        json={
            "suggestion_id": suggestion.json()["data"]["suggestion_id"],
            "decision": "override",
            "final_score": 7.5,
            "teacher_feedback": "书写工整，结果正确。",
            "version": 1,
        },
        headers={**teacher_auth, "Idempotency-Key": f"tel-photo-confirm-{item_id}"},
    )
    assert confirmed.json()["code"] == 0, confirmed.text
    assert confirmed.json()["data"]["teacher_final_score"] == 7.5

    result = await client.get(
        f"/api/student/assignments/{assignment_id}/result", headers=student_auth
    )
    assert result.json()["code"] == 0, result.text
    assert result.json()["data"]["status"] == "graded"
    assert result.json()["data"]["total_score"] == 7.5
    assert result.json()["data"]["items"][0]["teacher_feedback"] == "书写工整，结果正确。"
    assert result.json()["data"]["items"][0]["confirmed_at"] is not None

    async with async_session_factory() as db:
        kp_row = (
            await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == code))
        ).scalars().one()
        rec = await db.get(MasteryRecord, (student_id, kp_row.id))
        assert rec is not None
        assert rec.practice_count == 1 and rec.correct_count == 1  # 7.5 ≥ 0.6*10


# ==================== 8b. tel F3：选择题自动批分闭环 ====================


@pytest.mark.asyncio
async def test_choice_auto_graded_score_reaches_student_result_and_queue(client):
    """tel F3：选择题提交即判 → 学生结果页免 confirm 即见自动分（刷新不丢）；
    批改工作台队列 auto_graded（无需逐题人审，进度计入已批）+ 队列分数快照=系统分。"""
    async with async_session_factory() as db:
        teacher_id = await make_user(db)
        student_id = await make_user(db)
        class_id = await make_class(db, teacher_id, name="贯通自动批班")
        await add_member(db, class_id, student_id, confirmed=True)
        quiz, _items = await _make_quiz(
            db,
            teacher_id,
            [
                {"q_type": "choice", "question_text": "1+1=?", "options": {"A": "2", "B": "3"},
                 "answer": "A", "max_score": 10},
            ],
        )
        assignment = await _make_assignment(
            db, teacher_id, class_id, quiz, assignment_type="quiz", title="课堂练习·自动批"
        )
        await db.commit()
        assignment_id = assignment.id

    tok = _auth(token(student_id, "student"))
    submit = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": str(assignment_id),
            "client_submit_id": f"tel-auto-{uuid.uuid4().hex[:8]}",
            "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
        },
        headers=tok,
    )
    assert submit.json()["code"] == 0, submit.text
    first = submit.json()["data"]["results"][0]
    assert first["verdict"] == "correct" and first["score"] == 10.0

    # 刷新取结果（无教师 confirm）：自动分不再被隐藏（修复前恒「待老师确认」）
    result = await client.get(f"/api/student/assignments/{assignment_id}/result", headers=tok)
    assert result.json()["code"] == 0, result.text
    data = result.json()["data"]
    assert data["items"][0]["score"] == 10.0
    assert data["items"][0]["confirmed_at"] is None
    assert data["total_score"] == 10.0

    # 批改工作台队列：state=auto_graded（无需逐题人审）+ 进度计入已批
    tauth = _auth(token(teacher_id, "teacher"))
    ws = await client.get(
        f"/api/teacher/grading/workspace?class_id={class_id}&status=all", headers=tauth
    )
    assert ws.json()["code"] == 0, ws.text
    queue = ws.json()["data"]["queue"]
    assert len(queue) == 1 and queue[0]["state"] == "auto_graded"
    assert ws.json()["data"]["context"]["progress"] == {"total": 1, "confirmed": 1, "remaining": 0}

    # 队列分数快照（grading/queue）：系统分直接可见（修复前恒 null）
    snapshot = await client.get(
        f"/api/teacher/grading/queue?class_id={class_id}", headers=tauth
    )
    row = snapshot.json()["data"]["queue"][0]
    assert row["suggestion_score"] == 10.0


# ==================== 9b. tel 热修：演示批次不入供给 + 题干剥原卷序号 ====================


@pytest.mark.asyncio
async def test_generate_quiz_excludes_demo_batch_rows(client):
    """cmm_math_v1（许可证未声明演示数据）即使被错标了命中 KP 也不得进组卷供给。

    生产事故：565 道内容横跨算法初步/组合数学的演示题被 hybrid 批量标注成
    「1.1 集合的概念」，学前诊断组卷把"输入语句"发给了学生。修复口径：
    供给链一律排除演示批次——宁缺勿错（generate honest warning 兜底）。
    """
    tag = uuid.uuid4().hex[:8]
    async with async_session_factory() as db:
        code = f"TELKP-DEMO-{tag}"
        db.add(KnowledgePoint(code=code, name=f"演示排除知识点{tag}"))
        db.add_all([
            # 错标演示题：内容与 KP 无关，但 kp_codes 命中 → 旧链路会选中它
            QuestionBank(
                stem=f"{tag} 输入语句的说法不正确的是", q_type="choice",
                options={"A": "甲", "B": "乙"}, answer="A", analysis="演示",
                difficulty="easy", kp_codes=[code], scope="student",
                source_batch="cmm_math_v1", source="CMM-Math·高中·逻辑题（演示）",
                hash=stem_hash(f"{tag}-demo"),
            ),
            # 可信真题（高考批次）：同 KP 同题型，应被选中
            QuestionBank(
                stem=f"{tag} 已知集合M的子集个数是", q_type="choice",
                options={"A": "2", "B": "4"}, answer="B", analysis="子集 2^n",
                difficulty="easy", kp_codes=[code], scope="student",
                source_batch="gaokao_mm_v1", source="2013新课标ⅱMath II",
                hash=stem_hash(f"{tag}-real"),
            ),
        ])
        await db.commit()
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="演示排除教师")
        class_id = await make_class(db, teacher_id, name="演示排除班")
        await db.commit()
    auth = _auth(token(teacher_id, "teacher"))

    g = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(class_id),
            "knowledge_points": [code],
            "count": 1,
            "question_types": {"choice": 1, "blank": 0, "text": 0},
        },
        headers=auth,
    )
    assert g.json()["code"] == 0, g.text
    items = g.json()["data"]["content"]["items"]
    stems = [it["question_text"] for it in items]
    assert stems == [f"{tag} 已知集合M的子集个数是"], stems
    assert all("输入语句" not in s for s in stems)


def test_item_from_row_strips_leading_exam_numbering():
    """题库真题自带原卷序号（"1.（5 分）…"）不再与 item_no 叠成双序号；
    序号后紧跟数字（3.14）不剥，避免破坏小数题干。"""
    from app.domains.teacher.assessment import _item_from_row

    def row_with(stem: str) -> QuestionBank:
        return QuestionBank(
            stem=stem, q_type="choice", options={"A": "甲", "B": "乙"}, answer="A",
            analysis="析", difficulty="easy", kp_codes=[], scope="student",
        )

    allowed: set[str] = set()
    assert _item_from_row(row_with("1.（5 分）已知集合M"), 3, allowed)["question_text"] == "已知集合M"
    assert _item_from_row(row_with("18. 命题p的否命题是"), 2, allowed)["question_text"] == "命题p的否命题是"
    assert _item_from_row(row_with("3.14 计算"), 4, allowed)["question_text"] == "3.14 计算"
    assert _item_from_row(row_with("已知集合A"), 5, allowed)["question_text"] == "已知集合A"


# ==================== 9. M3 门控：关闭时新教师端点不可达 ====================


def test_teacher_learning_endpoints_gated_when_m3_disabled():
    """M3_ENABLE_TEACHER=false（默认）→ roster/learning-overview 不在 OpenAPI（404 契约）。"""
    code = (
        "from app.main import app; "
        "paths=app.openapi()['paths']; "
        "leaked=[p for p in paths if '/api/teacher/classes/{class_id}/roster' == p "
        "or '/api/teacher/classes/{class_id}/learning-overview' == p]; "
        "assert leaked==[], leaked"
    )
    env = dict(os.environ)
    env["M3_ENABLE_TEACHER"] = "false"
    env.pop("DATABASE_URL", None)  # 免连接数据库，仅验证路由挂载门控
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
    )
    assert result.returncode == 0, f"M3 关闭时不应暴露新教师端点：\n{result.stdout}\n{result.stderr}"
