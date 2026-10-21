"""班级域 API 测试

覆盖：建班/加入/列表/成员/确认/重置码 + 越权测试。
"""

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
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


async def _register_and_login(client, phone=None) -> tuple[str, str]:
    """注册并登录，返回 (token, user_id)"""
    phone = phone or f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


async def _make_teacher(client) -> tuple[str, str]:
    """创建教师用户（通过直接操作数据库添加 teacher 角色）"""
    token, user_id = await _register_and_login(client)
    # 通过数据库直接添加 teacher 角色
    from sqlalchemy import select

    from app.models.role_binding import RoleBinding

    async with _test_session_factory() as db:
        # 检查是否已有 teacher 角色
        result = await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.role == "teacher",
            )
        )
        if result.scalar_one_or_none() is None:
            rb = RoleBinding(user_id=user_id, role="teacher", verified=True)
            db.add(rb)
            await db.commit()
    return token, user_id


# ========== 建班测试 ==========


class TestCreateClass:
    """POST /api/classes — 教师建班"""

    async def test_teacher_create_class(self, client):
        """教师成功建班"""
        token, _ = await _make_teacher(client)
        resp = await client.post(
            "/api/classes",
            json={"name": "高一(1)班", "grade": "高一"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert "inviteCode" in data["data"]
        assert len(data["data"]["inviteCode"]) == 6

    async def test_student_cannot_create_class(self, client):
        """学生不能建班 → 40301"""
        token, _ = await _register_and_login(client)
        resp = await client.post(
            "/api/classes",
            json={"name": "测试班"},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        assert data["code"] == 40301

    async def test_create_class_unauthorized(self, client):
        """未认证建班 → 401"""
        resp = await client.post("/api/classes", json={"name": "测试班"})
        assert resp.status_code == 401

    async def test_create_class_invalid_name(self, client):
        """空班级名 → 422"""
        token, _ = await _make_teacher(client)
        resp = await client.post(
            "/api/classes",
            json={"name": ""},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


# ========== 加入班级测试 ==========


class TestJoinClass:
    """POST /api/classes/join — 学生加入"""

    async def test_student_join_class(self, client):
        """学生通过班级码加入"""
        # 教师建班
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "测试班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        invite_code = create_resp.json()["data"]["inviteCode"]

        # 学生加入
        student_token, _ = await _register_and_login(client)
        resp = await client.post(
            "/api/classes/join",
            json={"invite_code": invite_code},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["status"] == "pending"

    async def test_join_invalid_code(self, client):
        """无效班级码 → 40003"""
        token, _ = await _register_and_login(client)
        resp = await client.post(
            "/api/classes/join",
            json={"invite_code": "XXXXXX"},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        assert data["code"] == 40003

    async def test_join_duplicate(self, client):
        """重复加入 → 40902"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "重复测试班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        invite_code = create_resp.json()["data"]["inviteCode"]

        student_token, _ = await _register_and_login(client)
        # 第一次加入
        await client.post(
            "/api/classes/join",
            json={"invite_code": invite_code},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        # 第二次加入
        resp = await client.post(
            "/api/classes/join",
            json={"invite_code": invite_code},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        data = resp.json()
        assert data["code"] == 40902


# ========== 我的班级列表 ==========


class TestMyClasses:
    """GET /api/classes/mine"""

    async def test_my_classes(self, client):
        """获取我的班级列表"""
        teacher_token, _ = await _make_teacher(client)
        await client.post(
            "/api/classes",
            json={"name": "我的班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        resp = await client.get(
            "/api/classes/mine",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1


# ========== 成员管理 ==========


class TestMembers:
    """GET/PATCH 成员相关"""

    async def test_list_members_as_teacher(self, client):
        """教师查看成员列表（含待确认）"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "成员测试班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        class_id = create_resp.json()["data"]["id"]

        resp = await client.get(
            f"/api/classes/{class_id}/members",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        data = resp.json()
        assert data["code"] == 0
        assert data["data"]["total"] >= 1  # 至少有教师自己

    async def test_non_member_cannot_view(self, client):
        """非本班成员查看 → 404"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "封闭班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        class_id = create_resp.json()["data"]["id"]

        # 另一个用户尝试查看
        other_token, _ = await _register_and_login(client)
        resp = await client.get(
            f"/api/classes/{class_id}/members",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        data = resp.json()
        assert data["code"] == 40401

    async def test_student_cannot_confirm(self, client):
        """学生确认成员 → 40301"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "权限测试班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        class_id = create_resp.json()["data"]["id"]
        invite_code = create_resp.json()["data"]["inviteCode"]

        # 学生加入
        student_token, student_id = await _register_and_login(client)
        await client.post(
            "/api/classes/join",
            json={"invite_code": invite_code},
            headers={"Authorization": f"Bearer {student_token}"},
        )

        # 学生尝试确认自己
        resp = await client.patch(
            f"/api/classes/{class_id}/members/{student_id}",
            json={"confirmed": True},
            headers={"Authorization": f"Bearer {student_token}"},
        )
        data = resp.json()
        assert data["code"] == 40301


# ========== 重置班级码 ==========


class TestResetInviteCode:
    """POST /api/classes/{id}/invite-code/reset"""

    async def test_reset_code(self, client):
        """教师重置班级码"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "重置测试班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        class_id = create_resp.json()["data"]["id"]
        old_code = create_resp.json()["data"]["inviteCode"]

        resp = await client.post(
            f"/api/classes/{class_id}/invite-code/reset",
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        data = resp.json()
        assert data["code"] == 0
        new_code = data["data"]["inviteCode"]
        assert new_code != old_code
        assert len(new_code) == 6

    async def test_non_owner_cannot_reset(self, client):
        """非创建者重置 → 40302"""
        teacher_token, _ = await _make_teacher(client)
        create_resp = await client.post(
            "/api/classes",
            json={"name": "他人班"},
            headers={"Authorization": f"Bearer {teacher_token}"},
        )
        class_id = create_resp.json()["data"]["id"]

        other_token, _ = await _register_and_login(client)
        resp = await client.post(
            f"/api/classes/{class_id}/invite-code/reset",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        data = resp.json()
        assert data["code"] == 40302
