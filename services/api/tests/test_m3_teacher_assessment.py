"""M3 教师端：题集/作业流程（§12）。"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import JSONB

from app.main import app
from app.models.coursework import QuizItem
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank, stem_hash
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


async def _seed_bank(count: int = 3):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        tag = uuid.uuid4().hex[:8]
        for i in range(count):
            stem = f"{tag}题干{i} 求导 f(x)={i}"
            db.add(
                QuestionBank(
                    stem=stem,
                    q_type="solution",
                    answer=f"答案{i}",
                    difficulty="medium",
                    kp_codes=["MATH-002"],
                    scope="student",
                    hash=stem_hash(stem),
                )
            )
        await db.commit()
        return tid, cid


async def _make_confirmed_quiz(client, tok, cid) -> str:
    request_key = uuid.uuid4().hex
    g = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 3,
            "question_types": {"choice": 0, "blank": 0, "text": 3},
        },
        headers=_auth(tok),
    )
    assert g.json()["code"] == 0, g.text
    aid = g.json()["data"]["artifact_id"]
    await client.post(
        f"/api/teacher/artifacts/{aid}/confirm",
        json={"client_request_id": f"q-{request_key}", "idempotency_key": f"qc-{request_key}"},
        headers=_auth(tok),
    )
    return aid


@pytest.mark.asyncio
async def test_generate_quiz_creates_draft_artifact(client):
    tid, cid = await _seed_bank(3)
    g = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 3,
            "question_types": {"choice": 0, "blank": 0, "text": 3},
        },
        headers=_auth(token(tid, "teacher")),
    )
    assert g.json()["code"] == 0
    data = g.json()["data"]
    assert data["status"] == "draft"
    assert len(data["content"]["items"]) == 3


@pytest.mark.asyncio
async def test_generate_quiz_keeps_only_strict_matches_when_bank_is_insufficient(client):
    """Cross-topic or local-template padding must never make a quiz look publishable."""
    tid, cid = await _seed_bank(0)
    kp_code = f"TASK2-{uuid.uuid4().hex}"
    stem = f"{kp_code} 严格命中题"
    async with async_session_factory() as db:
        db.add(
            QuestionBank(
                stem=stem,
                q_type="solution",
                answer="唯一答案",
                analysis="唯一解析",
                difficulty="medium",
                kp_codes=[kp_code],
                scope="student",
                hash=stem_hash(stem),
            )
        )
        await db.commit()

    try:
        g = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 5,
                "question_types": {"choice": 0, "blank": 0, "text": 5},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert g.json()["code"] == 0, g.text
        data = g.json()["data"]
        assert [item["question_text"] for item in data["content"]["items"]] == [stem]
        assert data["degraded"] is True
        assert data["content"]["insufficient"] is True
        assert all(item["source"] != "local_template" for item in data["content"]["items"])
        assert data["validation"]["requested_count"] == 5
        assert data["validation"]["available_count"] == 1
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_generate_quiz_marks_count_distribution_mismatch_as_insufficient(client):
    """The requested count, not the partial type distribution, controls publication safety."""
    tid, cid = await _seed_bank(0)
    kp_code = f"TASK2-MISMATCH-{uuid.uuid4().hex}"
    stem = f"{kp_code} 唯一选择题"
    async with async_session_factory() as db:
        db.add(
            QuestionBank(
                stem=stem,
                q_type="choice",
                options={"A": "正确", "B": "错误"},
                answer="A",
                analysis="唯一解析",
                difficulty="medium",
                kp_codes=[kp_code],
                scope="student",
                hash=stem_hash(stem),
            )
        )
        await db.commit()

    try:
        g = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 5,
                "question_types": {"choice": 1, "blank": 0, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert g.json()["code"] == 0, g.text
        data = g.json()["data"]
        assert [item["question_text"] for item in data["content"]["items"]] == [stem]
        assert data["content"]["count"] == 5
        assert data["content"]["insufficient"] is True
        assert data["degraded"] is True
        assert data["validation"]["requested_count"] == 5
        assert data["validation"]["available_count"] == 1
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


async def _seed_rows(kp_code: str, specs: list[tuple[str, str, str | None, str | None]]):
    """Seed deterministic bank rows: (type, difficulty, analysis, leading_kp)."""
    async with async_session_factory() as db:
        stems: list[str] = []
        for index, (q_type, difficulty, analysis, leading_kp) in enumerate(specs):
            stem = f"{kp_code} 组卷合同题 {index} {q_type} {difficulty}"
            stems.append(stem)
            db.add(
                QuestionBank(
                    stem=stem,
                    q_type=q_type,
                    options={"A": "甲", "B": "乙"} if q_type == "choice" else None,
                    answer="A" if q_type == "choice" else "答案",
                    analysis=analysis,
                    difficulty=difficulty,
                    kp_codes=[leading_kp or kp_code],
                    scope="student",
                    hash=stem_hash(stem),
                )
            )
        await db.commit()
    return stems


@pytest.mark.asyncio
async def test_generate_quiz_normalizes_under_quota_without_dropping_requested_types(client):
    tid, cid = await _seed_bank(0)
    kp_code = f"T3U-{uuid.uuid4().hex[:16]}"
    try:
        await _seed_rows(
            kp_code,
            [
                ("choice", "easy", "解析", None),
                ("choice", "medium", "解析", None),
                ("blank", "easy", "解析", None),
            ],
        )
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 3,
                "question_types": {"choice": 1, "blank": 1, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert response.json()["code"] == 0, response.text
        data = response.json()["data"]
        assert len(data["content"]["items"]) == 3
        assert {item["q_type"] for item in data["content"]["items"]} == {"choice", "blank"}
        assert data["content"]["question_type_distribution"] == {
            "choice": 2,
            "blank": 1,
            "solution": 0,
        }
        assert data["validation"]["requested_question_type_distribution"] == {
            "choice": 1,
            "blank": 1,
            "text": 0,
        }
        assert data["validation"]["quota_normalized"] is True
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_generate_quiz_rejects_over_quota_instead_of_silently_dropping_a_type(client):
    tid, cid = await _seed_bank(0)
    response = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 1,
            "question_types": {"choice": 1, "blank": 1, "text": 0},
        },
        headers=_auth(token(tid, "teacher")),
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001
    assert response.json()["message"] == "question_type_quota_exceeds_count"


@pytest.mark.asyncio
async def test_generate_quiz_fulfills_difficulty_slots_and_audits_same_scope_relaxation(client):
    tid, cid = await _seed_bank(0)
    kp_code = f"T3D-{uuid.uuid4().hex[:16]}"
    try:
        await _seed_rows(
            kp_code,
            [
                ("solution", "easy", "解析", None),
                ("solution", "easy", "解析", None),
                ("solution", "hard", "解析", None),
                ("solution", "medium", "解析", None),
            ],
        )
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 4,
                "question_types": {"choice": 0, "blank": 0, "text": 4},
                "difficulty": {"easy": 0.5, "medium": 0, "hard": 0.5},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert response.json()["code"] == 0, response.text
        data = response.json()["data"]
        slots = data["validation"]["slot_fulfillment"]
        hard = next(
            slot
            for slot in slots
            if slot["question_type"] == "text" and slot["difficulty"] == "hard"
        )
        assert hard == {
            "question_type": "text",
            "difficulty": "hard",
            "requested": 2,
            "fulfilled": 2,
            "relaxed": 1,
        }
        assert any("hard" in warning and "放宽" in warning for warning in data["warnings"])
        assert data["validation"]["expanded_knowledge_points"] == [kp_code]
        assert [(item["kp_code"], item["q_type"]) for item in data["content"]["items"]] == [
            (kp_code, "solution")
        ] * 4
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_generate_quiz_expands_requested_subtree_but_never_selects_parent_or_sibling_kp(
    client,
):
    from app.models.knowledge_point import KnowledgePoint

    tid, cid = await _seed_bank(0)
    prefix = f"TASK3-TREE-{uuid.uuid4().hex[:8]}"
    parent_code, child_code, sibling_code = f"{prefix}-P", f"{prefix}-C", f"{prefix}-S"
    try:
        async with async_session_factory() as db:
            parent = KnowledgePoint(code=parent_code, name="父节点", grade="高一")
            db.add(parent)
            await db.flush()
            db.add_all(
                [
                    KnowledgePoint(
                        code=child_code, name="子节点", grade="高一", parent_id=parent.id
                    ),
                    KnowledgePoint(
                        code=sibling_code, name="兄弟节点", grade="高一", parent_id=parent.id
                    ),
                ]
            )
            await db.commit()
        child_stem, parent_stem, sibling_stem = await _seed_rows(
            prefix,
            [
                ("solution", "medium", "解析", child_code),
                ("solution", "medium", "解析", parent_code),
                ("solution", "medium", "解析", sibling_code),
            ],
        )
        joint_stem = f"{prefix} 联合知识点题"
        async with async_session_factory() as db:
            db.add(
                QuestionBank(
                    stem=joint_stem,
                    q_type="solution",
                    answer="联合答案",
                    analysis="联合解析",
                    difficulty="medium",
                    kp_codes=[sibling_code, child_code],
                    scope="student",
                    hash=stem_hash(joint_stem),
                )
            )
            await db.commit()
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [child_code],
                "count": 3,
                "question_types": {"choice": 0, "blank": 0, "text": 3},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert response.json()["code"] == 0, response.text
        items = response.json()["data"]["content"]["items"]
        assert {item["question_text"] for item in items} == {child_stem, joint_stem}
        assert {item["kp_code"] for item in items} == {child_code}
        assert parent_stem not in [item["question_text"] for item in items]
        assert sibling_stem not in [item["question_text"] for item in items]
    finally:
        async with async_session_factory() as db:
            await db.execute(
                delete(QuestionBank).where(
                    QuestionBank.kp_codes.overlap([parent_code, child_code, sibling_code])
                )
            )
            await db.execute(
                delete(KnowledgePoint).where(
                    KnowledgePoint.code.in_([parent_code, child_code, sibling_code])
                )
            )
            await db.commit()


@pytest.mark.asyncio
async def test_generate_quiz_excludes_incomplete_choice_but_keeps_missing_analysis_with_warning(
    client,
):
    tid, cid = await _seed_bank(0)
    kp_code = f"T3C-{uuid.uuid4().hex[:16]}"
    malformed_stem = f"{kp_code} 选择题缺选项"
    missing_answer_stem = f"{kp_code} 选择题缺答案"
    complete_stem = f"{kp_code} 选择题无解析"
    try:
        async with async_session_factory() as db:
            for stem, options, answer, analysis in [
                (malformed_stem, None, "A", "解析"),
                (missing_answer_stem, {"A": "甲"}, "", "解析"),
                (complete_stem, {"A": "甲", "B": "乙"}, "A", None),
            ]:
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options=options,
                        answer=answer,
                        analysis=analysis,
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(stem),
                    )
                )
            await db.commit()
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 2,
                "question_types": {"choice": 2, "blank": 0, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert response.json()["code"] == 0, response.text
        data = response.json()["data"]
        assert [item["question_text"] for item in data["content"]["items"]] == [complete_stem]
        assert data["content"]["items"][0]["analysis"] == "题库未提供解析，请教师确认后补充。"
        assert any("解析" in warning for warning in data["warnings"])
        assert data["validation"]["available_count"] == 1
        assert data["content"]["insufficient"] is True
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_new_assignment_is_draft_and_publish(client):
    tid, cid = await _seed_bank(3)
    tok = token(tid, "teacher")
    aid = await _make_confirmed_quiz(client, tok, cid)
    # 未确认不可建？已确认；创建 assignment draft
    r = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(cid),
            "title": "周测",
            "artifact_id": aid,
            "client_assignment_id": "ca-1",
        },
        headers=_auth(tok),
    )
    assert r.json()["code"] == 0
    a = r.json()["data"]
    assert a["status"] == "draft"
    # 班级定向已写入：学生端 /api/student/assignments 依赖 assignment_targets
    async with async_session_factory() as db:
        from sqlalchemy import select as sel

        from app.models.coursework import AssignmentTarget

        targets = (
            (
                await db.execute(
                    sel(AssignmentTarget).where(
                        AssignmentTarget.assignment_id == uuid.UUID(a["assignment_id"]),
                        AssignmentTarget.target_type == "class",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(targets) >= 1, "发布作业必须定向班级（学生联动可见性）"
    # 幂等：同 client_assignment_id
    r2 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(cid),
            "title": "周测",
            "artifact_id": aid,
            "client_assignment_id": "ca-1",
        },
        headers=_auth(tok),
    )
    assert r2.json()["data"]["assignment_id"] == a["assignment_id"]
    assert r2.json()["data"]["replayed"] is True
    # publish
    p = await client.post(
        f"/api/teacher/assignments/{a['assignment_id']}/publish",
        json={
            "client_request_id": f"p-{uuid.uuid4().hex}",
            "idempotency_key": f"pk-{uuid.uuid4().hex}",
        },
        headers=_auth(tok),
    )
    assert p.json()["data"]["status"] == "published"


@pytest.mark.asyncio
async def test_create_assignment_requires_confirmed_quiz(client):
    tid, cid = await _seed_bank(3)
    tok = token(tid, "teacher")
    # 生成但未确认的 quiz_set artifact
    g = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 3,
            "question_types": {"choice": 0, "blank": 0, "text": 3},
        },
        headers=_auth(tok),
    )
    aid = g.json()["data"]["artifact_id"]
    r = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(cid),
            "title": "未确认",
            "artifact_id": aid,
            "client_assignment_id": "ca-2",
        },
        headers=_auth(tok),
    )
    assert r.json()["code"] == 42210


@pytest.mark.asyncio
async def test_create_assignment_accepts_inline_quiz_items_for_diagnostic(client):
    """tel F1：备课诊断内联题组直发（tel 学前诊断链）。

    - quiz_items 与 artifact_id 二选一；皆缺 → 422（schema XOR 校验，fail-closed）；
    - 内联题组 → 直接物化 Quiz + QuizItems（与 artifact 路径同一落库事实），
      source_artifact_id 为 None；同 client_assignment_id 幂等重放。
    """
    from app.models.coursework import Assignment

    tid, cid = await _seed_bank(1)
    tok = token(tid, "teacher")
    items = [
        {
            "item_no": 1,
            "q_type": "choice",
            "question_text": "下列哪一组对象构成集合？",
            "options": {"A": "高一(3)班全体女生", "B": "很小的数"},
            "answer": "A",
            "analysis": "考查集合元素的确定性",
            "kp_code": "MATH-002",
            "difficulty": "easy",
            "max_score": 2,
        },
        {
            "item_no": 2,
            "q_type": "solution",
            "question_text": "简述集合中元素的确定性",
            "answer": "确定性",
            "analysis": "主观题，教师终审",
            "kp_code": "MATH-002",
            "difficulty": "medium",
            "max_score": 5,
        },
    ]
    key = f"tel-diag-{uuid.uuid4().hex[:12]}"
    body = {
        "class_id": str(cid),
        "title": "学前诊断",
        "quiz_items": items,
        "client_assignment_id": key,
        "type": "pre_class",
    }
    r = await client.post("/api/teacher/assignments", json=body, headers=_auth(tok))
    assert r.json()["code"] == 0, r.text
    a = r.json()["data"]
    assert a["status"] == "draft" and a["type"] == "pre_class"
    # 同 client_assignment_id 幂等重放（8100 422 对账读回依赖）
    r2 = await client.post("/api/teacher/assignments", json=body, headers=_auth(tok))
    assert r2.json()["data"]["assignment_id"] == a["assignment_id"]
    assert r2.json()["data"]["replayed"] is True
    # 物化事实：Quiz + QuizItems 真实存在，source_artifact_id=None
    async with async_session_factory() as db:
        row = await db.get(Assignment, uuid.UUID(a["assignment_id"]))
        assert row is not None and row.quiz_id is not None
        assert row.source_artifact_id is None
        qitems = (
            (await db.execute(select(QuizItem).where(QuizItem.quiz_id == row.quiz_id)))
            .scalars()
            .all()
        )
        assert len(qitems) == 2
        assert qitems[0].options == {"A": "高一(3)班全体女生", "B": "很小的数"}
        assert qitems[0].answer == "A"
    # XOR 红线：artifact_id 与 quiz_items 皆缺 → 422（不得静默建空任务）
    r3 = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(cid),
            "title": "空任务",
            "client_assignment_id": f"tel-diag-{uuid.uuid4().hex[:12]}",
        },
        headers=_auth(tok),
    )
    assert r3.status_code == 422


@pytest.mark.asyncio
async def test_solution_quiz_materializes_and_student_submission_enters_teacher_review(client):
    from tests._m3_helpers import add_member

    tid, cid = await _seed_bank(1)
    async with async_session_factory() as db:
        student_id = await make_user(db, teacher_verified=None)
        await add_member(db, cid, student_id)
        await db.commit()
    teacher_auth = _auth(token(tid, "teacher"))
    generated = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 1,
            "question_types": {"choice": 0, "blank": 0, "text": 1},
        },
        headers=teacher_auth,
    )
    artifact = generated.json()["data"]
    assert artifact["content"]["items"][0]["q_type"] == "solution"
    aid = artifact["artifact_id"]
    await client.post(
        f"/api/teacher/artifacts/{aid}/confirm",
        json={"client_request_id": "solution-confirm"},
        headers=teacher_auth,
    )
    created = await client.post(
        "/api/teacher/assignments",
        json={
            "class_id": str(cid),
            "title": "主观题",
            "artifact_id": aid,
            "client_assignment_id": f"solution-{uuid.uuid4().hex}",
        },
        headers=teacher_auth,
    )
    assignment_id = created.json()["data"]["assignment_id"]
    await client.post(
        f"/api/teacher/assignments/{assignment_id}/publish",
        json={"client_request_id": "solution-publish"},
        headers=teacher_auth,
    )
    student_auth = _auth(token(student_id, "student"))
    detail = await client.get(f"/api/student/assignments/{assignment_id}", headers=student_auth)
    quiz_id = detail.json()["data"]["quiz_id"]
    assert detail.json()["data"]["items"][0]["q_type"] == "solution"
    submitted = await client.post(
        "/api/student/practice/submit",
        json={
            "assignment_id": assignment_id,
            "quiz_id": quiz_id,
            "client_submit_id": uuid.uuid4().hex,
            "items": [{"item_no": 1, "q_type": "solution", "answer_text": "证明过程"}],
        },
        headers=student_auth,
    )
    assert submitted.json()["code"] == 0
    assert submitted.json()["data"]["results"][0]["verdict"] == "pending_review"


@pytest.mark.asyncio
async def test_explicit_high_school_math_rubric_materializes_from_question_bank_to_assignment(
    client,
):
    """A reviewed source rubric must survive the real quiz-set → assignment path unchanged."""
    tid, cid = await _seed_bank(0)
    kp_code = f"MATH-MONOTONICITY-{uuid.uuid4().hex[:12]}"
    rubric = [
        {"id": "derivative", "criterion": "正确求导", "points": 3, "evidence_hint": "写出 f'(x)"},
        {"id": "critical", "criterion": "确定分界点", "points": 3, "evidence_hint": "令 f'(x)=0"},
        {
            "id": "interval",
            "criterion": "写出单调区间",
            "points": 4,
            "evidence_hint": "说明增减区间",
        },
    ]
    stem = "已知 $f(x)=x^3-3x$，讨论函数的单调性。"
    try:
        async with async_session_factory() as db:
            db.add(
                QuestionBank(
                    stem=stem,
                    q_type="solution",
                    answer="$(-\\infty,-1)\\cup(1,+\\infty)$ 单调递增。",
                    analysis="先求导，再由导数符号判定。",
                    difficulty="medium",
                    kp_codes=[kp_code],
                    scope="student",
                    hash=stem_hash(stem),
                    annotate_meta={"max_score": 10, "grading_rubric": rubric},
                )
            )
            await db.commit()

        teacher_auth = _auth(token(tid, "teacher"))
        generated = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 1,
                "question_types": {"choice": 0, "blank": 0, "text": 1},
            },
            headers=teacher_auth,
        )
        assert generated.status_code == 200, generated.text
        generated_item = generated.json()["data"]["content"]["items"][0]
        assert generated_item["max_score"] == 10
        assert generated_item["grading_rubric"] == rubric

        artifact_id = generated.json()["data"]["artifact_id"]
        confirmed = await client.post(
            f"/api/teacher/artifacts/{artifact_id}/confirm",
            json={"client_request_id": f"rubric-confirm-{uuid.uuid4().hex}"},
            headers=teacher_auth,
        )
        assert confirmed.status_code == 200, confirmed.text
        created = await client.post(
            "/api/teacher/assignments",
            json={
                "class_id": str(cid),
                "title": "函数单调性",
                "artifact_id": artifact_id,
                "client_assignment_id": f"rubric-assignment-{uuid.uuid4().hex}",
            },
            headers=teacher_auth,
        )
        assert created.status_code == 200, created.text

        async with async_session_factory() as db:
            quiz_item = (
                await db.execute(select(QuizItem).where(QuizItem.question_text == stem))
            ).scalar_one()
        assert float(quiz_item.max_score) == 10.0
        assert quiz_item.grading_rubric == rubric
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_difficulty_exact_phase_reserves_hard_inventory_before_relaxing_easy(client):
    tid, cid = await _seed_bank(0)
    kp_code = f"T3P-{uuid.uuid4().hex[:16]}"
    try:
        await _seed_rows(
            kp_code,
            [
                ("solution", "easy", "解析", None),
                ("solution", "hard", "解析", None),
                ("solution", "hard", "解析", None),
                ("solution", "medium", "解析", None),
            ],
        )
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 4,
                "question_types": {"choice": 0, "blank": 0, "text": 4},
                "difficulty": {"easy": 0.5, "medium": 0, "hard": 0.5},
            },
            headers=_auth(token(tid, "teacher")),
        )
        slots = response.json()["data"]["validation"]["slot_fulfillment"]
        assert next(slot for slot in slots if slot["difficulty"] == "hard")["relaxed"] == 0
        assert next(slot for slot in slots if slot["difficulty"] == "easy")["relaxed"] == 1
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_choice_requires_two_options_and_a_matching_answer_key(client):
    tid, cid = await _seed_bank(0)
    kp_code = f"T3O-{uuid.uuid4().hex[:16]}"
    try:
        async with async_session_factory() as db:
            for index, options, answer in [
                (1, {"A": "唯一"}, "A"),
                (2, {"A": "甲", "B": "乙"}, "Z"),
                (3, {" A ": "甲", "B": "乙"}, "a"),
            ]:
                stem = f"{kp_code} choice {index}"
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options=options,
                        answer=answer,
                        analysis="  ",
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(stem),
                    )
                )
            await db.commit()
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 2,
                "question_types": {"choice": 2, "blank": 0, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        data = response.json()["data"]
        assert [item["answer"] for item in data["content"]["items"]] == ["a"]
        assert data["content"]["items"][0]["analysis"] == "题库未提供解析，请教师确认后补充。"
        assert any("解析" in warning for warning in data["warnings"])
        assert data["content"]["question_type_distribution"] == {
            "choice": 1,
            "blank": 0,
            "solution": 0,
        }
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_generate_quiz_publishable_sql_handles_non_object_json_and_all_ascii_whitespace(
    client,
):
    """JSONB invalid shapes and tab/newline-only fields are filtered in SQL, never 500."""
    tid, cid = await _seed_bank(0)
    kp_code = f"T3JSON-{uuid.uuid4().hex[:16]}"
    try:
        async with async_session_factory() as db:
            invalid_options = [None, JSONB.NULL, [], "scalar", {"\t": "甲", "B": "\n"}]
            for index, options in enumerate(invalid_options):
                stem = "\t\n" if index == 0 else f"{kp_code} malformed {index}"
                answer = "\r\f " if index == 1 else "A"
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options=options,
                        answer=answer,
                        analysis="解析",
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(f"{stem}-{index}"),
                    )
                )
            for index in range(2):
                stem = f"{kp_code} valid {index}"
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options={" A\t": "甲", "B": "乙"},
                        answer=" a ",
                        analysis="解析",
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(stem),
                    )
                )
            await db.commit()
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 2,
                "question_types": {"choice": 2, "blank": 0, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert {item["question_text"] for item in data["content"]["items"]} == {
            f"{kp_code} valid 0",
            f"{kp_code} valid 1",
        }
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()


@pytest.mark.asyncio
async def test_any_difficulty_slot_never_reports_relaxation(client):
    tid, cid = await _seed_bank(2)
    response = await client.post(
        "/api/teacher/quizzes/generate",
        json={
            "class_id": str(cid),
            "knowledge_points": ["MATH-002"],
            "count": 2,
            "question_types": {"choice": 0, "blank": 0, "text": 2},
        },
        headers=_auth(token(tid, "teacher")),
    )
    data = response.json()["data"]
    assert data["validation"]["slot_fulfillment"] == [
        {
            "question_type": "text",
            "difficulty": "any",
            "requested": 2,
            "fulfilled": 2,
            "relaxed": 0,
        },
    ]
    assert not any("放宽" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_generate_quiz_sql_publishability_filter_does_not_overfetch_into_false_shortage(
    client,
):
    """More malformed rows than the former 8x/64 cap cannot hide ten valid choices."""
    tid, cid = await _seed_bank(0)
    kp_code = f"T3SQL-{uuid.uuid4().hex[:16]}"
    try:
        async with async_session_factory() as db:
            for index in range(81):  # exceeds the old max(64, count * 8) for count=10
                stem = f"{kp_code} malformed {index}"
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options=None,
                        answer="A",
                        analysis="解析",
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(stem),
                    )
                )
            for index in range(10):
                stem = f"{kp_code} publishable {index}"
                db.add(
                    QuestionBank(
                        stem=stem,
                        q_type="choice",
                        options={"A": "甲", "B": "乙"},
                        answer=" a ",
                        analysis="解析",
                        difficulty="medium",
                        kp_codes=[kp_code],
                        scope="student",
                        hash=stem_hash(stem),
                    )
                )
            await db.commit()
        response = await client.post(
            "/api/teacher/quizzes/generate",
            json={
                "class_id": str(cid),
                "knowledge_points": [kp_code],
                "count": 10,
                "question_types": {"choice": 10, "blank": 0, "text": 0},
            },
            headers=_auth(token(tid, "teacher")),
        )
        data = response.json()["data"]
        assert data["content"]["insufficient"] is False
        assert len(data["content"]["items"]) == 10
        assert all(item["options"] == {"A": "甲", "B": "乙"} for item in data["content"]["items"])
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(QuestionBank).where(QuestionBank.kp_codes.overlap([kp_code])))
            await db.commit()
