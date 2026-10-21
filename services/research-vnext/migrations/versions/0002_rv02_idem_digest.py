"""RV02 · FIX-A1：command_record 增加服务端权威幂等摘要

Revision ID: 0002_rv02_idem_digest
Revises: 0001_rv02
Create Date: 2026-09-13

背景（评审 P1-1）：修复前幂等冲突判定只比较调用方自报的
`idempotency_fingerprint` 与其存档副本，**payload 从未在服务端被哈希**。
后果：同一 `client_request_id` + 同一 fingerprint + **不同 payload** 会被当作重放，
返回 200 REPLAYED 并**静默丢弃**新命令的 mutation。

本迁移**纯增量**（additive）：
- `canonical_digest`：服务端重算的 sha256 hex（可空 —— 历史行没有该值）；
- `payload_ref`：留痕用（可空）。

可空是刻意的：本迁移**不**回填历史行，因为历史行没有保存原始命令 payload，
无法重算出可信摘要。伪造一个摘要比留空更危险。运行时对
`canonical_digest IS NULL` 的历史行采取 **fail closed**（按冲突拒绝，见
`session_service._check_idempotency_conflict`）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.config import db_schema  # noqa: E402

SCHEMA = db_schema()

revision: str = "0002_rv02_idem_digest"
down_revision: str | None = "0001_rv02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "command_record",
        sa.Column("canonical_digest", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "command_record",
        sa.Column("payload_ref", sa.String(512), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("command_record", "payload_ref", schema=SCHEMA)
    op.drop_column("command_record", "canonical_digest", schema=SCHEMA)
