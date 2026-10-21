"""数据库引擎与会话工厂。

**生产启动不做 runtime DDL**：`ensure_schema` 只在 `RESEARCH_ENV=test` 下允许调用，
生产由 Alembic 迁移负责（`alembic upgrade head`）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import database_url, db_schema, is_test

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """一个业务事务 = 一个 DB transaction。

    domain event 与 outbox 必须在**同一** transaction 写入（总手册 §13.3）。
    """
    s = get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def ensure_schema() -> None:
    """仅测试环境可用的建表快捷方式。生产禁止调用。"""
    if not is_test():
        raise RuntimeError(
            "生产环境禁止 runtime DDL；请使用 `alembic upgrade head`。"
            "当前 RESEARCH_ENV != test。"
        )
    from .base import Base
    from . import models  # noqa: F401

    eng = get_engine()
    with eng.begin() as c:
        c.execute(__import__("sqlalchemy").text(f'CREATE SCHEMA IF NOT EXISTS "{db_schema()}"'))
    Base.metadata.create_all(eng)


def assert_no_runtime_ddl_in_prod() -> None:
    if not is_test():
        # 生产启动只验证 schema 存在，不创建任何对象
        from sqlalchemy import text

        with get_engine().begin() as c:
            n = c.execute(
                text(
                    "select count(*) from information_schema.tables where table_schema = :s"
                ),
                {"s": db_schema()},
            ).scalar()
            if not n:
                raise RuntimeError(
                    f"schema {db_schema()} 为空：请先执行 `alembic upgrade head`。"
                    "生产不做 runtime DDL。"
                )
