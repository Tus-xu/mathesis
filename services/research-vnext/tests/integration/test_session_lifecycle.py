"""RV02 · 会话生命周期集成测试（真实 PostgreSQL）。

覆盖：create / resume / get snapshot / 刷新恢复 / 并发 command / 进程重启恢复。
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import create_session


def test_create_session_returns_201_with_snapshot(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids, title="RV02-生命周期")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session"]["status"] == "DRAFT"
    assert body["session"]["revision"] == 0
    assert body["session"]["tenant_id"] == str(new_ids["tenant_id"])
    assert body["session"]["project_id"] == str(new_ids["project_id"])
    assert body["session"]["owner_id"] == ctx["actor_id"]


def test_get_snapshot_after_create(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    g = client.get(f"/api/research-v1/sessions/{sid}", headers=__import__("tests.integration.conftest", fromlist=["x"]).auth_headers(ctx))
    assert g.status_code == 200, g.text
    snap = g.json()
    assert snap["session"]["id"] == sid
    assert snap["revision"] == snap["session"]["revision"]
    assert "summary_ref" in snap["session"]


def test_refresh_recovery_same_snapshot_across_instances(client, new_ids, ctx_factory, app_env) -> None:
    """刷新恢复：另起一个 app 实例（模拟刷新/新请求）读到同一权威快照。"""
    from fastapi.testclient import TestClient

    from research_vnext.core.api.app import create_app
    from tests.integration.conftest import auth_headers

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids, title="刷新恢复")
    sid = r.json()["session"]["id"]

    with TestClient(create_app()) as c2:
        g = c2.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx))
    assert g.status_code == 200
    assert g.json()["session"]["revision"] == r.json()["session"]["revision"]
    assert g.json()["session"]["title"] == "刷新恢复"


def test_resume_returns_snapshot_and_events(client, new_ids, ctx_factory) -> None:
    from tests.integration.conftest import auth_headers

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    res = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": None},
        headers=auth_headers(ctx),
    )
    assert res.status_code == 200, res.text
    b = res.json()
    assert b["session"]["id"] == sid
    assert b["fallback"] in ("NONE", "SNAPSHOT_REQUIRED")
    assert isinstance(b["events"], list)
    assert len(b["events"]) >= 1


def test_concurrent_commands_only_one_wins(client, new_ids, ctx_factory) -> None:
    """并发命令：revision 乐观锁 —— 只有一个成功，其余 409 REVISION_CONFLICT。"""
    from tests.integration.conftest import auth_headers, envelope

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    rev = r.json()["session"]["revision"]

    payload = {"to": "ACTIVE"}
    bodies = [
        envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload=payload,
            expected_revision=rev,
            tenant_id=new_ids["tenant_id"],
        )
        for _ in range(5)
    ]
    codes = []
    for b in bodies:
        resp = client.post(
            f"/api/research-v1/sessions/{sid}:transition",
            json=b,
            headers=auth_headers(ctx),
        )
        codes.append(resp.status_code)

    oks = [c for c in codes if c == 200]
    conflicts = [c for c in codes if c == 409]
    assert len(oks) == 1, f"并发下应恰好 1 个成功，实际 {codes}"
    assert len(conflicts) == 4, f"其余应为 409，实际 {codes}"


def test_conflict_body_is_machine_readable(client, new_ids, ctx_factory) -> None:
    from tests.integration.conftest import auth_headers, envelope

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "ACTIVE"},
        expected_revision=99,
        tenant_id=new_ids["tenant_id"],
    )
    resp = client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))
    assert resp.status_code == 409, resp.text
    err = resp.json()["error"]
    assert err["code"] == "REVISION_CONFLICT"
    assert err["expected"] == 99
    assert err["current"] == 0
    assert "snapshot_ref" in err


def test_revision_is_strictly_monotonic_over_api(client, new_ids, ctx_factory) -> None:
    from tests.integration.conftest import auth_headers, envelope

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    revs = [r.json()["session"]["revision"]]

    for target in ("ACTIVE", "WAITING_CONFIRMATION"):
        b = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload={"to": target},
            expected_revision=revs[-1],
            tenant_id=new_ids["tenant_id"],
        )
        resp = client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))
        assert resp.status_code == 200, resp.text
        revs.append(resp.json()["session"]["revision"])

    assert revs == sorted(revs) and len(set(revs)) == len(revs)


def test_cannot_skip_confirmation_via_api(client, new_ids, ctx_factory) -> None:
    from tests.integration.conftest import auth_headers, envelope

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    def tr(to: str, rev: int, extra_payload=None):
        b = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload={"to": to, **(extra_payload or {})},
            expected_revision=rev,
            tenant_id=new_ids["tenant_id"],
        )
        return client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))

    a = tr("ACTIVE", 0)
    assert a.status_code == 200
    bad = tr("READY_FOR_REVIEW", a.json()["session"]["revision"])
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_partial_cannot_become_completed_via_api(client, new_ids, ctx_factory) -> None:
    from tests.integration.conftest import auth_headers, envelope

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    def tr(to: str, rev: int, extra=None):
        b = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload={"to": to, **(extra or {})},
            expected_revision=rev,
            tenant_id=new_ids["tenant_id"],
        )
        return client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))

    a = tr("ACTIVE", 0)
    run = tr("RUNNING", a.json()["session"]["revision"])
    p = tr(
        "PARTIAL",
        run.json()["session"]["revision"],
        {"partial_reason": {"succeeded": ["parse"], "failed": ["search"]}},
    )
    assert p.status_code == 200, p.text
    assert p.json()["session"]["status"] == "PARTIAL"

    bad = tr("COMPLETED", p.json()["session"]["revision"])
    assert bad.status_code == 409
    assert bad.json()["error"]["code"] in ("INVALID_TRANSITION", "PARTIAL_NOT_COMPLETED")
