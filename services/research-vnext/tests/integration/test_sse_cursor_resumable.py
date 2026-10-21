"""WP-A FIX-A2 · SSE 续传游标必须是**可续传的真实水位**（评审 P1-2 回归测试）。

背景（修复前）：
1. 每段流的**首帧**是 `id: cursor-<millis>` 这种合成 id。按 SSE 规范客户端会把最后见到的
   `id:` 作为 `Last-Event-ID` 回传，而服务端认不出这个合成 id → 每次自动重连都退化成
   `SNAPSHOT_REQUIRED` 全量快照。
2. `routes.py` 恒调用 `events_after(after_id=0, limit=500)`，数值水位从未被使用；
   超过 500 条事件时游标可能不在结果集内 → 重复下发或丢事件。

本文件锁定修复后的语义：
- SSE 的 `id:` 一律是数值 `domain_event.id`；
- 用最后收到的 id 重连 → **增量**，不是快照；
- 真正过期/异己的游标 → 仍然诚实地 `SNAPSHOT_REQUIRED`；
- 600 条事件下不会静默丢中间部分。

红→绿证据见 `docs/FIX-EVIDENCE.md`。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.conftest import TEST_HMAC_SECRET, auth_headers
from tests.integration.conftest import create_session, envelope

SERVICE_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    """独立的端口段，避免与 test_sse_recovery.py 的模块级 server 抢占同一端口。"""
    for p in (8350, 8351, 8352, 8353, 8354):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("无可用端口")


@pytest.fixture(scope="module")
def server(it_schema: str, pg_url: str):
    """真实 uvicorn 进程 —— 线上 SSE 帧只能在真实进程上验证。"""
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "RESEARCH_DATABASE_URL": pg_url,
            "RESEARCH_DB_SCHEMA": it_schema,
            "RESEARCH_ENV": "test",
            "RESEARCH_GATEWAY_HMAC_SECRET": TEST_HMAC_SECRET,
            "RESEARCH_SSE_HEARTBEAT_SECONDS": "1",
            "RESEARCH_SSE_RETENTION_EVENTS": "200",
            "PYTHONPATH": str(SERVICE_ROOT),
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "research_vnext.core.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(SERVICE_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            with httpx.Client(transport=httpx.HTTPTransport(), trust_env=False, timeout=2.0) as c:
                if c.get(f"{base}/healthz").status_code == 200:
                    break
        except Exception:
            time.sleep(0.5)
    else:
        proc.terminate()
        out = proc.stdout.read() if proc.stdout else ""
        pytest.fail(f"服务未就绪:\n{out}")

    yield base, env

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _mk(client, ctx, ids, title="fix-a2"):
    r, _ = create_session(client, ctx, ids, title=title)
    assert r.status_code == 201, r.text
    return r.json()["session"]["id"]


def _transition(client, ctx, ids, sid, to, rev, crid):
    b = envelope(
        command_type="session.transition",
        actor_id=uuid.UUID(ctx["actor_id"]),
        scope={"kind": "session", "id": sid, "role": "owner"},
        payload={"to": to},
        expected_revision=rev,
        tenant_id=ids["tenant_id"],
        client_request_id=crid,
    )
    return client.post(f"/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))


def _resume(client, ctx, sid, last_event_id):
    return client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": last_event_id},
        headers=auth_headers(ctx),
    )


def _mk_target_env(base: str, ctx: dict, project_id: str):
    """真实进程场景下建会话，返回 (env_dict, session_id)。"""
    with httpx.Client(trust_env=False, timeout=15) as c:
        body = envelope(
            command_type="session.create",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "project", "id": project_id, "role": "owner"},
            payload={
                "title": "fix-a2-target",
                "mode": "MODELING",
                "project_id": project_id,
                "tenant_id": ctx["tenant_id"],
                "policy_refs": ["policy:research.default@v1"],
            },
            expected_revision=0,
            tenant_id=uuid.UUID(ctx["tenant_id"]),
        )
        r = c.post(f"{base}/api/research-v1/sessions:create", json=body, headers=auth_headers(ctx))
        assert r.status_code == 201, r.text
        return r.json()["session"]["id"]


def _transition_target(base: str, ctx: dict, sid: str, to: str, rev: int, crid: str):
    with httpx.Client(trust_env=False, timeout=15) as c:
        body = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload={"to": to},
            expected_revision=rev,
            tenant_id=uuid.UUID(ctx["tenant_id"]),
            client_request_id=crid,
        )
        return c.post(f"{base}/api/research-v1/sessions/{sid}:transition", json=body,
                      headers=auth_headers(ctx))


def _read_sse_frames(base: str, url: str, headers: dict, *, max_ids: int = 3, timeout_s: float = 15.0):
    """读取**真实** SSE 帧，返回 (ids, payloads, raw_lines)。

    必须直接读线上帧，而不是断言 `:resume` 的返回值 —— 否则测不到
    "首帧发了什么 id"（这正是本次缺陷的载体）。
    """
    ids: list[str] = []
    payloads: list[str] = []
    raw: list[str] = []
    with httpx.Client(trust_env=False, timeout=None) as c:
        with c.stream("GET", url, headers=headers) as resp:
            assert resp.status_code == 200, resp.status_code
            start = time.time()
            for line in resp.iter_lines():
                raw.append(line)
                if line.startswith("id:"):
                    ids.append(line[3:].strip())
                elif line.startswith("data:"):
                    payloads.append(line[5:].strip())
                if len(ids) >= max_ids or time.time() - start > timeout_s:
                    break
    return ids, payloads, raw


# --------------------------------------------------------------------------
# 游标形态：必须是可续传的数值
# --------------------------------------------------------------------------


def test_resume_reports_resumable_numeric_cursor(client, new_ids, ctx_factory) -> None:
    """`:resume` 报告给客户端的 `cursor` 必须是**它自己可以续传**的数值水位。

    报告一个自己认不出的值，就等于把客户端直接推进"每次重连都全量快照"的坑。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2-ids").status_code == 200

    body = _resume(client, ctx, sid, None).json()
    ids = [e["id"] for e in body["events"]]
    assert ids, "窗口内应有事件"
    assert all(isinstance(i, int) and i > 0 for i in ids), f"事件 id 必须是正整数: {ids}"
    assert ids == sorted(ids), "事件 id 必须单调递增"

    assert isinstance(body["cursor"], int) and body["cursor"] > 0, (
        f"cursor 必须是数值水位，实际 {body['cursor']!r}"
    )
    assert body["cursor"] == ids[-1], "cursor 必须等于窗口内最新事件的数值 id"

    after = _resume(client, ctx, sid, str(body["cursor"])).json()
    assert after["fallback"] == "NONE", (
        "服务端自己报告的 cursor 必须可续传（修复前合成 id 回传即 SNAPSHOT_REQUIRED）"
    )
    assert after["events"] == [], "用最新水位续传应当没有增量"


