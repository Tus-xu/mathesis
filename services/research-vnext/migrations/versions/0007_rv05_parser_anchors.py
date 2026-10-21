"""RV05 · asset_parse_run + asset_anchor.

Revision ID: 0007_rv05_parser_anchors
Revises: 0006_rv04_assets_literature
Create Date: 2026-09-15
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

revision: str = "0007_rv05_parser_anchors"
down_revision: str | None = "0006_rv04_assets_literature"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "asset_parse_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("asset_id", UUID, nullable=False),
        sa.Column("asset_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("asset_content_hash", sa.String(64), nullable=False),
        sa.Column("job_id", UUID, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("parser_name", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("parser_digest", sa.String(128), nullable=False, server_default=""),
        sa.Column("parser_license", sa.String(128), nullable=False, server_default=""),
        sa.Column("parser_config", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("parser_config_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("input_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("output_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("isolation", sa.String(32), nullable=False, server_default="subprocess"),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_pages", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("failed_pages", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("missing_kinds", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("warnings", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("section_tree", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("job_id", name="uq_asset_parse_run_job"),
        schema=SCHEMA,
    )
    op.create_index("ix_asset_parse_run_tenant", "asset_parse_run", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_asset_parse_run_asset", "asset_parse_run", ["asset_id", "created_at"], schema=SCHEMA)

    op.create_table(
        "asset_anchor",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("anchor_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("asset_id", UUID, nullable=False),
        sa.Column("asset_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("parse_run_id", UUID, nullable=False),
        sa.Column("parser_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("parser_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("bbox", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("section_path", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("text_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("text_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("region_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("page_width", sa.Float(), nullable=False, server_default="0"),
        sa.Column("page_height", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("warnings", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("payload", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("anchor_id", name="uq_asset_anchor_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_asset_anchor_tenant", "asset_anchor", ["tenant_id"], schema=SCHEMA)
    op.create_index(
        "ix_asset_anchor_asset", "asset_anchor", ["asset_id", "asset_version", "content_hash"], schema=SCHEMA
    )
    op.create_index("ix_asset_anchor_parse_run", "asset_anchor", ["parse_run_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("asset_anchor", schema=SCHEMA)
    op.drop_table("asset_parse_run", schema=SCHEMA)
