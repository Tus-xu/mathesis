"""stu_002: 学习账本四表补迁移（learning_events / exam_papers / exam_paper_items / ai_recommendations）

背景（2026-10-11 现场核实）：
- 四张表自 M2 迭代17 起只有 ORM 模型（app/models/learning_event.py、exam_paper.py、
  ai_recommendation.py），alembic/versions 全目录无对应 create_table；
  开发库（54329/math_arena）的四表是 Base.metadata.create_all 越权建出的。
  干净环境（CI / 生产 / 新同事机器）`alembic upgrade head` 后缺这四张表，
  GET /api/trial/metrics（trial_router._user_profile_bundle 裸 select LearningEvent）
  与 GET /api/butler/actions（butler_router.list_actions 裸 select AIRecommendation）
  直接 500。
- 本迁移把四表纳入版本链：干净库建表；已有表的库（如 dev）按 has_table 守卫
  自动 no-op（沿用 auth_001_unified_identity.py:71 的先例），无需 stamp，
  既有数据零丢失。
- exam_papers / exam_paper_items 当前无 router/service 消费（dormant 模型），但仍在
  models/__init__.py 导出、属 Base.metadata，每轮测试 create_all 都会建出——
  缺迁移 = 永久 schema 漂移，故照建并在 downgrade 中对称回滚；
  未来若确认永不启用，按仓库 REMOVE 规则先删模型再删本段。

Revision ID: stu_002_learning_ledger_tables
Revises: trial_001_feedback
Create Date: 2026-10-11
"""

import logging

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "stu_002_learning_ledger_tables"
down_revision = "trial_001_feedback"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

# 模型列数基准（TimestampMixin: id/created_at/updated_at + 各业务列）。
# 已有表环境的轻量漂移检测：列数不符只 warning 不阻断（方案 §migrations ⑦），
# 因为 create_all 越权建出的表可能存在细微出入——暴露给人工核对，而不是静默掩盖。
_EXPECTED_COLUMNS = {
    "learning_events": 12,  # id, user_id, event_type, source_type, source_id, payload,
    #                        idempotency_key, status, retry_count, processed_at, created_at, updated_at
    "ai_recommendations": 11,  # id, user_id, kind, source, payload, llm_model,
    #                           user_feedback, shown_at, acted_at, created_at, updated_at
    "exam_papers": 12,  # id, title, source, year, subject, scope, total_score,
    #                    duration_minutes, structure, status, created_at, updated_at
    "exam_paper_items": 13,  # id, paper_id, item_no, q_type, stem, options, answer,
    #                         analysis, kp_code, difficulty, score, created_at, updated_at
}


def _adopt_or_create(bind, table: str, create_fn) -> None:
    """已有表 → no-op + 漂移告警；缺表 → 建表（auth_001 守卫先例）。"""
    if sa.inspect(bind).has_table(table):
        try:
            actual = sa.inspect(bind).get_columns(table)
            if len(actual) != _EXPECTED_COLUMNS[table]:
                logger.warning(
                    "stu_002 列漂移：已有表 %s 期望 %d 列，实得 %d 列 %s —— 已跳过建表（守卫 no-op），请人工核对",
                    table,
                    _EXPECTED_COLUMNS[table],
                    len(actual),
                    sorted(c["name"] for c in actual),
                )
        except Exception:  # noqa: BLE001 — 检测本身失败不阻断迁移
            logger.warning("stu_002 列漂移检测失败（表 %s），跳过告警", table)
        return
    create_fn()


