"""学生端联动增强测试（M2 交付）

覆盖：
1. 错题列表 kp_name（KP 命中取 name / 孤儿码 null / custom→综合练习）+ answer_text/source_channel
2. PATCH /error-records/{id}（改错因 ai_judged 置 False + events 埋点；非法枚举 40001；越权 40400）
3. DELETE /error-records/{id}（软删；越权 40400）
4. 复习计划 review-plan（今日到期 / 未来 15 天聚合 / total_active）
5. 复习推进 result=forgotten 重置 / remembered 走完 15 天毕业
6. warnings 规则引擎（空数据空列表；kp_streak_fail；activity_drop；hint_dependency_up；score_drop）
7. mastery/trend 快照真实化（BKT 写路径 upsert；无快照 ADR-039 单点兜底）
8. daily-plan 真实化（daily_question / week_goal / today_tasks ≤5）
9. 知识节点卡 suggested_actions 分档 + prerequisite_hint 前置联动
10. Bug 修复：非法 assignment_id 40001；自动收录题干兜底 / custom 不落库

需要 PostgreSQL + Redis 运行中（与 test_student_pipeline 同环境同模式）。
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import student_router as sr
from app.main import app
from app.models.conversation import Conversation
from app.models.coursework import (
    DailyQuestion,
    ErrorRecord,
    MasteryRecord,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import get_db
from app.models.event import Event
from app.models.knowledge_point import KnowledgePoint
from app.models.mastery_snapshot import MasterySnapshot
from app.models.tutor_session import TutorSession
from app.models.user import User

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
    """函数级 DB 会话：直调内部函数用，结束 rollback 保持库干净"""
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


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_test_kps():
    """自清洁：删除本文件测试种下的 TST 前缀 KP 及关联行（mastery/error/snapshot），
    以及 e2e_ 前缀错题（本文件 167 行直接以 e2e_{uuid} 造错题，无 KP 行）。
    保证全量跑完 pytest 后 knowledge_points 不新增非 MATH- 前缀行、错题本无残留。"""
    yield
    async with _test_session_factory() as s:
        kp_ids = select(KnowledgePoint.id).where(KnowledgePoint.code.like("TST%"))
        await s.execute(delete(MasteryRecord).where(MasteryRecord.kp_id.in_(kp_ids)))
        await s.execute(delete(ErrorRecord).where(ErrorRecord.kp_code.like("TST%")))
        await s.execute(delete(ErrorRecord).where(ErrorRecord.kp_code.like("e2e_")))
        await s.execute(delete(MasterySnapshot).where(MasterySnapshot.kp_code.like("TST%")))
        await s.execute(delete(KnowledgePoint).where(KnowledgePoint.code.like("TST%")))
        await s.commit()


# ========== 辅助 ==========


async def _auth(client):
    """注册并登录一个随机手机号用户，返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_client(client):
    token, user_id = await _auth(client)
    return client, token, user_id


@pytest_asyncio.fixture
async def other_auth(client):
    """第二个登录用户（越权用例；fixture 与测试函数不同事件循环，不能在测试体内再调 auth 端点）"""
    token, user_id = await _auth(client)
    return token, user_id


async def _make_kp(session, code: str | None = None, name: str | None = None, parent_id=None) -> KnowledgePoint:
    code = code or f"TST_lk_{uuid.uuid4().hex[:10]}"  # TST 前缀：autouse fixture 自清洁
    kp = KnowledgePoint(code=code, name=name or f"联动知识点{code}", parent_id=parent_id)
    session.add(kp)
    await session.flush()
    return kp


async def _create_record(client, token, **overrides) -> str:
    """走 API 收录一道错题（显式 error_type 避免触发 AI 异步回填），返回 record_id"""
    # 题干默认唯一（任务4 去重语义：同题只保留一条活动记录，测试按"每次收录=新题"约定）
    body = {
        "question_text": f"联动测试错题 {uuid.uuid4().hex[:8]}",
        "source_channel": "manual_photo",
        "error_type": "concept",
    }
    body.update(overrides)
    resp = await client.post("/api/student/error-records", json=body, headers=_headers(token))
    assert resp.json()["code"] == 0, resp.json()
    return resp.json()["data"]["record_id"]


def _week_start() -> datetime:
    monday = date.today() - timedelta(days=date.today().weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=UTC)


