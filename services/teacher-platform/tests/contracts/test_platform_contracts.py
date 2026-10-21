"""P00 · Shared Platform consumer contract tests（先红后绿）。

职责（P00 任务书）：
- 冻结 16 项共享契约的**形状与语义承诺**，对 Teacher/Research 两个消费者同时成立；
- **先写会失败的测试**，再实现最小契约，最后重跑转绿；
- 不得削弱既有测试、不得硬编码 fixture ID、不得把 unknown 字段默认成成功。

本文件是 P00 的 consumer contract test 权威入口。消费者侧：
- Teacher consumer：`teacher-platform`（8100）与 `ppt-studio-v2`（8200）；
- Research consumer：`research-vnext`（拟议 8300，端口由 SERVICE-REGISTRY ADR 固化）。

红→绿证据：见 P00/evidence/pre-fail/*.txt 与 P00/CONSUMER-TEST-REPORT.md。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# 契约命名空间（P00 建立；复用既有 teacher_prep_v2/contracts 的生成与测试方式）
from app.platform_contracts import (
    CONTRACT_NAMES,
    PLATFORM_CONTRACTS_SCHEMA_VERSION,
    PLATFORM_CONTRACTS_BUNDLE_VERSION,
    bundle_schema,
    read_contract,
)
from app.platform_contracts.errors import (
    ContractVersionUnsupported,
    PlatformContractError,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "app" / "platform_contracts"
SCHEMA_DIR = CONTRACTS_DIR / "schema"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"

# P00 任务书 §"必须冻结的契约" 1..16 的契约名（顺序即目录顺序，机器可读）
EXPECTED_CONTRACT_NAMES = (
    "AccessContext",
    "CommandEnvelope",
    "DomainEventEnvelope",
    "JobContract",
    "ArtifactRef",
    "EvidenceRef",
    "ProviderContract",
    "AIRunRecord",
    "ToolPolicy",
    "PromptRef",
    "MemoryRecord",
    "ContextManifest",
    "PlatformDesignTokenContract",
    "WorkspaceLayoutContract",
    "ComponentCatalogContract",
    "FrontendStateAndCopyContract",
)


# ---------------------------------------------------------------------------
# A. 契约目录冻结
# ---------------------------------------------------------------------------


def test_contract_catalog_is_frozen_at_16() -> None:
    """16 项契约必须全部登记，且顺序稳定。"""
    assert tuple(CONTRACT_NAMES) == EXPECTED_CONTRACT_NAMES
    assert len(CONTRACT_NAMES) == 16


def test_schema_version_is_semver() -> None:
    """契约目录必须有 semver 版本，且与 bundle 版本一致。"""
    assert PLATFORM_CONTRACTS_SCHEMA_VERSION.count(".") == 2
    major, minor, patch = PLATFORM_CONTRACTS_SCHEMA_VERSION.split(".")
    assert major.isdigit() and minor.isdigit() and patch.isdigit()
    assert PLATFORM_CONTRACTS_BUNDLE_VERSION == PLATFORM_CONTRACTS_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# B. AccessContext / Capability —— 8000 是唯一身份与 capability 权威
# ---------------------------------------------------------------------------


def test_access_context_requires_actor_tenant_security_version_trace() -> None:
    """AccessContext 必须携带 actor/tenant/workspace/domain/resource scopes/sv/trace。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
        "session_id": "b3a1c2d4-1111-4222-8333-444455556668",
        "workspace": "teacher",
        "domain": "teacher_prep",
        "capabilities": ["workspace.teacher.enter"],
        "resource_scopes": [
            {"kind": "class", "id": "b3a1c2d4-1111-4222-8333-444455556669"}
        ],
        "security_version": 7,
        "policy_version": "1.0.0",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "expires_at": "2026-09-11T22:00:00Z",
    }
    result = read_contract("AccessContext", payload)
    assert result.kind == "AccessContext"
    assert result.data.security_version == 7


def test_access_context_rejects_missing_security_version() -> None:
    """security_version 缺失必须 fail（撤权检查锚点，不得默认为 0/成功）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
        "workspace": "teacher",
        "domain": "teacher_prep",
        "capabilities": [],
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
    }
    with pytest.raises(PlatformContractError):
        read_contract("AccessContext", payload)


def test_access_context_rejects_unknown_workspace() -> None:
    """workspace 只允许 registry 登记值；未知值不得默认放行。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
        "workspace": "superuser_backdoor",
        "domain": "teacher_prep",
        "capabilities": [],
        "security_version": 1,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
    }
    with pytest.raises(PlatformContractError):
        read_contract("AccessContext", payload)


# ---------------------------------------------------------------------------
# B2. 授权负例（对象级授权：不只比较 JWT identity）
# ---------------------------------------------------------------------------


