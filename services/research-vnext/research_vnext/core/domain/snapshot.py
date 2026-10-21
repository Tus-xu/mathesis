"""`ResearchSnapshot` 投影（ADR-RV-003 §3.3）。

这是**前端 adapter 的硬契约**，不是可选美化：
`D:/frontend/src/features/research-vnext/adapters/researchAdapter.ts` 要求
`capabilities` / `timeline` / `evidence` **三者都是数组**，否则整体判 `503`；
`researchApi.ts` 又用 `research-message === 'available'` 决定 composer 是否可发送
—— **该 key 缺失 ⇒ 输入框永久禁用**。

因此本模块的两条不变量：
1. 三个数组字段**任何路径下**都返回 `[]` 而不是 `null`/缺失；
2. 五个必需 capability key（`research-session` / `research-message` /
   `research-stream` / `research-evidence` / `research-artifact`）**永远存在**。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import ResearchMessageRow, ResearchSessionRow
from ..domain.session import ResearchSession, SessionMode
from .message_projection import project_attachments, project_component

SNAPSHOT_CONTRACT_VERSION = "rv-cr6-1.0.0"

REQUIRED_CAPABILITY_KEYS: tuple[str, ...] = (
    "research-session",
    "research-message",
    "research-stream",
    "research-evidence",
    "research-artifact",
)

CAPABILITY_LABELS: dict[str, str] = {
    "research-session": "研究会话",
    "research-message": "对话输入",
    "research-stream": "实时事件流",
    "research-evidence": "证据",
    "research-artifact": "研究产物",
}

VALID_CAPABILITY_STATES = frozenset(
    {"available", "capability-disabled", "unavailable", "forbidden"}
)


def capability(key: str, state: str, *, reason: str = "") -> dict[str, Any]:
    if key not in CAPABILITY_LABELS:  # pragma: no cover - 程序员错误
        raise ValueError(f"未登记的 capability key: {key!r}")
    if state not in VALID_CAPABILITY_STATES:  # pragma: no cover - 程序员错误
        raise ValueError(f"非法 capability state: {state!r}")
    out: dict[str, Any] = {"key": key, "label": CAPABILITY_LABELS[key], "state": state}
    if reason:
        out["reason"] = reason
    return out


def timeline_item(row: ResearchMessageRow) -> dict[str, Any]:
    """`ResearchTimelineItem`（前端类型：id/role/text/createdAt/evidenceRefs
    + 可选 `component` / `attachments`，RV08 加法扩展）。

    `payload` 是服务器写入的结构化投影；这里只经 allowlist 转成
    `component{type, props}`，**不透传**原始 payload，也不产生任何标记。
    """
    item: dict[str, Any] = {
        "id": str(row.id),
        "role": row.role,
        "text": row.text,
        "createdAt": _iso(row.created_at),
        # 硬要求：永远数组
        "evidenceRefs": list(row.evidence_refs or []),
    }
    component = project_component(row.payload)
    if component is not None:
        item["component"] = component
    attachments = project_attachments(row.payload)
    if attachments:
        item["attachments"] = attachments
    return item


def _iso(value: datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def list_messages(db: Session, session_id: uuid.UUID, limit: int = 500) -> list[ResearchMessageRow]:
    return (
        db.query(ResearchMessageRow)
        .filter(ResearchMessageRow.session_id == session_id)
        .order_by(ResearchMessageRow.seq)
        .limit(limit)
        .all()
    )


def _problem(row: ResearchSessionRow) -> dict[str, Any] | None:
    """`problem.prompt` —— MVP 用会话 goal；为空则 `None`（前端允许 null）。"""
    goal = (row.goal or "").strip()
    if not goal:
        return None
    return {"prompt": goal}


def build_capabilities(
    *,
    session_available: bool = True,
    message_state: str = "available",
    message_reason: str = "",
    stream_available: bool = True,
    evidence_count: int = 0,
    artifact_available: bool = False,
    artifact_reason: str = "",
) -> list[dict[str, Any]]:
    """产出**恰好**五个必需 key 的能力数组（顺序稳定，便于前端/测试断言）。"""
    return [
        capability(
            "research-session",
            "available" if session_available else "unavailable",
            reason="" if session_available else "会话不可读",
        ),
        capability("research-message", message_state, reason=message_reason),
        capability(
            "research-stream",
            "available" if stream_available else "unavailable",
            reason="" if stream_available else "事件流不可用",
        ),
        capability(
            "research-evidence",
            "available" if evidence_count else "unavailable",
            reason="" if evidence_count else "本会话尚无已授权证据",
        ),
        capability(
            "research-artifact",
            "available" if artifact_available else "unavailable",
            reason="" if artifact_available else (artifact_reason or "尚无已确认的研究产物"),
        ),
    ]


def build_snapshot(
    db: Session,
    row: ResearchSessionRow,
    *,
    message_state: str = "available",
    message_reason: str = "",
    stream_available: bool = True,
) -> dict[str, Any]:
    """把会话投影成前端契约要求的 `ResearchSnapshot`。

    **加法式变更**（ADR-RV-003 §4）：既有内部字段（`revision` / `summary_ref` /
    `trace_hint`）原样保留，只在上面新增前端要的字段。
    """
    from ..application import session_service as svc  # 延迟 import：避免分层循环

    agg = svc._to_aggregate(row)
    msgs = list_messages(db, row.id)
    evidence = _evidence_projection(db, row.id)
    artifact_focus, artifact_available = artifact_focus_projection(db, row.id, row.revision)

    snapshot: dict[str, Any] = {
        # ---- 既有内部字段（保持不变） ----
        "session": agg.snapshot(),
        "revision": row.revision,
        "summary_ref": row.summary_ref,
        "trace_hint": row.last_event_id,
        # ---- 新增：前端契约 ----
        "contract_version": SNAPSHOT_CONTRACT_VERSION,
        "snapshot_ref": f"snapshot:{row.id}",
        "problem": _problem(row),
        "timeline": [timeline_item(m) for m in msgs],
        "evidence": evidence,
        "capabilities": build_capabilities(
            message_state=message_state,
            message_reason=message_reason,
            stream_available=stream_available,
            evidence_count=len(evidence),
            artifact_available=artifact_available,
        ),
        "artifactFocus": artifact_focus,
    }
    return snapshot


# --------------------------------------------------------------------------
# evidence / artifactFocus —— 归 RV09–RV13 的对象填充（本轮先接上已验证的产物）
# --------------------------------------------------------------------------


def _evidence_projection(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """本会话可见的证据。

    证据资产归平台能力（AssetRef）；本服务只投影**已被引用**的证据 id/label，
    不读任何 Teacher 表。无证据时返回 `[]`（不是 `None`）。
    """
    from .domain_objects import evidence_cards  # 延迟 import：避免循环

    return list(evidence_cards(db, session_id))


def artifact_focus_projection(
    db: Session, session_id: uuid.UUID, revision: int
) -> tuple[dict[str, Any] | None, bool]:
    """构造 `artifactFocus`（ADR-RV-003 §3.4）。

    返回 `(focus | None, 是否存在可用产物)`。前端把 `artifactFocus === null`
    当作"没有可聚焦产物"，因此**没有产物时必须返回 None**，不能返回空壳对象。
    """
    from .domain_objects import build_artifact_focus  # 延迟 import：避免循环

    return build_artifact_focus(db, session_id, revision)
