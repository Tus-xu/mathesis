"""Teacher/researcher application state-machine contracts."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.domains.identity.service import IdentityError, IdentityService
from app.domains.identity.sessions import SessionService
from app.models.database import async_session_factory
from app.models.identity import AuthSession, IdentityAuditLog
from app.models.role_binding import RoleBinding
from app.models.user import User


async def _user(db, role="student"):
    user = User(phone=f"134{uuid.uuid4().int % 100_000_000:08d}", nickname="申请人", onboarding_status="completed")
    db.add(user)
    await db.flush()
    db.add(RoleBinding(user_id=user.id, role=role, status="approved", verified=True))
    await db.flush()
    return user


async def test_teacher_application_is_pending_until_admin_approval():
    async with async_session_factory() as db:
        applicant = await _user(db)
        admin = await _user(db, "admin")
        application = await IdentityService().submit_role_application(
            db,
            applicant.id,
            role="teacher",
            organization_name="示例中学",
            department="数学组",
            staff_or_student_id="T-1001",
            teaching_stage="高中",
            subject="数学",
        )
        issued = await SessionService(refresh_pepper="role-test-pepper").issue(
            db, applicant, "student", remember=False, pending_role="teacher"
        )
        await db.commit()

    async with async_session_factory() as db:
        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == applicant.id, RoleBinding.role == "teacher"
                )
            )
        ).scalar_one()
        assert (application.status, binding.status) == ("pending", "pending")
        reviewed = await IdentityService().review_role_application(
            db, application.id, admin.id, decision="approved", note="材料有效"
        )
        await db.commit()

    async with async_session_factory() as db:
        binding = await db.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == applicant.id, RoleBinding.role == "teacher"
            )
        )
        session = await db.get(AuthSession, issued.session_id)
        audit = await db.scalar(
            select(IdentityAuditLog).where(
                IdentityAuditLog.subject_user_id == applicant.id,
                IdentityAuditLog.event_type == "role_application.approved",
            )
        )
        assert reviewed.status == "approved"
        assert binding.status == "approved"
        assert session.revoked_at is not None
        assert session.pending_role == "teacher"
        assert audit.actor_user_id == admin.id


async def test_admin_cannot_review_own_application():
    async with async_session_factory() as db:
        user = await _user(db)
        application = await IdentityService().submit_role_application(
            db, user.id, role="researcher", organization_name="数研院", research_direction="代数"
        )
        with pytest.raises(IdentityError, match="不能审核自己的申请") as exc:
            await IdentityService().review_role_application(
                db, application.id, user.id, decision="approved", note=None
            )
        assert exc.value.error_key == "IDENTITY_SELF_REVIEW_DENIED"


async def test_pending_application_cannot_be_submitted_twice():
    async with async_session_factory() as db:
        user = await _user(db)
        service = IdentityService()
        await service.submit_role_application(
            db, user.id, role="teacher", organization_name="一中", subject="数学"
        )
        with pytest.raises(IdentityError) as exc:
            await service.submit_role_application(
                db, user.id, role="teacher", organization_name="二中", subject="数学"
            )
        assert exc.value.error_key == "IDENTITY_APPLICATION_ALREADY_PENDING"
