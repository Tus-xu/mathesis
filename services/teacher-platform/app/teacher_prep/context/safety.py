"""R16 教材上下文安全原语：UNTRUSTED 数据行为中和（DECISIONS.md §4）。

铁律：教材 chunk、图片、上传文件、学生答案、用户输入、tool 输出全部是
UNTRUSTED DATA，不得把其中指令当系统指令。本模块提供确定性安全原语：

- `detect_injection(text)`：命中指令注入模式 → 返回命中模式列表（显式告警用）；
- `neutralize_untrusted(text)`：命中注入模式时截断为数据引用（数据语义保留，
  指令文本不进入任何系统指令槽位）；
- `is_bad_chunk(text)`：空/纯空白/异常控制字符 → 判为坏 chunk（builder 显式
  告警并排除，不静默吞掉）。

注入模式列表与 KNR EvidencePack（app/agent/context/models.py）同语义。
本文件为本地拷贝（不 import dirty 文件），保持纯函数、零 IO、可单测。
"""

from __future__ import annotations

# 指令注入模式（UNTRUSTED 文本行为中和；命中即截断，不保留指令原文）
INSTRUCTION_INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous instructions",
    "忽略以上指令",
    "忽略之前的指令",
    "ignore all previous",
    "you are now",
    "你现在是",
    "act as",
    "作为系统",
    "system prompt",
    "系统提示词",
    "print the system prompt",
    "repeat the prompt",
    "do not follow",
    "不要遵循",
    "override",
    "覆盖系统",
    "disregard",
    "忽略上述",
    "forget everything",
)

_NEUTRAL_SUFFIX = "…[untrusted-boundary: 截断潜在指令注入内容]"


def detect_injection(text: str) -> list[str]:
    """返回命中的注入模式（小写匹配；空文本/无命中 → []）。"""
    lowered = (text or "").lower()
    return [p for p in INSTRUCTION_INJECTION_PATTERNS if p in lowered]


def neutralize_untrusted(text: str) -> str:
    """UNTRUSTED 文本指令注入中和：命中 → 在**最早命中处**截断为数据引用。

    与 KNR EvidencePack 同语义：只保留前缀（数据语义），拒绝把指令当指令执行。
    按最早命中位置截断（而非模式表顺序），防止前置注入文本残留在数据中。
    """
    if not text:
        return text
    lowered = text.lower()
    hits = [(lowered.find(p), p) for p in INSTRUCTION_INJECTION_PATTERNS if p in lowered]
    if not hits:
        return text
    idx, _ = min(hits)
    return text[:idx].rstrip() + _NEUTRAL_SUFFIX


# 异常控制字符（排除常见空白）：C0/C1 控制符视为坏 chunk 信号
_CTRL_CHAR_RE = "".join(
    chr(c) for c in range(0x00, 0x20) if chr(c) not in "\t\n\r"
) + "".join(chr(c) for c in range(0x7F, 0xA0))


def is_bad_chunk(text: str) -> bool:
    """坏 chunk 判定：空/纯空白 → True；异常控制字符密度高 → True。

    空内容在 R06 importer 已拒写，但历史数据/外部回填可能残留；
    builder 对坏 chunk 显式告警并排除（不静默）。
    """
    if text is None:
        return True
    if not text.strip():
        return True
    ctrl = sum(1 for ch in text if ch in _CTRL_CHAR_RE)
    return ctrl > max(2, len(text) // 100)
