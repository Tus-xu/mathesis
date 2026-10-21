"""Retain pending professional login targets on auth sessions.

Revision ID: auth_002_role_selective_sms
Revises: auth_001_unified_identity
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "auth_002_role_selective_sms"
down_revision = "auth_001_unified_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column("pending_role", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_sessions", "pending_role")