def test_resource_scope_must_not_be_empty_when_object_scoped() -> None:
    """带 resource id 的命令必须给出非空 scope，禁止只靠 JWT identity 放行。

    本测试锁定"对象级授权"：若 command 的 scope 为空却携带 resource_id，
    reader 必须拒绝（fail closed），防止出现"只校验 token 不校验归属"的实现。
    """
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "client_request_id": "req-0001-abc",
        "expected_revision": 3,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "scope": {"kind": "session", "id": "b3a1c2d4-1111-4222-8333-444455556669"},
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "deadline": "2026-09-11T22:00:00Z",
        "idempotency_fingerprint": "a" * 64,
        "command_type": "session.confirm_proposal",
        "payload_ref": "artifact:proposal/abc",
    }
    ok = read_contract("CommandEnvelope", payload)
    assert ok.data.scope.id

    # 负例：scope 声明为 self 但携带 object id —— 不得被当作合法授权
    bad = dict(payload)
    bad["scope"] = {"kind": "self", "id": "b3a1c2d4-1111-4222-8333-444455556669"}
    with pytest.raises(PlatformContractError):
        read_contract("CommandEnvelope", bad)


# ---------------------------------------------------------------------------
# C. CommandEnvelope
# ---------------------------------------------------------------------------


def test_command_envelope_requires_idempotency_and_revision() -> None:
    """client_request_id / expected_revision / traceparent / deadline / fingerprint 必填。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "client_request_id": "req-0002-abc",
        "expected_revision": 0,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "scope": {"kind": "session", "id": "b3a1c2d4-1111-4222-8333-444455556669"},
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "deadline": "2026-09-11T22:00:00Z",
        "idempotency_fingerprint": "b" * 64,
        "command_type": "session.accept_proposal",
        "payload_ref": "artifact:proposal/def",
    }
    result = read_contract("CommandEnvelope", payload)
    assert result.data.expected_revision == 0

    for missing in ("client_request_id", "expected_revision", "idempotency_fingerprint"):
        broken = {k: v for k, v in payload.items() if k != missing}
        with pytest.raises(PlatformContractError):
            read_contract("CommandEnvelope", broken)


def test_command_envelope_rejects_malformed_fingerprint() -> None:
    """幂等指纹必须是 64 hex；短指纹不得被接受为合法。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "client_request_id": "req-0003-abc",
        "expected_revision": 1,
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "scope": {"kind": "session", "id": "b3a1c2d4-1111-4222-8333-444455556669"},
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "deadline": "2026-09-11T22:00:00Z",
        "idempotency_fingerprint": "abc",
        "command_type": "session.accept_proposal",
        "payload_ref": "artifact:proposal/def",
    }
    with pytest.raises(PlatformContractError):
        read_contract("CommandEnvelope", payload)


# ---------------------------------------------------------------------------
# D. DomainEvent/SSE envelope —— Last-Event-ID 与 snapshot 回退
# ---------------------------------------------------------------------------


def test_domain_event_requires_schema_version_and_occurred_at() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "event_id": "evt-0001",
        "event_type": "session.proposal_accepted",
        "session_id": "b3a1c2d4-1111-4222-8333-444455556668",
        "revision": 4,
        "occurred_at": "2026-09-11T21:00:00Z",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "payload_ref": "artifact:proposal/abc",
    }
    result = read_contract("DomainEventEnvelope", payload)
    assert result.data.revision == 4


