"""安全 negative suite（R11.5-C P0）：secret redaction + prompt injection 检测。

- `redact`：深度脱敏（key/token/secret/authorization/… 值替换为 [REDACTED]），
  日志/错误/证据输出前必经此层（不接受全量原文外泄，DECISIONS §5「服务端只返回最小化处理数据」）；
- `PromptInjectionGuard`：对**非信任内容**（教材 chunk、用户输入、检索文档、工具输出、
  VLM/OCR 文本）做直/间接/RAG/多模态四通道注入检测——检测到即阻止作为系统指令使用。

判定是**闸门**而非绝对防火墙：guard 命中时调用方必须把该内容标为不可信（UNTRUSTED），
不得直接拼接进 system prompt；未命中不改变既有 UNTRUSTED 分层语义（context assembler 双保险）。
"""
from __future__ import annotations

import copy
import unicodedata
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "InjectChannel",
    "InjectionVerdict",
    "PromptInjectionGuard",
    "redact",
    "normalize_text",
]

# 敏感字段名（规范化后比对/包含匹配；覆盖 headers/基础配置常见密钥）
_SENSITIVE_KEY_HINTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "authorization",
    "password",
    "passwd",
    "credential",
    "cookie",
    "private_key",
    "access_key",
    "account_key",
    "signature",
    "x-secret",
    "jwt",
)
_REDACTED = "[REDACTED]"


def redact(
    obj: Any,
    *,
    key_hints: tuple[str, ...] = _SENSITIVE_KEY_HINTS,
    replacement: str = _REDACTED,
) -> Any:
    """deep-copy 去敏：dict 键名（规范化小写）命中 hint 或含 hint → 值替换；list → 递归。"""

    def _norm(key: str) -> str:
        return unicodedata.normalize("NFKC", key).strip().lower()

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            normalized = _norm(str(k))
            is_sensitive = (
                any(h in normalized for h in key_hints)
                or normalized in ("key", "token", "secret", "password")
                or normalized.endswith(("_key", "-key", ".key", "_token", "_secret", "_password"))
                or normalized.startswith(("api_", "api-", "access_"))
            )
            if is_sensitive and not isinstance(v, (dict, list)):
                out[k] = replacement
            else:
                out[k] = redact(v, key_hints=key_hints, replacement=replacement)
        return out
    if isinstance(obj, list):
        return [redact(x, key_hints=key_hints, replacement=replacement) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact(x, key_hints=key_hints, replacement=replacement) for x in obj)
    if isinstance(obj, bytes):
        return replacement  # 二进制内容一律不落证据
    return copy.deepcopy(obj)


