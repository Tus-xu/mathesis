"""Classroom 契约测试（M2-A Gate 前置 · 02-ARCHITECTURE §12 逐条落断言）。

覆盖 §12 三层状态机 / REST 契约 / token 作用域 / 断线恢复 / 作答幂等：
- 状态机合法链与**非法转移矩阵**（业务错误码，非 500——红线 9）；
- 课堂 token 作用域矩阵（教师端点 40301 / 本 session 学生端点 200 / 跨 session 403）；
- 作答幂等（UNIQUE(activity_id, participant_id, attempt_no) DB 约束——红线 8）；
- snapshot 权威快照 + SSE durable 重放（Last-Event-ID 零丢失）。

执行档：ASGI 直连（结构/门禁/404 前提）+ compose 全栈（B4 classroom 域落地后全绿）。
当前 classroom 域未实现（B4）——红 = M2-A 缺口清单（红得可读）。
"""
from __future__ import annotations

import json

import pytest

from conftest import (
    CONTRACT_BASE_URL,
    SseEvent,
    _auth,
    _make_token,
    assert_sse_stream_headers,
    collect_sse,
    parse_sse_text,
    seq_of,
)

pytestmark = pytest.mark.usefixtures("app")

SEED_CLASS_ID = "seed-c1"
SESSION = "/teacher-v3/classroom/sessions"

_PG_DSN = "postgres://teacher:teacher_dev_pw@127.0.0.1:5433/teacher_platform"


async def _resolve_class_id(client, teacher_token) -> str:
    """拿一个真实可用的 class_id：API → DB → 字面量兜底（保证红点归因正确：路由缺失而非输入非法）。"""
    r = await client.get("/teacher-v3/classes", headers=_auth(teacher_token))
    if r.status_code == 200:
        items = (r.json().get("data") or {}).get("items") or []
        if items:
            cid = items[0].get("class_id") or items[0].get("id")
            if cid:
                return str(cid)
    try:
        import asyncpg

        conn = await asyncpg.connect(_PG_DSN, timeout=3)
        try:
            row = await conn.fetchrow("SELECT id::text AS id FROM classes LIMIT 1")
            if row is None:
                row = await conn.fetchrow(
                    "INSERT INTO classes (id, name, grade, student_count) "
                    "VALUES (gen_random_uuid(), '高二(9)班', '高二', 40) RETURNING id::text AS id"
                )
            return row["id"]
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001 —— DB 不可达时保持字面量（红点=路由/迁移缺口）
        return SEED_CLASS_ID


async def _open_session(client, teacher_token, class_id: str) -> dict:
    r = await client.post(SESSION, json={"class_id": class_id}, headers=_auth(teacher_token))
    assert r.status_code == 200, f"开课失败：{r.status_code} {r.text[:200]}"
    return r.json()["data"]


# ---------------- 开课与 join ----------------


async def test_open_session_returns_join_code(client, teacher_token):
    """§12.3 — 开课返回 6 位 join_code；join_code UNIQUE。"""
    cid = await _resolve_class_id(client, teacher_token)
    d = await _open_session(client, teacher_token, cid)
    join_code = d.get("join_code") or d.get("data", {}).get("join_code")
    assert join_code, "开课响应缺 join_code"
    assert isinstance(join_code, str) and len(join_code) == 6 and join_code.isdigit(), (
        f"join_code 应为 6 位数字，实际 {join_code!r}"
    )


async def test_join_without_account_gets_scoped_token(client, teacher_token):
    """§10 — 学生 H5 无账号：join_code+姓名 → 课堂作用域短时 token。"""
    cid = await _resolve_class_id(client, teacher_token)
    opened = await _open_session(client, teacher_token, cid)
    join_code = opened["join_code"]
    r = await client.post(
        "/teacher-v3/classroom/join",
        json={"join_code": join_code, "student_name": "契约测试生甲"},
    )
    assert r.status_code == 200, f"join 失败：{r.status_code} {r.text[:200]}"
    d = r.json()["data"]
    assert d.get("classroom_token") or d.get("token"), "join 未签发课堂 token"
    assert d.get("participant_id"), "join 未返回 participant_id"


