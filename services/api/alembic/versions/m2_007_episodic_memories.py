"""M2 迁移：episodic_memories 情景记忆激活

Revision ID: m2_007_episodic_memories
Revises: m2_006_mastery_snapshots
Create Date: 2026-08-07

变更（情景记忆 mem0 简化版落地的表结构补齐）：
- 新增 kind（weak_kp/preference/goal/note）、source（chat/quiz/manual）、
  conversation_id（软引用不建 FK）三列
- embedding 允许 NULL（embedding 服务不可用时尽力而为落 NULL，读路径降级排序）
- 新增活跃行部分索引 (user_id, created_at DESC) WHERE deleted_at IS NULL
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "m2_007_episodic_memories"
down_revision = "m2_006_mastery_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns("episodic_memories")]

    if "kind" not in cols:
        op.add_column(
            "episodic_memories",
            sa.Column("kind", sa.String(20), nullable=False, server_default="note"),
        )
    if "source" not in cols:
        op.add_column(
            "episodic_memories",
            sa.Column("source", sa.String(20), nullable=False, server_default="chat"),
        )
    if "conversation_id" not in cols:
        op.add_column(
            "episodic_memories",
            sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        )

    # embedding 尽力而为：允许 NULL（读路径降级为"最近 + kind 优先级"排序）
    op.alter_column("episodic_memories", "embedding", nullable=True)

    # 活跃行检索索引（幂等）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_memories_user_active "
        "ON episodic_memories (user_id, created_at DESC) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_episodic_memories_user_active")
    op.alter_column("episodic_memories", "embedding", nullable=False)
    op.drop_column("episodic_memories", "conversation_id")
    op.drop_column("episodic_memories", "source")
    op.drop_column("episodic_memories", "kind")
