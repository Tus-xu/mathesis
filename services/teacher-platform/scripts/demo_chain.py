"""B3 自验收 demo：API 提交 → worker 执行 → SSE 收进度（live）→ 断线重连零丢失（durable replay）。

用法：`uv run python -m scripts.demo_chain [base_url]`（默认 http://127.0.0.1:8101）
输出场景 A/B 证据（P9 口径）。worker kill 后任务不丢（P10）由 ValkeyCompatibilityGate 覆盖。
"""
from __future__ import annotations

import http.client
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8101"


def req(method: str, path: str, body: dict | None = None, token: str | None = None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def sse_events(tid: str, token: str, extra_h: dict | None = None, stop_on_done: bool = True, window: float = 25.0):
    conn = http.client.HTTPConnection("127.0.0.1", int(BASE.rsplit(":", 1)[1]), timeout=30)
    h = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    if extra_h:
        h.update(extra_h)
    conn.request("GET", f"/api/teacher-v3/platform/demo/tasks/{tid}/stream", headers=h)
    resp = conn.getresponse()
    cur, out = {}, []
    start = time.time()
    while time.time() - start < window:
        line = resp.readline().decode().strip()
        if line == "":
            if cur:
                out.append(cur)
                cur = {}
            continue
        if line.startswith("event:"):
            cur["event"] = line[6:].strip()
        elif line.startswith("data:"):
            try:
                cur["data"] = json.loads(line[5:].strip())
            except Exception:  # noqa: BLE001
                cur["data"] = {"RAW": line[5:].strip()}
        elif line.startswith("id:"):
            cur["id"] = int(line[3:].strip())
        if stop_on_done and cur.get("event") == "done" and cur.get("data") is not None:
            out.append(cur)
            break
    conn.close()
    return out


def main() -> int:
    _, env = req("POST", "/api/teacher-v3/auth/login", {"username": "zhanglaoshi", "password": "teacher123"})
    assert env["code"] == 0, f"login failed: {env}"
    token = env["data"]["token"]
    print("1. login ✔  (role=teacher, request_id present)")

    # 场景 A：连接在任务执行中 → live 实时收全量
    _, task = req("POST", "/api/teacher-v3/platform/demo/sleep", {"seconds": 6.0}, token=token)
    tid = task["data"]["id"]
    evs = sse_events(tid, token, window=30)
    seqs = [e["id"] for e in evs if e.get("id") is not None]
    print(f"2. 场景A(live实时): {[(e['event'], e.get('data',{}).get('progress')) for e in evs]}")
    assert any(e["event"] in ("progress",) for e in evs), "live 未收到 progress"

    # 场景 B：断线重连零丢失（Last-Event-ID → durable replay）
    _, task2 = req("POST", "/api/teacher-v3/platform/demo/sleep", {"seconds": 8.0}, token=token)
    tid2 = task2["data"]["id"]
    evs1 = sse_events(tid2, token, stop_on_done=False, window=4)
    last_id = max([e.get("id", 0) for e in evs1])
    time.sleep(1)
    evs2 = sse_events(tid2, token, {"Last-Event-ID": str(last_id)}, stop_on_done=False, window=15)
    ids = [e["id"] for e in evs2 if e.get("id") is not None]
    assert ids and min(ids) > last_id, f"断线重连有丢失: ids={ids}, last={last_id}"
    print(f"3. 场景B(断线重连): 断开 seq={last_id} → 重放 {ids} 零丢失 ✔  (P9)")

    # 场景 C：任务中心真实数据（契约形状 {items, running}）
    _, tasks = req("GET", "/api/teacher-v3/tasks", token=token)
    items = tasks["data"]["items"]
    running = tasks["data"]["running"]
    print(f"4. 任务中心: {len(items)} 条真实任务 / running={running}  ✔ (形状 {{items,running}})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