def test_synthetic_cursor_shape_fails_closed_to_snapshot(client, new_ids, ctx_factory) -> None:
    """历史合成 id（`cursor-<millis>`）必须 fail closed 成快照，绝不假装连续。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2-shape").status_code == 200

    synthetic = _resume(client, ctx, sid, f"cursor-{1789260910524}").json()
    assert synthetic["fallback"] == "SNAPSHOT_REQUIRED"
    assert synthetic["events"] == []


def test_sse_wire_frames_carry_only_resumable_numeric_ids(server, ctx_factory) -> None:
    """**线上帧**层面：SSE 发出的每一个 `id:` 都必须是可续传的数值水位。

    修复前首帧是 `id: cursor-<millis>`，本用例会失败 —— 这是本缺陷的真正载体，
    因此必须断言线上帧，而不是只断言 `:resume` 的返回值。
    """
    base, _env = server
    ctx = ctx_factory()
    project_id = str(uuid.uuid4())
    sid = _mk_target_env(base, ctx, project_id)
    assert _transition_target(base, ctx, sid, "ACTIVE", 0, "crq-fixa2-wire").status_code == 200

    ids, _payloads, raw = _read_sse_frames(
        base, f"{base}/api/research-v1/sessions/{sid}/events",
        auth_headers(ctx), max_ids=3,
    )
    assert ids, f"未收到任何 id: 行: {raw[:12]}"
    bad = [i for i in ids if not i.isdigit()]
    assert not bad, (
        f"SSE 发出了不可续传的合成 id: {bad}（客户端会把它当 Last-Event-ID 回传，"
        f"导致每次重连都退化为 SNAPSHOT_REQUIRED）。全部 id: {ids}"
    )
    assert all(int(i) > 0 for i in ids), f"水位必须是正整数: {ids}"

    # 每个 id 都真的可以续传
    for i in ids:
        got = _resume_client_sync(base, ctx, sid, i)
        assert got["fallback"] == "NONE", f"服务端自己发出的 id {i} 竟然不可续传: {got.get('reason')}"


def _resume_client_sync(base: str, ctx: dict, sid: str, last_event_id: str) -> dict:
    with httpx.Client(trust_env=False, timeout=15) as c:
        r = c.post(
            f"{base}/api/research-v1/sessions/{sid}:resume",
            json={"last_event_id": last_event_id},
            headers=auth_headers(ctx),
        )
        assert r.status_code == 200, r.text
        return r.json()


def test_live_reconnect_with_echoed_id_is_incremental_not_snapshot(server, ctx_factory) -> None:
    """端到端模拟浏览器行为：把流里收到的最后一个 `id:` 作为 `Last-Event-ID` 重连，
    必须拿到**增量**而不是 `snapshot_required`。

    这直接对撞评审实测的缺陷：修复前"每次自动重连都退化成全量快照"。
    """
    base, _env = server
    ctx = ctx_factory()
    project_id = str(uuid.uuid4())
    sid = _mk_target_env(base, ctx, project_id)
    assert _transition_target(base, ctx, sid, "ACTIVE", 0, "crq-fixa2-live1").status_code == 200

    h = auth_headers(ctx)
    ids, _p, _raw = _read_sse_frames(
        base, f"{base}/api/research-v1/sessions/{sid}/events", h, max_ids=2,
    )
    assert ids
    last_seen = ids[-1]

    # 再造一个新事件，然后"重连"
    assert _transition_target(base, ctx, sid, "RUNNING", 1, "crq-fixa2-live2").status_code == 200

    h2 = auth_headers(ctx)
    h2["Last-Event-ID"] = last_seen
    ids2, payloads2, raw2 = _read_sse_frames(
        base, f"{base}/api/research-v1/sessions/{sid}/events", h2, max_ids=1, timeout_s=8.0,
    )
    assert not any("snapshot_required" in p for p in payloads2), (
        f"重连被误判为需要快照（缺陷复现）: {raw2[:12]}"
    )
    assert ids2, "重连应收到增量事件"
    assert all(i.isdigit() for i in ids2), f"增量 id 不可续传: {ids2}"
    assert int(ids2[0]) > int(last_seen), "增量必须严格晚于客户端已持水位"


# --------------------------------------------------------------------------
# 增量 vs 快照
# --------------------------------------------------------------------------


def test_reconnect_with_last_real_id_returns_incremental_events(
    client, new_ids, ctx_factory
) -> None:
    """用**最后一个真实事件 id** 重连 → 只拿到其后的增量，而不是快照。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    h = auth_headers(ctx)

    first = _resume(client, ctx, sid, None).json()
    first_ids = [e["id"] for e in first["events"]]
    assert first_ids
    watermark = first_ids[0]

    # 再产生两个事件
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2-inc1").status_code == 200
    assert _transition(client, ctx, new_ids, sid, "RUNNING", 1, "crq-fixa2-inc2").status_code == 200

    got = client.post(
        f"/api/research-v1/sessions/{sid}:resume",
        json={"last_event_id": str(watermark)},
        headers=h,
    ).json()
    assert got["fallback"] == "NONE", f"窗口内续传不得要求快照: {got.get('reason')}"
    incremental = [e["id"] for e in got["events"]]
    assert incremental, "应至少有 1 个增量事件"
    assert all(i > watermark for i in incremental), f"增量必须严格晚于水位: {incremental}"
    assert watermark not in incremental, "不得重发客户端已持有的事件"


