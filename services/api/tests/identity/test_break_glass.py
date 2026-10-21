"""Offline break-glass and retention contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domains.identity.retention import RetentionService
from app.models.database import async_session_factory
from app.models.identity import IdentityAuditLog, UserCredential
from app.models.user import User
from scripts.identity_break_glass import BreakGlassError, issue_break_glass


async def test_break_glass_requires_enabled_trusted_two_person_operation():
    async with async_session_factory() as db:
        user = User(
            phone=f"135{uuid.uuid4().int % 100_000_000:08d}",
            nickname="恢复目标",
            phone_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
        with pytest.raises(BreakGlassError, match="disabled"):
            await issue_break_glass(
                db,
                enabled=False,
                trusted_environment=True,
                deployment_secret="correct-secret",
                supplied_secret="correct-secret",
                phone=user.phone,
                work_order="WO-2026-001",
                operator_one="alice",
                operator_two="bob",
            )
        token = await issue_break_glass(
            db,
            enabled=True,
            trusted_environment=True,
            deployment_secret="correct-secret",
            supplied_secret="correct-secret",
            phone=user.phone,
            work_order="WO-2026-001",
            operator_one="alice",
            operator_two="bob",
        )
        await db.commit()

    assert len(token) >= 32
    async with async_session_factory() as db:
        credential = await db.scalar(
            select(UserCredential).where(
                UserCredential.user_id == user.id,
                UserCredential.credential_type == "break_glass",
            )
        )
        audit = await db.scalar(
            select(IdentityAuditLog).where(
                IdentityAuditLog.subject_user_id == user.id,
                IdentityAuditLog.event_type == "break_glass.issued",
            )
        )
        assert credential.secret_hash != token
        assert credential.locked_until <= datetime.now(UTC) + timedelta(minutes=15)
        assert audit.details["operators"] == ["alice", "bob"]


async def test_retention_keeps_security_audit_for_at_least_180_days():
    now = datetime.now(UTC)
    service = RetentionService(now=lambda: now)
    assert service.classify_audit(now - timedelta(days=179), "auth.login") == "hot_or_archive"
    assert service.classify_audit(now - timedelta(days=181), "auth.login") == "delete_detail"
    assert service.classify_audit(now - timedelta(days=700), "role_application.approved") == "retain"
    assert service.classify_audit(now - timedelta(days=731), "role_application.approved") == "delete_detail"
