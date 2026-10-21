"""R19 教师侧教学解释证据事实表（8100 权威，前缀 tp_）。

边界（R19 DECISION-IMPLEMENTATION-DELTA D2/D5/D8）：
- 8100 **不复制** 8000 提交整行（无 answer_text/附件/昵称/明文 user_id 列）；
- `value` 只存聚合数字（计数/比率/分档）；`refs` 只存 typed refs（kind +
  external/contract id + item_no），指向 8000 事实行实现**证据可追溯**；
- `student_key` 等伪名化素材**禁止**落库（内存用后即弃）；
- `status`：valid（可用）/ invalid（证据失效：重算后旧证据不再支撑当前 revision，
  保留审计但不进入新画像 evidence_refs）；
- 每个指标族一行（bounded：coverage/confidence/anomaly 各 1 + 每题 difficulty +
  每 KP mastery/misconception），同一 profile 同一 revision 由
  `metrics_fingerprint` 幂等去重（重算 no-op 不重复落证据）；
- AI 解释（ev_kind=interpretation, source=ai_suggestion）是 proposal：教师确认前
  不进入正式 evidence_refs 引用集。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy import Uuid as SAUuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JsonB = JSONB().with_variant(JSON(), "sqlite")

EVIDENCE_KINDS = (
    "coverage",
    "mastery",
    "misconception",
    "difficulty",
    "confidence",
    "anomaly",
    "interpretation",
)
EVIDENCE_STATUSES = ("valid", "invalid")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TeachingEvidenceRow(Base):
    """教学解释证据：聚合数字 + typed refs（可追溯；零 PII 行复制）。"""

    __tablename__ = "tp_teaching_evidence"
    __table_args__ = (
        CheckConstraint(
            "ev_kind IN ('coverage','mastery','misconception','difficulty',"
            "'confidence','anomaly','interpretation')",
            name="ck_tp_tev_kind",
        ),
        CheckConstraint("status IN ('valid','invalid')", name="ck_tp_tev_status"),
        CheckConstraint("sample_size >= 0", name="ck_tp_tev_sample"),
        Index("ix_tp_tev_class", "class_id"),
        Index("ix_tp_tev_profile", "profile_id"),
        Index("ix_tp_tev_metric", "metric_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), primary_key=True, default=_uuid)
    class_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tp_sessions.id", ondelete="CASCADE"), nullable=False
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tp_class_learning_profiles.id", ondelete="SET NULL"), nullable=True
    )
    assignment_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    ev_kind: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="valid")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="system")

    value: Mapped[dict[str, Any]] = mapped_column(JsonB, nullable=False)
    refs: Mapped[list[Any]] = mapped_column(JsonB, nullable=False, default=list)

    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calc_version: Mapped[str] = mapped_column(Text, nullable=False)
    metrics_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actor_id: Mapped[uuid.UUID] = mapped_column(SAUuid(as_uuid=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = ["TeachingEvidenceRow", "EVIDENCE_KINDS", "EVIDENCE_STATUSES"]
