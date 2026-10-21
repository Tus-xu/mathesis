"""题库模型（结构化题库，与 chunks 非结构化知识库并存）

question_bank：结构化题目（题干/题型/选项/答案/解析/难度/知识点数组/来源/年份/真题标记），
供刷题/组卷"题库优先"精确检索（kp 数组重叠 + 题型 + 难度过滤）；chunks 继续服务 RAG 语义检索。
hash = 规范化题干 sha256（导入去重/幂等重跑依据）。
embedding 可空：导入时 best-effort 生成，服务不可用落 NULL 不阻塞入库。
"""

import hashlib
import re

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin

# 题型/难度三枚举（与判分契约 student_router._VALID_Q_TYPES 一致）
BANK_Q_TYPES = ("choice", "blank", "solution")
BANK_DIFFICULTIES = ("easy", "medium", "hard")


def stem_hash(stem: str) -> str:
    """题干去重 hash：剥掉全部空白字符后 sha256（同题异空格/换行视为同题）"""
    normalized = re.sub(r"\s+", "", stem or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class QuestionBank(Base, TimestampMixin, SoftDeleteMixin):
    """题库表（结构化题目，软删）"""

    __tablename__ = "question_bank"

    stem: Mapped[str] = mapped_column(Text)  # 题干（公式 $...$ LaTeX）
    q_type: Mapped[str] = mapped_column(String(20))  # choice/blank/solution
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 选择题 {"A": "A. ...", ...}
    answer: Mapped[str] = mapped_column(Text)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)  # 解析
    difficulty: Mapped[str] = mapped_column(String(10), default="medium")  # easy/medium/hard
    kp_codes: Mapped[list] = mapped_column(ARRAY(String(32)), server_default="{}")  # 知识点 code 数组
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 来源，如"2023新课标I卷"
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 年份（真题）
    is_real_exam: Mapped[bool] = mapped_column(Boolean, default=False)  # 真题标记
    embedding: Mapped[list | None] = mapped_column(Vector(1024), nullable=True)  # best-effort，可空
    hash: Mapped[str] = mapped_column(String(64), unique=True)  # stem_hash，导入去重唯一约束

    # 迭代09+ 题库治理新增字段（与 DDL 对齐）
    image: Mapped[list] = mapped_column(JSONB, server_default="[]")  # 图片 URL 数组（管道预留）
    figure_params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 参数化配图参数（figure_renderer 渲染）
    is_competition: Mapped[bool] = mapped_column(Boolean, server_default="false")  # 竞赛题标记
    out_of_syllabus: Mapped[bool] = mapped_column(Boolean, server_default="false")  # 超纲 flag
    source_batch: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 溯源批次
    scope: Mapped[str] = mapped_column(String(16), server_default="student")  # 三端隔离 student/teacher/research
    kp_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ok/failed
    kp_confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)  # high/mid/low
    kp_granular: Mapped[str | None] = mapped_column(String(16), nullable=True)  # module_only/section
    kp_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # rule/llm/hybrid
    annotate_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 标注元数据（reason 等）

    __table_args__ = (
        # kp 数组重叠检索（&&）走 GIN
        Index("idx_question_bank_kp_codes", "kp_codes", postgresql_using="gin"),
        # 题型+难度过滤（活跃行）
        Index(
            "idx_question_bank_type_diff",
            "q_type",
            "difficulty",
            postgresql_where="deleted_at IS NULL",
        ),
    )
