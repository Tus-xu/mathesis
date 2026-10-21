"""P6 · 课堂 100 学生并发模拟（B5 规范 §1 附加职责 / 02-ARCHITECTURE §12.5）。

场景编排（03-ACCEPTANCE §5 P6）：join（≤800ms P95 归 P7）→ 等待推题 → 作答
（提交 ≤500ms P95）→ 断线重连（含 Last-Event-ID 补拉）。SLO：零崩溃、
内存 <1.5GB/worker（docker stats 另取）。

拓扑：教师 bootstrap 进程预建 session 并把 join_code 写入文件——学生 User 启动时读取。
    # 1) bootstrap（一次性）：
    python tests/perf/classroom_sim.py bootstrap
    # 2) 压测（100 学生 + 1 教师）：
    LOCUST_TASKS=classroom locust -f tests/perf/locustfile_teacher.py ...   # 或本文件独立跑：
    locust -f tests/perf/classroom_sim.py --headless -u 101 -r 10 -t 5m --host http://127.0.0.1:8100
    # 3) 结课归档：
    python tests/perf/classroom_sim.py teardown

状态文件：tests/perf/.classroom_state.json（join_code/session_id/活动 id）。
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from locust import HttpUser, between, task

STATE_FILE = Path(__file__).parent / ".classroom_state.json"
NAMES = ["王雨桐", "陈子豪", "刘一鸣", "林小满", "赵启铭", "孙浩然", "周可欣", "吴宇轩", "郑好", "冯天佑"]


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- CLI：bootstrap / teardown（教师侧动作） ----------------


def bootstrap() -> None:
    import httpx

    base = os.environ.get("TEACHER_BASE_URL", "http://127.0.0.1:8100")
    login = httpx.post(f"{base}/teacher-v3/auth/login", json={"username": "zhanglaoshi", "password": "teacher123"})
    token = login.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.post(f"{base}/teacher-v3/classroom/sessions", json={"class_id": "seed-c1"}, headers=headers)
    r.raise_for_status()
    data = r.json()["data"]
    state = {"base": base, "token": token, "session_id": data["session_id"], "join_code": data["join_code"]}
    _save_state(state)
    print(f"bootstrap OK：session={data['session_id']} join_code={data['join_code']} → {STATE_FILE}")


def teardown() -> None:
    import httpx

    state = _load_state()
    base, token, sid = state["base"], state["token"], state["session_id"]
    r = httpx.post(f"{base}/teacher-v3/classroom/sessions/{sid}/end", headers={"Authorization": f"Bearer {token}"})
    print(f"end: {r.status_code} {r.text[:200]}")
    STATE_FILE.unlink(missing_ok=True)


class PerfStudentUser(HttpUser):
    """虚拟学生：join → 轮询 snapshot 等推题 → 作答 → 抽样断线重连（P9 语义）。"""

    wait_time = between(0.5, 2)

    def on_start(self) -> None:
        state = _load_state()
        self.session_id = state.get("session_id", "")
        self.student_name = random.choice(NAMES) + str(random.randint(0, 999))
        t0 = time.perf_counter()
        r = self.client.post(
            "/teacher-v3/classroom/join",
            json={"join_code": state.get("join_code", ""), "student_name": self.student_name},
            name="[P7] join",
        )
        self.client.environment.stats.log_request(
            "POST", "[P7] join-ms", response_time=int((time.perf_counter() - t0) * 1000), content_length=0
        )
        if r.status_code != 200:
            self.token = None
            self.client.environment.events.request.fire(
                request_method="POST", name="[P7] join", response_time=0,
                response_length=0, exception=RuntimeError(f"join HTTP {r.status_code}"),
            )
            return
        d = r.json()["data"]
        self.token = d["classroom_token"]
        self.participant_id = d["participant_id"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.answered: set[str] = set()

    def _snapshot(self) -> dict:
        r = self.client.get(
            f"/teacher-v3/classroom/sessions/{self.session_id}/snapshot",
            headers=self.headers,
            name="GET classroom snapshot",
        )
        return r.json().get("data", {}) if r.status_code == 200 else {}

    @task(4)
    def wait_and_answer(self) -> None:
        """等待 collecting 活动 → 提交作答（P6：提交 ≤500ms P95）。"""
        snap = self._snapshot()
        for act in snap.get("activities", []):
            if act.get("status") == "collecting" and act["activity_id"] not in self.answered:
                t0 = time.perf_counter()
                r = self.client.post(
                    f"/teacher-v3/classroom/sessions/{self.session_id}/responses",
                    json={"activity_id": act["activity_id"], "answer": {"choice": random.choice("ABCD")}},
                    headers=self.headers,
                    name="[P6] submit response",
                )
                elapsed = int((time.perf_counter() - t0) * 1000)
                if r.status_code == 200:
                    self.answered.add(act["activity_id"])
                    self.client.environment.stats.log_request(
                        "POST", "[P6] submit-ms", response_time=elapsed, content_length=0
                    )
                else:
                    r.failure(f"submit HTTP {r.status_code}")
                return

    @task(1)
    def reconnect_replay(self) -> None:
        """P9 语义模拟：带 Last-Event-ID 重拉 snapshot/stream，统计零丢失由离线 diff 脚本判。"""
        if not self.session_id:
            return
        self.client.get(
            f"/teacher-v3/classroom/sessions/{self.session_id}/snapshot",
            headers=self.headers,
            name="[P9] reconnect snapshot",
        )


class PerfTeacherUser(HttpUser):
    """压测教师：推题 → 等 60% 作答 → 锁定 → 公布 → 下一题（驱动学生循环）。"""

    wait_time = between(3, 6)

    def on_start(self) -> None:
        state = _load_state()
        self.token = state.get("token")
        self.session_id = state.get("session_id", "")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self.pushed = 0

    @task
    def drive_lesson(self) -> None:
        if not self.token:
            return
        if self.pushed >= 5:
            return
        r = self.client.post(
            f"/teacher-v3/classroom/sessions/{self.session_id}/activities",
            json={"kind": "question", "config": {"question_id": None, "round": self.pushed + 1}},
            headers=self.headers,
            name="[P6] push activity",
        )
        if r.status_code == 200:
            self.pushed += 1


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "bootstrap":
        bootstrap()
    elif cmd == "teardown":
        teardown()
    else:
        print(__doc__)
