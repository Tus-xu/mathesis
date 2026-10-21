"""角色绑定模型"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class RoleBinding(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "role_bindings"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_role_bindings_user_role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    org_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Keep the physical legacy column during the compatibility window, while
    # every Python/API read derives the value from the explicit state machine.
    _legacy_verified: Mapped[bool] = mapped_column(
        "verified", Boolean, nullable=False, default=False, server_default="false"
    )

    @property
    def verified(self) -> bool:
        return self.status == "approved"

    @verified.setter
    def verified(self, value: bool) -> None:
        self.status = "approved" if value else "pending"
        self._legacy_verified = bool(value)
