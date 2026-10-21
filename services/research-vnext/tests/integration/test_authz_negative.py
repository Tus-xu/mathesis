"""RV02 · 对象级鉴权负向测试（AUTHZ-NEGATIVE-TESTS 证据来源）。

铁律：**不只依赖 JWT 有效**。每个带 session/project/object ID 的 endpoint
都必须执行 tenant / project / owner / capability 对象级校验。
"""

from __future__ import annotations

import uuid

from tests.integration.conftest import auth_headers, create_session, envelope


def _hdr(ctx):
    return auth_headers(ctx)


def test_missing_access_context_is_401(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    g = client.get(f"/api/research-v1/sessions/{sid}")
    assert g.status_code == 401, g.text
    assert g.json()["error"]["code"] == "ACCESS_CONTEXT_MISSING"


def test_tampered_signature_is_401(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    g = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx, tamper=True))
    assert g.status_code == 401, g.text
    assert g.json()["error"]["code"] == "ACCESS_CONTEXT_INVALID_SIGNATURE"


def test_cross_tenant_read_is_403(client, new_ids, ctx_factory) -> None:
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]

    other_tenant = uuid.uuid4()
    ctx_other = ctx_factory(tenant_id=other_tenant)
    g = client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx_other))
    assert g.status_code == 403, g.text
    assert g.json()["error"]["code"] == "TENANT_MISMATCH"


def test_cross_tenant_is_403_not_404_leak_safe(client, new_ids, ctx_factory) -> None:
    """跨租户必须 403（明确拒绝），不得用 404 掩盖、也不得 200。"""
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]
    ctx_other = ctx_factory(tenant_id=uuid.uuid4())
    assert client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx_other)).status_code == 403
    assert client.get(f"/api/research-v1/sessions/{uuid.uuid4()}", headers=_hdr(ctx_other)).status_code == 404


def test_cross_project_same_tenant_is_403(client, new_ids, ctx_factory) -> None:
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]

    ctx_other_project = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        resource_scopes=[{"kind": "project", "id": str(uuid.uuid4()), "role": "owner"}],
    )
    g = client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx_other_project))
    assert g.status_code == 403, g.text
    assert g.json()["error"]["code"] == "PROJECT_MISMATCH"


def test_non_owner_without_capability_is_403(client, new_ids, ctx_factory) -> None:
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]

    ctx_other = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        capabilities=["workspace.research.enter"],  # 缺 research.session.write
        resource_scopes=[{"kind": "session", "id": sid, "role": "viewer"}],
    )
    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx_other["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "viewer"},
        payload={"to": "ACTIVE"},
        expected_revision=0,
        tenant_id=new_ids["tenant_id"],
    )
    resp = client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=_hdr(ctx_other))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "CAPABILITY_MISSING"


def test_scope_object_id_must_match_target(client, new_ids, ctx_factory) -> None:
    """scope 携带的 object id 必须与目标对象一致（禁止 identity-only 放行）。"""
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]

    ctx = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        resource_scopes=[{"kind": "session", "id": str(uuid.uuid4()), "role": "owner"}],
    )
    g = client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx))
    assert g.status_code == 403, g.text


def test_self_scope_cannot_stand_in_for_object_check(client, new_ids, ctx_factory) -> None:
    """scope.kind='self' + 无 session scope → 拒绝（P00 ResourceScope 语义）。"""
    ctx_owner = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx_owner, new_ids)
    sid = r.json()["session"]["id"]

    ctx = ctx_factory(
        tenant_id=new_ids["tenant_id"],
        resource_scopes=[{"kind": "self", "id": None}],
    )
    g = client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx))
    assert g.status_code == 403, g.text


def test_revocation_bumps_security_floor_then_403(client, new_ids, ctx_factory) -> None:
    """撤权：security_version 低于服务端 floor → 403（不依赖 JWT 有效）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"], security_version=5)
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    assert client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx)).status_code == 200

    # 撤权：把该 actor 的 floor 抬到 6
    from research_vnext.core.api.deps import set_security_floor

    set_security_floor(uuid.UUID(ctx["actor_id"]), 6)

    g = client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx))
    assert g.status_code == 403, g.text
    assert g.json()["error"]["code"] == "SECURITY_VERSION_REVOKED"

    # 重新登录后 sv=6 → 恢复
    ctx2 = dict(ctx)
    ctx2["security_version"] = 6
    assert client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx2)).status_code == 200


def test_revocation_is_floor_not_equality(client, new_ids, ctx_factory) -> None:
    from research_vnext.core.api.deps import set_security_floor

    ctx = ctx_factory(tenant_id=new_ids["tenant_id"], security_version=9)
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    set_security_floor(uuid.UUID(ctx["actor_id"]), 3)
    assert client.get(f"/api/research-v1/sessions/{sid}", headers=_hdr(ctx)).status_code == 200


def test_unknown_workspace_is_rejected(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"], workspace="teacher")
    r, _ = create_session(client, ctx, new_ids)
    # teacher workspace 无 research.session.write → 403
    assert r.status_code in (403, 401), r.text
