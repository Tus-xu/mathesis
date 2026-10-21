"""AI 管家路由（Butler Router）— M2 迭代17

端点（对齐方案 §7.3）：
- POST /api/butler/chat              学生 AI 管家对话（v2：Planner → 代建后台任务）
- POST /api/butler/events/emit         业务模块上报学习事件
- GET  /api/butler/dashboard           管家面板（右栏）：开场白 + 今日任务 + 到期错题 + 薄弱点 + 鼓励
- GET  /api/butler/daily-plan          今日 3 件事（LLM 生成版）
- GET  /api/butler/weekly-report       周报（"小婷的话"）
- GET  /api/butler/path-plan           学习路径规划
- GET  /api/butler/recommend           资源/变式推荐（薄弱点驱动）
- GET  /api/butler/actions             最近管家动作（前端轮询）
- POST /api/butler/actions/{id}/feedback  学生反馈（接受/拒绝/跳过）
- GET  /api/butler/settings            管家推送设置
- PATCH /api/butler/settings           更新管家推送设置

约定：信封 {code:0, message:"ok", data:...}；鉴权 get_current_user；
无数据返回空态默认值，绝不抛 500（对齐 growth_router）。
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler import skills as butler_skills
from app.butler.chat_bridge import (
    build_fixed_plan,
    build_student_chat_runtime,
    data_intent_of,
)
from app.butler.event_bus import get_event_bus
from app.butler.orchestrator import get_orchestrator
from app.butler.runtime import run_v2_shadow
from app.butler.student_task_tools import (
    STUDENT_TASK_TOOL_SUFFIX,
    TASK_JUMP,
    butler_task_idempotency_key,
)
from app.butler.tools import query_due_errors, query_weak_points
from app.config import settings
from app.gateway.auth import get_current_user
from app.models.ai_recommendation import AIRecommendation
from app.models.database import get_db
from app.models.task import Task
from app.models.user_profile import UserProfile
from app.services import copy_polish, task_runner
from app.services import growth as growth_svc

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/butler", tags=["butler"])


def _v2_migrated_scenes() -> frozenset[str]:
    """已迁移到 Butler Kernel v2 的场景。

    阶段 4 学生端：student.chat 已合并 3 个后台任务工具
    （student.practice.generate / student.classroom.session / student.socratic.autosolve，
    见 app/butler/student_task_tools.py），在此登记切流；
    其余场景继续走旧内核。
    """
    return frozenset({"student.chat"})


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


# ==================== Schemas ====================


class EventEmitRequest(BaseModel):
    event_type: str
    source_type: str
    source_id: str | None = None
    payload: dict | None = None
    idempotency_key: str | None = None


class FeedbackRequest(BaseModel):
    feedback: str  # accept/reject/skip


class SettingsRequest(BaseModel):
    butler_enabled: bool | None = None


class ErrorTutorRequest(BaseModel):
    record_id: uuid.UUID
    student_message: str
    history: list | None = None  # [{role: "user"|"assistant", content: str}, ...]


class ButlerChatRequest(BaseModel):
    """学生管家对话入参（镜像教师端：客户端只可提交消息与幂等键）。

    client_request_id 上限 128 对齐 agent_runs.client_request_id 列宽；
    重复 client_request_id 由 Runtime 按 UniqueConstraint 幂等回放。
    """

    message: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1, max_length=128)


# ==================== AI 管家对话（Butler Kernel v2 · student.chat） ====================
# planner 提示词已迁移至 app/butler/chat_bridge.py（STUDENT_PLANNER_PROMPT，
# floating-butler 与对话桥接共用一份，避免两处不一致）。


async def _build_student_butler_runtime(user_id: uuid.UUID, db: AsyncSession):
    """构建学生专属 Runtime（stu-r2 起委托 chat_bridge 共享工厂）。

    注册表 = M2 统一注册表 + 3 个学生后台任务工具 + 2 个学情可视化只读工具；
    PolicyGate 放行 READ + LEARNING_ACTION（工具只建任务/只读，无 WRITE/EXTERNAL 副作用）。
    """
    return await build_student_chat_runtime(user_id, db)


@router.post("/chat")
async def butler_chat(
    req: ButlerChatRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生 AI 管家对话：Planner 识别意图 → 代用户创建后台任务（出题/课堂/解题）。

    - butler_v2_enabled=false 或场景未迁移 → 走 v1 兜底（规则化回复，不报错）；
    - v2：ButlerRuntime 固定管线（Context→Plan→Policy→Execute→Compose），
      预算/超时/幂等沿用 runtime 既有机制，重复 client_request_id 幂等回放；
    - 信封 data={"envelope":{replies, actions, run_id}}，由 composer 输出如实序列化，
      task 信息按幂等键规则从 tasks 表取实际行（不编造字段）。
    """
    user_id = uuid.UUID(user["sub"])
    if not (settings.butler_v2_enabled and "student.chat" in _v2_migrated_scenes()):
        # v1 旧行为：旧内核未提供对话端点，返回规则化兜底回复，绝不报错
        return _ok(
            {
                "envelope": {
                    "replies": [
                        {
                            "kind": "text",
                            "text": "收到！智能任务助手升级中，你可以先在任务中心直接下单生成练习。",
                        }
                    ],
                    "actions": [],
                    "run_id": None,
                }
            }
        )

    from app.butler.facade import build_student_chat_butler_request

    butler_request = build_student_chat_butler_request(
        user_id=user_id,
        message=req.message,
        conversation_id=None,
        client_request_id=req.client_request_id,
    )
    # stu-r2 管线通电：数据类意图（学情/记忆曲线）→ 确定性 fixed_plan 强制真调工具
    # （PolicyGate/Registry 校验不变，LLM 无参数可编）；纯聊天/任务意图走原 planner。
    fixed_plan = None
    try:
        intent = data_intent_of(req.message)
        fixed_plan = build_fixed_plan(intent, req.message) if intent else None
    except Exception:  # noqa: BLE001 —— 意图识别失败不影响主链路
        fixed_plan = None
    runtime = await _build_student_butler_runtime(user_id, db)
    envelope = await runtime.run(butler_request, db, fixed_plan=fixed_plan)
    await db.commit()

    # composer 的 actions 只有 {tool_name, status}；task 信息按幂等键规则
    # 反查 tasks 实际行（工具已按 butler:{client_request_id}:{suffix} 下单）
    ok_task_tools = [
        name
        for name in STUDENT_TASK_TOOL_SUFFIX
        if any(
            a.get("tool_name") == name and a.get("status") == "ok"
            for a in envelope.actions
        )
    ]
    tasks_by_key: dict[str, Task] = {}
    if ok_task_tools:
        keys = [
            butler_task_idempotency_key(req.client_request_id, STUDENT_TASK_TOOL_SUFFIX[n])
            for n in ok_task_tools
        ]
        rows = await db.execute(
            select(Task).where(Task.user_id == user_id, Task.idempotency_key.in_(keys))
        )
        for t in rows.scalars():
            tasks_by_key[t.idempotency_key] = t
    actions_out: list[dict] = []
    for name in ok_task_tools:
        task = tasks_by_key.get(
            butler_task_idempotency_key(req.client_request_id, STUDENT_TASK_TOOL_SUFFIX[name])
        )
        if task is None:
            continue
        actions_out.append(
            {
                "type": "task_created",
                "label": task_runner.handler_label(task.kind),
                "task_id": str(task.id),
                "jump": TASK_JUMP,
            }
        )

    # stu-r2：结构化数据卡透传（additive）——composer 产出的 {type:'card'} 块
    # 供悬浮管家面板渲染同一组前端卡片；degraded 原样透传供 UI 降级提示。
    card_blocks = [
        b for b in (envelope.blocks or [])
        if isinstance(b, dict) and b.get("type") == "card"
    ]
    envelope_out = {
        "replies": [{"kind": "text", "text": envelope.text}],
        "actions": actions_out,
        "run_id": str(envelope.run_id),
        "degraded": envelope.degraded,
    }
    if card_blocks:
        envelope_out["blocks"] = card_blocks

    return _ok({"envelope": envelope_out})