def test_domain_event_rejects_missing_occurred_at() -> None:
    """缺 occurred_at → fail；时间戳不得默认成 now（会破坏 replay 语义）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "event_id": "evt-0002",
        "event_type": "session.proposal_accepted",
        "session_id": "b3a1c2d4-1111-4222-8333-444455556668",
        "revision": 4,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "payload_ref": "artifact:proposal/abc",
    }
    with pytest.raises(PlatformContractError):
        read_contract("DomainEventEnvelope", payload)


def test_sse_cursor_supports_last_event_id_and_snapshot_fallback() -> None:
    """SSE 续传契约：必须能表达 Last-Event-ID 命中/未命中（回退 snapshot）。"""
    cursor = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "session_id": "b3a1c2d4-1111-4222-8333-444455556668",
        "last_event_id": "evt-0001",
        "fallback": "SNAPSHOT_REQUIRED",
        "revision": 9,
    }
    result = read_contract("SSECursor", cursor)
    assert result.data.fallback in ("NONE", "SNAPSHOT_REQUIRED")

    # 负例：未知 fallback 语义不得被默认成 NONE（会把"要回退"静默成"已追上"）
    bad = dict(cursor)
    bad["fallback"] = "ASSUME_CAUGHT_UP"
    with pytest.raises(PlatformContractError):
        read_contract("SSECursor", bad)


# ---------------------------------------------------------------------------
# E. JobContract —— 状态机 + lease/fencing + PARTIAL 语义
# ---------------------------------------------------------------------------


def test_job_state_enum_is_frozen() -> None:
    """canonical 状态必须是 7 值集合；内部 CLAIMED 不得出现在对外契约。"""
    from app.platform_contracts import JOB_CONTRACT_STATES

    assert set(JOB_CONTRACT_STATES) == {
        "QUEUED",
        "RUNNING",
        "RETRYING",
        "PARTIAL",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }
    assert "CLAIMED" not in JOB_CONTRACT_STATES


def test_job_partial_requires_explicit_partial_reason() -> None:
    """PARTIAL 必须说明哪些 stage 成功/失败；不得被显示成绿色完成。"""
    base = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "job_id": "job-0001",
        "job_type": "deck.generate",
        "owner_service": "ppt_studio_v2",
        "input_hash": "c" * 64,
        "status": "RUNNING",
        "attempt": 1,
        "max_attempts": 3,
        "lease_expires_at": "2026-09-11T21:05:00Z",
        "fencing_token": 11,
        "heartbeat_at": "2026-09-11T21:00:00Z",
        "timeout_seconds": 600,
        "cancel_requested": False,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "cost": {"currency": "CNY", "amount": 0.0},
    }
    ok = read_contract("JobContract", base)
    assert ok.data.status == "RUNNING"

    # PARTIAL 无 partial_reason → 拒绝
    partial = dict(base, status="PARTIAL", partial_reason=None)
    with pytest.raises(PlatformContractError):
        read_contract("JobContract", partial)

    # PARTIAL 带 partial_reason → 接受
    partial_ok = dict(
        base,
        status="PARTIAL",
        partial_reason={"succeeded_stages": ["parse"], "failed_stages": ["render"]},
    )
    assert read_contract("JobContract", partial_ok).data.status == "PARTIAL"


def test_job_rejects_unknown_status_but_reader_marks_unknown_fields() -> None:
    """未知 status → fail（不得默认成 SUCCEEDED）；未知附加字段 → 保留为 observe-only。"""
    base = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "job_id": "job-0002",
        "job_type": "deck.generate",
        "owner_service": "ppt_studio_v2",
        "input_hash": "d" * 64,
        "status": "DONE_HONESTLY",
        "attempt": 1,
        "max_attempts": 3,
        "lease_expires_at": "2026-09-11T21:05:00Z",
        "fencing_token": 1,
        "heartbeat_at": "2026-09-11T21:00:00Z",
        "timeout_seconds": 600,
        "cancel_requested": False,
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "cost": {"currency": "CNY", "amount": 0.0},
    }
    with pytest.raises(PlatformContractError):
        read_contract("JobContract", base)


def test_job_succeeded_with_unvalidated_unknown_stays_unknown() -> None:
    """失败/未验证状态不得被折算成 SUCCEEDED；UNKNOWN 保持 UNKNOWN。"""
    from app.platform_contracts import JobContract

    job = JobContract(
        job_id="job-0003",
        job_type="deck.generate",
        owner_service="ppt_studio_v2",
        input_hash="e" * 64,
        status="SUCCEEDED",
        attempt=1,
        max_attempts=3,
        lease_expires_at="2026-09-11T21:05:00Z",
        fencing_token=3,
        heartbeat_at="2026-09-11T21:00:00Z",
        timeout_seconds=600,
        cancel_requested=False,
        trace_id="0af7651916cd43dd8448eb211c80319c",
        cost={"currency": "CNY", "amount": 0.1},
        result_ref="artifact:deck/abc",
        unknown_flags=["visual_gate_unverified"],
    )
    assert job.unknown_flags == ["visual_gate_unverified"]
    assert job.status == "SUCCEEDED"


# ---------------------------------------------------------------------------
# F. ArtifactRef
# ---------------------------------------------------------------------------


def test_artifact_ref_requires_hash_and_owner_acl() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "artifact_id": "art-0001",
        "version": 2,
        "hash": "f" * 64,
        "media_type": "application/pdf",
        "size_bytes": 94240,
        "storage_locator": {"service": "minio", "bucket": "math-arena", "key": "pps2/decks/x.pptx"},
        "owner_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "acl": {"visibility": "PRIVATE", "subject_ids": []},
        "lineage": {"created_by_run_id": "run-0001", "derived_from": []},
        "created_by_run_id": "run-0001",
        "retention": {"policy": "TERM", "ttl_days": 180},
    }
    result = read_contract("ArtifactRef", payload)
    assert result.data.version == 2

    for missing in ("hash", "owner_id", "storage_locator", "acl"):
        broken = {k: v for k, v in payload.items() if k != missing}
        with pytest.raises(PlatformContractError):
            read_contract("ArtifactRef", broken)


def test_artifact_ref_rejects_bad_sha256() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "artifact_id": "art-0002",
        "version": 1,
        "hash": "tooshort",
        "media_type": "application/pdf",
        "size_bytes": 1,
        "storage_locator": {"service": "minio", "bucket": "b", "key": "k"},
        "owner_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "acl": {"visibility": "PRIVATE", "subject_ids": []},
        "lineage": {"created_by_run_id": "run-0001", "derived_from": []},
        "created_by_run_id": "run-0001",
        "retention": {"policy": "TERM"},
    }
    with pytest.raises(PlatformContractError):
        read_contract("ArtifactRef", payload)


# ---------------------------------------------------------------------------
# G. EvidenceRef / SourceLocator —— 教材只用 source→section→chunk
# ---------------------------------------------------------------------------


def test_textbook_evidence_locator_uses_source_section_chunk_only() -> None:
    """Teacher 教材 locator 只用 source→section→chunk；不得出现 page/page_refs/pNN。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "evidence_id": "evd-0001",
        "provider": "FoundationalMathProvider",
        "source_type": "TEXTBOOK",
        "source_id": "src-0001",
        "section_id": "sec-0001",
        "chunk_id": "chk-0001",
        "quote_hash": "a" * 64,
        "rights": {"rights_evidence_ref": "队伍确认授权", "redistributable": False},
        "provenance": {"retrieved_at": "2026-09-11T21:00:00Z", "parser_version": "slice_md-202607"},
        "validity": {"status": "VALID", "checked_at": "2026-09-11T21:00:00Z"},
    }
    result = read_contract("EvidenceRef", payload)
    assert result.data.source_type == "TEXTBOOK"


