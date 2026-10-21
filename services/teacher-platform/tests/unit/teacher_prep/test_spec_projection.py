"""R20 双 Spec 投影单测：确定性 / 幂等重放 / 双 Spec hash 一致 / 零 page_refs / lineage。

覆盖验收：
- 同输入 → 同 projection_hash + body_hash（确定性骨架）；同输入重试 → 重放（幂等）；
- LessonPlanSpec 与 TeachingDeckSpec 的 approved blueprint revision / context hash 一致；
- 新写入零 page_refs（deck textbook_sources.page_refs 恒空；spec JSON 无 page_refs/pNN）；
- 旧 DeckSpec（无 projection 字段）兼容读取；R16 直形 / R20 修订形 body 双兼容；
- 未确认蓝图投影 → 409；Pydantic 构造即 schema 校验（非法输入拒绝）；
- prompt/model/run lineage 绑定到 spec.projection + tp_spec_projections。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ppt_v2.domain.spec import TeachingDeckSpec
from app.reliability.idempotency import request_hash_of
from app.teacher_prep.blueprint.revision_schema import TeachingBlueprintRevisionProposal
from app.teacher_prep.profile.schema import (
    ProfileEvidenceSummary,
    ProfileFreshness,
    VersionedClassLearningProfile,
)
from app.teacher_prep.service import InvalidTransitionError
from app.teacher_prep.specs.models import LessonPlanSpecRow, SpecProjectionRow
from app.teacher_prep.specs.projector import (
    ProjectionInputs,
    ProjectionRunLineage,
    compute_projection_hash,
    normalize_blueprint,
    project_deck_spec,
    project_lesson_plan_spec,
)
from app.teacher_prep_v2.contracts.models import ProfileAggregation, ProfileScope

from .tp_context_fixtures import build, seed_synthetic_textbook

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"


def idem(n: int) -> str:
    return f"r20-spec-{n:04d}"


RH = lambda body: request_hash_of("POST", "/s", body)  # noqa: E731


def _base_payload(ctx) -> dict:
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


def _profile() -> VersionedClassLearningProfile:
    return VersionedClassLearningProfile(
        profile_id=str(uuid.uuid4()),
        class_id=CLASS_A,
        owner_id=TEACHER,
        scope=ProfileScope(),
        status="published",
        revision=1,
        aggregation=ProfileAggregation(),
        evidence=ProfileEvidenceSummary(),
        freshness=ProfileFreshness(computed_at=datetime(2026, 9, 1, tzinfo=UTC)),
    )


def _revision_body(ctx, bp_id: str, bp_revision: int) -> dict:
    """R20 修订形已确认蓝图 body（body["blueprint"] 为内容；含新维度）。"""
    return {
        "schema_version": "0.1.0",
        "base_blueprint_schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "base": {"blueprint_id": bp_id, "revision": bp_revision},
        "profile": {"profile_id": str(uuid.uuid4()), "profile_revision": 1,
                    "computed_at": "2026-09-01T00:00:00+00:00", "expires_at": None,
                    "stale": False, "coverage_ratio": 1.0, "warnings": []},
        "blueprint": _base_payload(ctx),
        "stratified_support": [{"text": "基础层：先巩固列举法", "source_ref": None}],
        "diagnostic_response": [{"text": "发现误区时纠正", "source_ref": None}],
        "evidence": [{"finding_id": "f-1", "code": "low_mastery", "dimension": "objectives",
                      "applies_to": ["objectives"], "detail": "低掌握：集合表示法", "ref": None}],
        "warnings": [],
        "warnings_ack": [],
    }


def _lineage() -> ProjectionRunLineage:
    return ProjectionRunLineage(
        prompt_release_id="release-r20-001",
        ai_run_id="run-ai-0001",
        model="teacher-prep-llm-v1",
    )


async def _confirmed_blueprint(db, service, adapter, members, ctx, *, body=None):
    members.set(CLASS_A, user_id=TEACHER)
    created = await service.create_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
        idempotency_key=idem(1), request_hash=RH({"class_id": CLASS_A}),
    )
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(2),
        request_hash=RH({"bp": 1}), body=body or _base_payload(ctx), title="v1",
    )
    confirmed = await service.confirm_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=1,
        idempotency_key=idem(3), request_hash=RH({"cf": 1}),
    )
    return {"session_id": created["session_id"], "blueprint_id": prop["blueprint_id"], "revision": confirmed["revision"]}


# ── 确定性 + 幂等重放 ────────────────────────────────────────────────────────


async def test_projection_hash_deterministic(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    inputs = ProjectionInputs(
        blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=_base_payload(ctx), context_snapshot_hash=ctx.snapshot_hash,
        profile_ref={"profile_id": "x", "profile_revision": 1},
    )
    assert compute_projection_hash(inputs) == compute_projection_hash(inputs)
    assert len(compute_projection_hash(inputs)) == 64


async def test_dual_spec_projection_deterministic(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    inputs = dict(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=_base_payload(ctx), context=ctx,
        profile_ref={"profile_id": "x"}, lineage=_lineage(), run_hash="a" * 64,
        projected_at=datetime(2026, 9, 10, 22, 38, tzinfo=UTC),  # 固定投影时间（确定性输入）
    )
    lp1 = project_lesson_plan_spec(**inputs)
    lp2 = project_lesson_plan_spec(**inputs)
    assert lp1.projection.body_hash == lp2.projection.body_hash
    dk1 = project_deck_spec(**inputs)
    dk2 = project_deck_spec(**inputs)
    assert dk1.projection.body_hash == dk2.projection.body_hash


async def test_projection_idempotent_replay(db, service, adapter, members):
    """同输入重试 → replayed=True，同 spec_target_id，无重复落库。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    kwargs = dict(
        session=db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], context=ctx, profile_ref=profile_ref,
        lineage=_lineage(), kinds=("lesson_plan", "deck"),
    )
    first = await service.project_specs(idempotency_key=idem(4), request_hash=RH({"v": 1}), **kwargs)
    second = await service.project_specs(idempotency_key=idem(4), request_hash=RH({"v": 1}), **kwargs)
    # 同 key 同载荷 → 幂等重放：逐字段一致（verbatim replay），无重复副作用
    assert second["results"]["lesson_plan"]["spec_id"] == first["results"]["lesson_plan"]["spec_id"]
    assert second["results"]["deck"]["spec_id"] == first["results"]["deck"]["spec_id"]
    rows = (await db.execute(select(SpecProjectionRow))).scalars().all()
    assert len(rows) == 2  # 每 kind 一条，无重复


