"""显式依赖规则：计算一次 patch 的影响面（R10）。

铁律：标题/主题/教材 section 变化的实际影响面必须由本文件显式计算，
不得默认「全 deck 重生成」。规则按操作分类、可解释（rationale），
供 Director/QA 只重算受影响的 slide，杜绝整册重渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ppt_v2.domain.spec import TeachingDeckSpec

# 教学策略变更影响的页类型（引入/概念/小结页承载策略语义）
_STRATEGY_SENSITIVE_TYPES: frozenset[str] = frozenset({"introduction", "concept", "summary"})


@dataclass(frozen=True)
class AffectedSlideSet:
    """一次 patch 的影响面（机器可读 + 人可读解释）。"""

    slide_ids: frozenset[str] = field(default_factory=frozenset)
    layout_affected: frozenset[str] = field(default_factory=frozenset)
    content_affected: frozenset[str] = field(default_factory=frozenset)
    assets_affected: frozenset[str] = field(default_factory=frozenset)
    whole_deck_layout: bool = False  # 仅版式级全 deck（换主题/标题带），内容不变
    rationale: str = ""


def _all_slide_ids(spec: TeachingDeckSpec) -> frozenset[str]:
    return frozenset(s.id for s in spec.slides)


def compute_affected(spec: TeachingDeckSpec, *, op: str, target: str | None, field: str | None) -> AffectedSlideSet:
    """根据单一操作（op/target/field）计算影响面。

    patch 层逐操作调用本函数并对 op 冲突做防御（op 非法已由 PatchOp 校验拦截）；
    本函数保持确定性、可单测。
    """
    all_ids = _all_slide_ids(spec)

    if op == "set_metadata":
        # 标题等元数据只影响版式带（标题栏/主题氛围），教学内容零变化
        return AffectedSlideSet(
            slide_ids=all_ids,
            layout_affected=all_ids,
            rationale=f"元数据({field or 'deck'})变更影响全部页的版式带，不触碰教学内容",
        )

    if op == "set_theme":
        # 换主题 = 全部 layout 重排，教学内容、source refs 全部不变
        return AffectedSlideSet(
            slide_ids=all_ids,
            layout_affected=all_ids,
            whole_deck_layout=True,
            rationale="换主题影响全部 layout（版式/配色/字体），教学内容不变",
        )

    if op == "add_slide":
        assert target is not None
        return AffectedSlideSet(
            slide_ids=frozenset({target}),
            content_affected=frozenset({target}),
            rationale=f"新增 slide {target}",
        )

    if op == "remove_slide":
        assert target is not None
        return AffectedSlideSet(
            slide_ids=frozenset({target}),
            content_affected=frozenset({target}),
            rationale=f"删除 slide {target}（其 speaker_note 一并清理）",
        )

    if op == "set_slide":
        assert target is not None
        if target not in all_ids:
            return AffectedSlideSet(rationale=f"slide {target} 不存在，无影响")
        content_fields = {"content", "teaching_goal", "source_refs", "assets", "type", "elements"}
        layout_fields = {"title", "layout_hint", "visual_intent"}
        if field in content_fields:
            return AffectedSlideSet(
                slide_ids=frozenset({target}),
                content_affected=frozenset({target}),
                rationale=f"slide[{target}].{field} 教学内容变更",
            )
        if field in layout_fields:
            return AffectedSlideSet(
                slide_ids=frozenset({target}),
                layout_affected=frozenset({target}),
                rationale=f"slide[{target}].{field} 版式语义变更",
            )
        # speaker_note / status / qa_status / math_object：无需重生成内容或布局
        return AffectedSlideSet(slide_ids=frozenset({target}), rationale=f"slide[{target}].{field} 仅元数据变更")

    if op == "move_slide":
        assert target is not None
        if target not in all_ids:
            return AffectedSlideSet(rationale=f"slide {target} 不存在，无影响")
        return AffectedSlideSet(
            slide_ids=all_ids,
            layout_affected=all_ids,
            rationale=f"页序调整（{target} 移动）影响全部页码与演示顺序，教学内容不变",
        )

    if op == "set_outline":
        # 大纲重排只影响演示顺序与分组，slide 内容不变
        return AffectedSlideSet(
            rationale="大纲条目重排影响演示顺序；slide 内容不变",
        )

    if op == "set_strategy":
        sensitive = frozenset(s.id for s in spec.slides if s.type in _STRATEGY_SENSITIVE_TYPES)
        return AffectedSlideSet(
            slide_ids=sensitive,
            content_affected=sensitive,
            rationale="教学策略变更影响引入/概念/小结类页的内容生成依据",
        )

    if op == "set_selected_examples":
        selected = {e.problem_id for e in spec.selected_examples}
        affected = frozenset(
            s.id
            for s in spec.slides
            if any(r.rsplit("/", 1)[-1] in selected for r in (s.source_refs or []))
        )
        return AffectedSlideSet(
            slide_ids=affected,
            content_affected=affected,
            rationale="选中例题变更影响 source_refs 关联该例题的页",
        )

    if op.startswith("asset_"):
        sub = op.removeprefix("asset_")
        assert target is not None
        if sub == "add":
            return AffectedSlideSet(rationale=f"新增 asset {target}（尚未被任何 slide 引用，无影响）")
        referencing = frozenset(s.id for s in spec.slides if target in (s.assets or []))
        if sub == "remove":
            return AffectedSlideSet(
                slide_ids=referencing,
                content_affected=referencing,
                rationale=f"删除 asset {target}，引用它的页需要重生成内容",
            )
        # set_asset（rights/evidence）：引用页的渲染许可变化
        return AffectedSlideSet(
            slide_ids=referencing,
            layout_affected=referencing,
            assets_affected=frozenset({target}),
            rationale=f"asset {target} 元数据变更，影响引用页的渲染许可",
        )

    return AffectedSlideSet(rationale=f"op={op} 无既有依赖规则（保守返回空影响面）")
