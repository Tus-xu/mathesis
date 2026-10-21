"""Identity persistence contracts.

Each assertion protects a state or uniqueness rule used by authorization and
account lifecycle services.  The tests intentionally exercise real SQLAlchemy
metadata instead of checking source text.
"""

import uuid

from app.models.identity import (
    AccountDeletionRequest,
    AuthRefreshToken,
    AuthSession,
    IdentityAuditLog,
    Organization,
    OrganizationInvite,
    RoleApplication,
    UserConsent,
    UserCredential,
)
from app.models.role_binding import RoleBinding
from app.models.student_profile import StudentProfile
from app.models.user import User


def _unique_column_sets(model: type) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }


def test_user_security_defaults_require_onboarding():
    user = User(phone="13800138000")

    assert user.status == "active"
    assert user.onboarding_status == "required"
    assert user.security_version == 1
    assert user.phone_verified_at is None
    assert user.last_active_role is None


def test_role_binding_verified_is_derived_from_status():
    approved = RoleBinding(user_id=uuid.uuid4(), role="teacher", status="approved")
    pending = RoleBinding(user_id=uuid.uuid4(), role="researcher", status="pending")

    assert approved.verified is True
    assert pending.verified is False

    pending.verified = True
    assert pending.status == "approved"
    pending.verified = False
    assert pending.status == "pending"


def test_role_binding_preserves_one_binding_per_user_and_role():
    assert ("user_id", "role") in _unique_column_sets(RoleBinding)


def test_identity_models_publish_expected_table_names():
    assert {
        UserCredential.__tablename__,
        AuthSession.__tablename__,
        AuthRefreshToken.__tablename__,
        RoleApplication.__tablename__,
        Organization.__tablename__,
        OrganizationInvite.__tablename__,
        IdentityAuditLog.__tablename__,
        UserConsent.__tablename__,
        AccountDeletionRequest.__tablename__,
    } == {
        "user_credentials",
        "auth_sessions",
        "auth_refresh_tokens",
        "role_applications",
        "organizations",
        "organization_invites",
        "identity_audit_logs",
        "user_consents",
        "account_deletion_requests",
    }


def test_identity_models_enforce_security_uniqueness_boundaries():
    assert ("user_id", "credential_type") in _unique_column_sets(UserCredential)
    assert ("user_id", "consent_type", "consent_version") in _unique_column_sets(UserConsent)
    assert ("token_hash",) in _unique_column_sets(AuthRefreshToken)
    assert ("invite_digest",) in _unique_column_sets(OrganizationInvite)


def test_student_profile_can_capture_onboarding_without_a_class_claim():
    profile_columns = set(StudentProfile.__table__.columns.keys())

    assert {"school_stage", "grade", "organization_id"}.issubset(profile_columns)
    assert "class_id" not in profile_columns
