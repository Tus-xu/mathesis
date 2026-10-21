"""AI 产出防幻觉评分流水表（P1-3，M2 迭代18）

每行 = 一条 AI 生成题目的防幻觉评分结果（smart_quiz 对话出题 / 组卷补缺等场景）。
评分公式对齐队友离线前测脚本（wf_*前测防幻觉得分/，语义版 v4.0）：
    total = max(0, 100 − A×30 − B×10 − C_deduction)
- A 类（严重幻觉）：质量闸未通过（gates_passed=False）扣 30——产线内由
  smart_quiz 三闸强制执行，未过闸题不进本表，此处仅兜底记录。
- B 类（轻微幻觉）：输出 difficulty ≠ 期望 difficulty 扣 10。
- C 类（知识锚定偏差）：知识点名称 vs 题目文本 BGE-M3 语义相似度分段扣分。
"""

from sqlalchemy import Boolean, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIQualityScore(Base, TimestampMixin):
    """AI 生成题目逐题防幻觉评分流水"""

    __tablename__ = "ai_quality_scores"

    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene: Mapped[str] = mapped_column(String(32), nullable=False)  # smart_quiz_chat 等
    kp_code: Mapped[str] = mapped_column(String(32), default="")
    expected_difficulty: Mapped[str] = mapped_column(String(10), default="")
    output_difficulty: Mapped[str] = mapped_column(String(10), default="")
    q_type: Mapped[str] = mapped_column(String(20), default="")
    question_hash: Mapped[str] = mapped_column(String(64), default="")
    question_text: Mapped[str] = mapped_column(Text, default="")
    gates_passed: Mapped[bool] = mapped_column(Boolean, default=True)  # A 类（False=扣30）
    b_hit: Mapped[bool] = mapped_column(Boolean, default=False)  # B 类：难度漂移
    c_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)  # C 类语义相似度
    c_deduction: Mapped[float] = mapped_column(Float, default=0.0)  # C 类扣分
    total_score: Mapped[float] = mapped_column(Float, default=100.0)
    note: Mapped[str] = mapped_column(String(200), default="")

    __table_args__ = (
        Index("idx_ai_quality_scores_request", "request_id"),
        Index("idx_ai_quality_scores_scene_created", "scene", "created_at"),
    )
