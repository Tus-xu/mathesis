"""课堂会话模型（F9+ 双师课堂：OpenMAIC 融合改造，迭代一版）

一节课 = 一份 AI 主讲课堂：
- outlines: 大纲（{order,type,title,subtitle,kp_code,key_points,narration,minutes}）
- slides:   逐页内容（{order,title,kp_code,narration,minutes,blocks:[{kind,text|latex|question|analysis|answer}]}）
生成两段式（大纲→逐页内容），数据驱动自课程预处理产物（章节/知识点/知识卡）。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ClassroomSession(Base, TimestampMixin):
    """AI 数学课堂会话"""

    __tablename__ = "classroom_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 可选关联课程（OpenMAIC 语义：输入 topic 即可生成；course_id 仅作增强上下文）
    course_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)  # 属主（教师或学生）
    title: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(20), default="sync")  # sync/review/topic
    slide_count: Mapped[int] = mapped_column(default=10)
    status: Mapped[str] = mapped_column(String(20), default="generating")  # generating/ready/failed
    outlines: Mapped[list] = mapped_column(JSONB, server_default="[]")  # 大纲数组
    slides: Mapped[list] = mapped_column(JSONB, server_default="[]")  # 逐页内容
    engine: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 生成引擎标注
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # ===== 阶段一新增：来源/进度/笔记/问答/验证/知识点/软删除 =====
    # 来源类型：topic（主题）/ photo（拍题）/ file（PDF·DOCX·PPT 教案）
    source_type: Mapped[str] = mapped_column(String(20), default="topic", server_default="topic")
    # 原件留存：{filename, page, region, raw_meta, status, retry_reason}
    source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 学习进度（继续学习闭环）：{slide_index, page_check:{idx:ok|again}, completed_at}
    progress: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # 服务端笔记（Markdown/纯文本）
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 问答摘要与错因摘要：{messages:[{role,text}], error_summary}
    qa_summary: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # 数学验证结果：{per_slide:[{idx,status,detail}], overall}
    verification: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # 知识点锚定：[kp_code, ...]（历史按知识点筛选）
    knowledge_points: Mapped[list] = mapped_column(JSONB, server_default="[]")
    # 分层练习：{basic:[{question,options,answer,analysis}], advanced:[...], challenge:[...]}
    practice: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 练习作答统计：{basic:{total,correct}, advanced:{...}, challenge:{...}}
    practice_stats: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    # 内容版本（只重生错误页时的追溯锚点）
    content_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 软删除（历史课堂闭环）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
