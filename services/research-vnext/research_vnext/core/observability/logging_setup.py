"""结构化日志 + trace 注入 + 脱敏过滤。

链路：Browser → Gateway → Core → DB → outbox，全部通过 `trace_id` 串联。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from .redaction import redact
from .trace import service_name


class RedactingFilter(logging.Filter):
    """所有日志在 emit 前脱敏：token / PII / content。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.getMessage()))
            record.args = ()
        except Exception:  # noqa: BLE001
            record.msg = "[LOG_REDACTION_FAILED]"
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": service_name(),
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "session_id": getattr(record, "session_id", None),
            "component": getattr(record, "component", "core"),
        }
        return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(logging.WARNING)


def log_event(logger: logging.Logger, *, trace_id: str, component: str, message: str, **extra) -> None:
    logger.info(message, extra={"trace_id": trace_id, "component": component, **extra})
