"""Password policy, Argon2id credentials, and security-version behavior."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.domains.identity.router import get_challenge_service
from app.domains.identity.security import PasswordHasher, PasswordPolicyError
from app.domains.identity.service import PasswordService
from app.gateway.jwt import create_token_with_role
from app.main import app
from app.models.database import async_session_factory
from app.models.identity import AuthSession, RoleApplication, UserCredential
from app.models.role_binding import RoleBinding
from app.models.user import User


@pytest.fixture(scope="module")
def password_hasher() -> PasswordHasher:
    return PasswordHasher()


@pytest.mark.parametrize("length", [1, 8, 14, 129])
def test_password_policy_rejects_out_of_range_lengths(password_hasher, length):
    with pytest.raises(PasswordPolicyError) as error:
        password_hasher.hash("数" * length)
    assert error.value.error_key == "AUTH_PASSWORD_POLICY"


@pytest.mark.parametrize("length", [15, 64, 128])
def test_password_policy_accepts_nist_length_boundaries(password_hasher, length):
    encoded = password_hasher.hash("数" * length)
    assert encoded.startswith("$argon2id$")
    assert password_hasher.verify_and_rehash("数" * length, encoded).valid is True


def test_password_policy_accepts_spaces_unicode_and_normalizes_nfc(password_hasher):
    composed = "é" * 14 + " pass phrase"
    decomposed = "e\u0301" * 14 + " pass phrase"
    encoded = password_hasher.hash(composed)

    assert password_hasher.verify_and_rehash(decomposed, encoded).valid is True


def test_password_policy_rejects_local_blocklist(password_hasher):
    with pytest.raises(PasswordPolicyError) as error:
        password_hasher.hash("password123456789")
    assert error.value.error_key == "AUTH_PASSWORD_BLOCKED"


def test_wrong_password_does_not_verify(password_hasher):
    encoded = password_hasher.hash("correct horse battery staple")
    assert password_hasher.verify_and_rehash("wrong password value", encoded).valid is False


@pytest.fixture
async def password_db():
    async with async_session_factory() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            if transaction.is_active:
                await transaction.rollback()


async def _user_with_session(db):
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        phone=f"137{uuid.uuid4().int % 100_000_000:08d}",
        nickname="password test",
        onboarding_status="completed",
    )
    db.add(user)
    await db.flush()
    db.add(RoleBinding(user_id=user.id, role="student", status="approved", verified=True))
    auth_session = AuthSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token_family_id=uuid.uuid4(),
        security_version=1,
        active_role="student",
        last_seen_at=now,
        idle_expires_at=now + timedelta(hours=1),
        expires_at=now + timedelta(days=7),
    )
    db.add(auth_session)
    await db.flush()
    return user, auth_session


async def test_set_and_authenticate_password_uses_credentials_table(password_db, password_hasher):
    user, _ = await _user_with_session(password_db)
    service = PasswordService(password_hasher)

    await service.set_password(password_db, user.id, "correct horse battery staple")
    authenticated = await service.authenticate(
        password_db, user.phone, "correct horse battery staple"
    )

    credential = (
        await password_db.execute(
            select(UserCredential).where(UserCredential.user_id == user.id)
        )
    ).scalar_one()
    assert credential.secret_hash.startswith("$argon2id$")
    assert authenticated.id == user.id
    assert user.password_hash is None


async def test_password_reset_increments_security_version_and_revokes_sessions(
    password_db, password_hasher
):
    user, auth_session = await _user_with_session(password_db)
    service = PasswordService(password_hasher)
    await service.set_password(password_db, user.id, "first secure password value")

    await service.reset_password(password_db, user.id, "second secure password value")
    await password_db.refresh(user)
    await password_db.refresh(auth_session)

    assert user.security_version == 2
    assert auth_session.revoked_at is not None
    assert auth_session.revoke_reason == "password_reset"
    authenticated = await service.authenticate(
        password_db, user.phone, "second secure password value"
    )
    assert authenticated.id == user.id


class AcceptingPasswordResetChallenge:
    async def consume(self, challenge_id: str, phone: str, purpose: str, code: str) -> None:
        if (
            challenge_id != "password-reset-challenge"
            or purpose != "password_reset"
            or code != "123456"
        ):
            raise AssertionError("password reset used the wrong challenge contract")


async def test_password_endpoints_set_login_and_reset(password_db):
    user, _ = await _user_with_session(password_db)
    await password_db.commit()
    token = create_token_with_role(str(user.id), "student")
    headers = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[get_challenge_service] = lambda: AcceptingPasswordResetChallenge()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            set_response = await client.post(
                "/api/auth/password/set",
                json={"password": "correct horse battery staple"},
                headers=headers,
            )
            assert set_response.status_code == 200
            assert set_response.json()["code"] == 0

            login_response = await client.post(
                "/api/auth/login/password",
                json={"phone": user.phone, "password": "correct horse battery staple"},
            )
            assert login_response.status_code == 200
            assert login_response.json()["data"]["access_token"]

            reset_response = await client.post(
                "/api/auth/password/reset",
                json={
                    "phone": user.phone,
                    "challenge_id": "password-reset-challenge",
                    "code": "123456",
                    "password": "second secure password value",
                },
            )
            assert reset_response.status_code == 200
            assert reset_response.json()["code"] == 0

            unknown_reset = await client.post(
                "/api/auth/password/reset",
                json={
                    "phone": f"135{uuid.uuid4().int % 100_000_000:08d}",
                    "challenge_id": "password-reset-challenge",
                    "code": "123456",
                    "password": "second secure password value",
                },
            )
            assert unknown_reset.status_code == reset_response.status_code
            assert unknown_reset.json()["code"] == reset_response.json()["code"]
            assert unknown_reset.json()["message"] == reset_response.json()["message"]
            assert unknown_reset.json()["data"] == reset_response.json()["data"]
    finally:
        app.dependency_overrides.pop(get_challenge_service, None)


async def _password_user_with_roles(db, password_hasher, bindings, *, application_status=None):
    user, _ = await _user_with_session(db)
    for role, status in bindings:
        if role == "student":
            continue
        db.add(
            RoleBinding(
                user_id=user.id,
                role=role,
                status=status,
                _legacy_verified=status == "approved",
            )
        )
    if application_status is not None:
        db.add(
            RoleApplication(
                user_id=user.id,
                role="researcher",
                status=application_status,
                organization_name_snapshot="数研院",
                research_direction="代数",
                submitted_at=datetime.now(UTC),
            )
        )
    await PasswordService(password_hasher).set_password(
        db, user.id, "correct horse battery staple"
    )
    await db.commit()
    return user


async def test_password_login_selected_approved_researcher_issues_researcher_session(
    password_db, password_hasher
):
    user = await _password_user_with_roles(
        password_db, password_hasher, [("student", "approved"), ("researcher", "approved")]
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login/password",
            json={
                "phone": user.phone,
                "password": "correct horse battery staple",
                "preferred_role": "researcher",
            },
        )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["active_role"] == "researcher"
    assert response.json()["data"]["identity_status"] == "authenticated"


@pytest.mark.parametrize(
    ("binding_status", "application_status", "expected_role", "expected_status", "expected_pending"),
    [
        ("pending", "pending", "researcher", "authenticated", None),
        ("pending", "needs_more_info", "researcher", "authenticated", None),
        ("rejected", "rejected", "student", "rejected", "researcher"),
    ],
)
async def test_password_login_restores_pending_professional_but_keeps_rejected_identity_blocked(
    password_db, password_hasher, binding_status, application_status, expected_role, expected_status, expected_pending
):
    user = await _password_user_with_roles(
        password_db,
        password_hasher,
        [("student", "approved"), ("researcher", binding_status)],
        application_status=application_status,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login/password",
            json={
                "phone": user.phone,
                "password": "correct horse battery staple",
                "preferred_role": "researcher",
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"]["active_role"] == expected_role
    assert data["identity_status"] == expected_status
    assert data.get("pending_role") == expected_pending
    researcher = next(role for role in data["user"]["roles"] if role["role"] == "researcher")
    assert researcher["verified"] is (expected_role == "researcher")
