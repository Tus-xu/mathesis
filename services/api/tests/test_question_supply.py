"""题库优先（question_bank + question_supply + practice/exam 题库优先路径）测试

覆盖：
1. 迁移与模型：question_bank 表结构、quiz_items.source 列、hash 唯一约束
2. supply_questions：kp/题型/难度过滤、难度放宽补缺口、exclude_hashes 去重、章节码↔小节码展开
3. practice/start special：count=12 全题库命中零 LLM 调用；缺口混合供给构成标注；
   count 校验（5~30）；日限只计 AI 题（题库题免费、42901 只在需要 AI 且额度满时触发）
4. exam/generate：topic 题库优先快路径（mock LLM 零调用，bank_count/ai_count 标注）；
   full_mock 混合构成标注
5. import_question_bank：load_items 宽容缺省/去重 + --dry-run 不写库

LLM 全程 mock；run_sandbox mock 掉（与 test_mock_exam 同手法）。
需要 PostgreSQL + Redis 运行中（与 test_student_pipeline 同环境）。
"""

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import exam_router as er
from app.gateway import student_router as sr
from app.main import app
from app.models.coursework import Quiz, QuizItem, Submission, SubmissionItem
from app.models.database import get_db
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import QuestionBank, stem_hash
from app.skills.question_supply import expand_kp_codes, supply_questions
from scripts.import_question_bank import load_items
from scripts.import_question_bank import run as import_run

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

# 沙箱机检恒定通过（与 test_mock_exam 同手法）
_SANDBOX_OK = AsyncMock(return_value={"exec_status": "pass", "stdout": "True\n", "error": None})


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ========== 辅助 ==========