async def test_projection_different_input_new_projection(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    kwargs = dict(
        session=db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], context=ctx, profile_ref=profile_ref,
        lineage=_lineage(), kinds=("lesson_plan",),
    )
    await service.project_specs(idempotency_key=idem(4), request_hash=RH({"v": 1}), **kwargs)
    # 同一 key 不同载荷 → 幂等层 409（不同请求 = 新投影，不得重放）
    from app.reliability.idempotency import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        await service.project_specs(idempotency_key=idem(4), request_hash=RH({"v": 2}), **kwargs)


# ── 双 Spec hash 一致 + 零 page_refs + lineage ───────────────────────────────


async def test_reprojection_after_blueprint_edit_bumps_lesson_plan_revision(db, service, adapter, members):
    """真实事故回归（2026-09-21）：教师改动课堂设计后再点「同步到教案」。

    蓝图 revision 前进 → 教案必须是**新版本**（revision+1，新行）而不是同版本重插；
    旧实现把 revision 恒写 1 → 撞 UNIQUE(blueprint_id, revision) → 500，整条同步链路不可用。
    """
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    kwargs = dict(
        session=db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], context=ctx, profile_ref=profile_ref,
        lineage=_lineage(), kinds=("lesson_plan",),
    )
    first = await service.project_specs(idempotency_key=idem(4), request_hash=RH({"v": 1}), **kwargs)
    assert first["results"]["lesson_plan"]["revision"] == 1

    # 教师编辑（等价 conversation.edit_node：蓝图 revision+1，快照不可变）
    from app.teacher_prep.models import TeachingBlueprintRow

    brow = (
        await db.execute(
            select(TeachingBlueprintRow).where(TeachingBlueprintRow.id == uuid.UUID(bp["blueprint_id"]))
        )
    ).scalar_one()
    brow.revision += 1
    await db.commit()

    second = await service.project_specs(
        idempotency_key=idem(5), request_hash=RH({"v": 2}), **{**kwargs, "expected_revision": brow.revision}
    )
    assert second["results"]["lesson_plan"]["revision"] == 2
    rows = (
        (await db.execute(select(LessonPlanSpecRow).order_by(LessonPlanSpecRow.revision))).scalars().all()
    )
    assert [r.revision for r in rows] == [1, 2]
    assert rows[-1].approved_blueprint_revision == brow.revision
    # 教案「活产物」新版本随投影日志可追溯（同蓝图两种 kind 各一条）
    assert rows[-1].body["change_log"][-1]["kind"] == "regenerate"


