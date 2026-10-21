"""任务处理器扩展（三）：teacher.quiz.create / teacher.materials —— 教师端后台任务

T-B2 组卷 / T-B3 教学材料：管家后台任务直接复用教师域同一实现内核，
单一内核纪律：后台任务与教师工具/端点走同一底层函数，禁止第二套生成逻辑。

- teacher.quiz.create → assessment.generate_quiz（teacher.quiz.create 工具同一底层：
  题库严格供给 + 规范化去重 + 数量护栏 + 降级标注，不绕过任何既有闸口）；
- teacher.materials.class_review → insights.compute_class_insights（班级洞察端点同一服务函数）
  + grading.grading_review_insights（批后讲评/错题聚类，确定性 SQL 统计，import 复用不重写），
  LLM 仅生成讲评建议文案（get_model_router_for_user，与教师管家同一模型路由）；
- teacher.materials.lesson_plan → lessons.adapt_lesson（teacher.lesson.adapt 工具底层，
  内部自带能力网关与本地模板降级）；
- teacher.materials.slides → lessons.create_slides（teacher.slides.create 工具底层，
  仅接受已确认教案，沿用其 confirmation_required 状态机语义）。

部分成功语义（组卷）：题库严格命中不足时既有产物结构已标注
（validation.available_count/requested_count + insufficient + warnings），
任务侧折算 qualified_count/pending_count，任务仍算 succeeded；
全部题被拦（0 题）→ TaskPermanentError（此时产物未提交，随失败回滚）。

幂等：payload 写回 _artifact_id（对齐 classroom 的 _session_id 模式），
进程中断/手动重试按此复用已建产物，不重复建行。
本文件在 main.py lifespan 中 import 以完成注册（import 副作用即注册）。
"""

from __future__ import annotations

import json
import uuid

import structlog
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher import assessment, grading, insights, lessons
from app.domains.teacher.artifacts import create_artifact
from app.models.coursework import Assignment, Submission, SubmissionItem
from app.models.task import Task
from app.models.teacher import TeachingArtifact
from app.providers.router import get_model_router_for_user
from app.services import task_runner
from app.services.task_runner import TaskPermanentError

logger = structlog.get_logger()

# 讲评建议的 LLM 场景标识（与 agent_router scene 口径一致，用于审计与路由）
_REVIEW_LLM_SCENE = "teacher_materials"

# teacher.domain HTTPException 错误码 → 人话（detail.message 为机器键，需翻译）
_HTTP_MESSAGES = {
    40301: "需要教师身份，请用教师账号重试",
    40302: "你没有该班级的教学权限，无法完成该操作",
    40400: "目标不存在或已被删除",
    40901: "教案版本已变化，请重试",
    42210: "请先在备课页确认对应教案后再试",
    50311: "AI 生成暂不可用，请稍后重试",
}


def _humanize_http_error(e: HTTPException) -> str:
    """教师域 raise_http 的 detail.message 是机器键，翻译成用户能懂的话。"""
    detail = e.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    if code is None:
        return "AI 生成暂不可用，请稍后重试"
    return _HTTP_MESSAGES.get(
        int(code), f"操作未完成（错误码 {code}），请调整后重试"
    )


