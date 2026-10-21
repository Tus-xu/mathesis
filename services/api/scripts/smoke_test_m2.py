"""M2 快速冒烟测试脚本"""
import json

import httpx

BASE = "http://localhost:8000"
PHONE = "13900139001"  # 用新号避免频率限制

def main():
    client = httpx.Client(timeout=10)

    # 1. 登录
    print("=" * 50)
    print("[1] 登录...")
    r = client.post(f"{BASE}/api/auth/sms-code", json={"phone": PHONE})
    print(f"  sms-code: {r.json().get('code')}")
    r = client.post(f"{BASE}/api/auth/login", json={"phone": PHONE, "code": "123456"})
    data = r.json()
    if data.get("code") != 0:
        print(f"  登录失败: {data}")
        return
    token = data["data"]["token"]
    print(f"  token: {token[:30]}...")
    headers = {"Authorization": f"Bearer {token}"}

    # 2. 健康检查
    print("\n[2] 健康检查...")
    r = client.get(f"{BASE}/api/health")
    print(f"  {r.json()}")

    # 3. 测试 /solve（SSE 流式）
    print("\n[3] 测试引导式解题 /solve ...")
    try:
        with httpx.stream(
            "POST", f"{BASE}/api/agent/chat",
            headers={**headers, "Content-Type": "application/json"},
            json={"content": "/solve x^2-2x-3=0", "workspace": "student"},
            timeout=60.0,
        ) as resp:
            print(f"  HTTP {resp.status_code}")
            event_count = 0
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                print(f"  {line[:120]}")
                event_count += 1
                if event_count > 25 or "event: done" in line:
                    break
    except Exception as e:
        print(f"  SSE 错误: {e}")

    # 4. 测试 /出题
    print("\n[4] 测试智能出题 /出题 ...")
    try:
        with httpx.stream(
            "POST", f"{BASE}/api/agent/chat",
            headers={**headers, "Content-Type": "application/json"},
            json={"content": "/出题 三角函数 简单", "workspace": "student"},
            timeout=60.0,
        ) as resp:
            print(f"  HTTP {resp.status_code}")
            event_count = 0
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                print(f"  {line[:120]}")
                event_count += 1
                if event_count > 25 or "event: done" in line:
                    break
    except Exception as e:
        print(f"  SSE 错误: {e}")

    # 5. 测试学生端点（错题收录）
    print("\n[5] 测试错题收录...")
    r = client.post(f"{BASE}/api/student/error-records", headers=headers, json={
        "question_text": "求 x^2-2x-3=0 的解",
        "answer_text": "x=1",
        "source_channel": "manual_photo",
        "error_type": "formula",
        "kp_code": "function",
    })
    print(f"  {r.json()}")

    # 6. 测试错题查询
    print("\n[6] 测试错题查询...")
    r = client.get(f"{BASE}/api/student/error-records?view=time&page=1&size=5", headers=headers)
    print(f"  {json.dumps(r.json(), ensure_ascii=False)[:200]}")

    # 7. 测试知识图谱
    print("\n[7] 测试知识图谱...")
    r = client.get(f"{BASE}/api/student/knowledge-graph", headers=headers)
    d = r.json()
    print(f"  code={d.get('code')}, nodes={len(d.get('data',{}).get('nodes',[]))}")

    print("\n" + "=" * 50)
    print("冒烟测试完成！")


if __name__ == "__main__":
    main()
