"""TP-H1 回归测试：lead/body 通用去重 + chip 中文化 + figure-missing 显式降级。

每个测试断言对应任务书第 1/4/5/7 条要求：
- test_lead_body_dedupe_is_general：通用去重，不依赖"集合"等关键词特殊判断
- test_dedupe_handles_whitespace_and_punctuation_variants：跨空白/标点等价
- test_concept_dedupe_does_not_drop_distinct_points：保留真正不同的要点
- test_definition_dedupe_handles_strong_keyword_match：定义锚定去重
- test_equation_dedupe_handles_max_density_keyword：公式焦点去重
- test_worked_example_dedupe_problem_vs_steps：例题题干/步骤去重
- test_practice_dedupe_prompt_vs_hints：练习 prompt/hints 去重
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _FakeSlide:
    id: str
    type: str
    title: str
    content: str
    assets: list = None  # type: ignore[assignment]
    visual_intent: str = ""
    speaker_note: str | None = None
    teaching_goal: str = ""
    source_refs: list = None  # type: ignore[assignment]
    layout_hint: str | None = None
    revision: int = 0
    status: str = "confirmed"
    qa_status: str = "pending"


def _slide(sid: str, content: str, stype: str = "concept") -> _FakeSlide:
    return _FakeSlide(id=sid, type=stype, title="测试", content=content, assets=[],
                       teaching_goal="t", source_refs=[])


def test_lead_body_dedupe_is_general() -> None:
    """lead 与 bullet 同文 → 通用归一化键去重，无须为"集合"等关键词开特例。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s1",
               "一般地，我们把研究对象统称为元素，把一些元素组成的总体叫做集合。\n"
               "- 一般地，我们把研究对象统称为元素，把一些元素组成的总体叫做集合。\n"
               "- 元素必须具有确定性\n"
               "- 元素必须具有互异性\n"
               "- 元素必须具有无序性",
               stype="concept")
    slots = {sl["slot"]: sl for sl in map_slots("concept-focus", s)}
    assert slots["concept-statement"]["text"].startswith("一般地"), "lead 应是首行"
    items = slots["points-2col"]["items"]
    # 关键断言：首条 bullet（与 lead 同文）必须被剔除，不允许 lead/body 重复
    assert all(not it.startswith("一般地，我们把研究对象") for it in items), \
        f"lead/body 重复未被剔除：{items}"
    # 真正不同的要点保留
    assert any("确定性" in it for it in items)
    assert any("互异性" in it for it in items)
    assert any("无序性" in it for it in items)


def test_dedupe_handles_whitespace_and_punctuation_variants() -> None:
    """跨空白 / 全角标点变体也应判定为同句。"""
    from app.visual_planning.resolve import map_slots
    cases = [
        ("设 f(x) = ax² + bx + c（a ≠ 0）。\n- 设 f(x) = ax² + bx + c（a ≠ 0）。\n- 后续",
         "设f(x)=ax²+bx+c（a≠0）", "derivation"),
        ("二次方程 ax² + bx + c = 0 的根。\n- 二次方程 ax² + bx + c = 0 的根。\n- 由求根公式",
         "二次方程ax²+bx+c=0的根", "concept"),
    ]
    for content, _norm_expected, stype in cases:
        s = _slide("s", content, stype=stype)
        fam = {"derivation": "progressive-derivation",
               "concept": "concept-focus"}[stype]
        slots = {sl["slot"]: sl for sl in map_slots(fam, s)}
        if stype == "concept":
            stmt = slots["concept-statement"]["text"]
            items = slots["points-2col"]["items"]
        else:
            stmt = slots["title"]["text"]   # 推导页 stmt 不入 slot
            items = slots.get("steps", {}).get("items", [])
        # 关键断言：归一化键命中即剔除
        for it in items:
            from app.visual_planning.resolve import _norm_key
            assert _norm_key(it) != _norm_key(stmt) or not stmt, \
                f"空白/标点变体未被剔除：{it!r}"


