"""RV13 · Paper 域封闭错误码。

错误码一律可机器处理；`http_status` 由 API 层映射，领域层不认识 HTTP。
"""

from __future__ import annotations

from ..domain.errors import DomainError


class PaperSchemaError(DomainError):
    """论文 schema / 安全门违规（默认 422）。

    签名对齐 `ProposalRejected`：`(code, message, **detail)` ——
    拒绝必须带机读错误码，否则前端只能对文案做字符串匹配。
    """

    code = "PAPER_SCHEMA_VIOLATION"
    http_status = 422

    def __init__(self, code: str, message: str = "", **detail: object) -> None:
        super().__init__(message or code, code=code, **detail)
        self.code = code
        self.message = message or code


class PaperStateError(PaperSchemaError):
    """论文状态冲突（undo/redo 无可撤销等，409）。"""

    code = "PAPER_STATE_CONFLICT"
    http_status = 409


class PaperRevisionStale(PaperStateError):
    """patch / 手工编辑基于过期修订（409，附 nodeDiff）。"""

    code = "PAPER_REVISION_STALE"
