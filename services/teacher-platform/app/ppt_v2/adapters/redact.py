"""脱敏日志与引用摘要（R11 零秘密落盘红线）。

规则：日志只记录 hash/ref 与统计；绝不记录 key、原文、教材全文、老师上传原图、签名 URL。
"""
from __future__ import annotations

import hashlib
import logging


def sha16(text: str) -> str:
    """文本引用摘要（前 16 位 sha256），用于 redacted 日志。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def redacted_logger(name: str = "ppt_v2.adapters") -> logging.Logger:
    return logging.getLogger(name)
