"""W3C traceparent 解析与传播（Browser → Gateway → Core → DB → outbox）。"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field

_TRACEPARENT_RE = None  # 见下方 _compile


def _compile():
    global _TRACEPARENT_RE
    import re

    _TRACEPARENT_RE = re.compile(r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$")


def new_trace_id() -> str:
    """W3C trace-id：32 位小写 hex。"""
    return hashlib.md5(uuid.uuid4().bytes).hexdigest()


def new_span_id() -> str:
    return hashlib.md5(uuid.uuid4().bytes).hexdigest()[:16]


@dataclass
class TraceContext:
    trace_id: str = field(default_factory=new_trace_id)
    span_id: str = field(default_factory=new_span_id)
    flags: str = "01"

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    def child(self) -> "TraceContext":
        return TraceContext(trace_id=self.trace_id, span_id=new_span_id(), flags=self.flags)


def parse_traceparent(raw: str | None) -> TraceContext | None:
    """无法解析即返回 None（调用方 fail closed，不得伪造 trace）。"""
    if not raw:
        return None
    if _TRACEPARENT_RE is None:
        _compile()
    m = _TRACEPARENT_RE.match(raw.strip())  # type: ignore[union-attr]
    if not m:
        return None
    return TraceContext(trace_id=m.group("trace_id"), span_id=m.group("span_id"), flags=m.group("flags"))


def service_name() -> str:
    return os.getenv("RESEARCH_SERVICE_NAME", "research-vnext-8300")
