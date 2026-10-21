"""Administrator invitation and review contracts."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.domains.identity.router import get_challenge_service
from app.domains.identity.service import (
    IdentityError,
    IdentityService,
    InvitationService,
    PasswordService,
)
from app.domains.identity.sessions import SessionService
from app.main import app
from app.models.database import async_session_factory
from app.models.identity import Organization, OrganizationInvite
from app.models.role_binding import RoleBinding
from app.models.user import User


async def test_one_use_invitation_is_consumed_atomically():
    async with async_session_factory() as db:
        admin = User(phone=f"133{uuid.uuid4().int % 100_000_000:08d}", nickname="管理员", onboarding_status="completed")
        first = User(phone=f"132{uuid.uuid4().int % 100_000_000:08d}", nickname="甲", onboarding_status="completed")
        second = User(phone=f"131{uuid.uuid4().int % 100_000_000:08d}", nickname="乙", onboarding_status="completed")
        org = Organization(name="并发数学研究院", organization_type="research_institute")
        db.add_all([admin, first, second, org])
        await db.flush()
        for user in (first, second):
            db.add(RoleBinding(user_id=user.id, role="student", status="approved", verified=True))
        token, invitation = await InvitationService("invite-test-pepper").create(
            db,
            admin.id,
            organization_id=org.id,
            allowed_role="researcher",
            max_uses=1,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        await db.commit()
        ids = [first.id, second.id]

    async def redeem(user_id):
        async with async_session_factory() as db:
            try:
                binding = await InvitationService("invite-test-pepper").redeem(
                    db, user_id, token, role="researcher"
                )
                await db.commit()
                return binding.status
            except IdentityError as exc:
                await db.rollback()
                return exc.error_key

    results = await asyncio.gather(*(redeem(user_id) for user_id in ids))

    assert sorted(results) == ["IDENTITY_INVITE_EXHAUSTED", "approved"]
    async with async_session_factory() as db:
        stored = await db.get(OrganizationInvite, invitation.id)
        bindings = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id.in_(ids), RoleBinding.role == "researcher"
                )
            )
        ).scalars().all()
        assert stored.used_count == 1
        assert len(bindings) == 1


async def test_admin_approval_http_requires_recent_reauthentication():
    async with async_session_factory() as db:
        admin = User(phone=f"130{uuid.uuid4().int % 100_000_000:08d}", nickname="审核员", onboarding_status="completed")
        applicant = User(phone=f"139{uuid.uuid4().int % 100_000_000:08d}", nickname="教师", onboarding_status="completed")
        db.add_all([admin, applicant])
        await db.flush()
        db.add_all(
            [
                RoleBinding(user_id=admin.id, role="admin", status="approved", verified=True),
                RoleBinding(user_id=applicant.id, role="student", status="approved", verified=True),
            ]
        )
        await PasswordService().set_password(db, admin.id, "Valid admin passphrase 2026")
        application = await IdentityService().submit_role_application(
            db, applicant.id, role="teacher", organization_name="审核中学", subject="数学"
        )
        issued = await SessionService(refresh_pepper=settings.auth_refresh_token_pepper).issue(
            db, admin, "admin", remember=False
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {issued.access_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post(
            f"/api/admin/identity/applications/{application.id}/approve",
            headers=headers,
            json={"note": "材料真实"},
        )
    assert denied.status_code == 403
    assert denied.json()["error_key"] == "AUTH_RECENT_REAUTH_REQUIRED"
    class AcceptingAdminChallenge:
        async def consume(self, challenge_id, phone, purpose, code):
            assert (challenge_id, purpose, code) == ("admin-challenge", "admin_reauth", "123456")

    app.dependency_overrides[get_challenge_service] = lambda: AcceptingAdminChallenge()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            reauthenticated = await client.post(
                "/api/auth/reauth",
                headers=headers,
                json={
                    "password": "Valid admin passphrase 2026",
                    "challenge_id": "admin-challenge",
                    "code": "123456",
                },
            )
            approved = await client.post(
                f"/api/admin/identity/applications/{application.id}/approve",
                headers=headers,
                json={"note": "材料真实"},
            )
    finally:
        app.dependency_overrides.pop(get_challenge_service, None)
    assert reauthenticated.status_code == 200
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"
