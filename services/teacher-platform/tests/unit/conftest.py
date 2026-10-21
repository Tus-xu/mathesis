# coding: utf-8
"""tests/unit 级 conftest：app.db.base 协议 fake（B4 规范 §8 允许的 fake 路线）。

B3 未落 `app/db/base.py` 前，注入最小 DeclarativeBase 使 B4 域 models 可导入、
可离线断言约束；B3 真模块存在时本 stub 自动不生效（先尝试真实 import）。
不复制 B3 实现、不建迁移——生产 import 面始终是 `from app.db.base import Base`。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parents[2] / 'app'

if 'app' not in sys.modules:
    _m = types.ModuleType('app')
    _m.__path__ = [str(_APP_DIR)]
    sys.modules['app'] = _m

try:  # B3 真实实现存在则直接用
    import app.db  # noqa: F401
except ImportError:
    _db = types.ModuleType('app.db')
    _db.__path__ = []
    sys.modules['app.db'] = _db

if 'app.db.base' not in sys.modules:
    try:
        import app.db.base  # noqa: F401
    except ImportError:
        from sqlalchemy.orm import DeclarativeBase

        _base_mod = types.ModuleType('app.db.base')

        class Base(DeclarativeBase):
            """协议 fake：仅提供元数据注册面。"""

        _base_mod.Base = Base
        sys.modules['app.db.base'] = _base_mod
