"""M2 迁移：错题列表性能索引（m2_017_error_records_perf_index）

Revision ID: m2_017_error_records_perf_index
Revises: m2_016_error_record_dedup
Create Date: 2026-08-15

依据（任务10 性能实测 EXPLAIN）：错题列表 ORDER BY created_at DESC 走
Bitmap Heap Scan(user_id) + Sort；新增复合索引 (user_id, deleted_at, created_at)
一次覆盖过滤与排序（当前 P95 25.7ms 已达标，本索引为数据增长预留）。

幂等：CREATE INDEX IF NOT EXISTS。
"""

import sqlalchemy as sa

from alembic import op

revision = "m2_017_error_records_perf_index"
down_revision = "m2_016_error_record_dedup"
branch_labels = None
depends_on = None

_INDEX_NAME = "idx_error_records_user_deleted_created"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} "
            "ON error_records (user_id, deleted_at, created_at DESC)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
