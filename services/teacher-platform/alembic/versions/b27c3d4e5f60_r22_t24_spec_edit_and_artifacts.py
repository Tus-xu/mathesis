"""T24 编辑/修订/逐页 QA + 产物投递表（additive，down = f19a2b3c4d5e）

为什么需要这些表，而不是复用既有 spec JSONB：
- `change_log` 只记增量描述，撤销需要**可重放的全量快照** → `*_spec_revisions`；
- patch 必须先成为事实（proposed），apply 才是写 revision → `*_edit_proposals`；
- 逐页 QA 按 (spec, revision, page) 收敛，才能证明"局部重试没顺手改别的页" → `ppt_v2_page_qa`；
- 产物与下载需要 hash/血缘/ACL/审计，且 deck 与教案共用 → `teacher_artifact*`。

回滚：downgrade 逆序删表，均为新增表，不触碰任何既有表与数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b27c3d4e5f60"
down_revision = "f19a2b3c4d5e"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    # ── deck 修订快照 ────────────────────────────────────────────────────────
    op.create_table(
        "ppt_v2_spec_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deck_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("spec", _jsonb(), nullable=False),
        sa.Column("spec_sha256", sa.Text(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("session_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_pptspecrev_revision"),
        sa.CheckConstraint(
            "kind IN ('projection','patch','regen','revert','baseline')", name="ck_pptspecrev_kind"
        ),
        sa.UniqueConstraint("deck_id", "revision", name="uq_pptspecrev_deck_revision"),
    )
    op.create_index("ix_pptspecrev_deck", "ppt_v2_spec_revisions", ["deck_id"])

    # ── deck 编辑提案 ───────────────────────────────────────────────────────
    op.create_table(
        "ppt_v2_edit_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deck_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("source", sa.Text(), nullable=False, server_default="human"),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("operations", _jsonb(), nullable=False),
        sa.Column("refs", _jsonb(), nullable=True),
        sa.Column("affected", _jsonb(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed','applied','rejected','superseded','failed')",
            name="ck_pptedit_status",
        ),
        sa.CheckConstraint("source IN ('human','ai','regen')", name="ck_pptedit_source"),
        sa.CheckConstraint("expected_revision >= 0", name="ck_pptedit_expected"),
    )
    op.create_index("ix_pptedit_deck_status", "ppt_v2_edit_proposals", ["deck_id", "status"])

    # ── deck 逐页 QA ────────────────────────────────────────────────────────
    op.create_table(
        "ppt_v2_page_qa",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deck_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ppt_v2_deck_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("spec_revision", sa.Integer(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("defects", _jsonb(), nullable=False),
        sa.Column("metrics", _jsonb(), nullable=False),
        sa.Column("detector_version", sa.Text(), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("page_no >= 1", name="ck_pptqa_page_no"),
        sa.CheckConstraint("status IN ('pass','fail','unknown')", name="ck_pptqa_status"),
        sa.UniqueConstraint("deck_id", "spec_revision", "page_no", name="uq_pptqa_deck_rev_page"),
    )

    # ── 教案修订快照 / 编辑提案 ──────────────────────────────────────────────
    op.create_table(
        "tp_lesson_plan_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_lesson_plan_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", _jsonb(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_tplprev_revision"),
        sa.CheckConstraint(
            "kind IN ('projection','patch','regen','revert','baseline')", name="ck_tplprev_kind"
        ),
        sa.UniqueConstraint("spec_id", "revision", name="uq_tplprev_spec_revision"),
    )
    op.create_index("ix_tplprev_spec", "tp_lesson_plan_revisions", ["spec_id"])

    op.create_table(
        "tp_lesson_plan_edit_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_lesson_plan_specs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("source", sa.Text(), nullable=False, server_default="human"),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("operations", _jsonb(), nullable=False),
        sa.Column("refs", _jsonb(), nullable=True),
        sa.Column("affected", _jsonb(), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed','applied','rejected','superseded','failed')",
            name="ck_tplpedit_status",
        ),
        sa.CheckConstraint("source IN ('human','ai','regen')", name="ck_tplpedit_source"),
        sa.CheckConstraint("expected_revision >= 0", name="ck_tplpedit_expected"),
    )
    op.create_index("ix_tplpedit_spec_status", "tp_lesson_plan_edit_proposals", ["spec_id", "status"])

    # ── 产物投递：注册 / 票据 / 审计 ─────────────────────────────────────────
    op.create_table(
        "teacher_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("spec_kind", sa.Text(), nullable=False),
        sa.Column("spec_id", sa.Text(), nullable=False),
        sa.Column("spec_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_run_id", sa.Text(), nullable=False),
        sa.Column("source_lineage", _jsonb(), nullable=False),
        sa.Column("acl", _jsonb(), nullable=False),
        sa.Column("retention", _jsonb(), nullable=False),
        sa.Column("qa_status", _jsonb(), nullable=True),
        sa.Column("acl_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('pptx','pdf','docx','markdown','qa_json')", name="ck_teacher_artifact_kind"
        ),
        sa.CheckConstraint(
            "spec_kind IN ('teaching_deck','lesson_plan')", name="ck_teacher_artifact_spec_kind"
        ),
        sa.CheckConstraint("spec_revision >= 1", name="ck_teacher_artifact_spec_revision"),
        sa.UniqueConstraint("object_key", name="uq_teacher_artifact_object_key"),
    )
    op.create_index("ix_teacher_artifact_spec", "teacher_artifacts", ["spec_kind", "spec_id"])
    op.create_index("ix_teacher_artifact_owner", "teacher_artifacts", ["owner_id"])

    op.create_table(
        "teacher_artifact_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teacher_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acl_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_teacher_ticket_artifact", "teacher_artifact_tickets", ["artifact_id"])

    op.create_table(
        "teacher_artifact_download_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket", sa.Text(), nullable=True),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("decision IN ('allow','deny')", name="ck_teacher_dl_audit_decision"),
    )
    op.create_index("ix_teacher_dl_audit_artifact", "teacher_artifact_download_audit", ["artifact_id"])


def downgrade() -> None:
    op.drop_index("ix_teacher_dl_audit_artifact", table_name="teacher_artifact_download_audit")
    op.drop_table("teacher_artifact_download_audit")
    op.drop_index("ix_teacher_ticket_artifact", table_name="teacher_artifact_tickets")
    op.drop_table("teacher_artifact_tickets")
    op.drop_index("ix_teacher_artifact_owner", table_name="teacher_artifacts")
    op.drop_index("ix_teacher_artifact_spec", table_name="teacher_artifacts")
    op.drop_table("teacher_artifacts")
    op.drop_index("ix_tplpedit_spec_status", table_name="tp_lesson_plan_edit_proposals")
    op.drop_table("tp_lesson_plan_edit_proposals")
    op.drop_index("ix_tplprev_spec", table_name="tp_lesson_plan_revisions")
    op.drop_table("tp_lesson_plan_revisions")
    op.drop_table("ppt_v2_page_qa")
    op.drop_index("ix_pptedit_deck_status", table_name="ppt_v2_edit_proposals")
    op.drop_table("ppt_v2_edit_proposals")
    op.drop_index("ix_pptspecrev_deck", table_name="ppt_v2_spec_revisions")
    op.drop_table("ppt_v2_spec_revisions")