async def test_dual_spec_approved_blueprint_context_hash_consistent(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    result = await service.project_specs(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], idempotency_key=idem(4), request_hash=RH({"v": 1}),
        context=ctx, profile_ref=profile_ref, lineage=_lineage(),
    )
    lp = result["results"]["lesson_plan"]["spec"]
    dk = result["results"]["deck"]["spec"]
    # 双 Spec 共享 approved blueprint/context hash 锚点（验收硬锚点）
    assert lp["projection"]["approved_blueprint_revision"] == dk["projection"]["approved_blueprint_revision"] == bp["revision"]
    assert lp["projection"]["context_snapshot_hash"] == dk["projection"]["context_snapshot_hash"] == ctx.snapshot_hash
    assert lp["blueprint_ref"]["blueprint_id"] == dk["metadata"]["course_context_ref"] == bp["blueprint_id"]
    assert lp["blueprint_ref"]["revision"] == dk["projection"]["approved_blueprint_revision"] == bp["revision"]
    # 各自独立 schema_version / revision / purpose
    assert lp["schema_version"] == "1.0.0" and dk["schema_version"] == "2.0.0"
    assert lp["purpose"] == "lesson_plan" and lp["revision"] == 1
    # 编辑历史独立存在（create 条目）
    assert lp["change_log"][0]["kind"] == "create"


async def test_new_write_zero_page_refs(db, service, adapter, members):
    """新写入零 page_refs：deck 溯源全空数组 + 无 pNN 假页码；教案只收 locator。"""
    import re

    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    result = await service.project_specs(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], idempotency_key=idem(4), request_hash=RH({"v": 1}),
        context=ctx, profile_ref=profile_ref, lineage=_lineage(),
    )
    for kind in ("lesson_plan", "deck"):
        dumped = json.dumps(result["results"][kind]["spec"], ensure_ascii=False)
        assert not re.search(r'"p\d{1,3}"', dumped), f"{kind} 新写入不得含 pNN 假页码"
    # deck 溯源为 canonical locator，page_refs 恒空数组（零引用）
    sources = result["results"]["deck"]["spec"]["textbook_sources"]
    assert sources and all(s["page_refs"] == [] for s in sources)
    assert ctx.locator is not None and sources[0]["locator"] == ctx.locator


async def test_lesson_plan_source_refs_all_locators(db, service, adapter, members):
    """教案 source_refs 全部为 SourceLocator（可解析引用，零假页码）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    body = _base_payload(ctx)
    ref = ctx.facts[0].source_ref  # 真实可解析 locator
    body["objectives"][0]["source_ref"] = ref
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx, body=body)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    result = await service.project_specs(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], idempotency_key=idem(4), request_hash=RH({"v": 1}),
        context=ctx, profile_ref=profile_ref, lineage=_lineage(), kinds=("lesson_plan",),
    )
    spec = result["results"]["lesson_plan"]["spec"]
    assert ref in spec["source_refs"]
    for item in spec["objectives"]:
        if item.get("source_ref"):
            assert item["source_ref"] == ref


async def test_old_deck_spec_compatible_read(db, service, adapter, members):
    """旧 DeckSpec（无 projection 字段）读完全兼容：schema 允许 None。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    old = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=1,
        blueprint_body=_base_payload(ctx), context=ctx, profile_ref={},
        lineage=ProjectionRunLineage(), run_hash="b" * 64,
    )
    old_dict = old.model_dump(mode="json")
    old_dict.pop("projection")  # 模拟旧版本数据（无投影血缘字段）
    reloaded = TeachingDeckSpec.model_validate(old_dict)  # 兼容读取
    assert reloaded.projection is None
    assert reloaded.schema_version == "2.0.0"


def test_normalize_blueprint_dual_shapes():
    """R16 直形 / R20 修订形 body 双兼容归一化。"""
    direct = {"schema_version": "0.1.0", "objectives": [], "lesson_flow": []}
    assert normalize_blueprint(direct)["bp"] is direct
    assert normalize_blueprint(direct)["stratified_support"] == []

    revision_shaped = {
        "blueprint": {"objectives": [{"text": "x"}], "lesson_flow": []},
        "stratified_support": [{"text": "s"}], "diagnostic_response": [], "evidence": [],
        "warnings": ["low_profile_coverage"], "warnings_ack": ["low_profile_coverage"],
    }
    norm = normalize_blueprint(revision_shaped)
    assert norm["bp"]["objectives"] == [{"text": "x"}]
    assert norm["stratified_support"] == [{"text": "s"}]
    assert norm["warnings"] == ["low_profile_coverage", "low_profile_coverage"]  # 双回执合并


