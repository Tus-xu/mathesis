"""RV02 · 幂等与 revision 集成测试。

- 重复 `client_request_id + fingerprint` → 返回**原结果**（不产生第二次效果）
- 同一 `client_request_id` 不同 payload → 冲突（409 IDEMPOTENCY_CONFLICT）
- 过期 `expected_revision` → 可机器处理 409 REVISION_CONFLICT
"""

from __future__ import annotations

import uuid

from tests.integration.conftest import auth_headers, create_session, envelope


def _transition(client, ctx, sid, to, rev, *, crid=None, payload=None, ids=None):
    body = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload=payload or {"to": to},
        expected_revision=rev,
        tenant_id=ids["tenant_id"] if ids else None,
        client_request_id=crid,
    )
    return client.post(f"/api/research-v1/sessions/{sid}:transition", json=body, headers=auth_headers(ctx)), body


def test_replay_same_request_id_and_fingerprint_returns_original(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "crq-replay-0001"
    first, _ = _transition(client, ctx, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert first.status_code == 200, first.text
    first_rev = first.json()["session"]["revision"]

    # 重放：相同 client_request_id + 相同 fingerprint（payload 未变）
    second, _ = _transition(client, ctx, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert second.status_code == 200, second.text
    assert second.json()["session"]["revision"] == first_rev, "重放不得产生第二次效果"
    assert second.headers.get("X-Idempotency-Status") == "REPLAYED"
    assert first.headers.get("X-Idempotency-Status") == "EXECUTED"


def test_replay_returns_identical_body(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "crq-replay-identical"
    a, _ = _transition(client, ctx, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    b, _ = _transition(client, ctx, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert a.json()["session"] == b.json()["session"]
    assert a.json()["last_event_id"] == b.json()["last_event_id"]


def test_same_request_id_different_payload_is_conflict(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    crid = "crq-conflict-0001"
    a, _ = _transition(client, ctx, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert a.status_code == 200

    b, _ = _transition(
        client, ctx, sid, "CANCELLED", 0, crid=crid,
        payload={"to": "CANCELLED", "note": "different"},
        ids=new_ids,
    )
    assert b.status_code == 409, b.text
    assert b.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_idempotency_scope_includes_actor(client, new_ids, ctx_factory) -> None:
    """同一 client_request_id 但不同 actor → 视为不同命令（不得互相吞掉）。"""
    ctx_a = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_a, new_ids)
    sid = r.json()["session"]["id"]

    crid = "crq-actor-scope"
    a, _ = _transition(client, ctx_a, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert a.status_code == 200

    # 另一个 actor（同 tenant，无对象级 scope）→ 必须是 403 而不是"返回他人结果"
    ctx_b = ctx_factory(tenant_id=new_ids["tenant_id"])
    b, _ = _transition(client, ctx_b, sid, "ACTIVE", 0, crid=crid, ids=new_ids)
    assert b.status_code in (403, 404), b.text


def test_stale_revision_returns_409_with_current(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    a, _ = _transition(client, ctx, sid, "ACTIVE", 0, ids=new_ids)
    assert a.status_code == 200

    stale, _ = _transition(client, ctx, sid, "RUNNING", 0, ids=new_ids)
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "REVISION_CONFLICT"
    assert err["current"] == 1
    assert err["expected"] == 0
