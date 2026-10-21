"""RV06 · untrusted 文本的注入防线。

为什么放在检索层而不是提示词层：P00 ADR-PLAT-05 的结论是「prompt 措辞可被绕过，
契约校验不会」。检索结果是把**外部内容**带进上下文的唯一入口，所以标记与中性化
必须发生在结果离开 orchestrator 之前。

本模块只做两件事：
1. **检出**注入形态（指令覆写 / 伪 system 标签 / 角色标签逃逸 / 工具调用伪造 / 提权）；
2. **中性化**：把命中片段替换成 `[[INJECTION-REDACTED:<pattern_id>]]` 并如实上报。

它不做"清洗掉就当作安全"的假设 —— 结果的 `trust_level` 永远是
`UNTRUSTED_DATA`，中性化只是降低可操作性，不是把数据提升成指令。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .types import TrustLevel


class InjectionPattern(str, Enum):
    INSTRUCTION_OVERRIDE = "instruction_override"
    FAKE_SYSTEM_TAG = "fake_system_tag"
    ROLE_PLAY_ESCAPE = "role_play_escape"
    TOOL_CALL_FORGERY = "tool_call_forgery"
    PRIVILEGE_ESCALATION = "privilege_escalation"


#: 每个模式一组正则。模式刻意要求**指令形态**（动词 + 目标），
#: 以免把学术正文里合法出现的 "system" / "instruction" 误判成注入。
_PATTERNS: tuple[tuple[InjectionPattern, tuple[re.Pattern[str], ...]], ...] = (
    (
        InjectionPattern.INSTRUCTION_OVERRIDE,
        (
            re.compile(r"(?i)\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)"),
            re.compile(r"(?i)\b(?:new|updated)\s+(?:instructions?|system\s+prompt)\s*:\s*"),
            re.compile(r"(?:忽略|无视|忘记)\s*(?:之前|以上|先前|前述|上面|前面)[^，。；\n]{0,12}(?:指令|提示|要求|规则)"),
            re.compile(r"(?:你现在是|从现在起你是|扮演|切换到)[^，。；\n]{0,16}(?:不受限制|越狱|无限制)"),
        ),
    ),
    (
        InjectionPattern.FAKE_SYSTEM_TAG,
        (
            re.compile(r"<\|\s*(?:system|assistant|im_start|im_end)\s*\|>", re.IGNORECASE),
            re.compile(r"(?im)^\s*#{1,6}\s*system\s*:"),
            re.compile(r"(?i)<\s*/?\s*(?:system|sys)\s*>"),
            re.compile(r"<<\s*SYS\s*>>|<<\s*/SYS\s*>>", re.IGNORECASE),
        ),
    ),
    (
        InjectionPattern.ROLE_PLAY_ESCAPE,
        (
            re.compile(r"\[/?INST\]", re.IGNORECASE),
            re.compile(r"(?i)\[\[?\s*(?:system|assistant)\s*\]\]?\s*:"),
            re.compile(r"```\s*(?:system|instructions?)\b", re.IGNORECASE),
        ),
    ),
    (
        InjectionPattern.TOOL_CALL_FORGERY,
        (
            re.compile(r"\{[^\n]*\"(?:tool_call|function_call|tool_calls)\"[^\n]*\}"),
            re.compile(r"(?i)<\s*(?:tool_call|function_call)\b[^>]*>[\s\S]{0,400}?<\s*/\s*(?:tool_call|function_call)\s*>"),
            re.compile(r"(?i)\bname\s*[:=]\s*[\"']?(?:read_file|write_file|exec_shell|run_command|drop_database)\b"),
        ),
    ),
    (
        InjectionPattern.PRIVILEGE_ESCALATION,
        (
            re.compile(r"(?i)\b(?:exfiltrate|exfiltration)\w*"),
            re.compile(r"(?i)\b(?:all|every)\s+tenants?'?\s+(?:data|records|information)"),
            re.compile(r"(?:越权|提权|绕过(?:权限|鉴权|授权))"),
            re.compile(r"(?i)\b(?:grant|give)\s+(?:me|yourself)\s+(?:admin|root|elevated)\b"),
        ),
    ),
)


def _marker(pattern: InjectionPattern) -> str:
    return f"[[INJECTION-REDACTED:{pattern.value}]]"


@dataclass(frozen=True)
class SanitizeResult:
    """中性化结果。`trust_level` 恒为 `UNTRUSTED_DATA`，不可被调用方改写。"""

    text: str
    sanitized: bool
    patterns: tuple[str, ...]
    trust_level: TrustLevel = TrustLevel.UNTRUSTED_DATA
    original_length: int = 0

    @property
    def untrusted(self) -> bool:
        return self.trust_level is TrustLevel.UNTRUSTED_DATA


def sanitize_untrusted(text: str) -> SanitizeResult:
    """检出并中性化注入形态。良性文本原样返回。"""
    raw = text or ""
    if not raw:
        return SanitizeResult(text="", sanitized=False, patterns=(), original_length=0)

    out = raw
    hits: list[str] = []
    for pattern, regexes in _PATTERNS:
        matched = False
        for rx in regexes:
            if rx.search(out):
                out = rx.sub(_marker(pattern), out)
                matched = True
        if matched:
            hits.append(pattern.value)

    deduped = tuple(dict.fromkeys(hits))
    return SanitizeResult(
        text=out,
        sanitized=bool(deduped),
        patterns=deduped,
        original_length=len(raw),
    )


def contains_injection(text: str) -> bool:
    """只做判定（不中性化），供守卫与测试使用。"""
    return sanitize_untrusted(text).sanitized


__all__ = [
    "InjectionPattern",
    "SanitizeResult",
    "sanitize_untrusted",
    "contains_injection",
]
