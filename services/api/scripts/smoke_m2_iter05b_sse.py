"""迭代05 补充冒烟：引导解题 SSE 链路 + 刷题日限（阶段 5.2 收尾）

需要：后端 localhost:8000 运行中、LLM 可用（mimo）。
"""

import json
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
client = httpx.Client(timeout=900.0)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    client.post(f"{BASE}/api/auth/sms-code", json={"phone": phone})
    body = client.post(f"{BASE}/api/auth/login", json={"phone": phone, "code": "123456"}).json()
    token = body["data"]["token"]
    H = {"Authorization": f"Bearer {token}"}

    # 1. 引导解题 SSE（发一道三角函数简单题 → 事件流完整性验证）
    r = client.post(
        f"{BASE}/api/agent/chat",
        json={
            "message": "帮我解这道题：已知 $\\sin x = \\frac{1}{2}$，且 $0 < x < \\pi$，求 $x$。",
            "context": {"client_msg_id": f"sse-{uuid.uuid4().hex[:10]}", "workspace": "student"},
        },
        headers=H,
    )
    check("chat HTTP 200 + SSE", r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", ""))
    events = [ln[7:] for ln in r.text.split("\n") if ln.startswith("event: ")]
    check("SSE 事件序（meta 首个业务事件）", "meta" in events and events.index("meta") == 0,
          f"events={events[:6]}")
    check("SSE 引导解题卡（card 或 token 输出）", "card" in events or "token" in events, f"events={events[:8]}")
    check("SSE done 收尾", "done" in events, f"events 尾部={events[-3:]}")
    check("SSE 无 error", "error" not in events, f"events={events[:8]}")
    if "token" in events:
        idx = events.index("token")
        data_lines = [ln[6:] for ln in r.text.split("\n") if ln.startswith("data: ")]
        token_text = ""
        # data 行与 event 行对应解析
        for ln in r.text.split("\n"):
            if ln.startswith("data: ") and token_text == "":
                pass
        check("SSE 引导产出非空", len(r.text) > 500, f"len={len(r.text)}")

    # 2. 刷题日限验证（42901 双语义：此处验证 daily_cap 结构，42901 逻辑由单测覆盖）
    ok_cap = False
    for _ in range(2):
        rr = client.post(f"{BASE}/api/student/practice/start",
                         json={"mode": "special", "kp_code": "MATH-G1-TRIG-001"}, headers=H)
        b = rr.json()
        if b["code"] == 0:
            ok_cap = "daily_cap" in b["data"] and b["data"]["daily_cap"]["limit"] == 30
            break
    check("刷题 daily_cap（limit=30）", ok_cap, str(b)[:150])

    # 3. 非法 mode 校验（40001 双语义场景补充）
    r = client.post(f"{BASE}/api/student/practice/start", json={"mode": "hacked"}, headers=H)
    check("非法 mode → 40001", r.json()["code"] == 40001)

    print(f"\n===== 补充冒烟：{PASS} 通过 / {FAIL} 失败 =====")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