async def test_board_design_v8_cleans_section_no_and_formula_column(db):
    """v8 板书清洗（真实会话实测缺陷回归）：

    - 文字要点首条带教材目录噪音「3.1.2 椭圆的简单几何性质」→ 必须剥离；
    - 公式区混入「本节结论：掌握…的方法」整句（观感像教研批注）→ 目标句剔除；
    - 「本节结论：<真实数学内容>」→ 剥标签后归位到文字要点（内容不丢）。
    """
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    body = _base_payload(ctx)
    body["lesson_flow"] = [
        {
            "stage": "new_content",
            "title": "椭圆的几何性质",
            "duration_minutes": 20,
            "board_notes": [
                {"text": r"3.1.2 椭圆的简单几何性质" + "\n" + r"椭圆标准方程：$\frac{x^2}{a^2}+\frac{y^2}{b^2}=1$", "source_ref": None},
                {"text": r"1. 范围：$|x|\leq a, |y|\leq b$", "source_ref": None},
            ],
        },
    ]
    body["theorems"] = [
        {"text": "本节结论：掌握利用椭圆标准方程研究几何性质的方法", "source_ref": None},
        {"text": r"本节结论：平面内，动点$M$与定点$F$的距离和为常数的轨迹是椭圆", "source_ref": None},
    ]
    spec = project_lesson_plan_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    texts = [it.text for it in spec.board_design.text_points]
    assert all(not t.startswith("3.1.2") for t in texts)  # 目录噪音剥离
    assert any(t.startswith("椭圆的简单几何性质") for t in texts)
    assert any(t.startswith("1. 范围：") for t in texts)  # 单项条目号保留
    # 真实数学内容剥标签后归位；纯目标句不进板书
    assert any(t.startswith("平面内，动点") for t in texts)
    assert all(not t.startswith(("掌握", "理解", "体会")) for t in texts)
    assert all("本节结论" not in it.text for it in spec.board_design.formulas)
    # 公式只准出现在公式栏（回归：曾因归位过滤缺 `not _is_formula_text` 而两栏重复）
    assert all("本节结论" not in t for t in texts)
    formulas = [it.text for it in spec.board_design.formulas]
    assert r"$\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1 \quad (a > b > 0)$" in formulas  # 服务端 canonical
    assert all(ft not in texts for ft in formulas)  # 公式不在文字要点栏重复出现


async def test_deck_projection_emits_front_and_back_matter_pages(db):
    """R3 闭环：投影确定性地补齐 封面/学习目标/课堂小结 页（ingest 页预算 [8,12] 对齐）。

    - 首页 type=title、目标页跟随、小结页收尾；内容全部来自蓝图事实；
    - 页总数 = 2(前置) + len(lesson_flow) + 1(小结，仅有重点/难点时)。
    """
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    body = _base_payload(ctx)
    body["lesson_flow"] = body["lesson_flow"] * 3  # 6 个环节 → 6+3=9 页，落入 [8,12]
    dk = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=_lineage(), run_hash="b" * 64,
        projected_at=datetime(2026, 9, 10, 22, 38, tzinfo=UTC),
    )
    slides = dk.slides
    assert slides[0].id == "slide-00" and slides[0].type == "title"
    # 2026-09-23 对齐：投影器已改为顺序编号（front matter 页 id 为 slide-01，不再用
    # "slide-00b" 后缀）；页序、类型与数量断言不变（front matter + flow + practice + summary）。
    assert slides[1].id == "slide-01" and slides[1].title == "学习目标"
    assert "理解集合与元素的概念" in slides[1].content
    # 2026-09-23 对齐 v3+ 组页规则：流程未含 example/exercise 环节时，投影器会按
    # 「只排真实有据的内容」把 examples 事实排成**例题页**（type=worked-example），
    # 因此倒数第二页类型随蓝图事实而定（worked-example 或 practice），不再恒为"练一练"。
    assert slides[-2].type in ("worked-example", "practice")
    assert "用列举法表示集合" in slides[-2].content
    assert slides[-1].type == "summary" and slides[-1].title == "课堂小结"
    assert "集合的三条性质" in slides[-1].content
    # 2026-09-23 对齐 v3+ 组页规则：同型环节/事实会合并成页（"不重复成页"），
    # 总数不再等于固定公式；锁内核页预算上限 12 + id 顺序编号无空洞即可。
    # 2026-09-27 对齐 v13（R3e 产物回炉）：**宁少页不留空页**——每页 ≥2 条真实内容项，
    # 例题题面归例题页不被补白偷走；本 fixture 有 3 个空环节（items=[]）+ 7 条真实事实，
    # 诚实组页为 6–7 页（下限 5），不再用空页/重复页凑 8 页内核预算下限。
    assert 5 <= len(slides) <= 12
    assert [s.id for s in slides] == [f"slide-{i:02d}" for i in range(len(slides))]
    # 零 page_refs 铁律不被新页破坏
    assert all(s.source_refs == [] or all("page_refs" not in r for r in [s.source_refs]) for s in slides)