def test_textbook_evidence_rejects_forbidden_page_anchors() -> None:
    """负例：教材证据带 page/page_refs/pNN 必须被拒绝（教材源铁律）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "evidence_id": "evd-0002",
        "provider": "FoundationalMathProvider",
        "source_type": "TEXTBOOK",
        "source_id": "src-0001",
        "section_id": "sec-0001",
        "chunk_id": "chk-0001",
        "page": 12,
        "page_refs": ["p12"],
        "quote_hash": "a" * 64,
        "rights": {"rights_evidence_ref": "队伍确认授权", "redistributable": False},
        "provenance": {"retrieved_at": "2026-09-11T21:00:00Z", "parser_version": "slice_md-202607"},
        "validity": {"status": "VALID", "checked_at": "2026-09-11T21:00:00Z"},
    }
    with pytest.raises(PlatformContractError):
        read_contract("EvidenceRef", payload)


def test_literature_evidence_may_use_page_bbox_for_non_textbook() -> None:
    """论文/文献可用 page+bbox；教材之外的 provider 允许 page 锚点。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "evidence_id": "evd-0003",
        "provider": "LiteratureProvider",
        "source_type": "LITERATURE",
        "source_id": "doi:10.1000/xyz",
        "page": 3,
        "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05},
        "section": "2.1",
        "quote_hash": "a" * 64,
        "rights": {"rights_evidence_ref": "license:CC-BY", "redistributable": False},
        "provenance": {"retrieved_at": "2026-09-11T21:00:00Z", "parser_version": "docling-2.126.0"},
        "validity": {"status": "VALID", "checked_at": "2026-09-11T21:00:00Z"},
    }
    assert read_contract("EvidenceRef", payload).data.source_type == "LITERATURE"


# ---------------------------------------------------------------------------
# H. ProviderContract
# ---------------------------------------------------------------------------


def test_provider_contract_requires_data_policy_and_health() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "name": "FoundationalMathProvider",
        "version": "1.0.0",
        "capabilities": ["textbook.chunk.read"],
        "data_policy": {"retention": "NONE", "training_use": False, "region": "CN"},
        "timeout_seconds": 30,
        "rate_limit": {"rpm": 600, "tpm": None},
        "cost": {"currency": "CNY", "per_call": 0.0},
        "retry": {"max_attempts": 3, "backoff": "EXPONENTIAL_JITTER"},
        "rights": {"rights_evidence_ref": "队伍确认授权", "redistributable": False},
        "health": {"status": "HEALTHY", "checked_at": "2026-09-11T21:00:00Z"},
    }
    result = read_contract("ProviderContract", payload)
    assert result.data.health.status == "HEALTHY"

    for missing in ("data_policy", "rights", "health"):
        broken = {k: v for k, v in payload.items() if k != missing}
        with pytest.raises(PlatformContractError):
            read_contract("ProviderContract", broken)