def test_stale_cursor_outside_window_still_requires_snapshot(
    client, new_ids, ctx_factory
) -> None:
    """真正过期的游标仍必须 `SNAPSHOT_REQUIRED` —— 修复不得把诚实回退一起改掉。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2-stale").status_code == 200

    # 远小于窗口下界的数值游标
    stale = _resume(client, ctx, sid, "1").json()
    assert stale["fallback"] == "SNAPSHOT_REQUIRED"
    assert stale["snapshot_ref"] == f"snapshot:{sid}"
    assert stale["events"] == []

    # 远大于任何已发出 id 的数值游标：服务端从未发出过 → 无法证明连续 → 快照
    future = _resume(client, ctx, sid, "99999999").json()
    assert future["fallback"] == "SNAPSHOT_REQUIRED", "伪造/超前的游标不得被当作已追上"

    # 完全不存在的事件 id
    unknown = _resume(client, ctx, sid, "evt-does-not-exist-000000").json()
    assert unknown["fallback"] == "SNAPSHOT_REQUIRED"


def test_retention_zero_still_requires_snapshot(client, new_ids, ctx_factory, monkeypatch) -> None:
    """条数窗口为 0 → 窗口为空 → 任何游标都不可能落在窗口内 → 快照。"""
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    watermark = _resume(client, ctx, sid, None).json()["cursor"]

    monkeypatch.setenv("RESEARCH_SSE_RETENTION_EVENTS", "0")
    out = _resume(client, ctx, sid, str(watermark)).json()
    assert out["fallback"] == "SNAPSHOT_REQUIRED"


# --------------------------------------------------------------------------
# >500 事件：不得静默丢中间
# --------------------------------------------------------------------------


def _bulk_events(client, ctx, ids, sid, n: int) -> None:
    """直接向领域事件表批量插入 n 条事件（同一会话），用于大事件量场景。"""
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session

    from research_vnext.core.db.engine import get_engine
    from research_vnext.core.db.models import DomainEventRow

    session_id = uuid.UUID(sid)
    with Session(get_engine()) as s:
        for i in range(n):
            s.add(
                DomainEventRow(
                    event_id=f"evt-bulk-{uuid.uuid4().hex[:32]}",
                    session_id=session_id,
                    event_type="session.bulk_probe",
                    schema_version="1.0.0",
                    revision=1,
                    occurred_at=datetime.now(timezone.utc),
                    trace_id="f" * 32,
                    payload={"i": i},
                    aggregate_type="session",
                )
            )
        s.commit()


def test_batch_boundary_returns_exactly_the_remainder(
    client, new_ids, ctx_factory, app_env
) -> None:
    """游标与"已发出最大 id"之间的增量**必须精确等于余量**，不重不漏。

    这一条直接对撞评审 P1-2(b)：修复前 SSE tail 恒用 `events_after(after_id=0,
    limit=500)`，窗口与 500 的批次边界相互作用会把余量算错（重复下发/水位卡死）。
    这里锁定批量取值的正确性，且不依赖保留窗口大小（`RETENTION_EVENTS` 默认 200）。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2-bound").status_code == 200
    _bulk_events(client, ctx, new_ids, sid, 10)

    window = _resume(client, ctx, sid, None).json()
    ids_in_window = [e["id"] for e in window["events"]]
    assert len(ids_in_window) > 6, f"窗口应包含刚插入的事件: {len(ids_in_window)}"

    cursor = ids_in_window[5]
    got = _resume(client, ctx, sid, str(cursor)).json()
    assert got["fallback"] == "NONE"
    got_ids = [e["id"] for e in got["events"]]

    assert got_ids == ids_in_window[6:], (
        f"余量计算错误：got {len(got_ids)} 条，expected {len(ids_in_window[6:])} 条"
    )
    assert len(set(got_ids)) == len(got_ids), "不得重复下发"
    assert all(i > cursor for i in got_ids), "不得重发游标及更早的事件"


