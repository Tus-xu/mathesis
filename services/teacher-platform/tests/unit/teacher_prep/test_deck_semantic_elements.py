"""Deck 语义元素投影测试（FROZEN-CONTRACT §A）：投影产出的 slides[].elements。

覆盖：
- 椭圆概念页 → figure + definition + knowledge_card（canonical 公式同源，非 LLM 创作）；
- 其余页 → bullets；封面无 elements；
- 全元素无引擎坐标、role ∈ §A.2 枚举、字段长度满足 §A.3；
- 确定性：同输入 → 同 elements（幂等）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.ppt_v2.domain.spec import ENGINE_GEOMETRY_FIELD_NAMES

from app.teacher_prep.specs.projector import ProjectionRunLineage, project_deck_spec

from .tp_context_fixtures import build, seed_synthetic_textbook, selection

_VALID_ROLES = {
    "definition", "formula", "knowledge_card", "figure", "bullets", "note", "steps", "table",
}


def _body(ctx) -> dict:
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "合成椭圆 3.1.2 教学蓝图",
        "objectives": [{"text": "掌握椭圆的范围、对称性、特殊点", "source_ref": None}],
        "prior_knowledge": [],
        "concepts": [{"text": "椭圆的标准方程 $\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$", "source_ref": None}],
        "theorems": [],
        "examples": [],
        "inquiries": [{"text": "观察椭圆的扁平程度", "source_ref": None}],
        "key_points": [{"text": "椭圆的对称性", "source_ref": None}],
        "difficult_points": [],
        "lesson_flow": [
            {
                "stage": "new_content",
                "title": "探究椭圆的几何性质",
                "duration_minutes": 10,
                "items": [
                    {"text": "探究椭圆的范围：结合图形观察椭圆边界", "source_ref": None},
                    {"text": "探究椭圆的对称性与焦点 F_1、F_2 的位置", "source_ref": None},
                ],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 5,
                "items": [{"text": "椭圆的范围、对称性、特殊点", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }


def _project(ctx, body: dict):
    return project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=str(uuid.uuid4()), blueprint_revision=2,
        blueprint_body=body, context=ctx, profile_ref={"profile_id": "x"},
        lineage=ProjectionRunLineage(model="m"), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


async def test_ellipse_concept_page_emits_rich_elements(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    page = next(s for s in deck.slides if s.math_object is not None)
    roles = [e.role for e in page.elements]
    # 图形页承载 figure + knowledge_card + formula（canonical 与图形同源）
    assert "figure" in roles and "knowledge_card" in roles and "formula" in roles
    # W1-A 角色语义：concept 页不是定义页 → 不得用 definition（避免语义错误定义带）
    assert "definition" not in roles
    figure = next(e for e in page.elements if e.role == "figure")
    assert figure.figure_ref == "math_object"
    card = next(e for e in page.elements if e.role == "knowledge_card")
    # canonical 公式来自服务端（与图形同源），非 LLM 创作
    assert "a > b > 0" in (card.latex or "")


async def test_non_ellipse_page_uses_bullets(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    body = _body(ctx)
    body["title"] = "合成集合 1.1 教学蓝图"
    for stage in body["lesson_flow"]:
        stage["title"] = stage["title"].replace("椭圆", "集合")
        stage["items"] = [
            {**it, "text": it["text"].replace("椭圆", "集合").replace("F_1、F_2", "代表元素")}
            for it in stage["items"]
        ]
    body["concepts"] = [{"text": "集合与元素", "source_ref": None}]
    body["inquiries"] = [{"text": "集合的元素可以重复吗", "source_ref": None}]
    # fixture 自洽：非椭圆 deck 的所有桶都不得残留椭圆语义（objectives/key_points/difficult_points）
    body["objectives"] = [{"text": it["text"].replace("椭圆", "集合"), "source_ref": None} for it in body["objectives"]]
    body["key_points"] = [{"text": it["text"].replace("椭圆", "集合"), "source_ref": None} for it in body["key_points"]]
    body["difficult_points"] = [{"text": it["text"].replace("椭圆", "集合"), "source_ref": None} for it in body["difficult_points"]]
    deck = _project(ctx, body)
    assert all(s.math_object is None for s in deck.slides)
    # 非图形页：要点列表（无 figure/knowledge_card）
    for slide in deck.slides:
        for el in slide.elements:
            assert el.role in ("bullets", "definition", "steps", "formula", "note")
            assert el.role != "figure"


async def test_cover_has_no_elements(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    assert deck.slides[0].type == "title"
    assert deck.slides[0].elements == []


async def test_all_elements_obey_contract(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    deck = _project(ctx, _body(ctx))
    for slide in deck.slides:
        for el in slide.elements:
            assert el.role in _VALID_ROLES
            assert set(el.model_dump()).isdisjoint(set(ENGINE_GEOMETRY_FIELD_NAMES))
            if el.text is not None:
                assert len(el.text) <= 400
            if el.title is not None:
                assert len(el.title) <= 60
            assert len(el.items) <= 10
            assert all(len(it) <= 200 for it in el.items)


async def test_elements_deterministic(db) -> None:
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    bid = str(uuid.uuid4())
    body = _body(ctx)
    a = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2, blueprint_body=body,
        context=ctx, profile_ref={}, lineage=ProjectionRunLineage(), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    b = project_deck_spec(
        spec_id=str(uuid.uuid4()), blueprint_id=bid, blueprint_revision=2, blueprint_body=body,
        context=ctx, profile_ref={}, lineage=ProjectionRunLineage(), run_hash="c" * 64,
        projected_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert [s.elements for s in a.slides] == [s.elements for s in b.slides]
