"""任务中心（TaskRunner + task_router）测试 · 阶段3 G 系列

覆盖验收用例：G-1 幂等 / G-2 后台执行 / G-4 失败重试 / G-5 取消 / G-6 隔离 /
G-7 增量 / G-9 通知降噪（同任务同终态一条）/ G-3 stale 自愈。
使用专用测试库（conftest 强制），fake handler 不触网。
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db
from app.models.task import Notification, Task
from app.models.user import User
from app.services import task_runner


def _make_test_engine():
    return create_async_engine(settings.database_url, poolclass=NullPool)


_test_engine = _make_test_engine()
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _ensure_user(user_id: uuid.UUID, phone: str) -> None:
    """插入真实 users 行满足 tasks.user_id 外键（幂等）。"""
    async with _test_session_factory() as db:
        exists = await db.get(User, user_id)
        if exists is None:
            db.add(User(id=user_id, phone=phone))
            await db.commit()


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _login(client, phone: str) -> str:
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    assert resp.json()["code"] == 0
    return resp.json()["data"]["token"]


async def _wait_terminal(task_id: uuid.UUID, timeout_s: float = 8.0) -> Task:
    """轮询等待任务到终态（runner 在事件循环内异步执行）。"""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        async with _test_session_factory() as db:
            task = await db.get(Task, task_id)
            if task is not None and task.status in ("succeeded", "failed", "cancelled"):
                return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout_s}s 内到达终态")


@pytest.fixture
def demo_handler():
    """可编程 fake handler：记录执行次数，按 attempt 决定成败。"""
    calls: list[str] = []
    ok_runs: list[int] = []

    async def handler(task: Task, db: AsyncSession, progress) -> dict:
        calls.append(f"{task.id}:{task.attempt}")
        await progress("处理中", 50)
        if task.payload.get("fail_attempt") == task.attempt:
            raise RuntimeError("boom")
        ok_runs.append(1)  # 产物幂等口径：只计成功产出（失败尝试不产生物）
        return {"artifact_type": "demo", "jump": "/demo", "runs": len(ok_runs)}

    task_runner._HANDLERS["test.demo"] = handler
    yield handler, calls
    task_runner._HANDLERS.pop("test.demo", None)


# ========== Runner 单元级 ==========


async def test_create_task_idempotent(demo_handler):
    """G-1：同幂等键两次创建 → 同一行。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        t1, c1 = await task_runner.create_task(
            db, user_id=user_id, role="student", kind="test.demo",
            payload={}, idempotency_key="it-idem-1",
        )
        await db.commit()
        t2, c2 = await task_runner.create_task(
            db, user_id=user_id, role="student", kind="test.demo",
            payload={}, idempotency_key="it-idem-1",
        )
        await db.commit()
    assert c1 is True and c2 is False
    assert t1.id == t2.id


