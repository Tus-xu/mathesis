"""结构化日志（structlog）：request_id 贯穿 + PII 字段白名单化（G3）。

前端拿到的 `request_id` 与日志中的 `request_id` 一致（response.py 从 request.state 取同一值）。
PII 白名单：学生姓名/作业图片等字段绝不进日志；未命中白名单的字段丢弃。
"""
from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

# 允许进日志的字段白名单（G3：学生姓名/作业图片等 PII 不进日志与外呼）
LOG_FIELD_WHITELIST = {
    "request_id",
    "method",
    "path",
    "status",
    "latency_ms",
    "user_id",
    "user_role",
    "task_id",
    "task_type",
    "event_type",
    "channel",
    "seq",
    "code",
    "error",
    "feature",
    "model",
    "tokens",
    "prompt_version",
    "version",
    "duration_ms",
    "attempt",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def sanitize(logger, method_name: str, event_dict: dict) -> dict:
    """PII 白名单化：只保留白名单字段，其余丢弃（不落日志）。"""
    return {k: v for k, v in event_dict.items() if k in LOG_FIELD_WHITELIST}


def setup_logging(level: str = "INFO") -> None:
    # 容器内 stdout 是管道（块缓冲）——行缓冲保证日志实时进 docker logs（可观测性）
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # noqa: BLE001
        pass
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            sanitize,
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        cache_logger_on_first_use=True,
    )


def get_logger(*args, **kwargs):
    return structlog.get_logger(*args, **kwargs)
