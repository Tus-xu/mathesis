"""R14 API 层证据：真实 FastAPI 栈（router + middleware + fail 信封）+ 假 8000 成员。

覆盖：200 创建/列表/事件、403 跨班/角色、409 非法转换、404 未知会话、503 权威不可达。
通过 dependency_overrides 注入测试 DB session 与当前用户；router._adapter 替换为假成员适配器。
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.teacher_prep.router as prep_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
BASE = "/api/teacher-v3/prep"


class _MembershipSim:
    """8000 成员模拟（与 test_service 的 conftest 同构，独立一份避免跨文件相对导入）。"""

    def __init__(self) -> None:
        self._members: dict[str, dict] = {}
        self._fail_5xx: set[str] = set()

    def set(self, class_id: str, *, user_id: str = TEACHER, role: str = "teacher", confirmed: bool = True) -> None:
        self._members[class_id] = {"class_id": class_id, "user_id": user_id, "member_role": role, "confirmed": confirmed}

    def remove(self, class_id: str) -> None:
        self._members.pop(class_id, None)

    def set_5xx(self, class_id: str) -> None:
        self._fail_5xx.add(class_id)

    def handler(self, method: str, path: str, token: str, json_body: dict | None):
        if path.startswith("/api/classes/"):
            cid = path.split("/")[-1]
            if cid in self._fail_5xx:
                return 500, {"detail": "boom"}
            member = self._members.get(cid)
            if member is None:
                return 404, {"detail": "member not found"}
            if member.get("confirmed") is False:
                return 403, {"detail": "unconfirmed"}
            return 200, member
        return 404, {"detail": "unknown path"}


@pytest.fixture
def api(db: AsyncSession):
    """返回 (app, members)：真实 app + 注入测试 session/用户 + 假成员适配器。"""
    from main import create_app  # 与 tests/contract/conftest 同款惰性导入

    members = _MembershipSim()
    transport = FakeApiTransport()
    transport.handler = members.handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())

    app = create_app()
    prep_router._adapter = lambda: adapter  # type: ignore[assignment]

    async def _override_db():
        yield db

    def _override_user():
        return UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)

    app.dependency_overrides[get_session] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    return app, members


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_api_create_and_list(api) -> None:
    app, members = api
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/sessions",
            json={"class_id": CLASS_A, "title": "函数单调性", "idempotency_key": "api-key-0001"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 0 and body["data"]["status"] == "draft"
        sid = body["data"]["session_id"]

        # 非法转换：draft → cancel 合法，但 draft → open 后用陈旧 revision 再 open → 409
        r2 = await c.post(
            f"{BASE}/sessions/open",
            json={"session_id": sid, "expected_revision": 1, "idempotency_key": "api-key-0002"},
        )
        assert r2.status_code == 200 and r2.json()["data"]["status"] == "active"

        r3 = await c.post(
            f"{BASE}/sessions/open",
            json={"session_id": sid, "expected_revision": 1, "idempotency_key": "api-key-0003"},
        )
        assert r3.status_code == 409 and r3.json()["code"] == 40900  # revision 冲突

        r4 = await c.get(f"{BASE}/sessions/{sid}/events")
        assert r4.status_code == 200
        evs = r4.json()["data"]["items"]
        assert [e["event_type"] for e in evs] == [
            "command.applied", "session.created",
            "command.applied", "session.opened",
        ]
        # lineage 链完整
        assert evs[1]["parent_event_id"] == evs[0]["event_id"]


async def test_api_cross_class_403(api) -> None:
    app, members = api
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/sessions",
            json={"class_id": CLASS_A, "title": "t", "idempotency_key": "api-key-0011"},
        )
        sid = r.json()["data"]["session_id"]
        # 成员变更：剔除后 open → 40301
        members.remove(CLASS_A)
        r2 = await c.post(
            f"{BASE}/sessions/open",
            json={"session_id": sid, "expected_revision": 1, "idempotency_key": "api-key-0012"},
        )
        assert r2.status_code == 403 and r2.json()["code"] == 40301


async def test_api_llm_cannot_confirm(api) -> None:
    app, members = api
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/sessions",
            json={"class_id": CLASS_A, "title": "t", "idempotency_key": "api-key-0021"},
        )
        sid = r.json()["data"]["session_id"]
        rp = await c.post(
            f"{BASE}/blueprints/propose",
            json={"session_id": sid, "body": {"topic": "A"}, "idempotency_key": "api-key-0022"},
        )
        assert rp.status_code == 200
        bid = rp.json()["data"]["blueprint_id"]
        # actor_role_hint 由 JWT role 决定；这里 user 恒为 teacher → 服务内部仍过教学角色闸门。
        # 跨班/角色回退：把成员改为 student → confirm 403
        members.set(CLASS_A, user_id=TEACHER, role="student")
        rc = await c.post(
            f"{BASE}/blueprints/confirm",
            json={"blueprint_id": bid, "expected_revision": 1, "idempotency_key": "api-key-0023"},
        )
        assert rc.status_code == 403 and rc.json()["code"] == 40301


async def test_api_unknown_session_404(api) -> None:
    app, members = api
    members.set(CLASS_A, user_id=TEACHER)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/sessions/open",
            json={
                "session_id": "99999999-9999-9999-9999-999999999999",
                "expected_revision": 1,
                "idempotency_key": "api-key-0031",
            },
        )
        assert r.status_code == 404 and r.json()["code"] == 40400


async def test_api_authority_unreachable_503(api) -> None:
    app, members = api
    members.set(CLASS_A, user_id=TEACHER)
    members.set_5xx(CLASS_A)
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/sessions",
            json={"class_id": CLASS_A, "title": "t", "idempotency_key": "api-key-0041"},
        )
        assert r.status_code == 503 and r.json()["code"] == 50300
