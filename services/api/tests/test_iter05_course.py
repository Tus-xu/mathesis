"""迭代05 F9 双师课堂预处理管线测试（阶段 4，SSOT §4.10 / ADR-034）

覆盖：
1. 课程登记 → 触发预处理（BackgroundTasks）
2. 预处理三级降级链：工作流 → 本地 LLM → 固定切段
3. 预处理幂等（ready 直接返回缓存）
4. 阶段总结 / 看课检测端点
5. kp_codes 白名单过滤
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.domains.classroom import course_router as cr
from app.main import app
from app.models.database import get_db
from app.models.role_binding import RoleBinding

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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_teacher(client) -> tuple[str, str]:
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    token, user_id = data["token"], data["user"]["id"]
    async with _test_session_factory() as db:
        db.add(RoleBinding(user_id=user_id, role="teacher", verified=True))
        await db.commit()
    switch = await client.post(
        "/api/auth/role/switch", json={"role": "teacher"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return (switch.json().get("data", {}).get("token") or token), user_id


async def _make_student(client) -> tuple[str, str]:
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


_TRANSCRIPT = "[00:00] 今天我们讲三角函数。[00:30] 正弦函数的定义是 $\\sin x$。[01:00] 周期性是 $2\\pi$。"


# ==================== 预处理降级链（单元级） ====================


class TestPreprocessChain:
    @pytest.mark.asyncio
    async def test_workflow_preferred(self):
        """星辰开启且有效 → 使用工作流输出"""
        wf_out = {
            "chapters": [{"title": "三角函数入门", "start_ts": 0.0, "end_ts": 60.0, "summary": "定义与周期性"}],
            "kp_codes": ["MATH-PEP-BIXI1-B21C8FA0DE"],
            "knowledge_cards": [{"title": "正弦定义", "content": "sin x", "ts": 30.0}],
        }
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)):
            result = await cr._preprocess_via_workflow("cid", _TRANSCRIPT, "")
        assert result is not None
        assert result["chapters"][0]["title"] == "三角函数入门"
        assert result["kp_codes"] == ["MATH-PEP-BIXI1-B21C8FA0DE"]

    @pytest.mark.asyncio
    async def test_workflow_failure_returns_none(self):
        """工作流异常 → None（走下一级降级）"""
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await cr._preprocess_via_workflow("cid", _TRANSCRIPT, "")
        assert result is None

    @pytest.mark.asyncio
    async def test_xingchen_disabled_returns_none(self):
        with patch.object(settings, "xingchen_enabled", False):
            assert await cr._preprocess_via_workflow("cid", _TRANSCRIPT, "") is None

    @pytest.mark.asyncio
    async def test_local_llm_fallback(self):
        """本地 LLM 降级（mock 返回合法 JSON）"""
        class _FakeRouter:
            async def chat(self, **kwargs):
                return {"content": '{"chapters": [{"title": "ch1", "start_ts": null, "end_ts": null, "summary": "s"}], "kp_codes": [], "knowledge_cards": []}'}

        with patch("app.providers.router.get_model_router", return_value=_FakeRouter()):
            result = await cr._preprocess_via_local_llm(_TRANSCRIPT, "")
        assert result is not None and result["chapters"][0]["title"] == "ch1"

    @pytest.mark.asyncio
    async def test_fixed_split_never_fails(self):
        """固定切段兜底永不失败"""
        result = cr._preprocess_fixed_split("字" * 1200)
        assert len(result["chapters"]) == 3  # 1200 / 500 = 3 段
        assert result["kp_codes"] == []


# ==================== 端点测试 ====================


class TestCourseEndpoints:
    async def test_student_cannot_create_course(self, client):
        """学生建课 → 403"""
        phone = f"138{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token = resp.json()["data"]["token"]
        resp = await client.post(
            "/api/courses",
            json={"title": "t", "transcript": "字幕"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    async def test_student_cannot_access_foreign_courses(self, client):
        """越权防护（迭代06 审计修复）：学生看不到/访问不到他人课程（列表过滤 + 详情/summary/quiz 归属校验）"""
        # 教师建课（后台预处理打桩，课程真实落库）
        token_t, _ = await _make_teacher(client)
        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            resp = await client.post(
                "/api/courses",
                json={"title": "教师专属课", "transcript": "字幕"},
                headers={"Authorization": f"Bearer {token_t}"},
            )
        cid = resp.json()["data"]["course_id"]

        # 学生登录
        phone = f"137{str(uuid.uuid4().int)[:8]}"
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp_s = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
        token_s = resp_s.json()["data"]["token"]
        headers_s = {"Authorization": f"Bearer {token_s}"}

        # 列表不含他人课程
        resp_list = await client.get("/api/courses", headers=headers_s)
        body = resp_list.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 0
        assert all(item["course_id"] != cid for item in body["data"]["items"])

        # 详情/summary/quiz 全部 40400
        resp_detail = await client.get(f"/api/courses/{cid}", headers=headers_s)
        assert resp_detail.json()["code"] == 40400
        resp_summary = await client.get(f"/api/courses/{cid}/summary", headers=headers_s)
        assert resp_summary.json()["code"] == 40400
        resp_quiz = await client.post(
            f"/api/courses/{cid}/quiz",
            json={"q_type": "choice", "difficulty": "easy"},
            headers=headers_s,
        )
        assert resp_quiz.json()["code"] == 40400

        # 回归：教师本人仍可访问
        resp_own = await client.get(f"/api/courses/{cid}", headers={"Authorization": f"Bearer {token_t}"})
        assert resp_own.json()["code"] == 0

    async def test_confirmed_class_student_can_access_teacher_course(self, client):
        """已确认班级学生应能列出并读取教师派发的双师课堂。"""
        from app.models.class_ import Class
        from app.models.class_member import ClassMember

        token_t, teacher_id = await _make_teacher(client)
        token_s, student_id = await _make_student(client)
        async with _test_session_factory() as db:
            clazz = Class(
                owner_id=uuid.UUID(teacher_id),
                invite_code=uuid.uuid4().hex[:8],
                name="双师课堂班",
                grade="高二",
                subject="math",
            )
            db.add(clazz)
            await db.flush()
            db.add(
                ClassMember(
                    class_id=clazz.id,
                    user_id=uuid.UUID(student_id),
                    member_role="student",
                    confirmed=True,
                    join_via="code",
                )
            )
            await db.commit()
            class_id = str(clazz.id)

        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            created = await client.post(
                "/api/courses",
                json={"title": "教师派发课", "transcript": "字幕", "class_id": class_id},
                headers={"Authorization": f"Bearer {token_t}"},
            )
        assert created.json()["code"] == 0, created.text
        course_id = created.json()["data"]["course_id"]
        student_headers = {"Authorization": f"Bearer {token_s}"}

        listed = await client.get("/api/courses", headers=student_headers)
        assert listed.json()["code"] == 0, listed.text
        assert [item["course_id"] for item in listed.json()["data"]["items"]] == [course_id]

        detail = await client.get(f"/api/courses/{course_id}", headers=student_headers)
        assert detail.json()["code"] == 0, detail.text
        summary = await client.get(f"/api/courses/{course_id}/summary", headers=student_headers)
        assert summary.json()["code"] == 0, summary.text
        quiz = await client.post(
            f"/api/courses/{course_id}/quiz",
            json={"q_type": "choice", "difficulty": "easy"},
            headers=student_headers,
        )
        assert quiz.json()["code"] == 40901, quiz.text

    async def test_unconfirmed_class_student_cannot_access_teacher_course(self, client):
        """待确认成员不能借班级关系读取双师课堂。"""
        from app.models.class_ import Class
        from app.models.class_member import ClassMember

        token_t, teacher_id = await _make_teacher(client)
        token_s, student_id = await _make_student(client)
        async with _test_session_factory() as db:
            clazz = Class(
                owner_id=uuid.UUID(teacher_id),
                invite_code=uuid.uuid4().hex[:8],
                name="待审核班",
                grade="高二",
                subject="math",
            )
            db.add(clazz)
            await db.flush()
            db.add(
                ClassMember(
                    class_id=clazz.id,
                    user_id=uuid.UUID(student_id),
                    member_role="student",
                    confirmed=False,
                    join_via="code",
                )
            )
            await db.commit()
            class_id = str(clazz.id)

        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            created = await client.post(
                "/api/courses",
                json={"title": "未派发课", "transcript": "字幕", "class_id": class_id},
                headers={"Authorization": f"Bearer {token_t}"},
            )
        course_id = created.json()["data"]["course_id"]
        student_headers = {"Authorization": f"Bearer {token_s}"}

        listed = await client.get("/api/courses", headers=student_headers)
        assert all(
            item["course_id"] != course_id for item in listed.json()["data"]["items"]
        )
        detail = await client.get(f"/api/courses/{course_id}", headers=student_headers)
        assert detail.json()["code"] == 40400
        summary = await client.get(f"/api/courses/{course_id}/summary", headers=student_headers)
        assert summary.json()["code"] == 40400
        quiz = await client.post(
            f"/api/courses/{course_id}/quiz",
            json={"q_type": "choice", "difficulty": "easy"},
            headers=student_headers,
        )
        assert quiz.json()["code"] == 40400

    async def test_inactive_or_deleted_class_revokes_student_course_access(self, client):
        """班级停用或软删后，已确认成员也必须立即失去课程读取权。"""
        from datetime import UTC, datetime

        from app.models.class_ import Class
        from app.models.class_member import ClassMember

        token_t, teacher_id = await _make_teacher(client)
        token_s, student_id = await _make_student(client)
        async with _test_session_factory() as db:
            clazz = Class(
                owner_id=uuid.UUID(teacher_id),
                invite_code=uuid.uuid4().hex[:8],
                name="可撤销课程班",
                grade="高二",
                subject="math",
            )
            db.add(clazz)
            await db.flush()
            db.add(
                ClassMember(
                    class_id=clazz.id,
                    user_id=uuid.UUID(student_id),
                    member_role="student",
                    confirmed=True,
                    join_via="code",
                )
            )
            await db.commit()
            class_id = str(clazz.id)

        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            created = await client.post(
                "/api/courses",
                json={"title": "状态撤销课", "transcript": "字幕", "class_id": class_id},
                headers={"Authorization": f"Bearer {token_t}"},
            )
        course_id = created.json()["data"]["course_id"]
        student_headers = {"Authorization": f"Bearer {token_s}"}

        visible = await client.get(f"/api/courses/{course_id}", headers=student_headers)
        assert visible.json()["code"] == 0

        async with _test_session_factory() as db:
            clazz = await db.get(Class, uuid.UUID(class_id))
            assert clazz is not None
            clazz.status = "inactive"
            await db.commit()
        inactive = await client.get(f"/api/courses/{course_id}", headers=student_headers)
        assert inactive.json()["code"] == 40400

        async with _test_session_factory() as db:
            clazz = await db.get(Class, uuid.UUID(class_id))
            assert clazz is not None
            clazz.status = "active"
            clazz.deleted_at = datetime.now(UTC)
            await db.commit()
        deleted = await client.get(f"/api/courses/{course_id}", headers=student_headers)
        assert deleted.json()["code"] == 40400

    async def test_teacher_cannot_create_course_for_foreign_class(self, client):
        """教师不得通过 class_id 把课程登记到无权管理的班级。"""
        from app.models.class_ import Class

        token_t, _ = await _make_teacher(client)
        _, other_teacher_id = await _make_teacher(client)
        async with _test_session_factory() as db:
            clazz = Class(
                owner_id=uuid.UUID(other_teacher_id),
                invite_code=uuid.uuid4().hex[:8],
                name="他人班级",
                grade="高二",
                subject="math",
            )
            db.add(clazz)
            await db.commit()
            class_id = str(clazz.id)

        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            response = await client.post(
                "/api/courses",
                json={"title": "越权课程", "transcript": "字幕", "class_id": class_id},
                headers={"Authorization": f"Bearer {token_t}"},
            )

        assert response.status_code == 403
        assert response.json()["code"] == 40302

    async def test_create_course_triggers_preprocess(self, client):
        """教师建课 → 返回 course_id + pending（后台任务已挂）"""
        token, _ = await _make_teacher(client)
        with patch.object(cr, "_run_course_preprocess", new=AsyncMock()):
            resp = await client.post(
                "/api/courses",
                json={"title": "三角函数课", "transcript": _TRANSCRIPT},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "pending"
        assert body["data"]["course_id"]

    async def test_preprocess_idempotent_ready(self, client):
        """ready 课程手动触发 → 直接返回缓存产物"""
        token, user_id = await _make_teacher(client)
        # 直接种一个 ready 课程
        from app.models.course import COURSE_STATUS_READY, Course

        async with _test_session_factory() as db:
            course = Course(
                user_id=uuid.UUID(user_id),
                title="已完成课",
                transcript="字幕",
                status=COURSE_STATUS_READY,
                preprocess_result={"chapters": [{"title": "c1", "summary": "s"}], "kp_codes": [], "knowledge_cards": []},
                preprocess_engine="fixed_split",
            )
            db.add(course)
            await db.commit()
            cid = str(course.id)

        resp = await client.post(
            f"/api/courses/{cid}/preprocess",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["status"] == "ready"
        assert body["data"]["result"]["chapters"][0]["title"] == "c1"

    async def test_course_summary_and_detail(self, client):
        """课程详情 + 阶段总结"""
        token, user_id = await _make_teacher(client)
        from app.models.course import COURSE_STATUS_READY, Course

        async with _test_session_factory() as db:
            course = Course(
                user_id=uuid.UUID(user_id),
                title="总结课",
                transcript="字幕",
                status=COURSE_STATUS_READY,
                preprocess_result={
                    "chapters": [
                        {"title": "第1章", "start_ts": 0.0, "end_ts": 30.0, "summary": "引入"},
                        {"title": "第2章", "start_ts": 30.0, "end_ts": 60.0, "summary": "深入"},
                    ],
                    "kp_codes": [],
                    "knowledge_cards": [],
                },
                preprocess_engine="spark_direct",
            )
            db.add(course)
            await db.commit()
            cid = str(course.id)

        detail = await client.get(f"/api/courses/{cid}", headers={"Authorization": f"Bearer {token}"})
        assert detail.json()["data"]["status"] == "ready"
        assert len(detail.json()["data"]["chapters"]) == 2

        summary = await client.get(f"/api/courses/{cid}/summary", headers={"Authorization": f"Bearer {token}"})
        stages = summary.json()["data"]["stages"]
        assert len(stages) == 2
        assert stages[0]["title"] == "第1章"

    async def test_course_quiz_requires_kp(self, client):
        """看课检测：未锚定知识点 → 40901"""
        token, user_id = await _make_teacher(client)
        from app.models.course import COURSE_STATUS_READY, Course

        async with _test_session_factory() as db:
            course = Course(
                user_id=uuid.UUID(user_id),
                title="无kp课",
                transcript="字幕",
                status=COURSE_STATUS_READY,
                preprocess_result={"chapters": [{"title": "c"}], "kp_codes": [], "knowledge_cards": []},
                preprocess_engine="fixed_split",
            )
            db.add(course)
            await db.commit()
            cid = str(course.id)

        resp = await client.post(
            f"/api/courses/{cid}/quiz",
            json={"q_type": "choice", "difficulty": "medium"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()["code"] == 40901

    async def test_missing_course_40400(self, client):
        token, _ = await _make_teacher(client)
        resp = await client.get(f"/api/courses/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"})
        assert resp.json()["code"] == 40400
