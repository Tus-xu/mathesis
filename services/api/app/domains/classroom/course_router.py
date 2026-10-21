"""课程域路由（F9 双师课堂预处理管线，迭代05 阶段4）

SSOT §4.10 / 星辰指南 §7 / ADR-034：
- POST /api/courses — 课程登记（teacher/researcher）
- POST /api/courses/{id}/preprocess — 触发预处理（幂等：ready 直接返回缓存）
- GET /api/courses — 课程列表
- GET /api/courses/{id} — 课程 + 预处理产物
- GET /api/courses/{id}/summary — 阶段总结（章节摘要）
- POST /api/courses/{id}/quiz — 看课检测（复用 F2 出题管线，基于 kp_codes）

预处理三级降级链（星辰指南 §7.5）：
wf_course_preprocess（星辰开启）→ 本地星火直调 → 固定间隔切段（兜底，不阻塞）。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.teacher.scope import get_teacher_class
from app.gateway.auth import get_current_user, require_role
from app.gateway.schemas import ApiResponse
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.course import (
    COURSE_STATUS_PENDING,
    COURSE_STATUS_PREPROCESSING,
    COURSE_STATUS_READY,
    Course,
)
from app.models.database import background_session_factory, get_db
from app.models.knowledge_point import KnowledgePoint

if TYPE_CHECKING:
    from app.providers.xingchen import XingchenConfig

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/courses", tags=["courses"])

# 预处理输入上限（SSOT §4.10）
_TRANSCRIPT_MAX_LEN = 20000
# 固定间隔切段兜底：每段字数
_FIXED_SPLIT_SIZE = 500

# 本地星火直调 prompt（章节切分 + 知识点锚定 + 知识卡抽取，SSOT §4.10 基线三字段）
_LOCAL_PREPROCESS_PROMPT = """\
你是课程预处理专家。将下面的课堂 ASR 字幕切分为章节，锚定知识点，抽取知识卡。

【字幕全文】
{transcript}

【知识点编码表（只能从此表选择，禁止编造）】
{kp_table}

【输出严格 JSON 格式】
{{
  "chapters": [{{"title": "章节标题", "start_ts": 0.0, "end_ts": 120.5, "summary": "章节摘要（≤200字）"}}],
  "kp_codes": ["知识点编码"],
  "knowledge_cards": [{{"title": "知识卡标题", "content": "核心内容（≤300字）", "ts": 30.0}}]
}}

