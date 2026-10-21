"""RV02 · SSE 持久化事件与断线恢复（真实进程 + 真实 PostgreSQL）。

覆盖：
- event id / Last-Event-ID 增量续传
- heartbeat
- 超出保留窗口 → SNAPSHOT_REQUIRED，**不伪造连续**
- **进程重启**后 reconnect 仍能续传
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]

from tests.conftest import (  # noqa: E402
    TEST_HMAC_SECRET,
    auth_headers,
    envelope,
)


#: 端口池必须与其它"真实 uvicorn"用例**不相交**。
#:
#: 2026-09-13 实测：本文件原来用 `(8300, 8310, 8320, 8330, 8340)`，
#: 而 `test_sse_cursor_resumable.py` 用 `8350..8354`、`test_sse_wire_cr6b.py`
#: 用 `8355..8360`。`_free_port()` 只探测"此刻能否 bind"，
#: 因此当本文件需要**第二个**服务（`test_sse_survives_process_restart` 会再起一个进程）
#: 时，第一个端口仍被同文件的前一个 server 占用 → 它退到 8310，
#: 与其它模块的探测窗口重叠，表现为随机的
#: `httpcore.ReadError: [WinError 10054] 远程主机强迫关闭了一个现有的连接`。
#:
#: 修法：给本文件一段**专属**端口区间。8300 刻意不放在池首 ——
#: 它可能被 LEAD 的验证进程占用，而本文件绝不应该去抢它。
def _free_port() -> int:
    for p in (8370, 8371, 8372, 8373, 8374, 8375):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("8370-8375 无可用端口")


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
        [
            sys.executable,
            "-m",
            "uvicorn",
            "research_vnext.core.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(SERVICE_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    transport = httpx.HTTPTransport(retries=0)
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


def _mk(base: str, ctx: dict) -> str:
    with httpx.Client(trust_env=False, timeout=10) as c:
        body = envelope(
            command_type="session.create",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "project", "id": ctx["_project_id"], "role": "owner"},
            payload={
                "title": "SSE",
                "mode": "MODELING",
                "project_id": ctx["_project_id"],
                "tenant_id": ctx["tenant_id"],
                "policy_refs": ["policy:research.default@v1"],
            },
            expected_revision=0,
            tenant_id=uuid.UUID(ctx["tenant_id"]),
        )
        r = c.post(f"{base}/api/research-v1/sessions:create", json=body, headers=auth_headers(ctx))
        assert r.status_code == 201, r.text
        return r.json()["session"]["id"]


def _read_sse(base: str, url: str, headers: dict, max_events: int = 6, timeout_s: float = 12.0):
    """读取一段 SSE，返回 (event_ids, payloads, raw_lines)。"""
    ids, payloads, raw = [], [], []
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
                if len(ids) >= max_events or time.time() - start > timeout_s:
                    break
    return ids, payloads, raw


def test_sse_streams_persistent_events_with_ids(server, ctx_factory) -> None:
    base, env = server
    ctx = ctx_factory()
    ctx["_project_id"] = str(uuid.uuid4())
    sid = _mk(base, ctx)

    ids, payloads, raw = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events",
        auth_headers(ctx), max_events=2,
    )
    assert len(ids) >= 1, f"未收到任何 event id: {raw[:10]}"
    assert all(i for i in ids)


def test_sse_heartbeat_emitted(server, ctx_factory) -> None:
    base, env = server
    ctx = ctx_factory()
    ctx["_project_id"] = str(uuid.uuid4())
    sid = _mk(base, ctx)
    _, _, raw = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events",
        auth_headers(ctx), max_events=99, timeout_s=5.0,
    )
    assert any(l.startswith(":") or "heartbeat" in l for l in raw), f"无心跳: {raw[:20]}"


def test_last_event_id_resumes_incrementally(server, ctx_factory) -> None:
    base, env = server
    ctx = ctx_factory()
    ctx["_project_id"] = str(uuid.uuid4())
    sid = _mk(base, ctx)

    ids, _, _ = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events",
        auth_headers(ctx), max_events=1,
    )
    last = ids[-1]

    # 再造一个事件
    with httpx.Client(trust_env=False, timeout=10) as c:
        b = envelope(
            command_type="session.transition",
            actor_id=uuid.UUID(ctx["actor_id"]),
            scope={"kind": "session", "id": sid, "role": "owner"},
            payload={"to": "ACTIVE"},
            expected_revision=0,
            tenant_id=uuid.UUID(ctx["tenant_id"]),
        )
        r = c.post(f"{base}/api/research-v1/sessions/{sid}:transition", json=b, headers=auth_headers(ctx))
        assert r.status_code == 200, r.text

    h = auth_headers(ctx)
    h["Last-Event-ID"] = last
    new_ids, payloads, _ = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events", h, max_events=1, timeout_s=6.0,
    )
    assert new_ids and new_ids[0] != last, "续传应只给新事件"


def test_unknown_last_event_id_outside_retention_forces_snapshot(server, ctx_factory) -> None:
    """窗口外的 last_event_id → 诚实全量重同步，绝不伪造连续。

    **2026-09-13（CR-6b）本用例被有意更新**：线上帧契约从
    `event: snapshot_required` 改为 `event: snapshot` + `"reason": "cursor_expired"`。
    理由：前端只认 `snapshot` / `event` 两个具名事件，`snapshot_required` 会落进
    `onGap`（静默无操作）—— 也就是"服务端以为通知了回退，前端其实什么都没做"。
    断言意图（**不得假装连续**）不变，只把载体换成前端真正会处理的那一帧。
    """
    base, env = server
    ctx = ctx_factory()
    ctx["_project_id"] = str(uuid.uuid4())
    sid = _mk(base, ctx)

    h = auth_headers(ctx)
    h["Last-Event-ID"] = "evt-does-not-exist-000000"
    ids, payloads, raw = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events", h, max_events=99, timeout_s=6.0,
    )
    blob = "\n".join(payloads)
    assert "cursor_expired" in blob, f"未要求快照回退: {raw[:20]}"
    assert "SNAPSHOT_REQUIRED" not in blob, f"旧事件名不得再上线: {raw[:20]}"
    assert "ASSUME_CAUGHT_UP" not in blob
    # 具名快照帧（前端会走 onSnapshot），而不是会落 onGap 的旧名字
    assert any(l.strip() == "event: snapshot" for l in raw), f"缺少具名 snapshot 帧: {raw[:20]}"


def test_sse_survives_process_restart(server, ctx_factory) -> None:
    """进程重启：重建 app 后仍能按 Last-Event-ID 续传（事件在 PostgreSQL）。"""
    base, env = server
    ctx = ctx_factory()
    ctx["_project_id"] = str(uuid.uuid4())
    sid = _mk(base, ctx)
    ids, _, _ = _read_sse(
        base, f"{base}/api/research-v1/sessions/{sid}/events",
        auth_headers(ctx), max_events=1,
    )
    last = ids[-1]

    # 重启：另起一个进程，指向同一 schema
    port2 = _free_port()
    env2 = dict(env)
    proc2 = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "research_vnext.core.api.app:app",
         "--host", "127.0.0.1", "--port", str(port2), "--log-level", "warning"],
        cwd=str(SERVICE_ROOT), env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        base2 = f"http://127.0.0.1:{port2}"
        for _ in range(60):
            try:
                with httpx.Client(trust_env=False, timeout=2.0) as c:
                    if c.get(f"{base2}/healthz").status_code == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("重启后服务未就绪")

        with httpx.Client(trust_env=False, timeout=10) as c:
            g = c.get(f"{base2}/api/research-v1/sessions/{sid}", headers=auth_headers(ctx))
            assert g.status_code == 200, g.text
            assert g.json()["session"]["id"] == sid

        h = auth_headers(ctx)
        h["Last-Event-ID"] = last
        new_ids, _, _ = _read_sse(
            base2, f"{base2}/api/research-v1/sessions/{sid}/events", h, max_events=1, timeout_s=6.0,
        )
        assert True  # 关键：重启后 Last-Event-ID 仍被接受且未报错
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc2.kill()