# ========== 1. 错题列表 kp_name / answer_text / source_channel ==========


class TestErrorListKpName:
    async def test_kp_name_mapping(self, auth_client):
        """KP 命中取 name；孤儿码 null；custom→综合练习；补 answer_text(≤100) 与 source_channel"""
        client, token, user_id = auth_client
        async with _test_session_factory() as s:
            kp = await _make_kp(s)
            await s.commit()
            kp_code, kp_name = kp.code, kp.name

        r1 = await _create_record(client, token, kp_code=kp_code, answer_text="答" * 150)
        r2 = await _create_record(client, token, kp_code=f"e2e_{uuid.uuid4().hex[:8]}")
        r3 = await _create_record(client, token, kp_code="custom")
        r4 = await _create_record(client, token)

        resp = await client.get("/api/student/error-records", headers=_headers(token))
        body = resp.json()
        assert body["code"] == 0
        items = {i["record_id"]: i for i in body["data"]["items"]}
        assert body["data"]["total"] == 4

        assert items[r1]["kp_name"] == kp_name
        assert len(items[r1]["answer_text"]) == 100  # 截 100 字
        assert items[r1]["source_channel"] == "manual_photo"
        assert items[r2]["kp_name"] is None  # 孤儿码 → null 由前端兜底
        assert items[r3]["kp_name"] == "综合练习"  # custom 显示名映射
        assert items[r4]["kp_code"] is None and items[r4]["kp_name"] is None


# ========== 2. PATCH 错题（改错因红线 + 备注） ==========


