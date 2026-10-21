"""RV02 · Outbox 持久化 + PendingAction 审计（真实 PostgreSQL）。

- domain event 与 outbox 在**同一 DB transaction** 写入
- RV02 只到 outbox durable record，**不伪造 worker 完成**
- PendingAction accept / reject / expire / cancel 均有审计
- 接受前**重新校验 capability 与 revision**
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg

from tests.integration.conftest import auth_headers, create_session, envelope


def _sync_url(pg_url: str) -> str:
    return pg_url.replace("+psycopg", "").replace("+asyncpg", "")


def test_outbox_rows_written_in_same_transaction(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    with psycopg.connect(_sync_url(pg_url)) as c:
        ev = c.execute(
            f'select count(*) from "{it_schema}".domain_event where session_id = %s', (sid,)
        ).fetchone()[0]
        ob = c.execute(
            f'select count(*) from "{it_schema}".outbox_event where session_id = %s', (sid,)
        ).fetchone()[0]
    assert ev >= 1, "未写入 domain_event"
    assert ob >= 1, "未写入 outbox_event"
    assert ob == ev, f"outbox 与 domain_event 必须同事务一一对应: {ob} vs {ev}"


def test_outbox_is_durable_and_unpublished(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    """RV02 只到 durable record：不得出现已发布/已完成的假象。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    with psycopg.connect(_sync_url(pg_url)) as c:
        row = c.execute(
            f'select status, published_at, attempts from "{it_schema}".outbox_event limit 1'
        ).fetchone()
    assert row is not None
    assert row[0] == "PENDING"
    assert row[1] is None, "RV02 不实现 relay/worker —— 不得有 published_at"
    assert row[2] == 0


def test_pending_action_lifecycle_is_audited(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    # 进入 WAITING_CONFIRMATION 会产生一个 PendingAction
    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "ACTIVE"},
        expected_revision=0,
        tenant_id=new_ids["tenant_id"],
    )
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx)).status_code == 200

    b2 = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "WAITING_CONFIRMATION"},
        expected_revision=1,
        tenant_id=new_ids["tenant_id"],
    )
    resp = client.post(f"/api/research-v1/sessions/{sid}:transition", json=b2, headers=auth_headers(ctx))
    assert resp.status_code == 200, resp.text

    lst = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx))
    assert lst.status_code == 200
    items = lst.json()["items"]
    assert len(items) >= 1
    pid = items[0]["id"]
    assert items[0]["status"] == "PENDING"

    acc = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:accept",
        json={"expected_revision": resp.json()["session"]["revision"]},
        headers=auth_headers(ctx),
    )
    assert acc.status_code == 200, acc.text

    with psycopg.connect(_sync_url(pg_url)) as c:
        audits = c.execute(
            f'select action from "{it_schema}".pending_action_audit where pending_action_id = %s order by id',
            (pid,),
        ).fetchall()
    acts = [a[0] for a in audits]
    assert "CREATED" in acts
    assert "ACCEPTED" in acts


def test_reject_is_audited(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _to_waiting(client, ctx, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx)).json()["items"][0]["id"]

    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:reject",
        json={"reason": "不同意"},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 200, resp.text
    with psycopg.connect(_sync_url(pg_url)) as c:
        acts = [a[0] for a in c.execute(
            f'select action from "{it_schema}".pending_action_audit where pending_action_id = %s', (pid,)
        ).fetchall()]
    assert "REJECTED" in acts


def test_expire_is_audited(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _to_waiting(client, ctx, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx)).json()["items"][0]["id"]

    with psycopg.connect(_sync_url(pg_url)) as c:
        c.execute(
            f'update "{it_schema}".pending_action set expires_at = %s where id = %s',
            (datetime.now(timezone.utc) - timedelta(hours=1), pid),
        )
        c.commit()

    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:accept",
        json={"expected_revision": 99},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "PENDING_ACTION_EXPIRED"

    with psycopg.connect(_sync_url(pg_url)) as c:
        acts = [a[0] for a in c.execute(
            f'select action from "{it_schema}".pending_action_audit where pending_action_id = %s', (pid,)
        ).fetchall()]
    assert "EXPIRED" in acts


def test_cancel_is_audited(client, new_ids, ctx_factory, pg_url, it_schema) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _to_waiting(client, ctx, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx)).json()["items"][0]["id"]

    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:cancel",
        json={"reason": "用户取消"},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 200, resp.text
    with psycopg.connect(_sync_url(pg_url)) as c:
        acts = [a[0] for a in c.execute(
            f'select action from "{it_schema}".pending_action_audit where pending_action_id = %s', (pid,)
        ).fetchall()]
    assert "CANCELLED" in acts


def test_accept_rechecks_capability(client, new_ids, ctx_factory) -> None:
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]
    rev = _to_waiting(client, ctx_owner, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx_owner)).json()["items"][0]["id"]

    # 该 actor **有对象级 scope**（可通过入口校验），但**缺 write capability**
    # —— 命中"接受前重新校验 capability"这一层
    ctx_weak = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        capabilities=["workspace.research.enter"],
        resource_scopes=[{"kind": "session", "id": sid, "role": "owner"}],
    )
    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:accept",
        json={"expected_revision": rev},
        headers=auth_headers(ctx_weak),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CAPABILITY_MISSING"


def test_accept_without_object_scope_rejected_even_with_capability(client, new_ids, ctx_factory) -> None:
    """纵深防御：无对象级 scope 者即便 capability 齐全也不得接受他人 pending action。"""
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]
    rev = _to_waiting(client, ctx_owner, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx_owner)).json()["items"][0]["id"]

    ctx_other = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        capabilities=["workspace.research.enter", "research.session.write"],
        resource_scopes=[],  # 无对象级 scope，且非 owner
    )
    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:accept",
        json={"expected_revision": rev},
        headers=auth_headers(ctx_other),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "OBJECT_SCOPE_MISSING"


def test_accept_rechecks_revision(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    _to_waiting(client, ctx, new_ids, sid)
    pid = client.get(f"/api/research-v1/sessions/{sid}/pending-actions", headers=auth_headers(ctx)).json()["items"][0]["id"]

    resp = client.post(
        f"/api/research-v1/sessions/{sid}/pending-actions/{pid}:accept",
        json={"expected_revision": 0},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "REVISION_CONFLICT"


def _to_waiting(client, ctx, ids, sid) -> int:
    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "ACTIVE"},
        expected_revision=0,
        tenant_id=ids["tenant_id"],
    )
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx)).status_code == 200
    b2 = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "WAITING_CONFIRMATION"},
        expected_revision=1,
        tenant_id=ids["tenant_id"],
    )
    resp = client.post(f"/api/research-v1/sessions/{sid}:transition", json=b2, headers=auth_headers(ctx))
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]["revision"]
