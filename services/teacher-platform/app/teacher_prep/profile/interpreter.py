"""R19 AI 解释 proposal 通道（LLM 只产 proposal；失败不影响基础统计）。

边界（R19 DELTA D3/D6）：
- 解释输入 = 画像的**聚合统计块**（无 PII、无答案内容）；
- 输出 = 解释 proposal（结构化 dict，schema 校验由生产 adapter 负责）；
- 本模块只定义窄协议；生产 LLM gateway 接线留业务轮（测试注入 fake/raising）；
- 任何解释失败 → service 返回 basic_mode（画像与统计零改动）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProfileInterpreter(Protocol):
    """画像解释器协议：聚合统计 → 解释 proposal（async）。"""

    async def interpret(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NullInterpreter:
    """显式无解释器：直接 basic_mode（不假装 AI 成功）。"""

    async def interpret(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise InterpreterUnavailableError("interpreter_not_configured")


class InterpreterUnavailableError(RuntimeError):
    """解释器不可用/失败（service 捕获 → basic_mode，不冒充成功）。"""


__all__ = ["ProfileInterpreter", "NullInterpreter", "InterpreterUnavailableError"]