【纪律】
- 章节按字幕中的时间戳标记切分，连续覆盖不跳段；无时间戳时 start_ts/end_ts 填 null
- kp_codes 只能取自知识点编码表；无匹配填空数组
- 知识卡抽取课堂核心概念/公式/方法，口语数学转写为 LaTeX（$...$）
- 只输出 JSON，不要其他文字
"""


# ========== Schemas ==========


class CreateCourseRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    transcript: str = Field(..., min_length=1, max_length=_TRANSCRIPT_MAX_LEN)
    class_id: str | None = None


class CourseQuizRequest(BaseModel):
    q_type: str = "choice"  # choice/blank/solution
    difficulty: str = "medium"  # easy/medium/hard


class BindOpenmaicClassroomRequest(BaseModel):
    # 仅 stage_id（绑定现有课堂）时提供；或传 document={stage,scenes} 同时保存课堂文档。
    stage_id: str = Field("", max_length=128)
    # stage_id=None 时传空串解除绑定
    unbind: bool = False
    # OpenMAIC 课堂文档（SSOT）：{stage, scenes}，绑定/接入时一并保存并播种
    document: dict | None = None


class SyncOpenmaicClassroomRequest(BaseModel):
    force: bool = False


# ========== 预处理管线 ==========


async def _preprocess_via_workflow(
    course_id: str, transcript: str, kp_table: str, config: XingchenConfig | None = None
) -> dict | None:
    """星辰 wf_course_preprocess（开启时优先）；失败返回 None 走下一级降级

    config 为调用方三层解析后的有效配置（管理后台配置即时生效），缺省走 env。
    """
    from app.providers.xingchen import xingchen_config_from_settings

    cfg = config or xingchen_config_from_settings()
    if not cfg.enabled:
        return None
    try:
        from app.providers.xingchen import run_workflow

        result = await run_workflow(
            "wf_course_preprocess",
            uid="system",
            parameters={
                "course_id": course_id,
                "transcript": transcript[:_TRANSCRIPT_MAX_LEN],
            },
            config=cfg,
        )
        chapters = result.get("chapters") or []
        if chapters:
            return {
                "chapters": chapters,
                "kp_codes": result.get("kp_codes") or [],
                "knowledge_cards": result.get("knowledge_cards") or [],
            }
        logger.info("course_preprocess_workflow_empty")
        return None
    except Exception as e:
        logger.info("course_preprocess_workflow_failed", error=str(e)[:150])
        return None


async def _preprocess_via_local_llm(transcript: str, kp_table: str) -> dict | None:
    """本地星火/mimo 直调（二级降级）；失败返回 None 走固定切段"""
    try:
        from app.providers.router import get_model_router

        router_llm = get_model_router()
        result = await router_llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": _LOCAL_PREPROCESS_PROMPT.format(
                        transcript=transcript[:_TRANSCRIPT_MAX_LEN],
                        kp_table=kp_table or "（知识点表为空，kp_codes 输出空数组）",
                    ),
                }
            ],
            temperature=0.2,
            max_tokens=4000,
            request_id=f"courseprep-{uuid.uuid4().hex[:12]}",
            scene="course_preprocess",
        )
        from app.skills.smart_quiz.main import parse_quiz_json

        data = parse_quiz_json(result.get("content", ""))
        if data and data.get("chapters"):
            return {
                "chapters": data["chapters"],
                "kp_codes": data.get("kp_codes") or [],
                "knowledge_cards": data.get("knowledge_cards") or [],
            }
        logger.info("course_preprocess_local_invalid")
        return None
    except Exception as e:
        logger.warning("course_preprocess_local_failed", error=str(e)[:150])
        return None


def _preprocess_fixed_split(transcript: str) -> dict:
    """固定间隔切段（三级兜底，永不失败，SSOT §4.10 降级纪律）"""
    chapters = []
    text = transcript[:_TRANSCRIPT_MAX_LEN]
    for idx, start in enumerate(range(0, len(text), _FIXED_SPLIT_SIZE)):
        seg = text[start : start + _FIXED_SPLIT_SIZE]
        chapters.append(
            {
                "title": f"第 {idx + 1} 段",
                "start_ts": None,
                "end_ts": None,
                "summary": seg[:200],
            }
        )
    return {"chapters": chapters, "kp_codes": [], "knowledge_cards": []}


async def _run_course_preprocess(course_id: str) -> None:
    """后台预处理任务（BackgroundTasks 触发，后台专用 NullPool 会话）"""
    async with background_session_factory() as db:
        course = await db.get(Course, uuid.UUID(course_id))
        if course is None or course.deleted_at:
            return
        if course.status == COURSE_STATUS_READY:
            return  # 幂等：已有结果
        course.status = COURSE_STATUS_PREPROCESSING
        await db.commit()

        # kp 编码表（供本地 LLM 锚定，禁止编造）
        kp_rows = await db.execute(select(KnowledgePoint.code, KnowledgePoint.name))
        kp_table = "\n".join(f"- {code}: {name}" for code, name in kp_rows.all())

        # 三层解析有效配置（管理后台配置即时生效；课程属主的用户覆盖层一并生效）
        from app.providers.xingchen import resolve_effective_xingchen_config

        xcfg = await resolve_effective_xingchen_config(db, str(course.user_id))
        result = await _preprocess_via_workflow(
            str(course.id), course.transcript, kp_table, config=xcfg
        )
        engine = "wf_course_preprocess"
        if result is None:
            result = await _preprocess_via_local_llm(course.transcript, kp_table)
            engine = "spark_direct"
        if result is None:
            result = _preprocess_fixed_split(course.transcript)
            engine = "fixed_split"

        # kp_codes 白名单过滤（只保留库内编码，SSOT §4.10 输出纪律）
        if result.get("kp_codes"):
            valid_codes = {code for code, _ in kp_rows.all()}
            result["kp_codes"] = [c for c in result["kp_codes"] if c in valid_codes]

        course.preprocess_result = result
        course.preprocess_engine = engine
        course.status = COURSE_STATUS_READY
        course.error = None
        await db.commit()
        logger.info(
            "course_preprocessed",
            course_id=course_id,
            engine=engine,
            chapters=len(result.get("chapters") or []),
        )


# ========== 端点 ==========


def _openmaic_link(stage_id: str | None) -> dict | None:
    """OpenMAIC 双师课堂联动信息：未启用或未绑定返回 None"""
    if not settings.openmaic_enabled or not stage_id:
        return None
    return {
        "stage_id": stage_id,
        "classroom_url": f"{settings.openmaic_public_base_url.rstrip('/')}/classroom/{stage_id}",
    }


async def _seed_openmaic_classroom(document: dict) -> bool:
    """把课堂文档 {stage, scenes} 播种到 OpenMAIC /api/classroom(json store)，
    使任何 iframe 上下文都能按 stage_id 加载该课堂。失败不抛出，返回 False。"""
    if not settings.openmaic_enabled:
        return False
    stage = document.get("stage")
    scenes = document.get("scenes")
    if not stage or scenes is None:
        return False
    try:
        url = f"{settings.openmaic_public_base_url.rstrip('/')}/api/classroom"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"stage": stage, "scenes": scenes})
        return resp.status_code in (200, 201)
    except Exception as e:  # noqa: BLE001 —— 播种失败不阻塞绑定
        logger.warning("openmaic_seed_failed", error=str(e)[:150])
        return False


def _course_visibility_clause(user_id: uuid.UUID):
    """课程读取范围：本人创建，或本人是课程班级的已确认成员。"""
    member_class_ids = (
        select(ClassMember.class_id)
        .join(Class, Class.id == ClassMember.class_id)
        .where(
            ClassMember.user_id == user_id,
            ClassMember.confirmed.is_(True),
            ClassMember.deleted_at.is_(None),
            Class.status == "active",
            Class.deleted_at.is_(None),
        )
    )
    return or_(Course.user_id == user_id, Course.class_id.in_(member_class_ids))


async def _get_visible_course(
    db: AsyncSession, course_id: uuid.UUID, user_id: uuid.UUID
) -> Course | None:
    return await db.scalar(
        select(Course).where(
            Course.id == course_id,
            Course.deleted_at.is_(None),
            _course_visibility_clause(user_id),
        )
    )


@router.post("")
async def create_course(
    req: CreateCourseRequest,
    background: BackgroundTasks,
    user: dict = Depends(require_role("teacher", "researcher")),
    db: AsyncSession = Depends(get_db),
):
    """课程登记（登记即触发预处理；M2 最小实现）"""
    user_id = uuid.UUID(user["sub"])
    class_id = uuid.UUID(req.class_id) if req.class_id else None
    if class_id is not None:
        await get_teacher_class(db, user_id, class_id)
    course = Course(
        user_id=user_id,
        class_id=class_id,
        title=req.title,
        transcript=req.transcript,
        status=COURSE_STATUS_PENDING,
    )
    db.add(course)
    await db.flush()
    course_id = str(course.id)
    # 预处理用独立后台连接池 + asyncio.create_task 调度（不依赖 starlette
    # BackgroundTasks 的请求生命周期执行器，避免本环境后台任务不触发导致课程卡 pending）
    await db.commit()
    asyncio.create_task(_run_course_preprocess(course_id))
    return ApiResponse(code=0, message="ok", data={"course_id": course_id, "status": COURSE_STATUS_PENDING})


@router.post("/{course_id}/preprocess")
async def trigger_preprocess(
    course_id: uuid.UUID,
    background: BackgroundTasks,
    user: dict = Depends(require_role("teacher", "researcher")),
    db: AsyncSession = Depends(get_db),
):
    """手动触发预处理（幂等：ready 直接返回缓存产物）"""
    course = await db.get(Course, course_id)
    if course is None or course.deleted_at or str(course.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    if course.status == COURSE_STATUS_READY:
        return ApiResponse(
            code=0,
            message="ok",
            data={
                "course_id": str(course.id),
                "status": COURSE_STATUS_READY,
                "engine": course.preprocess_engine,
                "result": course.preprocess_result,
            },
        )
    asyncio.create_task(_run_course_preprocess(str(course.id)))
    return ApiResponse(
        code=0, message="ok", data={"course_id": str(course.id), "status": course.status}
    )


@router.get("")
async def list_courses(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """课程列表：本人课程，以及本人已确认加入班级的共享课程（学生端双师课堂可见教师登记课程）。"""
    user_id = uuid.UUID(user["sub"])
    active_role = user.get("active_role", "student")
    if active_role == "student":
        # 班级共享课程（与教师端数据打通）：Course.class_id ∈ 学生已确认班级
        member_class_ids = select(ClassMember.class_id).where(
            ClassMember.user_id == user_id,
            ClassMember.confirmed.is_(True),
            ClassMember.deleted_at.is_(None),
        )
        stmt = select(Course).where(
            Course.deleted_at.is_(None),
            (Course.user_id == user_id) | (Course.class_id.in_(member_class_ids)),
        )
    else:
        stmt = select(Course).where(
            Course.deleted_at.is_(None),
            _course_visibility_clause(user_id),
        )
    rows = await db.execute(
        stmt.order_by(Course.created_at.desc()).limit(50)
    )
    items = [
        {
            "course_id": str(c.id),
            "title": c.title,
            "status": c.status,
            "engine": c.preprocess_engine,
            "chapter_count": len((c.preprocess_result or {}).get("chapters") or []),
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "openmaic": _openmaic_link(c.openmaic_stage_id),
        }
        for c in rows.scalars().all()
    ]
    return ApiResponse(code=0, message="ok", data={"total": len(items), "items": items})


@router.get("/{course_id}")
async def get_course(
    course_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """课程 + 预处理产物（章节/知识点/知识卡）"""
    course = await _get_visible_course(db, course_id, uuid.UUID(user["sub"]))
    if course is None:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    result = course.preprocess_result or {}
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "course_id": str(course.id),
            "title": course.title,
            "status": course.status,
            "engine": course.preprocess_engine,
            "chapters": result.get("chapters") or [],
            "kp_codes": result.get("kp_codes") or [],
            "knowledge_cards": result.get("knowledge_cards") or [],
            "openmaic": _openmaic_link(course.openmaic_stage_id),
        },
    )


@router.post("/{course_id}/openmaic-classroom")
async def bind_openmaic_classroom(
    course_id: uuid.UUID,
    req: BindOpenmaicClassroomRequest,
    user: dict = Depends(require_role("teacher", "researcher")),
    db: AsyncSession = Depends(get_db),
):
    """绑定/解绑 OpenMAIC 双师课堂：把 course 与 OpenMAIC /classroom/{stage_id} 关联。

    teacher/researcher 在 OpenMAIC 生成交互课堂后，把生成的课堂 id 绑定到本课，
    学生端 /dual 即会以 iframe 嵌入该课堂。unbind=true 或 stage_id 为空串时解绑。
    """
    course = await db.get(Course, course_id)
    if course is None or course.deleted_at or str(course.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    if not settings.openmaic_enabled:
        return ApiResponse(code=50302, message="OpenMAIC 双师课堂未启用", data=None)

    if req.unbind:
        course.openmaic_stage_id = None
        course.openmaic_document = None
        await db.commit()
        return ApiResponse(code=0, message="ok", data={"course_id": str(course.id), "openmaic": None})

    # 优先采用文档自带的 stage.id；显式 stage_id 可覆盖（用于绑定已存在课堂）
    doc = req.document
    stage_id = req.stage_id.strip() if req.stage_id else ""
    if doc:
        d_stage = doc.get("stage") or {}
        if not stage_id:
            stage_id = str(d_stage.get("id") or "").strip()
        course.openmaic_document = doc
    if not stage_id:
        return ApiResponse(code=40001, message="stage_id 或 document.stage.id 至少提供一个", data=None)
    if not (stage_id.isalnum() or set(stage_id) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")):
        return ApiResponse(code=40001, message="stage_id 含非法字符", data=None)

    course.openmaic_stage_id = stage_id
    seeded = False
    if doc:
        seeded = await _seed_openmaic_classroom(doc)
    await db.commit()
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "course_id": str(course.id),
            "openmaic": _openmaic_link(course.openmaic_stage_id),
            "seeded_to_openmaic": seeded,
        },
    )


@router.post("/{course_id}/openmaic-sync")
async def sync_openmaic_classroom(
    course_id: uuid.UUID,
    req: SyncOpenmaicClassroomRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把课程上已保存的 OpenMAIC 课堂文档重新播种到 OpenMAIC（确保学生端 iframe 可加载）。

    若 openmaic_document 存在且未启用 force 且 OpenMAIC 已能 GET 到该课堂则跳过。
    """
    course = await db.get(Course, course_id)
    if course is None or course.deleted_at or str(course.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    doc = course.openmaic_document
    if not doc:
        return ApiResponse(code=40901, message="该课程未保存 OpenMAIC 课堂文档", data=None)

    await db.commit()
    seeded = await _seed_openmaic_classroom(doc)
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "course_id": str(course.id),
            "openmaic": _openmaic_link(course.openmaic_stage_id),
            "seeded_to_openmaic": seeded,
        },
    )


