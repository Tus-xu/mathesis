"""Purpose-bound, one-time SMS challenge behavior."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.domains.identity.challenges import (
    ChallengeError,
    ChallengeRecord,
    ChallengeService,
    ConsumeResult,
    RedisChallengeStore,
)
from app.domains.identity.router import get_challenge_service
from app.domains.identity.sms import (
    DemoSmsProvider,
    ProviderError,
    TencentSmsProvider,
    map_tencent_error,
)
from app.gateway.redis import get_redis
from app.main import app


class InMemoryChallengeStore:
    """Behavioral store double with the same atomic state transitions as Redis."""

    def __init__(self, now: datetime):
        self.now = now
        self.records: dict[str, ChallengeRecord] = {}
        self.phone_slots: dict[str, datetime] = {}

    async def reserve_send(self, phone_digest: str, ip_prefix: str) -> int:
        available_at = self.phone_slots.get(phone_digest)
        if available_at and available_at > self.now:
            return max(1, int((available_at - self.now).total_seconds()))
        self.phone_slots[phone_digest] = self.now + timedelta(seconds=60)
        return 0

    async def save(self, record: ChallengeRecord) -> None:
        self.records[record.challenge_id] = record

    async def consume(
        self,
        challenge_id: str,
        purpose: str,
        code_digest: str,
        max_attempts: int,
    ) -> ConsumeResult:
        record = self.records.get(challenge_id)
        if record is None or record.expires_at <= self.now:
            self.records.pop(challenge_id, None)
            return ConsumeResult.MISSING
        if record.attempts >= max_attempts:
            return ConsumeResult.LOCKED
        if record.purpose != purpose:
            self.records[challenge_id] = replace(record, attempts=record.attempts + 1)
            return ConsumeResult.PURPOSE_MISMATCH
        if record.code_digest != code_digest:
            self.records[challenge_id] = replace(record, attempts=record.attempts + 1)
            return ConsumeResult.INVALID
        del self.records[challenge_id]
        return ConsumeResult.CONSUMED


def _service(store: InMemoryChallengeStore, provider=None) -> ChallengeService:
    return ChallengeService(
        store=store,
        provider=provider
        or DemoSmsProvider(environment="development", allowlist={"13800138000"}),
        pepper="unit-test-otp-pepper",
        now=lambda: store.now,
        code_generator=lambda: "123456",
    )


async def test_challenge_is_single_use():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    issued = await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")

    await service.consume(issued.challenge_id, "13800138000", "login", "123456")
    with pytest.raises(ChallengeError) as replay:
        await service.consume(issued.challenge_id, "13800138000", "login", "123456")

    assert replay.value.error_key == "AUTH_CODE_EXPIRED"


async def test_purpose_mismatch_does_not_authenticate():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    issued = await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")

    with pytest.raises(ChallengeError) as mismatch:
        await service.consume(
            issued.challenge_id,
            "13800138000",
            "password_reset",
            "123456",
        )

    assert mismatch.value.error_key == "AUTH_CHALLENGE_PURPOSE_MISMATCH"
    await service.consume(issued.challenge_id, "13800138000", "login", "123456")


async def test_registration_purpose_can_issue_and_consume_a_challenge():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)

    issued = await service.create("13800138000", "registration", ip_prefix="127.0.0.0/24")

    await service.consume(issued.challenge_id, "13800138000", "registration", "123456")


async def test_five_invalid_attempts_lock_the_challenge():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    issued = await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")

    for _ in range(5):
        with pytest.raises(ChallengeError) as invalid:
            await service.consume(issued.challenge_id, "13800138000", "login", "000000")
        assert invalid.value.error_key == "AUTH_CODE_INVALID"
    with pytest.raises(ChallengeError) as locked:
        await service.consume(issued.challenge_id, "13800138000", "login", "123456")

    assert locked.value.error_key == "AUTH_CHALLENGE_LOCKED"


async def test_expired_challenge_is_rejected():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    issued = await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")
    store.now += timedelta(minutes=6)

    with pytest.raises(ChallengeError) as expired:
        await service.consume(issued.challenge_id, "13800138000", "login", "123456")

    assert expired.value.error_key == "AUTH_CODE_EXPIRED"


async def test_phone_send_slot_returns_retry_after():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")

    with pytest.raises(ChallengeError) as limited:
        await service.create("13800138000", "login", ip_prefix="127.0.0.0/24")

    assert limited.value.error_key == "AUTH_RATE_LIMITED"
    assert limited.value.retry_after == 60


async def test_demo_provider_is_allowlisted_and_never_runs_in_production():
    development = DemoSmsProvider(environment="development", allowlist={"13800138000"})
    receipt = await development.send("13800138000", "login", "123456")
    assert receipt.provider == "demo"
    assert receipt.demo_code == "123456"

    with pytest.raises(ProviderError) as not_allowed:
        await development.send("13900139000", "login", "123456")
    assert not_allowed.value.error_key == "SMS_DEMO_PHONE_NOT_ALLOWED"

    production = DemoSmsProvider(environment="production", allowlist={"13800138000"})
    with pytest.raises(ProviderError) as disabled:
        await production.send("13800138000", "login", "123456")
    assert disabled.value.error_key == "SMS_PROVIDER_UNAVAILABLE"


async def test_demo_provider_with_empty_allowlist_accepts_any_phone_in_development():
    open_dev = DemoSmsProvider(environment="development", allowlist=set())
    for phone in ("13800138000", "13900139000", "13712345678"):
        receipt = await open_dev.send(phone, "login", "654321")
        assert receipt.provider == "demo"
        assert receipt.demo_code == "654321"

    production = DemoSmsProvider(environment="production", allowlist=set())
    with pytest.raises(ProviderError) as disabled:
        await production.send("13800138000", "login", "123456")
    assert disabled.value.error_key == "SMS_PROVIDER_UNAVAILABLE"


@pytest.mark.parametrize(
    ("vendor_code", "expected"),
    [
        ("LimitExceeded.PhoneNumberDailyLimit", "SMS_RATE_LIMITED"),
        ("FailedOperation.SignatureIncorrectOrUnapproved", "SMS_TEMPLATE_UNAVAILABLE"),
        ("UnauthorizedOperation", "SMS_PROVIDER_AUTH_FAILED"),
        ("InternalError", "SMS_PROVIDER_TEMPORARY_FAILURE"),
        ("UnknownVendorCode", "SMS_PROVIDER_FAILURE"),
    ],
)
def test_tencent_errors_map_to_stable_keys(vendor_code, expected):
    assert map_tencent_error(vendor_code) == expected


async def test_tencent_boundary_surfaces_mapped_provider_error():
    async def failing_sender(phone: str, purpose: str, code: str):
        raise RuntimeError("LimitExceeded.PhoneNumberDailyLimit")

    provider = TencentSmsProvider(sender=failing_sender)
    with pytest.raises(ProviderError) as error:
        await provider.send("13800138000", "login", "123456")
    assert error.value.error_key == "SMS_RATE_LIMITED"


async def test_tencent_provider_sends_e164_request_with_rendered_template_parameters():
    captured: dict[str, object] = {}

    class FakeStatus:
        Code = "Ok"
        Message = "send success"

    class FakeResponse:
        RequestId = "request-123"
        SendStatusSet = [FakeStatus()]

    class FakeClient:
        def SendSms(self, request):
            captured["request"] = request
            return FakeResponse()

    def fake_client_factory(secret_id: str, secret_key: str, region: str):
        captured["credentials"] = (secret_id, secret_key, region)
        return FakeClient()

    provider = TencentSmsProvider(
        secret_id="secret-id",
        secret_key="secret-key",
        sdk_app_id="1400000000",
        sign_name="数学竞技场",
        template_id="1000000",
        region="ap-guangzhou",
        template_params=["{code}", "请勿泄露"],
        client_factory=fake_client_factory,
        request_factory=lambda payload: payload,
    )

    receipt = await provider.send("13800138000", "login", "123456")

    assert captured["credentials"] == ("secret-id", "secret-key", "ap-guangzhou")
    assert captured["request"] == {
        "SmsSdkAppId": "1400000000",
        "SignName": "数学竞技场",
        "TemplateId": "1000000",
        "PhoneNumberSet": ["+8613800138000"],
        "TemplateParamSet": ["123456", "请勿泄露"],
    }
    assert receipt.provider == "tencent"
    assert receipt.external_id == "request-123"


async def test_tencent_provider_maps_recipient_status_failure_to_stable_error():
    class FakeStatus:
        Code = "LimitExceeded.PhoneNumberDailyLimit"
        Message = "daily limit"

    class FakeResponse:
        RequestId = "request-123"
        SendStatusSet = [FakeStatus()]

    class FakeClient:
        def SendSms(self, request):
            return FakeResponse()

    provider = TencentSmsProvider(
        client_factory=lambda secret_id, secret_key, region: FakeClient(),
        request_factory=lambda payload: payload,
    )

    with pytest.raises(ProviderError) as error:
        await provider.send("13800138000", "login", "123456")

    assert error.value.error_key == "SMS_RATE_LIMITED"


async def test_sms_challenge_endpoint_returns_stable_envelope():
    store = InMemoryChallengeStore(datetime(2026, 8, 22, tzinfo=UTC))
    service = _service(store)
    app.dependency_overrides[get_challenge_service] = lambda: service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/auth/challenges/sms",
                json={"phone": "13800138000", "purpose": "login"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["expires_in"] == 300
        assert body["data"]["demo_code"] == "123456"
        assert "challenge_id" in body["data"]
    finally:
        app.dependency_overrides.pop(get_challenge_service, None)


async def test_redis_store_allows_only_one_concurrent_consume():
    redis = get_redis()
    store = RedisChallengeStore(redis)
    challenge_id = f"test-{datetime.now(UTC).timestamp()}"
    record = ChallengeRecord(
        challenge_id=challenge_id,
        phone_digest="phone-digest",
        purpose="login",
        code_digest="code-digest",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    await store.save(record)
    try:
        import asyncio

        results = await asyncio.gather(
            store.consume(challenge_id, "login", "code-digest", 5),
            store.consume(challenge_id, "login", "code-digest", 5),
        )
        assert sorted(results) == [ConsumeResult.MISSING, ConsumeResult.CONSUMED]
    finally:
        await redis.delete(f"auth:sms:challenge:{challenge_id}")
