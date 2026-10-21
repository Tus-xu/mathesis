# coding: utf-8
"""AI 充实后的确定性自动配图（真实 MATH_RENDER 资产，不是装饰图）。

用户实测：点「AI 充实」后页面只换了样式，整份课件仍零图片。根因是 enrich 只产出
文本元素，且图形候选只覆盖直线/二次/幂函数。本模块在 enrich 落库后，按同一份
``deck_polish.plan_math_figure`` 规划**最多 3 张**与课题相关的真实图形（数列/集合/函数/...），
通过既有 T24 提案通道（add_asset + set_slide）写入新 revision。

边界：
- 已达配图上限 → no-op（不重复配图）；
- 课题无图形族 / 无目标页 / 存储失败 → no-op + warnings，不伪造图片；
- 只改目标页的 assets/type/layout_hint，不改正文/教学事实。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ppt_v2.service import spec_edit
from app.teacher_prep.specs import deck_polish


async def ensure_deck_figure(
    session: AsyncSession,
    deck_id: str,
    *,
    actor_id: str | None = None,
    refs: dict[str, Any] | None = None,
    max_figures: int = deck_polish.MAX_DECK_FIGURES,
) -> dict[str, Any] | None:
    """为缺少真实图形的 deck 追加相关配图（默认最多 3 张）。"""
    added: list[dict[str, Any]] = []
    warnings: list[str] = []
    revision: int | None = None

    for _ in range(max(1, max_figures)):
        row = await spec_edit.load_deck_row(session, deck_id)
        spec = spec_edit.load_spec(row)
        plan = deck_polish.plan_math_figure(spec, max_figures=max_figures)
        if plan is None:
            break
        warnings.extend(plan.get("warnings") or [])
        asset = plan.get("asset")
        target = plan.get("target")
        if asset is None or target is None:
            break
        operations = deck_polish.figure_patch_operations(plan)
        if not operations:
            break
        proposal = await spec_edit.create_proposal(
            session,
            deck_id,
            expected_revision=row.revision,
            summary=f"自动配图：{plan['kind']} → 「{target.title}」",
            source="regen",
            operations=operations,
            instruction="deck_auto_figure",
            refs=refs,
            actor_id=actor_id,
        )
        applied = await spec_edit.apply_proposal_by_id(
            session, deck_id, proposal["patch_id"], expected_revision=row.revision, actor_id=actor_id
        )
        revision = applied["revision"]
        added.append(
            {
                "kind": plan["kind"],
                "asset_id": asset.id,
                "target_slide_id": target.id,
                "target_title": target.title,
                "patch_id": proposal["patch_id"],
            }
        )

    if not added:
        return {"changed": False, "warnings": warnings} if warnings else None
    return {
        "changed": True,
        "figures_added": len(added),
        "figures": added,
        "revision": revision,
        "warnings": warnings,
    }


__all__ = ["ensure_deck_figure"]
