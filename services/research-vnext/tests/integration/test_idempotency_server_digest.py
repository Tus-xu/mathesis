"""WP-A FIX-A1 · 幂等冲突必须以**服务端摘要**为准（评审 P1-1 回归测试）。

背景：修复前的判定是
    `existing.idempotency_fingerprint != cmd.idempotency_fingerprint`
—— 把调用方**自报**的字符串与这个字符串的存档副本比较，`payload` 从未在服务端被哈希。
于是"同一 `client_request_id` + 同一 fingerprint + 同一 `payload_ref` + **不同 payload**"
会被当作重放：返回 200 REPLAYED，新命令的 mutation 被**静默丢弃**。

**为什么原有测试看不见它**：`tests/conftest.envelope()` 用 `fingerprint(payload)` 派生
`idempotency_fingerprint`，payload 一变 fingerprint 必变，冲突检查总能命中。本文件的用例
**刻意解耦**这三个字段，直接构造信封。

红→绿证据见 `docs/FIX-EVIDENCE.md`。
"""

from __future__ import annotations

import uuid

from tests.conftest import auth_headers, fingerprint, make_access_context, traceparent
from tests.integration.conftest import create_session

FIXED_FP = "a" * 64
FIXED_PAYLOAD_REF = "inline:fix-a1-fixed"


def _envelope_raw(
    ctx: dict,
    ids: dict,
    *,
    payload: dict,
    crid: str,
    rev: int = 0,
    fp: str = FIXED_FP,
    payload_ref: str = FIXED_PAYLOAD_REF,
    command_type: str = "session.transition",
    session_id: str | None = None,
) -> dict:
    """构造信封，但 **fingerprint / payload_ref 与 payload 完全解耦**。

    这正是原测试做不到的事 —— 也是这个缺口能存活至今的原因。
    """
    scope = (
        {"kind": "session", "id": session_id, "role": "owner"}
        if session_id
        else {"kind": "project", "id": str(ids["project_id"]), "role": "owner"}
    )
    return {
        "client_request_id": crid,
        "expected_revision": rev,
        "actor_id": ctx["actor_id"],
        "scope": scope,
        "traceparent": traceparent(),
        "deadline": "2030-01-01T00:00:00Z",
        "idempotency_fingerprint": fp,
        "command_type": command_type,
        "payload_ref": payload_ref,
        "tenant_id": str(ids["tenant_id"]),
        "payload": payload,
    }


def _new_session(client, ctx, ids, title="fix-a1"):
    r, _ = create_session(client, ctx, ids, title=title)
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


# --------------------------------------------------------------------------
# 对抗用例：修复前必然失败
# --------------------------------------------------------------------------


def test_same_crid_same_fp_same_payload_ref_but_different_payload_is_conflict(
    client, new_ids, ctx_factory
) -> None:
    """评审的核心反例：三个字段全不变，只改 payload → 必须 409，且不得发生任何 mutation。

    修复前：返回 200 REPLAYED，`CANCELLED` 意图被静默丢弃。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _new_session(client, ctx, new_ids)
    h = auth_headers(ctx)
    crid = "crq-fixa1-same-fp"

    first = _envelope_raw(ctx, new_ids, payload={"to": "ACTIVE"}, crid=crid, session_id=sid)
    r1 = client.post(f"/api/research-v1/sessions/{sid}:transition", json=first, headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.headers.get("X-Idempotency-Status") == "EXECUTED"
    assert r1.json()["session"]["status"] == "ACTIVE"

    # 同一 crid / 同一 fingerprint / 同一 payload_ref，**payload 语义不同**
    second = _envelope_raw(ctx, new_ids, payload={"to": "CANCELLED"}, crid=crid, session_id=sid)
    assert second["idempotency_fingerprint"] == first["idempotency_fingerprint"]
    assert second["payload_ref"] == first["payload_ref"]
    assert second["payload"] != first["payload"]

    r2 = client.post(f"/api/research-v1/sessions/{sid}:transition", json=second, headers=h)
    assert r2.status_code == 409, (
        f"不同 payload 被当作重放吞掉了！HTTP {r2.status_code} body={r2.text}"
    )
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # 且必须**没有**发生任何 mutation：状态仍是 ACTIVE、revision 未推进
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=h)
    assert snap.status_code == 200
    assert snap.json()["session"]["status"] == "ACTIVE", "被拒绝的命令不得改变服务端状态"
    assert snap.json()["revision"] == 1, "被拒绝的命令不得推进 revision"


def test_same_crid_same_fp_different_payload_on_create_is_conflict(
    client, new_ids, ctx_factory
) -> None:
    """同一形态在 create 路径同样必须 409（不只是 transition）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    h = auth_headers(ctx)
    crid = "crq-fixa1-create"

    base_payload = {
        "title": "original title",
        "mode": "MODELING",
        "project_id": str(new_ids["project_id"]),
        "tenant_id": str(new_ids["tenant_id"]),
        "policy_refs": ["policy:research.default@v1"],
    }
    e1 = _envelope_raw(ctx, new_ids, payload=base_payload, crid=crid,
                       command_type="session.create", payload_ref="inline:fix-a1-create")
    r1 = client.post("/api/research-v1/sessions:create", json=e1, headers=h)
    assert r1.status_code == 201, r1.text
    original_id = r1.json()["session"]["id"]

    mutated = dict(base_payload)
    mutated["title"] = "MUTATED title"
    e2 = _envelope_raw(ctx, new_ids, payload=mutated, crid=crid,
                       command_type="session.create", payload_ref="inline:fix-a1-create")
    r2 = client.post("/api/research-v1/sessions:create", json=e2, headers=h)
    assert r2.status_code == 409, f"mutated create 被吞掉了: HTTP {r2.status_code} {r2.text}"
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # 仍然只有最初那一个会话
    g = client.get(f"/api/research-v1/sessions/{original_id}", headers=h)
    assert g.status_code == 200
    assert g.json()["session"]["title"] == "original title"


