"""Database-backed authorization gates.

These tests catch any regression that makes token claims, rather than current
database state, the source of authorization truth.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.domains.identity.types import CurrentIdentity
from app.gateway.auth import get_current_user, require_role
from app.gateway.jwt import create_token_with_role
from app.models.database import async_session_factory
from app.models.identity import AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User


def _credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture
async def identity_db():
    async with async_session_factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def _identity_token(
    session,
    *,
    account_status: str = "active",
    binding_status: str = "approved",
    active_role: str = "teacher",
    session_active_role: str | None = None,
    user_security_version: int = 1,
    claim_security_version: int | None = None,
    revoked: bool = False,
) -> str:
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        phone=f"139{uuid.uuid4().int % 100_000_000:08d}",
        nickname="authorization test",
        status=account_status,
        onboarding_status="completed",
        security_version=user_security_version,
    )
    session.add(user)
    await session.flush()
    session.add(
        RoleBinding(
            user_id=user.id,
            role=active_role,
            status=binding_status,
            verified=binding_status == "approved",
        )
    )
    auth_session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_family_id=uuid.uuid4(),
        security_version=user_security_version,
        active_role=session_active_role or active_role,
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        expires_at=now + timedelta(days=7),
        revoked_at=now if revoked else None,
    )
    session.add(auth_session)
    await session.flush()
    return create_token_with_role(
        user_id=str(user.id),
        role=active_role,
        session_id=str(auth_session.id),
        security_version=(
            user_security_version if claim_security_version is None else claim_security_version
        ),
    )


async def test_valid_approved_identity_is_loaded_from_database(identity_db):
    token = await _identity_token(identity_db)

    identity = await get_current_user(_credentials(token), identity_db)

    assert isinstance(identity, CurrentIdentity)
    assert identity.active_role == "teacher"
    assert identity["roles"] == ["teacher"]
    assert identity["verified"] is True


async def test_suspended_account_is_denied(identity_db):
    token = await _identity_token(identity_db, account_status="suspended")

    with pytest.raises(HTTPException) as error:
        await get_current_user(_credentials(token), identity_db)

    assert error.value.status_code == 403
    assert error.value.detail["error_key"] == "AUTH_ACCOUNT_SUSPENDED"


async def test_revoked_session_is_denied(identity_db):
    token = await _identity_token(identity_db, revoked=True)

    with pytest.raises(HTTPException) as error:
        await get_current_user(_credentials(token), identity_db)

    assert error.value.status_code == 401
    assert error.value.detail["error_key"] == "AUTH_SESSION_REVOKED"


async def test_security_version_mismatch_is_denied(identity_db):
    token = await _identity_token(identity_db, user_security_version=2, claim_security_version=1)

    with pytest.raises(HTTPException) as error:
        await get_current_user(_credentials(token), identity_db)

    assert error.value.status_code == 401
    assert error.value.detail["error_key"] == "AUTH_SECURITY_VERSION_MISMATCH"


@pytest.mark.parametrize("binding_status", ["pending", "rejected", "suspended"])
async def test_non_approved_active_binding_is_denied(identity_db, binding_status):
    token = await _identity_token(identity_db, binding_status=binding_status)

    with pytest.raises(HTTPException) as error:
        await get_current_user(_credentials(token), identity_db)

    assert error.value.status_code == 403
    assert error.value.detail["error_key"] == "AUTH_ROLE_NOT_APPROVED"


async def test_token_role_must_match_session_active_role(identity_db):
    token = await _identity_token(identity_db, active_role="teacher", session_active_role="student")

    with pytest.raises(HTTPException) as error:
        await get_current_user(_credentials(token), identity_db)

    assert error.value.status_code == 401
    assert error.value.detail["error_key"] == "AUTH_SESSION_ROLE_MISMATCH"


async def test_require_role_checks_only_the_approved_active_role():
    checker = require_role("teacher", "admin")
    identity = CurrentIdentity(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        active_role="student",
        security_version=1,
    )

    with pytest.raises(HTTPException) as error:
        await checker(identity)

    assert error.value.status_code == 403
    assert error.value.detail["error_key"] == "AUTH_ROLE_REQUIRED"
