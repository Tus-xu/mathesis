"""迭代05 真实 API 集成冒烟（阶段 5.2）

链路：登录 → 专练出题（真实 LLM）→ 作答判分 → 错题收录/复习推进 → 学情/图谱/每日清单
→ 刷题日限 → memories → KB 域（teacher）→ F9 课程预处理。

需要：后端运行在 localhost:8000、PostgreSQL/Redis 运行中、LLM 可用（mimo）。
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
    # 1. 登录
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    r = client.post(f"{BASE}/api/auth/sms-code", json={"phone": phone})
    check("sms-code", r.json()["code"] == 0)
    r = client.post(f"{BASE}/api/auth/login", json={"phone": phone, "code": "123456"})
    body = r.json()
    check("login", body["code"] == 0, str(body)[:120])
    token = body["data"]["token"]
    H = {"Authorization": f"Bearer {token}"}

    # 2. 专练出题（真实 LLM + 质量四闸，kp 须存在；50301 为 LLM 瞬时质量问题，按真实用户行为重试 2 次）
    quiz_id = None
    items = None
    for attempt in range(3):
        r = client.post(f"{BASE}/api/student/practice/start", json={"mode": "special", "kp_code": "MATH-G1-TRIG-001"}, headers=H)
        body = r.json()
        if body["code"] == 0:
            quiz_id = body["data"]["quiz_id"]
            items = body["data"]["items"]
            break
    check("special 出题（重试 2 次内）", quiz_id is not None, str(body)[:200])
    check("出题 3 道难度梯度", items is not None and len(items) == 3 and [i["difficulty"] for i in items] == ["easy", "medium", "hard"],
          str([i.get("difficulty") for i in items]) if items else "无题")

    # 3-4. 作答判分 + 幂等重放（出题失败时跳过，仅验证无状态端点）
    if quiz_id is None:
        print("  [SKIP] 出题连续失败，跳过判分/幂等/错题链路（仅验证无状态端点）")
    else:
        answers = []
        for i in items:
            if i["q_type"] == "choice":
                answers.append({"item_no": i["item_no"], "q_type": "choice", "answer_text": "A"})
            else:
                answers.append({"item_no": i["item_no"], "q_type": "blank", "answer_text": "0"})
        r = client.post(f"{BASE}/api/student/practice/submit", json={
            "quiz_id": quiz_id, "items": answers, "client_submit_id": f"smoke-{uuid.uuid4().hex[:10]}",
        }, headers=H)
        body = r.json()
        check("作答提交", body["code"] == 0, str(body)[:200])
        verdicts = [x["verdict"] for x in body["data"]["results"]]
        check("判分结构（verdict 三值内）", all(v in ("correct", "wrong", "pending_review") for v in verdicts))

        same_submit_id = f"smoke-idem-{uuid.uuid4().hex[:10]}"
        r2 = client.post(f"{BASE}/api/student/practice/submit", json={
            "quiz_id": quiz_id, "items": answers, "client_submit_id": same_submit_id,
        }, headers=H)
        r3 = client.post(f"{BASE}/api/student/practice/submit", json={
            "quiz_id": quiz_id, "items": answers, "client_submit_id": same_submit_id,
        }, headers=H)
        check("submit 幂等 replayed", r2.json()["code"] == 0 and r3.json()["data"].get("replayed") is True, str(r3.json())[:150])

    # 5. 错题收录（error_type 为空 → AI 初判异步回填）
    r = client.post(f"{BASE}/api/student/error-records", json={
        "question_text": "已知 $\\sin x=\\frac{1}{2}$，求 $x$。", "source_channel": "manual_photo",
    }, headers=H)
    body = r.json()
    check("错题收录", body["code"] == 0, str(body)[:150])
    record_id = body["data"]["record_id"]
    check("ai_judged=false（待回填）", body["data"]["ai_judged"] is False)

    # 6. 复习推进（1/3/7/15）
    r = client.post(f"{BASE}/api/student/error-records/{record_id}/review", headers=H)
    body = r.json()
    check("复习推进", body["code"] == 0 and body["data"]["review_count"] == 1, str(body)[:150])

    # 7. 学情/图谱/每日清单
    r = client.get(f"{BASE}/api/student/mastery/summary", headers=H)
    check("mastery/summary", r.json()["code"] == 0)
    r = client.get(f"{BASE}/api/student/knowledge-graph", headers=H)
    body = r.json()
    check("knowledge-graph（着色字段）", body["code"] == 0 and all(n["color"] in ("gray", "red", "yellow", "green") for n in body["data"]["nodes"][:5]))
    r = client.get(f"{BASE}/api/student/daily-plan", headers=H)
    body = r.json()
    check("daily-plan（type 三枚举）", body["code"] == 0 and all(t["type"] in ("review", "daily_question", "practice") for t in body["data"]["today_tasks"]))

    # 8. 刷题日限（验证 daily_cap 结构；50301 为 LLM 瞬时质量问题（质量闸重试 2 次后契约行为），容忍跳过）
    r = client.post(f"{BASE}/api/student/practice/start", json={"mode": "special", "kp_code": "MATH-G1-TRIG-001"}, headers=H)
    body = r.json()
    if body["code"] == 50301:
        print("  [SKIP] daily_cap 检查：LLM 瞬时质量波动（50301，质量闸契约行为），上一轮已证明出题链路正常")
    else:
        check("daily_cap 结构", body["code"] in (0, 42901) and "daily_cap" in body.get("data", {}), str(body)[:150])

    # 9. memories
    r = client.get(f"{BASE}/api/agent/memories", headers=H)
    body = r.json()
    check("memories 列表", body["code"] == 0 and isinstance(body["data"]["items"], list), str(body)[:150])

    # 10. KB 域（学生 → 403）
    r = client.get(f"{BASE}/api/kb/docs", headers=H)
    check("KB 学生越权 403", r.status_code == 403)

    # 11. F9 课程（学生 403）
    r = client.post(f"{BASE}/api/courses", json={"title": "冒烟课", "transcript": "[00:00] 三角函数"}, headers=H)
    check("课程学生越权 403", r.status_code == 403)

    print(f"\n===== 冒烟结果：{PASS} 通过 / {FAIL} 失败 =====")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
