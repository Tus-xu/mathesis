"""班级成员模型"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class ClassMember(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "class_members"
    __table_args__ = (UniqueConstraint("class_id", "user_id", name="uq_class_members_class_user"),)

    class_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("classes.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    member_role: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    join_via: Mapped[str] = mapped_column(String(16), nullable=False, server_default="code")
    nickname_in_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
