"""RV15 · Blocking gate —— P0/P1 未解决 finding 阻断 COMPLETED。

BLOCKING-GATE.md 的机器强制点。判定是**纯 DB、确定、同步**的：
不存在"门禁比事实旧"的窗口；豁免（WAIVED 且未过期）视为已处置。
"""

from __future__ import annotations

import uuid


class BlockedByFindings(Exception):
    """存在未解决的 P0/P1 finding（409，带机读 blockedBy 明细）。"""

    code = "RESEARCH_BLOCKED_BY_FINDINGS"
    http_status = 409

    def __init__(self, message: str, blocked: list[dict]) -> None:
        super().__init__(message)
        self.message = message
        self.blocked = blocked
        self.detail = {"blockedBy": blocked[:50], "count": len(blocked)}


def blocking_findings(db, *, session_id: uuid.UUID) -> list[dict]:
    from ..review.service import blocking_findings as _svc_blocking

    return _svc_blocking(db, session_id=session_id)


def assert_no_blocking_findings(db, *, session_id: uuid.UUID) -> None:
    """COMPLETED 迁移的最后一道门；有阻断 finding 即抛 `BlockedByFindings`。"""
    from ..domain.state_machine import SessionState

    blocked = blocking_findings(db, session_id=session_id)
    if blocked:
        raise BlockedByFindings(
            f"存在 {len(blocked)} 条未解决的 P0/P1 审查发现："
            "先修复并引用证据，或经人工豁免（有理由/有到期），才能完成会话。"
            "已有 artifact（论文/主张/证据/导出）不受影响。",
            blocked,
        )


# SessionState 仅用于文档性 import（保证模块边界清晰），运行时不使用。
_ = SessionState
