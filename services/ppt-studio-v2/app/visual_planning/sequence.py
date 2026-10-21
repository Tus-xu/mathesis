"""LayoutSequencePlan + Diversity Controller（deck 级序列控制，禁止随机布局）。

确定性规则（无 RNG）：
- 硬约束：同 family 连续 ≤2 页；教学阶段（narrative_phase）切换处必须换族；
  连续 text-only 族 ≤2；连续 3 页无视觉锚后第 4 页必须选含视觉槽位的族（候选允许时）。
- 软偏好：相邻页不同族（在候选顺序中体现，候选首选项冲突时顺延次优）。
- 变化由 上一页 role + 当前页 role + 下一页 role 共同决定（候选与约束均以 role 为输入），
  绝不为"多样化"随机换布局。
- 无可用候选 → **确定性 DFS 有界回溯**（回溯到上一个已定页改选次优，并清空下游页的
  tried 状态）；搜索步数超上限仍无解 → PlanningFailure（不得静默退化为重复布局）。
"""
from __future__ import annotations

from typing import Any

from app.visual_planning.families import REGISTRY, family

MAX_SAME_FAMILY_RUN = 2
MAX_TEXT_ONLY_RUN = 2
MAX_VISUAL_DROUGHT = 3
# DFS 搜索步数上限（防御病态输入；n≤12、每页候选≤3 的真实 deck 远不会触顶）
MAX_SEARCH_STEPS = 200_000


class PlanningFailure(Exception):
    """多样性约束无法满足（deck QA 必失败，禁止带病出计划）。"""


def plan_sequence(intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每页选定 layout_family；返回回填后的 intents（continuity/contrast 已写入）。

    确定性 DFS 有界回溯：
    - 从第 0 页起逐页按候选顺序选族；
    - 某页无可用候选 → 回溯到上一个已定页，把它当前选择记入 tried 并改选次优，
      同时**清空下游页的 tried 状态**（下游页此前的失败是在「旧前缀」下产生的，
      前缀已变 → 其 tried 必须重置，否则搜索被永久污染、无法找到可行解，R8-F F4）；
    - 搜索步数超上限仍无解 → PlanningFailure。
    """
    n = len(intents)
    chosen: list[str | None] = [None] * n
    tried: list[set[str]] = [set() for _ in range(n)]
    i = 0
    steps = 0
    while i < n:
        steps += 1
        if steps > MAX_SEARCH_STEPS:
            raise PlanningFailure(
                f"layout sequence 搜索步数超过上限 {MAX_SEARCH_STEPS}（deck 页数 {n}）"
            )
        fam = _pick(i, intents, chosen, tried[i])
        if fam is not None:
            chosen[i] = fam
            i += 1
            if i < n:
                tried[i] = set()  # 进入新页：前缀已定，重置其 tried
            continue
        # 当前页无可用候选 → 回溯到上一个已定页
        if i == 0:
            raise PlanningFailure(
                f"layout sequence 无可行解：第 1 页即无可用候选（deck 页数 {n}）"
            )
        tried[i] = set()  # 本页将在新前缀下重新评估
        i -= 1
        tried[i].add(chosen[i])  # 上一页当前选择判为失败 → 改选次优
        chosen[i] = None
    for idx, intent in enumerate(intents):
        intent["_chosen_family"] = chosen[idx]
    _fill_continuity(intents, chosen)
    return intents


def _pick(i: int, intents: list[dict[str, Any]], chosen: list[str | None],
          tried: set[str]) -> str | None:
    intent = intents[i]
    prev = chosen[i - 1] if i > 0 else None
    prev2 = chosen[i - 2] if i > 1 else None

    # 回溯感知：本页在当前前缀下「已尝试且失败」的候选不再重复选择（防死循环）
    candidates = [c for c in intent["layout_family_candidates"] if c not in tried]
    if not candidates:
        return None  # 全部候选均失败 → 交给上层回溯向前换页

    # 上下文共同决定：候选顺序按 (当前 role 候选) 基础上，
    # 若下一页 role 与当前相同且首选族相同 → 当前页优先让出首选，形成轮换
    if i + 1 < len(intents) and intents[i + 1]["teaching_role"] == intent["teaching_role"] \
            and len(candidates) > 1:
        next_cands = intents[i + 1]["layout_family_candidates"]
        if next_cands and next_cands[0] == candidates[0]:
            candidates = [candidates[1], candidates[0]] + candidates[2:]

    text_run = _run_len(chosen, i, lambda f: REGISTRY[f].is_text_only)
    drought = _visual_drought(intents, chosen, i)

    for fam_id in candidates:
        if fam_id not in REGISTRY:
            continue
        f = family(fam_id)
        if prev and fam_id == prev and prev2 == prev:
            continue  # 连续 3 页同族（已有 2 页）→ 违反 ≤2
        if (
            prev and fam_id == prev
            and intent["narrative_phase"] != intents[i - 1]["narrative_phase"]
        ):
            continue  # 教学阶段切换处必须换族
        if f.is_text_only and text_run >= MAX_TEXT_ONLY_RUN:
            continue  # text-only streak 上限
        if drought >= MAX_VISUAL_DROUGHT and not f.has_visual_slot and any(
            family(c).has_visual_slot for c in candidates if c in REGISTRY
        ):
            continue  # 视觉干旱：必须选含视觉锚的族（存在此类候选时）
        return fam_id
    return None


def _run_len(chosen: list[str | None], i: int, pred) -> int:
    n = 0
    j = i - 1
    while j >= 0 and chosen[j] is not None and pred(chosen[j]):
        n += 1
        j -= 1
    return n


def _visual_drought(intents: list[dict[str, Any]], chosen: list[str | None], i: int) -> int:
    n = 0
    j = i - 1
    while j >= 0:
        fam_ok = chosen[j] is not None and family(chosen[j]).has_visual_slot
        if fam_ok or intents[j]["visual_asset_need"].get("bound_asset_ids"):
            break
        n += 1
        j -= 1
    return n


def _fill_continuity(intents: list[dict[str, Any]], chosen: list[str | None]) -> None:
    for i, intent in enumerate(intents):
        prev = chosen[i - 1] if i > 0 else None
        cur = chosen[i]
        if prev is None:
            intent["continuity_with_previous"] = "开篇建立视觉基调"
            intent["contrast_with_previous"] = ""
            continue
        phase_switch = intent["narrative_phase"] != intents[i - 1]["narrative_phase"]
        if phase_switch:
            intent["continuity_with_previous"] = "教学阶段切换：kicker 阶段标签承接"
            intent["contrast_with_previous"] = "换族形成视觉转场"
        elif cur != prev:
            intent["continuity_with_previous"] = "同阶段推进：构图变化维持注意力"
            intent["contrast_with_previous"] = "同族语义、不同骨架"
        else:
            intent["continuity_with_previous"] = "同族延续：内容递进"
            intent["contrast_with_previous"] = ""
