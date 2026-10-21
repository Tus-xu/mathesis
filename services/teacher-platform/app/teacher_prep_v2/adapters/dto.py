"""8000 事实模型 DTO（R13）：Assignment/Submission/ClassMembership 的只读镜像。

这些 DTO 只描述 8000 既有表/接口的载荷（read-model），**不是**新事实表；
字段名与 8000（services/api）保持逐字对齐，避免 mapping 层作文义翻译漂移。
对齐依据（services/api）：
- tables: assignments / assignment_targets / submissions / submission_items / class_members
- teacher 路由: /api/teacher/assignments(create/list/publish/close/archive)
- student 路由: /api/student/practice/submit
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.teacher_prep_v2.contracts.base import OpaqueRef, UuidStr


class _RoModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ApiAssignmentCreate(_RoModel):
    """POST /api/teacher/assignments 请求体（create 幂等键 = client_assignment_id）。

    tel T3：逐字对齐 8000 ``CreateAssignmentRequest``（services/api/app/domains/teacher/
    schemas.py，``_Strict`` extra="forbid"）——

    - ``creator_id`` 由 8000 从教师 JWT 裁决（require_teacher_role），**不得出现在载荷**；
    - ``quiz_id`` / ``source_artifact_id`` / ``status`` 由 8000 自行物化/回填
      （assessment.create_assignment：_materialize_quiz + 恒建 draft），发送必 422；
    - ``artifact_id``：8000 教师名下「已确认 quiz_set artifact」（tel F1 起与 ``quiz_items``
      二选一；两者皆缺 8000 仍 422 拒绝，fail-closed）；
    - ``quiz_items``：内联题组（tel F1 新契约，8000 ``CreateAssignmentRequest.quiz_items``）——
      备课诊断无 8100→8000 artifact 物化链，学前诊断直发题目（学生端真实可作答）；
    - ``type``：tel D2/A3 语义扩展——quiz（课堂练习）/ pre_class（学前作业）/ homework（课后习题）。
    """

    class_id: UuidStr
    title: str = Field(min_length=1, max_length=200)
    artifact_id: UuidStr | None = None
    quiz_items: list[dict[str, Any]] | None = None
    client_assignment_id: str = Field(min_length=8, max_length=64)
    deadline: datetime | None = None
    type: Literal["quiz", "pre_class", "homework"] = "quiz"  # noqa: A003


class ApiAssignmentRow(_RoModel):
    """8000 assignments 行（对账/重放读取）。

    tel F1 实契约对齐：8000 列表行（_serialize_assignment）键为 ``assignment_id``、
    不含 ``creator_id``——适配器读回时把 ``assignment_id`` 归一为 ``id``，
    ``creator_id`` 放宽为可选（教师域端点已按 token 裁决，行内不再重复）。
    """

    id: UuidStr
    class_id: UuidStr
    creator_id: UuidStr | None = None
    title: str
    type: str  # noqa: A003
    quiz_id: UuidStr | None = None
    lesson_id: str | None = None
    deadline: datetime | None = None
    status: str = "published"
    client_assignment_id: str | None = None


class ApiSubmissionItem(_RoModel):
    """8000 submission_items 行（作答明细；attachments 为 JSONB 附件数组）。"""

    item_no: int = Field(ge=1)
    answer_text: str | None = None
    file_id: UuidStr | None = None
    attachments: list[dict] = Field(default_factory=list)
    verdict: str = "pending_review"  # correct | wrong | pending_review
    score: float | None = None
    ai_pregraded: bool = False
    suggested_score: float | None = None
    teacher_final_score: float | None = None
    needs_review: bool = False


class ApiSubmissionRow(_RoModel):
    """8000 submissions 行（提交事实；client_submit_id 为用户级唯一幂等键）。"""

    id: UuidStr
    user_id: UuidStr
    quiz_id: UuidStr | None = None
    assignment_id: UuidStr | None = None
    client_submit_id: str = Field(min_length=1, max_length=64)
    status: str = "pending_review"  # graded | pending_review
    total_score: float | None = None
    created_at: datetime | None = None
    items: list[ApiSubmissionItem] = Field(default_factory=list)
    created_by_app: str | None = None  # 供诊断：哪个入口写入


class ApiMembershipRow(_RoModel):
    """8000 class_members 成员事实（deleted_at 已软删视为非成员）。"""

    class_id: UuidStr
    user_id: UuidStr
    member_role: str = Field(pattern=r"^(teacher|student|owner|assistant)$")
    confirmed: bool = True
    deleted: bool = False
    nickname_in_class: str | None = None


class ApiAssignmentListResponse(_RoModel):
    """GET /api/teacher/assignments 单页响应（列表投影）。"""

    items: list[ApiAssignmentRow] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


ApiOpaqueRef = OpaqueRef
