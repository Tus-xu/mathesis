"""om7: 通用后台任务中心 + 站内通知（AI 管家悬浮球 · 阶段3 G 系列）

- tasks: 全角色通用任务表。字段语义参照 Dify WorkflowRun
  （status/outputs/error/elapsed_time/finished_at，见技术基准分析）与
  既有 teacher_tasks 状态机（queued|running|succeeded|failed|cancelled）；
  idempotency_key 唯一约束实现下单幂等（对齐 learning_events 范式）。
- notifications: 站内通知（任务终态回执）。dedup_key 唯一 = 同任务同终态同
  attempt 只推一条（降噪纪律，product-strategy v3.0 §反骚扰）。

Revision ID: om7_task_center
Revises: om6_classroom_practice
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "om7_task_center"
down_revision = "om6_classroom_practice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="student"),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(200), nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency_key"),
    )
    op.create_index("ix_tasks_user_status", "tasks", ["user_id", "status"])
    op.create_index("ix_tasks_status_updated", "tasks", ["status", "updated_at"])

    op.create_table(
        "notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(500), nullable=True),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("dedup_key", name="uq_notifications_dedup_key"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_tasks_status_updated", table_name="tasks")
    op.drop_index("ix_tasks_user_status", table_name="tasks")
    op.drop_table("tasks")