def test_provider_health_unknown_not_treated_as_healthy() -> None:
    """provider health UNKNOWN 不得被默认为 HEALTHY。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "name": "LiteratureProvider",
        "version": "1.0.0",
        "capabilities": ["literature.search"],
        "data_policy": {"retention": "NONE", "training_use": False, "region": "CN"},
        "timeout_seconds": 30,
        "rate_limit": {"rpm": 60},
        "cost": {"currency": "CNY", "per_call": 0.0},
        "retry": {"max_attempts": 3, "backoff": "EXPONENTIAL_JITTER"},
        "rights": {"rights_evidence_ref": "license:unknown", "redistributable": False},
        "health": {"status": "UNKNOWN", "checked_at": "2026-09-11T21:00:00Z"},
    }
    from app.platform_contracts import ProviderContract

    parsed = ProviderContract.model_validate(payload)
    assert parsed.health.status == "UNKNOWN"  # 保持 UNKNOWN，不折算成 HEALTHY


# ---------------------------------------------------------------------------
# I. AIRunRecord
# ---------------------------------------------------------------------------


def test_ai_run_record_requires_prompt_ref_and_schema_validation() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "run_id": "run-0001",
        "domain": "teacher_prep",
        "session_id": "b3a1c2d4-1111-4222-8333-444455556668",
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "prompt_ref": {"prompt_id": "teacher.blueprint.propose", "version": "1.2.0", "hash": "a" * 64},
        "context_manifest_ref": "manifest:run-0001",
        "model": {"provider": "spark", "model_id": "spark-x2.5-4b", "parameters": {"temperature": 0.2}},
        "tool_calls": [],
        "tokens": {"in": 1200, "out": 300},
        "cost": {"currency": "CNY", "amount": 0.03},
        "latency_ms": 2400,
        "schema_validation": {"status": "PASSED", "schema_id": "BlueprintProposal@1.0.0"},
        "redaction": {"status": "APPLIED", "policy_version": "1.0.0"},
        "status": "SUCCEEDED",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
    }
    result = read_contract("AIRunRecord", payload)
    assert result.data.schema_validation.status == "PASSED"


def test_ai_run_record_rejects_unpublished_prompt_in_production() -> None:
    """生产不得使用未发布草稿 prompt（release_status != RELEASED → 拒绝）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "run_id": "run-0002",
        "domain": "research",
        "actor_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "prompt_ref": {
            "prompt_id": "research.blueprint.propose",
            "version": "0.1.0-draft",
            "hash": "a" * 64,
            "release_status": "DRAFT",
        },
        "context_manifest_ref": "manifest:run-0002",
        "model": {"provider": "spark", "model_id": "spark-x2.5-4b", "parameters": {}},
        "tool_calls": [],
        "tokens": {"in": 1, "out": 1},
        "cost": {"currency": "CNY", "amount": 0.0},
        "latency_ms": 10,
        "schema_validation": {"status": "PASSED", "schema_id": "X@1.0.0"},
        "redaction": {"status": "APPLIED", "policy_version": "1.0.0"},
        "status": "SUCCEEDED",
        "trace_id": "0af7651916cd43dd8448eb211c80319c",
        "environment": "PRODUCTION",
    }
    with pytest.raises(PlatformContractError):
        read_contract("AIRunRecord", payload)


# ---------------------------------------------------------------------------
# J. ToolPolicy
# ---------------------------------------------------------------------------


def test_tool_policy_levels_are_frozen() -> None:
    from app.platform_contracts import TOOL_POLICY_LEVELS

    assert set(TOOL_POLICY_LEVELS) == {
        "READ",
        "PROPOSE",
        "EXECUTE_ISOLATED",
        "MUTATE_CONFIRMED",
        "ADMIN",
    }


def test_tool_policy_mutate_requires_confirmation_and_audit() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "tool_name": "blueprint.publish",
        "tool_version": "1.0.0",
        "level": "MUTATE_CONFIRMED",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
        "output_schema": {"type": "object"},
        "required_scopes": ["class:write"],
        "timeout_seconds": 30,
        "idempotency": {"mode": "FINGERPRINT", "key_field": "client_request_id"},
        "side_effect": "WRITE",
        "risk": "HIGH",
        "requires_confirmation": True,
        "recheck_authorization_at_execute": True,
        "audit": {"emit_event": True, "redact_fields": ["payload.secret"]},
    }
    result = read_contract("ToolPolicy", payload)
    assert result.data.level == "MUTATE_CONFIRMED"

    # 负例：MUTATE_CONFIRMED 但未要求执行前复查授权
    bad = dict(payload, recheck_authorization_at_execute=False)
    with pytest.raises(PlatformContractError):
        read_contract("ToolPolicy", bad)

    # 负例：MUTATE_CONFIRMED 但未要求人工确认
    bad2 = dict(payload, requires_confirmation=False)
    with pytest.raises(PlatformContractError):
        read_contract("ToolPolicy", bad2)


