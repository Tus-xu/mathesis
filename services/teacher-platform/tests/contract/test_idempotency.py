"""幂等契约（G8，B5 规范 M1）：Idempotency-Key 窗口重放 + DB 唯一约束铁证。

蓝本：02-ARCHITECTURE §14、app/core/idempotency.py、notes/B5-notes.md §1.7（#62-66）。
全部 compose 档（Valkey SETNX + PG 真实约束，红线 5 禁进程内模拟）。
"""
from __future__ import annotations

import asyncio

import pytest

from conftest import PrefixingSyncClient, _auth

pytestmark = [pytest.mark.compose, pytest.mark.usefixtures("http_base")]

IDEM_HEADERS = {"Idempotency-Key": "b5-contract-idem-001"}


@pytest.fixture(scope="module")
def http_client(http_base):
    import httpx

    with PrefixingSyncClient(base_url=http_base, timeout=30) as client:
        yield client


def _teacher(client, token):
    client.headers.update(_auth(token))
    return client


async def _teacher_async(client, token):
    """ASGI 档预留（当前全 compose）。"""
    return _teacher(client, token)


def test_rest_idempotency_replays_first_response(http_client, teacher_token):
    """#62 — 同 key 两次 POST /quiz/questions：响应字节级一致 + 库里只 +1 行。"""
    c = _teacher(http_client, teacher_token)
    before = c.get("/teacher-v3/quiz/questions").json()["data"]["total"]
    payload = {
        "kp_code": "YD-01",
        "kp_name": "幂等契约测试题",
        "q_type": "solve",
        "difficulty": "easy",
        "stem_latex": "Idempotency-Key 重放断言题（幂等测试）",
        "answer": "1",
    }
    r1 = c.post("/teacher-v3/quiz/questions", json=payload, headers=IDEM_HEADERS)
    assert r1.status_code == 200
    r2 = c.post("/teacher-v3/quiz/questions", json=payload, headers=IDEM_HEADERS)
    assert r2.status_code == 200
    assert r1.json() == r2.json(), "窗口内重放必须返回首次响应（逐字段一致）"
    after = c.get("/teacher-v3/quiz/questions").json()["data"]["total"]
    assert after == before + 1, f"同 key 重复提交不得双写：before={before} after={after}"


def test_concurrent_same_key_no_double_write(http_client, teacher_token):
    """#63 — 同 key 并发（SETNX 短锁）：只产生一行，其余请求拿到首结果。"""
    c = _teacher(http_client, teacher_token)
    payload = {
        "kp_code": "YD-01",
        "kp_name": "并发幂等测试题",
        "q_type": "solve",
        "difficulty": "easy",
        "stem_latex": "并发同 key 断言题（幂等测试）",
        "answer": "1",
    }

    async def _fire():
        import httpx

        from conftest import PrefixingAsyncClient

        async with PrefixingAsyncClient(
            base_url=str(http_client.base_url), headers=dict(http_client.headers), timeout=30
        ) as ac:
            rs = await asyncio.gather(
                *[
                    ac.post("/teacher-v3/quiz/questions", json=payload, headers=IDEM_HEADERS)
                    for _ in range(3)
                ]
            )
            return [r.json() for r in rs]

    bodies = asyncio.run(_fire())
    ids = {b["data"]["id"] for b in bodies if b.get("data")}
    assert len(ids) == 1, f"并发同 key 必须收敛到同一资源：{ids}"


def test_classroom_response_idempotent_double_submit(http_client, teacher_token):
    """#64 — 课堂作答：UNIQUE(activity_id, participant_id, attempt_no)，重复提交返回首次结果。

    M2-A 起 session/activity 可用后自动生效；前置缺失时以明确失败暴露（红是预期）。
    """
    c = _teacher(http_client, teacher_token)
    # 开课 + 学生 join + 推题（真实链路，禁合成）
    session = c.post("/teacher-v3/classroom/sessions", json={"class_id": "seed-c1"}).json()["data"]
    join_code = session["join_code"]
    joined = c.post(
        "/teacher-v3/classroom/join",
        json={"join_code": join_code, "student_name": "幂等测试生"},
    ).json()["data"]
    student_headers = {"Authorization": f"Bearer {joined['classroom_token']}"}
    activity = c.post(
        f"/teacher-v3/classroom/sessions/{session['session_id']}/activities",
        json={"kind": "question", "config": {}},
        headers=_auth(teacher_token),
    ).json()["data"]
    activity_id = activity["activity_id"]
    body = {"activity_id": activity_id, "answer": {"choice": "A"}}
    r1 = c.post(
        f"/teacher-v3/classroom/sessions/{session['session_id']}/responses",
        json=body,
        headers=student_headers,
    )
    assert r1.status_code == 200, f"首次作答应成功：{r1.text[:200]}"
    r2 = c.post(
        f"/teacher-v3/classroom/sessions/{session['session_id']}/responses",
        json=body,
        headers=student_headers,
    )
    assert r2.status_code == 200
    assert r1.json() == r2.json(), "重复作答必须返回首次结果"


def test_event_seq_unique_constraint(http_client):
    """#66 — classroom_events/task_events UNIQUE(scope,seq)：直接 SQL 重发同 seq 必被拒。

    测试行自清理（数据真实性审计黑名单禁「测试」内容入库——03-ACCEPTANCE §7.2）。
    """
    import os

    import asyncpg

    dsn = os.environ.get(
        "TEACHER_PG_DSN",
        "postgres://teacher:teacher_dev_pw@127.0.0.1:5433/teacher_platform",
    )

    async def _try_dup():
        conn = await asyncpg.connect(dsn)
        sid = None
        try:
            sid = await conn.fetchval(
                "INSERT INTO classroom_sessions (teacher_id, class_id, join_code, status)"
                " VALUES (gen_random_uuid(), gen_random_uuid(), $1, 'created')"
                " RETURNING id",
                f"B5QA-{os.getpid()}",
            )
            await conn.execute(
                "INSERT INTO classroom_events (session_id, seq, event_type, payload)"
                " VALUES ($1, 1, 'b5_probe', '{}')",
                sid,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO classroom_events (session_id, seq, event_type, payload)"
                    " VALUES ($1, 1, 'b5_probe', '{}')",
                    sid,
                )
        finally:
            if sid is not None:
                await conn.execute("DELETE FROM classroom_events WHERE session_id = $1", sid)
                await conn.execute("DELETE FROM classroom_sessions WHERE id = $1", sid)
            await conn.close()

    asyncio.run(_try_dup())
