"""教师课堂课件 HTTP 路由（Phase A：教师发布 → 学生进入 → 学情回流）。

- 教师端：`/api/teacher/classroom-coursewares*`（仅在 m3_enable_teacher=True 时挂载）；
- 学生端：`/api/student/classroom-coursewares*`（默认 profile 也可用）；
- 路由只做 HTTP 解析 / 鉴权 / 响应，业务在 `courseware.py`；
- 不新增 header 协议，不直连其它服务端口，不新建表。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.classroom import courseware as svc
from app.domains.teacher.scope import assert_teacher_in_class, require_teacher_role
from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.database import get_db

logger = structlog.get_logger()

teacher_router = APIRouter(prefix="/api/teacher/classroom-coursewares", tags=["teacher-courseware"])
student_router = APIRouter(prefix="/api/student/classroom-coursewares", tags=["student-courseware"])

# 后台准备任务丢失（进程重启）时，详情接口在超过该窗口后自动续跑
_RESUME_STALE_SECONDS = 45


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CoursewareAssetIn(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    data_base64: str = Field(min_length=1)
    sha256: str | None = Field(default=None, max_length=64)


class CoursewareSlideIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    type: str = Field(default="content", max_length=40)
    title: str = Field(default="", max_length=120)
    content: str | None = Field(default=None, max_length=1200)
    layout_hint: str | None = Field(default=None, max_length=40)
    elements: list[dict] = Field(default_factory=list)
    math_object: dict | None = None
    assets: list[CoursewareAssetIn] = Field(default_factory=list)
    speaker_note: str | None = Field(default=None, max_length=2000)


class CreateCoursewareRequest(BaseModel):
    class_id: uuid.UUID
    deck_id: str = Field(min_length=1, max_length=80)
    deck_revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=200)
    textbook_line: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=40)
    slides: list[CoursewareSlideIn] = Field(min_length=svc.MIN_SLIDES, max_length=svc.MAX_SLIDES)
    source_refs: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Teacher endpoints
# ---------------------------------------------------------------------------


@teacher_router.post("")
async def create_classroom_courseware(
    body: CreateCoursewareRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师发布课堂课件：创建 draft artifact 并立即返回，准备在后台进行。"""
    teacher_id = require_teacher_role(current_user)
    await assert_teacher_in_class(db, teacher_id, body.class_id)

    artifact = await svc.create_courseware(db, teacher_id=teacher_id, body=body.model_dump())
    # 先提交再起后台任务：create_task 可能在请求 session 提交前就查询 artifact
    await db.commit()
    svc.schedule_prepare(str(artifact.id))
    logger.info(
        "courseware_created",
        courseware_id=str(artifact.id),
        class_id=str(body.class_id),
        deck_id=body.deck_id,
        pages=len(body.slides),
    )
    return ApiResponse(
        code=0,
        message="ok",
        data={"courseware_id": str(artifact.id), "status": "preparing"},
    )


@teacher_router.get("")
@teacher_router.get("/", include_in_schema=False)
async def list_classroom_coursewares(
    class_id: uuid.UUID | None = Query(default=None),
    deck_id: str | None = Query(default=None, max_length=80),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师自己的已发布/准备中课件 + 真实汇总（已打开/总人数/平均进度/练习正确率）。"""
    teacher_id = require_teacher_role(current_user)
    if class_id is not None:
        await assert_teacher_in_class(db, teacher_id, class_id)
    items = await svc.list_teacher_coursewares(
        db, teacher_id=teacher_id, class_id=class_id, deck_id=deck_id
    )
    return ApiResponse(code=0, message="ok", data={"items": items, "total": len(items)})


@teacher_router.get("/{courseware_id}")
async def get_classroom_courseware(
    courseware_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师详情：准备进度 / 讲稿与练习状态 / warnings。"""
    teacher_id = require_teacher_role(current_user)
    artifact = await svc.get_courseware_artifact(
        db, teacher_id=teacher_id, courseware_id=courseware_id
    )
    # 进程重启导致后台准备丢失时自动续跑（与 stage_router 的 auto-resume 同理念）
    payload = artifact.payload or {}
    if (
        artifact.status == "draft"
        and not payload.get("prepared_at")
        and not svc.is_preparing(str(artifact.id))
    ):
        updated = getattr(artifact, "updated_at", None)
        stale = updated is not None and datetime.now(UTC) - updated > timedelta(
            seconds=_RESUME_STALE_SECONDS
        )
        if stale:
            logger.info("courseware_prepare_auto_resume", courseware_id=str(artifact.id))
            svc.schedule_prepare(str(artifact.id))
    data = await svc.courseware_detail(db, artifact)
    return ApiResponse(code=0, message="ok", data=data)


@teacher_router.get("/{courseware_id}/monitor")
async def monitor_classroom_courseware(
    courseware_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """真实学情：班级已确认学生 × 本课件会话的 progress / practice_stats。"""
    teacher_id = require_teacher_role(current_user)
    artifact = await svc.get_courseware_artifact(
        db, teacher_id=teacher_id, courseware_id=courseware_id
    )
    data = await svc.courseware_monitor(db, artifact)
    return ApiResponse(code=0, message="ok", data=data)


@teacher_router.post("/{courseware_id}/archive")
async def archive_classroom_courseware(
    courseware_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """软下架：status=archived（写 TeacherAction 审计），学生列表不再返回。"""
    teacher_id = require_teacher_role(current_user)
    await svc.archive_courseware(db, teacher_id=teacher_id, courseware_id=courseware_id)
    await db.commit()
    return ApiResponse(
        code=0,
        message="ok",
        data={"courseware_id": str(courseware_id), "status": "archived"},
    )


# ---------------------------------------------------------------------------
# Student endpoints
# ---------------------------------------------------------------------------


@student_router.get("")
@student_router.get("/", include_in_schema=False)
async def list_my_classroom_coursewares(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我确认加入的班级里、老师已发布的课堂课件（含我的进度/练习正确率）。"""
    user_id = uuid.UUID(str(current_user["sub"]))
    items = await svc.list_student_coursewares(db, user_id=user_id)
    return ApiResponse(code=0, message="ok", data={"items": items, "total": len(items)})


@student_router.post("/{courseware_id}/open")
async def open_classroom_courseware(
    courseware_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """打开老师课件：复用/创建我自己的 ClassroomSession（teacher_deck 来源）。"""
    user_id = uuid.UUID(str(current_user["sub"]))
    session = await svc.open_student_session(db, user_id=user_id, courseware_id=courseware_id)
    await db.commit()
    return ApiResponse(
        code=0,
        message="ok",
        data={"session_id": str(session.id), "title": session.title},
    )