def test_cross_field_digest_covers_command_type(client, new_ids, ctx_factory) -> None:
    """摘要必须覆盖 `command_type`：同 crid / 同 payload / 同 fp，命令类型不同即冲突。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _new_session(client, ctx, new_ids)
    h = auth_headers(ctx)
    crid = "crq-fixa1-cmdtype"

    e1 = _envelope_raw(ctx, new_ids, payload={"to": "ACTIVE"}, crid=crid,
                       command_type="session.transition", session_id=sid)
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=e1, headers=h).status_code == 200

    e2 = _envelope_raw(ctx, new_ids, payload={"to": "ACTIVE"}, crid=crid,
                       command_type="session.something_else", session_id=sid)
    r2 = client.post(f"/api/research-v1/sessions/{sid}:transition", json=e2, headers=h)
    assert r2.status_code == 409, f"command_type 未纳入摘要: HTTP {r2.status_code}"
    assert r2.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


# --------------------------------------------------------------------------
# 快乐路径：不得因为修复而破坏合法重试
# --------------------------------------------------------------------------


def test_legitimate_replay_still_replays_original(client, new_ids, ctx_factory) -> None:
    """合法重放（payload / fp / payload_ref 全同）必须仍返回 REPLAYED + 原始响应体。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _new_session(client, ctx, new_ids)
    h = auth_headers(ctx)
    crid = "crq-fixa1-legit"

    e = _envelope_raw(ctx, new_ids, payload={"to": "ACTIVE"}, crid=crid, session_id=sid)
    a = client.post(f"/api/research-v1/sessions/{sid}:transition", json=e, headers=h)
    assert a.status_code == 200
    assert a.headers.get("X-Idempotency-Status") == "EXECUTED"

    b = client.post(f"/api/research-v1/sessions/{sid}:transition", json=e, headers=h)
    assert b.status_code == 200, b.text
    assert b.headers.get("X-Idempotency-Status") == "REPLAYED"
    assert b.json()["session"] == a.json()["session"], "重放必须返回与首次相同的结果"
    assert b.json()["last_event_id"] == a.json()["last_event_id"]
    assert b.json()["revision"] == 1, "重放不得推进 revision"

    # 未产生第二条幂等记录
    snap = client.get(f"/api/research-v1/sessions/{sid}", headers=h)
    assert snap.json()["revision"] == 1


def test_replay_after_other_commands_still_works(client, new_ids, ctx_factory) -> None:
    """重放发生在其它命令推进 revision 之后，仍须返回**原始**结果（幂等语义与乐观锁解耦）。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _new_session(client, ctx, new_ids)
    h = auth_headers(ctx)
    crid = "crq-fixa1-late-replay"

    e = _envelope_raw(ctx, new_ids, payload={"to": "ACTIVE"}, crid=crid, session_id=sid)
    a = client.post(f"/api/research-v1/sessions/{sid}:transition", json=e, headers=h)
    assert a.status_code == 200

    e2 = _envelope_raw(ctx, new_ids, payload={"to": "RUNNING"}, crid="crq-fixa1-next", rev=1, session_id=sid)
    assert client.post(f"/api/research-v1/sessions/{sid}:transition", json=e2, headers=h).status_code == 200

    b = client.post(f"/api/research-v1/sessions/{sid}:transition", json=e, headers=h)
    assert b.status_code == 200, b.text
    assert b.headers.get("X-Idempotency-Status") == "REPLAYED"
    assert b.json()["revision"] == 1, "重放返回的是首次执行的快照，不是当前状态"
