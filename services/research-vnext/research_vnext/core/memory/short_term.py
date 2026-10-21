"""RV09 · 短期记忆 = ResearchSession / thread 级 checkpoint。

为什么按 `(session_id, thread_key)` 而不是按用户
------------------------------------------------
短期记忆回答的是"**这条对话**进行到哪了"。按用户存会让：

- 用户在会话 A 说"先按这个方法算"，
- 在会话 B（另一个完全不同的题）打开时，助手拿 A 的进度当 B 的前提。

这在产品上表现为"助手记得一件我没在这条对话里说过的事"，
而用户无法自救（他看不到记忆里有什么）。因此 checkpoint 的作用域
**必须**是会话（thread 再细分同会话内的并行话题）。

`tenant_id` 一起进谓词是纵深防御：会话 id 若因某种原因被跨租户猜中，
tenant 不匹配仍会返回空，而不是返回别人的进度。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ResearchThreadCheckpointRow

DEFAULT_THREAD = "main"
#: checkpoint 里允许出现的状态键（白名单）。放任意键 = 把 checkpoint 变成第二份会话记录。
STATE_KEYS: tuple[str, ...] = (
    "stage",
    "lastIntent",
    "openQuestions",
    "confirmedObjectRefs",
    "lastProblemMapRevision",
    "lastResearchMapRevision",
    "notes",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Checkpoint:
    session_id: str
    thread_key: str
    last_message_seq: int
    state: dict[str, Any]
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "threadKey": self.thread_key,
            "lastMessageSeq": self.last_message_seq,
            "state": self.state,
            "updatedAt": self.updated_at,
        }


def sanitize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """只保留白名单键。未知键**丢弃并记录**，不允许它悄悄进库。"""
    out: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in (state or {}).items():
        if key in STATE_KEYS:
            out[key] = value
        else:
            dropped.append(str(key))
    if dropped:
        out["_droppedKeys"] = sorted(dropped)
    return out


def save_checkpoint(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    thread_key: str = DEFAULT_THREAD,
    last_message_seq: int,
    state: dict[str, Any] | None,
) -> Checkpoint:
    """写入/更新本会话该 thread 的 checkpoint（幂等：同 thread 覆盖，不新增行）。"""
    thread = (thread_key or DEFAULT_THREAD).strip() or DEFAULT_THREAD
    row = (
        db.query(ResearchThreadCheckpointRow)
        .filter(
            ResearchThreadCheckpointRow.session_id == session_id,
            ResearchThreadCheckpointRow.thread_key == thread,
        )
        .one_or_none()
    )
    payload = sanitize_state(state)
    if row is None:
        row = ResearchThreadCheckpointRow(
            id=uuid.uuid4(),
            session_id=session_id,
            tenant_id=tenant_id,
            thread_key=thread,
            last_message_seq=int(last_message_seq),
            state=payload,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
    else:
        row.state = payload
        row.last_message_seq = max(int(row.last_message_seq or 0), int(last_message_seq))
        row.updated_at = _now()
    db.flush()
    return Checkpoint(
        session_id=str(row.session_id),
        thread_key=row.thread_key,
        last_message_seq=int(row.last_message_seq),
        state=dict(row.state or {}),
        updated_at=row.updated_at.isoformat(),
    )


def load_checkpoint(
    db: Session,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    thread_key: str = DEFAULT_THREAD,
) -> Checkpoint | None:
    """读本会话该 thread 的 checkpoint。

    **谓词里同时有 session_id 与 tenant_id** —— 跨会话/跨租户读取在 SQL 层
    就返回空，而不是靠调用方自觉。
    """
    thread = (thread_key or DEFAULT_THREAD).strip() or DEFAULT_THREAD
    row = (
        db.query(ResearchThreadCheckpointRow)
        .filter(
            ResearchThreadCheckpointRow.session_id == session_id,
            ResearchThreadCheckpointRow.tenant_id == tenant_id,
            ResearchThreadCheckpointRow.thread_key == thread,
        )
        .one_or_none()
    )
    if row is None:
        return None
    return Checkpoint(
        session_id=str(row.session_id),
        thread_key=row.thread_key,
        last_message_seq=int(row.last_message_seq),
        state=dict(row.state or {}),
        updated_at=row.updated_at.isoformat(),
    )


def list_threads(db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID) -> list[str]:
    rows = db.execute(
        select(ResearchThreadCheckpointRow.thread_key)
        .where(
            ResearchThreadCheckpointRow.session_id == session_id,
            ResearchThreadCheckpointRow.tenant_id == tenant_id,
        )
        .order_by(ResearchThreadCheckpointRow.thread_key)
    ).all()
    return [r[0] for r in rows]


def purge_session(db: Session, *, session_id: uuid.UUID) -> int:
    """删除本会话全部 checkpoint（会话结束时的清理路径）。"""
    n = (
        db.query(ResearchThreadCheckpointRow)
        .filter(ResearchThreadCheckpointRow.session_id == session_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n or 0)


__all__ = [
    "DEFAULT_THREAD",
    "STATE_KEYS",
    "Checkpoint",
    "list_threads",
    "load_checkpoint",
    "purge_session",
    "sanitize_state",
    "save_checkpoint",
]