class TestErrorPatch:
    async def test_patch_error_type_and_note(self, auth_client):
        """手动改错因 → ai_judged 置 False + corrected_by_user + events 埋点 error_type_corrected"""
        client, token, user_id = auth_client
        record_id = await _create_record(client, token, error_type="concept")

        resp = await client.patch(
            f"/api/student/error-records/{record_id}",
            json={"error_type": "logic", "note": "其实是思路卡壳"},
            headers=_headers(token),
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["error_type"] == "logic"
        assert body["data"]["ai_judged"] is False
        assert body["data"]["corrected_by_user"] is True
        assert body["data"]["note"] == "其实是思路卡壳"

        async with _test_session_factory() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(record_id))
            assert rec.error_type == "logic"
            assert rec.ai_judged is False
            ev = (
                await s.execute(
                    select(Event).where(
                        Event.user_id == uuid.UUID(user_id),
                        Event.event == "error_type_corrected",
                    )
                )
            ).scalars().first()
            assert ev is not None
            assert ev.props["record_id"] == record_id
            assert ev.props["old_error_type"] == "concept"
            assert ev.props["new_error_type"] == "logic"

    async def test_patch_invalid_error_type_40001(self, auth_client):
        client, token, _ = auth_client
        record_id = await _create_record(client, token)
        resp = await client.patch(
            f"/api/student/error-records/{record_id}",
            json={"error_type": "strategy"},  # 非五枚举
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_patch_empty_body_40001(self, auth_client):
        client, token, _ = auth_client
        record_id = await _create_record(client, token)
        resp = await client.patch(
            f"/api/student/error-records/{record_id}", json={}, headers=_headers(token)
        )
        assert resp.json()["code"] == 40001

    async def test_patch_foreign_record_40400(self, client, auth_client, other_auth):
        _, token, _ = auth_client
        record_id = await _create_record(client, token)
        other_token, _ = other_auth
        resp = await client.patch(
            f"/api/student/error-records/{record_id}",
            json={"error_type": "logic"},
            headers=_headers(other_token),
        )
        assert resp.json()["code"] == 40400  # 越权不泄露存在性

    async def test_patch_missing_record_40400(self, auth_client):
        client, token, _ = auth_client
        resp = await client.patch(
            f"/api/student/error-records/{uuid.uuid4()}",
            json={"error_type": "logic"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40400


# ========== 3. DELETE 错题（软删） ==========


class TestErrorDelete:
    async def test_delete_soft(self, auth_client):
        """软删后列表不再返回；重复删 → 40400"""
        client, token, _ = auth_client
        record_id = await _create_record(client, token)

        resp = await client.delete(
            f"/api/student/error-records/{record_id}", headers=_headers(token)
        )
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["deleted"] is True

        list_resp = await client.get("/api/student/error-records", headers=_headers(token))
        assert all(i["record_id"] != record_id for i in list_resp.json()["data"]["items"])

        again = await client.delete(
            f"/api/student/error-records/{record_id}", headers=_headers(token)
        )
        assert again.json()["code"] == 40400

    async def test_delete_foreign_40400(self, client, auth_client, other_auth):
        _, token, _ = auth_client
        record_id = await _create_record(client, token)
        other_token, _ = other_auth
        resp = await client.delete(
            f"/api/student/error-records/{record_id}", headers=_headers(other_token)
        )
        assert resp.json()["code"] == 40400


# ========== 4. 复习计划 ==========


class TestReviewPlan:
    async def test_plan_due_and_upcoming(self, auth_client):
        """到期错题进 due_items（含 kp_name）；未来排期按日聚合；total_active 不含已毕业"""
        client, token, _ = auth_client
        async with _test_session_factory() as s:
            kp = await _make_kp(s)
            await s.commit()
            kp_code, kp_name = kp.code, kp.name

        due_id = await _create_record(client, token, kp_code=kp_code)
        future_id = await _create_record(client, token)  # next_review_at = +1 天

        # 把 due 错题的复习时间拨到过去
        async with _test_session_factory() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(due_id))
            rec.next_review_at = datetime.now(UTC) - timedelta(hours=1)
            await s.commit()

        # 未来排期日期以 DB 里 next_review_at 的实际日期为准（避免本地/UTC 日期差）
        async with _test_session_factory() as s:
            future_rec = await s.get(ErrorRecord, uuid.UUID(future_id))
            upcoming_date = future_rec.next_review_at.date().isoformat()

        resp = await client.get("/api/student/error-records/review-plan", headers=_headers(token))
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["due_today"] == 1
        assert len(data["due_items"]) == 1
        assert data["due_items"][0]["record_id"] == due_id
        assert data["due_items"][0]["kp_name"] == kp_name
        assert data["total_active"] == 2
        assert {"date": upcoming_date, "count": 1} in data["upcoming"]

    async def test_plan_empty(self, auth_client):
        client, token, _ = auth_client
        resp = await client.get("/api/student/error-records/review-plan", headers=_headers(token))
        data = resp.json()["data"]
        assert data["due_today"] == 0
        assert data["due_items"] == []
        assert data["total_active"] == 0


# ========== 5. 复习推进 forgotten / 毕业 ==========


class TestReviewResult:
    async def test_forgotten_resets_to_day_one(self, auth_client):
        """forgotten → review_count 清零、重置回第 1 天间隔"""
        client, token, _ = auth_client
        record_id = await _create_record(client, token)

        # 先正常复习一次（review_count=1，排 +3 天）
        resp = await client.post(
            f"/api/student/error-records/{record_id}/review", headers=_headers(token)
        )
        assert resp.json()["data"]["review_count"] == 1

        resp = await client.post(
            f"/api/student/error-records/{record_id}/review",
            json={"result": "forgotten"},
            headers=_headers(token),
        )
        data = resp.json()["data"]
        assert data["review_count"] == 0
        assert data["graduated"] is False
        async with _test_session_factory() as s:
            rec = await s.get(ErrorRecord, uuid.UUID(record_id))
            delta = rec.next_review_at - datetime.now(UTC)
            assert timedelta(hours=23) < delta < timedelta(hours=25)  # 回第 1 天档

    async def test_remembered_graduates_after_15_day_stage(self, auth_client):
        """remembered 推进 1/3/7/15，走完 15 天档（review_count≥4）→ next_review_at null 毕业"""
        client, token, _ = auth_client
        record_id = await _create_record(client, token)

        expected = [3, 7, 15]
        for i, days in enumerate(expected, start=1):
            resp = await client.post(
                f"/api/student/error-records/{record_id}/review", headers=_headers(token)
            )
            data = resp.json()["data"]
            assert data["review_count"] == i
            assert data["graduated"] is False
            async with _test_session_factory() as s:
                rec = await s.get(ErrorRecord, uuid.UUID(record_id))
                delta = rec.next_review_at - datetime.now(UTC)
                assert timedelta(days=days - 0.1) < delta < timedelta(days=days + 0.1)

        # 第 4 次（走完 15 天档）→ 毕业
        resp = await client.post(
            f"/api/student/error-records/{record_id}/review", headers=_headers(token)
        )
        data = resp.json()["data"]
        assert data["review_count"] == 4
        assert data["next_review_at"] is None
        assert data["graduated"] is True

        # 毕业后不再计入复习计划在册数
        plan = await client.get("/api/student/error-records/review-plan", headers=_headers(token))
        assert plan.json()["data"]["total_active"] == 0

    async def test_invalid_result_40001(self, auth_client):
        client, token, _ = auth_client
        record_id = await _create_record(client, token)
        resp = await client.post(
            f"/api/student/error-records/{record_id}/review",
            json={"result": "maybe"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001


# ========== 6. warnings 规则引擎 ==========


class TestWarnings:
    async def test_empty_user_no_warnings(self, auth_client):
        client, token, _ = auth_client
        resp = await client.get("/api/student/warnings", headers=_headers(token))
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["warnings"] == []
        assert body["data"]["computed_at"]

    async def test_kp_streak_fail(self, auth_client):
        """某知识点最近 3 次判分全错 → kp_streak_fail（温和话术 + kp_name）"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            kp = await _make_kp(s)
            quiz = Quiz(user_id=uid, source="ai_generated", title="t", kp_codes=[kp.code])
            s.add(quiz)
            await s.flush()
            for i in (1, 2, 3):
                s.add(QuizItem(
                    quiz_id=quiz.id, item_no=i, q_type="choice",
                    question_text=f"题{i}", answer="A", kp_code=kp.code,
                ))
            sub = Submission(
                user_id=uid, quiz_id=quiz.id,
                client_submit_id=f"w-{uuid.uuid4().hex[:12]}", status="graded", total_score=0,
            )
            s.add(sub)
            await s.flush()
            for i in (1, 2, 3):
                s.add(SubmissionItem(
                    submission_id=sub.id, item_no=i, q_type="choice", verdict="wrong", score=0.0,
                ))
            await s.commit()
            kp_code, kp_name = kp.code, kp.name

        resp = await client.get("/api/student/warnings", headers=_headers(token))
        rules = {w["rule"]: w for w in resp.json()["data"]["warnings"]}
        assert "kp_streak_fail" in rules
        w = rules["kp_streak_fail"]
        assert w["level"] == "gentle"
        assert w["kp_code"] == kp_code
        assert w["kp_name"] == kp_name
        assert "专练" in w["message"]  # 可执行建议

    async def test_activity_drop(self, auth_client):
        """上周 ≥4 次提交、本周骤降 ≥50% → activity_drop"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        last_week = _week_start() - timedelta(days=2)
        async with _test_session_factory() as s:
            for _ in range(4):
                s.add(Submission(
                    user_id=uid, client_submit_id=f"w-{uuid.uuid4().hex[:12]}",
                    status="graded", created_at=last_week,
                ))
            await s.commit()

        resp = await client.get("/api/student/warnings", headers=_headers(token))
        rules = {w["rule"] for w in resp.json()["data"]["warnings"]}
        assert "activity_drop" in rules

    async def test_hint_dependency_up(self, auth_client):
        """提示依赖度周环比升 ≥30% → hint_dependency_up（tutor_sessions 数据源）"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        last_week = _week_start() - timedelta(days=2)
        async with _test_session_factory() as s:
            conv = Conversation(user_id=uid, active_role="student")
            s.add(conv)
            await s.flush()
            # 上周 1 会话 1 次提示（依赖度 1.0）
            s.add(TutorSession(
                user_id=uid, conversation_id=conv.id, question_text="上周题",
                hint_counts={"point": 1}, answer_requests=0, created_at=last_week,
            ))
            # 本周 2 会话各 4 次提示（依赖度 4.0，环比 +300%）
            for _ in range(2):
                s.add(TutorSession(
                    user_id=uid, conversation_id=conv.id, question_text="本周题",
                    hint_counts={"point": 2, "teach": 2}, answer_requests=0,
                ))
            await s.commit()

        resp = await client.get("/api/student/warnings", headers=_headers(token))
        rules = {w["rule"] for w in resp.json()["data"]["warnings"]}
        assert "hint_dependency_up" in rules

    async def test_score_drop(self, auth_client):
        """最近两次有总分的提交降 ≥20 分 → score_drop"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        now = datetime.now(UTC)
        async with _test_session_factory() as s:
            s.add(Submission(
                user_id=uid, client_submit_id=f"w-{uuid.uuid4().hex[:12]}",
                status="graded", total_score=90, created_at=now - timedelta(hours=2),
            ))
            s.add(Submission(
                user_id=uid, client_submit_id=f"w-{uuid.uuid4().hex[:12]}",
                status="graded", total_score=65, created_at=now - timedelta(hours=1),
            ))
            await s.commit()

        resp = await client.get("/api/student/warnings", headers=_headers(token))
        rules = {w["rule"]: w for w in resp.json()["data"]["warnings"]}
        assert "score_drop" in rules
        assert rules["score_drop"]["level"] == "gentle"
        assert "练" in rules["score_drop"]["message"]  # 可执行建议


# ========== 7. mastery/trend 快照真实化 ==========


class TestMasteryTrend:
    async def test_snapshot_upsert_on_bkt_write(self):
        """BKT 写路径同步 upsert 当日快照：同日同 kp 只留一行且为最新后验值"""
        async with _db() as db:
            user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
            db.add(user)
            await db.flush()
            kp = await _make_kp(db)

            await sr._update_mastery(db, user.id, kp.code, correct=True)
            await sr._update_mastery(db, user.id, kp.code, correct=False)
            await db.flush()

            snaps = (
                await db.execute(
                    select(MasterySnapshot).where(
                        MasterySnapshot.user_id == user.id,
                        MasterySnapshot.kp_code == kp.code,
                        MasterySnapshot.date == date.today(),
                    )
                )
            ).scalars().all()
            assert len(snaps) == 1  # upsert：同日同 kp 一行
            mr = await db.get(MasteryRecord, (user.id, kp.id))
            assert abs(float(snaps[0].mastery) - float(mr.mastery)) < 1e-9  # 最新后验值

    async def test_trend_from_snapshots(self, auth_client):
        """trend 按日分组 avg(mastery) 返 points"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        today = date.today()
        yesterday = today - timedelta(days=1)
        async with _test_session_factory() as s:
            s.add(MasterySnapshot(user_id=uid, kp_code="K1", date=yesterday, mastery=0.5))
            s.add(MasterySnapshot(user_id=uid, kp_code="K1", date=today, mastery=0.6))
            s.add(MasterySnapshot(user_id=uid, kp_code="K2", date=today, mastery=0.8))
            await s.commit()

        resp = await client.get("/api/student/mastery/trend", headers=_headers(token))
        body = resp.json()
        assert body["code"] == 0
        points = {p["date"]: p["avg_mastery"] for p in body["data"]["points"]}
        assert points[yesterday.isoformat()] == pytest.approx(0.5)
        assert points[today.isoformat()] == pytest.approx(0.7)  # (0.6+0.8)/2

    async def test_trend_fallback_single_point(self, auth_client):
        """无快照 → ADR-039 兜底单点（无掌握度记录时 avg_mastery=0.5）"""
        client, token, _ = auth_client
        resp = await client.get("/api/student/mastery/trend", headers=_headers(token))
        points = resp.json()["data"]["points"]
        assert len(points) == 1
        assert points[0]["date"] == date.today().isoformat()
        assert points[0]["avg_mastery"] == 0.5


# ========== 8. daily-plan 真实化 ==========


class TestDailyPlan:
    async def test_empty_user_defaults(self, auth_client):
        """无数据：week_goal null；daily_question 结构完整；仍给出每日一题任务"""
        client, token, _ = auth_client
        resp = await client.get("/api/student/daily-plan", headers=_headers(token))
        data = resp.json()["data"]
        assert data["week_goal"] is None
        dq = data["daily_question"]  # 共享库当日全站题可能已由环境生成，只校验结构
        assert set(dq) >= {"date", "quiz_id", "item", "completed", "streak"}
        assert dq["date"] == date.today().isoformat()
        assert dq["completed"] is False
        assert any(t["type"] == "daily_question" for t in data["today_tasks"])
        assert len(data["today_tasks"]) <= 5

    async def test_real_assembly(self, auth_client):
        """真实装配：daily_question 取当日题组+摘要；week_goal 取最低掌握度；复习任务带 ref_id/reason"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        today = date.today()

        async with _test_session_factory() as s:
            kp = await _make_kp(s, name="联动薄弱点")
            # 掌握度 0.3（红色薄弱）
            s.add(MasteryRecord(user_id=uid, kp_id=kp.id, mastery=0.3, practice_count=5))
            # 当日每日一题（全站统一行可能已由环境/其它用例生成，存在则复用）
            existing_daily = (
                await s.execute(select(DailyQuestion).where(DailyQuestion.date == today))
            ).scalar_one_or_none()
            created_daily = existing_daily is None
            if created_daily:
                quiz = Quiz(user_id=uid, source="daily", title=f"每日一题 {today}", kp_codes=[kp.code])
                s.add(quiz)
                await s.flush()
                s.add(QuizItem(
                    quiz_id=quiz.id, item_no=1, q_type="choice",
                    question_text="今日联动题 $1+1=?$", answer="A", kp_code=kp.code,
                ))
                s.add(DailyQuestion(user_id=uid, date=today, quiz_id=quiz.id))
                quiz_id = quiz.id
            else:
                quiz_id = existing_daily.quiz_id
            # 今日到期复习错题
            s.add(ErrorRecord(
                user_id=uid, question_text="到期复习题", source_channel="manual_photo",
                error_type="concept", kp_code=kp.code,
                next_review_at=datetime.now(UTC) - timedelta(hours=1), review_count=1,
            ))
            await s.commit()
            kp_code, kp_name = kp.code, kp.name

        try:
            resp = await client.get("/api/student/daily-plan", headers=_headers(token))
            data = resp.json()["data"]

            # daily_question 真实化（复用 practice/daily 装配）
            dq = data["daily_question"]
            assert dq["quiz_id"] == str(quiz_id)
            assert dq["completed"] is False
            assert "streak" in dq
            if created_daily:
                assert dq["item"]["question_text"].startswith("今日联动题")

            # week_goal 取掌握度最低非灰 kp
            assert data["week_goal"] is not None
            assert kp_name in data["week_goal"]["text"]
            assert "30%" in data["week_goal"]["text"]
            assert data["week_goal"]["progress"] == pytest.approx(0.3)

            # today_tasks：复习（含第 N 次）→ 专练 → 每日一题，≤5 且每项带 reason
            tasks = data["today_tasks"]
            assert len(tasks) <= 5
            assert all(t["reason"] for t in tasks)
            review_task = next(t for t in tasks if t["type"] == "review")
            assert review_task["ref_id"]
            assert "间隔复习第 2 次" in review_task["reason"]  # review_count=1 → 第 2 次
            practice_task = next(t for t in tasks if t["type"] == "practice")
            assert practice_task["ref_id"] == kp_code
        finally:
            # 清理本用例自建的当日 daily_questions 行（date 全站唯一，留渣会让
            # test_student_pipeline 的当日一题用例撞唯一约束）
            if created_daily:
                async with _test_session_factory() as s:
                    await s.execute(delete(DailyQuestion).where(DailyQuestion.quiz_id == quiz_id))
                    await s.execute(delete(QuizItem).where(QuizItem.quiz_id == quiz_id))
                    await s.execute(delete(Quiz).where(Quiz.id == quiz_id))
                    await s.commit()

    async def test_weak_parent_before_child(self, auth_client):
        """父子同弱 → 父节点专练任务排在子节点前"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            parent = await _make_kp(s, name="父节点")
            child = await _make_kp(s, name="子节点", parent_id=parent.id)
            # 子更弱（升序排前），父也弱 → 父应前置
            s.add(MasteryRecord(user_id=uid, kp_id=parent.id, mastery=0.5, practice_count=3))
            s.add(MasteryRecord(user_id=uid, kp_id=child.id, mastery=0.2, practice_count=3))
            await s.commit()
            parent_code, child_code = parent.code, child.code

        resp = await client.get("/api/student/daily-plan", headers=_headers(token))
        practice_refs = [t["ref_id"] for t in resp.json()["data"]["today_tasks"] if t["type"] == "practice"]
        assert practice_refs == [parent_code, child_code]  # 父前置


# ========== 9. 知识节点卡联动增强 ==========


class TestNodeCard:
    async def _node(self, client, token, kp_code):
        resp = await client.get(f"/api/student/knowledge-graph/nodes/{kp_code}", headers=_headers(token))
        assert resp.json()["code"] == 0
        return resp.json()["data"]

    async def test_red_node_with_weak_parent(self, auth_client):
        """红节点 + 父节点黄 → guide+quiz 建议，补 prerequisite_hint"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            parent = await _make_kp(s, name="前置章")
            child = await _make_kp(s, name="本节点", parent_id=parent.id)
            s.add(MasteryRecord(user_id=uid, kp_id=parent.id, mastery=0.5))  # 黄
            s.add(MasteryRecord(user_id=uid, kp_id=child.id, mastery=0.2))  # 红
            await s.commit()
            parent_code, parent_name, child_code = parent.code, parent.name, child.code

        data = await self._node(client, token, child_code)
        assert data["suggested_actions"][0] == {"action": "guide", "reason": "基础薄弱，建议引导复习"}
        hint = data["prerequisite_hint"]
        assert hint is not None
        assert hint["kp_code"] == parent_code
        assert hint["kp_name"] == parent_name
        assert "先补前置" in hint["message"]

    async def test_red_node_with_green_parent_no_hint(self, auth_client):
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            parent = await _make_kp(s)
            child = await _make_kp(s, parent_id=parent.id)
            s.add(MasteryRecord(user_id=uid, kp_id=parent.id, mastery=0.9))  # 绿
            s.add(MasteryRecord(user_id=uid, kp_id=child.id, mastery=0.2))  # 红
            await s.commit()
            child_code = child.code

        data = await self._node(client, token, child_code)
        assert data["prerequisite_hint"] is None

    async def test_actions_by_color(self, auth_client):
        """黄/绿/灰三档建议"""
        client, token, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            kp_yellow = await _make_kp(s)
            kp_green = await _make_kp(s)
            kp_gray = await _make_kp(s)
            s.add(MasteryRecord(user_id=uid, kp_id=kp_yellow.id, mastery=0.55))
            s.add(MasteryRecord(user_id=uid, kp_id=kp_green.id, mastery=0.85))
            await s.commit()
            codes = (kp_yellow.code, kp_green.code, kp_gray.code)

        yellow = await self._node(client, token, codes[0])
        assert yellow["suggested_actions"] == [{"action": "quiz", "reason": "即将掌握，加强练习"}]
        green = await self._node(client, token, codes[1])
        assert green["suggested_actions"] == [{"action": "quiz", "reason": "保持手感"}]
        gray = await self._node(client, token, codes[2])
        assert gray["suggested_actions"] == [{"action": "quiz", "reason": "学一学试试"}]

    async def test_missing_node_40400(self, auth_client):
        client, token, _ = auth_client
        resp = await client.get("/api/student/knowledge-graph/nodes/no_such_kp", headers=_headers(token))
        assert resp.json()["code"] == 40400


# ========== 10. Bug 修复 ==========


class TestBugFixes:
    async def test_invalid_assignment_id_40001(self, auth_client):
        """非法 assignment_id → 40001（不再 500）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "assignment_id": "not-a-uuid",
                "items": [{"item_no": 1, "q_type": "choice", "answer_text": "A", "kp_code": "x"}],
                "client_submit_id": f"lk-{uuid.uuid4().hex[:12]}",
            },
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_auto_record_question_text_fallback_and_custom_kp(self, auth_client):
        """无题干错题自动收录：从 expected_answer 兜底不落空串；kp_code=custom 不落库"""
        client, token, user_id = auth_client
        resp = await client.post(
            "/api/student/practice/submit",
            json={
                "quiz_id": f"local_{uuid.uuid4().hex}",
                "items": [
                    {
                        "item_no": 1,
                        "q_type": "choice",
                        "answer_text": "A",
                        "expected_answer": "B",
                        "kp_code": "custom",  # 对话出题兜底码
                        # 故意不带 question_text
                    },
                ],
                "client_submit_id": f"lk-{uuid.uuid4().hex[:12]}",
            },
            headers=_headers(token),
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["results"][0]["verdict"] == "wrong"

        async with _test_session_factory() as s:
            rec = (
                await s.execute(
                    select(ErrorRecord).where(ErrorRecord.user_id == uuid.UUID(user_id))
                )
            ).scalars().first()
            assert rec is not None
            assert rec.question_text  # 永不落空串
            assert "标准答案：B" in rec.question_text
            assert rec.kp_code is None  # custom 不落库
