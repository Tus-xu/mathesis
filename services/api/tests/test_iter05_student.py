"""迭代05 学生端补齐测试（阶段 1.2）

覆盖（审计清单 B-P1-2/3/4/5、C-P2-2/3/5）：
1. error-records：source_channel 枚举校验、AI 初判异步回填纪律（拿不准保 null 且 ai_judged=true）
2. 间隔复习 1/3/7/15 推进（POST /error-records/{id}/review，SSOT §6.3）
3. practice/submit 三重校验（空 items / 非法 q_type / 无任何归属 → 40001）
4. practice/submit weak_points 联动更新（SSOT §5.12）
5. practice/start mode 枚举校验（API §9.7）
6. mastery/summary radar 按掌握度降序（迭代05 定稿口径）

需要 PostgreSQL + Redis 运行中（与 test_student_pipeline 同环境/同模式）。
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import student_router as sr
from app.main import app
from app.models.coursework import ErrorRecord, MasteryRecord
from app.models.database import get_db
from app.models.knowledge_point import KnowledgePoint
from app.models.user_profile import UserProfile

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


@asynccontextmanager
async def _db():
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_client(client):
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = login_resp.json()["data"]
    return client, data["token"], data["user"]["id"]


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


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


async def _wait_backfill(record_id: str, timeout: float = 6.0):
    """等待异步回填任务落库（ai_judged 置 true 即完成）；返回快照 dict 避免 detached 访问"""
    import asyncio
    for _ in range(int(timeout * 10)):
        async with _test_session_factory() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(record_id))
            if rec and rec.ai_judged:
                return {"ai_judged": rec.ai_judged, "error_type": rec.error_type}
        await asyncio.sleep(0.1)
    raise AssertionError(f"回填超时（record_id={record_id}）")


# ==================== error-records 校验与 AI 初判 ====================


class TestErrorRecordValidation:
    async def test_source_channel_invalid_rejected(self, auth_client):
        """非法收录渠道 → 40001（C-P2-3）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/error-records",
            json={"question_text": "x+1=3 求 x", "source_channel": "hacked_channel"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_error_type_invalid_rejected(self, auth_client):
        """非法错因 → 40001"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/error-records",
            json={"question_text": "x+1=3", "source_channel": "manual_photo", "error_type": "strategy"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_ai_judge_backfill_discipline(self, auth_client):
        """error_type 为空 → ai_judged=false 入库；回填纪律：拿不准保 null 且 ai_judged=true（SSOT §4.9）"""
        client, token, user_id = auth_client
        # patch 须覆盖后台任务执行期（任务在响应返回后异步跑）
        p = patch.object(sr, "_judge_error_type", new=AsyncMock(return_value=None))
        p.start()
        try:
            resp = await client.post(
                "/api/student/error-records",
                json={"question_text": "求 sin30 的值", "source_channel": "manual_photo"},
                headers=_headers(token),
            )
            body = resp.json()
            assert body["code"] == 0
            assert body["data"]["ai_judged"] is False  # 收录时尚未回填
            # 等待后台回填完成，验证纪律：拿不准 → error_type 保持 null，ai_judged=true
            rec = await _wait_backfill(body["data"]["record_id"])
        finally:
            p.stop()
        assert rec["ai_judged"] is True
        assert rec["error_type"] is None  # 拿不准不硬猜，学生可手动改（红线）

    async def test_ai_judge_backfill_success(self, auth_client):
        """回填成功 → error_type 五枚举落库（直调回填函数验证纪律；BackgroundTasks 全链路已由 discipline 用例覆盖）"""
        client, token, _ = auth_client
        p = patch.object(sr, "_judge_error_type", new=AsyncMock(return_value="formula"))
        p.start()
        try:
            resp = await client.post(
                "/api/student/error-records",
                json={"question_text": "辅助角公式化简", "answer_text": "用错了公式", "source_channel": "chat_command"},
                headers=_headers(token),
            )
            record_id = resp.json()["data"]["record_id"]
            # 直调回填（与 BackgroundTasks 调用同一函数）
            await sr._async_error_analysis(record_id)
            rec = await _wait_backfill(record_id)
        finally:
            p.stop()
        assert rec["error_type"] == "formula"
        assert rec["ai_judged"] is True


# ==================== 间隔复习 1/3/7/15（SSOT §6.3） ====================


class TestSpacedReview:
    async def _create_record(self, client, token):
        resp = await client.post(
            "/api/student/error-records",
            json={"question_text": "间隔复习测试题", "source_channel": "manual_photo", "error_type": "calculation"},
            headers=_headers(token),
        )
        return resp.json()["data"]["record_id"]

    async def test_initial_review_one_day(self, auth_client):
        """收录 → next_review_at 首档 1 天"""
        client, token, _ = auth_client
        record_id = await self._create_record(client, token)
        async with _db() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(record_id))
            delta = rec.next_review_at - datetime.now(UTC)
            assert timedelta(hours=23) < delta < timedelta(hours=25)

    async def test_review_progression_1_3_7_15(self, auth_client):
        """复习完成推进：第 1/2/3 次完成后分别 +3/+7/+15 天，第 4 次毕业（next_review_at 置空）"""
        client, token, _ = auth_client
        record_id = await self._create_record(client, token)
        expected_days = [3, 7, 15]
        for i, days in enumerate(expected_days, start=1):
            resp = await client.post(
                f"/api/student/error-records/{record_id}/review",
                headers=_headers(token),
            )
            body = resp.json()
            assert body["code"] == 0, f"第 {i} 次复习失败: {body}"
            assert body["data"]["review_count"] == i
            async with _db() as s:
                rec = await s.get(ErrorRecord, uuid.UUID(record_id))
                delta = rec.next_review_at - datetime.now(UTC)
                assert timedelta(days=days - 0.1) < delta < timedelta(days=days + 0.1), \
                    f"第 {i} 次复习后应推进 {days} 天，实际 {delta}"
        # 第 4 次复习：走完 1/3/7/15 间隔，毕业（不再安排下次复习）
        resp = await client.post(
            f"/api/student/error-records/{record_id}/review",
            headers=_headers(token),
        )
        body = resp.json()
        assert body["code"] == 0, f"第 4 次复习失败: {body}"
        assert body["data"]["review_count"] == 4
        async with _db() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(record_id))
            assert rec.next_review_at is None, "走完 1/3/7/15 后应毕业（next_review_at 置空）"

    async def test_review_foreign_record_40400(self, auth_client):
        """复习他人错题 → 40400（越权不泄露存在性）"""
        client, token, _ = auth_client
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/student/error-records/{fake_id}/review",
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40400


# ==================== practice/submit 三重校验与 weak_points ====================


class TestSubmitValidation:
    async def test_empty_items_40001(self, auth_client):
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={"items": [], "client_submit_id": f"v-{uuid.uuid4().hex[:8]}"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_invalid_q_type_40001(self, auth_client):
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "items": [{"item_no": 1, "q_type": "essay", "answer_text": "x"}],
                "client_submit_id": f"v-{uuid.uuid4().hex[:8]}",
            },
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_no_attribution_40001(self, auth_client):
        """quiz_id/assignment_id 均缺且 items 无 kp_code → 40001（迭代05 归属校验）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
                "client_submit_id": f"v-{uuid.uuid4().hex[:8]}",
            },
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_foreign_quiz_40400(self, client):
        """越权防护（迭代06 审计修复）：他人 quiz_id 提交 → 40400，不触发判分/掌握度更新"""
        from app.models.coursework import Quiz, QuizItem

        # 用户 A 注册（体内注册，避免 auth_client fixture 跨 loop 的 Redis 连接问题）
        phone_a = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone_a})
        resp_a_login = await client.post("/api/auth/login", json={"phone": phone_a, "code": "123456"})
        token_a = resp_a_login.json()["data"]["token"]
        user_a = resp_a_login.json()["data"]["user"]["id"]

        # 用户 B 注册
        phone_b = f"137{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone_b})
        resp_b = await client.post("/api/auth/login", json={"phone": phone_b, "code": "123456"})
        token_b = resp_b.json()["data"]["token"]

        # 用户 A 种一个归属自己的 quiz（显式 commit，_db() 为回滚型）
        async with _test_session_factory() as s:
            quiz = Quiz(user_id=uuid.UUID(user_a), source="manual", title="越权测试题组", kp_codes=[])
            s.add(quiz)
            await s.flush()
            s.add(
                QuizItem(
                    quiz_id=quiz.id, item_no=1, q_type="choice",
                    question_text="1+1=", options={"A": "2", "B": "3", "C": "4", "D": "5"},
                    answer="A", difficulty="easy", ai_generated=False,
                )
            )
            await s.commit()
            foreign_quiz_id = str(quiz.id)

        # 用户 B 提交他人 quiz → 40400
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "quiz_id": foreign_quiz_id,
                "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
                "client_submit_id": f"v-{uuid.uuid4().hex[:8]}",
            },
            headers=_headers(token_b),
        )
        assert resp.json()["code"] == 40400

        # 用户 A 自己提交 → 正常判分（回归：不误伤本人）
        resp_a = await client.post(
            "/api/student/practice/submit",
            json={
                "quiz_id": foreign_quiz_id,
                "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A"}],
                "client_submit_id": f"v-{uuid.uuid4().hex[:8]}",
            },
            headers=_headers(token_a),
        )
        assert resp_a.json()["code"] == 0

    async def test_weak_points_updated_after_submit(self, auth_client):
        """提交判错 → mastery 更新 → weak_points 联动写入 user_profiles（SSOT §5.12，补 M1 只读缺口）"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        kp_code = f"TST_wp_{uuid.uuid4().hex[:8]}"  # TST 前缀：autouse fixture 自清洁
        async with _test_session_factory() as s:
            from app.models.coursework import Quiz, QuizItem
            kp = KnowledgePoint(code=kp_code, name="薄弱点测试")
            s.add(kp)
            await s.flush()
            quiz = Quiz(user_id=uid, source="ai_generated", title="t", kp_codes=[kp_code])
            s.add(quiz)
            await s.flush()
            s.add(QuizItem(quiz_id=quiz.id, item_no=1, q_type="choice",
                           question_text="1+1=?", answer="A", kp_code=kp_code))
            await s.commit()
            quiz_id = quiz.id

        p = patch.object(sr, "_judge_error_type", new=AsyncMock(return_value=None))
        p.start()
        try:
            resp = await client.post(
                "/api/student/practice/submit",
                json={
                    "quiz_id": str(quiz_id),
                    # 答错（标答 A）→ mastery 后验更新 + 错题收录 + weak_points 联动
                    "items": [{"item_no": 1, "q_type": "choice", "answer_text": "C"}],
                    "client_submit_id": f"wp-{uuid.uuid4().hex[:8]}",
                },
                headers=_headers(token),
            )
            body = resp.json()
            # 直调错题回填（验证纪律；BackgroundTasks 触发链路已由 discipline 用例覆盖）
            err_id = None
            async with _db() as s:
                err_rows = (await s.execute(
                    select(ErrorRecord.id).where(
                        ErrorRecord.user_id == uuid.UUID(user_id),
                        ErrorRecord.source_channel == "auto_judge",
                    ).order_by(ErrorRecord.created_at.desc()).limit(1)
                )).scalars().all()
                if err_rows:
                    err_id = str(err_rows[0])
            if err_id:
                await sr._async_error_analysis(err_id)
        finally:
            p.stop()
        assert body["code"] == 0
        assert body["data"]["mastery_updated"] is True
        async with _db() as s:
            profile = (await s.execute(
                select(UserProfile).where(UserProfile.user_id == uid)
            )).scalar_one_or_none()
            assert profile is not None
            assert kp_code in profile.weak_points

    async def test_mastery_updated_false_without_kp(self, auth_client):
        """全 pending_review（无掌握度更新）→ mastery_updated=false（C-P2-5）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                # solution 无 quiz 归属 → quiz_item=None → degraded=no_reference（pending_review，无 kp 更新）
                "items": [{"item_no": 1, "q_type": "solution", "answer_text": "解：x=1", "kp_code": f"nx_{uuid.uuid4().hex[:6]}"}],
                "client_submit_id": f"mu-{uuid.uuid4().hex[:8]}",
            },
            headers=_headers(token),
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["mastery_updated"] is False


# ==================== practice/start mode 校验 ====================


class TestPracticeStartValidation:
    async def test_invalid_mode_40001(self, auth_client):
        """非法 mode → 40001（不再静默按 special 出题，B-P1-4）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/start",
            json={"mode": "hacked", "kp_code": "MATH-G1-FUNC-001"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001


# ==================== mastery radar 排序 ====================


class TestMasteryRadarOrder:
    async def test_radar_sorted_desc(self, auth_client):
        """radar ≤12 且按掌握度降序（C-P2-2 定稿口径）"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            for i, m in enumerate([0.3, 0.9, 0.6, 0.1, 0.8]):
                kp = KnowledgePoint(code=f"TST_radar_{uuid.uuid4().hex[:8]}", name=f"排序{i}")
                s.add(kp)
                await s.flush()
                s.add(MasteryRecord(user_id=uid, kp_id=kp.id, mastery=m))
            await s.commit()

        resp = await client.get("/api/student/mastery/summary", headers=_headers(token))
        radar = resp.json()["data"]["radar"]
        assert len(radar) <= 12
        values = [r["mastery"] for r in radar]
        assert values == sorted(values, reverse=True), f"radar 未按掌握度降序: {values}"
