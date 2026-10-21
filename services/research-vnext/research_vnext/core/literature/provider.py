"""RV04 · LiteratureProvider typed contract。"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


AVAIL_UNKNOWN = "UNKNOWN"
AVAIL_YES = "YES"
AVAIL_NO = "NO"


class LiteratureProviderError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail or {}
        self.http_status = 502 if retryable else 502


class ProviderTimeout(LiteratureProviderError):
    def __init__(self, message: str = "provider timeout"):
        super().__init__("PROVIDER_TIMEOUT", message, retryable=True)


class ProviderRateLimited(LiteratureProviderError):
    def __init__(self, message: str = "provider rate limited", retry_after: int | None = None):
        super().__init__(
            "PROVIDER_RATE_LIMITED",
            message,
            retryable=True,
            detail={"retry_after": retry_after},
        )


@dataclass
class LiteratureMetadata:
    provider: str
    provider_version: str
    provider_record_id: str
    request_id: str
    query: str
    doi: str = ""
    title: str = ""
    authors: list[dict] = field(default_factory=list)
    published_date: str = ""
    venue: str = ""
    abstract_available: str = AVAIL_UNKNOWN
    fulltext_available: str = AVAIL_UNKNOWN
    rights_status: str = "UNKNOWN"
    rights_detail: dict = field(default_factory=dict)
    source_url: str = ""
    retrieved_at: str = ""
    raw_response_hash: str = ""
    raw: dict = field(default_factory=dict)

    def to_public(self) -> dict:
        d = asdict(self)
        # 原始响应不进入公开投影（体积 + 可能含第三方字段）
        d.pop("raw", None)
        d["record_kind"] = "METADATA_ONLY"
        d["note"] = "metadata ≠ Evidence；abstract ≠ full text"
        return d


def hash_raw_response(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return hashlib.sha256(bytes(payload)).hexdigest()
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiteratureProvider(ABC):
    name: str = "base"
    version: str = "0"

    @abstractmethod
    def search(self, query: str, *, limit: int = 5, request_id: str = "") -> list[LiteratureMetadata]:
        """返回 typed metadata 列表。禁止在此长时阻塞；限流/超时抛 Provider* 异常。"""

    def fetch_by_doi(self, doi: str, *, request_id: str = "") -> LiteratureMetadata | None:
        raise NotImplementedError
