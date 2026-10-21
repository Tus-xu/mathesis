"""Phase E / Golden B Gate §6：`deck_polish` 确定性整形单测。

覆盖设计 §6 的六条发布前门（不依赖真实 MinIO / LLM，存储一律 monkeypatch）：
1. 封面/页脚：title 去出版社+册次+课时前缀、保留章节号；副标题教材行只出现一次；
2. 去重：页内/跨页归一化去重、同题回声、教师口吻、占位符、工程黑话零上屏；
3. 薄页：非封面页真实内容单元 <2 → 并入相邻语义页 / 删除并重排 outline；
4. 公式：可见文本零裸源码（`$`/`\\boxed`/`\\frac`），formula 必有 latex，唯一事实不丢；
5. 目标页：真实概念补「学习重点」至 ≥3 条；
6. 图形：点斜式/直线课题无图时补 1 张真实 MATH_RENDER；存储失败诚实降级不挂假图。

证据链：R3e3 真实 deck spec（`deliverables/teacher-prep-center/V2/rounds/R3e3/_deck-spec.json`）
+ ppt_v2 golden fixture（合成集合，legacy content 路径）。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.ppt_v2.domain.spec import TeachingDeckSpec
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord
from app.reliability.idempotency import request_hash_of
from app.teacher_prep.profile.schema import (
    ProfileEvidenceSummary,
    ProfileFreshness,
    VersionedClassLearningProfile,
)
from app.teacher_prep.service import TeacherPrepService
from app.teacher_prep.specs import math_figures
from app.teacher_prep.specs.deck_polish import _slide_units, polish_deck_spec
from app.teacher_prep.specs.projector import ProjectionRunLineage
from app.teacher_prep_v2.contracts.models import ProfileAggregation, ProfileScope

from .tp_context_fixtures import build, seed_synthetic_textbook, selection

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"

_ROOT = Path(__file__).resolve().parents[5]
_R3E3_SPEC = _ROOT / "deliverables/teacher-prep-center/V2/rounds/R3e3/_deck-spec.json"
_GOLDEN_A = Path(__file__).resolve().parents[1] / "ppt_v2/fixtures/deck_golden_a.json"

_NORM_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _NORM_WS.sub("", str(text or "")).rstrip("。？！?!；;，,：:")


def _load_spec(path: Path) -> TeachingDeckSpec:
    return TeachingDeckSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _r3e3() -> TeachingDeckSpec:
    if not _R3E3_SPEC.exists():  # 证据不足时显式跳过（不静默通过）
        pytest.skip(f"R3e3 deck spec 不存在：{_R3E3_SPEC}")
    return _load_spec(_R3E3_SPEC)


def _golden_a() -> TeachingDeckSpec:
    return _load_spec(_GOLDEN_A)


def _is_cover(slide) -> bool:
    return slide.type == "title" or slide.layout_hint == "title"


def _visible_text(spec: TeachingDeckSpec) -> list[str]:
    """可见文本（不含 formula latex 源码——latex 字段是 KaTeX 的规范输入，不是可见文本）。"""
    out: list[str] = []
    for slide in spec.slides:
        out.append(slide.title)
        if not slide.elements:
            out.extend(slide.content.split("\n"))
            continue
        for el in slide.elements:
            if el.text:
                out.append(el.text)
            if el.title:
                out.append(el.title)
            out.extend(el.items or [])
    return [t for t in out if str(t).strip()]


def _element_contents(spec: TeachingDeckSpec) -> list[str]:
    """元素内容行（对齐 ppt_v2.qa.content：text + latex + items）。"""
    out: list[str] = []
    for slide in spec.slides:
        for el in slide.elements:
            if el.text:
                out.append(el.text)
            if el.latex:
                out.append(el.latex)
            out.extend(el.items or [])
    return [t for t in out if str(t).strip()]


def _page_title(spec: TeachingDeckSpec, title: str):
    return next(s for s in spec.slides if s.title == title)


def _items(slide) -> list[str]:
    return [it for el in slide.elements for it in (el.items or [])]


# ─────────────────────────────────────────────────────────────────────────────
# 1. 封面 / 页脚
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_cover_title_strips_publisher_and_period() -> None:
    spec, report = polish_deck_spec(_r3e3())
    cover = spec.slides[0]
    # 页脚文本 = metadata.title：出版社/册次/课时前缀去掉，章节号保留
    assert spec.metadata.title == "1.2.1 直线的点斜式方程"
    assert not re.match(r"^(?:苏教|人教|北师大)", cover.title)
    assert "苏教版" not in cover.title and "选择性必修第一册" not in cover.title
    assert "第一课时" not in cover.title
    # 副标题（教材行）只出现一次，且不与标题重复
    lines = [ln for ln in cover.content.split("\n") if ln.strip()]
    assert sum(1 for ln in lines if "选择性必修第一册" in ln) == 1
    assert _norm(cover.title) not in {_norm(ln) for ln in lines}
    assert report["warnings"], "应记录页脚/课件名去前缀的可观测说明"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 去重 / 教师口吻 / 同题回声 / 跨页重复
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_no_duplicate_math_lines_or_question_echo() -> None:
    spec, _ = polish_deck_spec(_r3e3())
    inquiry = _page_title(spec, "探究与思考")
    items = _items(inquiry)
    # 同题两写只剩一条（「…有什么共同特点？」vs「探究直线y=kx+2的共同特点」）
    assert sum(1 for it in items if "共同特点" in it) == 1
    # 公式卡已承载的「斜截式方程：y = kx + b」不再以同名行重复
    assert not any("斜截式方程" in it and "y" in it for it in items)
    # 每个公式只出现一次（y=kx+b / y=kx+2 各一张卡）
    latex_norms = [_norm(el.latex) for el in inquiry.elements if el.role == "formula"]
    assert len(latex_norms) == len(set(latex_norms)) == 2

    # 全 deck：页内可见行零完全重复；跨页元素内容零逐字重复（QA 同口径 ≥6 字）
    for slide in spec.slides:
        norms = [
            _norm(value)
            for el in slide.elements
            for value in (el.text, *el.items)
            if value
        ]
        norms = [n for n in norms if n]
        assert len(norms) == len(set(norms)), f"页内重复：{slide.title}"
    pages_of: dict[str, set[int]] = {}
    for idx, slide in enumerate(spec.slides):
        for el in slide.elements:
            for value in (el.text, *el.items):
                key = _norm(value or "")
                if len(key) >= 6:
                    pages_of.setdefault(key, set()).add(idx)
    assert [k for k, pages in pages_of.items() if len(pages) > 1] == []


def test_r3e3_no_teacher_voice_placeholder_or_jargon() -> None:
    spec, _ = polish_deck_spec(_r3e3())
    visible = "\n".join(_visible_text(spec))
    for banned in (
        "结合已有斜率知识",
        "结合教材开篇问题",
        "引导学生",
        "借助多媒体",
        "教师通过",
        "SourceLocator",
        "教材直连",
        "溯源见备注",
        "（待确认",
        "AI 生成",
    ):
        assert banned not in visible, f"非教学文本上屏：{banned}"
    # 真实教学内容保留（去重不删事实）
    for kept in ("我们已经学习了直线的斜率", "适用条件：直线斜率存在", "b是直线在y轴上的截距"):
        assert kept in visible


# ─────────────────────────────────────────────────────────────────────────────
# 3. 薄页
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_no_thin_pages_after_polish() -> None:
    spec, _ = polish_deck_spec(_r3e3())
    for slide in spec.slides:
        if _is_cover(slide):
            continue
        assert _slide_units(slide) >= 2, f"薄页残留：{slide.title}"
        assert slide.content.strip(), f"空页残留：{slide.title}"


def test_thin_page_merges_into_next_semantic_page() -> None:
    raw = _r3e3().model_dump(mode="python")
    # 目标页只留 1 条 + 清空概念来源 → 无法补到 3 条 → 应并入下一概念/推导页
    raw["slides"][1]["elements"] = [{"role": "bullets", "items": ["理解直线点斜式方程的推导过程"]}]
    raw["slides"][1]["content"] = "理解直线点斜式方程的推导过程"
    raw["teaching_strategy"]["concept_progression"] = []
    source = TeachingDeckSpec.model_validate(raw)
    source_page_count = len(source.slides)
    source_id = source.slides[1].id

    polished, report = polish_deck_spec(source)
    assert report["merged_pages"] == 1
    assert report["removed_pages"] == 0
    assert len(polished.slides) == source_page_count - 1
    assert source_id not in {s.id for s in polished.slides}
    assert all(source_id not in entry.slide_ids for entry in polished.outline)
    # 目标页由语义选择决定（目标/引入优先并入概念/推导页）；只断言内容真实保留，
    # 不把“并入哪一页”写死成另一页的实现细节。
    assert any("理解直线点斜式方程的推导过程" in _items(s) for s in polished.slides)


def test_empty_page_is_removed_and_outline_rebuilt() -> None:
    raw = _r3e3().model_dump(mode="python")
    # 整页只剩教师口吻 → 清理后 0 单元 → 删除（不是近乎空白的页面留在 deck 里）
    raw["slides"][2]["elements"] = [{"role": "bullets", "items": ["结合已有斜率知识，引出本节课"]}]
    raw["slides"][2]["content"] = "结合已有斜率知识，引出本节课"
    source = TeachingDeckSpec.model_validate(raw)
    removed_id = source.slides[2].id

    polished, report = polish_deck_spec(source)
    assert report["removed_pages"] == 1
    assert removed_id not in {s.id for s in polished.slides}
    assert all(removed_id not in entry.slide_ids for entry in polished.outline)
    assert len(polished.slides) == len(source.slides) - 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. 公式
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_no_visible_latex_source_and_formula_has_latex() -> None:
    spec, _ = polish_deck_spec(_r3e3())
    visible = "\n".join(_visible_text(spec))
    assert "$" not in visible
    assert "\\boxed" not in visible
    assert "\\frac" not in visible
    for slide in spec.slides:
        for el in slide.elements:
            if el.role == "formula":
                assert el.latex, f"formula 元素缺 latex：{slide.title}"
                assert "$" not in el.latex and "\\boxed" not in el.latex


def test_formula_source_is_split_into_formula_element() -> None:
    raw = _r3e3().model_dump(mode="python")
    page = raw["slides"][6]  # 例题页（steps 元素）
    page["elements"].append(
        {
            "role": "bullets",
            "items": [r"截距关系：$b = y_0 - k x_0$"],
            "text": None,
            "latex": None,
            "title": None,
            "figure_ref": None,
        }
    )
    page["elements"].append(
        {
            "role": "note",
            "text": r"\boxed{y = kx + b}",
            "items": [],
            "latex": None,
            "title": None,
            "figure_ref": None,
        }
    )
    source = TeachingDeckSpec.model_validate(raw)
    polished, _ = polish_deck_spec(source)
    worked = polished.slides[6]
    visible = "\n".join(_visible_text(polished))
    assert "$" not in visible and "\\boxed" not in visible and "\\frac" not in visible
    # 显示式拆成 formula 元素（latex 规范、标签保留），原始行不再重复
    assert any(
        el.role == "formula" and "y_0" in (el.latex or "") and (el.text or "") == "截距关系"
        for el in worked.elements
    )
    assert not any("截距关系" in it and "y_0" in it for it in _items(worked))


def test_legacy_content_latex_is_converted_to_unicode() -> None:
    raw = _golden_a().model_dump(mode="python")
    raw["slides"][3]["content"] += "\n" + r"直线斜率：$k = \frac{y_2 - y_1}{x_2 - x_1}$"
    source = TeachingDeckSpec.model_validate(raw)
    polished, _ = polish_deck_spec(source)
    content = polished.slides[3].content
    assert "$" not in content and "\\frac" not in content
    assert "k = (y₂ - y₁)/(x₂ - x₁)" in content
    # 集合描述法竖线属于数学事实，清洗不得吃掉
    assert "{x | x 是小于 4 的正整数}" in content


# ─────────────────────────────────────────────────────────────────────────────
# 5. 目标页
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_objectives_page_has_three_real_units() -> None:
    spec, _ = polish_deck_spec(_r3e3())
    objectives = _page_title(spec, "学习目标")
    assert _slide_units(objectives) >= 3
    items = _items(objectives)
    assert any("学习重点" in it for it in items)
    # 真实概念来自 teaching_strategy.concept_progression（不编造）
    for concept in spec.teaching_strategy.concept_progression:
        assert any(concept in it for it in items)


def test_objectives_without_source_is_not_fabricated() -> None:
    raw = _r3e3().model_dump(mode="python")
    raw["slides"][1]["elements"] = [{"role": "bullets", "items": ["理解点斜式方程"]}]
    raw["slides"][1]["content"] = "理解点斜式方程"
    raw["teaching_strategy"]["concept_progression"] = []
    source = TeachingDeckSpec.model_validate(raw)
    polished, report = polish_deck_spec(source)
    assert any("目标页真实内容不足 3 条" in w for w in report["warnings"])
    # 不编造：目标页只剩 1 条（随后按薄页规则并入相邻页）
    assert all(not (s.title == "学习目标" and _slide_units(s) < 2) for s in polished.slides)


# ─────────────────────────────────────────────────────────────────────────────
# 6. 图形
# ─────────────────────────────────────────────────────────────────────────────


def test_r3e3_keeps_existing_real_figure_asset() -> None:
    spec, report = polish_deck_spec(_r3e3())
    assert report["figures_added"] == 0  # 已有图不重复挂
    assert len(spec.assets) == 1
    asset = spec.assets[0]
    assert asset.kind == "MATH_RENDER"
    assert asset.object_key.startswith("math-figures/")
    referencing = [s for s in spec.slides if asset.id in s.assets]
    assert len(referencing) == 1


def test_line_deck_without_assets_gets_one_real_math_render(monkeypatch) -> None:
    raw = _r3e3().model_dump(mode="python")
    raw["assets"] = []
    for slide in raw["slides"]:
        slide["assets"] = []
    source = TeachingDeckSpec.model_validate(raw)
    stored: list[str] = []

    def _fake_store(asset) -> bool:
        stored.append(asset.id)
        return True

    monkeypatch.setattr(math_figures, "ensure_asset_stored", _fake_store)
    polished, report = polish_deck_spec(source)

    assert report["figures_added"] == 1
    assert stored and stored[0] in {a.id for a in polished.assets}
    assert len(polished.assets) == 1
    asset = polished.assets[0]
    assert asset.kind == "MATH_RENDER"
    assert asset.object_key.startswith("math-figures/v5/line__")
    # 真图（服务端确定性渲染）而不是空引用
    png = math_figures.asset_bytes(asset)
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n")
    hosts = [s for s in polished.slides if asset.id in s.assets]
    assert len(hosts) == 1
    host = hosts[0]
    assert host.type == "image-focus" and host.layout_hint == "image-focus"
    assert "推导" in host.title or "概念" in host.title or "引入" in host.title


def test_figure_storage_failure_degrades_honestly(monkeypatch) -> None:
    raw = _r3e3().model_dump(mode="python")
    raw["assets"] = []
    for slide in raw["slides"]:
        slide["assets"] = []
    source = TeachingDeckSpec.model_validate(raw)

    monkeypatch.setattr(math_figures, "ensure_asset_stored", lambda asset: False)
    polished, report = polish_deck_spec(source)

    assert report["figures_added"] == 0
    assert polished.assets == []
    assert all(not s.assets for s in polished.slides)
    assert any("落存储失败" in w for w in report["warnings"])
    # 不阻断：deck 本身仍合法
    TeachingDeckSpec.model_validate(polished.model_dump(mode="python"))


def test_sequence_deck_without_assets_gets_real_sequence_figure(monkeypatch) -> None:
    """R22：数列课件此前零图（只支持直线/二次/幂函数）→ 现在补真实数列点列图。"""
    raw = _r3e3().model_dump(mode="python")
    raw["metadata"]["title"] = "4.2.2 等差数列的前 n 项和公式"
    raw["assets"] = []
    for idx, slide in enumerate(raw["slides"]):
        slide["assets"] = []
        slide["math_object"] = None
        if idx == 3:
            slide["type"] = "derivation"
            slide["layout_hint"] = "derivation"
            slide["title"] = "等差数列前 n 项和推导"
            slide["content"] = "首项 a_1=1，公差 d=2，前 6 项和\nS_n = n(a_1+a_n)/2"
            slide["elements"] = [{"role": "steps", "items": ["写出前 6 项", "倒序相加", "得到 S_n 公式"]}]
    raw["teaching_strategy"] = {
        "intro": "从高斯配对求和引入等差数列前 n 项和",
        "concept_progression": ["等差数列", "倒序相加"],
        "derivation_chain": ["S_n = n(a_1+a_n)/2"],
        "checks_for_understanding": [],
    }
    source = TeachingDeckSpec.model_validate(raw)
    monkeypatch.setattr(math_figures, "ensure_asset_stored", lambda asset: True)

    polished, report = polish_deck_spec(source)

    assert report["figures_added"] == 1
    asset = polished.assets[0]
    assert asset.kind == "MATH_RENDER"
    assert asset.object_key.startswith("math-figures/v5/sequence__")
    png = math_figures.asset_bytes(asset)
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n")
    hosts = [s for s in polished.slides if asset.id in s.assets]
    assert len(hosts) == 1
    assert hosts[0].type == "image-focus"
    assert "数列" in hosts[0].title


# ─────────────────────────────────────────────────────────────────────────────
# 7. golden fixture：不删除唯一事实 + 幂等
# ─────────────────────────────────────────────────────────────────────────────


def test_golden_fixture_keeps_unique_facts_and_pages() -> None:
    source = _golden_a()
    polished, report = polish_deck_spec(source)
    assert len(polished.slides) == len(source.slides) == 8
    assert report["removed_duplicates"] == 0
    assert report["merged_pages"] == 0 and report["removed_pages"] == 0
    titles = [s.title for s in polished.slides]
    assert titles == [s.title for s in source.slides]
    joined = "\n".join(
        [*_visible_text(polished), *[s.content for s in polished.slides]]
    )
    for fact in (
        "集合",
        "确定性、互异性、无序性",
        "列举法",
        "描述法",
        "{x | x 是小于 4 的正整数}",
        "A={x∈Z | -2≤x<3}",
        "x^2-1=0",
        "数轴上的集合",
    ):
        assert fact in joined, f"唯一事实被删除/改写：{fact}"
    # 资产引用不被误删
    assert {a.id for a in polished.assets} == {"asset-fig-1", "asset-svg-1"}


def test_polish_is_idempotent_and_reference_safe() -> None:
    once, _ = polish_deck_spec(_r3e3())
    twice, report = polish_deck_spec(once)
    assert once.model_dump(mode="json") == twice.model_dump(mode="json")
    assert report["removed_duplicates"] == 0
    assert report["merged_pages"] == 0 and report["removed_pages"] == 0
    # 引用完整性（Pydantic 校验已覆盖，这里显式断言关键集合）
    slide_ids = [s.id for s in twice.slides]
    assert len(slide_ids) == len(set(slide_ids))
    for entry in twice.outline:
        assert set(entry.slide_ids) <= set(slide_ids)
    for key in twice.speaker_notes:
        assert key in set(slide_ids)


# ─────────────────────────────────────────────────────────────────────────────
# 8. 接线：project_specs 落库/返回的都是 polished 版本
# ─────────────────────────────────────────────────────────────────────────────


def _rh(body: dict) -> str:
    return request_hash_of("POST", "/specs", body)


def _profile_ref() -> dict:
    profile = VersionedClassLearningProfile(
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
    return {
        "profile_id": str(profile.profile_id),
        "profile_revision": 1,
        "computed_at": profile.freshness.computed_at.isoformat(),
        "expires_at": None,
        "stale": False,
        "coverage_ratio": 1.0,
        "warnings": [],
    }


def _line_lesson_body(ctx) -> dict:
    """点斜式课蓝图（含教师口吻 / 重复公式 / 2 条目标 → 触发 polish 全部规则）。"""
    return {
        "schema_version": "0.1.0",
        "context_snapshot_hash": ctx.snapshot_hash,
        "title": "苏教版选择性必修第一册1.2.1 直线的点斜式方程 第一课时",
        "objectives": [
            {"text": "理解直线点斜式方程的推导过程，明确其形式与适用条件", "source_ref": None},
            {"text": "能运用点斜式、斜截式方程求解直线方程，解决相关问题", "source_ref": None},
        ],
        "prior_knowledge": [{"text": "直线斜率的定义与计算公式", "source_ref": None}],
        "concepts": [
            {"text": "直线的点斜式方程", "source_ref": "locator/chunk/a"},
            {"text": "直线的斜截式方程", "source_ref": "locator/chunk/b"},
        ],
        "theorems": [
            {"text": "本节结论：直线点斜式方程的推导过程与形式", "source_ref": "locator/chunk/c"},
            {"text": "本节结论：斜截式方程的形式与截距的几何意义", "source_ref": "locator/chunk/d"},
        ],
        "examples": [],
        "inquiries": [{"text": "探究直线y=kx+2的共同特点", "source_ref": None}],
        "design_intent": [],
        "key_points": [{"text": "直线点斜式方程的推导过程与形式", "source_ref": "locator/chunk/c"}],
        "difficult_points": [{"text": "理解点斜式方程的适用条件与直线方程的意义", "source_ref": None}],
        "lesson_flow": [
            {
                "stage": "introduction",
                "title": "课程引入",
                "duration_minutes": 5,
                "items": [{"text": "结合已有斜率知识，引出本节课对直线方程的学习", "source_ref": None}],
                "board_notes": [
                    {"text": r"直线斜率公式：$k = \frac{y_2 - y_1}{x_2 - x_1}$", "source_ref": None}
                ],
            },
            {
                "stage": "new_content",
                "title": "点斜式方程推导",
                "duration_minutes": 20,
                "items": [{"text": "结合教材开篇问题，一步步推导点斜式方程", "source_ref": None}],
                "board_notes": [
                    {"text": r"直线点斜式方程：$y - y_1 = k(x - x_1)$", "source_ref": None},
                    {"text": "适用条件：直线斜率存在", "source_ref": None},
                ],
            },
            {
                "stage": "new_content",
                "title": "探究与思考",
                "duration_minutes": 10,
                "items": [{"text": "思考点斜式方程变形的几何意义", "source_ref": None}],
                "board_notes": [
                    {"text": r"斜截式方程：$y = kx + b$", "source_ref": None},
                    {"text": r"$b$是直线在$y$轴上的截距", "source_ref": None},
                ],
            },
            {
                "stage": "summary",
                "title": "课堂小结",
                "duration_minutes": 10,
                "items": [],
                "board_notes": [{"text": "课后作业：教材习题1.2第1-5题", "source_ref": None}],
            },
        ],
        "warnings_ack": [],
    }


async def test_projection_persists_polished_deck(db, service: TeacherPrepService, adapter, members) -> None:
    """service.project_specs 的返回与落库都必须是 polished 版本（不是仅内存整形）。"""
    await seed_synthetic_textbook(db)
    ctx = await build(db, selection(lesson_scope="第一课时"))
    assert ctx is not None
    members.set(CLASS_A, user_id=TEACHER)
    created = await service.create_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
        idempotency_key="polish-0001", request_hash=_rh({"class_id": CLASS_A}),
    )
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key="polish-0002",
        request_hash=_rh({"bp": 1}), body=_line_lesson_body(ctx), title="v1",
    )
    confirmed = await service.confirm_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=1,
        idempotency_key="polish-0003", request_hash=_rh({"cf": 1}),
    )
    result = await service.project_specs(
        session=db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], blueprint_id=prop["blueprint_id"],
        expected_revision=confirmed["revision"], context=ctx, profile_ref=_profile_ref(),
        lineage=ProjectionRunLineage(model="polish-test"),
        kinds=("deck",),
        idempotency_key="polish-0004", request_hash=_rh({"v": 1}),
    )
    deck = TeachingDeckSpec.model_validate(result["results"]["deck"]["spec"])
    deck_id = uuid.UUID(result["results"]["deck"]["spec_id"])
    stored_row = (
        await db.execute(select(PptV2DeckSpecRecord).where(PptV2DeckSpecRecord.id == deck_id))
    ).scalar_one()
    stored = TeachingDeckSpec.model_validate(stored_row.spec)

    for spec in (deck, stored):
        assert spec.metadata.title == "1.2.1 直线的点斜式方程"
        visible = "\n".join(_visible_text(spec))
        assert "结合已有斜率知识" not in visible
        assert "结合教材开篇问题" not in visible
        assert "SourceLocator" not in visible
        assert "$" not in visible and "\\boxed" not in visible
        objectives = next((s for s in spec.slides if s.title == "学习目标"), None)
        assert objectives is not None and _slide_units(objectives) >= 3
        for slide in spec.slides:
            if not _is_cover(slide):
                assert _slide_units(slide) >= 2, f"薄页落库：{slide.title}"
    assert deck.model_dump(mode="json") == stored.model_dump(mode="json")


def test_duplicate_visual_assets_are_deduped_and_adjacent_visual_pages_downgraded() -> None:
    """8200 版式序列可行性：同一资产只留一页；相邻 image-focus 降级但不删内容。"""
    raw = _r3e3().model_dump(mode="python")
    # 复现真实缺陷：投影器把同一张直线图同时挂到相邻两页，且两页都是 image-focus
    asset_id = raw["slides"][3]["assets"][0]
    raw["slides"][3]["type"] = "image-focus"
    raw["slides"][3]["layout_hint"] = "image-focus"
    raw["slides"][4]["assets"] = [asset_id]
    raw["slides"][4]["type"] = "image-focus"
    raw["slides"][4]["layout_hint"] = "image-focus"
    source = TeachingDeckSpec.model_validate(raw)

    spec, report = polish_deck_spec(source)
    seen: dict[str, int] = {}
    for slide in spec.slides:
        for aid in slide.assets:
            seen[aid] = seen.get(aid, 0) + 1
    assert all(count == 1 for count in seen.values()), seen
    types = [s.type for s in spec.slides if not _is_cover(s)]
    assert all(not (types[i] == "image-focus" and types[i + 1] == "image-focus")
               for i in range(len(types) - 1)), types
    assert report["visual_pages_downgraded"] >= 1
    # 降级只改版式，不丢页/不丢标题
    assert len(spec.slides) == len(source.slides)
    assert [s.title for s in spec.slides] == [s.title for s in source.slides]


def test_worked_example_and_practice_keep_full_problem_line() -> None:
    """enrich 后题干只藏在 knowledge_card；polish 必须保留原「例/问题」完整题面。"""
    spec, _ = polish_deck_spec(_r3e3())
    worked = next(s for s in spec.slides if s.type == "worked-example")
    practice = next(s for s in spec.slides if s.type == "practice")
    assert worked.content.splitlines()[0].startswith("例1 已知直线过点(1,2)")
    assert practice.content.splitlines()[0].startswith("例2 已知直线在y轴上的截距是4")
