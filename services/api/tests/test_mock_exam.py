"""模拟试卷/专题训练组卷测试

覆盖：
1. full_mock 结构正确性：16 题（choice8/blank3/solution5）150 分 120 分钟、
   模块覆盖 ≥4、难度 7:2:1、落库 Quiz/QuizItem、响应不含答案、多选偏差说明
2. topic：kp_module 必填 40001 / 非法卷型 40001 / 模块不存在 40400 /
   子树抽题 10 题 100 分 45 分钟
3. 质量闸全灭 → 50301（不落空卷子）；部分弃题 → dropped 如实标注
4. 日限：组卷计入日限，超出 42901（且不烧 LLM 调用）
5. history：best/last/attempts 聚合 + 分页
6. detail：归属 40400；未提交不含 expected_answer；已提交带 verdict/score

LLM 全程 mock（按出题 prompt 中的「题型：x」行返回对应合法 JSON）；
run_sandbox mock 掉（与 test_smart_quiz 同手法，防依赖沙箱服务）。
题库优先上线后，本文件经 autouse fixture 强制题库无命中（supply_questions 返回空），
锁定验证 AI 生成组卷路径；题库优先路径覆盖见 test_question_supply.py。
需要 PostgreSQL + Redis 运行中（与 test_student_pipeline 同环境）。
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import exam_router as er
from app.main import app
from app.models.coursework import (
    ErrorRecord,
    MasteryRecord,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import get_db
from app.models.knowledge_point import KnowledgePoint

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

# 沙箱机检恒定通过（blank 答案 sympify 校验走它；与 test_smart_quiz 同手法）
_SANDBOX_OK = AsyncMock(return_value={"exec_status": "pass", "stdout": "True\n", "error": None})


@pytest.fixture(autouse=True)
def _force_empty_bank():
    """题库优先上线后，本文件验证的是"AI 生成组卷"路径（四闸/重试/结构/日限）：
    强制题库无命中（返回空供给），使全量槽位走 LLM 生成，断言与题库库内数据无关、
    不再受 dev 库 question_bank 内容影响。题库优先路径覆盖见 test_question_supply.py。"""
    with patch("app.skills.mock_exam.supply_questions", new=AsyncMock(return_value=[])):
        yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_kps():
    """自清洁：删除本文件测试种下的 TST 前缀 KP 及关联行（mastery/error），
    保证全量跑完 pytest 后 knowledge_points 不新增非 MATH- 前缀行"""
    yield
    async with _test_session_factory() as s:
        kp_ids = select(KnowledgePoint.id).where(KnowledgePoint.code.like("TST%"))
        await s.execute(delete(MasteryRecord).where(MasteryRecord.kp_id.in_(kp_ids)))
        await s.execute(delete(ErrorRecord).where(ErrorRecord.kp_code.like("TST%")))
        await s.execute(delete(KnowledgePoint).where(KnowledgePoint.code.like("TST%")))
        await s.commit()


# ========== 辅助 ==========


async def _login(client) -> tuple[str, str]:
    """注册并登录一个新学生，返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


async def _seed_modules(session, n_modules: int, prefix: str) -> list[str]:
    """造 n_modules 个顶级模块（每模块 2 个叶子知识点，高中学段），返回模块 code 列表。

    code 统一加 TST 测试前缀（TST<prefix>-M<m>-<leaf>），由 autouse fixture 在测试后清除。
    """
    prefix = f"TST{prefix}"
    modules = []
    for m in range(n_modules):
        module = f"{prefix}-M{m}"
        modules.append(module)
        for leaf in (1, 2):
            session.add(
                KnowledgePoint(
                    code=f"{module}-{leaf:03d}", name=f"{module}知识点{leaf}", grade="高一"
                )
            )
    await session.flush()
    return modules


