"""Persistence models for authentication and identity lifecycle."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserCredential(Base, TimestampMixin):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "credential_type", name="uq_user_credentials_user_type"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    credential_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="password", server_default="password"
    )
    secret_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base, TimestampMixin):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),
        Index("ix_auth_sessions_token_family", "token_family_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_family_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid.uuid4)
    security_version: Mapped[int] = mapped_column(Integer, nullable=False)
    active_role: Mapped[str] = mapped_column(String(16), nullable=False)
    pending_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    remember: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthRefreshToken(Base, TimestampMixin):
    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_hash"),
        Index("ix_auth_refresh_tokens_family_status", "token_family_id", "status"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("auth_sessions.id"), nullable=False)
    token_family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    parent_token_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_refresh_tokens.id"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )


class OrganizationInvite(Base, TimestampMixin):
    __tablename__ = "organization_invites"
    __table_args__ = (
        UniqueConstraint("invite_digest", name="uq_organization_invites_digest"),
        Index("ix_organization_invites_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False
    )
    invite_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    allowed_role: Mapped[str] = mapped_column(String(16), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoleApplication(Base, TimestampMixin):
    __tablename__ = "role_applications"
    __table_args__ = (
        Index("ix_role_applications_review_queue", "status", "role", "submitted_at"),
        Index("ix_role_applications_user_role", "user_id", "role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    organization_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    staff_or_student_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    teaching_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(64), nullable=True)
    research_direction: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id"), nullable=True)
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization_invites.id"), nullable=True
    )
    previous_application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("role_applications.id"), nullable=True
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class IdentityAuditLog(Base, TimestampMixin):
    __tablename__ = "identity_audit_logs"
    __table_args__ = (
        Index("ix_identity_audit_actor_time", "actor_user_id", "created_at"),
        Index("ix_identity_audit_subject_time", "subject_user_id", "created_at"),
        Index("ix_identity_audit_event_time", "event_type", "created_at"),
    )

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    masked_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ip_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class UserConsent(Base, TimestampMixin):
    __tablename__ = "user_consents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "consent_type", "consent_version", name="uq_user_consents_version"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)


class AccountDeletionRequest(Base, TimestampMixin):
    __tablename__ = "account_deletion_requests"
    __table_args__ = (Index("ix_account_deletion_due", "status", "execute_after"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="cooling_off", server_default="cooling_off"
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    execute_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
