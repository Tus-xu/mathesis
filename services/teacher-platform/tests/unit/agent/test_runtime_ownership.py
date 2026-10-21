"""R11.5-B：ownership/preflight/reconcile 单测（行数 + 规范 hash 对账语义）。"""
from __future__ import annotations

import pytest

from app.db.ownership import (
    OWNED_TABLES,
    RuntimeOwnershipError,
    preflight_owned_tables,
    row_hash,
)


class _Row(dict):
    def __getitem__(self, k):  # 兼容 dict-row 访问
        return super().__getitem__(k)


class FakeSession:
    """表存在性可控的 fake session。"""

    def __init__(self, existing: set[str], columns: dict[str, set[str]] | None = None) -> None:
        self._existing = existing
        self._columns = columns or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        table = (params or {}).get("t")
        if "information_schema.tables" in sql:
            self._current_table = table

            class R:
                def scalar(self):
                    return 1 if (params or {}).get("t") in self._existing else None

            R._existing = self._existing
            return R()
        if "information_schema.columns" in sql:
            cols = self._columns.get(self._current_table, set())

            class RC:
                def __iter__(self):
                    return iter([(c,) for c in cols])

            return RC()
        raise AssertionError(f"unexpected sql: {sql}")


class FakeSessionMaker:
    """async_sessionmaker 等价物：() → session（与 db.session 契约一致）。"""

    def __init__(self, existing: set[str], columns: dict[str, set[str]] | None = None) -> None:
        self._existing = existing
        self._columns = columns or {}

    def __call__(self):
        return FakeSession(self._existing, self._columns)


class FakeFactory:
    """get_session_factory 函数等价物：() → async_sessionmaker。"""

    def __init__(self, existing: set[str], columns: dict[str, set[str]] | None = None) -> None:
        self._existing = existing
        self._columns = columns or {}

    def __call__(self):
        return FakeSessionMaker(self._existing, self._columns)


async def test_preflight_pass_when_all_tables_exist() -> None:
    existing = set(OWNED_TABLES)
    cols = {t: set(v) for t, v in OWNED_TABLES.items()}
    report = await preflight_owned_tables(FakeFactory(existing, cols), fail_closed=True)
    assert all(r["ok"] for r in report)


async def test_preflight_fails_closed_on_missing_table() -> None:
    existing = set(OWNED_TABLES) - {"pending_actions"}
    cols = {t: set(v) for t, v in OWNED_TABLES.items()}
    with pytest.raises(RuntimeOwnershipError) as ei:
        await preflight_owned_tables(FakeFactory(existing, cols), fail_closed=True)
    assert "pending_actions" in str(ei.value)
    # fail_closed=False → 仅报告不抛
    report = await preflight_owned_tables(FakeFactory(existing, cols), fail_closed=False)
    bad = [r for r in report if not r["ok"]]
    assert bad and bad[0]["table"] == "pending_actions"


async def test_preflight_detects_missing_columns() -> None:
    existing = set(OWNED_TABLES)
    cols = {t: set(v) for t, v in OWNED_TABLES.items()}
    cols["ai_runs"] = cols["ai_runs"] - {"feature"}  # 缺关键列 → 不 ok
    with pytest.raises(RuntimeOwnershipError) as ei:
        await preflight_owned_tables(FakeFactory(existing, cols), fail_closed=True)
    assert "ai_runs" in str(ei.value)


def test_row_hash_stable_and_excludes_volatile() -> None:
    rows = [(1, "a", "2026-01-01"), (2, "b", "2026-01-02")]
    h1 = row_hash(rows)
    h2 = row_hash(rows)
    h3 = row_hash([(2, "b", "2026-01-02"), (1, "a", "2026-01-01")])  # 顺序不同 → hash 不同
    assert h1 == h2 and h1 != h3
    # 剔除易变列后 hash 仍稳定
    h_excl = row_hash(rows, exclude_idx={2})
    assert h_excl == row_hash(rows, exclude_idx={2})
    assert h_excl != h1