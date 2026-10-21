"""Unified identity schema and legacy role-state migration.

Revision ID: auth_001_unified_identity
Revises: m3_002_fullstack_closure
Create Date: 2026-08-22
"""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "auth_001_unified_identity"
down_revision = "m3_002_fullstack_closure"
branch_labels = None
depends_on = None


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _approved_researcher_phones() -> list[str]:
    raw = os.getenv("AUTH_MIGRATION_APPROVED_RESEARCHER_PHONES", "")
    return [phone for phone in (item.strip() for item in raw.split(",")) if re.fullmatch(r"1\d{10}", phone)]


def upgrade() -> None:
    op.alter_column("users", "status", type_=sa.String(24), existing_type=sa.String(16))
    op.add_column("users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("onboarding_status", sa.String(16), nullable=False, server_default="required"),
    )
    op.add_column("users", sa.Column("last_active_role", sa.String(16), nullable=True))
    op.add_column(
        "users", sa.Column("security_version", sa.Integer(), nullable=False, server_default="1")
    )

    op.create_table(
        "organizations",
        _id_column(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("organization_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_timestamps(),
        sa.UniqueConstraint("name", "organization_type", name="uq_organizations_name_type"),
    )

    # The StudentProfile ORM predates its Alembic DDL. Existing developer
    # databases may have the table through metadata.create_all(), while a clean
    # Alembic database does not. Adopt it without deleting existing profile data.
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("student_profiles"):
        op.create_table(
            "student_profiles",
            _id_column(),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("weak_point_rank", postgresql.JSONB(), nullable=False, server_default="[]"),
            sa.Column("learning_style", sa.String(16), nullable=False, server_default="practice"),
            sa.Column("current_stage", sa.String(32), nullable=False, server_default=""),
            sa.Column("profile_card", sa.Text(), nullable=True),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("school_stage", sa.String(32), nullable=True),
            sa.Column("grade", sa.String(32), nullable=True),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("user_id", name="uq_student_profiles_user_id"),
        )
    else:
        op.add_column("student_profiles", sa.Column("school_stage", sa.String(32), nullable=True))
        op.add_column("student_profiles", sa.Column("grade", sa.String(32), nullable=True))
        op.add_column(
            "student_profiles",
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    op.create_foreign_key(
        "fk_student_profiles_organization",
        "student_profiles",
        "organizations",
        ["organization_id"],
        ["id"],
    )
    op.create_index("ix_student_profiles_organization", "student_profiles", ["organization_id"])

    op.add_column(
        "role_bindings",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("role_bindings", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("role_bindings", sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("role_bindings", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("role_bindings", sa.Column("suspended_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("role_bindings", sa.Column("status_reason", sa.String(512), nullable=True))
    op.create_foreign_key(
        "fk_role_bindings_approved_by", "role_bindings", "users", ["approved_by"], ["id"]
    )
    op.create_foreign_key(
        "fk_role_bindings_suspended_by", "role_bindings", "users", ["suspended_by"], ["id"]
    )

    bind.execute(sa.text("UPDATE role_bindings SET status = 'approved' WHERE role = 'student'"))
    bind.execute(
        sa.text(
            "UPDATE role_bindings SET status = CASE WHEN verified THEN 'approved' ELSE 'pending' END "
            "WHERE role IN ('teacher', 'admin')"
        )
    )
    bind.execute(sa.text("UPDATE role_bindings SET status = 'pending' WHERE role = 'researcher'"))
    phones = _approved_researcher_phones()
    for index, phone in enumerate(phones):
        bind.execute(
            sa.text(
                "UPDATE role_bindings rb SET status = 'approved' FROM users u "
                "WHERE rb.user_id = u.id AND rb.role = 'researcher' "
                f"AND u.phone = :phone_{index}"
            ),
            {f"phone_{index}": phone},
        )
    counts = bind.execute(
        sa.text("SELECT role, status, count(*) FROM role_bindings GROUP BY role, status ORDER BY role, status")
    ).fetchall()
    print(f"[auth_001] migrated role bindings: {counts}")

    op.create_table(
        "user_credentials",
        _id_column(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("credential_type", sa.String(24), nullable=False, server_default="password"),
        sa.Column("secret_hash", sa.String(512), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "credential_type", name="uq_user_credentials_user_type"),
    )
    op.create_index("ix_user_credentials_user", "user_credentials", ["user_id"])

    op.create_table(
        "auth_sessions",
        _id_column(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("security_version", sa.Integer(), nullable=False),
        sa.Column("active_role", sa.String(16), nullable=False),
        sa.Column("remember", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("device_name", sa.String(128), nullable=True),
        sa.Column("user_agent_digest", sa.String(64), nullable=True),
        sa.Column("ip_prefix", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(128), nullable=True),
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_token_family", "auth_sessions", ["token_family_id"])

    op.create_table(
        "auth_refresh_tokens",
        _id_column(),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("auth_sessions.id"), nullable=False),
        sa.Column("token_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["parent_token_id"], ["auth_refresh_tokens.id"]),
        sa.UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_hash"),
    )
    op.create_index(
        "ix_auth_refresh_tokens_family_status",
        "auth_refresh_tokens",
        ["token_family_id", "status"],
    )

    op.create_table(
        "organization_invites",
        _id_column(),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("invite_digest", sa.String(64), nullable=False),
        sa.Column("allowed_role", sa.String(16), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("invite_digest", name="uq_organization_invites_digest"),
    )
    op.create_index(
        "ix_organization_invites_org_status", "organization_invites", ["organization_id", "status"]
    )

    op.create_table(
        "role_applications",
        _id_column(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("organization_name_snapshot", sa.String(255), nullable=True),
        sa.Column("department", sa.String(128), nullable=True),
        sa.Column("staff_or_student_id", sa.String(64), nullable=True),
        sa.Column("teaching_stage", sa.String(64), nullable=True),
        sa.Column("subject", sa.String(64), nullable=True),
        sa.Column("research_direction", sa.String(255), nullable=True),
        sa.Column("evidence_file_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("files.id"), nullable=True),
        sa.Column("invite_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organization_invites.id"), nullable=True),
        sa.Column("previous_application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["previous_application_id"], ["role_applications.id"]),
    )
    op.create_index(
        "ix_role_applications_review_queue",
        "role_applications",
        ["status", "role", "submitted_at"],
    )
    op.create_index("ix_role_applications_user_role", "role_applications", ["user_id", "role"])
    op.create_index(
        "uq_role_applications_active",
        "role_applications",
        ["user_id", "role"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'needs_more_info')"),
    )

    op.create_table(
        "identity_audit_logs",
        _id_column(),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("masked_phone", sa.String(32), nullable=True),
        sa.Column("ip_prefix", sa.String(64), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_identity_audit_actor_time", "identity_audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_identity_audit_subject_time", "identity_audit_logs", ["subject_user_id", "created_at"])
    op.create_index("ix_identity_audit_event_time", "identity_audit_logs", ["event_type", "created_at"])

    op.create_table(
        "user_consents",
        _id_column(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("consent_type", sa.String(32), nullable=False),
        sa.Column("consent_version", sa.String(32), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "user_id", "consent_type", "consent_version", name="uq_user_consents_version"
        ),
    )

    op.create_table(
        "account_deletion_requests",
        _id_column(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="cooling_off"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execute_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_basis", postgresql.JSONB(), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_account_deletion_due", "account_deletion_requests", ["status", "execute_after"]
    )
    op.create_index(
        "uq_account_deletion_active",
        "account_deletion_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('cooling_off', 'processing', 'blocked_by_retention')"),
    )


def downgrade() -> None:
    op.drop_index("uq_account_deletion_active", table_name="account_deletion_requests")
    op.drop_index("ix_account_deletion_due", table_name="account_deletion_requests")
    op.drop_table("account_deletion_requests")
    op.drop_table("user_consents")
    op.drop_index("ix_identity_audit_event_time", table_name="identity_audit_logs")
    op.drop_index("ix_identity_audit_subject_time", table_name="identity_audit_logs")
    op.drop_index("ix_identity_audit_actor_time", table_name="identity_audit_logs")
    op.drop_table("identity_audit_logs")
    op.drop_index("uq_role_applications_active", table_name="role_applications")
    op.drop_index("ix_role_applications_user_role", table_name="role_applications")
    op.drop_index("ix_role_applications_review_queue", table_name="role_applications")
    op.drop_table("role_applications")
    op.drop_index("ix_organization_invites_org_status", table_name="organization_invites")
    op.drop_table("organization_invites")
    op.drop_index("ix_auth_refresh_tokens_family_status", table_name="auth_refresh_tokens")
    op.drop_table("auth_refresh_tokens")
    op.drop_index("ix_auth_sessions_token_family", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_user_credentials_user", table_name="user_credentials")
    op.drop_table("user_credentials")

    op.execute("UPDATE role_bindings SET verified = (status = 'approved')")
    op.drop_constraint("fk_role_bindings_suspended_by", "role_bindings", type_="foreignkey")
    op.drop_constraint("fk_role_bindings_approved_by", "role_bindings", type_="foreignkey")
    for column in (
        "status_reason",
        "suspended_by",
        "suspended_at",
        "approved_by",
        "approved_at",
        "status",
    ):
        op.drop_column("role_bindings", column)

    op.drop_index("ix_student_profiles_organization", table_name="student_profiles")
    op.drop_constraint("fk_student_profiles_organization", "student_profiles", type_="foreignkey")
    op.drop_column("student_profiles", "organization_id")
    op.drop_column("student_profiles", "grade")
    op.drop_column("student_profiles", "school_stage")
    op.drop_table("organizations")

    op.drop_column("users", "security_version")
    op.drop_column("users", "last_active_role")
    op.drop_column("users", "onboarding_status")
    op.drop_column("users", "phone_verified_at")
    op.alter_column("users", "status", type_=sa.String(16), existing_type=sa.String(24))
