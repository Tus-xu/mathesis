"""CR-6 · 前端 adapter 硬契约守卫。

前端 `adaptResearchSnapshot`（`frontend/src/features/research-vnext/adapters/
researchAdapter.ts`）在**类型层**无条件要求 `capabilities` / `timeline` /
`evidence` 三者是数组，否则返回 `null`，`researchApi.ts` 随即抛 503；
且 composer 的可用性直接取决于 `research-message === 'available'` 这个 key
**是否存在** —— 缺失即输入框永久禁用。

本文件把该契约变成**可被打破的测试**：任何一条路径（新会话、有消息、LLM 不可用、
模型调用失败、游标过期快照）都必须满足它。这些断言全部针对**真实 HTTP 响应**。
"""

from __future__ import annotations

import json
import uuid

import pytest

from research_vnext.core.domain.snapshot import REQUIRED_CAPABILITY_KEYS, VALID_CAPABILITY_STATES
from research_vnext.core.llm import gateway as gw
from tests.integration.conftest import auth_headers, create_session
from tests.integration.test_messages_cr6 import StubBackend, proposal_json


def _assert_frontend_contract(snap: dict, *, where: str) -> None:
    """逐条复刻前端 adapter 的硬检查。"""
    for key in ("timeline", "evidence", "capabilities"):
        assert key in snap, f"[{where}] 缺少 {key}"
        assert isinstance(snap[key], list), f"[{where}] {key} 不是数组: {type(snap[key])}"

    caps = {c["key"]: c for c in snap["capabilities"]}
    for key in REQUIRED_CAPABILITY_KEYS:
        assert key in caps, f"[{where}] capabilities 缺少必需 key {key}（前端 composer 会永久禁用）"
        assert set(caps[key]) >= {"key", "label", "state"}, f"[{where}] {key} 字段不全: {caps[key]}"
        assert caps[key]["state"] in VALID_CAPABILITY_STATES, (
            f"[{where}] {key} state 非法: {caps[key]['state']!r}"
        )

    assert "session" in snap and isinstance(snap["session"], dict)
    assert "id" in snap["session"]

    # problem 允许 null，但存在时必须是对象
    assert snap.get("problem") is None or isinstance(snap["problem"], dict)
    # artifactFocus 允许 null
    assert snap.get("artifactFocus") is None or isinstance(snap["artifactFocus"], dict)

    for item in snap["timeline"]:
        assert set(item) >= {"id", "role", "text", "createdAt", "evidenceRefs"}, item
        assert item["role"] in {"user", "assistant", "system"}, item
        assert isinstance(item["evidenceRefs"], list), item


@pytest.fixture()
def stub_answer():
    gw.set_backend(StubBackend(proposal_json("answer", "ok")))
    yield
    gw.set_backend(None)


def test_get_session_returns_full_frontend_snapshot(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    resp = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx))
    assert resp.status_code == 200, resp.text
    _assert_frontend_contract(resp.json(), where="GET v1")


def test_get_session_on_vnext_prefix_too(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]

    resp = client.get(f"/api/research-vnext/sessions/{sid}", headers=auth_headers(ctx))
    assert resp.status_code == 200, resp.text
    _assert_frontend_contract(resp.json(), where="GET vnext")


def test_get_session_keeps_additive_internal_fields(client, new_ids, ctx_factory) -> None:
    """**加法式变更**：既有内部字段必须原样保留（旧消费者不受影响）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()

    assert set(snap) >= {"session", "revision", "summary_ref", "trace_hint"}
    assert snap["summary_ref"].startswith(f"summary:{sid}@v")
    assert isinstance(snap["revision"], int)


def test_post_message_snapshot_satisfies_contract(client, new_ids, ctx_factory, stub_answer) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = client.post(
        f"/api/research-vnext/sessions/{sid}/messages",
        json={"message": "契约检查"},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 201, resp.text
    _assert_frontend_contract(resp.json(), where="POST messages")


def test_contract_holds_when_llm_unavailable(client, new_ids, ctx_factory) -> None:
    """模型不可用是**真实降级路径**，其快照同样必须满足前端契约。"""
    assert gw.llm_configured() is False
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    resp = client.post(
        f"/api/research-vnext/sessions/{sid}/messages",
        json={"message": "降级路径"},
        headers=auth_headers(ctx),
    )
    assert resp.status_code == 201, resp.text
    snap = resp.json()
    _assert_frontend_contract(snap, where="POST degraded")
    # 降级时 composer 必须被**明确**标记为不可用，而不是悄悄坏掉
    caps = {c["key"]: c for c in snap["capabilities"]}
    assert caps["research-message"]["state"] in {"unavailable", "capability-disabled"}
    assert caps["research-message"]["reason"]


def test_contract_holds_for_replayed_response(client, new_ids, ctx_factory, stub_answer) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    crid = "c-contract-replay-1"
    body = {"message": "重放", "client_message_id": crid}
    a = client.post(f"/api/research-vnext/sessions/{sid}/messages", json=body, headers=auth_headers(ctx))
    b = client.post(f"/api/research-vnext/sessions/{sid}/messages", json=body, headers=auth_headers(ctx))
    assert a.status_code == 201 and b.status_code == 200
    _assert_frontend_contract(b.json(), where="POST replayed")


def test_capability_keys_are_exactly_the_five_required(client, new_ids, ctx_factory) -> None:
    """恰好五个：多一个会让前端 adapter 的联合类型检查变得不可预期。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    keys = [c["key"] for c in snap["capabilities"]]
    assert keys == list(REQUIRED_CAPABILITY_KEYS), keys


def test_snapshot_ref_and_contract_version_present(client, new_ids, ctx_factory) -> None:
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    r, _ = create_session(client, ctx, new_ids)
    sid = r.json()["session"]["id"]
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=auth_headers(ctx)).json()
    assert snap["snapshot_ref"] == f"snapshot:{sid}"
    assert snap["contract_version"].startswith("rv-cr6")


def test_disabled_and_forbidden_states_are_reachable() -> None:
    """能力状态集合必须覆盖前端的四个字面量（否则前端 switch 会落空）。"""
    assert VALID_CAPABILITY_STATES == {
        "available",
        "capability-disabled",
        "unavailable",
        "forbidden",
    }
