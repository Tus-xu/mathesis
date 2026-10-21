"""CR-6b · SSE **线上帧**契约（真实 uvicorn 进程，逐帧读取）。

为什么必须读真实帧：CR-6b 的缺陷载体**就是帧本身**（事件名写错/缺失），
断言 `:resume` 的返回值永远测不到它。本文件只读线上字节。

锁定契约（ADR-RV-003 §3.5）：
1. 只出现 `snapshot` / `event` 两个具名 `event:`；
2. **不再出现** `event: cursor` / `event: snapshot_required`；
3. 不存在"无名帧承载 domain event"（有 `data:` 就必须有 `event:`）；
4. 每个 `id:` 都是可续传的**数值** `domain_event.id`；
5. 心跳注释帧**不带 `id:`**（否则浏览器把心跳 id 当 Last-Event-ID）；
6. 过期/异己游标 ⇒ `event: snapshot` 且携带 `reason`。
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

from tests.conftest import TEST_HMAC_SECRET, auth_headers, envelope

SERVICE_ROOT = Path(__file__).resolve().parents[2]

PORT_CANDIDATES = (8355, 8356, 8357, 8358, 8359, 8360)


def _free_port() -> int:
    """本文件的**专属**端口区间。

    与其它"真实 uvicorn"用例刻意不相交（见 `test_sse_recovery.py::_free_port`
    上方的说明）：多个模块共享同一个端口池会导致随机的连接重置。
    8300 刻意不在池内 —— 它可能被 LEAD 的验证进程占用，绝不抢占。
    """
    for p in PORT_CANDIDATES:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("8355-8360 无可用端口")


@pytest.fixture(scope="module")
def server(it_schema: str, pg_url: str):
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
    for _ in range(80):
        try:
            with httpx.Client(trust_env=False, timeout=2.0) as c:
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


class Frame:
    """一帧 SSE 的原始结构。"""

    __slots__ = ("event", "id", "data", "raw")

    def __init__(self, event: str | None, eid: str | None, data: str | None, raw: list[str]) -> None:
        self.event = event
        self.id = eid
        self.data = data
        self.raw = raw

    def json(self) -> dict:
        return json.loads(self.data or "{}")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Frame(event={self.event!r}, id={self.id!r}, data={self.data!r})"


def parse_frames(lines: list[str]) -> list[Frame]:
    """把原始行解析成帧（正确处理多行 `data:` 与注释帧）。"""
    frames: list[Frame] = []
    event = eid = None
    data_lines: list[str] = []
    raw: list[str] = []

    def flush() -> None:
        nonlocal event, eid, data_lines, raw
        if event is None and eid is None and not data_lines and not raw:
            return
        frames.append(
            Frame(event, eid, "\n".join(data_lines) if data_lines else None, list(raw))
        )
        event = eid = None
        data_lines = []
        raw = []

    for line in lines:
        if line == "":
            flush()
            continue
        raw.append(line)
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("id:"):
            eid = line[len("id:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
        # 其它行（`: comment`、`retry:`）留在 raw 里但不影响字段
    flush()
    return frames


def read_raw(base: str, sid: str, headers: dict, *, seconds: float = 6.0, min_frames: int = 1) -> list[str]:
    lines: list[str] = []
    started = time.time()
    with httpx.Client(trust_env=False, timeout=None) as c:
        with c.stream("GET", f"{base}/api/research-vnext/sessions/{sid}/events", headers=headers) as r:
            assert r.status_code == 200, r.status_code
            for line in r.iter_lines():
                lines.append(line)
                if time.time() - started > seconds:
                    break
                if len(parse_frames(lines)) >= min_frames and line == "" and time.time() - started > 1.0:
                    break
    return lines


def _mk(base: str, ctx: dict, project_id: str) -> str:
    with httpx.Client(trust_env=False, timeout=15) as c:
        body = envelope(
            command_type="session.create",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "project", "id": project_id, "role": "owner"},
            payload={
                "title": "sse-cr6b",
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


def _post_message(base: str, ctx: dict, sid: str, message: str) -> None:
    with httpx.Client(trust_env=False, timeout=30) as c:
        r = c.post(
            f"{base}/api/research-vnext/sessions/{sid}/messages",
            json={"message": message},
            headers=auth_headers(ctx),
        )
        assert r.status_code == 201, r.text


# --------------------------------------------------------------------------
# 帧契约
# --------------------------------------------------------------------------


def test_first_frame_is_named_snapshot_with_frontend_shape(server, ctx_factory) -> None:
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=4.0))
    assert frames, "未读到任何帧"
    first = frames[0]
    assert first.event == "snapshot", f"首帧必须是具名 snapshot，实际 {first!r}"
    payload = first.json()
    assert "snapshot" in payload, "snapshot 帧必须带 snapshot 键"
    snap = payload["snapshot"]
    assert isinstance(snap["timeline"], list)
    assert isinstance(snap["evidence"], list)
    assert isinstance(snap["capabilities"], list)
    assert first.id is None, "快照帧不得带 id:（否则客户端水位被毒化）"


def test_no_cursor_or_snapshot_required_frames_ever(server, ctx_factory) -> None:
    """`event: cursor` 与 `event: snapshot_required` 必须**彻底消失**。

    前端只认 `snapshot` / `event`；这两个名字一律落进 `onGap`（静默丢弃）。
    """
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "触发事件")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=5.0, min_frames=3))
    names = {f.event for f in frames}
    assert "cursor" not in names, f"仍有 event: cursor: {frames[:5]}"
    assert "snapshot_required" not in names, f"仍有 event: snapshot_required: {frames[:5]}"
    assert names <= {"snapshot", "event", None}, f"出现了契约外事件名: {names}"


def test_every_data_frame_is_named(server, ctx_factory) -> None:
    """不存在"无名帧承载 domain event"—— 有 data 就必须有 event 名。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "触发事件")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=5.0, min_frames=3))
    unnamed = [f for f in frames if f.data is not None and f.event is None]
    assert not unnamed, f"存在无名数据帧（前端会当 gap 丢弃）: {unnamed[:5]}"


