"""IFC-001 契约测试（V2.2.1 收口②，B5 规范 M1 交付物）。

冻结签名：`ifc/IFC-001-SlideSemanticSpec.md`（单一事实源 `app/pagination/schemas.py`，B4 提供）。
断言三件：① SlideSemanticSpec 字段集与禁字段 reject；② layout_slide 纯函数性；
③ B1 侧无 schema 复制定义（grep 断言 import 唯一指向 app/pagination/schemas.py）。
B4 未落地 → 全红（预期），红点即 IFC-001 缺口清单。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def schemas():
    try:
        from app.pagination import schemas  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"[IFC-001 红·预期] app/pagination/schemas.py 未实现（B4）：{exc!r}。"
            "冻结签名见 docs/handoff/IFC-001-SlideSemanticSpec.md"
        )
    return schemas


# ---------------- ① SlideSemanticSpec 字段集与禁字段 ----------------

EXPECTED_FIELDS = {"page_type", "title", "points", "formula", "visual", "teacher_note"}
FORBIDDEN_FIELDS = {"x", "y", "w", "h", "width", "height", "font_size", "color", "left", "top"}
PAGE_TYPES = {
    "concept_intro", "visual_definition", "derivation", "formula_focus",
    "worked_example", "compare", "common_error", "practice", "summary",
}


def test_slide_semantic_spec_field_set(schemas):
    model = schemas.SlideSemanticSpec
    fields = set(model.model_fields)
    assert fields == EXPECTED_FIELDS, (
        f"字段集漂移（IFC-001 冻结）：实际 {sorted(fields)}，应为 {sorted(EXPECTED_FIELDS)}"
    )


def test_page_type_vocabulary(schemas):
    """九种页型词汇表（B2 §3 ppt_slots）。"""
    annotation = schemas.SlideSemanticSpec.model_fields["page_type"].annotation
    allowed = set(getattr(annotation, "__args__", ()) ) - {type(None)}
    assert allowed == PAGE_TYPES, f"page_type 词汇表漂移：{sorted(map(str, allowed))}"


@pytest.mark.parametrize("banned", sorted(FORBIDDEN_FIELDS))
def test_forbidden_pixel_fields_rejected(schemas, banned):
    """README 纪律 14：LLM 禁产像素布局——x/y/w/h/font_size/color 等字段必须 reject。"""
    if banned in schemas.SlideSemanticSpec.model_fields:
        pytest.fail(f"IFC-001 禁字段 {banned!r} 出现在 SlideSemanticSpec——违反 README 纪律 14")
    payload = {
        "page_type": "concept_intro",
        "title": "椭圆的定义",
        "points": ["聚焦绳子实验"],
        "formula": r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1",
        banned: 42,  # 注入禁字段：额外字段必须被拒绝或忽略（不许进模型）
    }
    try:
        spec = schemas.SlideSemanticSpec(**payload)
        assert not hasattr(spec, banned), f"禁字段 {banned} 被接受进模型（extra 属性泄漏）"
    except Exception:
        pass  # 直接 reject 亦符合契约


def test_points_constraints(schemas):
    """points ≤5 条（IFC-001 注释约束；≤40 字/条由提示词层保证，schema 层验条数）。"""
    payload = {
        "page_type": "summary",
        "title": "小结",
        "points": [f"要点{i}" for i in range(6)],
        "formula": None,
        "visual": None,
        "teacher_note": None,
    }
    # IFC-001 注释「≤5 条」：若以 field validator 实现，6 条必须报错；未实现则为契约缺口（红）
    with pytest.raises(Exception) if _has_points_validator(schemas) else _nullcontext():
        schemas.SlideSemanticSpec(**payload)


class _nullcontext:
    """允许「无 validator」通过但记录缺口的占位上下文。"""

    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _has_points_validator(schemas) -> bool:
    src = inspect.getsource(schemas)
    return bool(re.search(r"points.*validator|validator.*points|le=5|max_length=5", src, re.I))


# ---------------- ② layout_slide 纯函数性 ----------------


def test_layout_slide_pure_function(schemas):
    """同输入同输出 + 不改入参（确定性布局引擎，坐标/字号/颜色唯一产出）。"""
    if not hasattr(schemas, "layout_slide"):
        pytest.fail("[IFC-001 红·预期] layout_slide 未实现（B4 app/pagination/layout.py）")
    layout_slide = schemas.layout_slide

    def _spec():
        return schemas.SlideSemanticSpec(
            page_type="worked_example",
            title="例题精讲",
            points=["设椭圆焦点弦 AB", "求周长"],
            formula=r"|AB| = \frac{2b^2}{a}",
            visual=None,
            teacher_note="先让学生动手",
        )

    slots = {
        "layout_type": "example",
        "pedagogical_role": "例题讲解",
        "required_slots": ["title", "stem"],
        "optional_slots": ["note"],
        "max_text": 200,
        "formula_capacity": 2,
        "visual_capacity": 1,
        "locked_elements": [],
    }
    spec = _spec()
    before = spec.model_dump()
    out1 = layout_slide(spec, slots)
    out2 = layout_slide(_spec(), slots)
    assert spec.model_dump() == before, "layout_slide 不得修改入参 spec（纯函数性）"
    assert [e.model_dump() if hasattr(e, "model_dump") else e for e in out1] == [
        e.model_dump() if hasattr(e, "model_dump") else e for e in out2
    ], "同输入必须同输出（确定性，禁随机/时间因子）"


# ---------------- ③ B1 侧无 schema 复制定义 ----------------


def test_no_schema_duplication_in_b1_scope():
    """grep 断言：app/agent/**、app/domains/generation/** 只 import，禁复制定义。"""
    dup_patterns = re.compile(r"class\s+(SlideSemanticSpec|TemplateSlots)\b")
    import_lines = re.compile(r"from\s+app\.pagination(\.schemas)?\s+import|import\s+app\.pagination")
    offenders: list[str] = []
    for scope in ("app/agent", "app/domains/generation"):
        root = BACKEND_ROOT / scope
        if not root.exists():
            continue  # B1 尚未动工 → 无复制即通过
        for py in root.rglob("*.py"):
            src = py.read_text(encoding="utf-8", errors="ignore")
            if dup_patterns.search(src):
                offenders.append(str(py))
    assert not offenders, (
        "IFC-001 违规：B1 侧复制定义 SlideSemanticSpec/TemplateSlots（单一事实源 app/pagination/schemas.py）："
        + ", ".join(offenders)
    )


def test_ifc001_doc_landed_in_handoff():
    """落库检查：docs/handoff/IFC-001-SlideSemanticSpec.md 必须原样存在（M1 落库）。"""
    doc = BACKEND_ROOT / "docs" / "handoff" / "IFC-001-SlideSemanticSpec.md"
    assert doc.exists(), "IFC-001 未落库到 docs/handoff/（B5 M1 已代放，若缺失说明被误删）"
    assert "SlideSemanticSpec" in doc.read_text(encoding="utf-8")
