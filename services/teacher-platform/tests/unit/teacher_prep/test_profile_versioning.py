"""R20 版本化 ClassLearningProfile 单测：版本化 / 时效性 / 覆盖 / 冲突 warning / 409 / 旧 revision 保留。

覆盖验收：
- Profile 每版本是不可变快照（revision++ 保留旧版本）；
- 时效（expires_at / 有效期）→ stale warning；低覆盖 → low_profile_coverage；
  证据冲突 → evidence_conflict（warning 随版本不可变）；
- expected_revision 乐观并发 → 409；archived 终态不可再发布；
- 发布/更新必须教学角色（actor_role 硬闸门）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.reliability.idempotency import request_hash_of
from app.teacher_prep.profile.models import (
    ClassLearningProfileRevisionRow,
    ClassLearningProfileRow,
)
from app.teacher_prep.profile.schema import (
    PROFILE_VALIDITY_DAYS,
    ProfileEvidenceSummary,
    ProfileFreshness,
    VersionedClassLearningProfile,
    eval_freshness,
)
from app.teacher_prep.service import (
    InvalidTransitionError,
    MembershipDeniedError,
    RevisionConflictError,
)
from app.teacher_prep_v2.contracts.models import (
    ComputationInfo,
    ProfileAggregation,
    ProfileScope,
)

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"


def idem(n: int) -> str:
    return f"r20-key-{n:04d}"


RH = lambda body: request_hash_of("POST", "/p", body)  # noqa: E731


async def _create_session(db, service, adapter, members, *, key=1):
    members.set(CLASS_A, user_id=TEACHER)
    return await service.create_session(
        db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        class_id=CLASS_A,
        idempotency_key=idem(key),
        request_hash=RH({"class_id": CLASS_A}),
    )


def _profile(
    *,
    session_id: str | None = None,
    profile_id: str | None = None,
    coverage_ratio: float = 1.0,
    low_coverage: bool = False,
    stale: bool = False,
    conflicts: list | None = None,
    revision: int = 1,
    status: str = "published",
) -> VersionedClassLearningProfile:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    evidence = ProfileEvidenceSummary(
        submission_count=10,
        evidence_count=3 if low_coverage else 10,
        coverage_ratio=coverage_ratio,
        expected_evidence=10,
        low_coverage=low_coverage,
        conflicts=conflicts or [],
    )
    return VersionedClassLearningProfile(
        profile_id=profile_id or str(uuid.uuid4()),
        class_id=CLASS_A,
        owner_id=TEACHER,
        session_id=session_id,
        scope=ProfileScope(),
        status=status,
        revision=revision,
        aggregation=ProfileAggregation(),
        computed_from=ComputationInfo(
            run_id="run-synth-001",
            provider_version="teacher_prep.aggregator.v1",
            computed_at=now,
        ),
        evidence=evidence,
        freshness=ProfileFreshness(computed_at=now, expires_at=None, stale=stale),
    )


# ── 版本化：发布 → 更新 → 旧版本保留 ──────────────────────────────────────────


async def test_publish_profile_creates_versioned_row(db, service, adapter, members) -> None:
    created = await _create_session(db, service, adapter, members)
    p = _profile(session_id=created["session_id"])

    result = await service.publish_profile(
        db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        session_id=created["session_id"],
        class_id=CLASS_A,
        idempotency_key=idem(2),
        request_hash=RH({"profile_id": p.profile_id}),
        profile=p,
    )
    assert result["status"] == "published" and result["revision"] == 1

    row = (
        await db.execute(
            select(ClassLearningProfileRow).where(ClassLearningProfileRow.id == uuid.UUID(result["profile_id"]))
        )
    ).scalar_one()
    assert row.revision == 1 and row.status == "published"
    assert row.body["revision"] == 1 and row.body["status"] == "published"
    # 时效列与 body.freshness 一致（SQLite DateTime 剥离 tzinfo → 归一比较）
    assert row.computed_at == p.freshness.computed_at.replace(tzinfo=None)

    revs = (
        (await db.execute(select(ClassLearningProfileRevisionRow)))
        .scalars()
        .all()
    )
    assert len(revs) == 1 and revs[0].revision_no == 1 and revs[0].verdict == "published"
    assert revs[0].parent_revision_no is None


async def test_profile_update_bumps_revision_keeps_old(db, service, adapter, members) -> None:
    created = await _create_session(db, service, adapter, members)
    p1 = _profile(session_id=created["session_id"])
    first = await service.publish_profile(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        session_id=created["session_id"], class_id=CLASS_A,
        idempotency_key=idem(2), request_hash=RH({"v": 1}), profile=p1,
    )
    p2 = _profile(
        session_id=created["session_id"],
        profile_id=first["profile_id"],
        coverage_ratio=0.4,
        low_coverage=True,
    )
    second = await service.publish_profile(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        session_id=created["session_id"], class_id=CLASS_A,
        profile_id=first["profile_id"], expected_revision=1,
        idempotency_key=idem(3), request_hash=RH({"v": 2}), profile=p2,
    )
    assert second["revision"] == 2 and second["status"] == "published"

    revs = (
        (await db.execute(select(ClassLearningProfileRevisionRow).order_by(ClassLearningProfileRevisionRow.revision_no)))
        .scalars()
        .all()
    )
    assert [r.revision_no for r in revs] == [1, 2]
    # 旧版本不可变保留，body 不回写
    assert revs[0].body["revision"] == 1
    assert revs[1].body["revision"] == 2
    assert revs[1].parent_revision_no == 1
    # 新版本保留低覆盖 warning
    assert "low_profile_coverage" in revs[1].body["warnings"]

    row = (
        await db.execute(
            select(ClassLearningProfileRow).where(ClassLearningProfileRow.id == uuid.UUID(first["profile_id"]))
        )
    ).scalar_one()
    assert row.revision == 2 and row.coverage["low_coverage"] is True


async def test_profile_revision_conflict_409(db, service, adapter, members) -> None:
    created = await _create_session(db, service, adapter, members)
    p1 = _profile(session_id=created["session_id"])
    first = await service.publish_profile(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        session_id=created["session_id"], class_id=CLASS_A,
        idempotency_key=idem(2), request_hash=RH({"v": 1}), profile=p1,
    )
    p2 = _profile(session_id=created["session_id"], profile_id=first["profile_id"])
    with pytest.raises(RevisionConflictError):
        await service.publish_profile(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=created["session_id"], class_id=CLASS_A,
            profile_id=first["profile_id"], expected_revision=99,  # 并发冲突
            idempotency_key=idem(4), request_hash=RH({"v": 99}), profile=p2,
        )


async def test_archived_profile_cannot_publish(db, service, adapter, members) -> None:
    created = await _create_session(db, service, adapter, members)
    pid = uuid.uuid4()
    db.add(
        ClassLearningProfileRow(
            id=pid,
            class_id=uuid.UUID(CLASS_A),
            session_id=uuid.UUID(created["session_id"]),
            owner_id=uuid.UUID(TEACHER),
            status="archived",
            revision=1,
            body={"revision": 1, "status": "archived"},
            computed_at=datetime(2026, 9, 1, tzinfo=UTC),
            warnings=[],
        )
    )
    await db.commit()
    p2 = _profile(session_id=created["session_id"], profile_id=str(pid))
    with pytest.raises(InvalidTransitionError):
        await service.publish_profile(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=created["session_id"], class_id=CLASS_A,
            profile_id=str(pid), expected_revision=1,
            idempotency_key=idem(5), request_hash=RH({"v": 5}), profile=p2,
        )


async def test_profile_publish_requires_teaching_role(db, service, adapter, members) -> None:
    created = await _create_session(db, service, adapter, members)
    p = _profile(session_id=created["session_id"])
    with pytest.raises(MembershipDeniedError):
        await service.publish_profile(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="ai",  # LLM 无权
            session_id=created["session_id"], class_id=CLASS_A,
            idempotency_key=idem(6), request_hash=RH({"v": 6}), profile=p,
        )


# ── warning 确定性：低覆盖 / 旧 Profile / 证据冲突 ────────────────────────────


def test_profile_warnings_deterministic() -> None:
    """同 evidence/freshness/conflicts → 同 warnings（body 自洽，防漂移）。"""
    clean = _profile()
    assert clean.warnings == []

    low = _profile(low_coverage=True, coverage_ratio=0.3)
    assert "low_profile_coverage" in low.warnings

    stale = _profile(stale=True)
    assert "stale_profile" in stale.warnings

    conflicted = _profile(
        conflicts=[{"code": "evidence_conflict", "detail": "证据冲突", "ref": "ev-1"}]
    )
    assert "evidence_conflict" in conflicted.warnings


def test_eval_freshness_deterministic() -> None:
    now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    computed = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

    fresh = eval_freshness(computed, expires_at=datetime(2026, 9, 30, tzinfo=UTC), now=now)
    assert fresh.stale is False

    expired = eval_freshness(computed, expires_at=datetime(2026, 9, 5, tzinfo=UTC), now=now)
    assert expired.stale is True and "expires_at" in expired.stale_reason

    # 无 expires_at：超出有效期 → stale
    limit = computed + timedelta(days=PROFILE_VALIDITY_DAYS)
    beyond = eval_freshness(computed, expires_at=None, now=limit + timedelta(seconds=1))
    assert beyond.stale is True and "有效期" in beyond.stale_reason