def test_domain_events_arrive_as_named_event_frames(server, ctx_factory) -> None:
    """真实 domain event 必须以 `event: event` 送达，且 data 是事件本体。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "请分析")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=6.0, min_frames=3))
    evs = [f for f in frames if f.event == "event"]
    assert evs, f"未收到 event: event 帧: {frames[:8]}"
    types = {f.json().get("event_type") for f in evs}
    assert "session.message_created" in types, types
    assert "session.assistant_message_created" in types, types
    # 事件本体必须含前端需要的字段
    for f in evs:
        d = f.json()
        assert {"event_id", "event_type", "session_id", "revision", "occurred_at"} <= set(d)


def test_all_ids_are_resumable_numeric_watermarks(server, ctx_factory) -> None:
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "x")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=5.0, min_frames=3))
    ids = [f.id for f in frames if f.id is not None]
    assert ids, f"未收到任何 id: 行: {frames[:8]}"
    bad = [i for i in ids if not i.isdigit()]
    assert not bad, f"出现不可续传的合成 id: {bad}"
    assert all(int(i) > 0 for i in ids)

    # 服务端自己发出的每个 id 都必须真的能续传
    with httpx.Client(trust_env=False, timeout=15) as c:
        for i in ids:
            r = c.post(
                f"{base}/api/research-v1/sessions/{sid}:resume",
                json={"last_event_id": i},
                headers=auth_headers(ctx),
            )
            assert r.status_code == 200, r.text
            assert r.json()["fallback"] == "NONE", f"自发的 id {i} 竟不可续传"


def test_heartbeat_frames_carry_no_id(server, ctx_factory) -> None:
    """心跳必须是**无 id** 的注释帧 —— 否则浏览器会把它当 Last-Event-ID 回传。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))

    lines = read_raw(base, sid, auth_headers(ctx), seconds=5.0, min_frames=1)
    frames = parse_frames(lines)
    hb = [f for f in frames if any(l.startswith(":") for l in f.raw)]
    assert hb, f"未观测到心跳注释帧: {lines[:20]}"
    for f in hb:
        assert f.id is None, f"心跳帧带了 id: {f.id!r}（会毒化客户端水位）: {f.raw}"
        assert f.data is None, f"心跳帧不应有 data: {f.raw}"


def test_stale_cursor_yields_named_snapshot_with_reason(server, ctx_factory) -> None:
    """过期/异己游标 ⇒ `event: snapshot` + `reason`（**不是** `snapshot_required`）。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))

    h = auth_headers(ctx)
    h["Last-Event-ID"] = "evt-does-not-exist-000000"
    frames = parse_frames(read_raw(base, sid, h, seconds=4.0, min_frames=1))

    assert frames, "未读到任何帧"
    first = frames[0]
    assert first.event == "snapshot", f"过期游标首帧必须是 snapshot，实际 {first!r}"
    assert first.json().get("reason") == "cursor_expired", first.json().keys()
    assert "snapshot" in first.json()
    assert first.id is None
    # 不得伪造连续
    assert not [f for f in frames if f.event == "event"], "过期游标不得下发增量事件"


def test_live_reconnect_with_echoed_id_is_incremental(server, ctx_factory) -> None:
    """浏览器真实行为：把最后见到的 `id:` 回传 → 必须是增量，而不是快照。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "第一条")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=5.0, min_frames=3))
    ids = [f.id for f in frames if f.id is not None]
    assert ids
    last_seen = ids[-1]

    _post_message(base, ctx, sid, "第二条")

    h = auth_headers(ctx)
    h["Last-Event-ID"] = last_seen
    frames2 = parse_frames(read_raw(base, sid, h, seconds=5.0, min_frames=2))
    assert frames2[0].event == "snapshot"
    assert "reason" not in frames2[0].json(), "窗口内续传不得报 cursor_expired"
    evs = [f for f in frames2 if f.event == "event"]
    assert evs, f"重连应收到增量: {frames2[:6]}"
    assert all(int(f.id) > int(last_seen) for f in evs), "增量必须严格晚于已持水位"


def test_snapshot_frame_reflects_timeline_after_message(server, ctx_factory) -> None:
    """快照帧必须是**真实投影**（含刚写入的消息），不是空壳。"""
    base, _ = server
    ctx = ctx_factory()
    sid = _mk(base, ctx, str(uuid.uuid4()))
    _post_message(base, ctx, sid, "快照里应当看到这句话")

    frames = parse_frames(read_raw(base, sid, auth_headers(ctx), seconds=4.0))
    snap = frames[0].json()["snapshot"]
    texts = [m["text"] for m in snap["timeline"]]
    assert "快照里应当看到这句话" in texts, texts
    caps = {c["key"]: c for c in snap["capabilities"]}
    assert set(caps) >= {
        "research-session",
        "research-message",
        "research-stream",
        "research-evidence",
        "research-artifact",
    }