def test_server_never_advertises_a_cursor_it_cannot_resume(
    client, new_ids, ctx_factory, app_env
) -> None:
    """服务端自报的 cursor 必须**始终**可续传 —— 否则每次自动重连都会全量回退。

    在 600 条事件（远超 500 批次与 200 窗口）下反复校验。
    """
    ctx = ctx_factory(tenant_id=new_ids["tenant_id"])
    sid = _mk(client, ctx, new_ids)
    assert _transition(client, ctx, new_ids, sid, "ACTIVE", 0, "crq-fixa2adv").status_code == 200
    _bulk_events(client, ctx, new_ids, sid, 600)

    window = _resume(client, ctx, sid, None).json()
    ids_in_window = [e["id"] for e in window["events"]]
    assert len(ids_in_window) == 200, f"条数窗口应为 200，实际 {len(ids_in_window)}"
    assert window["cursor"] == ids_in_window[-1], "cursor 必须是窗口内最新事件 id"

    # 自报 cursor 必须可续传
    again = _resume(client, ctx, sid, str(window["cursor"])).json()
    assert again["fallback"] == "NONE", f"服务端自报 cursor 不可续传: {again.get('reason')}"
    assert again["events"] == [], "无新事件时应返回空增量，而不是请求快照"

    # 窗口内任意水位都必须可续传，且结果精确
    for w in (ids_in_window[0], ids_in_window[len(ids_in_window) // 2], ids_in_window[-2]):
        r = _resume(client, ctx, sid, str(w)).json()
        assert r["fallback"] == "NONE", f"窗口内水位 {w} 被判为需快照: {r.get('reason')}"
        got = [e["id"] for e in r["events"]]
        assert got == [i for i in ids_in_window if i > w], f"水位 {w} 的余量不正确"
        assert len(set(got)) == len(got), "不得重复下发"
