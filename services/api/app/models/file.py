"""文件域模型（SSOT §6.1）

files: 文件元数据（软删）
file_assets: 解析产物（软删）
"""

import uuid

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class File(Base, TimestampMixin, SoftDeleteMixin):
    """文件元数据表"""

    __tablename__ = "files"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_type: Mapped[str] = mapped_column(String(20))  # pdf/image/docx/pptx/xlsx/md/txt
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded/parsing/parsed/failed
    parse_engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    parse_quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_files_user_sha256",
            "user_id",
            "sha256",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )


class FileAsset(Base, TimestampMixin, SoftDeleteMixin):
    """文件解析产物表"""

    __tablename__ = "file_assets"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id"), index=True
    )
    asset_type: Mapped[str] = mapped_column(String(20))  # markdown/page_image/text
    page_no: Mapped[int | None] = mapped_column(nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("uq_file_assets", "file_id", "asset_type", "page_no", unique=True),
    )