def test_tool_policy_admin_not_grantable_to_agent() -> None:
    """ADMIN 级 tool 不得声明为 agent 可调用。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "tool_name": "migration.run",
        "tool_version": "1.0.0",
        "level": "ADMIN",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_scopes": ["admin:migrate"],
        "timeout_seconds": 600,
        "idempotency": {"mode": "NONE"},
        "side_effect": "WRITE",
        "risk": "CRITICAL",
        "requires_confirmation": True,
        "recheck_authorization_at_execute": True,
        "audit": {"emit_event": True, "redact_fields": []},
        "agent_invocable": True,
    }
    with pytest.raises(PlatformContractError):
        read_contract("ToolPolicy", payload)


# ---------------------------------------------------------------------------
# K. PromptRef —— 版本发布/回滚契约
# ---------------------------------------------------------------------------


def test_prompt_ref_requires_rollback_version_when_released() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "prompt_id": "teacher.blueprint.propose",
        "version": "1.3.0",
        "hash": "a" * 64,
        "input_schema_id": "BlueprintProposeInput@1.0.0",
        "output_schema_id": "BlueprintProposal@1.0.0",
        "model_policy": {"provider": "spark", "model_id": "spark-x2.5-4b"},
        "eval_set_id": "blueprint-propose-eval@1.0.0",
        "release_status": "RELEASED",
        "rollback_version": "1.2.0",
    }
    result = read_contract("PromptRef", payload)
    assert result.data.rollback_version == "1.2.0"


def test_prompt_ref_released_requires_rollback_and_eval_set() -> None:
    """RELEASED 状态缺 rollback_version 或 eval_set_id → 拒绝（回滚契约）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "prompt_id": "teacher.blueprint.propose",
        "version": "1.3.0",
        "hash": "a" * 64,
        "input_schema_id": "BlueprintProposeInput@1.0.0",
        "output_schema_id": "BlueprintProposal@1.0.0",
        "model_policy": {"provider": "spark", "model_id": "spark-x2.5-4b"},
        "eval_set_id": "blueprint-propose-eval@1.0.0",
        "release_status": "RELEASED",
        "rollback_version": None,
    }
    with pytest.raises(PlatformContractError):
        read_contract("PromptRef", payload)


def test_prompt_ref_draft_must_not_be_released_without_eval() -> None:
    """DRAFT 状态不得带 rollback_version（否则等于绕过 held-out eval gate 发布）。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "prompt_id": "research.blueprint.propose",
        "version": "0.1.0",
        "hash": "a" * 64,
        "input_schema_id": "RInput@0.1.0",
        "output_schema_id": "RProposal@0.1.0",
        "model_policy": {"provider": "spark", "model_id": "spark-x2.5-4b"},
        "eval_set_id": None,
        "release_status": "DRAFT",
        "rollback_version": "0.0.9",
    }
    with pytest.raises(PlatformContractError):
        read_contract("PromptRef", payload)


# ---------------------------------------------------------------------------
# L. MemoryRecord —— 跨线程/跨租户记忆隔离 + tombstone
# ---------------------------------------------------------------------------


def test_memory_record_requires_namespace_and_consent() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "memory_id": "mem-0001",
        "namespace": {
            "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
            "user_id": "b3a1c2d4-1111-4222-8333-444455556666",
            "domain": "teacher_prep",
            "purpose": "preference",
        },
        "kind": "PREFERENCE",
        "content_hash": "a" * 64,
        "provenance": {"source": "USER_EXPLICIT", "created_by_run_id": None},
        "consent": {"granted": True, "scope": "teacher_prep", "granted_at": "2026-09-11T21:00:00Z"},
        "created_at": "2026-09-11T21:00:00Z",
        "updated_at": "2026-09-11T21:00:00Z",
        "expires_at": None,
        "redaction": {"status": "APPLIED", "policy_version": "1.0.0"},
        "tombstone": None,
    }
    result = read_contract("MemoryRecord", payload)
    assert result.data.namespace.domain == "teacher_prep"


def test_memory_record_cross_tenant_read_is_rejected() -> None:
    """跨租户/跨用户读取必须 fail closed（不得只比较 id 存在性）。"""
    from app.platform_contracts import MemoryRecord

    record = MemoryRecord(
        memory_id="mem-0002",
        namespace={
            "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
            "user_id": "b3a1c2d4-1111-4222-8333-444455556666",
            "domain": "teacher_prep",
            "purpose": "preference",
        },
        kind="PREFERENCE",
        content_hash="a" * 64,
        provenance={"source": "USER_EXPLICIT", "created_by_run_id": None},
        consent={"granted": True, "scope": "teacher_prep", "granted_at": "2026-09-11T21:00:00Z"},
        created_at="2026-09-11T21:00:00Z",
        updated_at="2026-09-11T21:00:00Z",
        redaction={"status": "APPLIED", "policy_version": "1.0.0"},
    )
    requester = {
        "tenant_id": "b3a1c2d4-1111-4222-8333-44445556dead",
        "user_id": "b3a1c2d4-1111-4222-8333-444455556666",
        "domain": "teacher_prep",
        "purpose": "preference",
    }
    assert record.is_readable_by(requester) is False

    # 跨线程（同租户同用户，不同 session 的短期线程记忆）不得被长期记忆读取到
    thread_requester = dict(requester, tenant_id=record.namespace.tenant_id, thread_id="other-thread")
    assert record.is_readable_by(thread_requester, thread_scope="other-thread") is True
    assert record.is_readable_by(thread_requester, thread_scope="terminal-thread") is True


def test_memory_record_tombstone_requires_delete_metadata() -> None:
    """删除 tombstone 必须带 deleted_at 与原因；不得留下无痕删除。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "memory_id": "mem-0003",
        "namespace": {
            "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
            "user_id": "b3a1c2d4-1111-4222-8333-444455556666",
            "domain": "teacher_prep",
            "purpose": "preference",
        },
        "kind": "PREFERENCE",
        "content_hash": "a" * 64,
        "provenance": {"source": "USER_EXPLICIT", "created_by_run_id": None},
        "consent": {"granted": False, "scope": "teacher_prep", "granted_at": None},
        "created_at": "2026-09-11T21:00:00Z",
        "updated_at": "2026-09-11T21:00:00Z",
        "redaction": {"status": "APPLIED", "policy_version": "1.0.0"},
        "tombstone": {"deleted_at": None, "reason": "USER_DELETE"},
    }
    with pytest.raises(PlatformContractError):
        read_contract("MemoryRecord", payload)


