"""RV02 · P00 共享契约一致性测试（只消费，不改共享契约）。

三条机器强制：
1. **哈希锚定**：`research-vnext` 内置一份 P00 冻结 schema 的只读副本，
   其 SHA-256 必须等于 `PLAT` 权威副本；PLAT 一改，本测试立即红（防漂移）。
2. **字段等价**：Research 侧 CommandEnvelope / DomainEventEnvelope 与 P00 权威
   Python 模型做字段名与约束等价性断言（测试期导入，运行时零耦合）。
3. **unknown 不静默放行**：未知 event_type / schema_version 必须拒绝。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

# 本文件位于 services/research-vnext/tests/contracts/，上溯 4 级到仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[4]

AUTHORITATIVE_SCHEMA = Path(
    os.getenv(
        "PLATFORM_CONTRACTS_SCHEMA_PATH",
        str(
            _REPO_ROOT
            / "services"
            / "teacher-platform"
            / "app"
            / "platform_contracts"
            / "schema"
            / "platform-contracts.schema.json"
        ),
    )
)
AUTHORITATIVE_PKG = Path(
    os.getenv(
        "PLATFORM_CONTRACTS_PKG_PATH",
        str(_REPO_ROOT / "services" / "teacher-platform"),
    )
)

VENDORED_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "research_vnext"
    / "core"
    / "contracts"
    / "schema"
    / "platform-contracts.schema.json"
)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------
# 1. 哈希锚定（fail closed：权威文件缺失即失败，不跳过）
# --------------------------------------------------------------------------


def test_authoritative_schema_present() -> None:
    assert AUTHORITATIVE_SCHEMA.is_file(), f"P00 权威 schema 不存在: {AUTHORITATIVE_SCHEMA}"


def test_vendored_schema_present() -> None:
    assert VENDORED_SCHEMA.is_file(), f"缺少内置契约副本: {VENDORED_SCHEMA}"


def test_vendored_schema_hash_matches_authoritative() -> None:
    a = _sha256(AUTHORITATIVE_SCHEMA)
    v = _sha256(VENDORED_SCHEMA)
    assert a == v, (
        "P00 共享契约已变更但 research-vnext 未同步 —— 这属于共享范围变更，"
        "须由 PLAT 走 SHARED-CHANGE-REQUEST，不得由 RES-B 自行漂移。"
    )


def test_pinned_hash_file_matches() -> None:
    pin_file = VENDORED_SCHEMA.with_suffix(".sha256")
    assert pin_file.is_file(), "缺少 .sha256 锚定文件"
    assert pin_file.read_text().strip() == _sha256(AUTHORITATIVE_SCHEMA)


# --------------------------------------------------------------------------
# 2. 字段等价（测试期导入权威包）
# --------------------------------------------------------------------------


def _load_authoritative_models():
    import sys

    if not (AUTHORITATIVE_PKG / "app" / "platform_contracts").is_dir():
        pytest.fail(f"P00 权威契约包不存在: {AUTHORITATIVE_PKG}")
    if str(AUTHORITATIVE_PKG) not in sys.path:
        sys.path.insert(0, str(AUTHORITATIVE_PKG))
    from app.platform_contracts.command import CommandEnvelope  # noqa: PLC0415
    from app.platform_contracts.event_envelope import (  # noqa: PLC0415
        DomainEventEnvelope,
        SSECursor,
    )

    return CommandEnvelope, DomainEventEnvelope, SSECursor


def test_command_envelope_field_parity() -> None:
    CmdEv, _, _ = _load_authoritative_models()
    from research_vnext.core.contracts.command import ResearchCommandEnvelope

    authoritative = set(CmdEv.model_fields)
    mine = set(ResearchCommandEnvelope.model_fields)
    missing = authoritative - mine
    assert not missing, f"Research CommandEnvelope 缺少 P00 字段: {sorted(missing)}"


def test_event_envelope_field_parity() -> None:
    _, EvEnv, _ = _load_authoritative_models()
    from research_vnext.core.contracts.events import ResearchDomainEventEnvelope

    authoritative = set(EvEnv.model_fields)
    mine = set(ResearchDomainEventEnvelope.model_fields)
    missing = authoritative - mine
    assert not missing, f"Research DomainEventEnvelope 缺少 P00 字段: {sorted(missing)}"


def test_sse_cursor_fallback_enum_is_two_valued() -> None:
    """P00 硬约束：fallback 只允许 NONE / SNAPSHOT_REQUIRED。"""
    _, _, Cursor = _load_authoritative_models()
    from research_vnext.core.contracts.events import ResearchSSECursor

    a = Cursor.model_fields["fallback"].annotation
    b = ResearchSSECursor.model_fields["fallback"].annotation
    assert set(a.__args__) == {"NONE", "SNAPSHOT_REQUIRED"}
    assert set(b.__args__) == {"NONE", "SNAPSHOT_REQUIRED"}
    assert "ASSUME_CAUGHT_UP" not in set(b.__args__)


def test_research_command_validates_against_authoritative_schema() -> None:
    from research_vnext.core.contracts.command import ResearchCommandEnvelope

    schema = json.loads(AUTHORITATIVE_SCHEMA.read_text(encoding="utf-8"))
    defs = schema.get("$defs", schema.get("definitions", {}))
    assert "CommandEnvelope" in defs, "权威 schema 缺少 CommandEnvelope 定义"
    spec = dict(defs["CommandEnvelope"])
    spec["$defs"] = defs
    spec["definitions"] = defs

    import jsonschema  # type: ignore

    env = ResearchCommandEnvelope(
        client_request_id="crq-abcdefgh1234",
        expected_revision=0,
        actor_id="11111111-1111-4111-8111-111111111111",
        scope={"kind": "session", "id": "22222222-2222-4222-8222-222222222222", "role": "owner"},
        traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        deadline="2030-01-01T00:00:00Z",
        idempotency_fingerprint="c" * 64,
        command_type="session.create",
        payload_ref="inline:abc",
        tenant_id="33333333-3333-4333-8333-333333333333",
    )
    jsonschema.validate(env.model_dump(mode="json"), spec)
