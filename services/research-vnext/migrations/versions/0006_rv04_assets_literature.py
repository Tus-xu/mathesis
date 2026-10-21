"""RV04 · research_asset + literature tables.

Revision ID: 0006_rv04_assets_literature
Revises: 0005_rv_domain_objects
Create Date: 2026-09-14
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

revision: str = "0006_rv04_assets_literature"
down_revision: str | None = "0005_rv_domain_objects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
EMPTY_OBJ = sa.text("'{}'::jsonb")
EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "research_asset",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False, server_default="application/octet-stream"),
        sa.Column("claimed_media_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("original_filename", sa.String(512), nullable=False, server_default=""),
        sa.Column("storage_backend", sa.String(32), nullable=False, server_default="minio"),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("acl", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("lineage", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("rights", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("retention", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("scan_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("scan_detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "content_hash", name="uq_research_asset_tenant_hash"),
        schema=SCHEMA,
    )
    op.create_index("ix_research_asset_tenant", "research_asset", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_research_asset_project", "research_asset", ["project_id"], schema=SCHEMA)
    op.create_index("ix_research_asset_session", "research_asset", ["session_id"], schema=SCHEMA)
    op.create_index("ix_research_asset_owner", "research_asset", ["tenant_id", "owner_id"], schema=SCHEMA)

    op.create_table(
        "asset_signed_url",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("asset_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("jti", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index("ix_asset_signed_url_asset", "asset_signed_url", ["asset_id", "expires_at"], schema=SCHEMA)

    op.create_table(
        "literature_record",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("provider_record_id", sa.String(256), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
        sa.Column("doi", sa.String(256), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("authors", JSONB(), nullable=False, server_default=EMPTY_ARR),
        sa.Column("published_date", sa.String(32), nullable=False, server_default=""),
        sa.Column("venue", sa.Text(), nullable=False, server_default=""),
        sa.Column("abstract_available", sa.String(8), nullable=False, server_default="UNKNOWN"),
        sa.Column("fulltext_available", sa.String(8), nullable=False, server_default="UNKNOWN"),
        sa.Column("rights_status", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("rights_detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("source_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("raw_response_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("record_kind", sa.String(32), nullable=False, server_default="METADATA_ONLY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "provider", "provider_record_id", name="uq_lit_tenant_provider_rec"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_lit_tenant", "literature_record", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_lit_project", "literature_record", ["project_id"], schema=SCHEMA)
    op.create_index("ix_lit_session", "literature_record", ["session_id"], schema=SCHEMA)
    op.create_index("ix_lit_tenant_doi", "literature_record", ["tenant_id", "doi"], schema=SCHEMA)
    op.create_index(
        "ix_lit_tenant_fingerprint", "literature_record", ["tenant_id", "fingerprint"], schema=SCHEMA
    )

    op.create_table(
        "literature_dedup_conflict",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("doi", sa.String(256), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("existing_id", UUID, nullable=False),
        sa.Column("incoming_hash", sa.String(64), nullable=False),
        sa.Column("existing_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(128), nullable=False, server_default=""),
        sa.Column("detail", JSONB(), nullable=False, server_default=EMPTY_OBJ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_lit_conflict_tenant", "literature_dedup_conflict", ["tenant_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_lit_conflict_tenant", table_name="literature_dedup_conflict", schema=SCHEMA)
    op.drop_table("literature_dedup_conflict", schema=SCHEMA)
    op.drop_index("ix_lit_tenant_fingerprint", table_name="literature_record", schema=SCHEMA)
    op.drop_index("ix_lit_tenant_doi", table_name="literature_record", schema=SCHEMA)
    op.drop_index("ix_lit_session", table_name="literature_record", schema=SCHEMA)
    op.drop_index("ix_lit_project", table_name="literature_record", schema=SCHEMA)
    op.drop_index("ix_lit_tenant", table_name="literature_record", schema=SCHEMA)
    op.drop_table("literature_record", schema=SCHEMA)
    op.drop_index("ix_asset_signed_url_asset", table_name="asset_signed_url", schema=SCHEMA)
    op.drop_table("asset_signed_url", schema=SCHEMA)
    op.drop_index("ix_research_asset_owner", table_name="research_asset", schema=SCHEMA)
    op.drop_index("ix_research_asset_session", table_name="research_asset", schema=SCHEMA)
    op.drop_index("ix_research_asset_project", table_name="research_asset", schema=SCHEMA)
    op.drop_index("ix_research_asset_tenant", table_name="research_asset", schema=SCHEMA)
    op.drop_table("research_asset", schema=SCHEMA)
