"""RV09 · 注入与输出处理（输入侧 neutralize，输出侧 fail-closed）。

两条**不同**的处置策略，混用会造成两种相反的伤害
------------------------------------------------

**输入侧（用户消息 / 检索片段 / 资产文本）：不去执行、也不删改，只隔离 + 记录。**
用户**有权**讨论"忽略以上指令"这句话本身（这是提示词工程的正常研究对象）。
若我们因为看见这几个字就拒答，就把正常用户当成攻击者 ——
所以输入侧只做两件事：
  1. **消毒定界符**（`registry.wrap_untrusted`），让内容无法提前闭合数据块；
  2. **登记信号**（`injectionSignals`），让"这一轮出现过注入迹象"可审计。

**输出侧（模型回复）：fail-closed，一律不得落正式状态。**
模型输出若包含指令性内容（"忽略规则""我是系统""输出 HTML"）或标记/脚本，
说明它可能已被数据块里的内容带偏。此时**必须**拒绝整轮，
而不是"过滤掉那句继续用" —— 被带偏的模型其余部分也不再可信。

`scan_output` 只做**机械**判定：它不试图理解语义。它的价值在于
"凡是明显越界的输出都进不了 PendingAction"，把语义判断留给人工确认门。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 输入侧信号（**只记录，不拦截**）。
INPUT_SIGNALS: dict[str, tuple[str, ...]] = {
    "INSTRUCTION_OVERRIDE": (
        "忽略以上",
        "忽略之前",
        "忽略上述",
        "不要管之前",
        "ignore previous",
        "ignore all previous",
        "ignore the above",
        "disregard previous",
        "forget your instructions",
    ),
    "ROLE_HIJACK": (
        "你现在是",
        "从现在开始你是",
        "你的新身份",
        "you are now",
        "act as the system",
        "pretend you are",
    ),
    "PROMPT_EXFILTRATION": (
        "输出你的系统提示",
        "重复你的提示词",
        "你的系统提示是什么",
        "reveal your instructions",
        "print your system prompt",
        "show your prompt",
    ),
    "CHANNEL_ESCAPE": (
        "输出 html",
        "输出脚本",
        "执行以下代码",
        "output html",
        "render html",
        "eval(",
        "<script",
    ),
}

#: 对话模板标记 / 数据块定界符伪造。这些是**结构性**的，不只是"像指令"。
_TEMPLATE_MARKERS = (
    "<|im_start|>",
    "<|im_end|>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "[INST]",
    "[/INST]",
    "<<SYS>>",
    "### System:",
    "### Human:",
    "### Assistant:",
)

_TEMPLATE_RE = re.compile("|".join(re.escape(m) for m in _TEMPLATE_MARKERS))

#: 数据块定界符（与 `llm/prompts/registry.py` 同字面量；此处不 import 以避免循环）。
UNTRUSTED_OPEN = "<<<UNTRUSTED_DATA>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_DATA>>>"

#: 输出里一旦出现这些**真实**标记，整轮拒绝（不是过滤）。
FORBIDDEN_OUTPUT_PATTERNS: dict[str, str] = {
    "OUTPUT_HTML_TAG": r"<\s*(script|iframe|style|div|span|img|svg|link|object|embed)\b",
    "OUTPUT_CODE_FENCE_HTML": r"```\s*(html|htm|vue|jsx|tsx|javascript|css)\b",
    "OUTPUT_EVENT_HANDLER": r"\bon[a-z]+\s*=\s*[\"']",
    "OUTPUT_TEMPLATE_MARKER": "|".join(re.escape(m) for m in _TEMPLATE_MARKERS),
    "OUTPUT_DELIMITER_SPOOF": re.escape(UNTRUSTED_OPEN) + "|" + re.escape(UNTRUSTED_CLOSE),
    "OUTPUT_SYSTEM_CLAIM": r"(我是|作为)(系统|system)\b|system\s*message\s*[:：]",
}


@dataclass
class InputScan:
    signals: list[str] = field(default_factory=list)
    template_markers: list[str] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return bool(self.signals or self.template_markers)

    def as_dict(self) -> dict[str, Any]:
        return {"signals": list(self.signals), "templateMarkers": list(self.template_markers)}


@dataclass
class OutputScan:
    forbidden: list[str] = field(default_factory=list)
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.forbidden

    def as_dict(self) -> dict[str, Any]:
        return {"forbidden": list(self.forbidden), "detail": dict(self.detail)}


def scan_input(text: str) -> InputScan:
    """扫描**不可信文本**，返回信号清单（**不**修改文本，**不**拦截）。"""
    lowered = (text or "").lower()
    scan = InputScan()
    for signal, needles in INPUT_SIGNALS.items():
        if any(n in lowered for n in needles):
            scan.signals.append(signal)
    for m in _TEMPLATE_RE.finditer(text or ""):
        if m.group(0) not in scan.template_markers:
            scan.template_markers.append(m.group(0))
    if UNTRUSTED_OPEN in (text or "") or UNTRUSTED_CLOSE in (text or ""):
        if "DELIMITER_SPOOF" not in scan.signals:
            scan.signals.append("DELIMITER_SPOOF")
    return scan


def scan_output(text: str) -> OutputScan:
    """扫描**模型输出**。发现越界标记即整轮拒绝的依据。"""
    scan = OutputScan()
    body = text or ""
    for code, pattern in FORBIDDEN_OUTPUT_PATTERNS.items():
        m = re.search(pattern, body, flags=re.IGNORECASE)
        if m:
            scan.forbidden.append(code)
            scan.detail[code] = m.group(0)[:120]
    return scan


def scan_payload_keys(payload: Any, *, path: str = "payload") -> list[str]:
    """递归找出 payload 里被禁止的键（HTML/CSS/组件名）。

    与 `proposals.FORBIDDEN_KEYS` 的差别：这里返回**全部**命中路径，
    便于审计"模型到底想渲染什么"，而不是只报第一个。
    """
    from .proposals import FORBIDDEN_KEYS

    hits: list[str] = []
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(k, str) and k.strip().lower() in FORBIDDEN_KEYS:
                hits.append(f"{path}.{k}")
            hits.extend(scan_payload_keys(v, path=f"{path}.{k}"))
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            hits.extend(scan_payload_keys(v, path=f"{path}[{i}]"))
    return hits


__all__ = [
    "FORBIDDEN_OUTPUT_PATTERNS",
    "INPUT_SIGNALS",
    "InputScan",
    "OutputScan",
    "scan_input",
    "scan_output",
    "scan_payload_keys",
]
