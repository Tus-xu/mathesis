"""WP-A FIX-A1 · 服务端权威摘要的**规范式**单元测试。

摘要必须只依赖语义内容，而不是字典插入顺序、空白或 `repr()` 的实现细节。
否则"同一命令的两次重试"会算出不同摘要 → 合法重试被误判为冲突。
"""

from __future__ import annotations

import json

from research_vnext.core.application.session_service import (
    DIGEST_FIELDS,
    canonical_command_digest,
)
from research_vnext.core.contracts.command import IncomingCommand

FP = "c" * 64


def _cmd(command_type: str, payload: dict, expected_revision: int = 0) -> IncomingCommand:
    """`IncomingCommand` 允许 extra 字段，因此可以夹带域专属 `payload`。"""
    return IncomingCommand(
        client_request_id="crq-digest-0001",
        expected_revision=expected_revision,
        actor_id="11111111-1111-4111-8111-111111111111",
        scope={"kind": "session", "id": "22222222-2222-4222-8222-222222222222", "role": "owner"},
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        deadline="2030-01-01T00:00:00Z",
        idempotency_fingerprint=FP,
        command_type=command_type,
        payload_ref="inline:x",
        payload=payload,
    )


def test_key_order_does_not_change_digest() -> None:
    """键序不同、语义相同 → 必须产出**同一**摘要（规范式是真实的，不是碰巧）。"""
    a = {"to": "ACTIVE", "reason": "go", "tags": ["x", "y"], "nested": {"b": 2, "a": 1}}
    b = {"nested": {"a": 1, "b": 2}, "tags": ["x", "y"], "reason": "go", "to": "ACTIVE"}
    assert json.dumps(a) != json.dumps(b), "前提：原始 json 序列化确实不同（键序不同）"
    assert canonical_command_digest(_cmd("session.transition", a)) == canonical_command_digest(
        _cmd("session.transition", b)
    )


def test_nested_key_order_does_not_change_digest() -> None:
    """嵌套字典的键序同样不得影响摘要（`sort_keys=True` 必须递归生效）。"""
    a = {"outer": {"z": 1, "a": {"m": 2, "b": 3}}}
    b = {"outer": {"a": {"b": 3, "m": 2}, "z": 1}}
    assert canonical_command_digest(_cmd("session.transition", a)) == canonical_command_digest(
        _cmd("session.transition", b)
    )


def test_different_payload_changes_digest() -> None:
    assert canonical_command_digest(_cmd("session.transition", {"to": "ACTIVE"})) != (
        canonical_command_digest(_cmd("session.transition", {"to": "CANCELLED"}))
    )


def test_different_command_type_changes_digest() -> None:
    p = {"to": "ACTIVE"}
    assert canonical_command_digest(_cmd("session.transition", p)) != (
        canonical_command_digest(_cmd("session.transition.v2", p))
    )


def test_expected_revision_is_part_of_digest() -> None:
    """`expected_revision` 参与摘要：合法重试必然重发同一 revision，故不会误判。"""
    p = {"to": "ACTIVE"}
    assert canonical_command_digest(_cmd("session.transition", p, 0)) != (
        canonical_command_digest(_cmd("session.transition", p, 1))
    )


def test_infra_fields_do_not_change_digest() -> None:
    """基础设施字段（客户端 fingerprint / payload_ref / traceparent / deadline）**不得**影响摘要。

    否则"同一意图的两次重试"（deadline 每次重算、payload_ref 可能变化）
    会被误判成冲突 —— 那比漏检更糟。
    """
    p = {"to": "ACTIVE"}
    base = _cmd("session.transition", p)

    other_fp = IncomingCommand(
        client_request_id="crq-digest-0001",
        expected_revision=0,
        actor_id="11111111-1111-4111-8111-111111111111",
        scope={"kind": "session", "id": "22222222-2222-4222-8222-222222222222", "role": "owner"},
        traceparent="00-" + "9" * 32 + "-" + "8" * 16 + "-01",
        deadline="2031-06-06T00:00:00Z",
        idempotency_fingerprint="d" * 64,
        command_type="session.transition",
        payload_ref="inline:completely-different-ref",
        payload=p,
    )

    assert canonical_command_digest(base) == canonical_command_digest(other_fp)

    # 显式断言：payload_ref 不在参与字段清单中（有意偏离评审建议，见 ADR-RV-002 §2.3）
    assert "payload_ref" not in DIGEST_FIELDS
    assert "idempotency_fingerprint" not in DIGEST_FIELDS
    assert set(DIGEST_FIELDS) == {"command_type", "payload", "expected_revision"}


def test_digest_is_stable_across_calls_and_utf8() -> None:
    """同一输入重复调用结果一致；非 ASCII 内容不得因转义而改变语义。"""
    p = {"note": "取消会话", "emoji": "✅"}
    first = canonical_command_digest(_cmd("session.transition", p))
    second = canonical_command_digest(_cmd("session.transition", p))
    assert first == second
    assert len(first) == 64 and all(c in "0123456789abcdef" for c in first)

    # 键序颠倒的中文键同样等价
    assert first == canonical_command_digest(_cmd("session.transition", {"emoji": "✅", "note": "取消会话"}))


def test_digest_does_not_rely_on_repr_or_hash() -> None:
    """两个内容相同但对象不同的 dict 必须同摘要（`repr()` 含内存地址时不会成立）。"""
    a = {"k": [1, 2, {"z": 3}]}
    b = json.loads(json.dumps(a))
    assert a is not b
    assert canonical_command_digest(_cmd("session.transition", a)) == canonical_command_digest(
        _cmd("session.transition", b)
    )
