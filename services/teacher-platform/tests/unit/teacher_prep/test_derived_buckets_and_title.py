"""蓝图推导桶收口 + 标题清理贯通（FROZEN-CONTRACT §B.2 / §B.3 / §F.4）。

覆盖验收：
- `sanitize_title` 清理占位后缀：确定性、幂等、清理后非空；
- 推导桶（objectives/key_points/difficult_points/prior_knowledge）由 **LLM 基于教材事实
  推导**并保留 source_ref；LLM 未产出时才诚实兜底（证据 → 「（待确认）」）；
- homework/reflection（§F.4）：LLM 推导桶，无依据留空 + warning，禁止编造；
- 质量门禁（数量下限 / 占位比例）只披露不阻断，透出到 warnings；
- 标题清理贯通：蓝图 title / deck metadata.title / 教案 metadata.title 零「（待确认）」。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from app.teacher_prep.blueprint import parse_and_validate, project_blueprint, sanitize_title
from app.teacher_prep.blueprint.projector import derived_bucket_quality
from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    project_deck_spec,
    project_lesson_plan_spec,
)

from .tp_context_fixtures import build, seed_synthetic_textbook

TBD = "（待确认）"


# ── 1. sanitize_title（§B.3）─────────────────────────────────────────────────


def test_sanitize_title_removes_placeholder_suffixes() -> None:
    assert sanitize_title("3.1.2 椭圆的简单几何性质（第一课时，待确认）") == "3.1.2 椭圆的简单几何性质"
    assert sanitize_title("椭圆及其标准方程（待确认）") == "椭圆及其标准方程"
    assert sanitize_title("椭圆及其标准方程，待确认") == "椭圆及其标准方程"
    assert sanitize_title("  椭圆  ") == "椭圆"


def test_sanitize_title_idempotent_and_nonempty() -> None:
    once = sanitize_title("集合的概念（待确认）")
    assert sanitize_title(once) == once
    assert sanitize_title("（待确认）", fallback="教学设计蓝图") == "教学设计蓝图"
    assert sanitize_title("", fallback="教案") == "教案"
    assert sanitize_title(None, fallback="课件") == "课件"


def test_sanitize_title_only_deletes_never_invents() -> None:
    # 只删除占位，不改写标题本体
    assert sanitize_title("A（第一课时，待确认）B") == "AB"


# ── 2. 推导桶：LLM 推导项被保留（带 source_ref）──────────────────────────────


async def test_llm_derived_buckets_preserved_with_refs(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    ref = ctx.facts[0].source_ref
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [
            {"text": "理解集合与元素的概念", "source_ref": ref},
            {"text": "会用列举法与描述法表示集合", "source_ref": ref},
        ],
        "prior_knowledge": [{"text": "回顾小学接触过的集合实例", "source_ref": ref}],
        "concepts": [],
        "theorems": [],
        "examples": [],
        "inquiries": [],
        "design_intent": [],
        "key_points": [{"text": "集合的三条性质", "source_ref": ref}, {"text": "集合的表示法", "source_ref": ref}],
        "difficult_points": [{"text": "元素与集合的从属关系", "source_ref": ref}],
        "homework": [{"text": "完成课本练习 1、2", "source_ref": ref}],
        "reflection": [{"text": "关注学生对确定性理解", "source_ref": ref}],
        "lesson_flow": [
            {"stage": "new_content", "title": "新授", "items": [{"text": "讲解概念", "source_ref": ref}], "duration_minutes": 20}
        ],
        "warnings_ack": [],
    }

    proposal = await project_blueprint(ctx, lambda _p: json.dumps(payload, ensure_ascii=False))
    # 推导项保留（非占位），source_ref 保留
    assert [o.text for o in proposal.objectives] == ["理解集合与元素的概念", "会用列举法与描述法表示集合"]
    assert all(o.source_ref == ref for o in proposal.objectives)
    assert [k.text for k in proposal.key_points] == ["集合的三条性质", "集合的表示法"]
    assert proposal.difficult_points[0].text == "元素与集合的从属关系"
    assert proposal.prior_knowledge[0].text == "回顾小学接触过的集合实例"
    assert proposal.homework[0].text == "完成课本练习 1、2"
    assert proposal.reflection[0].text == "关注学生对确定性理解"
    # 质量门禁：满足下限 → 无 below_min 码
    assert not any(w.startswith("objectives_below_min") for w in proposal.warnings_ack)


# ── 3. 推导桶：LLM 未产出 → 诚实兜底「（待确认）」（不编造）──────────────────


async def test_derived_buckets_fallback_when_llm_omits(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成集合 1.1 教学蓝图",
        "objectives": [],
        "prior_knowledge": [],
        "concepts": [],
        "theorems": [],
        "examples": [],
        "inquiries": [],
        "design_intent": [],
        "key_points": [],
        "difficult_points": [],
        "homework": [],
        "reflection": [],
        "lesson_flow": [{"stage": "new_content", "title": "新授", "items": [], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    proposal = await project_blueprint(ctx, lambda _p: json.dumps(payload, ensure_ascii=False))
    # 无教材证据的桶 → 「（待确认）」占位（绝不编造断言）
    assert proposal.difficult_points and all(TBD in it.text for it in proposal.difficult_points)
    # homework/reflection 无依据 → 留空（§F.4），并记 warning
    assert proposal.homework == [] and proposal.reflection == []
    assert "homework_no_basis" in proposal.warnings_ack
    assert "reflection_no_basis" in proposal.warnings_ack
    # 质量门禁披露：对最终 payload 重算的质量码必须全部并入 warnings_ack（只披露不阻断）
    recomputed = derived_bucket_quality(proposal.model_dump())
    assert recomputed, "至少应产生一个质量码（数量下限 / 占位比例）"
    assert set(recomputed) <= set(proposal.warnings_ack)
    assert any(w.startswith("difficult_points_below_min") for w in proposal.warnings_ack)


def test_derived_bucket_quality_reports_counts() -> None:
    warnings = derived_bucket_quality({"objectives": [{"text": "a"}], "key_points": [{"text": TBD}]})
    assert "objectives_below_min:1<2" in warnings
    assert "key_points_below_min:0<2" in warnings


# ── 4. 标题清理贯通（§B.3）──────────────────────────────────────────────────


async def test_llm_title_placeholder_stripped_on_proposal(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "3.1.2 椭圆的简单几何性质（第一课时，待确认）",
        "objectives": [{"text": "理解椭圆定义", "source_ref": None}],
        "key_points": [{"text": "椭圆定义", "source_ref": None}],
        "difficult_points": [{"text": "标准方程推导", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授", "items": [], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    proposal = await project_blueprint(ctx, lambda _p: json.dumps(payload, ensure_ascii=False))
    assert "待确认" not in proposal.title
    assert proposal.title == "3.1.2 椭圆的简单几何性质"


def test_parse_and_validate_sanitizes_title_without_context() -> None:
    payload = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": "a" * 64,
        "title": "椭圆（待确认）",
        "lesson_flow": [{"stage": "new_content", "title": "新授", "items": []}],
    }
    proposal = parse_and_validate(json.dumps(payload, ensure_ascii=False))
    assert proposal.title == "椭圆"


async def test_titles_clean_through_projection_chain(db) -> None:
    """蓝图 title → deck/教案 metadata.title 全链零「（待确认）」（§B.3）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    body = {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "3.1.2 椭圆的简单几何性质（第一课时，待确认）",
        "objectives": [{"text": "理解椭圆定义", "source_ref": None}],
        "concepts": [],
        "theorems": [],
        "examples": [],
        "inquiries": [],
        "key_points": [{"text": "椭圆定义", "source_ref": None}],
        "difficult_points": [{"text": "标准方程推导", "source_ref": None}],
        "lesson_flow": [{"stage": "new_content", "title": "新授", "items": [], "duration_minutes": 20}],
        "warnings_ack": [],
    }
    kw = dict(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    lp = project_lesson_plan_spec(**kw)
    deck = project_deck_spec(**kw)
    assert "待确认" not in lp.metadata.title
    assert "待确认" not in deck.metadata.title
    assert "待确认" not in deck.slides[0].title
