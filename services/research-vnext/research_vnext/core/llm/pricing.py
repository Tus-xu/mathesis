"""RV09 · 用量 → 成本（**绝不把"未知"算成 0**）。

为什么单独成模块
----------------
`cost_usd` 一旦只是个数字，`0` 与"不知道"就无法区分，于是：
- 预算告警永远不触发（因为看起来免费）；
- 事后无法解释"这轮为什么这么贵"（因为基线是错的 0）。

因此本模块的输出恒为 `(usd, basis)`：
- `usd is None` ⇒ **不知道**，`basis` 说明为什么（`unknown-model` / `no-usage`）；
- `usd == 0.0` ⇒ **确实免费**（例如本地模型，或供应商未计费）。

价格表是**配置**，不是常量：单价会变，写死在代码里就一定要改代码才能修正历史账。
默认值刻意留空（`PRICE_TABLE = {}`），因此默认结论是"不知道多少钱"。
运维用 `RESEARCH_LLM_PRICE_TABLE`（JSON）登记真实单价：

    RESEARCH_LLM_PRICE_TABLE={"spark-x2.5-4b": {"in": 0.0000005, "out": 0.000002}}
价格单位是 **USD / token**。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

ENV_PRICE_TABLE = "RESEARCH_LLM_PRICE_TABLE"

#: 空表是**刻意的默认**：没有登记单价时，诚实的答案是"未知"。
PRICE_TABLE: dict[str, dict[str, float]] = {}

BASIS_TABLE = "table"
BASIS_LOCAL_FREE = "local-free"
BASIS_UNKNOWN_MODEL = "unknown-model"
BASIS_NO_USAGE = "no-usage"
BASIS_INVALID_TABLE = "invalid-price-table"


@dataclass(frozen=True)
class Cost:
    usd: float | None
    basis: str

    def as_dict(self) -> dict[str, Any]:
        return {"usd": self.usd, "basis": self.basis}


def _load_table() -> tuple[dict[str, dict[str, float]], str | None]:
    raw = os.getenv(ENV_PRICE_TABLE, "").strip()
    if not raw:
        return PRICE_TABLE, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}, BASIS_INVALID_TABLE
    if not isinstance(parsed, dict):
        return {}, BASIS_INVALID_TABLE
    out: dict[str, dict[str, float]] = {}
    for model, prices in parsed.items():
        if not isinstance(prices, dict):
            continue
        try:
            out[str(model)] = {"in": float(prices.get("in", 0.0)), "out": float(prices.get("out", 0.0))}
        except (TypeError, ValueError):
            continue
    return out, None


def price_table() -> dict[str, dict[str, float]]:
    table, _err = _load_table()
    return table


def estimate_cost(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    provider: str = "",
) -> Cost:
    """估算一次调用的成本。返回 `Cost(None, 原因)` 表示**不知道**。"""
    table, err = _load_table()
    if err:
        return Cost(None, err)
    if not input_tokens and not output_tokens:
        # 没有用量（例如调用在发出前就失败）：成本不是 0，是"没有发生"。
        return Cost(None, BASIS_NO_USAGE)
    if provider.lower().startswith("local") or model.lower().startswith("local"):
        return Cost(0.0, BASIS_LOCAL_FREE)
    prices = table.get(model)
    if not prices:
        return Cost(None, BASIS_UNKNOWN_MODEL)
    usd = float(input_tokens) * prices.get("in", 0.0) + float(output_tokens) * prices.get("out", 0.0)
    return Cost(round(usd, 6), BASIS_TABLE)


__all__ = [
    "BASIS_INVALID_TABLE",
    "BASIS_LOCAL_FREE",
    "BASIS_NO_USAGE",
    "BASIS_TABLE",
    "BASIS_UNKNOWN_MODEL",
    "ENV_PRICE_TABLE",
    "PRICE_TABLE",
    "Cost",
    "estimate_cost",
    "price_table",
]
