"""R20 TeachingBlueprint revision proposal 单测：8 维 diff / 未确认不下发 / 409 / 旧 revision / 零 page_refs。

覆盖验收：
- 同 (base, proposal, profile_ref, context hash) → 同 diff（确定性骨架）；
- Profile 更新（低掌握 finding）→ 可解释 diff（objectives 维 change + 证据绑定）；
- 未确认蓝图不能生成修订提案（409）；expected_revision 并发冲突 → 409；
- 旧 revision 不可变保留；Profile 必须 published 才能作证据；
- 低覆盖/旧 Profile/证据冲突 warning 随 diff 保留；
- diff 条目只搬运 source_ref（SourceLocator），零 page_refs/pNN；
- LLM 输出必须 schema 校验（坏 JSON/发明 ref → 拒绝，不成为事实）。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.reliability.idempotency import request_hash_of
from app.teacher_prep.blueprint.revision_projector import (
    RevisionGroundingViolation,
    RevisionProposalInvalid,
    project_blueprint_revision,
)
from app.teacher_prep.profile.schema import (
    ProfileEvidenceSummary,
    ProfileFreshness,
    VersionedClassLearningProfile,
)
from app.teacher_prep.service import (
    ApiError,
    InvalidTransitionError,
    RevisionConflictError,
)
from app.teacher_prep_v2.contracts.models import ProfileAggregation, ProfileScope

from .tp_context_fixtures import build, seed_synthetic_textbook

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"


def idem(n: int) -> str:
    return f"r20-rev-{n:04d}"


RH = lambda body: request_hash_of("POST", "/r", body)  # noqa: E731


def _base_payload(ctx) -> dict:
    """R16 直形已确认蓝图 body（作为 base 与修订 proposal.blueprint 复用）。"""
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "集合与元素", "source_ref": None}],
        "theorems": [],
        "examples": [{"text": "用列举法表示集合", "source_ref": None}],
        "inquiries": [{"text": "思考：集合的元素可以重复吗？", "source_ref": None}],
        "design_intent": [],
        "key_points": [{"text": "集合的三条性质", "source_ref": None}],
        "difficult_points": [{"text": "元素与集合的从属关系", "source_ref": None}],
        "lesson_flow": [
            {"stage": "introduction", "title": "情境导入", "items": [{"text": "从生活实例引入集合", "source_ref": None}], "duration_minutes": 5},
            {"stage": "new_content", "title": "新授", "items": [], "duration_minutes": 25},
        ],
        "warnings_ack": [],
    }


def _profile(*, profile_id: str, low_coverage: bool = False, stale: bool = False) -> VersionedClassLearningProfile:
    return VersionedClassLearningProfile(
        profile_id=profile_id,
        class_id=CLASS_A,
        owner_id=TEACHER,
        scope=ProfileScope(),
        status="published",
        revision=1,
        aggregation=ProfileAggregation(),
        evidence=ProfileEvidenceSummary(
            submission_count=10,
            evidence_count=3 if low_coverage else 10,
            coverage_ratio=0.3 if low_coverage else 1.0,
            expected_evidence=10,
            low_coverage=low_coverage,
        ),
        freshness=ProfileFreshness(
            computed_at=datetime(2026, 9, 1, tzinfo=UTC),
            expires_at=None,
            stale=stale,
        ),
    )


def _revision_payload(
    ctx,
    *,
    blueprint_id: str,
    blueprint_revision: int,
    profile_ref: dict,
    objective_text: str | None = None,
    invented_ref: str | None = None,
) -> dict:
    """构造 TeachingBlueprintRevisionProposal 合法 JSON（LLM 输出模拟）。"""
    bp = _base_payload(ctx)
    objective = objective_text or "理解集合与元素的概念"
    bp["objectives"] = [{"text": objective, "source_ref": invented_ref if invented_ref else None}]
    if invented_ref:
        bp["concepts"] = [{"text": "概念", "source_ref": invented_ref}]
    evidence = []
    if objective_text:
        evidence.append(
            {
                "finding_id": "f-low-mastery-1",
                "code": "low_mastery",
                "dimension": "objectives",
                "applies_to": ["objectives", "key_difficult_points"],
                "detail": f"低掌握：{objective_text}",
                "ref": None,
            }
        )
    return {
        "schema_version": "0.1.0",
        "base_blueprint_schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "base": {"blueprint_id": blueprint_id, "revision": blueprint_revision},
        "profile": profile_ref,
        "blueprint": bp,
        "stratified_support": [{"text": "基础层：先巩固列举法", "source_ref": None}],
        "diagnostic_response": [{"text": "发现误区时按 profile finding 纠正", "source_ref": None}],
        "evidence": evidence,
        "warnings": list(profile_ref.get("warnings") or []),
        "warnings_ack": list(profile_ref.get("warnings") or []),
    }


def _fake_llm(payload: dict):
    def _gen(_prompt: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return _gen


async def _confirmed_blueprint(db, service, adapter, members, ctx):
    """创建会话 → 提案 → 确认 → 返回 blueprint_id/revision。"""
    members.set(CLASS_A, user_id=TEACHER)
    created = await service.create_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
        idempotency_key=idem(1), request_hash=RH({"class_id": CLASS_A}),
    )
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(2),
        request_hash=RH({"bp": 1}), body=_base_payload(ctx), title="v1",
    )
    confirmed = await service.confirm_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=1,
        idempotency_key=idem(3), request_hash=RH({"cf": 1}),
    )
    return {"session_id": created["session_id"], "blueprint_id": prop["blueprint_id"], "revision": confirmed["revision"]}


# ── 8 维 diff：确定性 + Profile 更新产生可解释 diff ──────────────────────────


async def test_revision_diff_deterministic(db):
    """同 (base, proposal, profile_ref, ctx) → 两次 diff 逐字段一致。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    profile = _profile(profile_id=str(uuid.uuid4()))
    profile_ref = {
        "profile_id": str(profile.profile_id),
        "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(),
        "expires_at": None,
        "stale": False,
        "coverage_ratio": 1.0,
        "warnings": [],
    }
    base = {
        "blueprint_id": str(uuid.uuid4()),
        "revision": 3,
        **{k: v for k, v in _base_payload(ctx).items()},
    }
    payload = _revision_payload(
        ctx, blueprint_id=base["blueprint_id"], blueprint_revision=3,
        profile_ref=profile_ref, objective_text="掌握集合与元素的双向从属",
    )
    p1, d1 = await project_blueprint_revision(ctx, base_blueprint_body=base, profile_ref=profile_ref, llm_generate=_fake_llm(payload))
    p2, d2 = await project_blueprint_revision(ctx, base_blueprint_body=base, profile_ref=profile_ref, llm_generate=_fake_llm(payload))
    assert d1.model_dump(mode="json") == d2.model_dump(mode="json")
    assert d1.summary.total == d2.summary.total > 0
    # 8 个维度全部出现
    dims = {e.dimension for e in d1.entries}
    assert {"objectives", "key_difficult_points", "time_allocation", "activities",
            "examples", "stratified_support", "diagnostic_response", "evidence"} <= dims


