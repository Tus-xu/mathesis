"""M3 Docker 全栈端到端冒烟（对 http://localhost:8001 真实后端）。

覆盖：教师登录 → Today → 教案生成/确认 → 课件 → 出题 → 确认 → 作业 → 发布
→ 学生登录（联动可见）→ 教师批改建议/确认 → 角色切换 → 越权负向。
"""

import asyncio
import json
import sys
import uuid

import httpx

BASE = "http://localhost:8001"
TEACHER_PHONE = "13900001001"
STUDENT_PHONE = "13900001002"
CODE = "123456"


def ok(resp, label):
    body = resp.json()
    if body.get("code") != 0:
        print(f"FAIL {label}: {json.dumps(body, ensure_ascii=False)[:300]}")
        sys.exit(1)
    print(f"PASS {label}")
    return body["data"]


async def login(client: httpx.AsyncClient, phone: str) -> str:
    await client.post(f"{BASE}/api/auth/sms-code", json={"phone": phone})
    r = await client.post(f"{BASE}/api/auth/login", json={"phone": phone, "code": CODE})
    data = ok(r, f"login {phone}")
    return data["token"]


async def main() -> None:
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        # 0) 健康
        r = await c.get(f"{BASE}/api/health")
        assert r.status_code == 200
        print("PASS health")

        # 1) 教师登录
        teacher_token = await login(c, TEACHER_PHONE)
        th = {"Authorization": f"Bearer {teacher_token}"}

        # 2) Today
        r = await c.get(f"{BASE}/api/teacher/today", headers=th)
        today = ok(r, "teacher today")
        assert "grading_queue" in today and "actionable_insights" in today

        # 3) 班级（教师 mine）
        r = await c.get(f"{BASE}/api/classes/mine", headers=th)
        classes = ok(r, "teacher classes/mine")
        class_id = classes["items"][0]["id"]

        # 4) 班级洞察
        r = await c.get(f"{BASE}/api/teacher/classes/{class_id}/insights?actionable=true", headers=th)
        ok(r, "class insights")

        # 5) 教案：adapt → confirm → slides
        r = await c.post(f"{BASE}/api/teacher/lessons/adapt", headers=th,
                         json={"class_id": class_id, "topic": "导数的应用", "requirements": "压缩讲授", "duration_minutes": 45})
        lesson = ok(r, "lesson adapt")
        assert lesson["status"] == "draft" and lesson["content"]
        r = await c.post(f"{BASE}/api/teacher/artifacts/{lesson['artifact_id']}/confirm", headers=th,
                         json={"client_request_id": str(uuid.uuid4()), "idempotency_key": f"smoke-conf-{uuid.uuid4().hex[:8]}"})
        ok(r, "lesson confirm")
        r = await c.post(f"{BASE}/api/teacher/lessons/{lesson['artifact_id']}/slides", headers=th,
                         json={"version": 1})
        slides = ok(r, "slides create")
        assert slides["content"]["slides"]

        # 6) 出题 → 确认 → 作业草稿 → 发布（题量护栏：不足明确失败，不重复凑数）
        r = await c.post(f"{BASE}/api/teacher/quizzes/generate", headers=th,
                         json={"class_id": class_id, "knowledge_points": ["MATH-003", "MATH-005", "MATH-006"],
                               "count": 4, "question_types": {"choice": 1, "blank": 1, "text": 2}})
        quiz = ok(r, "quiz generate")
        assert len(quiz["content"]["items"]) == 4
        r = await c.post(f"{BASE}/api/teacher/artifacts/{quiz['artifact_id']}/confirm", headers=th,
                         json={"client_request_id": str(uuid.uuid4()), "idempotency_key": f"smoke-qc-{uuid.uuid4().hex[:8]}"})
        ok(r, "quiz confirm")
        ca_id = f"smoke-{uuid.uuid4().hex[:12]}"
        r = await c.post(f"{BASE}/api/teacher/assignments", headers=th,
                         json={"class_id": class_id, "title": "导数巩固练习", "artifact_id": quiz["artifact_id"],
                               "client_assignment_id": ca_id})
        assignment = ok(r, "assignment draft")
        assert assignment["status"] == "draft"
        r = await c.post(f"{BASE}/api/teacher/assignments/{assignment['assignment_id']}/publish", headers=th,
                         json={"client_request_id": str(uuid.uuid4()), "idempotency_key": f"smoke-pub-{assignment['assignment_id']}"})
        pub = ok(r, "assignment publish")
        assert pub["status"] == "published"

        # 7) 学生登录 → 作业联动可见
        student_token = await login(c, STUDENT_PHONE)
        sh = {"Authorization": f"Bearer {student_token}"}
        r = await c.get(f"{BASE}/api/classes/mine", headers=sh)
        sdata = ok(r, "student classes/mine")
        sid = sdata["items"][0]["id"]
        assert sid == class_id, "学生与教师同班"
        # 学生任务列表（M2 既有接口 /api/student/assignments）应含教师刚发布的作业
        r = await c.get(f"{BASE}/api/student/assignments?status=all", headers=sh)
        sdata = ok(r, "student assignments")
        sitems = sdata.get("items") if isinstance(sdata, dict) else sdata
        sitems = sitems or []
        titles = [t.get("title") for t in sitems]
        assert any("导数巩固练习" in (t or "") for t in titles), f"学生未见已发布作业: {titles}"
        print(f"PASS student sees published assignment: {titles}")

        # 8) 教师批改队列（暂无提交 → 空队列不报错）
        r = await c.get(f"{BASE}/api/teacher/grading/queue", headers=th)
        ok(r, "grading queue")

        # 9) 课堂模式 开→查→关
        r = await c.post(f"{BASE}/api/teacher/classes/{class_id}/classroom-mode", headers=th,
                         json={"enabled": True, "lesson_id": lesson["artifact_id"],
                               "client_request_id": str(uuid.uuid4()),
                               "idempotency_key": f"smoke-mode-on-{uuid.uuid4().hex[:8]}"})
        ok(r, "classroom mode on")
        r = await c.get(f"{BASE}/api/teacher/classes/{class_id}/classroom-mode", headers=th)
        ok(r, "classroom mode state")
        r = await c.post(f"{BASE}/api/teacher/classes/{class_id}/classroom-mode", headers=th,
                         json={"enabled": False, "client_request_id": str(uuid.uuid4()),
                               "idempotency_key": f"smoke-mode-off-{uuid.uuid4().hex[:8]}"})
        ok(r, "classroom mode off")

        # 10) 角色切换（学生账号双绑定）：学生 → 教师 → 学生（换发 JWT 闭环）
        r = await c.post(f"{BASE}/api/auth/role/switch", headers=sh, json={"role": "teacher"})
        switched = ok(r, "role switch student->teacher")
        teacher_role_token = switched["token"]
        # 切换为教师后可访问教师端点
        r = await c.get(f"{BASE}/api/teacher/today",
                        headers={"Authorization": f"Bearer {teacher_role_token}"})
        ok(r, "teacher today after role switch")
        # 切回学生后教师端点应 403
        r = await c.post(f"{BASE}/api/auth/role/switch",
                         headers={"Authorization": f"Bearer {teacher_role_token}"},
                         json={"role": "student"})
        student_back = ok(r, "role switch teacher->student")
        r = await c.get(f"{BASE}/api/teacher/today",
                        headers={"Authorization": f"Bearer {student_back['token']}"})
        assert r.json()["code"] == 40301, r.text
        print("PASS teacher endpoint denied after switching back to student")

        # 11) 越权负向：学生访问教师端点
        r = await c.get(f"{BASE}/api/teacher/today", headers=sh)
        assert r.json()["code"] == 40301
        print("PASS student denied on teacher endpoint")

        # 12) Capability gateway（本地降级）
        r = await c.post(f"{BASE}/api/teacher/capabilities/adapt_lesson", headers=th,
                         json={"scene": "teacher.prep", "class_id": class_id,
                               "payload": {"topic": "数列", "requirements": None},
                               "client_request_id": str(uuid.uuid4())})
        ok(r, "capability adapt_lesson (local)")

        # 13) 资源上传（multipart）→ 预处理 → 理解 → 任务查询
        r = await c.post(f"{BASE}/api/teacher/resources/upload", headers=th,
                         files={"file": ("teaching-notes.docx", b"demo content", "application/octet-stream")})
        ticket = ok(r, "resource upload")
        rid = ticket["resource_id"]
        r = await c.post(f"{BASE}/api/teacher/resources/{rid}/preprocess", headers=th,
                         json={"client_request_id": str(uuid.uuid4())})
        ok(r, "resource preprocess")
        r = await c.post(f"{BASE}/api/teacher/resources/{rid}/understand", headers=th,
                         json={"client_request_id": str(uuid.uuid4()), "question": "本文要点"})
        ok(r, "resource understand")
        r = await c.get(f"{BASE}/api/teacher/tasks/{rid}", headers=th)
        ok(r, "task get")

        # 14) 前端入口（nginx）
        async with httpx.AsyncClient(timeout=10, trust_env=False) as wc:
            page = await wc.get("http://localhost:8090/")
            assert page.status_code == 200 and "智学数研" in page.text
            print("PASS web frontend served (8090)")
            api_probe = await wc.get("http://localhost:8090/api/health")
            assert api_probe.status_code == 200
            print("PASS web -> api proxy works")

        print("\nALL SMOKE TESTS PASSED")


asyncio.run(main())
