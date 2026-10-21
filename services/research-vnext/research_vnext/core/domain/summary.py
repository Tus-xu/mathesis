"""版本化 session summary（RV02 要求 9）。

**硬约束：不得用 AI 编造 summary。**
本轮 summary 完全由确定性领域数据生成（状态、标题、artifact 数、事件数、confirmation 数），
`generated_by` 恒为 `"deterministic-domain-projection"`。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SUMMARY_CONTRACT_VERSION = "1.0.0"
GENERATED_BY = "deterministic-domain-projection"


def build_summary(
    *,
    session_snapshot: dict[str, Any],
    artifact_count: int,
    event_count: int,
    pending_action_count: int,
) -> dict[str, Any]:
    """确定性投影：同样输入必得同样输出。"""
    content = {
        "title": session_snapshot["title"],
        "mode": session_snapshot["mode"],
        "status": session_snapshot["status"],
        "revision": session_snapshot["revision"],
        "goal": session_snapshot.get("goal", ""),
        "artifact_count": artifact_count,
        "event_count": event_count,
        "pending_action_count": pending_action_count,
        "confirmation_count": session_snapshot.get("confirmation_count", 0),
        "policy_refs": session_snapshot.get("policy_refs", []),
        "artifact_refs": session_snapshot.get("current_artifact_refs", []),
    }
    raw = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    return {
        "summary_version": 1,  # 由调用方按历史递增覆盖
        "contract_version": SUMMARY_CONTRACT_VERSION,
        "generated_by": GENERATED_BY,
        "content": content,
        "content_hash": hashlib.sha256(raw).hexdigest(),
    }
