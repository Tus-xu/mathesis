"""Checkpointer 工厂（thread_id=session_id，B1 红线 5：状态不得进进程内变量）。

R11.5-B 接管（DECISIONS §5，生产 fail-closed）：

- 表所有权：checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations
  由正式 Alembic 迁移（d17b2c3e4f5a）所有，`setup()`/建表不再在运行期发生。
- 生产（`fail_closed=True`）：PG/checkpointer 初始化失败 → 抛启动异常（禁止静默降级
  MemorySaver，禁止启动隐式 DDL——不调用 `.setup()`，改为 preflight 校验表存在）。
- local/test：允许 MemorySaver（无 DSN）或 `.setup()`（开发库；受 unlock 控制）。
- 连接姿势依据 notes/B1-notes.md §1.2：AsyncPostgresSaver 用单连接 pipeline
  （psycopg 通道）。DSN 转换保持向后兼容。
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def sqlalchemy_to_psycopg_dsn(database_url: str) -> str:
    """postgresql+asyncpg://user:pw@host:port/db → postgresql://user:pw@host:port/db。"""
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


# 迁移 d17b2c3e4f5a 所有权内的 checkpoint 表
CHECKPOINT_OWNED_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def is_production() -> bool:
    """生产判定：非 development/test 即视为生产（fail-closed 生效域）。"""
    env = (get_settings().environment or "").lower()
    return env not in ("development", "test", "testing", "local")


class CheckpointerInitError(RuntimeError):
    """生产 checkpointer 初始化失败——启动必须失败（禁止内存降级）。"""


class ManagedCheckpointer:
    """长生命周期 checkpointer 句柄（worker/api 启动时 start，退出时 close）。

    fail_closed=True（生产）：任何失败直接 raise；fail_closed=False（local/test）：
    PG 不可达或无 DSN 时回退 MemorySaver。
    """

    def __init__(self, database_url: str | None, *, fail_closed: bool | None = None) -> None:
        self._database_url = database_url
        self._fail_closed = is_production() if fail_closed is None else fail_closed
        self._saver: Any = None
        self._cm: Any = None

    @property
    def saver(self) -> Any:
        return self._saver

    async def _start_pg(self) -> Any:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        dsn = sqlalchemy_to_psycopg_dsn(self._database_url)
        self._cm = AsyncPostgresSaver.from_conn_string(dsn)
        self._saver = await self._cm.__aenter__()
        # R11.5-B：所有权归迁移——生产不跑 setup()（无隐式 DDL），改为 preflight 校验表存在。
        await self._preflight_checkpoint_tables()
        return self._saver

    async def _preflight_checkpoint_tables(self) -> None:
        """校验 checkpoint 表已由迁移建好；缺失 → fail-closed 抛错（不执行 DDL）。"""
        missing: list[str] = []
        async with self._saver._cursor() as cur:  # type: ignore[attr-defined]
            for table in CHECKPOINT_OWNED_TABLES:
                await cur.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_name = %s",
                    (table,),
                )
                if await cur.fetchone() is None:
                    missing.append(table)
        if missing:
            raise CheckpointerInitError(
                f"checkpoint 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）: {', '.join(missing)}"
            )

    async def start(self) -> Any:
        if self._saver is not None:
            return self._saver
        if not self._database_url:
            if self._fail_closed:
                raise CheckpointerInitError("生产环境未配置 DATABASE_URL，无法初始化 Postgres checkpointer（禁止 MemorySaver）")
            from langgraph.checkpoint.memory import MemorySaver

            self._saver = MemorySaver()
            return self._saver
        try:
            return await self._start_pg()
        except CheckpointerInitError:
            raise  # preflight 语义失败：原样上抛（启动失败门）
        except Exception:
            if self._fail_closed:
                logger.exception("Postgres checkpointer 初始化失败（生产 fail-closed，不降级内存）")
                raise CheckpointerInitError("Postgres checkpointer 初始化失败（生产禁止内存降级）") from None
            logger.exception("PostgresSaver 初始化失败，降级为 MemorySaver（仅限 local/test）")
            from langgraph.checkpoint.memory import MemorySaver

            self._saver = MemorySaver()
            return self._saver

    async def close(self) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            self._saver = None