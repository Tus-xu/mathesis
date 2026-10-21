"""任务处理器扩展（一）：classroom.session —— S-B3 课程 → 双师课堂自动链路

黄金路径 S-B3：管家后台任务直接复用 classroom 同一生成内核
（course_router._run_course_preprocess + stage_router._run_generation），
单一内核纪律：后台任务与双师课堂页直开走同一实现，禁止第二套生成逻辑。

链路（对齐 POST /api/classroom/sessions 端点语义）：
  课程未 ready → 直接 await 课程预处理内核（自动链式预处理，S-B3 关键）
  → 创建 ClassroomSession 行（status="generating"，与端点同构）
  → create_task 调度 _run_generation（端点同款，内部自带 _active_generations 防重入
    与 stale 自愈，因此重试/复用场景重复调度是安全的）
  → 轮询 classroom_sessions.status 至 ready/failed（任务语义 = 生成完毕后通知）。

payload: {course_id: str, title?: str, outline_mode?: str（预留参数，内核暂无对应
          开关，仅透传保留，不影响生成）}
result:  {artifact_type:"classroom", session_id, course_id, page_count, jump:"/dual/{id}"}

本文件在 main.py lifespan 中 import 以完成注册（import 副作用即注册）。
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.classroom import ClassroomSession
from app.models.course import Course
from app.models.task import Task
from app.services import task_runner
from app.services.task_runner import TaskPermanentError

logger = structlog.get_logger()

# 复用 classroom 生成内核（stage_router/course_router 不做任何改动）
from app.domains.classroom.course_router import _run_course_preprocess  # noqa: E402
from app.domains.classroom.stage_router import _run_generation  # noqa: E402

# 轮询口径：每 2s 查一次会话状态，上限 15 分钟（课堂生成实测 ~90s，留足余量）
CLASSROOM_POLL_INTERVAL_S = 2.0
CLASSROOM_POLL_TIMEOUT_S = 15 * 60


@task_runner.register_handler("classroom.session")
async def classroom_session(task: Task, db: AsyncSession, progress) -> dict:
    """S-B3：后台为课程生成双师课堂（预处理 → 建会话 → 生成 → ready 通知）。

    任务语义是"课堂生成完毕后通知"，因此等待生成终态（轮询）后才返回；
    生成过程本身的进度可见性由课堂 events 流承担（与端点直开一致）。
    """
    payload = task.payload or {}
    course_id_raw = str(payload.get("course_id") or "").strip()
    if not course_id_raw:
        raise TaskPermanentError("缺少课程参数，请告诉管家要为哪门课生成课堂")

    try:
        course_id = uuid.UUID(course_id_raw)
    except ValueError:
        raise TaskPermanentError(f"课程标识无效: {course_id_raw[:60]}") from None

    course = await db.get(Course, course_id)
    if course is None or course.deleted_at:
        raise TaskPermanentError("课程不存在，请先在课程页上传/选择课程")

    # ===== 链式预处理（S-B3 关键）：课程未 ready → 直接 await 预处理内核 =====
    if course.status != "ready":
        await progress("课程预处理中", 10)
        # 预处理内核自带幂等（ready 短路）与三层降级（workflow→本地 LLM→固定切段），
        # 内部使用独立后台会话，与 handler 会话解耦
        await _run_course_preprocess(str(course_id))
        # 其他会话提交的结果：READ COMMITTED 下重新 SELECT 取最新行
        await db.refresh(course)
        if course.status != "ready":
            raise TaskPermanentError(
                "课程预处理失败，请检查课程转写内容后在课程页重试"
            )

    # ===== 幂等：重试时复用上次已创建的会话（防重复产物）=====
    session: ClassroomSession | None = None
    prev_sid = str(payload.get("_session_id") or "").strip()
    if prev_sid:
        try:
            prev = await db.get(ClassroomSession, uuid.UUID(prev_sid))
        except ValueError:
            prev = None
        if prev is not None and not prev.deleted_at and prev.status in ("generating", "ready"):
            session = prev

    if session is None:
        # 与 POST /sessions 端点同构建行（OpenMAIC 语义：course 为增强上下文）
        title = (str(payload.get("title") or "").strip() or course.title or "").strip()
        session = ClassroomSession(
            course_id=course_id,
            user_id=task.user_id,
            title=title[:180] or "数学课堂",
            mode="sync",
            slide_count=10,
            status="generating",
            source_type="topic",
        )
        db.add(session)
        await db.flush()
        # 记下会话 id：进程中断后 stale 重拉/手动重试时按此复用，不重复建会话
        task.payload = {**payload, "_session_id": str(session.id)}
        await db.commit()
    else:
        await db.refresh(session)

    session_id = str(session.id)

    # ===== 调度生成（端点同款 create_task；_run_generation 自带防重入）=====
    await progress("课堂生成中", 30)
    gen_task = asyncio.get_running_loop().create_task(_run_generation(session_id))

    # ===== 轮询终态（progress 提交即结束当前事务，下轮 SELECT 取新快照）=====
    deadline = time.monotonic() + CLASSROOM_POLL_TIMEOUT_S
    cycles = 0
    while True:
        row = (
            await db.execute(
                select(ClassroomSession.status, ClassroomSession.deleted_at).where(
                    ClassroomSession.id == session.id
                )
            )
        ).first()
        if row is None:
            raise TaskPermanentError("课堂会话不存在，任务终止")
        status, deleted_at = row
        if deleted_at is not None:
            raise TaskPermanentError("课堂已被删除，任务终止")
        if status == "ready":
            break
        if status == "failed":
            raise TaskPermanentError("课堂生成失败，可在双师课堂页查看原因并重试")
        if gen_task.done():
            # 生成协程已退出：复核一次状态（避免"状态落库 vs 协程退出"竞态误判）
            await asyncio.sleep(0.05)
            final = (
                await db.execute(
                    select(ClassroomSession.status).where(ClassroomSession.id == session.id)
                )
            ).first()
            final_status = (final or ("generating",))[0]
            if final_status == "ready":
                break
            if final_status == "failed":
                raise TaskPermanentError("课堂生成失败，可在双师课堂页查看原因并重试")
            raise TaskPermanentError("课堂生成异常结束，可在双师课堂页重试")
        if time.monotonic() > deadline:
            raise TaskPermanentError(
                "课堂生成超时（15 分钟），可稍后在双师课堂页查看结果"
            )
        cycles += 1
        # 协作取消检查点（progress 内感知 cancelled 并抛 CancelledError）
        await progress("课堂生成中", min(95, 30 + cycles * 5))
        await asyncio.sleep(CLASSROOM_POLL_INTERVAL_S)

    await progress("课堂生成完成", 98)
    await db.refresh(session)  # 轮询期间其他会话落库的 slides，重新加载
    slides = session.slides or []
    page_count = len(slides) if isinstance(slides, list) else 0

    logger.info(
        "task.classroom_session_done",
        task_id=str(task.id),
        session_id=session_id,
        pages=page_count,
    )
    return {
        "artifact_type": "classroom",
        "session_id": session_id,
        "course_id": str(course_id),
        "page_count": page_count,
        "jump": f"/dual/{session_id}",
    }
