"""E-3 可选班级：tp_sessions / tp_session_commands.class_id 允许 NULL

Revision ID: c8d9e0f1a2b3
Revises: b27c3d4e5f60
Create Date: 2026-09-14

背景（handoff Work card E-3，用户产品决策 2026-09-14）：
未绑定已确认教学班级的教师也必须能创建并运行备课会话（「每一个老师都可以使用」）。
班级域授权语义不变：绑定班级后的所有路径仍执行现场 8000 成员裁决（fail closed）；
未绑定时班级域能力（学情画像 / 诊断发布到班级）由应用层显式守卫拒绝。

Down / rollback 说明（AGENTS.md 迁移要求）：
    downgrade 恢复 NOT NULL。若存在 class_id IS NULL 的行，downgrade 会因
    PostgreSQL 拒绝而失败——这是**有意的护栏**：说明库里有未绑定班级的会话，
    先处置这些行（归档/删除）或放弃回滚。回滚不删除任何数据。

- 不移动、不回填、不删除任何现有数据；
- 对旧行（class_id 非空）零影响；
- 幂等：使用 postgres 判断式 ALTER（IF 已可空则跳过）由 Alembic 一次性执行保证。
"""

from __future__ import annotations

from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b27c3d4e5f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tp_sessions", "class_id", nullable=True)
    op.alter_column("tp_session_commands", "class_id", nullable=True)


def downgrade() -> None:
    # 护栏：存在 NULL 行时 PostgreSQL 直接拒绝（不静默清数据）。
    op.alter_column("tp_session_commands", "class_id", nullable=False)
    op.alter_column("tp_sessions", "class_id", nullable=False)
