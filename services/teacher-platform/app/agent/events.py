"""AgentEvent 与规范事件序列（02-ARCHITECTURE §7.4，逐字对齐）。

SSE 事件名/顺序/载荷与 mock teacherV3Server.ts 逐一对齐是 B1 红线 4——
差异即 bug（真实数据内容除外）。此模块是适配层与契约测试的共同事实源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# butler / 生成任务 / 语音的全部合法事件名
EventName = Literal[
    "meta",
    "thinking",
    "token",
    "tool_call",
    "tool_result",
    "card",
    "action",
    "citation",
    "outline",
    "slide",
    "section",
    "suggest",
    "diff",
    "asr_partial",
    "asr_final",
    "done",
    "error",
]

# §7.4 规范序列（端点 → 事件名骨架；* = 重复，? = 可选）
CANONICAL_SEQUENCES: dict[str, tuple[str, ...]] = {
    "POST /butler/chat": ("meta", "thinking", "token*", "tool_call?", "tool_result?", "card*", "action?", "citation?", "done"),
    "POST /butler/voice-formula": ("asr_partial*", "asr_final", "card", "done"),
    "POST /generation/deck": ("meta", "outline", "slide*", "done"),
    "POST /generation/plan": ("meta", "outline", "section*", "done"),
    "POST /generation/decks/{id}/ai-element": ("meta", "suggest", "diff", "done"),
    "POST /grading/assignments/{id}/review-pack": ("meta", "slide*", "done"),
}


@dataclass
class AgentEvent:
    """图/管线对外吐出的一个事件；SSE 层序列化为 `event: <name>
data: <json>`。"""

    name: str
    payload: dict = field(default_factory=dict)

    def sse_tuple(self) -> tuple[str, str]:
        import json

        return self.name, json.dumps(self.payload, ensure_ascii=False, default=str)


def skeleton_of(names: list[str]) -> list[str]:
    """把实际事件名序列折叠成规范骨架：连续重复名去重保序（§7.4 的 * 只标记可重复，
    顺序断言不需要星号），供序列断言使用。"""
    out: list[str] = []
    for n in names:
        if out and out[-1] == n:
            continue
        out.append(n)
    return out