# ==================== 事件上报 ====================


@router.post("/events/emit")
async def emit_event(
    req: EventEmitRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """业务模块上报学习事件（判分/错题/登录等）→ 落库 + 触发管家调度。"""
    user_id = uuid.UUID(user["sub"])
    bus = get_event_bus()
    event = await bus.emit(
        db,
        user_id=user_id,
        event_type=req.event_type,
        source_type=req.source_type,
        source_id=req.source_id,
        payload=req.payload or {},
        idempotency_key=req.idempotency_key,
    )
    await db.commit()
    if event is not None:
        # 事件驱动处理（best-effort，不阻塞响应）
        await get_orchestrator().dispatch(db, event)
        await db.commit()
    return _ok({"event_id": str(event.id) if event else None, "processed": event is not None})


# ==================== 管家面板 ====================


@router.get("/dashboard")
async def dashboard(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管家面板（右栏 L2）：开场白 + 今日 3 件事 + 到期错题 + 薄弱点 + 鼓励语。"""
    user_id = uuid.UUID(user["sub"])

    # Butler Kernel v2（阶段 3C，默认关闭；真实工具迁移前不切流）
    if settings.butler_v2_shadow:
        asyncio.create_task(run_v2_shadow(user_id, "student.dashboard", "dashboard"))
    if settings.butler_v2_enabled and "student.dashboard" in _v2_migrated_scenes():
        # 已迁移场景走 v2；本阶段 _v2_migrated_scenes() 为空，永不进入
        pass

    plan = await butler_skills.daily_plan(db, user_id)
    due = await query_due_errors(db, user_id, limit=5)
    weak = await query_weak_points(db, user_id, limit=4)

    # 鼓励语（复用 copy_polish，数据驱动模板兜底）
    streak = await growth_svc.current_streak(db, user_id)
    encourage_tpl = (
        f"已连续学习 {streak} 天，保持这个节奏，薄弱点会一个个拿下。"
        if streak
        else "今天迈出第一步，就是最好的开始。"
    )
    encouragement = await copy_polish.polish(
        copy_polish.SCENE_PANEL_ENCOURAGEMENT,
        encourage_tpl,
        data_fingerprint=f"streak:{streak}",
        user_id=user_id,
    )

    return _ok(
        {
            "greeting": plan["greeting"],
            "today_tasks": plan["tasks"],
            "due_errors": due,
            "weak_points": weak,
            "encouragement": encouragement,
            "streak_days": streak,
        }
    )


@router.get("/daily-plan")
async def daily_plan(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日 3 件事（LLM 生成版）。"""
    user_id = uuid.UUID(user["sub"])
    plan = await butler_skills.daily_plan(db, user_id)
    return _ok(plan)


@router.get("/weekly-report")
async def weekly_report(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """周报（"小婷的话"，LLM 生成版）。"""
    user_id = uuid.UUID(user["sub"])
    report = await butler_skills.weekly_report(db, user_id)
    return _ok(report)


@router.get("/error-diagnosis/{record_id}")
async def error_diagnosis(
    record_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题 AI 错因诊断：根因 + 记忆口诀 + 补救建议（12 类思维漏洞归类）。"""
    user_id = uuid.UUID(user["sub"])
    diag = await butler_skills.error_diagnosis(db, user_id, record_id)
    if "error" in diag:
        return {"code": 40400, "message": diag["error"]}
    return _ok(diag)


@router.get("/error-detail/{record_id}")
async def error_detail(
    record_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题 AI 详情：原题 + 学生答案 + 错因 + **AI 生成正解**（补全缺失的答案字段）。"""
    user_id = uuid.UUID(user["sub"])
    detail = await butler_skills.error_detail(db, user_id, record_id)
    if "error" in detail:
        return {"code": 40400, "message": detail["error"]}
    return _ok(detail)


@router.post("/error-tutor")
async def error_tutor(
    req: ErrorTutorRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题 AI 答疑（Khanmigo 苏格拉底引导式 chat）：**强约束不直给答案**，每轮 ≤5 句。"""
    user_id = uuid.UUID(user["sub"])
    result = await butler_skills.error_tutor(
        db, user_id, req.record_id, req.student_message, history=req.history or []
    )
    if "error" in result:
        return {"code": 40400, "message": result["error"]}
    return _ok(result)


@router.get("/path-plan")
async def path_plan(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学习路径规划（薄弱点 + 前置依赖规则骨架 + LLM 编排）。"""
    user_id = uuid.UUID(user["sub"])
    plan = await butler_skills.path_plan(db, user_id)
    return _ok(plan)


@router.get("/recommend")
async def recommend(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """资源/变式推荐：薄弱点驱动（题库同类题 + AI 建议文案）。"""
    from app.butler.tools import supply_variants

    user_id = uuid.UUID(user["sub"])
    weak = await query_weak_points(db, user_id, limit=3)
    if not weak:
        return _ok({"weak_point": None, "variants": [], "suggestion": "先做几道题建立基线"})

    top = weak[0]
    variants = await supply_variants(db, user_id, top["kp_code"])
    suggestion = (
        f"「{top['kp_name']}」掌握度 {round(top['mastery'] * 100)}%，建议先做基础题，"
        f"逐步递进到难题。"
    )
    return _ok(
        {
            "weak_point": top,
            "variants": variants,
            "suggestion": suggestion,
        }
    )


# ==================== 管家动作（轮询 + 反馈） ====================


@router.get("/actions")
async def list_actions(
    limit: int = 20,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """最近管家动作（前端轮询/首屏拉取）。"""
    user_id = uuid.UUID(user["sub"])
    # 迭代21 防御（兑现模块头「无数据返回空态默认值，绝不抛 500」承诺）：
    # ai_recommendations 属 stu_002_learning_ledger_tables 迁移补齐的四表之一——
    # 干净环境迁移未跑到 head 时缺表，裸 select 500 整个轮询接口。降级为空列表。
    try:
        rs = await db.execute(
            select(AIRecommendation)
            .where(AIRecommendation.user_id == user_id)
            .order_by(AIRecommendation.created_at.desc())
            .limit(min(max(limit, 1), 50))
        )
    except ProgrammingError as e:
        logger.warning(
            "butler_actions_table_missing",
            error=str(e.orig if hasattr(e, "orig") else e)[:150],
        )
        await db.rollback()
        return {"code": 0, "message": "ok", "data": {"items": []}}
    items = [
        {
            "id": str(r.id),
            "kind": r.kind,
            "source": r.source,
            "payload": r.payload,
            "user_feedback": r.user_feedback,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rs.scalars().all()
    ]
    return _ok({"items": items})


@router.post("/actions/{action_id}/feedback")
async def action_feedback(
    action_id: uuid.UUID,
    req: FeedbackRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生反馈（accept/reject/skip）→ 数据飞轮。"""
    from datetime import UTC, datetime

    user_id = uuid.UUID(user["sub"])
    if req.feedback not in ("accept", "reject", "skip"):
        return {"code": 40001, "message": "feedback 仅支持 accept/reject/skip"}

    rec = await db.get(AIRecommendation, action_id)
    if rec is None or rec.user_id != user_id:
        return {"code": 40400, "message": "推荐记录不存在"}
    rec.user_feedback = req.feedback
    rec.acted_at = datetime.now(UTC)
    await db.commit()
    return _ok({"id": str(action_id), "feedback": req.feedback})


# ==================== 管家设置 ====================


@router.get("/settings")
async def get_settings(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管家推送设置（学生可关闭主动推送）。"""
    user_id = uuid.UUID(user["sub"])
    rs = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = rs.scalar_one_or_none()
    prefs = dict(profile.preferences or {}) if profile else {}
    return _ok(
        {
            "butler_enabled": prefs.get("butler_enabled", True),
            "butler_scenes": prefs.get("butler_scenes", {}),
        }
    )


@router.patch("/settings")
async def update_settings(
    req: SettingsRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新管家推送设置（存 user_profiles.preferences.butler_enabled）。"""
    user_id = uuid.UUID(user["sub"])
    rs = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = rs.scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id, level="unknown")
        db.add(profile)
    prefs = dict(profile.preferences or {})
    if req.butler_enabled is not None:
        prefs["butler_enabled"] = req.butler_enabled
    profile.preferences = prefs
    await db.commit()
    return _ok({"butler_enabled": prefs.get("butler_enabled", True)})