async def test_profile_update_produces_explainable_diff(db):
    """Profile 更新（新增低掌握 finding）→ 同 base 下 diff 目标维 change + 证据绑定。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    base = {"blueprint_id": str(uuid.uuid4()), "revision": 3, **{k: v for k, v in _base_payload(ctx).items()}}

    p_plain = _profile(profile_id="aaaaaaaa-0000-0000-0000-000000000001")
    ref_plain = {
        "profile_id": str(p_plain.profile_id), "profile_revision": 1,
        "computed_at": p_plain.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    p_low = _profile(profile_id="bbbbbbbb-0000-0000-0000-000000000002", low_coverage=True)
    ref_low = {
        "profile_id": str(p_low.profile_id), "profile_revision": 2,
        "computed_at": p_low.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 0.3, "warnings": ["low_profile_coverage"],
    }
    obj_low = "针对低掌握：先建立元素从属关系的直观练习"
    _, d_plain = await project_blueprint_revision(
        ctx, base_blueprint_body=base, profile_ref=ref_plain, llm_generate=_fake_llm(
            _revision_payload(ctx, blueprint_id=base["blueprint_id"], blueprint_revision=3, profile_ref=ref_plain)
        )
    )
    _, d_low = await project_blueprint_revision(
        ctx, base_blueprint_body=base, profile_ref=ref_low, llm_generate=_fake_llm(
            _revision_payload(ctx, blueprint_id=base["blueprint_id"], blueprint_revision=3,
                              profile_ref=ref_low, objective_text=obj_low)
        )
    )
    # Profile 更新 → 目标维出现 change 且绑定 finding 证据
    changes = [e for e in d_low.entries if e.op == "change"]
    assert any(e.dimension == "objectives" and e.evidence_refs == ["f-low-mastery-1"] for e in changes)
    # 低覆盖 warning 随 diff 保留（不静默吞没）
    assert "low_profile_coverage" in d_low.warnings
    # 两 diff 不同（可解释：Profile 变化驱动修订）
    assert d_plain.model_dump(mode="json") != d_low.model_dump(mode="json")


def test_diff_entries_no_page_refs(db):
    """diff 条目零 page_refs/pNN：只搬运 source_ref（SourceLocator 形状）。"""
    from app.teacher_prep.blueprint.diff import build_revision_diff

    base = {"blueprint_id": str(uuid.uuid4()), "revision": 1,
            "objectives": [{"text": "A", "source_ref": None}], "key_points": [],
            "difficult_points": [], "inquiries": [], "examples": [],
            "lesson_flow": [{"stage": "introduction", "title": "t", "duration_minutes": 5}]}
    proposal = {
        "blueprint": {"objectives": [{"text": "A2", "source_ref": None}], "key_points": [],
                      "difficult_points": [], "inquiries": [], "examples": [],
                      "lesson_flow": [{"stage": "introduction", "title": "t", "duration_minutes": 8}]},
        "stratified_support": [], "diagnostic_response": [], "evidence": [],
    }
    diff = build_revision_diff(
        base=base, proposal=proposal, profile_ref={"profile_id": "x"},
        context_snapshot_hash="0" * 64,
    )
    dumped = json.dumps(diff.model_dump(mode="json"), ensure_ascii=False)
    assert "page_refs" not in dumped and "p" not in [k for k in ["p01", "p1", "p12"] if k in dumped]


# ── 服务级：未确认不下发 / 409 / 旧 revision / 画像状态闸门 ──────────────────


async def test_unconfirmed_blueprint_cannot_revise(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    members.set(CLASS_A, user_id=TEACHER)
    created = await service.create_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
        idempotency_key=idem(1), request_hash=RH({"class_id": CLASS_A}),
    )
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(2),
        request_hash=RH({"bp": 1}), body=_base_payload(ctx), title="v1",
    )  # 状态 = proposal（未确认）
    profile = _profile(profile_id=str(uuid.uuid4()))
    with pytest.raises(InvalidTransitionError) as ei:
        await service.propose_blueprint_revision(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], blueprint_id=prop["blueprint_id"],
            expected_revision=1, idempotency_key=idem(4), request_hash=RH({"rv": 1}),
            context=ctx, profile=profile, llm_generate=_fake_llm({}),
        )
    assert ei.value.code == "40900" or ei.value.status_code == 409


async def test_revision_conflict_409(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile(profile_id=str(uuid.uuid4()))
    with pytest.raises(RevisionConflictError):
        await service.propose_blueprint_revision(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
            expected_revision=bp["revision"] + 99,  # 并发冲突
            idempotency_key=idem(5), request_hash=RH({"rv": 2}),
            context=ctx, profile=profile, llm_generate=_fake_llm({}),
        )


async def test_profile_must_be_published(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile(profile_id=str(uuid.uuid4()))
    profile.status = "collecting"  # 未发布不可作证据
    with pytest.raises(ApiError):
        await service.propose_blueprint_revision(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
            expected_revision=bp["revision"], idempotency_key=idem(6), request_hash=RH({"rv": 3}),
            context=ctx, profile=profile, llm_generate=_fake_llm({}),
        )


async def test_revision_proposal_preserves_old_revision(db, service, adapter, members):
    """修订提案：旧 confirmed revision 不可变保留；新 revision 为 proposal。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile(profile_id=str(uuid.uuid4()))
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    payload = _revision_payload(
        ctx, blueprint_id=bp["blueprint_id"], blueprint_revision=bp["revision"],
        profile_ref=profile_ref, objective_text="修订后的目标",
    )
    result = await service.propose_blueprint_revision(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], idempotency_key=idem(7), request_hash=RH({"rv": 4}),
        context=ctx, profile=profile, llm_generate=_fake_llm(payload),
    )
    assert result["status"] == "proposal" and result["revision"] == bp["revision"] + 1
    # 提案写库，但 verdict=proposal → 未经教师确认不得成为 approved
    from sqlalchemy import select

    from app.teacher_prep.models import BlueprintRevisionRow

    revs = (
        (await db.execute(
            select(BlueprintRevisionRow).where(BlueprintRevisionRow.blueprint_id == uuid.UUID(bp["blueprint_id"])).order_by(BlueprintRevisionRow.revision_no)
        ))
        .scalars()
        .all()
    )
    assert [r.verdict for r in revs] == ["proposal", "confirmed", "proposal"]  # 旧 confirmed 保留，新提案未确认
    assert revs[2].body["blueprint"]["objectives"][0]["text"] == "修订后的目标"  # 提案 body 为修订形


async def test_revision_schema_invalid_rejected(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile(profile_id=str(uuid.uuid4()))
    with pytest.raises(RevisionProposalInvalid):
        await service.propose_blueprint_revision(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
            expected_revision=bp["revision"], idempotency_key=idem(8), request_hash=RH({"rv": 5}),
            context=ctx, profile=profile, llm_generate=lambda _p: "not json {{{",
        )


async def test_revision_grounding_violation(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile(profile_id=str(uuid.uuid4()))
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    payload = _revision_payload(
        ctx, blueprint_id=bp["blueprint_id"], blueprint_revision=bp["revision"],
        profile_ref=profile_ref, invented_ref=(
            f"textbook_v2/locator/v1/source/{uuid.uuid4()}/section/{uuid.uuid4()}/chunk/{uuid.uuid4()}"
        ),
    )
    with pytest.raises(RevisionGroundingViolation):
        await service.propose_blueprint_revision(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
            expected_revision=bp["revision"], idempotency_key=idem(9), request_hash=RH({"rv": 6}),
            context=ctx, profile=profile, llm_generate=_fake_llm(payload),
        )
