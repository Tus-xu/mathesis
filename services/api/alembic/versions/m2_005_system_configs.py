"""M2 迁移：system_configs（管理后台系统级配置）

Revision ID: m2_005_system_configs
Revises: m2_004_courses
Create Date: 2026-08-06

新增 1 张表：
- system_configs（管理后台维护的全局 KV 配置：model.global / xingchen.global /
  cloud_kb / workflows 等，敏感字段 Fernet 加密后存 JSONB）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "m2_005_system_configs"
down_revision = "m2_004_courses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 并行开发期间表可能被手动预建，存在则跳过（幂等保护）
    bind = op.get_bind()
    if "system_configs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "system_configs",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB, nullable=False, server_default="{}"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("system_configs")
