"""SQLAlchemy DeclarativeBase（B3 拥有）。

各域模型由域所有者写入 `app/db/models/<域>.py`，B3 评审迁移一致性（02-ARCHITECTURE §9）。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