def upgrade() -> None:
    bind = op.get_bind()

    # ---- 1. learning_events（学习事件账本：管家决策输入源，M2 迭代17）----
    def _create_learning_events() -> None:
        op.create_table(
            "learning_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("source_type", sa.String(32), nullable=False),
            sa.Column("source_id", sa.String(64), nullable=True),
            sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
            sa.Column("idempotency_key", sa.String(128), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("idempotency_key", name="learning_events_idempotency_key_key"),
        )
        op.create_index(
            "idx_learning_events_user_status",
            "learning_events",
            ["user_id", "status", "created_at"],
        )
        op.create_index(
            "idx_learning_events_type_time",
            "learning_events",
            ["event_type", "created_at"],
        )

    # ---- 2. ai_recommendations（AI 推荐记录：管家动作轮询/反馈的数据飞轮）----
    def _create_ai_recommendations() -> None:
        op.create_table(
            "ai_recommendations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
            sa.Column("llm_model", sa.String(64), nullable=True),
            sa.Column("user_feedback", sa.String(16), nullable=True),
            sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )
        op.create_index(
            "idx_ai_rec_user_kind_time",
            "ai_recommendations",
            ["user_id", "kind", "created_at"],
        )

    # ---- 3. exam_papers（套卷素材库；dormant 但属 Base.metadata）----
    def _create_exam_papers() -> None:
        op.create_table(
            "exam_papers",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("source", sa.String(100), nullable=True),
            sa.Column("year", sa.Integer, nullable=True),
            sa.Column("subject", sa.String(16), nullable=False, server_default="math"),
            sa.Column("scope", sa.String(16), nullable=False, server_default="student"),
            sa.Column("total_score", sa.Numeric, nullable=True),
            sa.Column("duration_minutes", sa.Integer, nullable=True),
            sa.Column("structure", postgresql.JSONB, nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_exam_papers_scope_year", "exam_papers", ["scope", "year"])

    # ---- 4. exam_paper_items（套卷题目；FK → exam_papers，须在其后创建）----
    def _create_exam_paper_items() -> None:
        op.create_table(
            "exam_paper_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "paper_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("exam_papers.id"),
                nullable=False,
            ),
            sa.Column("item_no", sa.Integer, nullable=False),
            sa.Column("q_type", sa.String(20), nullable=False),
            sa.Column("stem", sa.Text, nullable=False),
            sa.Column("options", postgresql.JSONB, nullable=True),
            sa.Column("answer", sa.Text, nullable=False),
            sa.Column("analysis", sa.Text, nullable=True),
            sa.Column("kp_code", sa.String(32), nullable=True),
            sa.Column("difficulty", sa.String(10), nullable=False, server_default="medium"),
            sa.Column("score", sa.Numeric, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["paper_id"], ["exam_papers.id"]),
        )
        op.create_index(
            "idx_exam_paper_items_paper_no", "exam_paper_items", ["paper_id", "item_no"]
        )

    _adopt_or_create(bind, "learning_events", _create_learning_events)
    _adopt_or_create(bind, "ai_recommendations", _create_ai_recommendations)
    _adopt_or_create(bind, "exam_papers", _create_exam_papers)
    _adopt_or_create(bind, "exam_paper_items", _create_exam_paper_items)


def downgrade() -> None:
    """对称回滚（⚠️ 会删表丢数据：仅在全量确认无生产数据或已备份后允许，见方案 deployNotes）。"""
    bind = op.get_bind()
    inspect = sa.inspect(bind)

    if inspect.has_table("exam_paper_items"):
        op.drop_index("idx_exam_paper_items_paper_no", table_name="exam_paper_items")
        op.drop_table("exam_paper_items")
    if inspect.has_table("exam_papers"):
        op.drop_index("idx_exam_papers_scope_year", table_name="exam_papers")
        op.drop_table("exam_papers")
    if inspect.has_table("ai_recommendations"):
        op.drop_index("idx_ai_rec_user_kind_time", table_name="ai_recommendations")
        op.drop_table("ai_recommendations")
    if inspect.has_table("learning_events"):
        op.drop_index("idx_learning_events_type_time", table_name="learning_events")
        op.drop_index("idx_learning_events_user_status", table_name="learning_events")
        op.drop_table("learning_events")
