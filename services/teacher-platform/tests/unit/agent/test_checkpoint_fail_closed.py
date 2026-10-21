"""R11.5-B：checkpoint 生产 fail-closed 单测（无 PG 路径，纯语义）。"""
from __future__ import annotations

import pytest

from app.agent.checkpoint import CheckpointerInitError, ManagedCheckpointer, is_production
from langgraph.checkpoint.memory import MemorySaver


@pytest.fixture(autouse=True)
def _production_env(monkeypatch):
    # 显式切生产：Pydantic 读 ENVIRONMENT；monkeypatch 环境后 get_settings() 有缓存需绕过
    from app.core import config as cfg

    monkeypatch.setenv("ENVIRONMENT", "production")
    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


async def test_production_no_dsn_fails_closed() -> None:
    """生产 fail-closed：无 DATABASE_URL → 抛错，禁止 MemorySaver。"""
    handle = ManagedCheckpointer(None, fail_closed=True)
    with pytest.raises(CheckpointerInitError):
        await handle.start()


async def test_local_no_dsn_uses_memory_saver() -> None:
    """local/test：无 DSN → MemorySaver 允许。"""
    handle = ManagedCheckpointer(None, fail_closed=False)
    saver = await handle.start()
    assert isinstance(saver, MemorySaver)
    await handle.close()


def test_is_production_flag() -> None:
    assert is_production() is True  # autouse 切了 ENVIRONMENT=production
    assert ManagedCheckpointer(None, fail_closed=None)._fail_closed is True
    assert ManagedCheckpointer(None, fail_closed=False)._fail_closed is False