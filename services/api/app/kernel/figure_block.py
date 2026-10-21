"""F13 figure 事件契约（kernel/figure_block.py，对标 graph_block.py）

契约形态（skill 产出 {"type":"figure","data":{...}}，gateway 统一校验后转发/落库）：
{
  "step_no": 2,                 # 可选：对应参考解步骤（1 基）
  "caption": "作出函数图像",      # 可选：图形说明（≤80 字）
  "frames": [                   # 1~6 帧，累计式渐进揭示
    {"data_uri": "data:image/svg+xml;base64,...", "label": "坐标系与曲线"},
    ...
  ],
  "figure_params": {...}        # 可选：完整渲染参数（调试/审计，前端不渲染）
}

纪律：skill 产出不可信——非法 figure 降级丢弃并记日志，绝不允许 500。
尺寸纪律：data_uri 单帧 ≤ _MAX_DATA_URI_BYTES（典型 460×330 SVG ≈ 5KB），
frames ≤ _MAX_FRAMES，防 LLM/渲染器异常产出撑爆 SSE 与信封。
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = structlog.get_logger()

MAX_FRAMES = 6
MAX_DATA_URI_BYTES = 200_000
DATA_URI_PREFIX = "data:image/svg+xml;base64,"
MAX_CAPTION_CHARS = 80


class FigureFrame(BaseModel):
    """单帧：SVG data URI + 帧说明"""

    model_config = ConfigDict(strict=True)

    data_uri: str
    label: str = ""

    @field_validator("data_uri")
    @classmethod
    def _check_data_uri(cls, v: str) -> str:
        if not v.startswith(DATA_URI_PREFIX):
            raise ValueError(f"data_uri 前缀必须是 {DATA_URI_PREFIX!r}")
        if len(v) > MAX_DATA_URI_BYTES:
            raise ValueError(f"data_uri 超长（>{MAX_DATA_URI_BYTES} 字节）")
        return v


class FigureBlock(BaseModel):
    """figure 事件载荷契约"""

    model_config = ConfigDict(strict=True)

    step_no: int | None = Field(default=None, ge=1)
    caption: str = Field(default="", max_length=MAX_CAPTION_CHARS)
    frames: list[FigureFrame] = Field(min_length=1, max_length=MAX_FRAMES)
    figure_params: dict[str, Any] | None = None


def validate_figure_block(payload: Any) -> dict[str, Any] | None:
    """校验 figure 契约：合法返回规范化 dict，非法记日志返回 None。

    绝不抛异常——坏 figure 只丢这一块，不影响 SSE 主链路与信封落库。
    """
    if not isinstance(payload, dict):
        logger.warning(
            "figure_block.invalid", reason="not_a_dict", got=type(payload).__name__
        )
        return None
    try:
        block = FigureBlock.model_validate(payload)
    except ValidationError as e:
        logger.warning(
            "figure_block.invalid", reason="schema_mismatch", error=str(e)[:200]
        )
        return None
    data = {
        "frames": [
            {"data_uri": fr.data_uri, "label": fr.label} for fr in block.frames
        ]
    }
    if block.step_no is not None:
        data["step_no"] = block.step_no
    if block.caption:
        data["caption"] = block.caption
    if block.figure_params is not None:
        data["figure_params"] = block.figure_params
    return data
