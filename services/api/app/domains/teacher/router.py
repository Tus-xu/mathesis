"""M3 教师端 HTTP 路由（§「router 只做 HTTP 解析、鉴权、响应」）。

- 统一信封 {code,message,data}；错误码按 API v2.1；
- P0 主链 + Artifact/任务 + P1 教案/作业/批改/资源 + Capability Gateway；
- 静态路由（/queue、/generate 等）声明在 {id} 参数路由之前，避免被吞。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher import artifacts as art
from app.domains.teacher import (
    assessment,
    classroom,
    classroom_session,
    grading,
    insights,
    lessons,
    resources,
    submission_facts,
    today,
)
from app.domains.teacher.capability_gateway import run_capability
from app.domains.teacher.registry import build_teacher_registry
from app.domains.teacher.schemas import (
    AdaptLessonRequest,
    ApplyInsightRequest,
    ApproveQuestionCandidatesRequest,
    ArtifactActionRequest,
    ArtifactUpdateRequest,
    BatchConfirmRequest,
    CancelTaskRequest,
    CapabilityRequest,
    ClassroomModeRequest,
    ConfirmGradeRequest,
    CreateAssignmentRequest,
    CreateExternalResourceReferenceRequest,
    CreateLessonRequest,
    CreateSlidesRequest,
    ExplainRequest,
    GenerateQuizRequest,
    PreprocessRequest,
    PublishAssignmentRequest,
    SaveQuestionCandidatesRequest,
    SetGradingReviewRequest,
    SuggestGradeRequest,
    UnderstandRequest,
)
from app.domains.teacher.scope import require_teacher_role, require_verified_teacher
from app.gateway.auth import get_current_user
from app.models.database import get_db
from app.models.teacher import TeacherTask

router = APIRouter(
    prefix="/api/teacher",
    tags=["teacher"],
    dependencies=[Depends(require_verified_teacher)],
)


class TeacherButlerChatRequest(BaseModel):
    """前端只可提交业务上下文，不可指定工具、模型、Provider 或 workflow。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    scene: str
    class_id: uuid.UUID | None = Field(default=None, alias="classId")
    artifact_id: uuid.UUID | None = Field(default=None, alias="artifactId")
    user_message: str = Field(min_length=1, max_length=4000, alias="userMessage")
    client_request_id: str = Field(min_length=1, max_length=128, alias="clientRequestId")


_TEACHER_PLANNER_PROMPT = (
    "你是高中数学教师的AI教研助手，只输出 ActionPlan JSON。"
    "只能选择当前场景列出的 teacher.* 工具；班级数据不足时不得编造。"
    "生成内容一律是待教师核对的草稿；发布、确认分数、课堂模式等正式写操作"
    "必须要求教师通过专用接口显式确认，不得在对话中自动执行。"
    '结构为 {"intent":str,"goal":str,"actions":[{"tool_name":str,'
    '"arguments":{},"reason":str}],"response_mode":"direct"|"cards"|'
    '"socratic"|"degraded","needs_web_search":false}。'
)


def _ok(data, request: Request | None = None) -> dict:
    """M3 统一成功信封（审计 I-02）：含 request_id，与 SSOT 对齐。"""
    return {
        "code": 0,
        "message": "ok",
        "data": data,
        "request_id": (_req_id(request) if request is not None else None) or str(uuid.uuid4()),
    }


def _req_id(request: Request) -> str | None:
    return request.headers.get("X-Request-Id")


def _idem(request: Request) -> str | None:
    return request.headers.get("Idempotency-Key")


# ==================== P0 主链 ====================