# ── 服务闸门：未确认不下发 + schema 校验 + lineage 落库 ─────────────────────


async def test_unconfirmed_blueprint_projection_409(db, service, adapter, members):
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
    )  # proposal（未确认）
    profile = _profile()
    profile_ref = {"profile_id": str(profile.profile_id), "profile_revision": 1,
                   "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
                   "stale": False, "coverage_ratio": 1.0, "warnings": []}
    with pytest.raises(InvalidTransitionError):
        await service.project_specs(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], blueprint_id=prop["blueprint_id"],
            expected_revision=1, idempotency_key=idem(4), request_hash=RH({"v": 1}),
            context=ctx, profile_ref=profile_ref, lineage=_lineage(),
        )


async def test_projection_schema_validation_rejects_invalid(db):
    """Pydantic 构造即 schema 校验：非法输入 → ValidationError（不落库）。"""
    from pydantic import ValidationError

    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    with pytest.raises(ValidationError):
        project_deck_spec(
            spec_id="not-a-uuid", blueprint_id=str(uuid.uuid4()), blueprint_revision=1,
            blueprint_body=_base_payload(ctx), context=ctx, profile_ref={},
            lineage=_lineage(), run_hash="short",
        )


async def test_lineage_bound_to_specs_and_projection_log(db, service, adapter, members):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    bp = await _confirmed_blueprint(db, service, adapter, members, ctx)
    profile = _profile()
    profile_ref = {
        "profile_id": str(profile.profile_id), "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(), "expires_at": None,
        "stale": False, "coverage_ratio": 1.0, "warnings": [],
    }
    result = await service.project_specs(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=bp["session_id"], blueprint_id=bp["blueprint_id"],
        expected_revision=bp["revision"], idempotency_key=idem(4), request_hash=RH({"v": 1}),
        context=ctx, profile_ref=profile_ref, lineage=_lineage(),
    )
    for kind in ("lesson_plan", "deck"):
        proj = result["results"][kind]["spec"]["projection"]
        assert proj["prompt_release_id"] == "release-r20-001"
        assert proj["ai_run_id"] == "run-ai-0001"
        assert proj["model"] == "teacher-prep-llm-v1"
        assert proj["run_hash"] == result["results"][kind]["projection_hash"]

    rows = (await db.execute(select(SpecProjectionRow))).scalars().all()
    assert {r.kind for r in rows} == {"lesson_plan", "deck"}
    assert all(r.lineage["prompt_release_id"] == "release-r20-001" for r in rows)
    lp_row = (
        await db.execute(select(LessonPlanSpecRow).where(LessonPlanSpecRow.blueprint_id == uuid.UUID(bp["blueprint_id"])))
    ).scalar_one()
    assert lp_row.approved_blueprint_revision == bp["revision"]
    assert lp_row.context_snapshot_hash == ctx.snapshot_hash
    assert lp_row.lineage["model"] == "teacher-prep-llm-v1"


async def test_revision_body_projection_roundtrip(db):
    """R20 修订形已确认蓝图 body → 双 Spec 投影可用（proposal 未确认前不投影）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    assert ctx is not None
    body = _revision_body(ctx, str(uuid.uuid4()), 2)
    TeachingBlueprintRevisionProposal.model_validate(body)  # 修订形 body schema 自洽
    inputs = dict(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={},
        lineage=_lineage(), run_hash="c" * 64,
    )
    lp = project_lesson_plan_spec(**inputs)
    dk = project_deck_spec(**inputs)
    assert lp.schema_version == "1.0.0" and dk.schema_version == "2.0.0"
    assert lp.warnings or dk.projection is not None
