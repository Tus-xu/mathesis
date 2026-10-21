"""ppt v2 domain tables

PPT Studio V2（R10）首版迁移：创建 2 张独立于旧 Deck 域的 V2 表。

设计要点（对应 app/ppt_v2/persistence/models.py，模型与迁移一致，禁止漂移）：
- 表名前缀 ppt_v2_*，与旧 PPT 生成/导出/内容管理域的 Deck/Plan 结构完全隔离，
  旧表与旧接口行为零变化（R10 验收：旧数据不受影响）；
- ppt_v2_deck_specs 存 V2 唯一事实来源（TeachingDeckSpec JSONB）：
  revision 为乐观并发列，schema_version 冗余存储供读前过滤；
  spec 内嵌 revision 与列 revision 由 domain 层保证一致；
- ppt_v2_assets 为资产注册表：object_key 指向 MinIO 不可变对象，
  rights_status 默认 QUARANTINED（未授权不入生产渲染/检索，与教材域同哲学）；
  preserve_original 原件保护列（TEACHER_UPLOAD 必须为 true，域校验保证）；
- 删除 deck 级联删资产注册行（内存数据），**不触碰 MinIO 对象**——
  对象生命周期由独立策略管理（与教材域 R05 验收一致）；
- 不迁移任何旧 deck 数据；downgrade 仅 drop 本轮 2 表。

Revision ID: c17d2e3f405a
Revises: b06c2d3e4f5a
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c17d2e3f405a"
down_revision = "b06c2d3e4f5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ppt_v2_deck_specs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=True),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "spec", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_pptdeck_revision"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pptdeck_school_schema", "ppt_v2_deck_specs", ["school_id", "schema_version"], unique=False)

    op.create_table(
        "ppt_v2_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deck_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("rights_status", sa.Text(), server_default="QUARANTINED", nullable=False),
        sa.Column("rights_evidence_ref", sa.Text(), nullable=True),
        sa.Column("preserve_original", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column(
            "asset_meta", postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('TEXTBOOK','TEACHER_UPLOAD','MATH_RENDER','AI_IMAGE')", name="ck_pptasset_kind"
        ),
        sa.CheckConstraint(
            "rights_status IN ('QUARANTINED','STAGED','ACTIVE')", name="ck_pptasset_rights"
        ),
        sa.ForeignKeyConstraint(["deck_id"], ["ppt_v2_deck_specs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deck_id", "object_key", name="uq_pptasset_deck_key"),
    )
    op.create_index("ix_pptasset_kind", "ppt_v2_assets", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_pptasset_kind", table_name="ppt_v2_assets")
    op.drop_table("ppt_v2_assets")
    op.drop_index("ix_pptdeck_school_schema", table_name="ppt_v2_deck_specs")
    op.drop_table("ppt_v2_deck_specs")