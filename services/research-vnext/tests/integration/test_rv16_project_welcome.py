"""RV16 · ResearchProject + 欢迎页集成测试（真实 PostgreSQL + Alembic schema）。

覆盖 K5（幂等 + revision 闸门）/ K9（双前缀）/ K1（欢迎页聚合真实数据）。
"""

from __future__ import annotations

import uuid

from tests.conftest import auth_headers


def _ctx(ctx_factory, actor_id, tenant_id):
    return ctx_factory(actor_id=actor_id, tenant_id=tenant_id)


def test_welcome_requires_access_context(client):
    resp = client.get("/api/research-vnext/welcome")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ACCESS_CONTEXT_MISSING"


def test_project_crud_and_revision_gate(client, ctx_factory):
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ctx = _ctx(ctx_factory, actor_id, tenant_id)
    headers = auth_headers(ctx)
    crid = f"rv16-{uuid.uuid4().hex[:12]}"

    # 创建
    r = client.post(
        "/api/research-vnext/projects",
        json={"name": "项目A", "client_request_id": crid},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    project = r.json()["project"]
    pid = project["id"]
    assert project["revision"] == 1
    assert project["tenant_id"] == str(tenant_id)
    assert project["owner_id"] == str(actor_id)

    # 幂等重放：同 crid 同内容 → 同项目
    r2 = client.post(
        "/api/research-vnext/projects",
        json={"name": "项目A", "client_request_id": crid},
        headers=headers,
    )
    assert r2.json()["project"]["id"] == pid

    # 幂等冲突：同 crid 异内容 → 409
    r3 = client.post(
        "/api/research-vnext/projects",
        json={"name": "项目B", "client_request_id": crid},
        headers=headers,
    )
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # 列表
    r4 = client.get("/api/research-vnext/projects", headers=headers)
    assert r4.status_code == 200
    assert any(p["id"] == pid for p in r4.json()["items"])

    # revision 闸门：正确 revision → 200 且 revision+1
    r5 = client.patch(
        f"/api/research-vnext/projects/{pid}",
        json={"name": "项目A2", "expected_revision": 1, "client_request_id": f"rv16-p-{uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert r5.status_code == 200, r5.text
    assert r5.json()["revision"] == 2

    # revision 冲突 → 409 REVISION_CONFLICT
    r6 = client.patch(
        f"/api/research-vnext/projects/{pid}",
        json={"name": "项目A3", "expected_revision": 1, "client_request_id": f"rv16-p-{uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert r6.status_code == 409
    assert r6.json()["error"]["code"] == "REVISION_CONFLICT"

    # 对象级鉴权：别的 actor 看不到（404，不区分"不存在/不属于你"）
    other = auth_headers(_ctx(ctx_factory, uuid.uuid4(), tenant_id))
    r7 = client.get(f"/api/research-vnext/projects/{pid}", headers=other)
    assert r7.status_code == 404
    assert r7.json()["error"]["code"] == "NOT_FOUND"


def test_dual_prefix_reachability(client, ctx_factory):
    """K9：新端点必须双前缀均可达。"""
    ctx = _ctx(ctx_factory, uuid.uuid4(), uuid.uuid4())
    headers = auth_headers(ctx)
    for prefix in ("/api/research-v1", "/api/research-vnext"):
        r = client.get(f"{prefix}/welcome", headers=headers)
        assert r.status_code == 200, f"{prefix}: {r.text}"
        assert len(r.json()["capabilities"]) == 4
        r2 = client.get(f"{prefix}/projects", headers=headers)
        assert r2.status_code == 200


def test_welcome_aggregate_shape(client, ctx_factory):
    actor_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    headers = auth_headers(_ctx(ctx_factory, actor_id, tenant_id))
    r = client.get("/api/research-vnext/welcome", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"greeting_name", "recent_projects", "recent_literature", "capabilities"}
    assert isinstance(body["recent_projects"], list)
    assert isinstance(body["recent_literature"], list)
    keys = {c["key"] for c in body["capabilities"]}
    assert keys == {"read", "write", "retrieve", "export"}
