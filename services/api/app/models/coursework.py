"""教学任务域模型（ADR-M2B-002）

quizzes: 题组（软删）
quiz_items: 题目（软删）
submissions: 提交（软删）
submission_items: 作答明细（软删）
daily_questions: 每日一题（纯流水）
streaks: 打卡（单行/用户）
mastery_records: 掌握度
assignments: 教师任务（软删，M2 最小版）
assignment_targets: 定向（软删）
error_records: 错题记录（软删）
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin


class Quiz(Base, TimestampMixin, SoftDeleteMixin):
    """题组表"""

    __tablename__ = "quizzes"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    source: Mapped[str] = mapped_column(String(20))  # ai_generated/bank/daily/retry/assignment
    title: Mapped[str] = mapped_column(String(200))
    kp_codes: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/archived


class QuizItem(Base, TimestampMixin, SoftDeleteMixin):
    """题目表"""

    __tablename__ = "quiz_items"

    quiz_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quizzes.id"), index=True)
    item_no: Mapped[int] = mapped_column(Integer)
    q_type: Mapped[str] = mapped_column(String(20))  # choice/judge/blank/solution
    question_text: Mapped[str] = mapped_column(Text)
    options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer: Mapped[str] = mapped_column(Text)
    answer_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    kp_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10), default="medium")  # easy/medium/hard
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    sympy_check_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 题库真题来源（AI 题为 NULL）
    image: Mapped[list] = mapped_column(JSONB, server_default="[]")  # 配图（data URI / URL 列表，P2-5）
    # V2 批改量规：历史题可能尚未补齐，空值必须如实进入人工判断，不得推断默认分值。
    max_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    grading_rubric: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class Submission(Base, TimestampMixin, SoftDeleteMixin):
    """提交表"""

    __tablename__ = "submissions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    quiz_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quizzes.id"), nullable=True)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    client_submit_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="graded")  # graded/pending_review
    total_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    __table_args__ = (
        Index(
            "uq_submissions_user_client",
            "user_id",
            "client_submit_id",
            unique=True,
            postgresql_where="deleted_at IS NULL",
        ),
    )


class SubmissionItem(Base, TimestampMixin, SoftDeleteMixin):
    """作答明细表"""

    __tablename__ = "submission_items"

    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), index=True
    )
    item_no: Mapped[int] = mapped_column(Integer)
    q_type: Mapped[str] = mapped_column(String(20))
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # 多图片/多附件作业（学生端图片作业上传，M2 迭代扩展）：
    # [{file_id, filename, mime, status(photo|ready), ocr_text?}]
    attachments: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    verdict: Mapped[str] = mapped_column(String(20))  # correct/wrong/pending_review
    score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_pregraded: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # ===== M3 教师端扩展：AI 建议分与正式终评分分离 =====
    # 建议分（AI/规则产出，draft），仅教师确认后才同步到 score
    suggested_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    suggestion_rationale: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 得分点依据（脱敏）
    suggestion_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion_confidence: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    suggestion_status: Mapped[str] = mapped_column(
        String(20), default="draft"
    )  # draft|accepted|overridden|applied
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)  # 低置信/OCR 不清/Schema 非法
    # 正式终评分（教师确认写入）
    teacher_final_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    teacher_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DailyQuestion(Base):
    """每日一题（按用户流水：每生每天一题，学情化选题）

    M2 迭代17 AI 管家化：由"全站统一一题"改为"每生一题"，
    知识点按该生薄弱点 Top 加权轮换（date.today().toordinal()%len 静态轮换已废弃）。
    """

    __tablename__ = "daily_questions"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_questions_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    quiz_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quizzes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Streak(Base):
    """打卡表（单行/用户）"""

    __tablename__ = "streaks"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MasteryRecord(Base):
    """掌握度表（PK = user_id + kp_id）"""

    __tablename__ = "mastery_records"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    kp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id"), primary_key=True
    )
    mastery: Mapped[float] = mapped_column(Numeric, default=0.5)  # 0~1 BKT 后验
    practice_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    hint_count: Mapped[int] = mapped_column(Integer, default=0)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Assignment(Base, TimestampMixin, SoftDeleteMixin):
    """教师任务表（M2 最小版 + M3 教师端扩展）"""

    __tablename__ = "assignments"

    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id"), index=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))  # quiz/watch/pre_class/homework（tel D2 代码级扩展，无 CheckConstraint）
    quiz_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("quizzes.id"), nullable=True)
    lesson_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="published")  # draft/published/closed/archived（M2 存量默认 published）
    # ===== M3 教师端扩展 =====
    # 客户端幂等键：教师创建 assignment 由前端生成的请求唯一标识（幂等）
    client_assignment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 来源题集 Artifact（已确认的 quiz_set teaching_artifact）
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teaching_artifacts.id"), nullable=True
    )


class AssignmentTarget(Base, TimestampMixin, SoftDeleteMixin):
    """任务定向表"""

    __tablename__ = "assignment_targets"

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assignments.id"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(20))  # class/group/student
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ErrorRecord(Base, TimestampMixin, SoftDeleteMixin):
    """错题记录表（ADR-002 / ADR-M2B-003 #1 补列）"""

    __tablename__ = "error_records"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    question_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {question_id 或 chunk_id}
    source_channel: Mapped[str] = mapped_column(String(20))  # manual_photo/auto_judge/chat_command
    error_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # concept/formula/calculation/logic/reading
    kp_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ai_judged: Mapped[bool] = mapped_column(Boolean, default=False)
    corrected_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0)  # 间隔复习完成次数（1/3/7/15 推进依据，SSOT §6.3）
    # M2 迭代16 第二批：答错次数（forgotten 复习 +1；存量行 server_default=1，与 enrich_error_fsrs 保守口径一致）
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 学生备注（PATCH 可改）
    image: Mapped[list] = mapped_column(JSONB, server_default="[]")  # 题目配图快照（P2-5）
    # om5：来源细分（self_test/chat_quiz/socratic/mock_exam/variant/manual），可空不回填历史
    origin: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # om8：正解持久化（首次生成后缓存，避免每次打开详情重复调模型；图形失败不回滚正解）
    generated_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    solution_figure: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")  # 正解示意图（规范 image/ggb 契约）
    solution_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ===== M2 迭代16：FSRS 缓存列（纯扩展，读取时计算回填；write-path 已接入 complete_error_review） =====
    fsrs_stability: Mapped[float | None] = mapped_column(Numeric, nullable=True)  # 记忆稳定度 S（天）
    fsrs_difficulty: Mapped[float | None] = mapped_column(Numeric, nullable=True)  # 难度 D（1~10，预留）
    fsrs_retrievability: Mapped[float | None] = mapped_column(Numeric, nullable=True)  # 最近计算的可提取性 R
    fsrs_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_error_records_user_kp", "user_id", "kp_code"),
    )
