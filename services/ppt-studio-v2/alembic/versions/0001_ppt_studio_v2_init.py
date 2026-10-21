"""ppt_studio_v2 初始链（8200 独立迁移头，唯一 head）

Revision ID: 0001_ppt_studio_v2_init
Revises:
Create Date: 2026-09-11

表集合（全部属于 8200 独立库，物理隔离于 8100 teacher_platform 库）：
- ppt_v2_deck_specs / ppt_v2_assets（deck 事实权威 + 资产注册）
- ppt_v2_jobs / ppt_v2_job_events（可恢复空流程作业 + durable 事件）
- ppt_v2_outbox / ppt_v2_inbox（事务性发件箱 + 消费侧去重）

回滚：downgrade() 删除上述 6 张表（本链首个迁移，删除即回到空链）。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_ppt_studio_v2_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── deck 事实权威 ──────────────────────────────────────────────
    op.create_table(
        "ppt_v2_deck_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_pptdeck_revision"),
    )
    op.create_index("ix_pptdeck_school_schema", "ppt_v2_deck_specs", ["school_id", "schema_version"])

    op.create_table(
        "ppt_v2_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deck_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rights_status", sa.Text(), nullable=False, server_default="QUARANTINED"),
        sa.Column("rights_evidence_ref", sa.Text(), nullable=True),
        sa.Column("preserve_original", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("asset_meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('TEXTBOOK','TEACHER_UPLOAD','MATH_RENDER','AI_IMAGE')", name="ck_pptasset_kind"),
        sa.CheckConstraint("rights_status IN ('QUARANTINED','STAGED','ACTIVE')", name="ck_pptasset_rights"),
        sa.UniqueConstraint("deck_id", "object_key", name="uq_pptasset_deck_key"),
    )
    op.create_index("ix_pptasset_kind", "ppt_v2_assets", ["kind"])

    # ── 可恢复空流程 ───────────────────────────────────────────────
    op.create_table(
        "ppt_v2_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_service", sa.Text(), nullable=False, server_default="ppt_studio_v2"),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("claim_token", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timeout_s", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PENDING','CLAIMED','COMPLETED','FAILED','CANCELLED','DLQ')", name="ck_pptjobs_status"),
        sa.CheckConstraint("attempt >= 0", name="ck_pptjobs_attempt"),
        sa.CheckConstraint("revision >= 1", name="ck_pptjobs_revision"),
        sa.UniqueConstraint("job_type", "dedupe_key", name="uq_pptjobs_dedupe"),
    )
    op.create_index("ix_pptjobs_status_avail", "ppt_v2_jobs", ["status", "available_at"])
    op.create_index("ix_pptjobs_claim_expires", "ppt_v2_jobs", ["claim_expires_at"])
    op.create_index("ix_pptjobs_owner_created", "ppt_v2_jobs", ["owner_service", "created_at"])

    op.create_table(
        "ppt_v2_job_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ppt_v2_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "seq", name="uq_pptjob_events_job_seq"),
        sa.CheckConstraint("seq > 0", name="ck_pptjob_events_seq"),
    )
    op.create_index("ix_pptjob_events_job_id", "ppt_v2_job_events", ["job_id"])

    op.create_table(
        "ppt_v2_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PENDING','PUBLISHED','FAILED','DLQ')", name="ck_pptoutbox_status"),
        sa.CheckConstraint("attempt >= 0", name="ck_pptoutbox_attempt"),
        sa.UniqueConstraint("topic", "dedupe_key", name="uq_pptoutbox_dedupe"),
    )
    op.create_index("ix_pptoutbox_topic_status_avail", "ppt_v2_outbox", ["topic", "status", "available_at"])

    op.create_table(
        "ppt_v2_inbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("owner_service", sa.Text(), nullable=False, server_default="ppt_studio_v2"),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="PROCESSED"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('PROCESSED','FAILED')", name="ck_pptinbox_status"),
        sa.UniqueConstraint("dedupe_key", name="uq_pptinbox_dedupe"),
    )
    op.create_index("ix_pptinbox_created", "ppt_v2_inbox", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_pptinbox_created", table_name="ppt_v2_inbox")
    op.drop_table("ppt_v2_inbox")
    op.drop_index("ix_pptoutbox_topic_status_avail", table_name="ppt_v2_outbox")
    op.drop_table("ppt_v2_outbox")
    op.drop_index("ix_pptjob_events_job_id", table_name="ppt_v2_job_events")
    op.drop_table("ppt_v2_job_events")
    op.drop_index("ix_pptjobs_owner_created", table_name="ppt_v2_jobs")
    op.drop_index("ix_pptjobs_claim_expires", table_name="ppt_v2_jobs")
    op.drop_index("ix_pptjobs_status_avail", table_name="ppt_v2_jobs")
    op.drop_table("ppt_v2_jobs")
    op.drop_index("ix_pptasset_kind", table_name="ppt_v2_assets")
    op.drop_table("ppt_v2_assets")
    op.drop_index("ix_pptdeck_school_schema", table_name="ppt_v2_deck_specs")
    op.drop_table("ppt_v2_deck_specs")
