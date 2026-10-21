"""RV02 · SSE 保留窗口（条数 + 时间双上限）。

超出窗口一律 `SNAPSHOT_REQUIRED`，**绝不伪造连续**。
"""

from __future__ import annotations

import uuid

from tests.integration.conftest import auth_headers, create_session, envelope


def _last_event_id(client, ctx, sid) -> str:
    r = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": None},
        headers=auth_headers(ctx),
    )
    assert r.status_code == 200, r.text
    return r.json()["events"][-1]["event_id"]


def test_within_retention_window_is_incremental(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    last = _last_event_id(client, ctx, sid)

    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": last},
        headers=auth_headers(ctx),
    )
    assert res.status_code == 200
    assert res.json()["fallback"] == "NONE"


def test_retention_by_count_is_enforced(client, new_ids, ctx_factory, monkeypatch) -> None:
    """窗口条数设为 0 → 任何 last_event_id 都被视为窗口外。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    last = _last_event_id(client, ctx, sid)

    monkeypatch.setenv("RESEARCH_SSE_RETENTION_EVENTS", "0")
    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": last},
        headers=auth_headers(ctx),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["fallback"] == "SNAPSHOT_REQUIRED"
    assert body["snapshot_ref"] == f"snapshot:{sid}"
    assert "ASSUME_CAUGHT_UP" not in str(body)


def test_retention_by_time_is_enforced(client, new_ids, ctx_factory, monkeypatch) -> None:
    """窗口时间设为 0 小时 → 刚发生的事件也落在窗口外（时间维度已接线）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    last = _last_event_id(client, ctx, sid)

    monkeypatch.setenv("RESEARCH_SSE_RETENTION_HOURS", "0")
    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": last},
        headers=auth_headers(ctx),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["fallback"] == "SNAPSHOT_REQUIRED"
    assert "小时" in body["reason"]


def test_unknown_event_id_is_snapshot_required(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": "evt-not-exist"},
        headers=auth_headers(ctx),
    )
    assert res.json()["fallback"] == "SNAPSHOT_REQUIRED"


def test_no_last_event_id_returns_full_window(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "ACTIVE"},
        expected_revision=0,
        tenant_id=new_ids["tenant_id"],
    )
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx)).status_code == 200

    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": None},
        headers=auth_headers(ctx),
    )
    assert res.status_code == 200
    assert res.json()["fallback"] == "NONE"
    assert len(res.json()["events"]) >= 2