@router.get("/today")
async def get_today(
    date: str | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    data = await today.build_today(db, teacher_id, date=date)
    return _ok(data)


@router.get("/classes/{class_id}/insights")
async def class_insights(
    class_id: uuid.UUID,
    actionable: bool = False,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    result = await insights.compute_class_insights(db, teacher_id, class_id, actionable=actionable)
    return _ok({"insights": result})


@router.get("/classes/{class_id}/roster")
async def class_roster(
    class_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """tel A4：每生花名册（提交数/待批数/掌握度/薄弱 KP，确定性 SQL 聚合）。"""
    teacher_id = require_teacher_role(user)
    return _ok(await insights.class_roster(db, teacher_id, class_id))


@router.get("/classes/{class_id}/learning-overview")
async def class_learning_overview(
    class_id: uuid.UUID,
    window_days: int = Query(default=14, ge=1, le=90),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """tel A5：班级学情聚合卡（提交趋势/KP 热度/待批积压/掌握度/错误聚类，无 LLM）。"""
    teacher_id = require_teacher_role(user)
    return _ok(
        await insights.class_learning_overview(
            db, teacher_id, class_id, window_days=window_days
        )
    )


@router.get("/classes/{class_id}/video-insights")
async def video_insights(
    class_id: uuid.UUID,
    lesson_id: uuid.UUID | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(await classroom.video_insights(db, teacher_id, class_id, lesson_id=lesson_id))


@router.get("/classes/{class_id}/classroom-mode")
async def get_classroom_mode(
    class_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """课堂模式当前状态（审计 C-04：前端轮询契约）。"""
    teacher_id = require_teacher_role(user)
    return _ok(await classroom.classroom_state(db, teacher_id, class_id))


@router.post("/classes/{class_id}/classroom-mode")
async def classroom_mode(
    class_id: uuid.UUID,
    req: ClassroomModeRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await classroom.set_classroom_mode(
            db, teacher_id, class_id,
            enabled=req.enabled, lesson_id=req.lesson_id, duration_minutes=req.duration_minutes,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key or _idem(request),
            request_id=_req_id(request),
        )
    )


# ---------------- 课堂会话（Session 语义：发题 / 收答分布 / AI 提醒） ----------------


@router.get("/classes/{class_id}/classroom-session")
async def classroom_session_state(
    class_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(await classroom_session.session_state(db, teacher_id, class_id))


class ClassroomSessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    topic: str = Field(default="", max_length=120)
    room: str | None = Field(default=None, max_length=80)
    lesson_id: uuid.UUID | None = Field(default=None, alias="lessonId")
    client_request_id: str = Field(min_length=1, max_length=128, alias="clientRequestId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ClassroomQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question_no: int = Field(default=0, ge=0, le=99)
    prompt: str | None = Field(default=None, max_length=400)
    client_request_id: str = Field(min_length=1, max_length=128, alias="clientRequestId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ClassroomSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    client_request_id: str = Field(min_length=1, max_length=128, alias="clientRequestId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


@router.post("/classes/{class_id}/classroom-session/start")
async def classroom_session_start(
    class_id: uuid.UUID,
    req: ClassroomSessionStartRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await classroom_session.start_session(
            db, teacher_id, class_id,
            topic=req.topic, room=req.room, lesson_id=req.lesson_id,
            client_request_id=req.client_request_id,
            idempotency_key=req.idempotency_key or _idem(request),
        )
    )


@router.post("/classes/{class_id}/classroom-session/question")
async def classroom_session_launch_question(
    class_id: uuid.UUID,
    req: ClassroomQuestionRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await classroom_session.launch_question(
            db, teacher_id, class_id,
            question_no=req.question_no, prompt=req.prompt,
            client_request_id=req.client_request_id,
            idempotency_key=req.idempotency_key or _idem(request),
        )
    )


@router.post("/classes/{class_id}/classroom-session/close")
async def classroom_session_close(
    class_id: uuid.UUID,
    req: ClassroomSessionCloseRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await classroom_session.close_session(
            db, teacher_id, class_id,
            client_request_id=req.client_request_id,
            idempotency_key=req.idempotency_key or _idem(request),
        )
    )


# ---------------- 教案 ----------------


@router.post("/lessons/adapt")
async def adapt_lesson(req: AdaptLessonRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await lessons.adapt_lesson(
            db, teacher_id, req.class_id,
            topic=req.topic, source_artifact_id=req.source_artifact_id,
            source_resource_ids=req.source_resource_ids,
            source_refs=req.source_refs, requirements=req.requirements,
            duration_minutes=req.duration_minutes, client_request_id=_req_id(request) or str(uuid.uuid4()),
        )
    )


@router.get("/lessons")
async def list_lessons(class_id: uuid.UUID | None = None, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok({"lessons": await lessons.list_lessons(db, teacher_id, class_id)})


@router.post("/lessons")
async def create_lesson(req: CreateLessonRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await lessons.create_lesson(
            db, teacher_id, req.class_id,
            title=req.title, topic=req.topic, duration_minutes=req.duration_minutes, content=req.content,
        )
    )


@router.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: uuid.UUID, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await lessons.get_lesson(db, teacher_id, lesson_id))


@router.get("/lessons/{lesson_id}/download")
async def download_lesson(
    lesson_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    teacher_id = require_teacher_role(user)
    content, filename = await lessons.render_lesson_docx(db, teacher_id, lesson_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@router.post("/lessons/{lesson_id}/apply-insight")
async def apply_insight(
    lesson_id: uuid.UUID, req: ApplyInsightRequest,
    user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """应用洞察到教案草稿（审计 I-08：按 insight_id 加载真实洞察，写新 draft 版本）。"""
    teacher_id = require_teacher_role(user)
    return _ok(
        await lessons.apply_insight_to_lesson(
            db, teacher_id, lesson_id, insight_id=req.insight_id,
            version=req.version, instruction=req.instruction,
        )
    )


@router.post("/lessons/{lesson_id}/slides")
async def create_slides(lesson_id: uuid.UUID, req: CreateSlidesRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await lessons.create_slides(db, teacher_id, lesson_id, version=req.version, style=req.style, requirements=req.requirements))


@router.get("/slides/{slide_id}/download")
async def download_slides(
    slide_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    teacher_id = require_teacher_role(user)
    content, filename = await lessons.render_slides_pptx(db, teacher_id, slide_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@router.post("/lessons/{lesson_id}/explainer")
async def create_explainer(lesson_id: uuid.UUID, req: ExplainRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    # 独立的讲解接口：由题目与参考解法生成 explanation artifact
    return _ok(
        await lessons.create_explainer(
            db, teacher_id, None,
            question=req.question, reference_solution=req.reference_solution, target_minutes=req.target_minutes,
        )
    )


# ---------------- 出题 / 作业 ----------------


@router.post("/quizzes/generate")
async def generate_quiz(req: GenerateQuizRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await assessment.generate_quiz(
            db, teacher_id, req.class_id,
            knowledge_points=req.knowledge_points, count=req.count,
            question_types=req.question_types.model_dump(), difficulty=req.difficulty.model_dump(),
            exclude_hashes=req.exclude_hashes, client_request_id=_req_id(request) or str(uuid.uuid4()),
        )
    )


@router.get("/assignments")
async def list_assignments(
    class_id: uuid.UUID | None = None,
    status: str | None = None,
    client_assignment_id: str | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        {
            "assignments": await assessment.list_assignments(
                db, teacher_id, class_id, status, client_assignment_id=client_assignment_id
            )
        }
    )


@router.post("/assignments")
async def create_assignment(req: CreateAssignmentRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await assessment.create_assignment(
            db, teacher_id, req.class_id,
            title=req.title, artifact_id=req.artifact_id, client_assignment_id=req.client_assignment_id, deadline=req.deadline,
            assignment_type=req.type,
            quiz_items=[it.model_dump() for it in req.quiz_items] if req.quiz_items else None,
        )
    )


@router.get("/assignments/{assignment_id}/submission-facts")
async def get_assignment_submission_facts(assignment_id: uuid.UUID, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """R19 学情证据只读投影：按作业导出统计最小素材（PII 出口即伪名化；additive）。"""
    teacher_id = require_teacher_role(user)
    return _ok(await submission_facts.assignment_submission_facts(db, teacher_id, assignment_id=assignment_id))


@router.post("/assignments/{assignment_id}/publish")
async def publish_assignment(assignment_id: uuid.UUID, req: PublishAssignmentRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    clazz = await _assignment_class(db, assignment_id)
    return _ok(
        await assessment.publish_assignment(
            db, teacher_id, clazz, assignment_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key, request_id=None,
        )
    )


@router.post("/assignments/{assignment_id}/close")
async def close_assignment(assignment_id: uuid.UUID, req: ArtifactActionRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    clazz = await _assignment_class(db, assignment_id)
    return _ok(
        await assessment.close_assignment(
            db, teacher_id, clazz, assignment_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key, request_id=None,
        )
    )


@router.post("/assignments/{assignment_id}/archive")
async def archive_assignment(assignment_id: uuid.UUID, req: ArtifactActionRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    clazz = await _assignment_class(db, assignment_id)
    return _ok(
        await assessment.archive_assignment(
            db, teacher_id, clazz, assignment_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key, request_id=None,
        )
    )


async def _assignment_class(db, assignment_id: uuid.UUID) -> uuid.UUID:
    from app.models.coursework import Assignment

    a = await db.get(Assignment, assignment_id)
    if a is None or a.deleted_at is not None:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    return a.class_id


# ---------------- 批改 ----------------


@router.get("/grading/insights")
async def grading_review_insights(
    class_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """批后讲评建议：最近一份已批作业里最值得讲的 Top 题（含典型作答）。"""
    teacher_id = require_teacher_role(user)
    return _ok(await grading.grading_review_insights(db, teacher_id, class_id))


@router.get("/grading/queue")
async def grading_queue(class_id: uuid.UUID | None = None, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok({"queue": await grading.grading_queue(db, teacher_id, class_id)})


@router.get("/grading/workspace")
async def get_grading_workspace(
    class_id: uuid.UUID | None = None,
    assignment_id: uuid.UUID | None = None,
    item_no: int | None = None,
    status: str = "all",
    submission_item_id: uuid.UUID | None = None,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await grading.grading_workspace(
            db,
            teacher_id,
            class_id=class_id,
            assignment_id=assignment_id,
            item_no=item_no,
            status=status,
            submission_item_id=submission_item_id,
        )
    )


@router.post("/grading/{submission_item_id}/suggest")
async def suggest_grade(submission_item_id: uuid.UUID, req: SuggestGradeRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await grading.suggest_grade(
            db, teacher_id, req.class_id, submission_item_id,
            client_request_id=req.client_request_id,
        )
    )


@router.post("/grading/{submission_item_id}/review")
async def set_grading_review(
    submission_item_id: uuid.UUID,
    req: SetGradingReviewRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await grading.set_grading_review(
            db,
            teacher_id,
            submission_item_id,
            state=req.state,
            note=req.note,
            client_request_id=req.client_request_id,
            idempotency_key=_idem(request),
            request_id=_req_id(request),
        )
    )


@router.get("/grading/{submission_item_id}")
async def get_grading(submission_item_id: uuid.UUID, class_id: uuid.UUID | None = None, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """批改详情：经 submission→assignment 反查班级并校验教师范围（审计 C-03）；
    尚无建议时自动生成本地建议。"""
    teacher_id = require_teacher_role(user)
    return _ok(await grading.grading_detail(db, teacher_id, submission_item_id))


@router.get("/grading/{submission_item_id}/file")
async def get_grading_file(
    submission_item_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from urllib.parse import quote

    teacher_id = require_teacher_role(user)
    content, mime, filename = await grading.grading_file(db, teacher_id, submission_item_id)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@router.post("/grading/{submission_item_id}/confirm")
async def confirm_grade(submission_item_id: uuid.UUID, req: ConfirmGradeRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)

    clazz = await _grade_class(db, submission_item_id)
    return _ok(
        await grading.confirm_grade(
            db, teacher_id, clazz, submission_item_id,
            suggestion_id=req.suggestion_id, decision=req.decision, final_score=req.final_score,
            teacher_feedback=req.teacher_feedback, version=req.version,
            client_request_id=str(req.suggestion_id), idempotency_key=_idem(request),
            request_id=_req_id(request),
        )
    )


@router.post("/grading/batch-confirm")
async def batch_confirm(req: BatchConfirmRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """批量确认：逐项独立校验范围/幂等并返回稳定错误码（审计 I-07）。

    每项使用 teacher+item+suggestion 派生幂等键，重放不重复更新；
    单项失败不掩盖其他项；错误只暴露稳定 code/message，不含异常原文。
    """
    import hashlib

    from fastapi import HTTPException

    teacher_id = require_teacher_role(user)
    await grading.lock_batch_submission_items(
        db, [item.submission_item_id for item in req.items]
    )
    results = []
    for it in req.items:
        try:
            clazz = await _grade_class(db, it.submission_item_id)
            item_key = (
                f"batch:{teacher_id}:{it.submission_item_id}:{it.suggestion_id}"
            )
            r = await grading.confirm_grade(
                db, teacher_id, clazz, it.submission_item_id,
                suggestion_id=it.suggestion_id, decision=it.decision, final_score=it.final_score,
                teacher_feedback=it.teacher_feedback, version=it.version,
                client_request_id=hashlib.sha256(item_key.encode()).hexdigest()[:32],
                idempotency_key=item_key,
            )
            results.append({"ok": True, "submission_item_id": str(it.submission_item_id), "result": r})
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            results.append(
                {
                    "ok": False,
                    "submission_item_id": str(it.submission_item_id),
                    "code": detail.get("code", 50000),
                    "message": detail.get("message", "grading_confirm_failed"),
                }
            )
    return _ok({"results": results})


async def _grade_class(db, submission_item_id: uuid.UUID) -> uuid.UUID:
    """submission_item → submission → assignment 反查 class_id；不可追溯按 404 拒绝。"""
    from app.models.coursework import Assignment, Submission, SubmissionItem

    item = await db.get(SubmissionItem, submission_item_id)
    if item is None or item.deleted_at is not None:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    sub = await db.get(Submission, item.submission_id)
    if sub is None or sub.deleted_at is not None:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    a = await db.get(Assignment, sub.assignment_id) if sub.assignment_id else None
    if a is None or a.deleted_at is not None:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    return a.class_id


# ---------------- 资源 ----------------


@router.get("/resources")
async def list_resources(class_id: uuid.UUID | None = None, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok({"resources": await resources.list_resources(db, teacher_id, class_id)})


@router.post("/resources/upload")
async def upload_resource(file: UploadFile = File(...), user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await resources.resource_upload(db, teacher_id, None, file, client_request_id=str(uuid.uuid4()))
    )


@router.post("/resources/{resource_id}/preprocess")
async def preprocess(resource_id: str, req: PreprocessRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await resources.resource_preprocess(
            db, teacher_id, None, resource_id, client_request_id=req.client_request_id
        )
    )


@router.post("/resources/{resource_id}/understand")
async def understand(resource_id: str, req: UnderstandRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await resources.resource_understand(
            db, teacher_id, None, resource_id,
            question=req.question, output_type=req.output_type, client_request_id=req.client_request_id,
        )
    )


@router.post("/resources/external-reference")
async def create_external_resource_reference(
    req: CreateExternalResourceReferenceRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    teacher_id = require_teacher_role(user)
    return _ok(
        await resources.create_external_reference(
            db, teacher_id, class_id=req.class_id, title=req.title, url=req.url,
            provider=req.provider, attribution=req.attribution, intended_use=req.intended_use,
        )
    )


@router.post("/resources/{resource_id}/question-candidates")
async def save_question_candidates(resource_id: str, req: SaveQuestionCandidatesRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await resources.save_question_candidates(db, teacher_id, resource_id, [item.model_dump() for item in req.candidates]))


@router.post("/resources/{resource_id}/question-candidates/approve")
async def approve_question_candidates(resource_id: str, req: ApproveQuestionCandidatesRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await resources.approve_question_candidates(db, teacher_id, resource_id, req.candidate_ids))


@router.post("/resources/{resource_id}/publish")
async def publish_resource(resource_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await resources.set_resource_published(db, teacher_id, resource_id, True))


@router.post("/resources/{resource_id}/unpublish")
async def unpublish_resource(resource_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await resources.set_resource_published(db, teacher_id, resource_id, False))


@router.delete("/resources/{resource_id}")
async def delete_resource(resource_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(await resources.resource_delete(db, teacher_id, resource_id))


@router.get("/resources/{resource_id}/download")
async def download_resource(resource_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from urllib.parse import quote

    teacher_id = require_teacher_role(user)
    content, filename, mime = await resources.resource_content(db, teacher_id, resource_id)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


# ---------------- Artifact 与任务 ----------------


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: uuid.UUID, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    a = await art.get_owned_artifact(db, teacher_id, artifact_id)
    return _ok(art._serialize_artifact(a))


@router.put("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: uuid.UUID, req: ArtifactUpdateRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    a, created = await art.update_artifact(db, teacher_id, artifact_id, version=req.version, payload=req.content, source_refs=req.source_refs)
    await db.flush()
    return _ok({"artifact": art._serialize_artifact(a), "created_new_version": created})


@router.post("/artifacts/{artifact_id}/confirm")
async def confirm_artifact(artifact_id: uuid.UUID, req: ArtifactActionRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await art.confirm_artifact(
            db, teacher_id, artifact_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key or _idem(request),
            request_id=_req_id(request),
        )
    )


@router.post("/artifacts/{artifact_id}/publish")
async def publish_artifact(artifact_id: uuid.UUID, req: ArtifactActionRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await art.publish_artifact(
            db, teacher_id, artifact_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key or _idem(request),
            request_id=_req_id(request),
        )
    )


@router.post("/artifacts/{artifact_id}/archive")
async def archive_artifact(artifact_id: uuid.UUID, req: ArtifactActionRequest, request: Request, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    return _ok(
        await art.archive_artifact(
            db, teacher_id, artifact_id,
            client_request_id=req.client_request_id, idempotency_key=req.idempotency_key or _idem(request),
            request_id=_req_id(request),
        )
    )


@router.get("/tasks/{task_id}")
async def get_task(task_id: uuid.UUID, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    t = await db.get(TeacherTask, task_id)
    if t is None or t.owner_id != teacher_id:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    return _ok(
        {
            "task_id": str(t.id),
            "capability": t.capability,
            "status": t.status,
            "progress": t.progress,
            "artifact_id": str(t.artifact_id) if t.artifact_id else None,
            "error_code": t.error_code,
        }
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: uuid.UUID, req: CancelTaskRequest, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    teacher_id = require_teacher_role(user)
    t = await db.get(TeacherTask, task_id)
    if t is None or t.owner_id != teacher_id:
        from app.domains.teacher.scope import raise_http

        raise_http(40400, 404, "not_found", recoverable=False)
    if t.status in ("queued", "running"):
        t.status = "cancelled"
        await db.flush()
    return _ok({"task_id": str(t.id), "status": t.status})


# ==================== Capability Gateway（内部受控） ====================


async def _build_teacher_butler_runtime(teacher_id: uuid.UUID, db: AsyncSession):
    """构建教师专属 Runtime；聊天不自动执行正式 WRITE。"""
    from app.butler.contracts import ButlerBudget, ToolRisk
    from app.butler.executor import ButlerExecutor
    from app.butler.model_adapter import ButlerModelAdapter, build_planner
    from app.butler.policy import PolicyGate
    from app.butler.runtime import ButlerRuntime
    from app.providers import router as provider_router

    registry = build_teacher_registry()
    policy = PolicyGate(
        registry,
        allowed_risks=frozenset({ToolRisk.READ, ToolRisk.LEARNING_ACTION}),
    )
    budget = ButlerBudget()
    model_router = await provider_router.get_model_router_for_user(str(teacher_id), db)
    adapter = ButlerModelAdapter(model_router, budget=budget)
    planner = build_planner(
        adapter,
        registry,
        budget=budget,
        system_prompt=_TEACHER_PLANNER_PROMPT,
    )
    executor = ButlerExecutor(registry, policy, budget=budget)
    return ButlerRuntime(
        registry=registry,
        policy=policy,
        adapter=adapter,
        planner=planner,
        executor=executor,
        budget=budget,
    )


@router.post("/butler/chat")
async def teacher_butler_chat(
    req: TeacherButlerChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师 Butler 正式入口：固定教师角色、受控场景、scope 与完整运行账本。"""
    from app.butler.contracts import ActorContext, ActorRole, ButlerRequest
    from app.domains.teacher.registry import TEACHER_SCENES
    from app.domains.teacher.scope import assert_teacher_in_class, raise_http

    teacher_id = require_teacher_role(user)
    if req.scene not in TEACHER_SCENES:
        raise_http(40001, 422, "invalid_scene", recoverable=True)

    class_id = req.class_id
    if class_id is not None:
        await assert_teacher_in_class(db, teacher_id, class_id)
    if req.artifact_id is not None:
        artifact = await art.get_owned_artifact(db, teacher_id, req.artifact_id)
        if class_id is None and artifact.class_id is not None:
            class_id = artifact.class_id
            await assert_teacher_in_class(db, teacher_id, class_id)
        elif class_id is not None and artifact.class_id not in (None, class_id):
            raise_http(40001, 422, "artifact_class_mismatch", recoverable=True)

    trusted_context = []
    if class_id is not None:
        trusted_context.append(f"class_id={class_id}")
    if req.artifact_id is not None:
        trusted_context.append(f"artifact_id={req.artifact_id}")
    message = req.user_message
    if trusted_context:
        message = f"{message}\n[trusted_context] {'; '.join(trusted_context)}"

    butler_request = ButlerRequest(
        actor=ActorContext(
            user_id=teacher_id,
            role=ActorRole.TEACHER,
            class_ids=(class_id,) if class_id is not None else (),
        ),
        message=message,
        scene=req.scene,
        client_request_id=req.client_request_id,
    )
    runtime = await _build_teacher_butler_runtime(teacher_id, db)
    envelope = await runtime.run(butler_request, db)
    await db.flush()

    trace = dict(envelope.trace or {})
    error_codes = set(trace.get("error_codes") or [])
    if trace.get("error_code"):
        error_codes.add(trace["error_code"])
    confirmation_required = bool(
        error_codes & {"risk_denied", "confirmation_required", "idempotency_required"}
    )
    artifact_block = envelope.blocks[0] if envelope.blocks else None
    return _ok(
        {
            "run_id": str(envelope.run_id),
            "intent": envelope.intent,
            "message": envelope.text,
            "artifact": artifact_block,
            "blocks": envelope.blocks,
            "actions": envelope.actions,
            "sources": envelope.sources,
            "degraded": envelope.degraded,
            "confirmation_required": confirmation_required,
            "trace": trace,
        },
        request,
    )


@router.get("/capabilities")
async def list_capabilities(user: dict = Depends(get_current_user)):
    require_teacher_role(user)
    from app.domains.teacher.capability_gateway import VALID_CAPABILITIES

    return _ok({"capabilities": sorted(VALID_CAPABILITIES)})


@router.post("/capabilities/{capability}")
async def run_capability_endpoint(
    capability: str,
    req: CapabilityRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Capability Gateway 统一入口（审计 C-02 修复）。

    固定顺序：teacher role → class scope → scene 白名单 → Registry/Policy 校验
    → 本地/星辰执行 → Artifact + Ledger。前端不得指定 workflow/Provider/模型/密钥。
    """
    import time as _time

    from app.butler.contracts import (
        ActorContext,
        ActorRole,
        ButlerRequest,
        PlannedAction,
    )
    from app.butler.policy import PolicyGate
    from app.domains.teacher.registry import TEACHER_SCENES, build_teacher_registry
    from app.domains.teacher.scope import assert_teacher_in_class, raise_http

    teacher_id = require_teacher_role(user)

    # 1) class scope：class_id 存在时必须任教该班
    if req.class_id is not None:
        await assert_teacher_in_class(db, teacher_id, req.class_id)

    # 2) scene 白名单：拒绝任意 scene（不信任前端传入的场景字符串）
    if req.scene not in TEACHER_SCENES:
        raise_http(40001, 422, "invalid_scene", recoverable=True)

    # 3) Registry/Policy：capability → teacher 工具映射并经 PolicyGate 校验
    reg = build_teacher_registry()
    policy = PolicyGate(reg)
    tool_name = _CAPABILITY_TO_TOOL.get(capability)
    if tool_name is None or tool_name not in reg.names():
        raise_http(40001, 404, "unknown_capability", recoverable=True)
    butler_request = ButlerRequest(
        actor=ActorContext(user_id=teacher_id, role=ActorRole.TEACHER),
        message="capability",
        scene=req.scene,
        client_request_id=req.client_request_id,
    )
    # Policy 输入 = class_id + 业务 payload（经工具 input_model 校验，拒绝任意 payload）
    action_args: dict = dict(req.payload) if isinstance(req.payload, dict) else {}
    if req.class_id is not None:
        action_args["class_id"] = str(req.class_id)
    action = PlannedAction(
        tool_name=tool_name,
        arguments=action_args,
        reason="teacher capability gateway",
    )
    decision = policy.validate_action(butler_request, action)
    if not decision.allowed:
        raise_http(
            40302 if decision.error_code in ("role_denied", "scene_denied") else 40001,
            403 if decision.error_code in ("role_denied", "scene_denied") else 422,
            decision.error_code or "policy_denied",
            recoverable=True,
        )

    started = _time.perf_counter()
    result = await run_capability(
        db, teacher_id,
        scene=req.scene, class_id=req.class_id, capability=capability,
        payload=req.payload, source_refs=req.source_refs,
        client_request_id=req.client_request_id,
    )
    latency_ms = max(1, int((_time.perf_counter() - started) * 1000))

    # 星辰/本地执行后生成 draft artifact
    artifact = await art.create_artifact(
        db,
        owner_id=teacher_id,
        artifact_type=_capability_artifact_type(capability),
        scene=req.scene,
        class_id=req.class_id,
        payload=result["payload"],
        source_refs=req.source_refs,
        engine=result["engine"],
        degraded=result["degraded"],
        warnings=result.get("warnings") or [],
        validation=result.get("validation", {}),
    )
    db.add(artifact)
    await db.flush()

    # 4) Ledger：写 agent_runs + tool_invocations（只存 digest/脱敏摘要，不存原文）
    import hashlib
    import json

    from app.models.agent_run import AgentRun, ToolInvocation

    run = AgentRun(
        user_id=teacher_id,
        role="teacher",
        scene=req.scene,
        client_request_id=f"cap:{req.client_request_id}:{capability}",
        intent="capability_gateway",
        status="succeeded" if not result["degraded"] else "fallback",
        degraded=bool(result["degraded"]),
        model_request_count=0,
        tool_call_count=1,
        latency_ms=latency_ms,
        run_meta={"capability": capability, "engine": result["engine"]},
    )
    db.add(run)
    await db.flush()
    db.add(
        ToolInvocation(
            run_id=run.id,
            tool_name=tool_name,
            tool_version="1.0.0",
            status="succeeded" if not result["degraded"] else "degraded",
            latency_ms=latency_ms,
            idempotency_key=None,
            arguments_digest=hashlib.sha256(
                json.dumps(req.payload, sort_keys=True, default=str).encode()
            ).hexdigest()[:16],
            result_digest=hashlib.sha256(
                json.dumps({"artifact_id": str(artifact.id), "engine": result["engine"]},
                           sort_keys=True).encode()
            ).hexdigest()[:16],
            error_code=None,
        )
    )
    await db.flush()
    return _ok(
        {
            "artifact_id": str(artifact.id),
            "status": artifact.status,
            "engine": artifact.engine,
            "degraded": artifact.degraded,
            "warnings": artifact.warnings,
        },
        request,
    )


#: Capability → teacher Registry 工具映射（Policy 校验以工具名为准）
_CAPABILITY_TO_TOOL = {
    "adapt_lesson": "teacher.lesson.adapt",
    "create_slides": "teacher.slides.create",
    "create_quiz": "teacher.quiz.create",
    "suggest_grade": "teacher.grade.suggest",
    "explain_problem": "teacher.problem.explain",
    "preprocess_course": "teacher.course.preprocess",
    "understand_document": "teacher.document.understand",
}


def _capability_artifact_type(capability: str) -> str:
    return {
        "adapt_lesson": "lesson_plan",
        "create_slides": "slide_deck",
        "create_quiz": "quiz_set",
        "suggest_grade": "grading_suggestion",
        "explain_problem": "explanation",
        "preprocess_course": "preprocess",
        "understand_document": "document",
    }.get(capability, "artifact")


# ==================== teacher registry 暴露（供但ler 集成测试/影子运行） ====================


def get_teacher_registry():

    return build_teacher_registry()
