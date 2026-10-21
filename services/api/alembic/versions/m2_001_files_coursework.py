"""M2 迁移：files/speech/search/eval 表 + coursework 域表

Revision ID: m2_001_files_coursework
Revises: a3f2b8c4d1e9
Create Date: 2026-07-31

新增 17 张表：
- files, file_assets（文件域）
- speech_logs, search_logs, xingchen_kb_mappings, kb_eval_runs, router_eval_logs（流水/映射）
- quizzes, quiz_items, submissions, submission_items, daily_questions, streaks,
  mastery_records, assignments, assignment_targets, error_records（教学任务域）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "m2_001_files_coursework"
down_revision = "a3f2b8c4d1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================== 文件域 ====================
    op.create_table(
        "files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.String(512), nullable=True),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("parse_engine", sa.String(20), nullable=True),
        sa.Column("parse_quality", JSONB, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_files_user_id", "files", ["user_id"])
    op.create_index(
        "uq_files_user_sha256", "files", ["user_id", "sha256"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "file_assets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id"), nullable=False),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("page_no", sa.Integer, nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("storage_uri", sa.String(512), nullable=True),
        sa.Column("meta", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_file_assets_file_id", "file_assets", ["file_id"])
    op.create_index("uq_file_assets", "file_assets", ["file_id", "asset_type", "page_no"], unique=True)

    # ==================== 流水表 ====================
    op.create_table(
        "speech_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("asr_text", sa.Text, nullable=False),
        sa.Column("normalized_text", sa.Text, nullable=True),
        sa.Column("latex", sa.Text, nullable=True),
        sa.Column("ambiguous", sa.Boolean, server_default="false"),
        sa.Column("engine", sa.String(30), nullable=False),
        sa.Column("latency_ms", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_speech_logs_user", "speech_logs", ["user_id"])

    op.create_table(
        "search_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("query", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("result_count", sa.Integer, server_default="0"),
        sa.Column("top_results", JSONB, nullable=True),
        sa.Column("latency_ms", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_search_logs_user", "search_logs", ["user_id"])

    op.create_table(
        "xingchen_kb_mappings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("xingchen_doc_id", sa.String(128), nullable=False),
        sa.Column("xingchen_kb_id", sa.String(128), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "kb_eval_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("eval_set", sa.String(64), nullable=False),
        sa.Column("recall_at_5", sa.Numeric, nullable=False),
        sa.Column("mrr", sa.Numeric, nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("meta", JSONB, server_default="{}"),
    )

    op.create_table(
        "router_eval_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("utterance", sa.Text, nullable=False),
        sa.Column("workspace", sa.String(20), nullable=False),
        sa.Column("local_decision", sa.String(30), nullable=False),
        sa.Column("xc_decision", sa.String(30), nullable=True),
        sa.Column("agree", sa.Boolean, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ==================== 教学任务域 ====================
    op.create_table(
        "quizzes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("kp_codes", JSONB, server_default="[]"),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_quizzes_user", "quizzes", ["user_id"])

    op.create_table(
        "quiz_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("quiz_id", UUID(as_uuid=True), sa.ForeignKey("quizzes.id"), nullable=False),
        sa.Column("item_no", sa.Integer, nullable=False),
        sa.Column("q_type", sa.String(20), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("options", JSONB, nullable=True),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("answer_analysis", sa.Text, nullable=True),
        sa.Column("kp_code", sa.String(30), nullable=True),
        sa.Column("difficulty", sa.String(10), server_default="medium"),
        sa.Column("ai_generated", sa.Boolean, server_default="false"),
        sa.Column("sympy_check_code", sa.Text, nullable=True),
        sa.Column("source_chunk_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_quiz_items_quiz", "quiz_items", ["quiz_id"])

    op.create_table(
        "submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("quiz_id", UUID(as_uuid=True), sa.ForeignKey("quizzes.id"), nullable=True),
        sa.Column("assignment_id", UUID(as_uuid=True), nullable=True),
        sa.Column("client_submit_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="graded"),
        sa.Column("total_score", sa.Numeric, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_submissions_user", "submissions", ["user_id"])
    op.create_index(
        "uq_submissions_user_client", "submissions", ["user_id", "client_submit_id"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "submission_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), nullable=False),
        sa.Column("item_no", sa.Integer, nullable=False),
        sa.Column("q_type", sa.String(20), nullable=False),
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("verdict", sa.String(20), nullable=False),
        sa.Column("score", sa.Numeric, nullable=True),
        sa.Column("ai_pregraded", sa.Boolean, server_default="false"),
        sa.Column("error_type", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_submission_items_sub", "submission_items", ["submission_id"])

    op.create_table(
        "daily_questions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("date", sa.Date, unique=True, nullable=False),
        sa.Column("quiz_id", UUID(as_uuid=True), sa.ForeignKey("quizzes.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "streaks",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("current_streak", sa.Integer, server_default="0"),
        sa.Column("longest_streak", sa.Integer, server_default="0"),
        sa.Column("last_active_date", sa.Date, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "mastery_records",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("kp_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_points.id"), primary_key=True),
        sa.Column("mastery", sa.Numeric, server_default="0.5"),
        sa.Column("practice_count", sa.Integer, server_default="0"),
        sa.Column("correct_count", sa.Integer, server_default="0"),
        sa.Column("hint_count", sa.Integer, server_default="0"),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id", UUID(as_uuid=True), sa.ForeignKey("classes.id"), nullable=False),
        sa.Column("creator_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("quiz_id", UUID(as_uuid=True), sa.ForeignKey("quizzes.id"), nullable=True),
        sa.Column("lesson_id", sa.String(64), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default="published"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_assignments_class", "assignments", ["class_id"])

    op.create_table(
        "assignment_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("assignment_id", UUID(as_uuid=True), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_assignment_targets_aid", "assignment_targets", ["assignment_id"])

    op.create_table(
        "error_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("question_ref", JSONB, nullable=True),
        sa.Column("source_channel", sa.String(20), nullable=False),
        sa.Column("error_type", sa.String(20), nullable=True),
        sa.Column("kp_code", sa.String(30), nullable=True),
        sa.Column("file_id", UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("ai_judged", sa.Boolean, server_default="false"),
        sa.Column("corrected_by_user", sa.Boolean, server_default="false"),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_error_records_user", "error_records", ["user_id"])
    op.create_index("idx_error_records_user_kp", "error_records", ["user_id", "kp_code"])


def downgrade() -> None:
    op.drop_table("error_records")
    op.drop_table("assignment_targets")
    op.drop_table("assignments")
    op.drop_table("mastery_records")
    op.drop_table("streaks")
    op.drop_table("daily_questions")
    op.drop_table("submission_items")
    op.drop_table("submissions")
    op.drop_table("quiz_items")
    op.drop_table("quizzes")
    op.drop_table("router_eval_logs")
    op.drop_table("kb_eval_runs")
    op.drop_table("xingchen_kb_mappings")
    op.drop_table("search_logs")
    op.drop_table("speech_logs")
    op.drop_table("file_assets")
    op.drop_table("files")
