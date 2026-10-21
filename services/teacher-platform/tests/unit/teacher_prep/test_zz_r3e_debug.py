"""R3e debug scratch (temporary)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.teacher_prep.specs.projector import (
    ProjectionRunLineage,
    project_deck_spec,
    project_lesson_plan_spec,
)

from .tp_context_fixtures import build, seed_synthetic_textbook


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


async def test_debug_board(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
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
        lineage=ProjectionRunLineage(model="teacher-prep-specs-v1"), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    print("\n=== TEXT POINTS ===")
    for it in spec.board_design.text_points:
        print(repr(it.text))
    print("\n=== FORMULAS ===")
    for it in spec.board_design.formulas:
        print(repr(it.text))
    print("\n=== MATH OBJECT ===")
    print(spec.board_design.math_object)


async def test_debug_deck_pages(db):
    await seed_synthetic_textbook(db)
    ctx = await build(db)
    body = _base_payload(ctx)
    body["lesson_flow"] = body["lesson_flow"] * 3
    dk = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="teacher-prep-specs-v1"), run_hash="b" * 64,
        projected_at=datetime(2026, 9, 10, 22, 38, tzinfo=UTC),
    )
    print("\n=== SLIDES ===", len(dk.slides))
    for s in dk.slides:
        print(s.id, s.type, s.title, "|", repr(s.content[:120]))
        for e in s.elements:
            print("   ", e.role, (e.text or "")[:80], (e.items or [])[:3])
