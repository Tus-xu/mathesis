"""CR-6（ADR-RV-003）· 消息层：research_message + research_ai_run

Revision ID: 0003_rv_cr6_msg
Revises: 0002_rv02_idem_digest
Create Date: 2026-09-13

背景（CHANGE-REQUEST CR-6a）：前端 `researchApi.ts` 调用
`POST /api/research-vnext/sessions/{id}/messages`，后端不存在该端点；
且前端 `adaptResearchSnapshot` 硬要求 `timeline` / `evidence` / `capabilities`
三个数组，后端 `GET /sessions/{id}` 返回的是内部快照，没有这些字段。

ADR-RV-003 采用 **Option C 双层模型**，本迁移是它的持久层：

1. `research_message` —— **会话记录**（时间线的唯一来源），
   *不是*正式领域真相。assistant 行的 `payload` 只承载 **typed proposal**。
2. `research_ai_run` —— 每次 LLM 调用的审计记录（模型/供应商/用量/时延/
   prompt ref/trace）。**不落 prompt 正文**（可能含用户数据），只落
   `<prompt_id>@v<n>#<content-hash>` 形式的引用。

设计要点：
- `(session_id, seq)` 唯一 + 索引：`created_at` 在并发下可能相同，仅按时间排序
  会产出不稳定 timeline；`seq` 把它变成可证明的稳定序。
- 所有列都带 DB 侧 `server_default`（非空列），因此迁移建出的表与 ORM 模型
  一致，不依赖应用层补默认值。
- `evidence_refs` 用 JSONB **数组**（默认 `[]`），因为投影必须永远是数组。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

revision: str = "0003_rv_cr6_msg"
down_revision: str | None = "0002_rv02_idem_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "research_message",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_run_id", UUID, nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        # 幂等键：崩溃恢复时判断"user 行是否已落"，避免重复插入同一条用户消息
        sa.Column("client_request_id", sa.String(128), nullable=False, server_default=""),
        sa.UniqueConstraint("session_id", "seq", name="uq_research_message_seq"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_rm_session_seq", "research_message", ["session_id", "seq"], schema=SCHEMA
    )
    op.create_index("ix_rm_session", "research_message", ["session_id"], schema=SCHEMA)
    op.create_index("ix_rm_tenant", "research_message", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_rm_crid", "research_message", ["client_request_id"], schema=SCHEMA)
    # role 只能是三种之一：投影层依赖这个封闭集合
    op.create_check_constraint(
        "ck_research_message_role",
        "research_message",
        "role IN ('user','assistant','system')",
        schema=SCHEMA,
    )

    op.create_table(
        "research_ai_run",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("message_id", UUID, nullable=True),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_ref", sa.String(160), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_rar_session", "research_ai_run", ["session_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_rar_session", "research_ai_run", schema=SCHEMA)
    op.drop_table("research_ai_run", schema=SCHEMA)

    op.drop_constraint(
        "ck_research_message_role", "research_message", schema=SCHEMA, type_="check"
    )
    op.drop_index("ix_rm_tenant", "research_message", schema=SCHEMA)
    op.drop_index("ix_rm_session", "research_message", schema=SCHEMA)
    op.drop_index("ix_rm_crid", "research_message", schema=SCHEMA)
    op.drop_index("ix_rm_session_seq", "research_message", schema=SCHEMA)
    op.drop_table("research_message", schema=SCHEMA)
