"""REST 幂等（G8）：Idempotency-Key 头 → Valkey SETNX 短锁 + PG 存响应，15min 窗口重放首结果。

幂等靠 DB 唯一约束兜底（红线 8）——本中间件是「窗口内重放首结果」的语义层，
底层 `idempotency_records(key,user_id)` 主键保证并发竞态不双写。
SSE 流式端点不缓存（流不可重放）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import get_settings
from ..db.models import IdempotencyRecords
from ..db.session import session_factory

logger = logging.getLogger("teacher.idempotency")

_IDEM_SKIP_SUFFIXES = ("/stream", "/student-stream", "/healthz")
_IDEM_SKIP_PREFIXES = ("/api/teacher-v3",)  # 路由实际挂载前缀（BASE='/api' + teacher-v3）


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """包装写端点：带 Idempotency-Key 的请求窗口内返回首次响应。

    流程：SETNX 短锁（30s）→ 处理 → PG 存响应 → 释放锁；
    重放（同 key+user 窗口内）→ 直接返回 PG 首结果；锁被占（并发同 key）→ 轮询等待首结果。
    """

    def __init__(self, app, *, redis_url: str | None = None, window_seconds: int | None = None):
        super().__init__(app)
        settings = get_settings()
        self.redis_url = redis_url or settings.valkey_url
        self.window = window_seconds or settings.idempotency_window_seconds
        self._redis: aioredis.Redis | None = None

    async def _redis_client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.Redis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    def _should_skip(self, request: Request) -> bool:
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return True
        if not any(request.url.path.startswith(p) for p in _IDEM_SKIP_PREFIXES):
            return True
        if any(request.url.path.endswith(s) for s in _IDEM_SKIP_SUFFIXES):
            return True
        return not request.headers.get("Idempotency-Key")

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if self._should_skip(request):
            return await call_next(request)

        idem_key = request.headers["Idempotency-Key"]
        user_id = _user_id_from(request)
        record_key = f"idem:{user_id}:{idem_key}"
        r = await self._redis_client()

        # 1) 窗口内重放：命中 PG 首结果直接返回（唯一约束保证同 key+user 只存一行）
        async with session_factory()() as session:
            row = (
                await session.execute(
                    select(IdempotencyRecords).where(
                        IdempotencyRecords.key == idem_key,
                        IdempotencyRecords.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is not None and _within_window(row.created_at):
                logger.info("idempotency replay", extra={"key": idem_key})
                return JSONResponse(content=row.response, status_code=200)

        # 2) 短锁：SETNX 30s；拿不到锁 = 同 key 并发在途 → 轮询等首结果
        locked = await r.set(record_key, "processing", nx=True, ex=30)
        if not locked:
            for _ in range(40):  # ≤ ~10s
                await asyncio.sleep(0.25)
                async with session_factory()() as session:
                    row = (
                        await session.execute(
                            select(IdempotencyRecords).where(
                                IdempotencyRecords.key == idem_key,
                                IdempotencyRecords.user_id == user_id,
                            )
                        )
                    ).scalar_one_or_none()
                if row is not None:
                    return JSONResponse(content=row.response, status_code=200)
            locked = True  # 锁持有者疑似崩溃：接管执行（底层唯一约束仍防双写）

        # 3) 正常执行并缓存响应（BaseHTTPMiddleware 的 call_next 返回流式包装响应，
        #    必须消费 body_iterator 重建响应体后再判断缓存）
        try:
            response = await call_next(request)
            body_bytes = b"".join([chunk async for chunk in response.body_iterator])
            cached = Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
            )
            if response.status_code < 400:
                try:
                    body_json = json.loads(body_bytes)
                except (ValueError, TypeError):
                    body_json = None
                if isinstance(body_json, dict):
                    async with session_factory()() as session:
                        session.add(
                            IdempotencyRecords(
                                key=idem_key,
                                user_id=user_id,
                                endpoint=request.url.path,
                                response=body_json,
                            )
                        )
                        await session.commit()
            return cached
        finally:
            await r.delete(record_key)


def _within_window(created_at: Any) -> bool:
    if isinstance(created_at, (int, float)):
        ts = float(created_at)
    elif hasattr(created_at, "timestamp"):
        ts = created_at.timestamp()
    else:
        ts = time.time()
    return time.time() - ts < get_settings().idempotency_window_seconds


def _user_id_from(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from .security import decode_access_token

            return str(decode_access_token(auth[7:]).get("sub", "anon"))
        except Exception:  # noqa: BLE001
            return "anon"
    return "anon"
