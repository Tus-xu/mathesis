"""M2 流水表模型（SSOT §6.1 / §6.4）

speech_logs: 语音转 LaTeX 流水（纯流水表）
search_logs: 联网搜索流水（纯流水表）
xingchen_kb_mappings: 星辰知识库映射（软删）
kb_eval_runs: RAG 评测结果（纯流水表，ADR-016）
router_eval_logs: 路由影子评测流水（纯流水表，ADR-022）
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class SpeechLog(Base):
    """语音转 LaTeX 流水表（纯流水：id + created_at）"""

    __tablename__ = "speech_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    session_id: Mapped[str] = mapped_column(String(64))
    asr_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    latex: Mapped[str | None] = mapped_column(Text, nullable=True)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    engine: Mapped[str] = mapped_column(String(30))  # wf_speech_to_latex / spark_direct
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))  # success/error/fallback
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchLog(Base):
    """联网搜索流水表（纯流水）"""

    __tablename__ = "search_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    query: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))  # xingchen_wf
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    top_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class XingchenKbMapping(Base, TimestampMixin, SoftDeleteMixin):
    """星辰知识库映射表（f4 §4.1 "只存映射"）"""

    __tablename__ = "xingchen_kb_mappings"

    chunk_id: Mapped[str] = mapped_column(String(64))  # 字符串引用不留外键
    xingchen_doc_id: Mapped[str] = mapped_column(String(128))
    xingchen_kb_id: Mapped[str] = mapped_column(String(128))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KbEvalRun(Base):
    """RAG 评测结果表（纯流水，ADR-016）"""

    __tablename__ = "kb_eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_set: Mapped[str] = mapped_column(String(64))  # 如 rag_gaokao_100
    recall_at_5: Mapped[float] = mapped_column(Numeric)
    mrr: Mapped[float] = mapped_column(Numeric)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class RouterEvalLog(Base):
    """路由影子评测流水表（ADR-022）"""

    __tablename__ = "router_eval_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    utterance: Mapped[str] = mapped_column(Text)
    workspace: Mapped[str] = mapped_column(String(20))
    local_decision: Mapped[str] = mapped_column(String(30))
    xc_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    agree: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