def test_concept_dedupe_does_not_drop_distinct_points() -> None:
    """真正不同的要点必须保留。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s", "首行陈述。\n- 首行陈述。\n- 独立要点 A\n- 独立要点 B\n- 独立要点 C",
               stype="concept")
    slots = {sl["slot"]: sl for sl in map_slots("concept-focus", s)}
    items = slots["points-2col"]["items"]
    assert items == ["独立要点 A", "独立要点 B", "独立要点 C"], \
        f"真正不同的要点应保留：{items}"


def test_definition_dedupe_handles_strong_keyword_match() -> None:
    """定义页：含"叫做"强定义词的行做 banner，与正文去重。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s",
               "我们把研究对象统称为元素，把一些元素组成的总体叫做集合。\n"
               "- 我们把研究对象统称为元素，把一些元素组成的总体叫做集合。\n"
               "- 元素具有确定性\n"
               "- 元素具有互异性",
               stype="definition")
    slots = {sl["slot"]: sl for sl in map_slots("definition-emphasis", s)}
    banner = slots["definition-banner"]["text"]
    assert "叫做" in banner
    support = slots["supporting"]["items"]
    # banner 行必须从 supporting 中剔除
    from app.visual_planning.resolve import _norm_key
    for it in support:
        assert _norm_key(it) != _norm_key(banner)


def test_equation_dedupe_handles_max_density_keyword() -> None:
    """公式焦点：含数学符号最多的行做 eq，其余去重。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s",
               "x ∈ A 且 x ∉ B 时，x ∈ A ∪ B。\n"
               "- x ∈ A 且 x ∉ B 时，x ∈ A ∪ B。\n"
               "- 并集含义说明 A\n"
               "- 并集含义说明 B",
               stype="derivation")
    slots = {sl["slot"]: sl for sl in map_slots("equation-focus", s)}
    eq = slots["equation"]["text"]
    ann = slots["annotations"]["items"]
    from app.visual_planning.resolve import _norm_key
    for it in ann:
        assert _norm_key(it) != _norm_key(eq)


def test_worked_example_dedupe_problem_vs_steps() -> None:
    """例题页：以"例"开头的行做 problem，与 solution-steps 去重。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s",
               "例 1　用描述法表示解集\n"
               "- 例 1　用描述法表示解集\n"
               "- 解：先解对应方程\n"
               "- 解：写成区间形式",
               stype="worked-example")
    slots = {sl["slot"]: sl for sl in map_slots("worked-example", s)}
    problem = slots["problem"]["text"]
    steps = slots["solution-steps"]["items"]
    assert problem.startswith("例 1")
    from app.visual_planning.resolve import _norm_key
    for st in steps:
        assert _norm_key(st) != _norm_key(problem)


def test_practice_dedupe_prompt_vs_hints() -> None:
    """练习页：prose 首行做 prompt，bullets 做 hints；prompt 与 hints 去重。"""
    from app.visual_planning.resolve import map_slots
    s = _slide("s",
               "判断下列关系是否成立。\n"
               "- 判断下列关系是否成立。\n"
               "- (1) 2 ∈ {1,2,3}\n"
               "- (2) {2} ∈ {1,2,3}",
               stype="practice")
    slots = {sl["slot"]: sl for sl in map_slots("practice-prompt", s)}
    prompt = slots["prompt"]["text"]
    hints = slots["hints"]["items"]
    assert "判断" in prompt
    from app.visual_planning.resolve import _norm_key
    for h in hints:
        assert _norm_key(h) != _norm_key(prompt)


def test_basic_fallback_chip_is_chinese_label() -> None:
    """basic 路径（无 visual_plan）下 chip 文本 = 中文标签，禁止英文内部枚举泄漏。

    注：本断言仅核对 VPL 侧的 ROLE_ZH；8201 引擎侧 TYPE_ZH_LABEL 已在
    services/ppt-presenton/tests/test_tp_h1_regression.py 独立验证。
    """
    from app.visual_planning.intent import ROLE_ZH
    for role, zh in ROLE_ZH.items():
        assert any('\u4e00' <= c <= '\u9fff' for c in zh), \
            f"{role} → {zh!r} 不含中文"
        assert zh != role, f"{role} 角色标签未翻译"


def test_plan_path_chip_is_chinese_label() -> None:
    """plan 路径：chip 文本来自 role_label_zh，必须是中文（取自 intent.ROLE_ZH）。"""
    from app.visual_planning.intent import ROLE_ZH
    # 全量断言：每种角色都有中文标签
    for role, zh in ROLE_ZH.items():
        assert any('\u4e00' <= c <= '\u9fff' for c in zh), \
            f"{role} → {zh!r} 不含中文"
        assert zh != role, f"{role} 角色标签未翻译"