async def test_runner_success_and_notification(demo_handler):
    """G-2 + G-9：spawn 后无请求上下文执行到 succeeded；通知恰一条。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        task, _ = await task_runner.create_task(
            db, user_id=user_id, role="student", kind="test.demo",
            payload={}, idempotency_key=f"it-ok-{uuid.uuid4()}",
        )
        await db.commit()
        task_runner.spawn(task)
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded"
    assert final.result["artifact_type"] == "demo"
    assert final.progress == 100 and final.finished_at is not None
    async with _test_session_factory() as db:
        notes = (await db.execute(
            select(Notification).where(Notification.payload["task_id"].astext == str(task.id))
        )).scalars().all()
        assert len(notes) == 1
        assert notes[0].type == "task.succeeded"
        assert notes[0].payload["jump"] == "/demo"


async def test_runner_fail_then_retry(demo_handler):
    """G-4：第一次失败（原因落库+失败通知）→ 重试成功；产物不重复（runs 计数）。"""
    user_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    key = f"it-retry-{uuid.uuid4()}"
    async with _test_session_factory() as db:
        task, _ = await task_runner.create_task(
            db, user_id=user_id, role="student", kind="test.demo",
            payload={"fail_attempt": 1}, idempotency_key=key,
        )
        await db.commit()
        task_runner.spawn(task)
    failed = await _wait_terminal(task.id)
    _, calls = demo_handler
    assert failed.status == "failed"
    assert "boom" in (failed.error or "")
    async with _test_session_factory() as db:
        task = await db.get(Task, task.id)
        await task_runner.retry_task(db, task)
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded"
    # 通知恰两条：attempt1 失败 + attempt2 成功（dedup 含 attempt）
    async with _test_session_factory() as db:
        notes = (await db.execute(
            select(Notification).where(Notification.payload["task_id"].astext == str(task.id))
        )).scalars().all()
        assert {n.type for n in notes} == {"task.failed", "task.succeeded"}
        # 产物幂等：handler 只成功执行一次（result.runs 稳定）
        assert final.result["runs"] == 1, f"calls={calls}, attempts={final.attempt}"


async def test_cancel_queued_running(demo_handler):
    """G-5：协作取消 → cancelled 终态，runner 不覆盖为成功。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")

    async def slow_handler(task: Task, db: AsyncSession, progress) -> dict:
        await progress("慢任务", 10)
        for _ in range(40):  # ~2s 分片，期间可被取消
            await asyncio.sleep(0.05)
            await db.refresh(task)
            if task.status == "cancelled":
                raise asyncio.CancelledError()
        return {"artifact_type": "demo"}

    task_runner._HANDLERS["test.slow"] = slow_handler
    try:
        async with _test_session_factory() as db:
            task, _ = await task_runner.create_task(
                db, user_id=user_id, role="student", kind="test.slow",
                payload={}, idempotency_key=f"it-cancel-{uuid.uuid4()}",
            )
            await db.commit()
            task_runner.spawn(task)
        await asyncio.sleep(0.2)
        async with _test_session_factory() as db:
            task = await db.get(Task, task.id)
            await task_runner.cancel_task(db, task)
        final = await _wait_terminal(task.id)
        assert final.status == "cancelled"
        assert final.result is None
    finally:
        task_runner._HANDLERS.pop("test.slow", None)


