"""initial_15_tables

Revision ID: e544b0fef0bd
Revises:
Create Date: 2026-07-22 12:00:19.206912
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e544b0fef0bd"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that need updated_at triggers (all tables with updated_at column)
TABLES_WITH_UPDATED_AT = [
    "users",
    "role_bindings",
    "classes",
    "class_members",
    "conversations",
    "messages",
    "user_profiles",
    "episodic_memories",
    "knowledge_points",
    "knowledge_docs",
    "chunks",
    "skills",
]

# Dollar-quoted PL/pgSQL for the trigger function
_SET_UPDATED_AT_FN = (
    "CREATE OR REPLACE FUNCTION set_updated_at() "
    "RETURNS trigger AS $func_body$ "
    "BEGIN NEW.updated_at = now(); RETURN NEW; END; "
    "$func_body$ LANGUAGE plpgsql"
)

# DO block with exception handling for optional pgvector
_VECTOR_EXT_DO = (
    "DO $vec$ BEGIN "
    "CREATE EXTENSION IF NOT EXISTS vector; "
    "EXCEPTION WHEN OTHERS THEN "
    "RAISE NOTICE 'pgvector not available, embedding columns use TEXT'; "
    "END $vec$"
)


def upgrade() -> None:
    # ==================================================================
    # 1. PostgreSQL extensions
    # ==================================================================
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # pgvector: optional - if not available, embedding columns stay TEXT
    # TODO: install pgvector, then alter embedding columns to vector(1024)
    #       and create HNSW indexes.
    op.execute(_VECTOR_EXT_DO)

    # ==================================================================
    # 2. updated_at trigger function
    # ==================================================================
    op.execute(_SET_UPDATED_AT_FN)

    # ==================================================================
    # 3. Tables
    # ==================================================================

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("nickname", sa.String(64), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
        sa.UniqueConstraint("email"),
    )

    # --- role_bindings ---
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("org_name", sa.String(128), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "role", name="uq_role_bindings_user_role"),
    )

    # --- classes ---
    op.create_table(
        "classes",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("invite_code", sa.String(8), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("grade", sa.String(16), nullable=True),
        sa.Column("subject", sa.String(16), nullable=False, server_default="math"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.UniqueConstraint("invite_code"),
    )

    # --- class_members ---
    op.create_table(
        "class_members",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("member_role", sa.String(16), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("join_via", sa.String(16), nullable=False, server_default="code"),
        sa.Column("nickname_in_class", sa.String(64), nullable=True),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("class_id", "user_id", name="uq_class_members_class_user"),
    )
    op.create_index(
        "idx_class_members_user",
        "class_members",
        ["user_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("active_role", sa.String(16), nullable=False),
        sa.Column("title", sa.String(128), nullable=False, server_default="\u65b0\u5bf9\u8bdd"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "idx_conversations_user",
        "conversations",
        ["user_id", "updated_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- messages ---
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("client_msg_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("skill_id", sa.String(64), nullable=True),
        sa.Column("route_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.UniqueConstraint("conversation_id", "client_msg_id", name="uq_messages_conv_client"),
    )
    op.create_index(
        "idx_messages_conv",
        "messages",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- user_profiles ---
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("grade", sa.String(16), nullable=True),
        sa.Column("level", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column(
            "weak_points",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id"),
    )

    # --- episodic_memories ---
    op.create_table(
        "episodic_memories",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # TODO: pgvector available -> change to vector(1024) + HNSW index
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column("importance", sa.SmallInteger(), nullable=False, server_default="3"),
        sa.Column("kp_ids", postgresql.ARRAY(sa.UUID()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    # --- knowledge_points ---
    op.create_table(
        "knowledge_points",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("grade", sa.String(16), nullable=True),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_id"], ["knowledge_points.id"]),
        sa.UniqueConstraint("code"),
    )

    # --- knowledge_docs ---
    op.create_table(
        "knowledge_docs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("file_uri", sa.String(512), nullable=True),
        sa.Column("uploader_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"]),
    )

    # --- chunks ---
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("doc_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # TODO: pgvector available -> change to vector(1024) + HNSW index
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("kp_ids", postgresql.ARRAY(sa.UUID()), nullable=False, server_default="{}"),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["doc_id"], ["knowledge_docs.id"]),
    )
    # GIN index on kp_ids (tag-based recall)
    op.create_index("idx_chunks_kp", "chunks", ["kp_ids"], postgresql_using="gin")
    # pg_trgm GIN index on content (fuzzy search, ADR-001-9)
    op.create_index(
        "idx_chunks_trgm",
        "chunks",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )

    # --- skills ---
    op.create_table(
        "skills",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("installed_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["installed_by"], ["users.id"]),
    )

    # --- skill_runs (flow table: id + created_at only) ---
    op.create_table(
        "skill_runs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("skill_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=True),
        sa.Column(
            "params", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"]),
    )
    op.create_index("idx_skill_runs_stat", "skill_runs", ["skill_id", "created_at"])

    # --- ai_calls (flow table: id + created_at only) ---
    op.create_table(
        "ai_calls",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("scene", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_ai_calls_req", "ai_calls", ["request_id"])

    # --- events (flow table: BIGSERIAL id + created_at only) ---
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column(
            "props", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("idx_events_name_time", "events", ["event", "created_at"])

    # ==================================================================
    # 4. updated_at triggers
    # ==================================================================
    for tbl in TABLES_WITH_UPDATED_AT:
        op.execute(
            f"CREATE TRIGGER trg_{tbl}_updated "
            f"BEFORE UPDATE ON {tbl} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    # ==================================================================
    # 1. Drop triggers (reverse order)
    # ==================================================================
    for tbl in reversed(TABLES_WITH_UPDATED_AT):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_updated ON {tbl}")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    # ==================================================================
    # 2. Drop tables (reverse dependency order)
    # ==================================================================
    op.drop_index("idx_events_name_time", table_name="events")
    op.drop_table("events")

    op.drop_index("idx_ai_calls_req", table_name="ai_calls")
    op.drop_table("ai_calls")

    op.drop_index("idx_skill_runs_stat", table_name="skill_runs")
    op.drop_table("skill_runs")

    op.drop_table("skills")

    op.drop_index("idx_chunks_trgm", table_name="chunks")
    op.drop_index("idx_chunks_kp", table_name="chunks")
    op.drop_table("chunks")

    op.drop_table("knowledge_docs")
    op.drop_table("knowledge_points")
    op.drop_table("episodic_memories")
    op.drop_table("user_profiles")

    op.drop_index(
        "idx_messages_conv", table_name="messages", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_table("messages")

    op.drop_index(
        "idx_conversations_user",
        table_name="conversations",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("conversations")

    op.drop_index(
        "idx_class_members_user",
        table_name="class_members",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("class_members")

    op.drop_table("classes")
    op.drop_table("role_bindings")
    op.drop_table("users")

    # ==================================================================
    # 3. Drop extensions
    # ==================================================================
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    # pgcrypto: keep - may be needed by other things
