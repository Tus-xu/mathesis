"""enable pgvector columns and HNSW indexes

Revision ID: 1d7107084a02
Revises: e544b0fef0bd
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1d7107084a02"
down_revision: str | None = "e544b0fef0bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 修改 episodic_memories.embedding 从 TEXT 到 vector(1024)
    op.execute(
        "ALTER TABLE episodic_memories ALTER COLUMN embedding TYPE vector(1024) "
        "USING embedding::vector(1024)"
    )

    # 修改 chunks.embedding 从 TEXT 到 vector(1024)
    op.execute(
        "ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024) "
        "USING embedding::vector(1024)"
    )

    # 创建 HNSW 索引 (cosine distance)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_episodic_memories_embedding "
        "ON episodic_memories USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_embedding "
        "ON chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m=16, ef_construction=64)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS idx_episodic_memories_embedding")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE TEXT")
    op.execute("ALTER TABLE episodic_memories ALTER COLUMN embedding TYPE TEXT")