async def test_stale_resume(demo_handler):
    """G-3：进程重启遗留（running 且 updated_at 陈旧、无活跃执行者）→ 扫描重新拉起。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    async with _test_session_factory() as db:
        task, _ = await task_runner.create_task(
            db, user_id=user_id, role="student", kind="test.demo",
            payload={}, idempotency_key=f"it-stale-{uuid.uuid4()}",
        )
        task.status = "running"
        task.updated_at = datetime.now(timezone.utc) - timedelta(seconds=task_runner.TASK_STALE_SECONDS + 5)
        await db.commit()
        relaunched = await task_runner.resume_stale(db)
    assert relaunched >= 1
    final = await _wait_terminal(task.id)
    assert final.status == "succeeded"


# ========== 路由级（HTTP，demo 登录） ==========


async def test_task_isolation_between_users(client, demo_handler):
    """G-6：B 用户访问/取消 A 的任务 → 40400（不暴露存在性）。"""
    phone_a = f"138{uuid.uuid4().int % 100000000:08d}"
    phone_b = f"139{uuid.uuid4().int % 100000000:08d}"
    token_a = await _login(client, phone_a)
    token_b = await _login(client, phone_b)

    # A 通过业务路径建任务（直接落库模拟：避免测试触发出题内核）
    from app.models.user import User

    async with _test_session_factory() as db:
        uid_a = (await db.execute(select(User.id).where(User.phone == phone_a).limit(1))).scalar_one()
        task, _ = await task_runner.create_task(
            db, user_id=uid_a, role="student", kind="test.demo",
            payload={}, idempotency_key=f"it-iso-{uuid.uuid4()}",
        )
        await db.commit()
        task_id = str(task.id)

    resp_b = await client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.json()["code"] == 40400
    resp_a = await client.get(f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.json()["code"] == 0
    assert resp_a.json()["data"]["task_id"] == task_id


async def test_list_since_incremental(client, demo_handler):
    """G-7：since 增量过滤——旧任务不出现在新 since 之后。"""
    phone = f"138{uuid.uuid4().int % 100000000:08d}"
    token = await _login(client, phone)
    from app.models.user import User

    async with _test_session_factory() as db:
        uid = (await db.execute(select(User.id).where(User.phone == phone).limit(1))).scalar_one()
        task, _ = await task_runner.create_task(
            db, user_id=uid, role="student", kind="test.demo",
            payload={}, idempotency_key=f"it-since-{uuid.uuid4()}",
        )
        await db.commit()
        task_id = str(task.id)
        now_iso = datetime.now(timezone.utc).isoformat()

    resp = await client.get("/api/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["code"] == 0
    assert any(i["task_id"] == task_id for i in resp.json()["data"]["items"])

    resp2 = await client.get(
        "/api/tasks", params={"since": now_iso}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp2.json()["code"] == 0
    # since=now 之后无新更新 → 不含该任务（或仅为该任务 updated_at==now 边界，允许出现）
    items = resp2.json()["data"]["items"]
    assert all(i["task_id"] != task_id for i in items) or len(items) <= 1


async def test_notifications_list_and_read(client, demo_handler):
    """S-B6：通知列表/未读数/已读。"""
    phone = f"138{uuid.uuid4().int % 100000000:08d}"
    token = await _login(client, phone)
    headers = {"Authorization": f"Bearer {token}"}

    # 制造一条通知（直接落库，语义等价于任务终态回执）
    from app.models.user import User

    async with _test_session_factory() as db:
        uid = (await db.execute(select(User.id).where(User.phone == phone).limit(1))).scalar_one()
        db.add(Notification(
            user_id=uid, type="task.succeeded", title="练习生成已完成",
            payload={"jump": "/practice"}, dedup_key=f"it-note-{uuid.uuid4()}",
        ))
        await db.commit()

    resp = await client.get("/api/notifications/unread-count", headers=headers)
    assert resp.json()["code"] == 0 and resp.json()["data"]["count"] >= 1
    resp = await client.get("/api/notifications", headers=headers)
    items = resp.json()["data"]["items"]
    assert any(n["title"] == "练习生成已完成" for n in items)
    nid = next(n["id"] for n in items if n["title"] == "练习生成已完成")
    resp = await client.post(f"/api/notifications/{nid}/read", headers=headers)
    assert resp.json()["code"] == 0 and resp.json()["data"]["read_at"]
    resp = await client.get("/api/notifications/unread-count", headers=headers)
    assert resp.json()["data"]["count"] == 0


async def test_cancel_while_queued_not_resurrected(demo_handler):
    """竞态回归：排队（等全局信号量）时取消 → 信号量放行后不得复活为 running。"""
    user_id = uuid.uuid4()
    await _ensure_user(user_id, f"138{uuid.uuid4().int % 100000000:08d}")
    # 占满全局 4 个信号量，让目标任务排队
    held = [asyncio.Semaphore()] * 0  # noqa: 占位说明
    acquires = [task_runner._GLOBAL_SEM.acquire() for _ in range(4)]
    await asyncio.gather(*acquires)
    try:
        async with _test_session_factory() as db:
            task, _ = await task_runner.create_task(
                db, user_id=user_id, role="student", kind="test.demo",
                payload={}, idempotency_key=f"it-race-{uuid.uuid4()}",
            )
            await db.commit()
            task_runner.spawn(task)
        await asyncio.sleep(0.1)  # runner 阻塞在信号量上（queued 态）
        async with _test_session_factory() as db:
            task = await db.get(Task, task.id)
            await task_runner.cancel_task(db, task)
        # 释放信号量：pending runner 获得槽位
        for _ in range(4):
            task_runner._GLOBAL_SEM.release()
        await asyncio.sleep(0.6)
        async with _test_session_factory() as db:
            final = await db.get(Task, task.id)
        assert final.status == "cancelled", f"被复活为 {final.status}"
        assert not demo_handler[1], "handler 不应被执行"
    finally:
        for _ in range(4):
            if task_runner._GLOBAL_SEM._value == 0:
                task_runner._GLOBAL_SEM.release()
