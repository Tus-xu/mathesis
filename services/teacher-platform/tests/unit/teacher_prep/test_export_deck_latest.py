"""F1 回归：导出必须确定性取**最新**的 DECK_PPTX（不能取最旧）。

背景（R8-F P0）：8200 `GET /decks/{deck_id}/artifacts` 的 SQL 排序为
`ORDER BY created_at ASC, kind ASC`（services/ppt-studio-v2/app/api/routes.py）。
旧实现用 `next(...)` 取列表**首个** DECK_PPTX = 最旧，导致引擎升级（数学图形 PNG 注入）
后老师导出拿到的仍是旧的无图产物。此测试锁定「取最新」语义。
"""

from __future__ import annotations

from app.tasks.jobs.export_artifact_v2 import _latest_deck_pptx


def test_latest_deck_pptx_picks_newest_by_created_at() -> None:
    artifacts = [
        {"id": "old", "kind": "DECK_PPTX", "created_at": "2026-09-23T10:00:00+00:00", "sha256": "aaa"},
        {"id": "png", "kind": "PAGE_PNG", "created_at": "2026-09-23T11:00:00+00:00", "page_no": 1},
        {"id": "new", "kind": "DECK_PPTX", "created_at": "2026-09-23T12:00:00+00:00", "sha256": "bbb"},
    ]
    picked = _latest_deck_pptx(artifacts)
    assert picked is not None
    assert picked["id"] == "new"
    assert picked["sha256"] == "bbb"


def test_latest_deck_pptx_multiple_pptx_must_not_return_oldest() -> None:
    """列表里有多份 DECK_PPTX 时，必须取最新（旧实现返回第一份 = 最旧 → 本断言会失败）。"""
    artifacts = [
        {"id": "v1", "kind": "DECK_PPTX", "created_at": "2026-09-20T09:00:00+00:00"},
        {"id": "v2", "kind": "DECK_PPTX", "created_at": "2026-09-22T09:00:00+00:00"},
        {"id": "v3", "kind": "DECK_PPTX", "created_at": "2026-09-24T09:00:00+00:00"},
    ]
    assert _latest_deck_pptx(artifacts)["id"] == "v3"


def test_latest_deck_pptx_without_timestamps_falls_back_to_last_in_list() -> None:
    """无 created_at 时按返回顺序取最后一个（列表升序 → 末位为最新）。"""
    artifacts = [
        {"id": "a", "kind": "DECK_PPTX"},
        {"id": "b", "kind": "DECK_PPTX"},
    ]
    assert _latest_deck_pptx(artifacts)["id"] == "b"


def test_latest_deck_pptx_tie_breaks_by_list_position() -> None:
    """created_at 相等时以列表位置为准（越靠后越新）。"""
    ts = "2026-09-24T09:00:00+00:00"
    artifacts = [
        {"id": "first", "kind": "DECK_PPTX", "created_at": ts},
        {"id": "second", "kind": "DECK_PPTX", "created_at": ts},
    ]
    assert _latest_deck_pptx(artifacts)["id"] == "second"


def test_latest_deck_pptx_none_when_absent() -> None:
    assert _latest_deck_pptx([{"id": "png", "kind": "PAGE_PNG"}]) is None
    assert _latest_deck_pptx([]) is None
