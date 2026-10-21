"""Rotating refresh sessions, CSRF, cookie, and concurrency contracts."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.domains.identity.sessions import (
    IssuedSession,
    SessionError,
    SessionService,
    set_session_cookies,
    validate_csrf,
)
from app.gateway.jwt import decode_token
from app.main import app
from app.models.database import async_session_factory
from app.models.identity import AuthRefreshToken, AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User


async def _create_user(db, role: str = "student") -> User:
    user = User(
        id=uuid.uuid4(),
        phone=f"136{uuid.uuid4().int % 100_000_000:08d}",
        nickname="session test",
        onboarding_status="completed",
    )
    db.add(user)
    await db.flush()
    db.add(RoleBinding(user_id=user.id, role=role, status="approved", verified=True))
    await db.flush()
    return user


async def approved_student(db) -> User:
    return await _create_user(db, "student")


@pytest.fixture
async def session_db():
    async with async_session_factory() as db:
        transaction = await db.begin()
        try:
            yield db
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def test_issue_session_uses_short_access_and_hashed_refresh(session_db):
    user = await _create_user(session_db)
    service = SessionService(refresh_pepper="test-refresh-pepper")

    issued = await service.issue(session_db, user, "student", remember=False)
    claims = decode_token(issued.access_token)
    stored = (
        await session_db.execute(
            select(AuthRefreshToken).where(AuthRefreshToken.session_id == issued.session_id)
        )
    ).scalar_one()

    assert claims["sid"] == str(issued.session_id)
    assert claims["sv"] == 1
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert stored.token_hash != issued.refresh_token
    assert len(stored.token_hash) == 64


async def test_session_preserves_pending_role_across_refresh(session_db):
    user = await approved_student(session_db)
    issued = await SessionService(refresh_pepper="test-pepper").issue(
        session_db, user, "student", remember=False, pending_role="teacher"
    )
    await session_db.flush()
    stored = await session_db.get(AuthSession, issued.session_id)
    assert stored.active_role == "student"
    assert stored.pending_role == "teacher"


async def test_session_rejects_admin_pending_role_without_persisting(session_db):
    user = await approved_student(session_db)

    with pytest.raises(ValueError, match="pending role"):
        await SessionService(refresh_pepper="test-pepper").issue(
            session_db, user, "student", remember=False, pending_role="admin"
        )

    stored = await session_db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    assert stored is None


@pytest.mark.parametrize(
    ("role", "remember", "absolute", "idle"),
    [
        ("student", False, timedelta(days=7), timedelta(hours=24)),
        ("student", True, timedelta(days=30), timedelta(days=7)),
        ("admin", False, timedelta(hours=12), timedelta(minutes=30)),
    ],
)
async def test_session_expiry_policy(session_db, role, remember, absolute, idle):
    user = await _create_user(session_db, role)
    now = datetime.now(UTC)
    service = SessionService(refresh_pepper="test-refresh-pepper", now=lambda: now)

    issued = await service.issue(session_db, user, role, remember=remember)
    auth_session = await session_db.get(AuthSession, issued.session_id)

    assert auth_session.expires_at == now + absolute
    assert auth_session.idle_expires_at == now + idle


async def test_refresh_rotation_marks_parent_used_and_creates_child(session_db):
    user = await _create_user(session_db)
    service = SessionService(refresh_pepper="test-refresh-pepper")
    first = await service.issue(session_db, user, "student", remember=False)

    second = await service.rotate(session_db, first.refresh_token)
    tokens = (
        await session_db.execute(
            select(AuthRefreshToken)
            .where(AuthRefreshToken.session_id == first.session_id)
            .order_by(AuthRefreshToken.issued_at)
        )
    ).scalars().all()

    assert len(tokens) == 2
    assert tokens[0].status == "used"
    assert tokens[1].status == "active"
    assert tokens[1].parent_token_id == tokens[0].id
    assert second.refresh_token != first.refresh_token


async def test_replaying_used_refresh_revokes_the_family(session_db):
    user = await _create_user(session_db)
    # 宽限=0：模拟"远超并发窗口"的真实重放，仍必须吊销 family。
    service = SessionService(refresh_pepper="test-refresh-pepper", rotation_grace_seconds=0)
    first = await service.issue(session_db, user, "student", remember=False)
    await service.rotate(session_db, first.refresh_token)

    with pytest.raises(SessionError) as replay:
        await service.rotate(session_db, first.refresh_token)

    auth_session = await session_db.get(AuthSession, first.session_id)
    assert replay.value.error_key == "AUTH_REFRESH_REPLAYED"
    assert auth_session.revoked_at is not None
    assert auth_session.revoke_reason == "refresh_replay"


async def test_same_refresh_concurrency_within_grace_allows_both():
    """两个标签页同时刷新：宽限窗口内各发一个 child，不吊销 family。"""
    service = SessionService(refresh_pepper="test-refresh-pepper")
    async with async_session_factory() as setup_db:
        user = await _create_user(setup_db)
        issued = await service.issue(setup_db, user, "student", remember=False)
        await setup_db.commit()

    async def rotate_once():
        async with async_session_factory() as db:
            value = await service.rotate(db, issued.refresh_token, csrf_token="csrf-stable")
            await db.commit()
            return value

    first, second = await asyncio.gather(rotate_once(), rotate_once())
    assert first.refresh_token != second.refresh_token
    assert first.csrf_token == second.csrf_token == "csrf-stable"
    async with async_session_factory() as verify_db:
        auth_session = await verify_db.get(AuthSession, issued.session_id)
        assert auth_session.revoked_at is None


def test_csrf_requires_matching_cookie_and_header():
    validate_csrf("csrf-token", "csrf-token")
    with pytest.raises(SessionError) as missing:
        validate_csrf("csrf-token", None)
    assert missing.value.error_key == "AUTH_CSRF_INVALID"
    with pytest.raises(SessionError):
        validate_csrf("csrf-token", "different-token")


def test_session_cookies_use_secure_http_only_boundaries():
    response = Response()
    issued = IssuedSession(
        session_id=uuid.uuid4(),
        access_token="access-token",
        refresh_token="refresh-token",
        csrf_token="csrf-token",
        access_expires_in=900,
        refresh_expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    set_session_cookies(response, issued, secure=True)
    headers = response.headers.getlist("set-cookie")

    refresh = next(value for value in headers if value.startswith("ma_refresh="))
    csrf = next(value for value in headers if value.startswith("ma_csrf="))
    assert "HttpOnly" in refresh
    assert "Secure" in refresh
    assert "SameSite=lax" in refresh
    assert "Path=/api/auth" in refresh
    assert "HttpOnly" not in csrf
    assert "Secure" in csrf
    assert "Path=/" in csrf


async def test_role_switch_preserves_revocable_session(session_db):
    user = await _create_user(session_db)
    session_db.add(RoleBinding(user_id=user.id, role="teacher", status="approved", verified=True))
    service = SessionService(refresh_pepper=settings.auth_refresh_token_pepper)
    issued = await service.issue(session_db, user, "student", remember=False)
    await session_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/role/switch",
            headers={"Authorization": f"Bearer {issued.access_token}"},
            json={"role": "teacher"},
        )

    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    claims = decode_token(token)
    assert claims["sid"] == str(issued.session_id)
    assert claims["active_role"] == "teacher"
    async with async_session_factory() as db:
        stored = await db.get(AuthSession, issued.session_id)
        assert stored.active_role == "teacher"


async def test_switch_to_approved_role_clears_pending_role(session_db):
    user = await _create_user(session_db)
    session_db.add(RoleBinding(user_id=user.id, role="teacher", status="approved", verified=True))
    issued = await SessionService(refresh_pepper=settings.auth_refresh_token_pepper).issue(
        session_db, user, "student", remember=False, pending_role="teacher"
    )
    await session_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/role/switch",
            headers={"Authorization": f"Bearer {issued.access_token}"},
            json={"role": "teacher"},
        )

    assert response.status_code == 200
    async with async_session_factory() as db:
        stored = await db.get(AuthSession, issued.session_id)
        assert stored.pending_role is None


async def test_me_reports_pending_role_without_elevating_active_role(session_db):
    user = await _create_user(session_db)
    session_db.add(RoleBinding(user_id=user.id, role="teacher", status="pending", verified=False))
    issued = await SessionService(refresh_pepper=settings.auth_refresh_token_pepper).issue(
        session_db, user, "student", remember=False, pending_role="teacher"
    )
    await session_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {issued.access_token}"}
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["active_role"] == "student"
    assert data["pending_role"] == "teacher"
    assert data["identity_status"] == "pending_review"


async def test_refresh_sessions_and_logout_http_contract(session_db):
    user = await _create_user(session_db)
    service = SessionService(refresh_pepper=settings.auth_refresh_token_pepper)
    issued = await service.issue(session_db, user, "student", remember=False)
    await session_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set(
            "ma_refresh", issued.refresh_token, domain="test.local", path="/api/auth"
        )
        client.cookies.set(
            "ma_csrf", issued.csrf_token, domain="test.local", path="/"
        )

        denied = await client.post(
            "/api/auth/token/refresh",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert denied.status_code == 403
        assert denied.json()["error_key"] == "AUTH_CSRF_INVALID"

        refreshed = await client.post(
            "/api/auth/token/refresh",
            headers={"X-CSRF-Token": issued.csrf_token},
        )
        assert refreshed.status_code == 200
        body = refreshed.json()
        assert body["data"]["access_token"]
        assert body["data"]["expires_in"] == 900
        assert any(
            "ma_refresh=" in value and "HttpOnly" in value
            for value in refreshed.headers.get_list("set-cookie")
        )

        sessions = await client.get(
            "/api/auth/sessions",
            headers={"Authorization": f"Bearer {body['data']['access_token']}"},
        )
        assert sessions.status_code == 200
        assert sessions.json()["data"][0]["id"] == str(issued.session_id)

        csrf = client.cookies.get("ma_csrf")
        logged_out = await client.post(
            "/api/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert logged_out.status_code == 200

    async with async_session_factory() as verify_db:
        auth_session = await verify_db.get(AuthSession, issued.session_id)
        assert auth_session.revoked_at is not None
        assert auth_session.revoke_reason == "logout"


async def test_scoped_refresh_cookies_coexist_for_teacher_and_student(session_db):
    """同一浏览器/不同标签页：教师与学生各自刷新，互不覆盖。"""
    user = await _create_user(session_db)
    session_db.add(RoleBinding(user_id=user.id, role="teacher", status="approved", verified=True))
    await session_db.flush()
    service = SessionService(refresh_pepper=settings.auth_refresh_token_pepper)
    teacher = await service.issue(session_db, user, "teacher", remember=False)
    student = await service.issue(session_db, user, "student", remember=False)
    await session_db.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for scope, issued in (("teacher", teacher), ("student", student)):
            client.cookies.set(
                f"ma_refresh_{scope}", issued.refresh_token, domain="test.local", path="/api/auth"
            )
            client.cookies.set(
                f"ma_csrf_{scope}", issued.csrf_token, domain="test.local", path="/"
            )

        teacher_refresh = await client.post(
            "/api/auth/token/refresh",
            headers={"X-CSRF-Token": teacher.csrf_token, "X-Auth-Scope": "teacher"},
        )
        assert teacher_refresh.status_code == 200, teacher_refresh.text
        assert teacher_refresh.json()["data"]["active_role"] == "teacher"
        # CSRF 不随刷新轮换：再用同一个 token 刷新学生仍可成功
        student_refresh = await client.post(
            "/api/auth/token/refresh",
            headers={"X-CSRF-Token": student.csrf_token, "X-Auth-Scope": "student"},
        )
        assert student_refresh.status_code == 200, student_refresh.text
        assert student_refresh.json()["data"]["active_role"] == "student"

        cookies = teacher_refresh.headers.get_list("set-cookie")
        assert any(value.startswith("ma_refresh_teacher=") for value in cookies)
        assert any("ma_csrf_teacher=" in value for value in cookies)
