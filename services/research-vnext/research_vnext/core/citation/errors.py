"""RV07 · Citation 域错误。"""

from __future__ import annotations


class CitationError(Exception):
    """运行时基类。"""


class CitationContractError(CitationError, ValueError):
    """契约违规：未知 CSL 类型 / 未知格式 / 往返静默丢失 / 缺失字段被要求导出。"""


class CitationNotEligibleError(CitationContractError):
    """该证据不允许作为文献引用出现（教材 / AI 派生）。"""


__all__ = ["CitationError", "CitationContractError", "CitationNotEligibleError"]
