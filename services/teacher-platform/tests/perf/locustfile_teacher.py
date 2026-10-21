"""G9 perf harness · 教师端 locust 脚本（P1/P5/P8 取数主体）。

跑法（compose 全栈真实容器，禁进程内模拟——02-ARCHITECTURE §18.5）：
    # P8：教师侧 50 SSE 同时在线 10 分钟（零崩溃、内存 <1.5GB/worker，docker stats 另取）
    locust -f tests/perf/locustfile_teacher.py --headless -u 50 -r 5 -t 10m \
        --only-sse --host http://127.0.0.1:8100

    # P5：题库 5000 题规模列表 ≤500ms P95（种子库真实规模）
    locust -f tests/perf/locustfile_teacher.py --headless -u 20 -r 5 -t 3m \
        --only-rest --host http://127.0.0.1:8100

    # P1 交叉验证：butler 对话首 token（SSE 墙钟 TTFB）+ 跑后 SQL ai_runs.latency_ms 对比
    locust -f tests/perf/locustfile_teacher.py --headless -u 10 -r 2 -t 3m \
        --only-butler --host http://127.0.0.1:8100

取数口径（B5 §2.5 定案）：P1 首 token = ① 客户端 SSE 首个含内容 data 帧墙钟（本文件
fire_event 记录）② ai_runs.latency_ms（跑后 SQL）双源交叉，差异 >30% 以 ai_runs 为准并标注。
原始数据入 acceptance/perf/（03-ACCEPTANCE §5 执行纪律②）。
"""
from __future__ import annotations

import json
import os
import time
import uuid

from locust import HttpUser, between, events, task

TEACHER_USER = os.environ.get("TEACHER_SEED_USER", "zhanglaoshi")
TEACHER_PASSWORD = os.environ.get("TEACHER_SEED_PASSWORD", "teacher123")


def _tags(*tags: str):
    """按 --only-* 参数过滤 task（locust 无原生 task 级开关，用环境变量约定）。"""
    enabled = os.environ.get("LOCUST_TASKS", "rest,sse,butler").split(",")
    return all(t in enabled for t in tags)


class TeacherUser(HttpUser):
    """教师端混合负载：登录 → 目录读 → butler SSE 首 token 计时。"""

    wait_time = between(1, 3)
    weight = 1

    def on_start(self) -> None:
        self.token: str | None = None
        r = self.client.post(
            "/teacher-v3/auth/login",
            json={"username": TEACHER_USER, "password": TEACHER_PASSWORD},
        )
        if r.status_code == 200:
            self.token = r.json().get("data", {}).get("token")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ---------------- P5：列表接口 ----------------

    @task(6)
    def quiz_questions_5000(self) -> None:
        """P5 — 题库 5000 题规模列表 ≤500ms P95（SLO 表首测 Gate=M2-B）。"""
        if not _tags("rest"):
            return
        self.client.get(
            "/teacher-v3/quiz/questions",
            headers=self.headers,
            name="[P5] GET /quiz/questions",
        )

    @task(2)
    def today_workbench(self) -> None:
        if not _tags("rest"):
            return
        self.client.get("/teacher-v3/today", headers=self.headers, name="GET /today")

    @task(2)
    def deck_templates(self) -> None:
        if not _tags("rest"):
            return
        self.client.get("/teacher-v3/deck-templates", headers=self.headers, name="GET /deck-templates")

    # ---------------- P1：butler 首 token（SSE TTFB） ----------------

    @task(3)
    def butler_chat_first_token(self) -> None:
        """P1 — 首 token ≤2s P50 / ≤4s P95：墙钟 = 首个非 retry/注释/心跳帧到达。"""
        if not _tags("butler"):
            return
        if not self.token:
            return
        body = {
            "message": "椭圆第一定义和第二定义有什么区别？",
            "context": {"route": "/teacher-v3/today"},
            "web_search": False,
        }
        start = time.perf_counter()
        first_token_ms = None
        event_count = 0
        with self.client.post(
            "/teacher-v3/butler/chat",
            json=body,
            headers={**self.headers, "Accept": "text/event-stream"},
            stream=True,
            catch_response=True,
            name="[P1] butler/chat 首 token",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            for line in resp.iter_lines():
                if not line or line.startswith(":") or line.startswith("retry:"):
                    continue  # 心跳/retry 帧不算内容
                if line.startswith("event: "):
                    event_count += 1
                    ev = line[7:].strip()
                    if ev == "token" and first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - start) * 1000)
                elif line.startswith("data: ") and first_token_ms is None and event_count:
                    first_token_ms = int((time.perf_counter() - start) * 1000)
        if first_token_ms is not None:
            self.environment.stats.log_request(
                "POST", "[P1] first-token-ms", response_time=first_token_ms, content_length=0
            )


class SseHoldUser(HttpUser):
    """P8 — 长连持有者：连上任务型/课堂 SSE 后保持 10 分钟，只计连接成功与事件量。"""

    wait_time = between(0, 0)
    weight = 0  # 仅 --only-sse 时通过 LOCUST_SSE_WEIGHT 启用

    def on_start(self) -> None:
        r = self.client.post(
            "/teacher-v3/auth/login",
            json={"username": TEACHER_USER, "password": TEACHER_PASSWORD},
        )
        token = r.json().get("data", {}).get("token") if r.status_code == 200 else None
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.session_id = os.environ.get("LOCUST_CLASSROOM_SESSION_ID", "")

    @task
    def hold_stream(self) -> None:
        if not _tags("sse"):
            import gevent

            gevent.sleep(5)
            return
        path = (
            f"/teacher-v3/classroom/sessions/{self.session_id}/stream"
            if self.session_id
            else "/teacher-v3/butler/tools"  # 无课堂时退化占位（P8 正式跑必须提供 session_id）
        )
        start = time.perf_counter()
        events_seen = 0
        with self.client.get(
            path, headers={**self.headers, "Accept": "text/event-stream"}, stream=True,
            name="[P8] SSE hold 10min",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"SSE 连接失败 HTTP {resp.status_code}")
                return
            for _line in resp.iter_lines():
                events_seen += 1
        self.client.get("/healthz", name="[P8] post-hold healthz")


@events.quitting.add_listener
def _print_p_summary(environment, **_kw):
    """退出时打印 SLO 摘要（供 acceptance/perf/ 原始记录）。"""
    rows = []
    for key, entry in environment.stats.entries.items():
        name = key[1] if isinstance(key, tuple) else str(key)
        if any(tag in str(name) for tag in ("[P1]", "[P5]", "[P8]", "[P6]", "[P7]")):
            rows.append(
                f"{name}: n={entry.num_requests} P50={entry.get_response_time_percentile(0.5):.0f}ms "
                f"P95={entry.get_response_time_percentile(0.95):.0f}ms fail={entry.fail_ratio:.2%}"
            )
    if rows:
        print("\n===== G9 SLO 摘要（B5 perf harness）=====")
        print("\n".join(rows))
        print("原始 CSV：locust -f ... --csv=acceptance/perf/m<gate>-<date>")
