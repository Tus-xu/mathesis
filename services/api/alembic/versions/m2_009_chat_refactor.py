"""M2 迁移：对话系统根本性重构（消息版本分支 + 会话置顶）

Revision ID: m2_009_chat_refactor
Revises: m2_008_question_bank
Create Date: 2026-08-11

变更（规格 .tmp/m2-chat-refactor-spec.md §1）：
- messages 新增：
  parent_id（自引用 FK，ON DELETE SET NULL，线性链）、
  superseded_at（NULL = 活动分支版本）、
  attachments JSONB（user 消息附件持久化）、
  thinking TEXT（assistant 思考内容）、
  feedback VARCHAR(8) / feedback_reason TEXT（反馈持久化）
- conversations 新增：pinned BOOLEAN NOT NULL DEFAULT FALSE
- 回填：按 (conversation_id, created_at, id) LAG 线性回填 parent_id
- 索引：ix_messages_conv_parent (conversation_id, parent_id)
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m2_009_chat_refactor"
down_revision = "m2_008_question_bank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("messages")]

    if "parent_id" not in cols:
        op.add_column(
            "messages",
            sa.Column(
                "parent_id",
                UUID(as_uuid=True),
                sa.ForeignKey("messages.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    if "superseded_at" not in cols:
        op.add_column("messages", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    if "attachments" not in cols:
        op.add_column("messages", sa.Column("attachments", JSONB, nullable=True))
    if "thinking" not in cols:
        op.add_column("messages", sa.Column("thinking", sa.Text, nullable=True))
    if "feedback" not in cols:
        op.add_column("messages", sa.Column("feedback", sa.String(8), nullable=True))
    if "feedback_reason" not in cols:
        op.add_column("messages", sa.Column("feedback_reason", sa.Text, nullable=True))

    conv_cols = [c["name"] for c in sa.inspect(bind).get_columns("conversations")]
    if "pinned" not in conv_cols:
        op.add_column(
            "conversations",
            sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        )

    # 回填线性链：每条消息的 parent = 同会话按 (created_at, id) 排序的前一条
    op.execute(
        """
        UPDATE messages m SET parent_id = sub.prev
        FROM (
            SELECT id, LAG(id) OVER (PARTITION BY conversation_id ORDER BY created_at, id) prev
            FROM messages
        ) sub
        WHERE m.id = sub.id
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_conv_parent ON messages (conversation_id, parent_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_conv_parent")
    op.drop_column("conversations", "pinned")
    op.drop_column("messages", "feedback_reason")
    op.drop_column("messages", "feedback")
    op.drop_column("messages", "thinking")
    op.drop_column("messages", "attachments")
    op.drop_column("messages", "superseded_at")
    op.drop_column("messages", "parent_id")
