"""管家工具集（Tool Set）— 封装所有业务能力供 AI 调用（M2 迭代17）

每个工具是独立 async 函数，返回结构化 dict。orchestrator / skills 直接调用；
TOOL_SPECS + call_tool 暴露统一调度入口（为后续 Function Calling 预留）。

底层 100% 复用既有服务，不重写业务：
- 学情：services/growth.py（load_mastery_rows / load_errors_fsrs / composite_score / classify_subtype）
- 出题：skills/question_supply.py（supply_questions 题库真题优先）
- 组卷：skills/mock_exam.py（assemble_exam）
- 图谱：knowledge_points + kp_prerequisites
- 路由：services/platform_context.py（_PLATFORM_MAP / match_platform_item）
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler.contracts import ActorRole, ToolRisk
from app.butler.executor import ToolExecutionContext
from app.butler.registry import ToolDefinition, ToolRegistry
from app.models.growth import KpPrerequisite
from app.models.knowledge_point import KnowledgePoint
from app.services import growth as growth_svc
from app.services.platform_context import match_platform_item

# 页面路由表：前端路由 key → 跳转指令（与平台地图对齐）
_ROUTE_HINTS: dict[str, dict[str, str]] = {
    "errors": {"route": "/errors", "label": "错题本"},
    "practice": {"route": "/practice", "label": "练题中心"},
    "report": {"route": "/report", "label": "学情报告"},
    "graph": {"route": "/graph", "label": "知识图谱"},
    "exam": {"route": "/exam", "label": "模拟考试"},
    "tasks": {"route": "/tasks", "label": "每日任务"},
}


async def query_profile(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """学情查询：综合分 / 掌握度概览 / 薄弱点 Top / 错题统计。"""
    mastery_rows = await growth_svc.load_mastery_rows(db, user_id)
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    hint_dep = await growth_svc.hint_dependency(db, user_id)
    streak = await growth_svc.current_streak(db, user_id)

    avg_mastery = (
        sum(r["mastery"] for r in mastery_rows) / len(mastery_rows) if mastery_rows else 0.0
    )
    weak = sorted(mastery_rows, key=lambda r: r["mastery"])[:5]
    due_errors = [v for v in error_views if v["is_due"]]

    return {
        "composite_score": growth_svc.composite_score(avg_mastery, hint_dep, streak),
        "avg_mastery": round(avg_mastery, 4),
        "hint_dependency": round(hint_dep, 4),
        "streak_days": streak,
        "mastery_count": len(mastery_rows),
        "weak_points": [
            {
                "kp_code": w["kp_code"],
                "kp_name": w["kp_name"],
                "mastery": round(w["mastery"], 4),
            }
            for w in weak
        ],
        "error_total": len(error_views),
        "error_due": len(due_errors),
    }


async def query_due_errors(
    db: AsyncSession, user_id: uuid.UUID, limit: int = 10
) -> list[dict[str, Any]]:
    """错题调度：FSRS 到期错题队列（按可提取性升序，最该先复习的在前）。"""
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    due = sorted((v for v in error_views if v["is_due"]), key=lambda v: v["retrievability"])
    return [
        {
            "record_id": str(v["record"].id),
            "kp_code": v["record"].kp_code,
            "retrievability": v["retrievability"],
            "level": v["level"],
            "hours_to_forget": v["hours_to_forget"],
        }
        for v in due[:limit]
    ]


async def query_weak_points(
    db: AsyncSession, user_id: uuid.UUID, limit: int = 4
) -> list[dict[str, Any]]:
    """薄弱点排名：按掌握度升序（含练习量，避免"没练过"与"练了还差"混为一谈）。"""
    rows = sorted(await growth_svc.load_mastery_rows(db, user_id), key=lambda r: r["mastery"])
    return [
        {
            "kp_code": r["kp_code"],
            "kp_name": r["kp_name"],
            "mastery": round(r["mastery"], 4),
            "practice_count": r["practice_count"],
            "correct_count": r["correct_count"],
        }
        for r in rows[:limit]
    ]


async def query_kp_deps(db: AsyncSession, kp_code: str) -> dict[str, Any]:
    """知识图谱：查询某知识点的前置依赖链（ALEKS precedence relation）。"""
    rs = await db.execute(select(KpPrerequisite).where(KpPrerequisite.kp_code == kp_code))
    prereqs = [r.prereq_code for r in rs.scalars().all()]
    kp = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    kp_row = kp.scalar_one_or_none()
    return {
        "kp_code": kp_code,
        "kp_name": kp_row.name if kp_row else kp_code,
        "prereqs": prereqs,
    }


async def supply_variants(
    db: AsyncSession,
    user_id: uuid.UUID,
    kp_code: str,
    difficulties: tuple[str, ...] = ("easy", "medium", "hard"),
) -> list[dict[str, Any]]:
    """同类变式题：按知识点 + 难度递进供题（题库真题优先，复用 question_supply）。

    注：AI 生成变式走 smart_quiz（对话流），此处为题库确定性兜底。
    """
    from app.skills.question_supply import supply_questions

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for diff in difficulties:
        rows = await supply_questions(
            db, kp_codes=[kp_code], q_type=None, difficulty=diff, count=1, exclude_hashes=seen
        )
        for row in rows:
            seen.add(row.hash)
            out.append(
                {
                    "hash": row.hash,
                    "q_type": row.q_type,
                    "stem": row.stem[:200],
                    "difficulty": row.difficulty,
                    "kp_codes": row.kp_codes,
                    "source": row.source,
                }
            )
    return out


def build_route(action: str) -> dict[str, Any]:
    """页面路由：生成前端跳转指令（支持对话内跳转）。"""
    hint = _ROUTE_HINTS.get(action)
    if hint:
        return {"type": "route", "route": hint["route"], "label": hint["label"]}
    # 回退平台地图意图匹配（动作词 + 功能名）
    matched = match_platform_item(action)
    if matched:
        return {"type": "route", "route": matched.get("route"), "label": matched.get("label")}
    return {"type": "route", "route": None, "label": action}


async def recommend_path(
    db: AsyncSession, user_id: uuid.UUID, top_n: int = 3
) -> list[dict[str, Any]]:
    """学习路径推荐（规则骨架）：薄弱点 TopN → 查前置依赖 → 生成阶梯学习顺序。

    LLM 只做文案润色，前置依赖正确性由规则保证（对齐方案 §4.5）。
    """
    weak = await query_weak_points(db, user_id, limit=top_n)
    path: list[dict[str, Any]] = []
    for w in weak:
        deps = await query_kp_deps(db, w["kp_code"])
        path.append(
            {
                "kp_code": w["kp_code"],
                "kp_name": w["kp_name"],
                "mastery": w["mastery"],
                "prereqs": deps["prereqs"],
                "type": "专练",
            }
        )
    return path


# ==================== 统一调度入口 ====================

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "query_profile",
        "description": "查询学生综合分、掌握度、薄弱点、错题统计",
        "params": [],
    },
    {"name": "query_due_errors", "description": "查询 FSRS 到期错题队列", "params": ["limit"]},
    {"name": "query_weak_points", "description": "查询薄弱知识点排名", "params": ["limit"]},
    {"name": "query_kp_deps", "description": "查询知识点前置依赖链", "params": ["kp_code"]},
    {
        "name": "supply_variants",
        "description": "按知识点/难度生成同类变式题",
        "params": ["kp_code", "difficulties"],
    },
    {"name": "build_route", "description": "生成前端页面跳转指令", "params": ["action"]},
    {"name": "recommend_path", "description": "基于掌握度推荐学习路径", "params": ["top_n"]},
]

_TOOL_FUNCS: dict[str, Callable[..., Any]] = {
    "query_profile": query_profile,
    "query_due_errors": query_due_errors,
    "query_weak_points": query_weak_points,
    "query_kp_deps": query_kp_deps,
    "supply_variants": supply_variants,
    "build_route": build_route,
    "recommend_path": recommend_path,
}


async def call_tool(
    name: str, db: AsyncSession, user_id: uuid.UUID, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """按名称调度工具（供 orchestrator / 未来 Function Calling 使用）。"""
    params = params or {}
    fn = _TOOL_FUNCS.get(name)
    if fn is None:
        return {"error": f"未知工具: {name}"}
    try:
        if name == "build_route":
            out: dict[str, Any] = fn(params.get("action", ""))
            return out
        if name == "query_kp_deps":
            out = await fn(db, params.get("kp_code", ""))
            return out
        if name == "supply_variants":
            out = await fn(
                db,
                user_id,
                params.get("kp_code", ""),
                params.get("difficulties", ("easy", "medium", "hard")),
            )
            return out
        if name == "recommend_path":
            out = await fn(db, user_id, params.get("top_n", 3))
            return out
        if name == "query_due_errors":
            out = await fn(db, user_id, params.get("limit", 10))
            return out
        if name == "query_weak_points":
            out = await fn(db, user_id, params.get("limit", 4))
            return out
        out = await fn(db, user_id)
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


# ==================== 阶段 4A：类型化领域工具（Task 7） ====================
#
# 9 个本地领域工具：复用上方领域函数，封装为 ToolDefinition 注册到 Butler Registry。
# - 全部 student 可见；不暴露教师/科研/管理能力；
# - handler 只用 ToolExecutionContext 的 user_id/db，不信任模型传入的 user_id；
# - handler 不 commit/rollback/开第二事务（由 Runtime 管理事务）；
# - db=None → ToolUnavailableError → Executor 转稳定 ToolResult（不抛 AttributeError）；
# - 不使用 eval/getattr 动态调用；LLM 不得直接写掌握度/成绩/判分/错题状态。


class ToolUnavailableError(Exception):
    """工具不可用（db 缺失等）：由 Executor 转为稳定 ToolResult，不泄漏内部细节。"""


def _require_db(context: ToolExecutionContext) -> AsyncSession:
    if context.db is None:
        raise ToolUnavailableError("database unavailable")
    return context.db


async def query_assignments(
    db: AsyncSession, user_id: uuid.UUID, status: str = "todo"
) -> dict[str, Any]:
    """任务列表（F10 口径）：本人所在班级 → 定向（class/student）→ published 任务 + 进度。"""
    from datetime import UTC, datetime

    from sqlalchemy import and_, func, or_

    from app.models.class_member import ClassMember
    from app.models.coursework import (
        Assignment,
        AssignmentTarget,
        QuizItem,
        Submission,
        SubmissionItem,
    )
    from app.models.event import Event

    if status not in ("todo", "done", "all"):
        return {"total": 0, "items": [], "error": f"invalid status: {status}"}
    now = datetime.now(UTC)

    cls_rs = await db.execute(
        select(ClassMember.class_id).where(
            ClassMember.user_id == user_id, ClassMember.deleted_at.is_(None)
        )
    )
    class_ids = [c for (c,) in cls_rs.all()]

    ors = [
        and_(
            AssignmentTarget.target_type == "student",
            AssignmentTarget.target_id == user_id,
        )
    ]
    if class_ids:
        ors.append(
            and_(
                AssignmentTarget.target_type == "class",
                AssignmentTarget.target_id.in_(class_ids),
            )
        )
    target_rs = await db.execute(
        select(AssignmentTarget.assignment_id).where(
            AssignmentTarget.deleted_at.is_(None), or_(*ors)
        )
    )
    assignment_ids = list({a for (a,) in target_rs.all()})
    if not assignment_ids:
        return {"total": 0, "items": []}

    asg_rs = await db.execute(
        select(Assignment)
        .where(
            Assignment.id.in_(assignment_ids),
            Assignment.deleted_at.is_(None),
            Assignment.status == "published",
        )
        .order_by(Assignment.created_at.desc())
    )
    assignments = asg_rs.scalars().all()

    rows = []
    for a in assignments:
        if a.type == "quiz" and a.quiz_id:
            total = (
                await db.execute(
                    select(func.count(QuizItem.id)).where(
                        QuizItem.quiz_id == a.quiz_id, QuizItem.deleted_at.is_(None)
                    )
                )
            ).scalar() or 0
            done = (
                await db.execute(
                    select(func.count(SubmissionItem.id))
                    .join(Submission, SubmissionItem.submission_id == Submission.id)
                    .where(
                        Submission.user_id == user_id,
                        Submission.assignment_id == a.id,
                        Submission.deleted_at.is_(None),
                        SubmissionItem.deleted_at.is_(None),
                    )
                )
            ).scalar() or 0
            done = min(int(done), int(total)) if total else 0
            progress = {"done": done, "total": int(total)}
            completed = total > 0 and done >= total
        else:
            done = 0
            if a.lesson_id:
                ev_cnt = (
                    await db.execute(
                        select(func.count(Event.id)).where(
                            Event.user_id == user_id,
                            Event.event == "lesson_done",
                            Event.props["lesson_id"].as_string() == a.lesson_id,
                        )
                    )
                ).scalar() or 0
                done = 1 if ev_cnt > 0 else 0
            progress = {"done": done, "total": 1}
            completed = done >= 1

        overdue = bool(a.deadline and a.deadline < now and not completed)
        rows.append(
            {
                "assignment_id": str(a.id),
                "title": a.title,
                "type": a.type,
                "deadline": a.deadline.isoformat() if a.deadline else None,
                "status": a.status,
                "progress": progress,
                "overdue": overdue,
                "_completed": completed,
            }
        )

    if status == "todo":
        rows = [r for r in rows if not r["_completed"]]
    elif status == "done":
        rows = [r for r in rows if r["_completed"]]

    items = [{k: v for k, v in r.items() if k != "_completed"} for r in rows]
    return {"total": len(items), "items": items}


async def build_review_plan(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """复习计划规则骨架（无 LLM）：FSRS 到期 + 薄弱 Top1 + 打卡维持（对齐 daily_plan 三件事）。"""
    due = await query_due_errors(db, user_id, limit=10)
    weak = await query_weak_points(db, user_id, limit=2)
    profile = await query_profile(db, user_id)

    top_weak = weak[0] if weak else None
    streak = profile["streak_days"]
    due_n = len(due)

    tasks = [
        {
            "key": "review_errors",
            "title": f"复习 {due_n} 道到期错题" if due_n else "今日无到期错题",
            "why": f"{due_n} 道错题进入遗忘危险区" if due_n else "错题本已清空，保持即可",
            "est_minutes": 10,
            "benefit": "稳住记忆曲线，防止回生",
            "route": "/errors",
        },
        {
            "key": "variant_top1",
            "title": f"专练「{top_weak['kp_name']}」" if top_weak else "摸底练习",
            "why": (
                f"掌握度 {round(top_weak['mastery'] * 100)}%，当前最弱"
                if top_weak
                else "先摸底建立基线"
            ),
            "est_minutes": 15,
            "benefit": "定向突破最薄弱知识点",
            "route": f"/practice?kp={top_weak['kp_code']}" if top_weak else "/practice",
        },
        {
            "key": "keep_streak",
            "title": "保持今日打卡",
            "why": f"已连续学习 {streak} 天" if streak else "从今天开始积累学习惯性",
            "est_minutes": 5,
            "benefit": "维持学习节奏与连击",
            "route": "/practice?mode=daily",
        },
    ]
    return {"tasks": tasks, "due_count": due_n, "streak_days": streak}


# ---------- 类型化 I/O ----------


class ProfileReadInput(BaseModel):
    pass


class WeakPointItem(BaseModel):
    kp_code: str
    kp_name: str
    mastery: float


class ProfileReadOutput(BaseModel):
    composite_score: int
    avg_mastery: float
    hint_dependency: float
    streak_days: int
    mastery_count: int
    weak_points: list[WeakPointItem]
    error_total: int
    error_due: int


class DueErrorsReadInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


class DueErrorItem(BaseModel):
    record_id: str
    kp_code: str | None = None
    retrievability: float
    level: int
    hours_to_forget: float


class DueErrorsReadOutput(BaseModel):
    items: list[DueErrorItem]


class WeakPointsReadInput(BaseModel):
    limit: int = Field(default=4, ge=1, le=20)


class WeakPointDetail(BaseModel):
    kp_code: str
    kp_name: str
    mastery: float
    practice_count: int
    correct_count: int


class WeakPointsReadOutput(BaseModel):
    items: list[WeakPointDetail]


class PrerequisitesReadInput(BaseModel):
    kp_code: str = Field(min_length=1, max_length=64)


class PrerequisitesReadOutput(BaseModel):
    kp_code: str
    kp_name: str
    prereqs: list[str]


class VariantsCreateInput(BaseModel):
    kp_code: str = Field(min_length=1, max_length=64)
    difficulties: tuple[str, ...] = ("easy", "medium", "hard")


class VariantItem(BaseModel):
    hash: str
    q_type: str
    stem: str
    difficulty: str
    kp_codes: list[str]
    source: str | None = None


class VariantsCreateOutput(BaseModel):
    items: list[VariantItem]


class PathRecommendInput(BaseModel):
    top_n: int = Field(default=3, ge=1, le=5)


class PathStep(BaseModel):
    kp_code: str
    kp_name: str
    mastery: float
    prereqs: list[str]
    type: str


class PathRecommendOutput(BaseModel):
    items: list[PathStep]


class RouteBuildInput(BaseModel):
    action: str = Field(min_length=1, max_length=64)


class RouteBuildOutput(BaseModel):
    type: str
    route: str | None = None
    label: str


class AssignmentListInput(BaseModel):
    status: str = Field(default="todo", pattern="^(todo|done|all)$")


class AssignmentProgress(BaseModel):
    done: int
    total: int


class AssignmentItem(BaseModel):
    assignment_id: str
    title: str
    type: str
    deadline: str | None = None
    status: str
    progress: AssignmentProgress
    overdue: bool


class AssignmentListOutput(BaseModel):
    total: int
    items: list[AssignmentItem]


class ReviewPlanReadInput(BaseModel):
    pass


class ReviewTask(BaseModel):
    key: str
    title: str
    why: str
    est_minutes: int
    benefit: str
    route: str


class ReviewPlanReadOutput(BaseModel):
    tasks: list[ReviewTask]
    due_count: int
    streak_days: int


# ---------- handlers（显式函数，不使用 eval/getattr） ----------


async def _h_profile_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await query_profile(db, context.request.actor.user_id)


async def _h_due_errors_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    items = await query_due_errors(
        db, context.request.actor.user_id, limit=validated_input["limit"]
    )
    return {"items": items}


async def _h_weak_points_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    items = await query_weak_points(
        db, context.request.actor.user_id, limit=validated_input["limit"]
    )
    return {"items": items}


async def _h_kp_deps_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await query_kp_deps(db, validated_input["kp_code"])


async def _h_variants_create(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    items = await supply_variants(
        db,
        context.request.actor.user_id,
        validated_input["kp_code"],
        tuple(validated_input["difficulties"]),
    )
    return {"items": items}


async def _h_path_recommend(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    items = await recommend_path(db, context.request.actor.user_id, top_n=validated_input["top_n"])
    return {"items": items}


async def _h_route_build(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    return build_route(validated_input["action"])


async def _h_assignment_list(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await query_assignments(
        db, context.request.actor.user_id, status=validated_input["status"]
    )


async def _h_review_plan_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await build_review_plan(db, context.request.actor.user_id)


# ---------- 注册 ----------

_STUDENT_ONLY = frozenset({ActorRole.STUDENT})

_DOMAIN_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name="student.profile.read",
        version="1.0.0",
        description="查询学生学情画像：综合分、掌握度概览、薄弱点 Top、错题统计",
        input_model=ProfileReadInput,
        output_model=ProfileReadOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        # 迭代20：追加 "student.chat"（P2 前瞻场景扩展，现有工具纯扩面；
        # 消费链 build_m2_registry→ButlerRuntime 当前被 butler_router 短路，行为零变化）
        allowed_scenes=frozenset({"student.dashboard", "student.profile", "student.chat"}),
        timeout_s=10.0,
        handler=_h_profile_read,
    ),
    ToolDefinition(
        name="student.errors.due.read",
        version="1.0.0",
        description="查询 FSRS 到期错题队列（按可提取性升序，最该先复习的在前）",
        input_model=DueErrorsReadInput,
        output_model=DueErrorsReadOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset(
            {"student.dashboard", "student.errors", "student.review", "student.chat"}
        ),
        timeout_s=10.0,
        handler=_h_due_errors_read,
    ),
    ToolDefinition(
        name="student.mastery.weak_points.read",
        version="1.0.0",
        description="查询薄弱知识点排名（按掌握度升序，含练习量）",
        input_model=WeakPointsReadInput,
        output_model=WeakPointsReadOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.dashboard", "student.profile", "student.chat"}),
        timeout_s=10.0,
        handler=_h_weak_points_read,
    ),
    ToolDefinition(
        name="knowledge.prerequisites.read",
        version="1.0.0",
        description="查询某知识点的前置依赖链（ALEKS precedence relation）",
        input_model=PrerequisitesReadInput,
        output_model=PrerequisitesReadOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice", "student.graph"}),
        timeout_s=10.0,
        handler=_h_kp_deps_read,
    ),
    ToolDefinition(
        name="practice.variants.create",
        version="1.0.0",
        description="按知识点/难度递进生成同类变式题（题库真题优先，question_supply 去重与质量链）",
        input_model=VariantsCreateInput,
        output_model=VariantsCreateOutput,
        risk=ToolRisk.LEARNING_ACTION,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.practice"}),
        timeout_s=15.0,
        handler=_h_variants_create,
    ),
    ToolDefinition(
        name="learning.path.recommend",
        version="1.0.0",
        description="基于掌握度推荐学习路径（薄弱点 TopN + 前置依赖阶梯顺序）",
        input_model=PathRecommendInput,
        output_model=PathRecommendOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.dashboard", "student.path"}),
        timeout_s=10.0,
        handler=_h_path_recommend,
    ),
    ToolDefinition(
        name="ui.route.build",
        version="1.0.0",
        description="生成前端页面跳转指令（对话内跳转）",
        input_model=RouteBuildInput,
        output_model=RouteBuildOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset(
            {
                "student.dashboard",
                "student.practice",
                "student.errors",
                "student.tasks",
                "student.profile",
                "student.graph",
                "student.path",
                "student.review",
            }
        ),
        timeout_s=5.0,
        handler=_h_route_build,
    ),
    ToolDefinition(
        name="assignment.list",
        version="1.0.0",
        description="查询学生任务列表（本人所在班级定向任务 + 进度）",
        input_model=AssignmentListInput,
        output_model=AssignmentListOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.dashboard", "student.tasks"}),
        timeout_s=10.0,
        handler=_h_assignment_list,
    ),
    ToolDefinition(
        name="review.plan.read",
        version="1.0.0",
        description="查询今日复习计划规则骨架（FSRS 到期 + 薄弱 Top1 + 打卡维持，无 LLM）",
        input_model=ReviewPlanReadInput,
        output_model=ReviewPlanReadOutput,
        risk=ToolRisk.READ,
        allowed_roles=_STUDENT_ONLY,
        allowed_scenes=frozenset({"student.dashboard", "student.review"}),
        timeout_s=10.0,
        handler=_h_review_plan_read,
    ),
)


def register_domain_tools(registry: ToolRegistry) -> None:
    """显式注册 9 个本地领域工具到指定 registry（阶段 4.1 统一工厂组合用）。"""
    for definition in _DOMAIN_TOOL_DEFINITIONS:
        registry.register(definition)


def build_domain_registry() -> ToolRegistry:
    """注册 9 个本地领域工具（阶段 4A）：全部学生可见，不暴露教师/科研/管理能力。"""
    reg = ToolRegistry()
    register_domain_tools(reg)
    return reg


# ==================== stu-r2 管线通电：student.chat 场景注册表组合 ====================
# 注意：可视化工具不并入 _DOMAIN_TOOL_DEFINITIONS / register_domain_tools——
# tests/test_butler_domain_tools.py:232 断言 domain registry 恰为 9 个工具；
# 本组合仅供 student.chat 共享 Runtime 工厂（app/butler/chat_bridge.py）使用。


def register_student_chat_tools(registry: ToolRegistry) -> None:
    """student.chat 场景完整注册：9 领域工具 + 3 后台任务工具 + 2 学情可视化只读工具。"""
    from app.butler.learning_viz_tools import register_learning_viz_tools
    from app.butler.student_task_tools import register_student_task_tools

    for definition in _DOMAIN_TOOL_DEFINITIONS:
        registry.register(definition)
    register_student_task_tools(registry)
    register_learning_viz_tools(registry)
