"""RV02 · 版本化 summary（要求 9：不得用 AI 编造 summary）。"""

from __future__ import annotations

import uuid

from tests.integration.conftest import auth_headers, create_session, envelope


def test_summary_is_versioned_and_deterministic(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    s1 = client.get(f"/api/research-v1/sessions/{sid}/summary", headers=auth_headers(ctx))
    assert s1.status_code == 200, s1.text
    b1 = s1.json()
    assert b1["summary_version"] >= 1
    assert "generated_by" in b1
    assert b1["generated_by"] != "ai", "RV02 禁止 AI 编造 summary"


def test_summary_content_is_deterministic_domain_data(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids, title="确定性摘要")
    sid = r.json()["session"]["id"]

    a = client.get(f"/api/research-v1/sessions/{sid}/summary", headers=auth_headers(ctx)).json()
    b = client.get(f"/api/research-v1/sessions/{sid}/summary", headers=auth_headers(ctx)).json()
    assert a["content"] == b["content"], "summary 必须确定性，不得随机/AI 生成"
    assert a["content"]["title"] == "确定性摘要"
    assert a["content"]["status"] == "DRAFT"


def test_summary_bumps_with_revision(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    s0 = client.get(f"/api/research-v1/sessions/{sid}/summary", headers=auth_headers(ctx)).json()

    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": "ACTIVE"},
        expected_revision=0,
        tenant_id=new_ids["tenant_id"],
    )
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx)).status_code == 200

    s1 = client.get(f"/api/research-v1/sessions/{sid}/summary", headers=auth_headers(ctx)).json()
    assert s1["summary_version"] > s0["summary_version"]
    assert s1["content"]["status"] == "ACTIVE"


def test_summary_history_is_retrievable(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    h = client.get(f"/api/research-v1/sessions/{sid}/summary/history", headers=auth_headers(ctx))
    assert h.status_code == 200, h.text
    assert isinstance(h.json()["items"], list)
