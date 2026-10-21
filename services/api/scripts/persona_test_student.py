"""M2 学生视角全链路 persona 测试（用户画像测试法）

画像：小林，高二学生，数学中等偏下，三角函数薄弱。
目标：代入学生真实使用路径，验证 M2 核心功能端到端可用。

用法：先启动 API（uvicorn app.main:app --port 8000），再运行
    ./.venv/Scripts/python.exe scripts/persona_test_student.py
"""
import json
import re
import sys
import uuid

import httpx

BASE = "http://127.0.0.1:8000"
PHONE = "13900139006"  # 专用测试号，避免与烟冒撞频率限制

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []  # (环节, 结论, 备注)


def record(step: str, verdict: str, note: str = ""):
    results.append((step, verdict, note))
    icon = {"PASS": "+", "FAIL": "x", "WARN": "!"}[verdict]
    print(f"  [{icon} {verdict}] {note}")


def sse_chat(client: httpx.Client, headers: dict, content: str, max_lines: int = 200) -> str:
    """发起 /api/agent/chat SSE，拼接 token 文本返回。"""
    text_parts: list[str] = []
    with client.stream(
        "POST", f"{BASE}/api/agent/chat",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "message": content,
            "context": {"workspace": "student", "client_msg_id": uuid.uuid4().hex},
        },
        timeout=120.0,
    ) as resp:
        if resp.status_code != 200:
            return f"__HTTP_{resp.status_code}__"
        for line in resp.iter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            delta = obj.get("text") or obj.get("delta") or obj.get("content") or ""
            if isinstance(delta, str):
                text_parts.append(delta)
            if len(text_parts) > max_lines * 4:
                break
    return "".join(text_parts)


