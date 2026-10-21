"""add user_model_configs

Revision ID: a3f2b8c4d1e9
Revises: 1d7107084a02
Create Date: 2026-07-26

用户自定义模型配置表 — 一用户一行，字段级回退 .env
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f2b8c4d1e9"
down_revision: str | None = "1d7107084a02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_model_configs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("primary_api_key", sa.String(512), nullable=True),
        sa.Column("primary_base_url", sa.String(512), nullable=True),
        sa.Column("primary_model", sa.String(128), nullable=True),
        sa.Column("secondary_api_key", sa.String(512), nullable=True),
        sa.Column("secondary_base_url", sa.String(512), nullable=True),
        sa.Column("secondary_model", sa.String(128), nullable=True),
        sa.Column("secondary_thinking", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("idx_umc_user", "user_model_configs", ["user_id"], unique=True)

    # updated_at trigger（与初始迁移保持一致）
    op.execute(
        "CREATE TRIGGER trg_user_model_configs_updated "
        "BEFORE UPDATE ON user_model_configs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_user_model_configs_updated ON user_model_configs")
    op.drop_index("idx_umc_user", table_name="user_model_configs")
    op.drop_table("user_model_configs")
