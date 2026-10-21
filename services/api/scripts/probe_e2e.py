# -*- coding: utf-8 -*-
"""端到端实测脚本：登录 → 发消息（SSE）→ 记录各事件耗时与内容摘要。

用法: python scripts/probe_e2e.py "<消息文本>" [tag]
"""
import json
import os
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8000"


def post_json(path, body, headers=None, timeout=10):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


import os

def login():
    phone = os.environ.get("PROBE_PHONE", "13900000003")
    post_json("/api/auth/sms-code", {"phone": phone})
    r = post_json("/api/auth/login", {"phone": phone, "code": "123456"})
    if not r.get("data"):
        raise SystemExit(f"login failed: {r}")
    return r["data"]["token"]


def sse_chat(token, message, tag):
    conv = post_json(
        "/api/agent/conversations",
        {},
        headers={"Authorization": f"Bearer {token}"},
    )
    conv_id = conv["data"]["id"]
    payload = {
        "conversation_id": conv_id,
        "message": message,
        "context": {"client_msg_id": f"probe_{uuid.uuid4().hex[:8]}"},
    }
    # 思考模式开关（M2.2）：PROBE_THINKING=off 时显式关闭
    if os.environ.get("PROBE_THINKING", "").lower() == "off":
        payload["context"]["thinking"] = False
    req = urllib.request.Request(
        BASE + "/api/agent/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    t0 = time.monotonic()
    print(f"[{tag}] >>> send: {message[:60]!r}", flush=True)
    event_count = {}
    first_token_t = None
    with urllib.request.urlopen(req, timeout=600) as resp:
        event = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event:
                data_str = line[5:].strip()
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    data = {"raw": data_str[:200]}
                dt = time.monotonic() - t0
                event_count[event] = event_count.get(event, 0) + 1
                if event == "token":
                    if first_token_t is None:
                        first_token_t = dt
                    text = data.get("text", "")
                    # 只打印首末 token，避免刷屏
                    if event_count["token"] == 1:
                        print(f"[{tag}] [{dt:7.1f}s] TOKEN(first): {text[:80]!r}", flush=True)
                elif event == "status":
                    print(
                        f"[{tag}] [{dt:7.1f}s] STATUS: {data.get('stage','')} | {data.get('text','')[:100]}",
                        flush=True,
                    )
                elif event == "thinking":
                    if event_count["thinking"] == 1:
                        print(f"[{tag}] [{dt:7.1f}s] THINKING(first): {data.get('text','')[:80]!r}", flush=True)
                elif event == "card":
                    cd = data.get("data", data)
                    print(
                        f"[{tag}] [{dt:7.1f}s] CARD: {cd.get('card_type') or cd.get('type','?')}",
                        flush=True,
                    )
                elif event == "meta":
                    print(f"[{tag}] [{dt:7.1f}s] META: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
                elif event == "done":
                    print(f"[{tag}] [{dt:7.1f}s] DONE. tokens={event_count.get('token',0)}", flush=True)
                elif event == "error":
                    print(f"[{tag}] [{dt:7.1f}s] ERROR: {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)
                event = ""
    total = time.monotonic() - t0
    print(f"[{tag}] TOTAL {total:.1f}s | first_token={first_token_t} | events={event_count}", flush=True)


if __name__ == "__main__":
    msg = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else "probe"
    tok = login()
    print(f"[{tag}] login ok", flush=True)
    sse_chat(tok, msg, tag)