@router.get("/{course_id}/summary")
async def course_summary(
    course_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """阶段总结（学伴基础版：章节摘要时间线，SSOT §4.10）"""
    course = await _get_visible_course(db, course_id, uuid.UUID(user["sub"]))
    if course is None:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    chapters = (course.preprocess_result or {}).get("chapters") or []
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "course_id": str(course.id),
            "status": course.status,
            "stages": [
                {
                    "index": idx,
                    "title": ch.get("title"),
                    "start_ts": ch.get("start_ts"),
                    "end_ts": ch.get("end_ts"),
                    "summary": ch.get("summary"),
                }
                for idx, ch in enumerate(chapters)
            ],
        },
    )


@router.post("/{course_id}/quiz")
async def course_quiz(
    course_id: uuid.UUID,
    req: CourseQuizRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """看课检测（学伴基础版：复用 F2 出题管线，基于预处理锚定的 kp_codes）"""
    if req.q_type not in ("choice", "blank", "solution"):
        return ApiResponse(code=40001, message="非法题型", data=None)
    if req.difficulty not in ("easy", "medium", "hard"):
        return ApiResponse(code=40001, message="非法难度", data=None)

    course = await _get_visible_course(db, course_id, uuid.UUID(user["sub"]))
    if course is None:
        return ApiResponse(code=40400, message="课程不存在", data=None)
    if course.status != COURSE_STATUS_READY:
        return ApiResponse(code=40901, message="课程预处理未完成，请稍后再试", data=None)

    kp_codes = (course.preprocess_result or {}).get("kp_codes") or []
    if not kp_codes:
        return ApiResponse(code=40901, message="课程未锚定知识点，无法生成看课检测", data=None)

    # 复用 F2 出题管线（质量四闸 + 工作流优先降级本地）
    from app.gateway.student_router import QuizGenerationError, _generate_one_quiz_item
    from app.models.coursework import Quiz
    from app.providers.router import get_model_router

    kp_code = kp_codes[0]
    kp_row = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    kp_obj = kp_row.scalar_one_or_none()
    kp_name = kp_obj.name if kp_obj else kp_code

    quiz = Quiz(
        user_id=uuid.UUID(user["sub"]),
        source="course_check",
        title=f"看课检测：{course.title}",
        kp_codes=kp_codes,
    )
    db.add(quiz)
    await db.flush()
    try:
        quiz_data = await _generate_one_quiz_item(
            get_model_router(), quiz, kp_code, kp_name, req.difficulty, req.q_type, db=db
        )
    except QuizGenerationError as e:
        await db.rollback()
        logger.warning("course_quiz_generation_failed", error=str(e)[:150])
        return ApiResponse(code=50301, message=f"出题失败：{str(e)[:80]}", data=None)

    from app.gateway.student_router import _normalize_options
    from app.models.coursework import QuizItem

    db.add(
        QuizItem(
            quiz_id=quiz.id,
            item_no=1,
            q_type=str(quiz_data.get("q_type") or req.q_type),
            question_text=str(quiz_data["question_text"]),
            options=_normalize_options(quiz_data.get("options")),
            answer=str(quiz_data["answer"]),
            answer_analysis=quiz_data.get("answer_analysis"),
            kp_code=kp_code,
            difficulty=str(quiz_data.get("difficulty") or req.difficulty),
            ai_generated=True,
        )
    )
    await db.flush()
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "quiz_id": str(quiz.id),
            "kp_code": kp_code,
            "item": {
                "item_no": 1,
                "q_type": quiz_data.get("q_type") or req.q_type,
                "question_text": quiz_data["question_text"],
                "options": quiz_data.get("options"),
                "difficulty": quiz_data.get("difficulty") or req.difficulty,
            },
        },
    )
