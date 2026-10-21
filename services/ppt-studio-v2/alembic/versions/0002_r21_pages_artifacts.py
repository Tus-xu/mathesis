"""R21：页级 checkpoint（ppt_v2_deck_pages）+ 产物登记（ppt_v2_artifacts）+ spec 指纹列

Revision ID: 0002_r21_pages_artifacts
Revises: 0001_ppt_studio_v2_init
Create Date: 2026-09-11

回滚说明（down/rollback）：
- downgrade() 依次 DROP ppt_v2_artifacts / ppt_v2_deck_pages，
  并 DROP ppt_v2_deck_specs 的 uq_pptdeck_spec_sha 唯一约束与 spec_sha256 列；
- 不触碰 0001 的其余 6 张表与任何数据行；回滚后回到 R15 空链形态。
向后兼容：spec_sha256 为 nullable=False、server_default=''（空库场景无冲突；
旧读取方按 JSONB spec 列读取，不受影响）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_r21_pages_artifacts"
down_revision = "0001_ppt_studio_v2_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ppt_v2_deck_specs",
        sa.Column("spec_sha256", sa.Text(), nullable=False, server_default=""),
    )
    op.create_unique_constraint("uq_pptdeck_spec_sha", "ppt_v2_deck_specs", ["spec_sha256"])

    op.create_table(
        "ppt_v2_deck_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deck_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("slide_id", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="COMPLETED"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fragment_ref", sa.Text(), nullable=True),
        sa.Column("png_ref", sa.Text(), nullable=True),
        sa.Column("fragment_sha256", sa.Text(), nullable=True),
        sa.Column("png_sha256", sa.Text(), nullable=True),
        sa.Column("qa", postgresql.JSONB(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('COMPLETED','FAILED')", name="ck_pptpage_status"),
        sa.CheckConstraint("page_no >= 1", name="ck_pptpage_no"),
        sa.CheckConstraint("attempt >= 0", name="ck_pptpage_attempt"),
        sa.UniqueConstraint("deck_id", "page_no", "content_sha256", name="uq_pptpage_deck_no_hash"),
    )
    op.create_index("ix_pptpage_deck_status", "ppt_v2_deck_pages", ["deck_id", "status"])

    op.create_table(
        "ppt_v2_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deck_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("artifact_meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('DECK_PPTX','PAGE_FRAGMENT','PAGE_PNG','CONTACT_SHEET','ASSET')",
            name="ck_pptartifact_kind",
        ),
        sa.UniqueConstraint("object_key", name="uq_pptartifact_object_key"),
    )
    op.create_index("ix_pptartifact_deck", "ppt_v2_artifacts", ["deck_id"])
    op.create_index("ix_pptartifact_job", "ppt_v2_artifacts", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_pptartifact_job", table_name="ppt_v2_artifacts")
    op.drop_index("ix_pptartifact_deck", table_name="ppt_v2_artifacts")
    op.drop_table("ppt_v2_artifacts")
    op.drop_index("ix_pptpage_deck_status", table_name="ppt_v2_deck_pages")
    op.drop_table("ppt_v2_deck_pages")
    op.drop_constraint("uq_pptdeck_spec_sha", "ppt_v2_deck_specs", type_="unique")
    op.drop_column("ppt_v2_deck_specs", "spec_sha256")
    # 回滚后回到 R15 空链形态（0001）
