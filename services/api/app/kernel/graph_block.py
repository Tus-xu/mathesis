"""F11 动态几何/函数图像 graph block 契约（M2）

契约形态：{"type": "graph", "engine": "jsxgraph", "schema": {...}}
- engine：渲染引擎，当前仅支持 "jsxgraph"（渲染由前端 KaTeX+JSXGraph 完成）
- schema：JSXGraph 场景描述（点/线/函数曲线等），结构由前端渲染器解释，后端不约束内部形状

纪律：skill 产出不可信——非法 graph 降级丢弃并记日志，绝不允许 500。
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError

logger = structlog.get_logger()


class GraphBlock(BaseModel):
    """graph block 契约（engine 固定 jsxgraph；schema 为任意场景描述 dict，必须存在）"""

    engine: Literal["jsxgraph"]
    # 契约键名为 "schema"；字段带下划线避开 BaseModel.schema 方法名冲突
    schema_: dict[str, Any] = Field(alias="schema")


def validate_graph_block(payload: Any) -> dict[str, Any] | None:
    """校验 graph 契约：合法返回规范化 dict（{"engine", "schema"}），非法记日志返回 None。

    绝不抛异常——坏 graph 只丢这一块，不影响 SSE 主链路与信封落库。
    """
    if not isinstance(payload, dict):
        logger.warning(
            "graph_block.invalid", reason="not_a_dict", got=type(payload).__name__
        )
        return None
    try:
        block = GraphBlock.model_validate(payload)
    except ValidationError as e:
        logger.warning(
            "graph_block.invalid", reason="schema_mismatch", error=str(e)[:200]
        )
        return None
    return {"engine": block.engine, "schema": block.schema_}
