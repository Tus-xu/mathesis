"""试卷题库表模型（M2 迭代17 AI 管家化 · 补全模拟考试业务）

exam_papers：导入的真题/模拟套卷（套卷管理，结构化落库）；
exam_paper_items：套卷题目（题干/选项/答案/解析/知识点/难度/分值）。

与 exam_router 生成的 quiz（source="exam:*"）关系：
- 本表是"套卷素材库"（教师/内容侧导入的真实套卷），可整卷或抽题组卷；
- exam_router 的 /generate 仍走 quizzes（题库真题优先 + AI 补缺口）即时组卷；
- 管家"模拟考试推荐"可基于本表推送完整套卷，或按薄弱点抽题组卷。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ExamPaper(Base, TimestampMixin):
    """套卷表（导入真题/模拟卷，软删）"""

    __tablename__ = "exam_papers"
    __table_args__ = (Index("idx_exam_papers_scope_year", "scope", "year"),)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 如"2023新课标I卷"
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(String(16), nullable=False, server_default="math")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, server_default="student")  # student/teacher/research
    total_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structure: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 题型结构 {choice: n, blank: n, solution: n}
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")  # active/archived


class ExamPaperItem(Base, TimestampMixin):
    """套卷题目表（软删）"""

    __tablename__ = "exam_paper_items"
    __table_args__ = (Index("idx_exam_paper_items_paper_no", "paper_id", "item_no"),)

    paper_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("exam_papers.id"), nullable=False)
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    q_type: Mapped[str] = mapped_column(String(20), nullable=False)  # choice/blank/solution
    stem: Mapped[str] = mapped_column(Text, nullable=False)  # 题干（$...$ LaTeX）
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 选择题 {"A": "...", ...}
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    kp_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), nullable=False, server_default="medium")  # easy/medium/hard
    score: Mapped[float | None] = mapped_column(Numeric, nullable=True)  # 单题分值
