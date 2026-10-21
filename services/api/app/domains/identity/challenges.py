"""Purpose-bound SMS challenge lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import Protocol

from app.domains.identity.sms import ProviderError

ALLOWED_PURPOSES = {
    "login",
    "registration",
    "password_reset",
    "phone_change_old",
    "phone_change_new",
    "admin_mfa",
    "account_deletion",
}


class ConsumeResult(IntEnum):
    CONSUMED = 1
    MISSING = -1
    INVALID = -2
    LOCKED = -3
    PURPOSE_MISMATCH = -4


@dataclass(frozen=True)
class ChallengeRecord:
    challenge_id: str
    phone_digest: str
    purpose: str
    code_digest: str
    expires_at: datetime
    attempts: int = 0


@dataclass(frozen=True)
class ChallengeIssued:
    challenge_id: str
    expires_in: int
    retry_after: int
    demo_code: str | None = None


class ChallengeError(Exception):
    def __init__(
        self,
        error_key: str,
        message: str,
        *,
        http_status: int = 400,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.error_key = error_key
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after


class ChallengeStore(Protocol):
    async def reserve_send(self, phone_digest: str, ip_prefix: str) -> int: ...

    async def save(self, record: ChallengeRecord) -> None: ...

    async def consume(
        self,
        challenge_id: str,
        purpose: str,
        code_digest: str,
        max_attempts: int,
    ) -> ConsumeResult: ...


class RedisChallengeStore:
    _CONSUME_SCRIPT = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 0 then return -1 end
local attempts = tonumber(redis.call('HGET', key, 'attempts') or '0')
local max_attempts = tonumber(ARGV[3])
if attempts >= max_attempts then return -3 end
local stored_purpose = redis.call('HGET', key, 'purpose')
if stored_purpose ~= ARGV[1] then
  redis.call('HINCRBY', key, 'attempts', 1)
  return -4
end
local stored_digest = redis.call('HGET', key, 'code_digest')
if stored_digest ~= ARGV[2] then
  redis.call('HINCRBY', key, 'attempts', 1)
  return -2
end
redis.call('DEL', key)
return 1
"""

    def __init__(self, redis_client):
        self.redis = redis_client

    async def reserve_send(self, phone_digest: str, ip_prefix: str) -> int:
        key = f"auth:sms:slot:{phone_digest}"
        acquired = await self.redis.set(key, "1", ex=60, nx=True)
        if acquired:
            return 0
        ttl = await self.redis.ttl(key)
        return max(1, int(ttl or 1))

    async def save(self, record: ChallengeRecord) -> None:
        key = f"auth:sms:challenge:{record.challenge_id}"
        ttl = max(1, int((record.expires_at - datetime.now(UTC)).total_seconds()))
        values = {
            "phone_digest": record.phone_digest,
            "purpose": record.purpose,
            "code_digest": record.code_digest,
            "attempts": str(record.attempts),
        }
        pipeline = self.redis.pipeline(transaction=True)
        for field, value in values.items():
            pipeline.hset(key, field, value)
        pipeline.expire(key, ttl)
        await pipeline.execute()

    async def consume(
        self,
        challenge_id: str,
        purpose: str,
        code_digest: str,
        max_attempts: int,
    ) -> ConsumeResult:
        value = await self.redis.eval(
            self._CONSUME_SCRIPT,
            1,
            f"auth:sms:challenge:{challenge_id}",
            purpose,
            code_digest,
            max_attempts,
        )
        return ConsumeResult(int(value))


class ChallengeService:
    ttl_seconds = 300
    max_attempts = 5

    def __init__(
        self,
        *,
        store: ChallengeStore,
        provider,
        pepper: str,
        now: Callable[[], datetime] | None = None,
        code_generator: Callable[[], str] | None = None,
    ):
        if not pepper:
            raise ValueError("OTP pepper must not be empty")
        self.store = store
        self.provider = provider
        self.pepper = pepper.encode("utf-8")
        self.now = now or (lambda: datetime.now(UTC))
        self.code_generator = code_generator or (lambda: f"{secrets.randbelow(1_000_000):06d}")

    def _digest(self, value: str) -> str:
        return hmac.new(self.pepper, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _code_digest(self, challenge_id: str, phone: str, purpose: str, code: str) -> str:
        return self._digest(f"{challenge_id}:{phone}:{purpose}:{code}")

    async def create(self, phone: str, purpose: str, *, ip_prefix: str) -> ChallengeIssued:
        if purpose not in ALLOWED_PURPOSES:
            raise ChallengeError("AUTH_CHALLENGE_PURPOSE_INVALID", "验证码用途无效")
        phone_digest = self._digest(phone)
        retry_after = await self.store.reserve_send(phone_digest, ip_prefix)
        if retry_after:
            raise ChallengeError(
                "AUTH_RATE_LIMITED",
                "验证码发送过于频繁",
                http_status=429,
                retry_after=retry_after,
            )
        challenge_id = str(uuid.uuid4())
        code = self.code_generator()
        try:
            receipt = await self.provider.send(phone, purpose, code)
        except ProviderError as exc:
            status_code = 503 if exc.error_key == "SMS_PROVIDER_UNAVAILABLE" else 400
            raise ChallengeError(exc.error_key, exc.message, http_status=status_code) from None
        await self.store.save(
            ChallengeRecord(
                challenge_id=challenge_id,
                phone_digest=phone_digest,
                purpose=purpose,
                code_digest=self._code_digest(challenge_id, phone, purpose, code),
                expires_at=self.now() + timedelta(seconds=self.ttl_seconds),
            )
        )
        return ChallengeIssued(
            challenge_id=challenge_id,
            expires_in=self.ttl_seconds,
            retry_after=60,
            demo_code=receipt.demo_code,
        )

    async def consume(self, challenge_id: str, phone: str, purpose: str, code: str) -> None:
        result = await self.store.consume(
            challenge_id,
            purpose,
            self._code_digest(challenge_id, phone, purpose, code),
            self.max_attempts,
        )
        errors = {
            ConsumeResult.MISSING: ("AUTH_CODE_EXPIRED", "验证码已过期，请重新获取"),
            ConsumeResult.INVALID: ("AUTH_CODE_INVALID", "验证码错误"),
            ConsumeResult.LOCKED: ("AUTH_CHALLENGE_LOCKED", "验证码失败次数过多，请重新获取"),
            ConsumeResult.PURPOSE_MISMATCH: (
                "AUTH_CHALLENGE_PURPOSE_MISMATCH",
                "验证码用途不匹配",
            ),
        }
        if result != ConsumeResult.CONSUMED:
            error_key, message = errors[result]
            raise ChallengeError(error_key, message)
