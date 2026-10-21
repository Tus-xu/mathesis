"""M2 迭代16 迁移：学情增长基础设施（growth foundation）

Revision ID: m2_010_growth_foundation
Revises: m2_009_chat_refactor
Create Date: 2026-08-13

变更（方案《M2_迭代16_前端融合版后端改造方案_v1.0》§4.2，全部幂等）：
- error_records 追加 4 个可空 FSRS 缓存列（ADD COLUMN IF NOT EXISTS，不动原有字段）
- 新建 user_daily_stats（用户学情日统计，趋势图/综合分数据源）
- 新建 kp_prerequisites（知识点前置依赖，ALEKS precedence relation）
- kp_prerequisites 种子 9 条高中数学主干链（ON CONFLICT DO NOTHING）
"""


from alembic import op

revision = "m2_010_growth_foundation"
down_revision = "m2_009_chat_refactor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) error_records 追加 FSRS 缓存列（读取时计算回填；write-path 第二批接入）
    op.execute("ALTER TABLE error_records ADD COLUMN IF NOT EXISTS fsrs_stability NUMERIC")
    op.execute("ALTER TABLE error_records ADD COLUMN IF NOT EXISTS fsrs_difficulty NUMERIC")
    op.execute("ALTER TABLE error_records ADD COLUMN IF NOT EXISTS fsrs_retrievability NUMERIC")
    op.execute("ALTER TABLE error_records ADD COLUMN IF NOT EXISTS fsrs_computed_at TIMESTAMPTZ")

    # 2) 用户学情日统计表（每日快照：综合分/独立解题率/答题数等）
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_daily_stats (
            id               BIGSERIAL PRIMARY KEY,
            user_id          UUID NOT NULL REFERENCES users(id),
            date             DATE NOT NULL,
            composite_score  NUMERIC,
            independent_rate NUMERIC,
            answer_count     INTEGER NOT NULL DEFAULT 0,
            correct_count    INTEGER NOT NULL DEFAULT 0,
            hint_count       INTEGER NOT NULL DEFAULT 0,
            study_minutes    INTEGER NOT NULL DEFAULT 0,
            error_count      INTEGER NOT NULL DEFAULT 0,
            reviewed_count   INTEGER NOT NULL DEFAULT 0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_user_daily_stats_user_date UNIQUE (user_id, date)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_daily_stats_user_date "
        "ON user_daily_stats (user_id, date)"
    )

    # 3) 知识点前置依赖表（追根溯源/ALEKS 依赖链）
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS kp_prerequisites (
            id          BIGSERIAL PRIMARY KEY,
            kp_code     VARCHAR(32) NOT NULL,
            prereq_code VARCHAR(32) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_kp_prereq UNIQUE (kp_code, prereq_code)
        )
        """
    )
    # 种子数据（高中数学主干链，随题库标注持续扩充）
    op.execute(
        """
        INSERT INTO kp_prerequisites (kp_code, prereq_code) VALUES
          ('DR-01','HS-02'), ('DR-02','DR-01'), ('DR-02','HS-02'),
          ('DR-03','DR-02'), ('TG-03','TG-02'), ('TG-02','TG-01'),
          ('SL-02','SL-01'), ('JH-02','VE-01'), ('JH-02','HS-02')
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS kp_prerequisites")
    op.execute("DROP INDEX IF EXISTS idx_user_daily_stats_user_date")
    op.execute("DROP TABLE IF EXISTS user_daily_stats")
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS fsrs_computed_at")
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS fsrs_retrievability")
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS fsrs_difficulty")
    op.execute("ALTER TABLE error_records DROP COLUMN IF EXISTS fsrs_stability")
