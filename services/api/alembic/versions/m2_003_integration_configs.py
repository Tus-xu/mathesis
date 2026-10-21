"""M2 迁移：user_integration_configs（用户级集成配置）

Revision ID: m2_003_integration_configs
Revises: m2_002_tutor_sessions
Create Date: 2026-07-31

新增 1 张表：
- user_integration_configs（对象存储 / 星辰工作流的用户级运行时配置，
  config JSONB 存字段级覆盖，敏感字段 Fernet 加密）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m2_003_integration_configs"
down_revision = "m2_002_tutor_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 并行开发期间表可能被手动预建，存在则跳过（幂等保护）
    bind = op.get_bind()
    if "user_integration_configs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "user_integration_configs",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("user_integration_configs")