def _payload_for(q_type: str) -> dict:
    """各题型合法出题 JSON（难度字段刻意缺省：难度由组卷槽位决定）"""
    payload = {
        "q_type": q_type,
        "answer_analysis": "[[STEP]] 由定义逐步求解",
        "self_check": {
            "answer_verified": True,
            "computation_double_checked": True,
            "no_ambiguity": True,
            "difficulty_match": True,
            "in_syllabus": True,
            "note": "已代回验证",
        },
    }
    if q_type == "choice":
        payload.update(
            {
                "question_text": "求函数 $f(x)=x^2$ 的导数",
                "options": ["A. $2x$", "B. $x$", "C. $x^2$", "D. $2$"],
                "answer": "A",
            }
        )
    elif q_type == "blank":
        payload.update({"question_text": "计算：$1+1=$ ____", "options": [], "answer": "2"})
    else:
        # 解答题必须含 (1) 小问标注（大题规格软闸）
        payload.update(
            {
                "question_text": "已知 $f(x)=x^2-2x$。(1) 求 $f(x)$ 的最小值；(2) 求 $f(x)$ 的零点。",
                "options": [],
                "answer": "(1) $-1$；(2) $x=0$ 或 $x=2$",
            }
        )
    return payload


def _llm_router(*, fail_q_types: set[str] | None = None, always_raw: str | None = None):
    """按出题 prompt 中的「题型：x」行返回对应合法 JSON 的 mock ModelRouter"""
    router = AsyncMock()

    async def _chat(messages, **kwargs):
        if always_raw is not None:
            return {"content": always_raw}
        prompt = messages[0]["content"]
        m = re.search(r"题型：(\w+)", prompt)
        q_type = m.group(1) if m else "choice"
        if fail_q_types and q_type in fail_q_types:
            return {"content": "这不是 JSON"}
        return {"content": json.dumps(_payload_for(q_type), ensure_ascii=False)}

    router.chat.side_effect = _chat
    return router


