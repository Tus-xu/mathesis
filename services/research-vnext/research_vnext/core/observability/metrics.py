"""轻量指标采集（不引入第二套框架，进程内计数器 + 日志导出）。"""

from __future__ import annotations

import threading
from collections import defaultdict

_LOCK = threading.Lock()
_COUNTERS: dict[str, float] = defaultdict(float)
_HIST: dict[str, list[float]] = defaultdict(list)


def incr(name: str, value: float = 1.0, **labels) -> None:
    key = name + "{" + ",".join(f"{k}={v}" for k, v in sorted(labels.items())) + "}"
    with _LOCK:
        _COUNTERS[key] += value


def observe(name: str, value: float) -> None:
    with _LOCK:
        _HIST[name].append(value)


def snapshot() -> dict:
    with _LOCK:
        return {
            "counters": dict(_COUNTERS),
            "histograms": {k: {"n": len(v), "avg": (sum(v) / len(v)) if v else 0.0} for k, v in _HIST.items()},
        }