async def _login(client) -> tuple[str, str]:
    """注册并登录一个新学生，返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _bank_row(kp_code: str, q_type: str, difficulty: str, tag: str) -> QuestionBank:
    """造一条题库行（tag 保证 stem/hash 唯一）"""
    options = None
    answer = "2"
    if q_type == "choice":
        options = {"A": "A. $1$", "B": "B. $2$", "C": "C. $3$", "D": "D. $4$"}
        answer = "B"
    stem = f"[{tag}] 题库示例题干 ${q_type}$ $1+1=?$"
    return QuestionBank(
        stem=stem,
        q_type=q_type,
        options=options,
        answer=answer,
        analysis="解析：直接计算得",
        difficulty=difficulty,
        kp_codes=[kp_code],
        source="2023新课标I卷",
        year=2023,
        is_real_exam=True,
        hash=stem_hash(stem),
    )


def _llm_router():
    """按出题 prompt 中的「题型：x」行返回合法 JSON 的 mock ModelRouter（被调用即留痕）"""
    router = AsyncMock()

    async def _chat(messages, **kwargs):
        prompt = messages[0]["content"]
        m = re.search(r"题型：(\w+)", prompt)
        q_type = m.group(1) if m else "choice"
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
        elif q_type == "solution":
            # 解答题必须含 (1) 小问标注（大题规格软闸）
            payload.update(
                {
                    "question_text": "已知 $f(x)=x^2-2x$。(1) 求 $f(x)$ 的最小值；(2) 求 $f(x)$ 的零点。",
                    "options": [],
                    "answer": "(1) $-1$；(2) $x=0$ 或 $x=2$",
                }
            )
        else:
            payload.update({"question_text": "计算：$1+1=$ ____", "options": [], "answer": "2"})
        return {"content": json.dumps(payload, ensure_ascii=False)}

    router.chat.side_effect = _chat
    return router


async def _practice_start(client, token, body: dict):
    return await client.post(
        "/api/student/practice/start", json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


async def _cleanup_bank(*kp_codes: str) -> None:
    """清理端点测试落库的题库行（防测试数据在 dev 库跨次运行累积/混入全库抽题）"""
    async with _test_session_factory() as s:
        await s.execute(
            delete(QuestionBank).where(QuestionBank.kp_codes.overlap(list(kp_codes)))
        )
        await s.commit()


# ========== 1. 迁移与模型 ==========


class TestBankModelAndMigration:
    async def test_question_bank_table_and_quiz_item_source_exist(self):
        """m2_008 迁移：question_bank 全字段 + quiz_items.source 列"""
        async with _test_session_factory() as s:
            cols = dict(
                (
                    await s.execute(
                        text(
                            "select column_name, data_type from information_schema.columns "
                            "where table_name='question_bank'"
                        )
                    )
                ).all()
            )
            for col in (
                "id", "stem", "q_type", "options", "answer", "analysis", "difficulty",
                "kp_codes", "source", "year", "is_real_exam", "embedding", "hash",
                "created_at", "updated_at", "deleted_at",
            ):
                assert col in cols, f"question_bank 缺列 {col}"
            src = (
                await s.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_name='quiz_items' and column_name='source'"
                    )
                )
            ).scalar()
            assert src == "source"

    async def test_model_insert_and_hash_unique(self):
        """模型落库可读回；同 hash 唯一约束生效"""
        async with _test_session_factory() as s:
            kp_code = f"qb_{uuid.uuid4().hex[:8]}"
            row = _bank_row(kp_code, "choice", "easy", f"m{uuid.uuid4().hex[:6]}")
            s.add(row)
            await s.flush()
            got = await s.get(QuestionBank, row.id)
            assert got.stem == row.stem
            assert got.is_real_exam is True
            assert got.source == "2023新课标I卷"
            assert got.kp_codes == [kp_code]

            dup = _bank_row(kp_code, "choice", "easy", "x")
            dup.hash = row.hash  # 同 hash 撞唯一约束
            s.add(dup)
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    async def test_stem_hash_normalizes_whitespace(self):
        """同题异空白 → 同 hash（导入去重依据）"""
        assert stem_hash("已知 $a=1$ 求 b") == stem_hash("已知 $a=1$\n  求 b")
        assert stem_hash("题目甲") != stem_hash("题目乙")


# ========== 2. supply_questions 过滤/放宽/去重/展开 ==========


class TestSupply:
    async def test_filter_relax_dedup_expand(self):
        async with _test_session_factory() as s:
            try:
                prefix = f"qs{uuid.uuid4().hex[:6]}"
                parent = KnowledgePoint(code=f"{prefix}-001", name="章节", grade="高一")
                s.add(parent)
                await s.flush()
                child = KnowledgePoint(
                    code=f"{prefix}-101", name="小节", grade="高一", parent_id=parent.id
                )
                other = KnowledgePoint(code=f"{prefix}-999", name="无关", grade="高一")
                s.add_all([child, other])
                await s.flush()

                tag = uuid.uuid4().hex[:6]
                rows = [
                    _bank_row(child.code, "choice", "easy", f"{tag}-e{i}") for i in range(3)
                ]
                rows.append(_bank_row(child.code, "choice", "hard", f"{tag}-h0"))
                rows.append(_bank_row(child.code, "blank", "easy", f"{tag}-b0"))
                rows.append(_bank_row(other.code, "choice", "easy", f"{tag}-x0"))
                s.add_all(rows)
                await s.flush()

                # 题型+难度过滤：3 道 easy choice（count 恰好供满，不触发放宽）
                got = await supply_questions(
                    s, kp_codes=[child.code], q_type="choice", difficulty="easy", count=3
                )
                assert len(got) == 3
                assert all(r.difficulty == "easy" and r.q_type == "choice" for r in got)

                # 指定难度供不满 → 放宽难度补缺口：3 easy + 1 hard（题型/kp 不放宽）
                got = await supply_questions(
                    s, kp_codes=[child.code], q_type="choice", difficulty="easy", count=5
                )
                assert len(got) == 4
                assert {r.difficulty for r in got} == {"easy", "hard"}
                assert all(r.q_type == "choice" for r in got)

                # 难度不足放宽：medium 无题 → 放宽后补满 4 道 choice（3 easy + 1 hard）
                got = await supply_questions(
                    s, kp_codes=[child.code], q_type="choice", difficulty="medium", count=5
                )
                assert len(got) == 4
                assert {r.difficulty for r in got} == {"easy", "hard"}

                # kp 外行不命中
                assert all(r.kp_codes == [child.code] for r in got)

                # 章节码展开命中子节标注的题
                got = await supply_questions(s, kp_codes=[parent.code], q_type="choice", count=10)
                assert len(got) == 4
                # 子节码回溯命中父级查询；反向亦然（expand 双向）
                expanded = await expand_kp_codes(s, [child.code])
                assert parent.code in expanded and child.code in expanded

                # exclude_hashes 去重
                excluded = {rows[0].hash, rows[1].hash}
                got = await supply_questions(
                    s, kp_codes=[child.code], q_type="choice", count=10, exclude_hashes=excluded
                )
                assert len(got) == 2
                assert all(r.hash not in excluded for r in got)

                # 空供给不报错
                assert await supply_questions(s, kp_codes=["no-such-kp"], count=3) == []
                assert await supply_questions(s, kp_codes=[child.code], count=0) == []
            finally:
                await s.rollback()

    async def test_publishable_supply_uses_stable_sql_limit_without_random_sort(self):
        """Supply SQL limits eligible rows directly; it never uses random() or Python over-fetch."""
        seen_sql: list[str] = []

        def capture_sql(_conn, _cursor, statement, _params, _context, _executemany):
            if "question_bank" in statement and "SELECT" in statement.upper():
                seen_sql.append(statement)

        event.listen(_test_engine.sync_engine, "before_cursor_execute", capture_sql)
        try:
            async with _test_session_factory() as s:
                await supply_questions(
                    s, kp_codes=["no-such-kp"], q_type="choice", count=7,
                    publishable_only=True, relax_difficulty=False,
                )
        finally:
            event.remove(_test_engine.sync_engine, "before_cursor_execute", capture_sql)
        supply_sql = [statement.lower() for statement in seen_sql if "from question_bank" in statement.lower()]
        assert supply_sql
        assert all("random(" not in statement for statement in supply_sql)
        assert any("order by question_bank.hash asc" in statement and "limit" in statement for statement in supply_sql)

    async def test_selection_seed_rotates_hash_pivot_with_bounded_indexable_segments(self):
        """Different deterministic seeds choose different cyclic windows without random sorting."""
        async with _test_session_factory() as s:
            kp_code = f"pivot-{uuid.uuid4().hex[:8]}"
            rows = [_bank_row(kp_code, "choice", "easy", f"pivot-{index}-{uuid.uuid4().hex[:6]}") for index in range(8)]
            s.add_all(rows)
            await s.commit()
            hashes = sorted(row.hash for row in rows)
        try:
            def seed_in_range(lower: str, upper: str | None) -> str:
                for index in range(10_000):
                    candidate = f"rotation-{index}"
                    pivot = hashlib.sha256(candidate.encode()).hexdigest()
                    if pivot >= lower and (upper is None or pivot < upper):
                        return candidate
                raise AssertionError("test could not find a hash pivot")

            low_seed = seed_in_range("0", hashes[0])
            middle_seed = seed_in_range(hashes[3], hashes[4])
            wrap_seed = seed_in_range(hashes[-1], None)
            seen_sql: list[str] = []

            def capture_sql(_conn, _cursor, statement, _params, _context, _executemany):
                if "question_bank" in statement and "SELECT" in statement.upper():
                    seen_sql.append(statement.lower())

            event.listen(_test_engine.sync_engine, "before_cursor_execute", capture_sql)
            try:
                async with _test_session_factory() as s:
                    low = await supply_questions(s, kp_codes=[kp_code], q_type="choice", count=2, selection_seed=low_seed, relax_difficulty=False)
                    middle = await supply_questions(s, kp_codes=[kp_code], q_type="choice", count=2, selection_seed=middle_seed, relax_difficulty=False)
                    await supply_questions(s, kp_codes=[kp_code], q_type="choice", count=2, selection_seed=wrap_seed, relax_difficulty=False)
            finally:
                event.remove(_test_engine.sync_engine, "before_cursor_execute", capture_sql)
            assert {row.hash for row in low} != {row.hash for row in middle}
            supply_sql = [statement for statement in seen_sql if "from question_bank" in statement]
            assert all("random(" not in statement for statement in supply_sql)
            assert any("question_bank.hash >=" in statement and "limit" in statement for statement in supply_sql)
            assert any("question_bank.hash <" in statement and "limit" in statement for statement in supply_sql)
        finally:
            await _cleanup_bank(kp_code)


# ========== 3. practice/start 题库优先 ==========


class TestPracticeBankFirst:
    async def test_special_count_12_all_bank_zero_llm(self, client):
        """count=12 全题库命中：零 LLM 调用，items 带 source，ai_generated=False"""
        token, _ = await _login(client)
        kp_code = f"pb{uuid.uuid4().hex[:8]}"
        async with _test_session_factory() as s:
            s.add(KnowledgePoint(code=kp_code, name="题库专练点", grade="高一"))
            tag = uuid.uuid4().hex[:6]
            specs = (
                [("choice", "easy")] * 6 + [("blank", "medium")] * 3 + [("solution", "hard")] * 3
            )
            for i, (qt, diff) in enumerate(specs):
                s.add(_bank_row(kp_code, qt, diff, f"{tag}-{i}"))
            await s.commit()

        try:
            router = _llm_router()
            with patch.object(sr, "get_model_router", return_value=router):
                resp = await _practice_start(
                    client, token, {"mode": "special", "kp_code": kp_code, "count": 12}
                )
            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert len(data["items"]) == 12
            assert data["bank_count"] == 12
            assert data["ai_count"] == 0
            assert router.chat.await_count == 0  # 全题库命中，零 LLM 调用
            for it in data["items"]:
                assert it["ai_generated"] is False
                assert it["source"] == "2023新课标I卷"
                assert it["kp_code"] == kp_code
            # 日限口径如实标注：只计 AI 题
            assert data["daily_cap"]["scope"] == "ai_only"
            assert data["daily_cap"]["used"] == 0

            # 落库 Quiz.source=bank（纯题库构成如实标注）
            async with _test_session_factory() as s:
                quiz = await s.get(Quiz, uuid.UUID(data["quiz_id"]))
                assert quiz.source == "bank"
        finally:
            await _cleanup_bank(kp_code)

    async def test_special_mixed_supply_composition(self, client):
        """4 题库 + 2 AI 缺口：构成标注 + 逐题 source/ai_generated 标记（AI 题干唯一，符合阶段 1.1 去重护栏）"""
        token, _ = await _login(client)
        kp_code = f"pb{uuid.uuid4().hex[:8]}"
        async with _test_session_factory() as s:
            s.add(KnowledgePoint(code=kp_code, name="混合供给点", grade="高一"))
            tag = uuid.uuid4().hex[:6]
            for i in range(4):
                s.add(_bank_row(kp_code, "choice", "easy", f"{tag}-{i}"))
            await s.commit()

        try:
            # AI 缺口 2 道：mock 按调用序号返回唯一题干（x^2、x^3），避免与题库/彼此重复
            router = AsyncMock()
            _seq = {"n": 0}

            async def _chat(messages, **kwargs):
                prompt = messages[0]["content"]
                m = re.search(r"题型：(\w+)", prompt)
                q_type = m.group(1) if m else "choice"
                _seq["n"] += 1
                payload = {
                    "q_type": q_type,
                    "question_text": f"求函数 $f(x)=x^{{{2 + _seq['n']}}}$ 的导数",
                    "options": ["A. $2x$", "B. $x$", "C. $x^2$", "D. $2$"],
                    "answer": "A",
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
                return {"content": json.dumps(payload, ensure_ascii=False)}

            router.chat.side_effect = _chat
            with (
                patch.object(sr, "get_model_router", return_value=router),
                patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
            ):
                resp = await _practice_start(
                    client, token, {"mode": "special", "kp_code": kp_code, "count": 6}
                )
            body = resp.json()
            assert body["code"] == 0, body
            data = body["data"]
            assert len(data["items"]) == 6
            assert data["bank_count"] == 4
            assert data["ai_count"] == 2
            assert router.chat.await_count == 4  # 补缺口 2 题 + 每题 1 次答案键盲解校验（自愈闸）
            bank_items = [it for it in data["items"] if not it["ai_generated"]]
            ai_items = [it for it in data["items"] if it["ai_generated"]]
            assert len(bank_items) == 4 and len(ai_items) == 2
            assert all(it["source"] == "2023新课标I卷" for it in bank_items)
            assert all(it["source"] is None for it in ai_items)
            # 去重护栏：6 道题题干必须全部唯一
            stems = {"".join(it["question_text"].split()).lower() for it in data["items"]}
            assert len(stems) == 6

            async with _test_session_factory() as s:
                quiz = await s.get(Quiz, uuid.UUID(data["quiz_id"]))
                assert quiz.source == "mixed"
        finally:
            await _cleanup_bank(kp_code)

    async def test_special_count_validation(self, client):
        """count 超出 5~30 → 40001"""
        token, _ = await _login(client)
        kp_code = f"pb{uuid.uuid4().hex[:8]}"
        async with _test_session_factory() as s:
            s.add(KnowledgePoint(code=kp_code, name="校验点", grade="高一"))
            await s.commit()
        for bad in (2, 31):
            resp = await _practice_start(
                client, token, {"mode": "special", "kp_code": kp_code, "count": bad}
            )
            assert resp.json()["code"] == 40001

    async def test_daily_cap_counts_ai_only(self, client, monkeypatch):
        """日限只计 AI 题：今日已答 3 题库题 + 2 AI 题 → used=2；
        额度满（limit=2）时纯题库专练仍放行（0 LLM），需 AI 补缺口才 42901"""
        token, user_id = await _login(client)
        uid = uuid.UUID(user_id)
        kp_bank = f"pb{uuid.uuid4().hex[:8]}"
        kp_empty = f"pb{uuid.uuid4().hex[:8]}"
        async with _test_session_factory() as s:
            s.add(KnowledgePoint(code=kp_bank, name="题库点", grade="高一"))
            s.add(KnowledgePoint(code=kp_empty, name="空点", grade="高一"))
            tag = uuid.uuid4().hex[:6]
            for i in range(5):
                s.add(_bank_row(kp_bank, "choice", "easy", f"{tag}-{i}"))
            # 今日已答：3 题库题（ai_generated=False）+ 2 AI 题
            quiz_bank = Quiz(user_id=uid, source="bank", title="题库组", kp_codes=[kp_bank])
            quiz_ai = Quiz(user_id=uid, source="ai_generated", title="AI组", kp_codes=[kp_bank])
            s.add_all([quiz_bank, quiz_ai])
            await s.flush()
            for i in range(3):
                s.add(QuizItem(
                    quiz_id=quiz_bank.id, item_no=i + 1, q_type="choice",
                    question_text=f"题库题{i}", answer="B", kp_code=kp_bank,
                    ai_generated=False, source="2023新课标I卷",
                ))
            for i in range(2):
                s.add(QuizItem(
                    quiz_id=quiz_ai.id, item_no=i + 1, q_type="choice",
                    question_text=f"AI题{i}", answer="A", kp_code=kp_bank, ai_generated=True,
                ))
            await s.flush()
            for quiz, n in ((quiz_bank, 3), (quiz_ai, 2)):
                sub = Submission(
                    user_id=uid, quiz_id=quiz.id,
                    client_submit_id=f"cap-{uuid.uuid4().hex[:8]}", status="graded",
                )
                s.add(sub)
                await s.flush()
                for i in range(n):
                    s.add(SubmissionItem(
                        submission_id=sub.id, item_no=i + 1, q_type="choice", verdict="correct",
                    ))
            await s.commit()

        try:
            # used 只计 AI 题：3 题库题作答不计 → used=2
            router = _llm_router()
            with patch.object(sr, "get_model_router", return_value=router):
                resp = await _practice_start(
                    client, token, {"mode": "special", "kp_code": kp_bank, "count": 5}
                )
            body = resp.json()
            assert body["code"] == 0
            assert body["data"]["daily_cap"]["used"] == 2
            assert body["data"]["bank_count"] == 5
            assert router.chat.await_count == 0

            # 额度压到 2（已用 2）：纯题库专练仍放行；需 AI 补缺口的专练 42901 且零 LLM 调用
            monkeypatch.setattr(settings, "student_daily_practice_limit", 2)
            with patch.object(sr, "get_model_router", return_value=router):
                resp = await _practice_start(
                    client, token, {"mode": "special", "kp_code": kp_bank, "count": 5}
                )
                assert resp.json()["code"] == 0  # 题库题不占额度
                resp = await _practice_start(
                    client, token, {"mode": "special", "kp_code": kp_empty, "count": 5}
                )
                body = resp.json()
                assert body["code"] == 42901
                assert "AI" in body["message"]
            assert router.chat.await_count == 0
        finally:
            await _cleanup_bank(kp_bank)


# ========== 4. exam/generate 题库优先 ==========


class TestExamBankFirst:
    async def _seed_topic_bank(self, s, module: str) -> list[str]:
        """造专题模块（2 叶子）+ 每叶子足量题库行（choice7/blank3/solution3），返回叶子码"""
        leaves = []
        tag = uuid.uuid4().hex[:6]
        for leaf in (1, 2):
            code = f"{module}-{leaf:03d}"
            leaves.append(code)
            s.add(KnowledgePoint(code=code, name=f"{module}知识点{leaf}", grade="高二"))
            specs = [("choice", 7), ("blank", 3), ("solution", 3)]
            for qt, n in specs:
                for i in range(n):
                    diff = ("easy", "medium", "hard")[i % 3]
                    s.add(_bank_row(code, qt, diff, f"{tag}-{leaf}-{qt}{i}"))
        await s.flush()
        return leaves

    async def test_topic_exam_all_bank_zero_llm(self, client):
        """topic 题库优先快路径：10 题全真题，mock LLM 零调用，构成如实标注"""
        token, _ = await _login(client)
        module = f"BK{uuid.uuid4().hex[:6]}"
        async with _test_session_factory() as s:
            leaves = await self._seed_topic_bank(s, module)
            await s.commit()

        try:
            router = _llm_router()
            with (
                patch.object(er, "get_model_router", return_value=router),
                patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
            ):
                t0 = datetime.now(UTC)
                resp = await client.post(
                    "/api/student/exam/generate",
                    json={"type": "topic", "kp_module": module},
                    headers={"Authorization": f"Bearer {token}"},
                )
                elapsed = (datetime.now(UTC) - t0).total_seconds()

            body = resp.json()
            assert body["code"] == 0
            data = body["data"]
            assert data["bank_count"] == 10
            assert data["ai_count"] == 0
            assert data["total_score"] == 100
            assert router.chat.await_count == 0  # 题库充足，零 LLM 调用
            assert elapsed < 5  # 快路径：整卷秒级
            struct = {s_["q_type"]: s_ for s_ in data["structure"]}
            assert struct["choice"]["count"] == 6
            assert struct["blank"]["count"] == 2
            assert struct["solution"]["count"] == 2
            # 题号连续、题型组序保持、无重复题（卷内 hash 去重）
            assert [it["item_no"] for it in data["items"]] == list(range(1, 11))
            assert [it["q_type"] for it in data["items"]] == ["choice"] * 6 + ["blank"] * 2 + ["solution"] * 2
            stems = [it["question_text"] for it in data["items"]]
            assert len(set(stems)) == 10
            assert all(it["ai_generated"] is False for it in data["items"])
            # 纯题库成卷不占 AI 日限
            assert data["daily_cap"]["used"] == 0

            async with _test_session_factory() as s:
                items = (
                    (
                        await s.execute(
                            select(QuizItem).where(
                                QuizItem.quiz_id == uuid.UUID(data["exam_id"])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(items) == 10
                assert all(not i.ai_generated for i in items)
                assert all(i.source == "2023新课标I卷" for i in items)
                assert all(i.answer for i in items)  # 答案落库（判分用）
        finally:
            await _cleanup_bank(*leaves)

    async def test_full_mock_mixed_composition_marks(self, client):
        """full_mock：题库+AI 合并成卷，bank_count/ai_count 构成标注自洽"""
        token, _ = await _login(client)
        async with _test_session_factory() as s:
            # 5 个无题库模块，保证 LLM 补缺口路径存在
            for m in range(5):
                for leaf in (1, 2):
                    s.add(
                        KnowledgePoint(
                            code=f"MX{uuid.uuid4().hex[:4]}-M{m}-{leaf:03d}",
                            name=f"混合模块{m}知识点{leaf}",
                            grade="高一",
                        )
                    )
            await s.commit()

        router = _llm_router()
        with (
            patch.object(er, "get_model_router", return_value=router),
            patch("app.skills.smart_quiz.main.run_sandbox", new=_SANDBOX_OK, create=True),
        ):
            resp = await client.post(
                "/api/student/exam/generate",
                json={"type": "full_mock"},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert len(data["items"]) == 16
        # 构成标注自洽：题库 + AI = 成卷题数
        assert data["bank_count"] + data["ai_count"] == 16
        assert data["bank_count"] >= 0
        # AI 题数 = 生成调用；choice/blank 每题另有 1 次答案键盲解校验（自愈闸，solution 不盲解）
        n_verify = sum(
            1 for it in data["items"]
            if it["ai_generated"] and it["q_type"] in ("choice", "blank")
        )
        assert router.chat.await_count == data["ai_count"] + n_verify
        # 日限只计 AI 题
        assert data["daily_cap"]["used"] == data["ai_count"]


# ========== 5. import 脚本 dry-run ==========


class TestImportScript:
    async def test_load_items_and_dry_run(self, tmp_path):
        """宽容缺省 + 文件内去重 + 非法行报告；--dry-run 不写库"""
        line_choice = {
            "stem": "函数 $f(x)=x^2$ 的导数是",
            "options": ["A. $2x$", "B. $x$", "C. $x^2$", "D. $2$"],  # list 形式自动归一化
            "answer": "A",
            "kp_codes": ["MATH-G2-DERIV-102"],
            "source": "2022全国甲卷",
            "year": 2022,
            "is_real_exam": True,
        }
        line_blank = {"stem": "计算：$1+1=$ ____", "answer": "2"}  # 缺省 q_type→blank/medium
        line_bad = {"stem": "缺答案的题"}  # answer 缺失 → 跳过
        p = tmp_path / "bank.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(line_choice, ensure_ascii=False),
                    json.dumps(line_blank, ensure_ascii=False),
                    json.dumps(line_bad, ensure_ascii=False),
                    json.dumps(line_choice, ensure_ascii=False),  # 文件内重复 → 去重
                ]
            ),
            encoding="utf-8",
        )

        report = load_items(p)
        assert report["total"] == 4
        assert len(report["items"]) == 2
        assert report["dup_in_file"] == 1
        assert len(report["errors"]) == 1 and "answer" in report["errors"][0]
        choice_item = next(i for i in report["items"] if i["q_type"] == "choice")
        assert choice_item["options"]["A"] == "A. $2x$"  # list → dict 归一化
        assert choice_item["difficulty"] == "medium"  # 缺省
        assert choice_item["year"] == 2022 and choice_item["is_real_exam"] is True
        blank_item = next(i for i in report["items"] if i["q_type"] == "blank")
        assert blank_item["options"] is None

        # dry-run：不写库
        async with _test_session_factory() as s:
            before = (await s.execute(select(QuestionBank.hash).where(
                QuestionBank.hash.in_([i["hash"] for i in report["items"]])
            ))).scalars().all()
        rc = await import_run(p, dry_run=True)
        assert rc == 0
        async with _test_session_factory() as s:
            after = (await s.execute(select(QuestionBank.hash).where(
                QuestionBank.hash.in_([i["hash"] for i in report["items"]])
            ))).scalars().all()
        assert set(after) == set(before)  # 库内无新增
