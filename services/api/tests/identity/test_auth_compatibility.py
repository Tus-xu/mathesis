"""Compatibility-window authentication contracts."""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.models.database import async_session_factory
from app.models.identity import AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User


async def test_class_code_login_is_deprecated_and_creates_no_user():
    nickname = f"legacy-{uuid.uuid4().hex[:10]}"
    async with async_session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(User))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login-by-code",
            json={"invite_code": "ABCDEF", "nickname": nickname},
        )

    assert response.status_code == 410
    assert response.json()["error_key"] == "AUTH_CLASS_CODE_LOGIN_DEPRECATED"
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"]
    async with async_session_factory() as db:
        after = await db.scalar(select(func.count()).select_from(User))
    assert after == before


async def test_legacy_sms_endpoint_announces_compatibility_sunset():
    phone = f"135{uuid.uuid4().int % 100_000_000:08d}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/auth/sms-code", json={"phone": phone})

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Sat, 05 Sep 2026 00:00:00 GMT"


async def test_legacy_login_uses_revocable_session_while_preserving_token_field():
    phone = f"136{uuid.uuid4().int % 100_000_000:08d}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/auth/sms-code", json={"phone": phone})
        response = await client.post(
            "/api/auth/login", json={"phone": phone, "code": "123456"}
        )

    assert response.status_code == 200
    assert response.json()["data"]["token"]
    assert any("ma_refresh=" in value for value in response.headers.get_list("set-cookie"))
    async with async_session_factory() as db:
        user = (await db.execute(select(User).where(User.phone == phone))).scalar_one()
        sessions = (
            await db.execute(select(AuthSession).where(AuthSession.user_id == user.id))
        ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].active_role == "student"


async def test_legacy_teacher_registration_is_retired_without_creating_identity_state():
    phone = f"137{uuid.uuid4().int % 100_000_000:08d}"
    async with async_session_factory() as db:
        users_before = await db.scalar(select(func.count()).select_from(User))
        bindings_before = await db.scalar(select(func.count()).select_from(RoleBinding))
        sessions_before = await db.scalar(select(func.count()).select_from(AuthSession))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/register/teacher",
            json={"phone": phone, "code": "123456", "name": "Legacy Teacher", "school": "School"},
        )

    assert response.status_code == 410
    assert response.json()["error_key"] == "AUTH_LEGACY_TEACHER_REGISTRATION_RETIRED"
    async with async_session_factory() as db:
        users_after = await db.scalar(select(func.count()).select_from(User))
        bindings_after = await db.scalar(select(func.count()).select_from(RoleBinding))
        sessions_after = await db.scalar(select(func.count()).select_from(AuthSession))
    assert (users_after, bindings_after, sessions_after) == (
        users_before,
        bindings_before,
        sessions_before,
    )
