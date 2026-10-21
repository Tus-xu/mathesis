"""8200 独立 SQLAlchemy Base（独立 metadata/session/migration，绝不混入 8100 表）。"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    __mapper_args__ = {"eager_defaults": True}
