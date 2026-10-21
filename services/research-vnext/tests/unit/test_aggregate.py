"""RV02 · ResearchSession 聚合根单元测试（先红后绿）。

任务书要求 1：聚合根最少含
id / tenant / project / owner / title / status / revision / created / updated /
current artifact refs / summary ref / policy refs。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from research_vnext.core.domain.errors import (
    InvalidTransition,
    UnknownEventType,
    UnsupportedSchemaVersion,
)
from research_vnext.core.domain.events import SUPPORTED_EVENT_SCHEMA_VERSIONS, DomainEvent
from research_vnext.core.domain.session import ResearchSession, SessionMode, SessionState


def _new(**kw) -> ResearchSession:
    return ResearchSession.create(
        session_id=kw.get("session_id", uuid.uuid4()),
        tenant_id=kw.get("tenant_id", uuid.uuid4()),
        project_id=kw.get("project_id", uuid.uuid4()),
        owner_id=kw.get("owner_id", uuid.uuid4()),
        title=kw.get("title", "RV02 会话"),
        mode=kw.get("mode", SessionMode.MODELING),
        policy_refs=kw.get("policy_refs", ["policy:research.default@v1"]),
        occurred_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------
# 1. 聚合根最低字段
# --------------------------------------------------------------------------


def test_aggregate_has_all_required_fields() -> None:
    s = _new()
    for attr in (
        "id",
        "tenant_id",
        "project_id",
        "owner_id",
        "title",
        "status",
        "revision",
        "created_at",
        "updated_at",
        "current_artifact_refs",
        "summary_ref",
        "policy_refs",
    ):
        assert hasattr(s, attr), f"ResearchSession 缺少必需字段 {attr}"


def test_create_starts_draft_revision_zero() -> None:
    s = _new()
    assert s.status == SessionState.DRAFT
    assert s.revision == 0
    assert s.current_artifact_refs == []
    assert s.policy_refs == ["policy:research.default@v1"]
    assert s.summary_ref is not None
    assert s.created_at <= s.updated_at


def test_summary_ref_is_versioned() -> None:
    s = _new()
    assert s.summary_ref.startswith("summary:")


# --------------------------------------------------------------------------
# 2. revision 严格单调递增
# --------------------------------------------------------------------------


def test_revision_strictly_monotonic() -> None:
    s = _new()
    s.transition(SessionState.ACTIVE, at=datetime.now(timezone.utc))
    assert s.revision == 1
    s.transition(SessionState.WAITING_CONFIRMATION, at=datetime.now(timezone.utc))
    assert s.revision == 2
    s.transition(SessionState.ACTIVE, at=datetime.now(timezone.utc), confirmation=True)
    assert s.revision == 3
    assert s.confirmation_count == 1


def test_revision_never_decreases() -> None:
    s = _new()
    seen = []
    for st in (SessionState.ACTIVE, SessionState.RUNNING, SessionState.ACTIVE):
        s.transition(st, at=datetime.now(timezone.utc))
        seen.append(s.revision)
    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen)


# --------------------------------------------------------------------------
# 3. 事件：未知 event / schema version 拒绝
# --------------------------------------------------------------------------


def test_known_event_accepted() -> None:
    s = _new()
    ev = DomainEvent.create(
        event_type="session.created",
        schema_version=SUPPORTED_EVENT_SCHEMA_VERSIONS[0],
        session_id=s.id,
        revision=s.revision,
        payload={"title": s.title},
        trace_id="a" * 32,
    )
    s.record_event(ev)
    assert s.last_event_id == ev.event_id


def test_unknown_event_type_rejected() -> None:
    s = _new()
    with pytest.raises(UnknownEventType):
        DomainEvent.create(
            event_type="session.does_not_exist",
            schema_version=SUPPORTED_EVENT_SCHEMA_VERSIONS[0],
            session_id=s.id,
            revision=s.revision,
            payload={},
            trace_id="a" * 32,
        )


def test_unsupported_schema_version_rejected() -> None:
    s = _new()
    with pytest.raises(UnsupportedSchemaVersion):
        DomainEvent.create(
            event_type="session.created",
            schema_version="99.0.0",
            session_id=s.id,
            revision=s.revision,
            payload={},
            trace_id="a" * 32,
        )


def test_non_namespaced_event_type_rejected() -> None:
    with pytest.raises(UnknownEventType):
        DomainEvent.create(
            event_type="created",
            schema_version=SUPPORTED_EVENT_SCHEMA_VERSIONS[0],
            session_id=uuid.uuid4(),
            revision=0,
            payload={},
            trace_id="a" * 32,
        )


# --------------------------------------------------------------------------
# 4. 状态机在聚合上生效
# --------------------------------------------------------------------------


def test_cannot_skip_confirmation_on_aggregate() -> None:
    s = _new()
    s.transition(SessionState.ACTIVE, at=datetime.now(timezone.utc))
    with pytest.raises(InvalidTransition):
        s.transition(SessionState.READY_FOR_REVIEW, at=datetime.now(timezone.utc))


def test_partial_cannot_become_completed_on_aggregate() -> None:
    s = _new()
    s.transition(SessionState.ACTIVE, at=datetime.now(timezone.utc))
    s.transition(SessionState.RUNNING, at=datetime.now(timezone.utc))
    s.transition(
        SessionState.PARTIAL,
        at=datetime.now(timezone.utc),
        partial_reason={"succeeded": ["parse"], "failed": ["search"]},
    )
    assert s.status == SessionState.PARTIAL
    with pytest.raises(InvalidTransition):
        s.transition(SessionState.COMPLETED, at=datetime.now(timezone.utc))


def test_artifact_refs_replaceable_with_revision_bump() -> None:
    s = _new()
    before = s.revision
    s.set_artifact_refs(["artifact:1@sha256:" + "a" * 64], at=datetime.now(timezone.utc))
    assert s.revision == before + 1
    assert len(s.current_artifact_refs) == 1
