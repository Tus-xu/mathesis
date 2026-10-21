"""队列（Valkey / Redis Streams）—— **协调层，不是事实来源**。

来自 `deploy/registry.yml` 的冻结值（**不发明**）：
- port `6380`
- `stream_prefix` = `strm:rv8300`
- `consumer_group` = `rv8300-workers`
- `dedupe_scope` = `rv8300`

**铁律**：
1. PostgreSQL 是唯一事实权威。队列只负责"尽快把已提交的事件送达 worker"。
2. 丢 Valkey 不得丢真相：未发布事件仍在 `outbox_event`，relay 会重发；
   已发布未消费的，worker 也能从 `job_run` 直接捞（见 `job_service.claim_due_jobs`）。
3. **生产绝不退化为进程内队列**：`InProcessQueue` 只在
   `RESEARCH_ENV=test` 且显式请求时可用；生产路径拿到它即报错。
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from ..config import (
    CONSUMER_GROUP,
    DEDUPE_SCOPE,
    consumer_name,
    is_test,
    queue_dlq_key,
    queue_stream_key,
    valkey_url,
)


class QueueUnavailable(RuntimeError):
    """Valkey 不可达。**不得**吞掉后改用本地队列假装正常。"""


def _decode_entry(stream_id: str, fields: dict) -> dict:
    """把 stream entry 解成消息 dict。

    **必须**把 entry 的 `event_id` / `scope` 一并带出：worker 的 inbox 去重键是
    `(event_id, job_id, attempt)`，丢了 `event_id` 就等于丢了去重能力
    （实测：只返回 payload 时 `event_id` 缺失，重复投递不再可去重）。
    """
    import json

    raw = fields.get("payload") or "{}"
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001 - 坏消息不能卡死消费
        payload = {"_corrupt": raw[:512]}
    if not isinstance(payload, dict):
        payload = {"_value": payload}
    payload.setdefault("event_id", fields.get("event_id", ""))
    payload.setdefault("scope", fields.get("scope", ""))
    payload["_stream_id"] = stream_id
    return payload


@runtime_checkable
class Queue(Protocol):
    name: str

    def publish(self, event_id: str, payload: dict[str, Any]) -> str | None: ...

    def read(self, *, consumer: str, count: int, block_ms: int) -> list[tuple[str, dict]]: ...

    def ack(self, stream_id: str) -> None: ...

    def dedupe(self, event_id: str, job_id: str, attempt: int) -> bool: ...

    def pending_count(self) -> int: ...

    def send_to_dlq(self, event_id: str, payload: dict[str, Any]) -> None: ...


class ValkeyStreamQueue:
    """Redis Streams 实现（consumer group + 显式 ack + dedupe key）。"""

    name = "valkey"

    def __init__(
        self,
        *,
        url: str | None = None,
        group: str | None = None,
        dedupe_scope: str | None = None,
        stream: str | None = None,
    ) -> None:
        self._url = url or valkey_url()
        self._group = group or CONSUMER_GROUP
        self._scope = dedupe_scope or DEDUPE_SCOPE
        # RV14：compile pool 使用独立 stream（strm:rv8300:compile），
        # 与主队列共享同一 Valkey 但 stream/group/dedupe 全隔离。
        self._stream = stream or queue_stream_key()
        self._dlq = f"{self._stream}:dlq" if stream else queue_dlq_key()
        self._client: Any = None

    # ---------------- 连接 ----------------

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import redis  # 惰性 import
            except ModuleNotFoundError as e:  # pragma: no cover - 依赖缺失即 fail loud
                raise QueueUnavailable(
                    "redis 客户端未安装：无法使用 Valkey 队列（生产禁止退化为进程内队列）"
                ) from e

            self._client = redis.Redis.from_url(
                self._url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5
            )
        return self._client

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as e:  # noqa: BLE001
            if "BUSYGROUP" not in str(e):
                raise QueueUnavailable(f"无法创建 consumer group: {e}") from e

    # ---------------- 生产 ----------------

    def publish(self, event_id: str, payload: dict[str, Any]) -> str | None:
        import json

        fields = {
            "event_id": event_id,
            "scope": self._scope,
            "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        }
        try:
            return self.client.xadd(self._stream, fields)
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XADD 失败: {e}") from e

    # ---------------- 消费 ----------------

    def read(self, *, consumer: str | None = None, count: int = 10, block_ms: int = 2000) -> list[tuple[str, dict]]:
        consumer = consumer or consumer_name()
        try:
            self.ensure_group()
            resp = self.client.xreadgroup(
                self._group, consumer, {self._stream: ">"}, count=count, block=block_ms
            )
        except QueueUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XREADGROUP 失败: {e}") from e

        out: list[tuple[str, dict]] = []
        for _stream, entries in resp or []:
            for stream_id, fields in entries:
                out.append((stream_id, _decode_entry(stream_id, fields)))
        return out

    def claim_stale(self, *, consumer: str, min_idle_ms: int, count: int = 10) -> list[tuple[str, dict]]:
        """接管"被投递但消费者已死"的消息（XAUTOCLAIM）。"""
        try:
            self.ensure_group()
            resp = self.client.xautoclaim(
                self._stream, self._group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count
            )
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XAUTOCLAIM 失败: {e}") from e

        entries = resp[1] if isinstance(resp, (list, tuple)) and len(resp) > 1 else []
        out: list[tuple[str, dict]] = []
        for stream_id, fields in entries or []:
            if not fields:
                continue
            out.append((stream_id, _decode_entry(stream_id, fields)))
        return out

    def ack(self, stream_id: str) -> None:
        try:
            self.client.xack(self._stream, self._group, stream_id)
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XACK 失败: {e}") from e

    # ---------------- 去重 / DLQ / 观测 ----------------

    def dedupe(self, event_id: str, job_id: str, attempt: int) -> bool:
        """`SET NX` 去重键 —— 队列层的**快速**去重。

        权威去重仍是 PostgreSQL 的 `job_inbox` 唯一约束：
        队列去重丢 key（重启/过期）也不会导致重复执行。
        """
        key = f"{self._scope}:seen:{event_id}:{job_id}:{attempt}"
        try:
            return bool(self.client.set(key, "1", nx=True, ex=86400))
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"SETNX 去重失败: {e}") from e

    def send_to_dlq(self, event_id: str, payload: dict[str, Any]) -> None:
        import json

        try:
            self.client.xadd(
                self._dlq,
                {"event_id": event_id, "payload": json.dumps(payload, ensure_ascii=False)},
            )
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"DLQ XADD 失败: {e}") from e

    def pending_count(self) -> int:
        try:
            info = self.client.xpending(self._stream, self._group)
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XPENDING 失败: {e}") from e
        if isinstance(info, dict):
            return int(info.get("pending") or 0)
        if isinstance(info, (list, tuple)) and info:
            return int(info[0])
        return 0

    def stream_length(self) -> int:
        try:
            return int(self.client.xlen(self._stream))
        except Exception as e:  # noqa: BLE001
            raise QueueUnavailable(f"XLEN 失败: {e}") from e

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:  # noqa: BLE001
            return False


class InProcessQueue:
    """**仅测试**用的进程内队列。

    生产环境构造它会直接抛错 —— 这是"绝不退化为进程内队列"的机器强制点。
    它存在的唯一理由：让幂等/租约/围栏这些逻辑可以在没有 Valkey 的场景下被单测。
    """

    name = "in-process"

    def __init__(self) -> None:
        if not is_test():
            raise QueueUnavailable(
                "生产环境禁止进程内队列（RESEARCH_ENV != test）："
                "Valkey 不可用时必须诚实失败，而不是伪装成功"
            )
        self._items: list[tuple[str, dict]] = []
        self._acks: list[str] = []
        self._seen: set[str] = set()
        self._dlq: list[tuple[str, dict]] = []
        self._n = 0

    def publish(self, event_id: str, payload: dict[str, Any]) -> str | None:
        self._n += 1
        sid = f"{self._n}-0"
        self._items.append((sid, {"event_id": event_id, "_stream_id": sid, **payload}))
        return sid

    def read(self, *, consumer: str = "inproc", count: int = 10, block_ms: int = 0) -> list[tuple[str, dict]]:
        out = self._items[:count]
        self._items = self._items[count:]
        return out

    def ack(self, stream_id: str) -> None:
        self._acks.append(stream_id)

    def dedupe(self, event_id: str, job_id: str, attempt: int) -> bool:
        key = f"{event_id}:{job_id}:{attempt}"
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def send_to_dlq(self, event_id: str, payload: dict[str, Any]) -> None:
        self._dlq.append((event_id, payload))

    def pending_count(self) -> int:
        return len(self._items)

    def stream_length(self) -> int:
        return len(self._items) + len(self._acks)

    def ping(self) -> bool:
        return True


_queue: Queue | None = None


def get_queue() -> Queue:
    """当前进程的队列。默认 Valkey；测试可用 `RESEARCH_QUEUE_BACKEND=memory` 切换。"""
    global _queue
    if _queue is None:
        backend = os.getenv("RESEARCH_QUEUE_BACKEND", "valkey").strip().lower()
        if backend == "memory":
            _queue = InProcessQueue()
        else:
            _queue = ValkeyStreamQueue()
    return _queue


def set_queue(q: Queue | None) -> None:
    global _queue
    _queue = q


def queue_available() -> bool:
    q = get_queue()
    ping = getattr(q, "ping", None)
    return bool(ping()) if callable(ping) else False