def _parse_uuid(raw: str, *, kind_label: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise TaskPermanentError(f"{kind_label}标识无效: {raw[:60]}") from None


async def _load_reusable_artifact(
    db: AsyncSession, task: Task, expected_type: str
) -> TeachingArtifact | None:
    """幂等复用：payload._artifact_id 指向本人未删除且有内容的产物 → 返回它。"""
    prev = str((task.payload or {}).get("_artifact_id") or "").strip()
    if not prev:
        return None
    try:
        aid = uuid.UUID(prev)
    except ValueError:
        return None
    artifact = await db.get(TeachingArtifact, aid)
    if (
        artifact is None
        or artifact.deleted_at is not None
        or str(artifact.owner_id) != str(task.user_id)
        or artifact.artifact_type != expected_type
    ):
        return None
    return artifact


def _bind_artifact(task: Task, payload: dict, artifact_id: uuid.UUID) -> None:
    """产物 id 写回任务 payload（幂等锚点），由调用方随后 commit。"""
    task.payload = {**payload, "_artifact_id": str(artifact_id)}


async def _bind_artifact_and_commit(
    task: Task, db: AsyncSession, artifact_id: uuid.UUID
) -> None:
    _bind_artifact(task, task.payload or {}, artifact_id)
    await db.commit()


# ==================== T-B2 组卷：teacher.quiz.create ====================


@task_runner.register_handler("teacher.quiz.create")
async def teacher_quiz_create(task: Task, db: AsyncSession, progress) -> dict:
    """T-B2 后台组卷：复用 assessment.generate_quiz（题库优先，同一内核）。

    payload: {kp_codes: [str...], count: int(3~20), difficulty?: str, title?: str, class_id: str}
    result:  {artifact_type:"teacher_quiz", artifact_id, qualified_count, pending_count,
              jump:"/teacher/resources"}
    """
    payload = task.payload or {}
    user_id = task.user_id

    kp_codes = [str(k).strip() for k in (payload.get("kp_codes") or []) if str(k).strip()]
    if not kp_codes:
        raise TaskPermanentError("缺少知识点参数，请告诉管家组卷要覆盖哪些知识点")
    try:
        count = int(payload.get("count") or 0)
    except (TypeError, ValueError):
        raise TaskPermanentError("题量参数无效，请告诉管家要出几道题") from None
    if not (3 <= count <= 20):
        raise TaskPermanentError(f"非法题量: {count}，支持 3~20 题")
    difficulty = str(payload.get("difficulty") or "").strip().lower()
    if difficulty and difficulty not in assessment.DIFFICULTIES:
        raise TaskPermanentError(
            f"非法难度: {difficulty}，支持 {'/'.join(assessment.DIFFICULTIES)}"
        )
    class_id_raw = str(payload.get("class_id") or "").strip()
    if not class_id_raw:
        raise TaskPermanentError("缺少班级参数，请告诉管家要为哪个班级组卷")
    class_id = _parse_uuid(class_id_raw, kind_label="班级")

    # ===== 幂等：重试时复用上次已创建的题集产物（防重复产物）=====
    prev = await _load_reusable_artifact(db, task, "quiz_set")
    if prev is not None:
        return _quiz_result(prev, count)

    await progress("题库检索中", 15)
    await progress(f"AI 出题中（0/{count}）", 35)

    # 与 teacher.quiz.create 工具同构：全选择题配额；难度 str → 单槽位权重
    try:
        serialized = await assessment.generate_quiz(
            db,
            user_id,
            class_id,
            knowledge_points=kp_codes,
            count=count,
            question_types={"choice": count},
            difficulty={difficulty: 1.0} if difficulty else None,
            exclude_hashes=[],
            client_request_id=f"task:{task.id}",
        )
    except HTTPException as e:
        raise TaskPermanentError(_humanize_http_error(e)) from None

    validation = serialized.get("validation") or {}
    items = (serialized.get("content") or {}).get("items") or []
    qualified = int(validation.get("available_count") or len(items))
    requested = int(validation.get("requested_count") or count)
    if qualified <= 0:
        # 全部题被拦：产物尚未提交（无后续 commit），随任务失败一并回滚
        raise TaskPermanentError(
            f"题库中没有符合知识点与题型要求的题目（0/{count}），"
            "组卷失败，请调整知识点范围或题量后重试"
        )
    pending = max(0, requested - qualified)

    await progress(f"AI 出题中（{qualified}/{count}）", 55)
    await progress("质检中", 80)

    artifact = await db.get(TeachingArtifact, uuid.UUID(serialized["artifact_id"]))
    if artifact is not None:
        # 既有结构（validation.available_count/requested_count + insufficient）之外，
        # 把达标/待重生成题数显式标注进产物 JSON，前端无需换算
        extra: dict = {}
        if pending > 0:
            extra["qualified_count"] = qualified
            extra["pending_count"] = pending
        title = str(payload.get("title") or "").strip()
        if title:
            extra["title"] = title
        if extra:
            artifact.payload = {**(artifact.payload or {}), **extra}
    _bind_artifact(task, payload, uuid.UUID(serialized["artifact_id"]))
    await db.commit()

    result = _quiz_result_from_counts(serialized["artifact_id"], qualified, pending)
    logger.info(
        "task.teacher_quiz_created",
        task_id=str(task.id),
        artifact_id=serialized["artifact_id"],
        qualified=qualified,
        pending=pending,
    )
    return result


def _quiz_result_from_counts(artifact_id: str, qualified: int, pending: int) -> dict:
    return {
        "artifact_type": "teacher_quiz",
        "artifact_id": artifact_id,
        "qualified_count": qualified,
        "pending_count": pending,
        "jump": "/teacher/resources",
    }


def _quiz_result(artifact: TeachingArtifact, count: int) -> dict:
    payload = artifact.payload or {}
    items = payload.get("items") or []
    qualified = len(items)
    requested = int((artifact.validation or {}).get("requested_count") or count)
    return _quiz_result_from_counts(str(artifact.id), qualified, max(0, requested - qualified))


# ==================== T-B3 教学材料：teacher.materials ====================

_MATERIAL_TYPES = ("class_review", "lesson_plan", "slides")
_MATERIAL_ARTIFACT_TYPES = {
    "class_review": "lecture_review",
    "lesson_plan": "lesson_plan",
    "slides": "slide_deck",
}
_MATERIAL_LABELS = {"class_review": "讲评材料", "lesson_plan": "教案", "slides": "课件"}


@task_runner.register_handler("teacher.materials")
async def teacher_materials(task: Task, db: AsyncSession, progress) -> dict:
    """T-B3 后台教学材料：讲评材料 / 教案 / 课件（复用教师域底层函数）。

    payload: {material_type: "class_review"|"lesson_plan"|"slides", class_id: str,
              assignment_id?: str, topic?: str}
    result:  {artifact_type:"teacher_materials", material_type, artifact_id,
              assignment_id?(仅 class_review), jump:"/teacher/resources"}
    """
    payload = task.payload or {}
    user_id = task.user_id
    material_type = str(payload.get("material_type") or "").strip()
    if material_type not in _MATERIAL_TYPES:
        raise TaskPermanentError(
            f"未知的教学材料类型: {material_type or '（空）'}，"
            f"支持 {'/'.join(_MATERIAL_LABELS.values())}"
        )
    class_id_raw = str(payload.get("class_id") or "").strip()
    if not class_id_raw:
        raise TaskPermanentError(
            f"缺少班级参数，请告诉管家要为哪个班级生成{_MATERIAL_LABELS[material_type]}"
        )
    class_id = _parse_uuid(class_id_raw, kind_label="班级")

    prev = await _load_reusable_artifact(db, task, _MATERIAL_ARTIFACT_TYPES[material_type])
    if prev is not None:
        return _materials_result(material_type, prev)

    if material_type == "class_review":
        return await _gen_class_review(task, db, progress, user_id, class_id, payload)
    if material_type == "lesson_plan":
        return await _gen_lesson_plan(task, db, progress, user_id, class_id, payload)
    return await _gen_slides(task, db, progress, user_id, class_id, payload)


def _materials_result(material_type: str, artifact: TeachingArtifact) -> dict:
    result = {
        "artifact_type": "teacher_materials",
        "material_type": material_type,
        "artifact_id": str(artifact.id),
        "jump": "/teacher/resources",
    }
    if material_type == "class_review":
        result["assignment_id"] = str((artifact.payload or {}).get("assignment_id") or "")
    return result


async def _undecided_submission_count(db: AsyncSession, assignment_id: uuid.UUID) -> int:
    """未批改完成的学生份数：与 insights.review_backlog 同口径
    （needs_review 或 verdict=pending_review），按提交去重。"""
    sub_ids = select(Submission.id).where(
        Submission.assignment_id == assignment_id, Submission.deleted_at.is_(None)
    )
    cnt = await db.scalar(
        select(func.count(func.distinct(SubmissionItem.submission_id))).where(
            SubmissionItem.submission_id.in_(sub_ids),
            SubmissionItem.deleted_at.is_(None),
            (SubmissionItem.needs_review.is_(True))
            | (SubmissionItem.verdict == "pending_review"),
        )
    )
    return int(cnt or 0)


async def _gen_class_review(
    task: Task,
    db: AsyncSession,
    progress,
    user_id: uuid.UUID,
    class_id: uuid.UUID,
    payload: dict,
) -> dict:
    """讲评材料：批改完整性前置校验 → 洞察/错题聚类（复用统计）→ LLM 讲评建议。"""
    try:
        # ===== 前置校验（T3-1）：作业批改完成才能讲评 =====
        await progress("作业批改状态检查中", 10)
        assignment_id_raw = str(payload.get("assignment_id") or "").strip()
        if assignment_id_raw:
            aid = _parse_uuid(assignment_id_raw, kind_label="作业")
            assignment = await db.get(Assignment, aid)
            if (
                assignment is None
                or assignment.deleted_at is not None
                or assignment.class_id != class_id
            ):
                raise TaskPermanentError("作业不存在或不属于该班级，请确认后重试")
        else:
            # 与 grading_review_insights 同一选择口径：该班最近一份已发布/已截止作业
            assignment = (
                await db.execute(
                    select(Assignment)
                    .where(
                        Assignment.class_id == class_id,
                        Assignment.status.in_(("published", "closed")),
                        Assignment.deleted_at.is_(None),
                    )
                    .order_by(Assignment.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if assignment is None:
                raise TaskPermanentError("该班级还没有可讲评的作业，请先发布并批改作业")

        undecided = await _undecided_submission_count(db, assignment.id)
        if undecided > 0:
            raise TaskPermanentError(
                f"该作业还有 {undecided} 份未批改完成，请先完成批改再生成讲评材料"
            )

        # ===== 班级洞察 + 错题聚类（确定性统计 import 复用，禁止重写）=====
        await progress("班级洞察汇总中", 30)
        insights_data = await insights.compute_class_insights(db, user_id, class_id)
        review = await grading.grading_review_insights(db, user_id, class_id)

        # ===== LLM 讲评建议（与教师管家同一模型路由）=====
        await progress("AI 生成讲评建议中", 60)
        model_router = await get_model_router_for_user(str(user_id), db)
        prompt_user = json.dumps(
            {
                "assignment": {"title": assignment.title, "assignment_id": str(assignment.id)},
                "review": review,
                "class_insights": insights_data,
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            chat = await model_router.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是数学教研助理。基于给定的确定性班级统计数据生成作业讲评建议。"
                            "只允许引用数据中出现的题目、人数与比例，禁止编造数据之外的数字或题目。"
                            "输出 3-5 条讲评建议，每条包含：聚焦的题目或知识点、讲评切入点、"
                            "课堂跟进动作。用简洁中文输出。"
                        ),
                    },
                    {"role": "user", "content": prompt_user},
                ],
                temperature=0.3,
                max_tokens=1500,
                thinking=False,
                request_id=str(task.id),
                scene=_REVIEW_LLM_SCENE,
            )
        except TaskPermanentError:
            raise
        except Exception as e:
            logger.warning("task.teacher_review_llm_error", task_id=str(task.id), error=str(e)[:200])
            raise TaskPermanentError("AI 生成暂不可用，请稍后重试") from None
        suggestions = str(chat.get("content") or "").strip()
        if not suggestions:
            raise TaskPermanentError("AI 生成暂不可用，请稍后重试")

        await progress("保存讲评材料", 90)
        artifact = await create_artifact(
            db,
            owner_id=user_id,
            artifact_type="lecture_review",
            scene="teacher.assessment",
            class_id=class_id,
            payload={
                "material_type": "class_review",
                "title": f"《{assignment.title}》讲评材料",
                "assignment_id": str(assignment.id),
                "assignment_title": assignment.title,
                "review": review,
                "class_insights": insights_data,
                "suggestions": suggestions,
            },
            source_refs=[f"assignment:{assignment.id}"],
            engine="model_router",
            degraded=False,
            validation={
                "kind": "llm_review",
                "assignment_id": str(assignment.id),
                "review_data_assignment_id": review.get("assignment_id"),
                "insight_count": len(insights_data),
                "top_question_count": len(review.get("top_questions") or []),
            },
        )
        db.add(artifact)
        await db.flush()
    except HTTPException as e:
        raise TaskPermanentError(_humanize_http_error(e)) from None

    await _bind_artifact_and_commit(task, db, artifact.id)
    logger.info(
        "task.teacher_class_review_done",
        task_id=str(task.id),
        artifact_id=str(artifact.id),
        assignment_id=str(assignment.id),
    )
    return {
        "artifact_type": "teacher_materials",
        "material_type": "class_review",
        "artifact_id": str(artifact.id),
        "assignment_id": str(assignment.id),
        "jump": "/teacher/resources",
    }


async def _gen_lesson_plan(
    task: Task,
    db: AsyncSession,
    progress,
    user_id: uuid.UUID,
    class_id: uuid.UUID,
    payload: dict,
) -> dict:
    """教案：复用 teacher.lesson.adapt 工具底层（能力网关 + 本地模板降级）。"""
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        raise TaskPermanentError("缺少主题参数，请告诉管家这节课要讲什么")
    await progress("教案生成中", 40)
    try:
        serialized = await lessons.adapt_lesson(
            db,
            user_id,
            class_id,
            topic=topic,
            source_artifact_id=None,
            source_resource_ids=[],
            source_refs=[],
            requirements=None,
            duration_minutes=None,
            client_request_id=f"task:{task.id}",
        )
    except HTTPException as e:
        raise TaskPermanentError(_humanize_http_error(e)) from None

    artifact_id = uuid.UUID(serialized["artifact_id"])
    await _bind_artifact_and_commit(task, db, artifact_id)
    logger.info("task.teacher_lesson_plan_done", task_id=str(task.id), artifact_id=str(artifact_id))
    return {
        "artifact_type": "teacher_materials",
        "material_type": "lesson_plan",
        "artifact_id": str(artifact_id),
        "jump": "/teacher/resources",
    }


async def _gen_slides(
    task: Task,
    db: AsyncSession,
    progress,
    user_id: uuid.UUID,
    class_id: uuid.UUID,
    payload: dict,
) -> dict:
    """课件：复用 teacher.slides.create 工具底层（仅接受已确认教案）。"""
    lesson = (
        await db.execute(
            select(TeachingArtifact)
            .where(
                TeachingArtifact.owner_id == user_id,
                TeachingArtifact.class_id == class_id,
                TeachingArtifact.artifact_type == "lesson_plan",
                TeachingArtifact.status == "confirmed",
                TeachingArtifact.deleted_at.is_(None),
            )
            .order_by(TeachingArtifact.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if lesson is None:
        raise TaskPermanentError("该班级还没有已确认的教案，请先在备课页确认教案后再生成课件")

    await progress("课件生成中", 40)
    try:
        serialized = await lessons.create_slides(
            db,
            user_id,
            lesson.id,
            version=lesson.version,
            style=None,
            requirements=None,
        )
    except HTTPException as e:
        raise TaskPermanentError(_humanize_http_error(e)) from None

    artifact_id = uuid.UUID(serialized["artifact_id"])
    await _bind_artifact_and_commit(task, db, artifact_id)
    logger.info(
        "task.teacher_slides_done",
        task_id=str(task.id),
        artifact_id=str(artifact_id),
        lesson_id=str(lesson.id),
    )
    return {
        "artifact_type": "teacher_materials",
        "material_type": "slides",
        "artifact_id": str(artifact_id),
        "jump": "/teacher/resources",
    }
