"""WP-A 追加（LEAD ADDENDUM A-2）· 双前缀真实等价性（真实 PostgreSQL）。

前端（`frontend/src/features/research-vnext/api/researchApi.ts`）使用
`RESEARCH_API_BASE = '/api/research-vnext'`，而服务原先只挂载 `/api/research-v1`，
导致前端所有 research 调用 404。本用例证明修复后**两个前缀都可真实读写同一份状态**。

放在 integration 而非 contracts：需要真实 DB 与 `client` 夹具，
且必须与 `Base.metadata.schema`（import 期求值）指向同一个 schema。
"""

from __future__ import annotations

import uuid

from research_vnext.core.config import API_PREFIX_V1, API_PREFIX_VNEXT

PREFIXES = (API_PREFIX_V1, API_PREFIX_VNEXT)


def _hdr(ctx):
    from tests.conftest import auth_headers

    return auth_headers(ctx)


def test_session_snapshot_is_equivalent_under_both_prefixes(client, ctx_factory) -> None:
    """读路由：两个前缀都 200，且返回**完全等价**的快照。"""
    from tests.integration.conftest import create_session

    ctx = ctx_factory()
    ids = {
        "tenant_id": uuid.UUID(ctx["tenant_id"]),
        "project_id": uuid.uuid4(),
        "owner_id": uuid.UUID(ctx["actor_id"]),
    }
    resp, _ = create_session(client, ctx, ids, title="prefix-parity")
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session"]["id"]

    h = _hdr(ctx)
    seen = {}
    for prefix in PREFIXES:
        g = client.get(f"{prefix}/sessions/{sid}", headers=h)
        assert g.status_code == 200, f"{prefix} 返回 {g.status_code}: {g.text}"
        seen[prefix] = g.json()

    assert seen[API_PREFIX_V1] == seen[API_PREFIX_VNEXT], "两个前缀返回的快照不一致"
    assert seen[API_PREFIX_V1]["session"]["id"] == sid


def test_write_route_through_vnext_prefix_affects_same_state(client, ctx_factory) -> None:
    """写路由：v1 推进 revision 后，vnext 发同一起点命令必须命中 409 ——
    证明新前缀作用在**同一份**状态上，而不是另建了一套。
    """
    from tests.conftest import envelope
    from tests.integration.conftest import create_session

    ctx = ctx_factory()
    ids = {
        "tenant_id": uuid.UUID(ctx["tenant_id"]),
        "project_id": uuid.uuid4(),
        "owner_id": uuid.UUID(ctx["actor_id"]),
    }
    resp, _ = create_session(client, ctx, ids, title="prefix-write")
    sid = resp.json()["session"]["id"]
    h = _hdr(ctx)

    def _transition(prefix: str, crid: str, to: str = "ACTIVE", rev: int = 0):
        cmd = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "project", "id": str(ids["project_id"]), "role": "owner"},
            payload={"to": to},
            expected_revision=rev,
            tenant_id=ids["tenant_id"],
            client_request_id=crid,
        )
        return client.post(f"{prefix}/sessions/{sid}:transition", json=cmd, headers=h)

    r1 = _transition(API_PREFIX_V1, "crq-prefix-parity-w1")
    assert r1.status_code == 200, f"v1 写路由失败: {r1.text}"
    assert r1.json()["revision"] == 1

    # 经**新前缀**发同一起点命令 → 必须 409，且 current 就是 v1 推进后的 1
    r2 = _transition(API_PREFIX_VNEXT, "crq-prefix-parity-w2")
    assert r2.status_code == 409, f"vnext 写路由未作用到同一份状态: {r2.text}"
    assert r2.json()["error"]["code"] == "REVISION_CONFLICT"
    assert r2.json()["error"]["current"] == 1

    # 反向：经新前缀推进到合法的新 revision，再用旧前缀读，必须看到新状态
    # ACTIVE -> RUNNING 是状态机允许的转换（注意：不存在 PAUSED 状态，
    # RV02 的 9 态取代了手册旧表述 —— 见 domain/state_machine.py 模块 docstring）
    r3 = _transition(API_PREFIX_VNEXT, "crq-prefix-parity-w3", to="RUNNING", rev=1)
    assert r3.status_code == 200, f"vnext 写路由失败: {r3.text}"
    assert r3.json()["revision"] == 2

    g = client.get(f"{API_PREFIX_V1}/sessions/{sid}", headers=h)
    assert g.status_code == 200
    assert g.json()["revision"] == 2, "旧前缀未看到新前缀写入的状态"
    assert g.json()["session"]["status"] == "RUNNING"


def test_unknown_prefix_still_404s(client, ctx_factory) -> None:
    """反向守卫：不存在的前缀必须 404 —— 证明匹配是精确前缀，不是前缀模糊包含。"""
    ctx = ctx_factory()
    h = _hdr(ctx)
    for bogus in ("/api/research", "/api/research-v2", "/research-vnext/sessions"):
        r = client.get(f"{bogus}/sessions/{uuid.uuid4()}", headers=h)
        assert r.status_code == 404, f"{bogus} 意外返回 {r.status_code}"