async def _gen(client, token, body: dict):
    return await client.post(
        "/api/student/exam/generate",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


# ========== 1. full_mock 结构正确性 ==========


class TestFullMockGenerate:
    async def test_full_mock_structure(self, client):
        token, user_id = await _login(client)
        async with _test_session_factory() as s:
            await _seed_modules(s, 5, f"FM{uuid.uuid4().hex[:4]}")
            await s.commit()

        with (
            patch.object(er, "get_model_router", return_value=_llm_router()),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "full_mock"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]

        # 卷型元数据
        assert data["type"] == "full_mock"
        assert data["duration_minutes"] == 120
        assert data["total_score"] == 150
        assert data["planned"] == 16 and data["dropped"] == 0
        assert "多选" in data["structure_note"]  # 结构偏差如实标注

        # 结构：choice8×5 + blank3×5 + solution5×19
        struct = {s["q_type"]: s for s in data["structure"]}
        assert struct["choice"] == {"q_type": "choice", "count": 8, "score_each": 5}
        assert struct["blank"] == {"q_type": "blank", "count": 3, "score_each": 5}
        assert struct["solution"] == {"q_type": "solution", "count": 5, "score_each": 19}

        # 题目：16 题、编号连续、形状同 practice/start、不含答案
        items = data["items"]
        assert len(items) == 16
        assert [it["item_no"] for it in items] == list(range(1, 17))
        assert [it["q_type"] for it in items] == ["choice"] * 8 + ["blank"] * 3 + ["solution"] * 5
        for it in items:
            assert set(it) == {
                "item_no",
                "q_type",
                "question_text",
                "options",
                "kp_code",
                "difficulty",
                "ai_generated",
                "image",  # m2_013 题目配图快照（参数化渲染后为 SVG data URI）
                "source",  # 阶段3 来源透明：题库真题来源（AI 题为 null）
            }
            assert it["ai_generated"] is True
        # 选择题 options 归一化为 dict
        assert items[0]["options"] == {
            "A": "A. $2x$",
            "B": "B. $x$",
            "C": "C. $x^2$",
            "D": "D. $2$",
        }

        # 模块覆盖 ≥4 个顶级模块
        modules = {re.sub(r"-\d+$", "", it["kp_code"]) for it in items}
        assert len(modules) >= 4

        # 难度 7:2:1（16 题 → easy 11 / medium 3 / hard 2）
        diffs = [it["difficulty"] for it in items]
        assert diffs.count("easy") == 11
        assert diffs.count("medium") == 3
        assert diffs.count("hard") == 2

        # 日限：本次成卷 16 题计入已用
        assert data["daily_cap"] == {"limit": 30, "used": 16}

        # 落库：Quiz(source=exam:full_mock) + 16 个 QuizItem
        async with _test_session_factory() as s:
            quiz = await s.get(Quiz, uuid.UUID(data["exam_id"]))
            assert quiz is not None
            assert quiz.source == "exam:full_mock"
            assert str(quiz.user_id) == user_id
            db_items = (
                (
                    await s.execute(
                        select(QuizItem).where(QuizItem.quiz_id == quiz.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(db_items) == 16
            assert all(i.ai_generated for i in db_items)
            assert all(i.answer for i in db_items)  # 答案落库（判分用），不外泄

    async def test_full_mock_custom_title(self, client):
        token, _ = await _login(client)
        async with _test_session_factory() as s:
            await _seed_modules(s, 4, f"FT{uuid.uuid4().hex[:4]}")
            await s.commit()
        with (
            patch.object(er, "get_model_router", return_value=_llm_router()),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "full_mock", "title": "三月联考模拟"})
        assert resp.json()["data"]["title"] == "三月联考模拟"


# ========== 2. topic 专题训练 ==========


class TestTopicGenerate:
    async def test_topic_requires_kp_module(self, client):
        token, _ = await _login(client)
        resp = await _gen(client, token, {"type": "topic"})
        assert resp.json()["code"] == 40001
        assert "kp_module" in resp.json()["message"]

    async def test_invalid_type(self, client):
        token, _ = await _login(client)
        resp = await _gen(client, token, {"type": "weird"})
        assert resp.json()["code"] == 40001

    async def test_topic_module_not_found(self, client):
        token, _ = await _login(client)
        resp = await _gen(client, token, {"type": "topic", "kp_module": f"NONE-{uuid.uuid4().hex[:6]}"})
        assert resp.json()["code"] == 40400

    async def test_topic_structure(self, client):
        token, user_id = await _login(client)
        module = f"TSTTP{uuid.uuid4().hex[:4]}"  # TST 前缀：autouse fixture 自清洁
        async with _test_session_factory() as s:
            for leaf in (1, 2, 3):
                s.add(
                    KnowledgePoint(
                        code=f"{module}-{leaf:03d}", name=f"专题知识点{leaf}", grade="高二"
                    )
                )
            await s.commit()

        with (
            patch.object(er, "get_model_router", return_value=_llm_router()),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "topic", "kp_module": module})

        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["type"] == "topic"
        assert data["duration_minutes"] == 45
        assert data["total_score"] == 100
        assert data["dropped"] == 0
        assert "structure_note" not in data  # 偏差说明仅 full_mock 有

        struct = {s["q_type"]: s for s in data["structure"]}
        assert struct["choice"]["count"] == 6 and struct["choice"]["score_each"] == 5
        assert struct["blank"]["count"] == 2 and struct["blank"]["score_each"] == 10
        assert struct["solution"]["count"] == 2 and struct["solution"]["score_each"] == 25

        items = data["items"]
        assert len(items) == 10
        # kp 全部来自该模块子树
        assert all(it["kp_code"].startswith(f"{module}-") for it in items)
        # 难度 7:2:1（10 题 → easy 7 / medium 2 / hard 1）
        diffs = [it["difficulty"] for it in items]
        assert diffs.count("easy") == 7
        assert diffs.count("medium") == 2
        assert diffs.count("hard") == 1

        assert data["daily_cap"] == {"limit": 30, "used": 10}

        async with _test_session_factory() as s:
            quiz = await s.get(Quiz, uuid.UUID(data["exam_id"]))
            assert quiz.source == "exam:topic"
            assert str(quiz.user_id) == user_id


# ========== 3. 生成失败：50301 与弃题标注 ==========


class TestGenerationFailure:
    async def test_all_gates_fail_50301(self, client):
        """闸门全灭 → 50301，不落空卷子"""
        token, user_id = await _login(client)
        async with _test_session_factory() as s:
            await _seed_modules(s, 5, f"FL{uuid.uuid4().hex[:4]}")
            await s.commit()

        bad_router = _llm_router(
            always_raw=json.dumps(
                {"q_type": "choice", "question_text": "缺答案的坏题"}, ensure_ascii=False
            )
        )
        with (
            patch.object(er, "get_model_router", return_value=bad_router),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "full_mock"})

        body = resp.json()
        assert body["code"] == 50301
        assert "组卷失败" in body["message"]
        # 每题首次 + 重试 2 次 = 3 次调用，16 题共 48 次
        assert bad_router.chat.await_count == 48

        # 不落任何试卷数据
        async with _test_session_factory() as s:
            rows = (
                (
                    await s.execute(
                        select(Quiz).where(
                            Quiz.user_id == uuid.UUID(user_id),
                            Quiz.source.like("exam:%"),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert rows == []

    async def test_partial_drop_reported(self, client):
        """blank 全灭 → 弃 3 题仍成卷（13/16 ≥ 70%），dropped 与 structure 如实标注"""
        token, _ = await _login(client)
        async with _test_session_factory() as s:
            await _seed_modules(s, 5, f"PD{uuid.uuid4().hex[:4]}")
            await s.commit()

        with (
            patch.object(er, "get_model_router", return_value=_llm_router(fail_q_types={"blank"})),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "full_mock"})

        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["dropped"] == 3
        assert len(data["items"]) == 13
        # 题号重排连续
        assert [it["item_no"] for it in data["items"]] == list(range(1, 14))
        struct = {s["q_type"]: s for s in data["structure"]}
        assert struct["blank"]["count"] == 0
        # total_score 按实际成卷折算：150 - 3×5 = 135
        assert data["total_score"] == 135
        assert data["daily_cap"]["used"] == 13


# ========== 4. 日限 42901 ==========


class TestDailyCap:
    async def test_exam_counts_toward_daily_cap(self, client, monkeypatch):
        """组卷计入日限：首卷 16 题用满额度，第二卷 42901（且不烧 LLM 调用）"""
        monkeypatch.setattr(settings, "student_daily_practice_limit", 16)
        token, _ = await _login(client)
        async with _test_session_factory() as s:
            await _seed_modules(s, 5, f"DC{uuid.uuid4().hex[:4]}")
            await s.commit()

        router = _llm_router()
        with (
            patch.object(er, "get_model_router", return_value=router),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp1 = await _gen(client, token, {"type": "full_mock"})
            assert resp1.json()["code"] == 0
            assert resp1.json()["data"]["daily_cap"] == {"limit": 16, "used": 16}
            assert router.chat.await_count == 27  # 16 生成 + choice8/blank3 各 1 次盲解校验（自愈闸）

            resp2 = await _gen(client, token, {"type": "full_mock"})
            body2 = resp2.json()
            assert body2["code"] == 42901
            assert "上限" in body2["message"]
            # 拒绝发生在生成前：LLM 调用数不变
            assert router.chat.await_count == 27

    async def test_practice_answers_also_count(self, client, monkeypatch):
        """非试卷作答（practice 口径）与组卷合并计题：已答 10 + 计划 16 > 20 → 42901"""
        monkeypatch.setattr(settings, "student_daily_practice_limit", 20)
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            await _seed_modules(s, 5, f"DA{uuid.uuid4().hex[:4]}")
            # 普通题组 + 今日 10 题作答
            quiz = Quiz(user_id=uid, source="ai_generated", title="t", kp_codes=[])
            s.add(quiz)
            await s.flush()
            sub = Submission(
                user_id=uid,
                quiz_id=quiz.id,
                client_submit_id=f"cap-{uuid.uuid4().hex[:8]}",
                status="graded",
            )
            s.add(sub)
            await s.flush()
            for i in range(10):
                s.add(
                    SubmissionItem(
                        submission_id=sub.id, item_no=i + 1, q_type="choice", verdict="correct"
                    )
                )
            await s.commit()

        router = _llm_router()
        with (
            patch.object(er, "get_model_router", return_value=router),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await _gen(client, token, {"type": "full_mock"})
        assert resp.json()["code"] == 42901
        assert router.chat.await_count == 0


# ========== 5. history 聚合 ==========


class TestExamHistory:
    async def test_history_aggregates(self, client):
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            q1 = Quiz(user_id=uid, source="exam:full_mock", title="模拟一", kp_codes=[])
            q2 = Quiz(user_id=uid, source="exam:topic", title="专题一", kp_codes=[])
            s.add_all([q1, q2])
            await s.flush()
            s.add(
                QuizItem(
                    quiz_id=q1.id, item_no=1, q_type="choice", question_text="q", answer="A"
                )
            )
            s.add(
                QuizItem(
                    quiz_id=q1.id, item_no=2, q_type="solution", question_text="q2", answer="a"
                )
            )
            sub_old = Submission(
                user_id=uid,
                quiz_id=q1.id,
                client_submit_id=f"h-{uuid.uuid4().hex[:8]}",
                status="graded",
                total_score=40,
            )
            sub_new = Submission(
                user_id=uid,
                quiz_id=q1.id,
                client_submit_id=f"h-{uuid.uuid4().hex[:8]}",
                status="graded",
                total_score=70,
            )
            s.add_all([sub_old, sub_new])
            await s.flush()
            sub_old.created_at = datetime.now(UTC) - timedelta(hours=1)
            sub_new.created_at = datetime.now(UTC)
            await s.commit()

        resp = await client.get(
            "/api/student/exam/history", headers={"Authorization": f"Bearer {token}"}
        )
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["total"] == 2
        by_title = {i["title"]: i for i in data["items"]}

        e1 = by_title["模拟一"]
        assert e1["type"] == "full_mock"
        assert e1["attempts"] == 2
        assert e1["best_score"] == 70.0
        assert e1["last_score"] == 70.0  # 最近一次提交
        # total_score 按实际题量折算：choice1×5 + solution1×19 = 24
        assert e1["total_score"] == 24
        assert e1["created_at"]

        e2 = by_title["专题一"]
        assert e2["type"] == "topic"
        assert e2["attempts"] == 0
        assert e2["best_score"] is None
        assert e2["last_score"] is None
        assert e2["total_score"] == 0

    async def test_history_pagination_and_isolation(self, client):
        """分页生效；他人试卷不出现在本人列表"""
        token_a, user_id_a = await _login(client)
        token_b, _ = await _login(client)
        uid_a = uuid.UUID(user_id_a)
        async with _test_session_factory() as s:
            for i in range(3):
                s.add(
                    Quiz(
                        user_id=uid_a,
                        source="exam:topic",
                        title=f"A卷{i}",
                        kp_codes=[],
                    )
                )
            await s.commit()

        # 本人分页：3 卷，size=2 → 第 1 页 2 条、第 2 页 1 条
        resp1 = await client.get(
            "/api/student/exam/history?page=1&size=2",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        data1 = resp1.json()["data"]
        assert data1["total"] == 3
        assert len(data1["items"]) == 2
        resp2 = await client.get(
            "/api/student/exam/history?page=2&size=2",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert len(resp2.json()["data"]["items"]) == 1

        # 他人列表为空（隔离）
        resp_b = await client.get(
            "/api/student/exam/history",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.json()["data"]["total"] == 0


# ========== 6. detail 归属与提交后判分 ==========


class TestExamDetail:
    async def _make_exam(self, s, uid, *, with_submission: bool) -> tuple[Quiz, list[QuizItem]]:
        quiz = Quiz(user_id=uid, source="exam:topic", title="专题卷", kp_codes=[])
        s.add(quiz)
        await s.flush()
        items = []
        for no, (qt, ans) in enumerate([("choice", "A"), ("blank", "2")], start=1):
            it = QuizItem(
                quiz_id=quiz.id,
                item_no=no,
                q_type=qt,
                question_text=f"题{no}",
                answer=ans,
                ai_generated=True,
            )
            s.add(it)
            items.append(it)
        await s.flush()
        if with_submission:
            sub = Submission(
                user_id=uid,
                quiz_id=quiz.id,
                client_submit_id=f"d-{uuid.uuid4().hex[:8]}",
                status="graded",
                total_score=10,
            )
            s.add(sub)
            await s.flush()
            s.add(
                SubmissionItem(
                    submission_id=sub.id, item_no=1, q_type="choice", verdict="correct", score=10
                )
            )
            s.add(
                SubmissionItem(
                    submission_id=sub.id, item_no=2, q_type="blank", verdict="wrong", score=0
                )
            )
            await s.flush()
        return quiz, items

    async def test_detail_unsubmitted_hides_answers(self, client):
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            quiz, _ = await self._make_exam(s, uid, with_submission=False)
            await s.commit()
            exam_id = str(quiz.id)

        resp = await client.get(
            f"/api/student/exam/{exam_id}", headers={"Authorization": f"Bearer {token}"}
        )
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["submitted"] is False
        assert data["type"] == "topic"
        assert data["duration_minutes"] == 45
        # total_score = choice1×5 + blank1×10 = 15
        assert data["total_score"] == 15
        for it in data["items"]:
            assert "expected_answer" not in it
            assert "verdict" not in it

    async def test_detail_after_submit_shows_verdict(self, client):
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            quiz, _ = await self._make_exam(s, uid, with_submission=True)
            await s.commit()
            exam_id = str(quiz.id)

        resp = await client.get(
            f"/api/student/exam/{exam_id}", headers={"Authorization": f"Bearer {token}"}
        )
        data = resp.json()["data"]
        assert data["submitted"] is True
        by_no = {it["item_no"]: it for it in data["items"]}
        assert by_no[1]["expected_answer"] == "A"
        assert by_no[1]["verdict"] == "correct"
        assert by_no[1]["score"] == 10.0
        assert by_no[2]["expected_answer"] == "2"
        assert by_no[2]["verdict"] == "wrong"
        assert by_no[2]["score"] == 0.0

    async def test_detail_other_user_40400(self, client):
        """越权一律 40400（不泄露存在性）"""
        token_a, user_id_a = await _login(client)
        token_b, _ = await _login(client)
        async with _test_session_factory() as s:
            quiz, _ = await self._make_exam(s, uuid.UUID(user_id_a), with_submission=False)
            await s.commit()
            exam_id = str(quiz.id)

        resp = await client.get(
            f"/api/student/exam/{exam_id}", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert resp.json()["code"] == 40400

    async def test_detail_not_found(self, client):
        token, _ = await _login(client)
        resp = await client.get(
            f"/api/student/exam/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.json()["code"] == 40400

    async def test_detail_non_exam_quiz_40400(self, client):
        """普通 practice 题组（非试卷）走 exam 详情也 40400"""
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            quiz = Quiz(user_id=uid, source="ai_generated", title="普通题组", kp_codes=[])
            s.add(quiz)
            await s.commit()
            exam_id = str(quiz.id)
        resp = await client.get(
            f"/api/student/exam/{exam_id}", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.json()["code"] == 40400
