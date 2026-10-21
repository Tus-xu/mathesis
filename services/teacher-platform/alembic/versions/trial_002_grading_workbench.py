# coding: utf-8
"""trial_002: 批改工作台真实化——补齐教师批改契约所需列（2026-10-05）。

背景：前端批改工作台（AssignView / V3GradingAssignment 契约）需要
    每题 q_no / full_score / 题干配图，以及每份作答的 文本作答与得分；
    旧表只有 kp_code/stem_latex 与 image_key，导致后端返回结构与前端契约
    对不上 → 打开作业后页面空白（用户实测「看不到真实的作业批改」）。

本迁移（纯加法，不触碰既有数据）：
    grading_questions  + q_no(int, 默认序号) / full_score(numeric) / image_key(text)
    grading_answers    + answer_text(text, 文本作答) / score(numeric, 终审定分)

Rollback：downgrade 删除上述新增列；不触碰任何既有行数据。

Revision ID: trial_002_grading_workbench
Revises: trial_001_teacher
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "trial_002_grading_workbench"
down_revision = "trial_001_teacher"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "grading_questions",
        sa.Column("q_no", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "grading_questions",
        sa.Column("full_score", sa.Numeric(5, 1), nullable=False, server_default="10"),
    )
    op.add_column(
        "grading_questions",
        sa.Column("image_key", sa.Text, nullable=True),
    )
    # 参考答案（判定聚类 kind 的依据：与参考答案一致的作答 → correct）
    op.add_column(
        "grading_questions",
        sa.Column("reference_answer", sa.Text, nullable=True),
    )
    op.add_column(
        "grading_answers",
        sa.Column("answer_text", sa.Text, nullable=True),
    )
    op.add_column(
        "grading_answers",
        sa.Column("score", sa.Numeric(5, 1), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("grading_answers", "score")
    op.drop_column("grading_answers", "answer_text")
    op.drop_column("grading_questions", "reference_answer")
    op.drop_column("grading_questions", "image_key")
    op.drop_column("grading_questions", "full_score")
    op.drop_column("grading_questions", "q_no")
