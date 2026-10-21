"""ppt_v2.qa：逐页视觉 QA（读真实 PPTX + 真实渲染 PNG）。

为什么独立子包：QA 必须与生成侧（builder / layout_resolver / visual_planning）解耦——
同源逻辑看不见自己的错排；本包只依赖 python-pptx / Pillow / fontTools cmap。
对外只暴露冻结接口与冻结常量（8100 主干 `GET /decks/{id}/qa`、`POST /qa/retry` 直接调用）。
"""

from __future__ import annotations

from app.ppt_v2.qa.detector import (
    DETECTOR_VERSION,
    INFO_KINDS,
    P0_KINDS,
    P1_KINDS,
    RUBRIC,
    SCHEMA,
    detect_deck_qa,
    detect_page_qa,
)

__all__ = [
    "DETECTOR_VERSION",
    "INFO_KINDS",
    "P0_KINDS",
    "P1_KINDS",
    "RUBRIC",
    "SCHEMA",
    "detect_deck_qa",
    "detect_page_qa",
]
