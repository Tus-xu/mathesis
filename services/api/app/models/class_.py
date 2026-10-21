"""班级模型"""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Class(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "classes"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subject: Mapped[str] = mapped_column(String(16), nullable=False, server_default="math")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
