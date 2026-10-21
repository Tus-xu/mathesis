"""M2 迭代18：AI 产出防幻觉评分流水表（ai_quality_scores）

Revision ID: m2_012_ai_quality_scores
Revises: m2_011_fsrs_write_path
Create Date: 2026-08-14

变更（P1-3 防幻觉打分机制产线化）：
- 新增 ai_quality_scores 表：AI 生成题目逐题评分落库
  （A 类由质量闸强制；B 类难度漂移；C 类知识点 BGE-M3 语义锚定）
"""

from alembic import op

revision = "m2_012_ai_quality_scores"
down_revision = "m2_011_fsrs_write_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_quality_scores (
            id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            request_id VARCHAR(64) NOT NULL,
            scene VARCHAR(32) NOT NULL,
            kp_code VARCHAR(32) NOT NULL DEFAULT '',
            expected_difficulty VARCHAR(10) NOT NULL DEFAULT '',
            output_difficulty VARCHAR(10) NOT NULL DEFAULT '',
            q_type VARCHAR(20) NOT NULL DEFAULT '',
            question_hash VARCHAR(64) NOT NULL DEFAULT '',
            question_text TEXT NOT NULL DEFAULT '',
            gates_passed BOOLEAN NOT NULL DEFAULT TRUE,
            b_hit BOOLEAN NOT NULL DEFAULT FALSE,
            c_similarity DOUBLE PRECISION,
            c_deduction DOUBLE PRECISION NOT NULL DEFAULT 0,
            total_score DOUBLE PRECISION NOT NULL DEFAULT 100,
            note VARCHAR(200) NOT NULL DEFAULT ''
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_ai_quality_scores_request ON ai_quality_scores (request_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ai_quality_scores_scene_created ON ai_quality_scores (scene, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ai_quality_scores")
