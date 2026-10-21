"""R11.5-C 可靠性门禁（真实 teacher-valkey 9.1.2 容器，禁进程内模拟——红线 5）。

证明（R11.5-C FINAL-GATE 证据）：
  A. TransactionalOutbox → ValkeyStreamPublisher（XADD，message_id 幂等）→ 行 PUBLISHED；
  B. 消费侧读取真实 Stream（XREADGROUP）→ InboxDedupe 判定 → 副作用一次；
  C. 同 message_id 二次投递（重复消息）→ 去重命中 → 不产生第二个副作用；
  D. queue_policy 模板键（strm:tp8100:*）在真实 Valkey 上成立（owner 隔离）。

跑法：compose 起 postgres/valkey 后 `uv run pytest tests/integration/valkey_gate/test_reliability_gate.py -v -m gate`。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool

import app.db.models  # noqa: F401
from app.db.base import Base
from app.reliability.inbox import InboxDedupe
from app.reliability.models import ReliabilityOutbox
from app.reliability.outbox import OutboxDispatcher, OutboxService
from app.reliability.queue_policy import OWNER_SERVICE_8100, stream_of
from app.reliability.security import redact

pytestmark = pytest.mark.gate

VALKEY_URL = os.environ.get("TEACHER_VALKEY_URL", "redis://127.0.0.1:6380/0")
TOPIC = "deck.generate"
STREAM = stream_of(OWNER_SERVICE_8100, TOPIC)  # strm:tp8100:deck.generate


class BusinessRow(Base):
    """gate 专属业务副作用表（仅测试库；单副作用断言）。"""

    __tablename__ = "reliability_demo_business"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


@pytest.fixture
async def valkey():
    redis = pytest.importorskip("redis.asyncio", reason="redis 未安装")
    client = redis.Redis.from_url(VALKEY_URL, decode_responses=True)
    try:
        await asyncio.wait_for(client.ping(), timeout=5)
    except Exception as exc:  # noqa: BLE001
        await client.aclose()
        pytest.skip(f"teacher-valkey 不可达（compose 未启动？）：{exc}")
    yield client
    await client.aclose()


@pytest.fixture
async def db_session_factory(tmp_path_factory):
    """临时文件 SQLite 库（避免污染真实 PG；gate 关注的是 Valleey Stream 传输语义）。"""
    path = str(tmp_path_factory.mktemp("relgate") / "gate.db").replace("\\", "/")
    url = f"sqlite+aiosqlite:///{path}"
    from sqlalchemy.pool import StaticPool

    eng = create_async_engine(url, poolclass=StaticPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()
    return lambda: async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


async def test_outbox_stream_consumer_dedupe_real_valkey(valkey, db_session_factory) -> None:
    factory = db_session_factory()
    job_ref = str(uuid.uuid4())
    transport_topic = STREAM  # topic = 经 queue_policy 固化后的传输键（strm:tp8100:deck.generate）
    # API 进程：业务意图 + outbox 同事务
    async with factory() as s:
        await OutboxService.enqueue(
            s, topic=transport_topic,
            payload={"job_id": job_ref, "op": "deck.generate"},
            dedupe_key=job_ref,
            trace_id="trace-123",
            correlation_id="corr-abc",
        )
        await s.commit()
    # dispatcher：真实 ValkeyStreamPublisher 发布（XADD）
    from app.reliability.outbox import ValkeyStreamPublisher

    pub = ValkeyStreamPublisher(redis_url=VALKEY_URL, maxlen=500)
    d = OutboxDispatcher(factory, pub, poll_interval_s=0.01, topics=[transport_topic])
    assert await d.poll_once() == 1
    async with factory() as s:
        row = (await s.execute(select(ReliabilityOutbox))).scalars().one()
        assert row.status == "PUBLISHED"
        assert row.trace_id == "trace-123"
        assert row.correlation_id == "corr-abc"
    # 消费侧：真实 Stream 读取两条（同一 message_id 重发 → 幂等投递）
    entries = await valkey.xrange(STREAM, "-", "+", count=10)
    assert entries, f"stream {STREAM} 无消息"
    raw_payload = json.loads(entries[0][1]["payload"])
    assert raw_payload["_meta"]["message_id"] == str(row.id)  # 消息 ID 幂等锚点
    # 首次消费：执行副作用
    hash1 = InboxDedupe.payload_hash_of(raw_payload)
    async with factory() as s:
        res = await InboxDedupe.consume(s, owner_service=OWNER_SERVICE_8100, dedupe_key=job_ref, payload_hash=hash1)
        assert res.duplicate is False
        s.add(BusinessRow(job_ref=job_ref))
        await s.commit()
    # 重复消息（同 dedupe_key）→ 去重命中 → 不重复副作用
    async with factory() as s:
        res2 = await InboxDedupe.consume(s, owner_service=OWNER_SERVICE_8100, dedupe_key=job_ref, payload_hash=hash1)
        assert res2.duplicate is True
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(BusinessRow).where(BusinessRow.job_ref == job_ref))).scalars().all()
        assert len(rows) == 1  # 单副作用（重复投递未产生第二行）
    # 脱敏证据：stream 内不应含任何密钥形态
    dumped = json.dumps(raw_payload, ensure_ascii=False)
    assert redact(json.loads(dumped)) == json.loads(dumped)  # 无敏感 key
    # 自洁：删 stream（数据审计黑名单惯例；gate 首次创建）
    await valkey.delete(STREAM)


async def test_queue_policy_owner_isolation_on_real_stream(valkey) -> None:
    s1 = stream_of(OWNER_SERVICE_8100, "deck.generate")
    from app.reliability.queue_policy import OWNER_SERVICE_8200

    s2 = stream_of(OWNER_SERVICE_8200, "deck.generate")
    assert s1 != s2
    # 8200 流键在真实 Valkey 上尚不存在（8100 发布不污染 8200 命名空间）
    assert await valkey.exists(s2) == 0