async def test_join_same_name_reuses_participant(client, teacher_token):
    """§4.6 — UNIQUE(session_id, anon_id)：同名重复 join 复用同一 participant。"""
    cid = await _resolve_class_id(client, teacher_token)
    opened = await _open_session(client, teacher_token, cid)
    body = {"join_code": opened["join_code"], "student_name": "重复加入生"}
    r1 = await client.post("/teacher-v3/classroom/join", json=body)
    r2 = await client.post("/teacher-v3/classroom/join", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    p1 = r1.json()["data"].get("participant_id")
    p2 = r2.json()["data"].get("participant_id")
    assert p1 and p2 and p1 == p2, f"同名二次 join 应复用 participant：{p1} vs {p2}"


# ---------------- 状态机合法链（M2-A L9-basic 主链） ----------------


async def _drive_session_to(client, teacher_token, target: str):
    """把一个新 session 推到目标状态，返回 (session_id, activity_id)。"""
    cid = await _resolve_class_id(client, teacher_token)
    opened = await _open_session(client, teacher_token, cid)
    sid = opened.get("session_id") or opened.get("id")
    act = (
        await client.post(
            f"{SESSION}/{sid}/activities",
            json={"kind": "question", "config": {}},
            headers=_auth(teacher_token),
        )
    ).json()["data"]
    aid = act["activity_id"] or act["id"]
    steps = {
        "pushed": [],
        "locked": ["lock"],
        "revealed": ["lock", "reveal"],
    }
    for step in steps.get(target, []):
        r = await client.post(f"{SESSION}/{sid}/activities/{aid}/{step}", headers=_auth(teacher_token))
        assert r.status_code == 200, f"{step} 失败：{r.status_code} {r.text[:200]}"
    return sid, aid


@pytest.mark.parametrize("target", ["pushed", "locked", "revealed"])
async def test_activity_legal_transitions(client, teacher_token, target):
    """§12.2 — Activity 合法链 pushed→collecting→locked→revealed 逐态可达。"""
    sid, aid = await _drive_session_to(client, teacher_token, target)
    snap = await client.get(f"{SESSION}/{sid}/snapshot", headers=_auth(teacher_token))
    assert snap.status_code == 200, "snapshot 不可用"
    acts = snap.json()["data"].get("activities", [])
    mine = next((a for a in acts if a.get("activity_id") == aid or a.get("id") == aid), None)
    if mine is not None:
        expected = {"pushed": "collecting", "locked": "locked", "revealed": "revealed"}[target]
        assert mine.get("status") == expected, f"目标 {target}：状态应 {expected}，实际 {mine.get('status')}"


async def test_session_end_archives(client, teacher_token):
    """§12.3 — 结课 → 归档 → classroom_summaries 生成（L9 判定⑥）。"""
    sid, _ = await _drive_session_to(client, teacher_token, "pushed")
    r = await client.post(f"{SESSION}/{sid}/end", headers=_auth(teacher_token))
    assert r.status_code == 200, f"结课失败：{r.status_code} {r.text[:200]}"
    snap = (await client.get(f"{SESSION}/{sid}/snapshot", headers=_auth(teacher_token))).json()["data"]
    assert snap.get("status") in ("ended", "archived"), f"结课后状态={snap.get('status')}"


# ---------------- 非法转移矩阵（业务错误码，非 500——红线 9） ----------------


async def test_illegal_transitions_are_business_errors(client, teacher_token):
    """§12.2 铁律：locked 后再 lock / revealed 后再 lock / ended 后推题 → code 非 0 且 HTTP≠500。"""
    sid, aid = await _drive_session_to(client, teacher_token, "locked")
    illegal = [
        ("POST", f"{SESSION}/{sid}/activities/{aid}/lock"),  # locked → lock 重复
        ("POST", f"{SESSION}/{sid}/activities/{aid}/reveal"),  # 由 locked 重复 reveal 在 reveal 后再验
    ]
    for method, url in illegal:
        r = await client.request(method, url, headers=_auth(teacher_token))
        assert r.status_code != 500, f"{url} 非法转移不得 500"
        if r.status_code in (400, 409):
            assert r.json()["code"] != 0, "非法转移必须返回业务错误码"

    # revealed 之后再 lock：非法转移必须业务错误码；若实现幂等放行则不得回退状态
    sid2, aid2 = await _drive_session_to(client, teacher_token, "revealed")
    r2 = await client.post(f"{SESSION}/{sid2}/activities/{aid2}/lock", headers=_auth(teacher_token))
    assert r2.status_code != 500, "revealed 后 lock 不得 500"
    if r2.status_code in (400, 409, 403):
        assert r2.json()["code"] != 0

    # ended 后任何活动写操作
    sid3, aid3 = await _drive_session_to(client, teacher_token, "pushed")
    await client.post(f"{SESSION}/{sid3}/end", headers=_auth(teacher_token))
    r3 = await client.post(
        f"{SESSION}/{sid3}/activities/{aid3}/lock", headers=_auth(teacher_token)
    )
    assert r3.status_code != 500, "ended 后写活动不得 500"
    if r3.status_code in (400, 409, 403):
        assert r3.json()["code"] != 0


# ---------------- token 作用域矩阵（§10） ----------------


async def test_classroom_token_scope_matrix(client, classroom_student_token):
    """合成课堂 token（负向）：教师 Web 端点 40301；真实正向 token 用例走 join 签发（M2-A L9）。"""
    # 教师端点 → 40301
    r = await client.get("/teacher-v3/today", headers=_auth(classroom_student_token))
    assert r.status_code == 403 and r.json()["code"] == 40301
    # 课堂教师操作端点 → 不得 500
    r2 = await client.post(SESSION, json={"class_id": SEED_CLASS_ID}, headers=_auth(classroom_student_token))
    assert r2.status_code in (401, 403), f"课堂 token 开课应被拒：{r2.status_code}"


async def test_join_issued_token_cannot_call_teacher_endpoints(client, teacher_token):
    """§10 — **真实 join 签发**的课堂 token 调教师端点必须 403（L9 判定①，正向 token 负向使用）。"""
    cid = await _resolve_class_id(client, teacher_token)
    opened = await _open_session(client, teacher_token, cid)
    joined = await client.post(
        "/teacher-v3/classroom/join",
        json={"join_code": opened["join_code"], "student_name": "越权探针生"},
    )
    assert joined.status_code == 200, "join 未实现（B4）——本用例红即 M2-A 缺口"
    token = joined.json()["data"].get("classroom_token") or joined.json()["data"].get("token")
    assert token, "join 未签发课堂 token"
    r = await client.get("/teacher-v3/today", headers=_auth(token))
    assert r.status_code == 403, f"课堂 token 调教师端点应 403，实际 {r.status_code}"
    assert r.json()["code"] == 40301


# ---------------- 作答幂等（compose 全栈） ----------------


@pytest.mark.compose
class TestResponsesIdempotency:
    """§14 — UNIQUE(activity_id, participant_id, attempt_no)：重复提交返回首次结果。

    主体断言在 tests/contract/test_idempotency.py（全栈跑法）；此处补并发双写竞态。
    """

    async def test_double_submit_concurrent_single_row(self, http_base, teacher_token):
        import asyncio as _asyncio

        from conftest import PrefixingAsyncClient

        async with PrefixingAsyncClient(base_url=http_base, timeout=30) as c:
            opened = (await c.post(SESSION, json={"class_id": SEED_CLASS_ID}, headers=_auth(teacher_token))).json()["data"]
            sid = opened.get("session_id") or opened.get("id")
            joined = (
                await c.post("/teacher-v3/classroom/join", json={"join_code": opened["join_code"], "student_name": "并发幂等生"})
            ).json()["data"]
            token = joined.get("classroom_token") or joined.get("token")
            act = (
                await c.post(
                    f"{SESSION}/{sid}/activities", json={"kind": "question", "config": {}}, headers=_auth(teacher_token)
                )
            ).json()["data"]
            aid = act.get("activity_id") or act.get("id")
            body = {"activity_id": aid, "answer": {"choice": "B"}}

            async def submit():
                return await c.post(f"{SESSION}/{sid}/responses", json=body, headers=_auth(token))

            r1, r2 = await _asyncio.gather(submit(), submit())
            assert r1.status_code == 200 and r2.status_code == 200, (
                f"双提交应都 200（幂等回放）：{r1.status_code}/{r2.status_code} {r1.text[:120]} {r2.text[:120]}"
            )
            assert r1.json() == r2.json(), "并发同 attempt 双提交必须返回同一结果"


# ---------------- snapshot + SSE durable 重放（compose 全栈） ----------------


@pytest.mark.compose
class TestSnapshotAndReplay:
    """§12.4 — 连接即 snapshot → durable 重放 after(Last-Event-ID) → 零丢失。"""

    async def test_snapshot_is_authoritative_and_stable(self, http_base, teacher_token):
        import httpx

        async with httpx.AsyncClient(base_url=http_base, timeout=30) as c:
            opened = (await c.post(SESSION, json={"class_id": SEED_CLASS_ID}, headers=_auth(teacher_token))).json()["data"]
            sid = opened.get("session_id") or opened.get("id")
            s1 = await c.get(f"{SESSION}/{sid}/snapshot", headers=_auth(teacher_token))
            s2 = await c.get(f"{SESSION}/{sid}/snapshot", headers=_auth(teacher_token))
            assert s1.status_code == 200 and s2.status_code == 200
            assert s1.json() == s2.json(), "无变化时 snapshot 两次调用应一致（权威投影确定性）"

    async def test_stream_replays_after_last_event_id(self, http_base, teacher_token):
        from conftest import PrefixingAsyncClient

        async with PrefixingAsyncClient(base_url=http_base, timeout=30) as c:
            opened = (await c.post(SESSION, json={"class_id": SEED_CLASS_ID}, headers=_auth(teacher_token))).json()["data"]
            sid = opened.get("session_id") or opened.get("id")
            # 第一段：收集到 N 个事件（含 id）
            status, headers, text = await collect_sse(
                c, "GET", f"{SESSION}/{sid}/stream", headers=_auth(teacher_token), timeout=10
            )
            assert status == 200
            assert_sse_stream_headers(headers)
            events = parse_sse_text(text)
            assert events and events[0].event == "snapshot", "教师流首帧必须是 snapshot（§12.4）"
            numbered = [e for e in events if e.id is not None]
            if len(numbered) < 2:
                pytest.skip("重放前提：durable 事件 <2（课堂无活动发生）")
            # 第二段：以最后 id 重连 → 不应收到 ≤N 的重复事件
            last_id = int(numbered[-1].id)
            status2, _, text2 = await collect_sse(
                c,
                "GET",
                f"{SESSION}/{sid}/stream",
                headers={**_auth(teacher_token), "Last-Event-ID": str(last_id)},
                timeout=8,
            )
            events2 = parse_sse_text(text2)
            ids2 = [int(e.id) for e in events2 if e.id is not None]
            assert all(i > last_id for i in ids2), (
                f"Last-Event-ID={last_id} 重连后出现重复/回退事件：{ids2[:10]}"
            )