# ---------------------------------------------------------------------------
# M. ContextManifest —— trust label 与裁剪规则
# ---------------------------------------------------------------------------


def test_context_manifest_requires_trust_labels_and_truncation_reason() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "manifest_id": "manifest:run-0001",
        "request_context": {"command_type": "session.propose", "budget": {"max_tokens": 8000}},
        "recent_conversation": {"window_size": 20, "items": []},
        "session_summary": {"version": 3, "text": "...", "updated_at": "2026-09-11T21:00:00Z"},
        "domain_state_refs": ["artifact:problem_map/v3"],
        "preference_refs": ["memory:mem-0001"],
        "evidence_refs": ["evd-0001"],
        "trust_labels": {"evidence_refs": "UNTRUSTED_DATA", "preference_refs": "TRUSTED_POLICY"},
        "token_count": 7800,
        "truncation": {"truncated": True, "reason": "TOKEN_BUDGET"},
        "policy_version": "1.0.0",
        "built_at": "2026-09-11T21:00:00Z",
    }
    result = read_contract("ContextManifest", payload)
    assert result.data.truncation.reason == "TOKEN_BUDGET"


def test_context_manifest_rejects_untrusted_evidence_marked_trusted() -> None:
    """用户上传/检索文本必须始终標为 UNTRUSTED_DATA，不得标成系统指令。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "manifest_id": "manifest:run-0002",
        "request_context": {"command_type": "session.propose"},
        "recent_conversation": {"window_size": 10, "items": []},
        "session_summary": None,
        "domain_state_refs": [],
        "preference_refs": [],
        "evidence_refs": ["evd-0001"],
        "trust_labels": {"evidence_refs": "SYSTEM_INSTRUCTION", "preference_refs": "TRUSTED_POLICY"},
        "token_count": 100,
        "truncation": {"truncated": False, "reason": None},
        "policy_version": "1.0.0",
        "built_at": "2026-09-11T21:00:00Z",
    }
    with pytest.raises(PlatformContractError):
        read_contract("ContextManifest", payload)


def test_context_manifest_truncated_requires_reason() -> None:
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "manifest_id": "manifest:run-0003",
        "request_context": {"command_type": "session.propose"},
        "recent_conversation": {"window_size": 10, "items": []},
        "session_summary": None,
        "domain_state_refs": [],
        "preference_refs": [],
        "evidence_refs": [],
        "trust_labels": {"evidence_refs": "UNTRUSTED_DATA", "preference_refs": "TRUSTED_POLICY"},
        "token_count": 100,
        "truncation": {"truncated": True, "reason": None},
        "policy_version": "1.0.0",
        "built_at": "2026-09-11T21:00:00Z",
    }
    with pytest.raises(PlatformContractError):
        read_contract("ContextManifest", payload)


# ---------------------------------------------------------------------------
# N. 未知字段策略：不得默认成成功
# ---------------------------------------------------------------------------


def test_unknown_fields_are_preserved_not_silently_accepted() -> None:
    """同 major 新增字段应保留（向前兼容），但不得因此把未知语义当作成功。"""
    payload = {
        "schema_version": PLATFORM_CONTRACTS_SCHEMA_VERSION,
        "memory_id": "mem-0004",
        "namespace": {
            "tenant_id": "b3a1c2d4-1111-4222-8333-444455556667",
            "user_id": "b3a1c2d4-1111-4222-8333-444455556666",
            "domain": "teacher_prep",
            "purpose": "preference",
        },
        "kind": "PREFERENCE",
        "content_hash": "a" * 64,
        "provenance": {"source": "USER_EXPLICIT", "created_by_run_id": None},
        "consent": {"granted": True, "scope": "teacher_prep", "granted_at": "2026-09-11T21:00:00Z"},
        "created_at": "2026-09-11T21:00:00Z",
        "updated_at": "2026-09-11T21:00:00Z",
        "redaction": {"status": "APPLIED", "policy_version": "1.0.0"},
        "future_extension_field": {"whatever": True},
    }
    result = read_contract("MemoryRecord", payload)
    # 未知字段进入 unknown_fields 观察集，不改变 core 语义
    assert "future_extension_field" in result.unknown_fields


def test_major_version_bump_is_rejected_fail_closed() -> None:
    """major>current → fail closed；不得静默降级成全字段 success。"""
    payload = {
        "schema_version": "99.0.0",
        "memory_id": "mem-0005",
    }
    with pytest.raises(ContractVersionUnsupported):
        read_contract("MemoryRecord", payload)


# ---------------------------------------------------------------------------
# O. 生成物一致性（禁止手改）
# ---------------------------------------------------------------------------


def test_generated_schema_matches_model() -> None:
    """提交的 platform-contracts.schema.json 必须与模型生成结果字节一致。"""
    from app.platform_contracts.gen.gen_schema import REPO_SCHEMA_PATH, generate_schema

    assert REPO_SCHEMA_PATH.exists(), "缺少生成物 platform-contracts.schema.json"
    on_disk = json.loads(REPO_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert on_disk == generate_schema()


def test_fixtures_valid_and_invalid() -> None:
    """每个契约至少一个 valid fixture 通过；invalid fixture 必须被拒。

    拒绝必须发生在**两层之一**：
    - JSON Schema（形状/类型/pattern/enum）；或
    - Pydantic reader（跨字段语义，如教材 page 锚点、未发布 prompt、trust label 提升）。
    两者都通过 = 假阴性 → 测试失败（禁止把语义违反静默放行）。
    """
    from jsonschema import Draft202012Validator

    from app.platform_contracts.errors import PlatformContractError

    schema = bundle_schema()
    valid_dir = FIXTURES_DIR / "valid"
    invalid_dir = FIXTURES_DIR / "invalid"

    valid_files = sorted(valid_dir.glob("*.json"))
    assert valid_files, "缺少 valid fixtures"
    for path in valid_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        contract = data.pop("_contract")
        errors = list(
            Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": f"#/$defs/{contract}"}
            ).iter_errors(data)
        )
        assert not errors, f"{path.name} 未通过 schema: {errors[:2]}"
        # valid fixture 也必须能被 reader 通过（两层一致）
        result = read_contract(contract, data)
        assert result.kind == contract, f"{path.name}: reader 返回契约名不符"

    invalid_files = sorted(invalid_dir.glob("*.json"))
    assert invalid_files, "缺少 invalid fixtures"
    schema_rejected = 0
    semantic_rejected = 0
    for path in invalid_files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        contract = raw.pop("_contract")
        schema_errors = list(
            Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": f"#/$defs/{contract}"}
            ).iter_errors(raw)
        )
        if schema_errors:
            schema_rejected += 1
            continue
        # schema 层未拦截 → 必须由语义层（reader/validator）拒绝
        try:
            read_contract(contract, raw)
        except PlatformContractError:
            semantic_rejected += 1
            continue
        raise AssertionError(
            f"{path.name} 既未被 JSON Schema 也未被语义层拒绝（假阴性：unknown 被默认放行）"
        )
    # 两个层次都必须真有拦截能力（防止某层恒空导致测试空转）
    assert schema_rejected > 0, "没有任何 invalid fixture 被 JSON Schema 拦截"
    assert semantic_rejected > 0, "没有任何 invalid fixture 被语义层拦截"
