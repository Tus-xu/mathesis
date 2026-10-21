"""日志/指标/trace 脱敏（RV02 要求 10 与总手册 §13.7）。

进入日志前必须脱敏：token / Authorization / api key / AccessContext /
手机号 / 邮箱 / 长正文。**不得打印真实密钥。**
"""

from __future__ import annotations

import re
from typing import Any

MAX_CONTENT_CHARS = 200

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT / Bearer
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{4,}\.?[A-Za-z0-9\-_.]*"), "[REDACTED_JWT]"),
    # 讯飞/博查风格 api key
    (re.compile(r"\b(?:sk|ak)-[A-Za-z0-9]{16,}"), "[REDACTED_KEY]"),
    # 通用长 hex / base64 密钥片段
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[REDACTED_HEX]"),
    # AccessContext header
    (re.compile(r"(?i)(X-Research-Access-Context\s*[:=]\s*)[A-Za-z0-9\-_=]{16,}"), r"\1[REDACTED_CTX]"),
    # 中国大陆手机号
    (re.compile(r"\b1[3-9]\d{9}\b"), "[REDACTED_PHONE]"),
    # 邮箱（保留域名，隐去本地部分）
    (re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"), r"[REDACTED_USER]@\1"),
    # Authorization 头整行
    (re.compile(r"(?i)(authorization\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
]


def redact(value: str) -> str:
    out = value
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    if len(out) > MAX_CONTENT_CHARS and "REDACTED" not in out[:MAX_CONTENT_CHARS]:
        out = out[:MAX_CONTENT_CHARS] + f"...[TRUNCATED {len(out) - MAX_CONTENT_CHARS} chars]"
    return out


#: 键名含这些提示的**纯 hex 字符串**视为完整性哈希（sha256/digest），不是秘密：
#: manifest 的哈希必须真实可校验（RV10 L-1 缺陷修复：此前 contentHash /
#: sha256 被误标为 [REDACTED_HEX]，导致产物清单不可验证）。
#: 日志正文里的裸 hex 仍按 _PATTERNS 脱敏（redact() 行为不变）。
_HASH_KEY_HINTS = ("sha", "hash", "digest", "fingerprint", "imageid")
_PURE_HEX = re.compile(r"[A-Fa-f0-9]{32,}")
#: 镜像 ID 常见的 "算法:hex" 前缀（sha256:…）；前缀本身非秘密。
_HASH_VALUE_PREFIX = re.compile(r"^(?P<prefix>sha256:|sha1:|sha512:|md5:)(?P<hex>[A-Fa-f0-9]{32,})$")


def redact_mapping(data: Any, _depth: int = 0, _key: str = "") -> Any:
    if _depth > 6:
        return "[DEPTH_LIMIT]"
    if isinstance(data, dict):
        return {k: redact_mapping(v, _depth + 1, str(k)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact_mapping(v, _depth + 1, _key) for v in data]
    if isinstance(data, str):
        if _key and any(h in _key.lower() for h in _HASH_KEY_HINTS):
            if _PURE_HEX.fullmatch(data):
                return data
            m = _HASH_VALUE_PREFIX.match(data)
            if m:
                return f"{m.group('prefix')}{m.group('hex')}"
        return redact(data)
    return data
