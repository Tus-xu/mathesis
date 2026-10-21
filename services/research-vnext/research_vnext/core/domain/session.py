"""ResearchSession 聚合根（RV02）。

任务书要求 1 —— 最少字段：
id / tenant_id / project_id / owner_id / title / status / revision /
created_at / updated_at / current_artifact_refs / summary_ref / policy_refs。

PostgreSQL 是事实权威；本聚合只在内存中表达不变量，落库由 repository 负责。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .errors import InvalidTransition
from .events import DomainEvent
from .state_machine import SessionState, can_transition


class SessionMode(str, Enum):
    MODELING = "MODELING"
    PAPER_READING = "PAPER_READING"
    PAPER_REVIEW = "PAPER_REVIEW"
    RESEARCH_TRAINING = "RESEARCH_TRAINING"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchSession(BaseModel):
    """ResearchSession 聚合根。"""

    id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    title: str = Field(min_length=1, max_length=256)
    mode: SessionMode = SessionMode.MODELING
    status: SessionState = SessionState.DRAFT
    revision: int = 0
    created_at: datetime
    updated_at: datetime
    current_artifact_refs: list[str] = Field(default_factory=list)
    summary_ref: str
    policy_refs: list[str] = Field(default_factory=list)

    # 不变量支撑字段（不对外暴露为业务字段）
    confirmation_count: int = 0
    last_event_id: str | None = None
    goal: str = ""

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        session_id: uuid.UUID,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        owner_id: uuid.UUID,
        title: str,
        mode: SessionMode = SessionMode.MODELING,
        policy_refs: list[str] | None = None,
        goal: str = "",
        occurred_at: datetime | None = None,
    ) -> "ResearchSession":
        now = occurred_at or _now()
        return cls(
            id=session_id,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=owner_id,
            title=title,
            mode=mode,
            status=SessionState.DRAFT,
            revision=0,
            created_at=now,
            updated_at=now,
            current_artifact_refs=[],
            summary_ref=f"summary:{session_id}@v1",
            policy_refs=policy_refs or ["policy:research.default@v1"],
            confirmation_count=0,
            goal=goal,
        )

    # ------------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------------

    def transition(
        self,
        target: SessionState,
        *,
        at: datetime | None = None,
        confirmation: bool = False,
        partial_reason: dict | str | None = None,
    ) -> None:
        """执行一次状态转换；revision 严格 +1。"""
        can_transition(
            self.status,
            target,
            confirmation_count=self.confirmation_count,
            partial_reason=partial_reason,
        )
        now = at or _now()
        self.status = target
        self.revision += 1
        self.updated_at = now
        if confirmation:
            self.confirmation_count += 1

    def set_artifact_refs(self, refs: list[str], *, at: datetime | None = None) -> None:
        self.current_artifact_refs = list(refs)
        self.revision += 1
        self.updated_at = at or _now()

    def bump_summary(self, version: int) -> None:
        self.summary_ref = f"summary:{self.id}@v{version}"

    def record_event(self, ev: DomainEvent) -> None:
        self.last_event_id = ev.event_id

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id),
            "owner_id": str(self.owner_id),
            "title": self.title,
            "mode": self.mode.value,
            "status": self.status.value,
            "revision": self.revision,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "current_artifact_refs": list(self.current_artifact_refs),
            "summary_ref": self.summary_ref,
            "policy_refs": list(self.policy_refs),
            "confirmation_count": self.confirmation_count,
            "last_event_id": self.last_event_id,
        }


__all__ = ["ResearchSession", "SessionMode", "SessionState", "InvalidTransition"]
