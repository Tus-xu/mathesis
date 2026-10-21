"""M2 迁移：mastery_snapshots（掌握度日快照）+ error_records.note

Revision ID: m2_006_mastery_snapshots
Revises: m2_005_system_configs
Create Date: 2026-08-07

变更：
- 新增 mastery_snapshots 表（F6 mastery/trend 数据源：(user_id, kp_code, date) 唯一，
  BKT 写路径同步 upsert 当日快照）
- error_records 新增 note 列（学生错题备注，PATCH /error-records/{id} 可改）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "m2_006_mastery_snapshots"
down_revision = "m2_005_system_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 并行开发期间表可能被手动预建，存在则跳过（幂等保护）
    bind = op.get_bind()
    if "mastery_snapshots" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "mastery_snapshots",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("kp_code", sa.String(32), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("mastery", sa.Numeric(), nullable=False, server_default="0.5"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "kp_code", "date", name="uq_mastery_snapshots_user_kp_date"),
        )
        op.create_index("idx_mastery_snapshots_user_date", "mastery_snapshots", ["user_id", "date"])

    # error_records.note（学生备注，可空；已有列则跳过）
    err_cols = [c["name"] for c in sa.inspect(bind).get_columns("error_records")]
    if "note" not in err_cols:
        op.add_column("error_records", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("error_records", "note")
    op.drop_index("idx_mastery_snapshots_user_date", table_name="mastery_snapshots")
    op.drop_table("mastery_snapshots")
