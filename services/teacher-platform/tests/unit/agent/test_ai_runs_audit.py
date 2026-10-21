"""R11.5-B：ai_runs 台账审计列单测（hash 脱敏 / 脱敏 redaction / sink 所有权校验）。"""
from __future__ import annotations

from app.llm.ai_runs import (
    AiRunRecord,
    MemoryAiRunsSink,
    digest_payload,
    redact_error,
)


def test_digest_payload_is_deterministic_and_order_insensitive() -> None:
    a = digest_payload({"b": 1, "a": [1, 2]})
    b = digest_payload({"a": [1, 2], "b": 1})
    assert a and a == b
    assert digest_payload(None) is None
    assert a != digest_payload({"b": 1, "a": [1, 3]})


def test_redact_error_masks_secrets_and_truncates() -> None:
    err = "HTTP 401: Bearer sk-ABCDEFGH12345678 failed; api_key=secret_token_more"
    masked = redact_error(err)
    assert "sk-" not in masked and "secret_token" not in masked
    assert "<redacted>" in masked
    long_err = "x" * 1000
    assert len(redact_error(long_err)) <= 500


def test_memory_sink_records_and_by_feature() -> None:
    sink = MemoryAiRunsSink()
    rec = AiRunRecord(feature="butler", model="deepseek/deepseek-chat", input_hash="h1", output_hash="h2")
    import asyncio

    asyncio.run(sink.write(rec))
    assert sink.by_feature("butler") == [rec]
    assert sink.by_feature("deck") == []


def test_postgres_sink_rejects_missing_table() -> None:
    """fail-closed：缺表抛 RuntimeOwnershipError（不再运行期建表）。"""
    from app.db.ownership import RuntimeOwnershipError
    from app.llm.ai_runs import PostgresAiRunsSink

    class FakeSessionFactory:
        def __call__(self):
            class S:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, stmt):
                    class R:
                        def scalar(self):
                            return None  # 表不存在

                    return R()

            return S()

    sink = PostgresAiRunsSink(FakeSessionFactory())
    import pytest

    with pytest.raises(RuntimeOwnershipError):
        import asyncio

        asyncio.run(sink.write(AiRunRecord(feature="deck")))