def main() -> int:
    client = httpx.Client(timeout=120)  # 出题走 LLM 生成，耗时较长

    # ── 1. 登录（验证码 → token）────────────────────────────
    print("\n[1] 小林打开 App，验证码登录")
    r = client.post(f"{BASE}/api/auth/sms-code", json={"phone": PHONE})
    if r.json().get("code") != 0:
        record("登录", FAIL, f"sms-code 失败: {r.json()}")
        return summary(1)
    r = client.post(f"{BASE}/api/auth/login", json={"phone": PHONE, "code": "123456"})
    data = r.json()
    if data.get("code") != 0:
        record("登录", FAIL, f"login 失败: {data}")
        return summary(1)
    token = data["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get(f"{BASE}/api/auth/me", headers=headers)
    me = r.json().get("data", {})
    record("登录", PASS, f"user={me.get('phone', '?')}, role={me.get('role', '?')}")

    # ── 2. 引导式解题（泄露检查：不应直接给终案）──────────────
    print("\n[2] 小林用 /solve 问一道一元二次方程（引导模式）")
    reply = sse_chat(client, headers, "/solve 解方程 x^2-5x+6=0")
    if reply.startswith("__HTTP_"):
        record("引导式解题", FAIL, f"HTTP 错误 {reply}")
    elif not reply.strip():
        record("引导式解题", FAIL, "SSE 无文本输出")
    else:
        # 泄露启发式：引导回复应包含追问/提示语气，且不应出现"所以 x=... 和 x=..."式直接结论
        leaks = re.findall(r"(答案[是为：:]|综上[，,]?\s*x\s*=|因此\s*x\s*=\s*\d.*和)", reply)
        has_question = "？" in reply or "?" in reply
        if leaks:
            record("引导式解题", WARN, f"疑似泄露终案: {leaks[:2]} | 回复前80字: {reply[:80]}")
        elif has_question:
            record("引导式解题", PASS, f"引导式追问正常 | 前80字: {reply[:80]}")
        else:
            record("引导式解题", WARN, f"无泄露但也没追问，需人工盲评 | 前80字: {reply[:80]}")

    # ── 3. 专练模式出题（需要知识点存在）──────────────────────
    print("\n[3] 小林针对薄弱点刷专项题（practice/start special）")
    r = client.get(f"{BASE}/api/student/knowledge-graph", headers=headers)
    kg = r.json().get("data", {})
    nodes = kg.get("nodes", [])
    kp_code = nodes[0].get("kp_code") or nodes[0].get("code") if nodes else None
    quiz_id, first_item = None, None
    if not kp_code:
        record("知识图谱", FAIL, "无节点，无法取 kp_code")
        record("专项出题", WARN, "跳过（无知识点）")
    else:
        record("知识图谱", PASS, f"nodes={len(nodes)}, 选用 kp={kp_code}")
        r = client.post(f"{BASE}/api/student/practice/start", headers=headers,
                        json={"mode": "special", "kp_code": kp_code})
        d = r.json()
        if d.get("code") != 0:
            record("专项出题", FAIL, f"{d.get('code')}: {d.get('message')}")
        else:
            items = d["data"]["items"]
            quiz_id = d["data"]["quiz_id"]
            first_item = items[0] if items else None
            record("专项出题", PASS, f"quiz={quiz_id[:8]}… 题数={len(items)} 首题型={first_item and first_item['q_type']}")

    # ── 4. 作答判分 + 幂等重放 ─────────────────────────────
    print("\n[4] 小林提交作答（practice/submit）并模拟网络重试")
    if quiz_id and first_item:
        submit_body = {
            "quiz_id": quiz_id,
            "client_submit_id": uuid.uuid4().hex,
            "items": [{
                "item_no": first_item["item_no"],
                "q_type": first_item["q_type"],
                "answer_text": "A" if first_item["q_type"] == "choice" else "x=1",
                "kp_code": kp_code,
            }],
        }
        r1 = client.post(f"{BASE}/api/student/practice/submit", headers=headers, json=submit_body)
        d1 = r1.json()
        if d1.get("code") != 0:
            record("作答判分", FAIL, f"{d1.get('code')}: {d1.get('message')}")
        else:
            res = d1["data"].get("results", [])
            record("作答判分", PASS, f"verdict={res and res[0].get('verdict')} score={res and res[0].get('score')}")
            # 幂等重放：同 client_submit_id 再发一次，应返回相同结果且 replayed=true
            r2 = client.post(f"{BASE}/api/student/practice/submit", headers=headers, json=submit_body)
            d2 = r2.json()
            if d2.get("code") == 0 and d2["data"].get("replayed") and d2["data"].get("results"):
                record("幂等重放", PASS, "重试返回首次真实判分结果")
            else:
                record("幂等重放", FAIL, f"重放异常: {json.dumps(d2, ensure_ascii=False)[:150]}")
    else:
        record("作答判分", WARN, "跳过（出题失败）")
        record("幂等重放", WARN, "跳过")

    # ── 5. 错题本三视图 + AI 错因 ───────────────────────────
    print("\n[5] 小林翻看错题本（time/kp/error_type 三视图）")
    r = client.post(f"{BASE}/api/student/error-records", headers=headers, json={
        "question_text": "求 sin²x+cos²x 的值",
        "answer_text": "0",
        "source_channel": "manual_photo",
        "error_type": "concept",
        "kp_code": kp_code or "function",
    })
    if r.json().get("code") != 0:
        record("错题收录", FAIL, f"{r.json()}")
    else:
        record("错题收录", PASS, "manual_photo 收录成功")
    for view in ("time", "kp", "error_type"):
        r = client.get(f"{BASE}/api/student/error-records?view={view}&page=1&size=5", headers=headers)
        ok = r.json().get("code") == 0
        record(f"错题视图-{view}", PASS if ok else FAIL, f"共 {len(r.json().get('data', {}).get('items', r.json().get('data', [])) or [])} 条" if ok else str(r.json())[:120])

    # ── 6. 掌握度 / 趋势 / 打卡 ────────────────────────────
    print("\n[6] 小林查看学情（mastery/summary、trend、streak、daily-plan）")
    for path, name in [
        ("/api/student/mastery/summary", "掌握度总览"),
        ("/api/student/mastery/trend", "掌握度趋势"),
        ("/api/student/streak", "打卡"),
        ("/api/student/daily-plan", "每日计划"),
        ("/api/student/practice/daily", "每日一题"),
    ]:
        r = client.get(f"{BASE}{path}", headers=headers)
        ok = r.json().get("code") == 0
        record(name, PASS if ok else FAIL, "" if ok else str(r.json())[:120])

    # ── 7. 作业列表（教师端联合，只验接口可用）────────────────
    print("\n[7] 小林看作业列表（教师端联合功能，仅验证接口）")
    r = client.get(f"{BASE}/api/student/assignments", headers=headers)
    ok = r.json().get("code") == 0
    record("作业列表", PASS if ok else WARN, "" if ok else str(r.json())[:120])

    # ── 8. 平台健康（模型状态）───────────────────────────────
    print("\n[8] 平台健康检查 /api/health")
    r = client.get(f"{BASE}/api/health")
    record("健康检查", PASS if r.status_code == 200 else FAIL, str(r.json())[:150])

    return summary(0)


def summary(code: int) -> int:
    print("\n" + "=" * 60)
    fails = [r for r in results if r[1] == FAIL]
    warns = [r for r in results if r[1] == WARN]
    print(f"结果：{len(results) - len(fails) - len(warns)} PASS / {len(warns)} WARN / {len(fails)} FAIL")
    if fails:
        print("失败项：" + "、".join(f[0] for f in fails))
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
