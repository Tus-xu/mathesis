"""R21.1 Visual Planning Layer — 引擎中立的视觉规划（8200 侧）。

分层（DECISIONS/任务书 §5 铁律：TeachingDeckSpec 仍是内容事实源，本层只决定
"如何教学表达与视觉呈现"，不篡改教材事实/例题/数学结论）：

TeachingDeckSpec
    → DeckNarrativePlan      (narrative.py, deck 级)
    → SlideVisualIntent[]    (intent.py, 每页)
    → LayoutSequencePlan     (sequence.py, family 选择 + 多样性控制)
    → VisualAssetPlan        (assets.py, 数学视觉判定)
    → ResolvedVisualPlan     (resolve.py, 可序列化；禁止 x/y/w/h)
    → PresentationEngine Adapter（坐标仅在 engine compiler/layout resolver 产生）

坐标零容忍：本包任何输出不得包含 x/y/w/h 键（qa_deck 会扫描）。
"""
from app.visual_planning._meta import VERSION
from app.visual_planning.resolve import build_visual_plan

__all__ = ["build_visual_plan", "VERSION"]
