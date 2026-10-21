"""M3 教师端 Pydantic 请求/响应 Schema（extra="forbid"）。

仅定义 HTTP 层契约；深层业务由领域服务映射为 Artifact/业务对象。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------- Today ----------------


class TodayRequest(_Strict):
    date: str | None = None


# ---------------- Insights ----------------


class _BaseInsight(_Strict):
    class_id: uuid.UUID
    actionable: bool = False


# ---------------- Lessons ----------------


class AdaptLessonRequest(_Strict):
    class_id: uuid.UUID
    topic: str = Field(min_length=1, max_length=200)
    source_artifact_id: uuid.UUID | None = None
    # Uploaded material is resolved server-side against the current teacher.  The
    # client supplies IDs only, never extracted text or an arbitrary file path.
    source_resource_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    source_refs: list[str] = Field(default_factory=list)
    requirements: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=240)

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("topic must not be blank")
        return normalized


class ApplyInsightRequest(_Strict):
    insight_id: uuid.UUID
    version: int = Field(ge=1)
    instruction: str | None = None


class CreateLessonRequest(_Strict):
    class_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    topic: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=240)
    content: dict[str, Any] = Field(default_factory=dict)


class CreateSlidesRequest(_Strict):
    version: int = Field(ge=1)
    style: str | None = None
    page_range: tuple[int, int] | None = None
    requirements: str | None = None


class ExplainRequest(_Strict):
    question: str = Field(min_length=1)
    reference_solution: str | None = None
    target_minutes: int | None = None


# ---------------- Assessment / Quiz ----------------


class _DifficultySpec(_Strict):
    easy: float | None = Field(default=None, ge=0)
    medium: float | None = Field(default=None, ge=0)
    hard: float | None = Field(default=None, ge=0)


class _QuestionTypeSpec(_Strict):
    choice: int = Field(default=0, ge=0)
    blank: int = Field(default=0, ge=0)
    text: int = Field(default=0, ge=0)


class GenerateQuizRequest(_Strict):
    class_id: uuid.UUID
    knowledge_points: list[str] = Field(default_factory=list)
    count: int = Field(ge=1, le=100)
    question_types: _QuestionTypeSpec = Field(default_factory=_QuestionTypeSpec)
    difficulty: _DifficultySpec = Field(default_factory=_DifficultySpec)
    exclude_hashes: list[str] = Field(default_factory=list)


class CreateQuizItemIn(_Strict):
    """内联题组单题（tel F1：备课诊断无 8000 artifact 物化链时的直发内容）。

    字段与 quiz_set artifact payload 的 item 形态对齐（_materialize_quiz 消费口径），
    8000 侧据此直接物化 Quiz + QuizItems（与 artifact 路径同一落库事实）。
    """

    item_no: int = Field(ge=1)
    q_type: Literal["choice", "judge", "blank", "solution"]
    question_text: str = Field(min_length=1, max_length=2000)
    options: dict | None = None
    answer: str = Field(min_length=1, max_length=2000)
    analysis: str | None = Field(default=None, max_length=4000)
    kp_code: str | None = Field(default=None, max_length=64)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    max_score: float | None = Field(default=None, ge=0.5, le=100)
    grading_rubric: list | None = None


class CreateAssignmentRequest(_Strict):
    class_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    artifact_id: uuid.UUID | None = None  # 已确认的 quiz_set artifact；内联 quiz_items 时可省
    quiz_items: list[CreateQuizItemIn] | None = None  # 内联题组（诊断直发）；与 artifact_id 二选一
    client_assignment_id: str = Field(min_length=1, max_length=64)
    deadline: str | None = None
    # tel D2/A3：任务类型扩展——quiz（课堂练习）/ pre_class（学前作业）/ homework（课后习题）。
    # type 是 String(20) 无 CheckConstraint（coursework.py），代码级扩展零 DDL；存量 quiz/watch 不动。
    type: Literal["quiz", "pre_class", "homework"] = "quiz"

    @model_validator(mode="after")
    def _artifact_xor_quiz_items(self):  # noqa: ANN001 —— pydantic 自检方法
        if (self.artifact_id is None) == (self.quiz_items is None):
            raise ValueError("artifact_id 与 quiz_items 必须二选一")
        return self


class PublishAssignmentRequest(_Strict):
    client_request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)


# ---------------- Grading ----------------


class SuggestGradeRequest(_Strict):
    class_id: uuid.UUID
    client_request_id: str = Field(min_length=1, max_length=128)


class ConfirmGradeRequest(_Strict):
    suggestion_id: uuid.UUID
    decision: Literal["accept", "override"]
    final_score: float | None = Field(default=None, ge=0)
    teacher_feedback: str | None = None
    version: int = Field(default=1, ge=1)


class SetGradingReviewRequest(_Strict):
    state: Literal["pending", "cleared"]
    note: str | None = Field(default=None, max_length=500)
    client_request_id: str = Field(min_length=1, max_length=128)


class BatchConfirmItem(_Strict):
    submission_item_id: uuid.UUID
    suggestion_id: uuid.UUID
    decision: Literal["accept", "override"]
    final_score: float | None = Field(default=None, ge=0)
    teacher_feedback: str | None = None
    version: int = Field(default=1, ge=1)


class BatchConfirmRequest(_Strict):
    items: list[BatchConfirmItem] = Field(max_length=200)


# ---------------- Classroom ----------------


class ClassroomModeRequest(_Strict):
    enabled: bool
    lesson_id: uuid.UUID | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    client_request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)


class VideoInsightsRequest(_Strict):
    lesson_id: uuid.UUID | None = None


# ---------------- Resources ----------------


class CreateExternalResourceReferenceRequest(_Strict):
    title: str = Field(min_length=1, max_length=240)
    url: str = Field(min_length=12, max_length=2048, pattern=r"^https?://")
    provider: str | None = Field(default=None, max_length=120)
    attribution: str | None = Field(default=None, max_length=500)
    intended_use: str | None = Field(default=None, max_length=500)
    class_id: uuid.UUID | None = None


class PreprocessRequest(_Strict):
    client_request_id: str = Field(min_length=1, max_length=128)


class UnderstandRequest(_Strict):
    question: str | None = None
    output_type: str | None = None
    client_request_id: str = Field(min_length=1, max_length=128)


class QuestionCandidate(_Strict):
    candidate_id: str | None = Field(default=None, max_length=64)
    stem: str = Field(min_length=1, max_length=10000)
    q_type: Literal["choice", "blank", "solution"]
    answer: str = Field(min_length=1, max_length=4000)
    options: dict[str, str] | None = None
    analysis: str | None = Field(default=None, max_length=10000)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)


class SaveQuestionCandidatesRequest(_Strict):
    candidates: list[QuestionCandidate] = Field(min_length=1, max_length=100)


class ApproveQuestionCandidatesRequest(_Strict):
    candidate_ids: list[str] = Field(min_length=1, max_length=100)


# ---------------- Capability Gateway ----------------


class CapabilityRequest(_Strict):
    scene: str = Field(min_length=1, max_length=40)
    class_id: uuid.UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    client_request_id: str = Field(min_length=1, max_length=128)


# ---------------- Artifact Task 通用 ----------------


class ArtifactActionRequest(_Strict):
    client_request_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=256)


class ArtifactUpdateRequest(_Strict):
    version: int = Field(ge=1)
    content: dict[str, Any]
    source_refs: list[str] | None = None


class CancelTaskRequest(_Strict):
    client_request_id: str = Field(min_length=1, max_length=128)
