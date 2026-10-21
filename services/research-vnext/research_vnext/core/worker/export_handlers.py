"""RV14 · paper.export handler 注册（runtime 唯一登记点）。

独立 compile pool 的存在意义：`paper.export` 与主 pool 的 LLM 类作业
**完全隔离**（独立 stream/group/dedupe + job_types 过滤）——
长编译永不挤占主池，主池故障也拖不垮编译池。
"""

from __future__ import annotations


def register_export_jobs() -> None:
    from . import runtime
    from ..export.service import export_handler

    runtime.register("paper.export", export_handler)
