"""r19 teaching evidence table (teacher-side interpretation evidence, typed refs)

R19（教师侧 Evidence + 版本化 ClassLearningProfile）additive 迁移：
在 8100 新增一张表 `tp_teaching_evidence`（down = 3a4b5c6d7e8f，单头推进）。

1. `tp_teaching_evidence`：教学解释证据（8100 权威事实）。
   - `value`：聚合统计数字（计数/比率/分档）——由确定性代码计算，非 LLM；
   - `refs`：typed refs（kind=submission/submission_item/assignment +
     external_id/contract_id + item_no），指向 8000 事实行，**不复制整行提交**
     （零 answer_text/附件/昵称/明文 user_id 列，PII 最小化，R19 DELTA D2/D5）；
   - `status`：valid/invalid（证据失效保留审计，不进新画像 evidence_refs）；
   - `ev_kind`：coverage/mastery/misconception/difficulty/confidence/anomaly/
     interpretation（AI 解释=ai_suggestion proposal，教师确认前不升格）；
   - `metrics_fingerprint`：所属统计指纹（重算幂等去重锚）。

设计要点（DECISIONS.md §2/§5/§6）：
- 8000 继续唯一拥有 StudentSubmission 事实；8100 只存解释证据与聚合数字；
- 教材引用零 page_refs/pNN；回滚：downgrade 逆序删表（可完整回滚）；
- 幂等：alembic 版本表保证重复 upgrade no-op。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f19a2b3c4d5e"
down_revision = "3a4b5c6d7e8f"
branch_labels = None
depends_on = None


def _jsonb() -> sa.types.TypeEngine:
    return postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "tp_teaching_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tp_class_learning_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assignment_external_id", sa.Text(), nullable=True),
        sa.Column("ev_kind", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="valid"),
        sa.Column("source", sa.Text(), nullable=False, server_default="system"),
        sa.Column("value", _jsonb(), nullable=False),
        sa.Column("refs", _jsonb(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calc_version", sa.Text(), nullable=False),
        sa.Column("metrics_fingerprint", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "ev_kind IN ('coverage','mastery','misconception','difficulty',"
            "'confidence','anomaly','interpretation')",
            name="ck_tp_tev_kind",
        ),
        sa.CheckConstraint("status IN ('valid','invalid')", name="ck_tp_tev_status"),
        sa.CheckConstraint("sample_size >= 0", name="ck_tp_tev_sample"),
    )
    op.create_index("ix_tp_tev_class", "tp_teaching_evidence", ["class_id"])
    op.create_index("ix_tp_tev_profile", "tp_teaching_evidence", ["profile_id"])
    op.create_index("ix_tp_tev_metric", "tp_teaching_evidence", ["metric_key"])


def downgrade() -> None:
    op.drop_index("ix_tp_tev_metric", table_name="tp_teaching_evidence")
    op.drop_index("ix_tp_tev_profile", table_name="tp_teaching_evidence")
    op.drop_index("ix_tp_tev_class", table_name="tp_teaching_evidence")
    op.drop_table("tp_teaching_evidence")