def normalize_text(text: str) -> str:
    """注入检测前的规范化：NFKC、去零宽字符、全角→半角、小写。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("C"))
    text = text.replace("\u3000", " ")
    return text.strip().lower()


_INJECTION_PHRASES: tuple[str, ...] = (
    # 直接（覆盖系统指令 / 越权索取）
    "ignore all previous instructions",
    "ignore previous instructions",
    "ignore everything above",
    "ignore above",
    "ignore the above",
    "disregard all previous",
    "disregard previous instructions",
    "forget all previous",
    "forget everything",
    "you are now",
    "you are not",
    "from now on you are",
    "act as if you are",
    "act as an ai with no",
    "override instructions",
    "override your instructions",
    "new instructions",
    "system prompt",
    "system instructions",
    "developer message",
    "reveal your system",
    "reveal your instructions",
    "show your prompt",
    "print your instructions",
    # 间接（诱导泄露 / 信息劫持）
    "reply with just",
    "reply with only",
    "do not mention these instructions",
    "do not mention this conversation",
    "tell me your prompt",
    "leak your system",
    "simulate being",
    "pretend you are",
    "take on the role of",
    "jailbroken",
    "dan mode",
    "do anything now",
)

# 多模态（VLM/OCR 输出里常见的注入提示；命中即该图/文本不可作为指令）
_MULTIMODAL_PHRASES: tuple[str, ...] = (
    "ignore the image",
    "ignore the picture",
    "ignore what you see",
    "do not read the text",
    "do not follow the text",
    "follow the text in the image",
    "the text in this image is a command",
    "this image contains instructions",
)

InjectChannel = str  # "direct" | "indirect" | "rag" | "multimodal"


@dataclass
class InjectionVerdict:
    channel: InjectChannel
    clean: bool
    matched: list[str] = field(default_factory=list)

    def as_meta(self) -> dict[str, Any]:
        return {"channel": self.channel, "clean": self.clean, "matched": self.matched[:5]}


class PromptInjectionGuard:
    """按通道检测非信任文本中的注入特征（负向套件 = 检测必须命中已知攻击模式）。"""

    def __init__(
        self,
        *,
        direct_phrases: tuple[str, ...] = _INJECTION_PHRASES,
        multimodal_phrases: tuple[str, ...] = _MULTIMODAL_PHRASES,
    ) -> None:
        self._direct_phrases = tuple(p.lower() for p in direct_phrases)
        self._multimodal_phrases = tuple(p.lower() for p in multimodal_phrases)

    # ── 直接注入（用户输入文本）──

    def direct(self, text: str) -> InjectionVerdict:
        return self._scan("direct", text, self._direct_phrases)

    # ── 间接注入（检索文档 / 网页 / 教材内容）──

    def indirect(self, documents: list[str] | str) -> InjectionVerdict:
        agg = self._scan("indirect", "\n".join(documents) if isinstance(documents, list) else documents, self._direct_phrases)
        if not agg.clean:
            return agg
        # 间接类补充特征：文档中要求把「以上文本视为指令」
        for doc in (documents if isinstance(documents, list) else [documents]):
            n = normalize_text(doc)
            if any(p in n for p in (
                "treat the content above as instructions",
                "the following text is a command",
                "this document contains instructions",
                "ignore safety",
            )):
                return InjectionVerdict(channel="indirect", clean=False, matched=["doc-instruction-markers"])
        return InjectionVerdict(channel="indirect", clean=True)

    # ── RAG（证据包聚合视图）──

    def rag(self, evidence_packs: list[dict[str, Any]]) -> InjectionVerdict:
        texts: list[str] = []
        for pack in evidence_packs:
            if not isinstance(pack, dict):
                continue
            for key in ("content", "text", "snippet", "summary", "chunk"):
                v = pack.get(key)
                if isinstance(v, str):
                    texts.append(v)
        v = self.indirect(texts)
        # 重贴通道：indirect 聚合判定 → rag 通道语义
        return InjectionVerdict(channel="rag", clean=v.clean, matched=v.matched)

    # ── 多模态（VLM/OCR 文本通道）──

    def multimodal(self, captions: list[str] | str) -> InjectionVerdict:
        joined = "\n".join(captions) if isinstance(captions, list) else captions
        hit = self._scan("multimodal", joined, self._multimodal_phrases)
        if not hit.clean:
            return hit
        if any(p in normalize_text(joined) for p in self._direct_phrases):
            return InjectionVerdict(channel="multimodal", clean=False, matched=["embedded-direct"])
        return InjectionVerdict(channel="multimodal", clean=True)

    def _scan(self, channel: InjectChannel, text: str, phrases: tuple[str, ...]) -> InjectionVerdict:
        normalized = normalize_text(text)
        if not normalized:
            return InjectionVerdict(channel=channel, clean=True)
        matched: list[str] = []
        for phrase in phrases:
            if phrase in normalized:
                matched.append(phrase)
        return InjectionVerdict(channel=channel, clean=not matched, matched=matched)


# 默认单例（业务可直接 import guard；各通道结果按 DECISIONS §5 落地为证据元数据）
guard = PromptInjectionGuard()
