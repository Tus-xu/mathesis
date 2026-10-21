"""学生端 AI 管家 v2 后台任务工具（Butler Kernel v2 · student.chat 场景）

镜像 app/domains/teacher/registry.py 的 ToolDefinition 写法：
3 个学生工具，全部 actor=STUDENT、scene="student.chat"、risk=LEARNING_ACTION。
PolicyGate 以 allowed_risks={READ, LEARNING_ACTION} 放行（对齐教师端）。

工具只负责「建任务（queued + spawn）」，不等待完成——完成通知由任务中心发
（task_runner._notify_terminal；classroom/socratic 处理器后续在 task_handlers
注册，未注册的 kind 会被任务中心以稳定失败收尾，不阻塞对话）。

幂等键约定（落 tasks.idempotency_key，与 /api/tasks 同列便于对账）：
    butler:{client_request_id}:{practice|classroom|socratic}
Runtime 对重复 client_request_id 直接 duplicate envelope（不会重复进工具）；
create_task 的幂等键再兜一层：同键返回已有行，不重复下单。

Planner 选工具依赖 description（提示词有 80 字符截断，关键信息前置）：
什么时候用、参数含义写清楚；参数不全时 Planner 应先向用户澄清（ask），
工具侧对缺参只做兜底校验（返回提示性文本，不创建任务），不编造业务参数。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.butler.contracts import ActorRole, ToolRisk
from app.butler.executor import ToolExecutionContext
from app.butler.registry import ToolDefinition, ToolRegistry
from app.services import task_runner

# 任务 kind（与 task_handlers 处理器注册表 / task_runner._HANDLER_LABELS 对齐）
KIND_PRACTICE_GENERATE = "practice.generate"
KIND_CLASSROOM_SESSION = "classroom.session"
KIND_SOCRATIC_AUTOSOLVE = "socratic.autosolve"

# 幂等键后缀（router 按 tool_name 反查任务行时复用同一规则，不复制魔法字符串）
IDEM_SUFFIX_PRACTICE = "practice"
IDEM_SUFFIX_CLASSROOM = "classroom"
IDEM_SUFFIX_SOCRATIC = "socratic"

#: 工具名 → 幂等键后缀（router 序列化 task_created action 时按此反查 tasks 行）
STUDENT_TASK_TOOL_SUFFIX: dict[str, str] = {
    "student.practice.generate": IDEM_SUFFIX_PRACTICE,
    "student.classroom.session": IDEM_SUFFIX_CLASSROOM,
    "student.socratic.autosolve": IDEM_SUFFIX_SOCRATIC,
}

STUDENT_CHAT_SCENE = "student.chat"
TASK_JUMP = "/tasks"

# tasks.idempotency_key 列宽（对齐 task_router 的截断口径）
_IDEM_MAX_LEN = 160


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ==================== 输入/输出模型 ====================


class PracticeGenerateIn(_Strict):
    kp_code: str = Field(min_length=1)
    count: int = Field(default=5, ge=5, le=30)


class ClassroomSessionIn(_Strict):
    course_id: str | None = None
    topic: str | None = None


class SocraticAutosolveIn(_Strict):
    question_text: str | None = None
    file_id: str | None = None


class StudentTaskOut(_Strict):
    """建任务结果：task_id 为空表示未下单（缺参提示，message 承载提示文本）。"""

    task_id: str | None = None
    kind: str | None = None
    jump: str | None = None
    message: str


# ==================== 工具实现 ====================


def butler_task_idempotency_key(client_request_id: str, suffix: str) -> str:
    """管家对话下单的幂等键规则（router 反查任务行与工具下单共用同一函数）。"""
    return f"butler:{client_request_id}:{suffix}"[:_IDEM_MAX_LEN]


async def _create_and_spawn(
    context: ToolExecutionContext, *, kind: str, payload: dict, suffix: str, message: str
) -> dict:
    """业务事务内建任务行 → commit → spawn（对齐 task_router 既有模式）。

    commit 必须先于 spawn：后台 runner 用独立会话（background_session_factory），
    未提交的任务行读不到；同时本次运行的 AgentRun(running) 一并落库。
    """
    db = context.db
    if db is None:
        # executor 会把异常转成稳定 tool_error（degraded），不向学生端泄漏堆栈
        raise RuntimeError("db_unavailable")
    task, created = await task_runner.create_task(
        db,
        user_id=context.request.actor.user_id,
        role=context.request.actor.role.value,
        kind=kind,
        payload=payload,
        idempotency_key=butler_task_idempotency_key(
            context.request.client_request_id, suffix
        ),
    )
    await db.commit()
    if created:
        task_runner.spawn(task)
    return {"task_id": str(task.id), "kind": kind, "jump": TASK_JUMP, "message": message}


async def _resolve_kp_code(db, raw: str) -> dict:
    """把用户/Planner 给的知识点名称或编码解析为真实 kp_code。

    解析顺序：精确编码/名称 → 子串匹配取名最短者（宽泛口语名如「集合」
    落到章节级知识点，AI 出题兜底）。零命中才返回空候选（调用方澄清，
    不编造编码——4B 小模型常见幻觉是拿口语名当编码）。
    返回 {"code": str} 或 {"candidates": []}。
    """
    from sqlalchemy import select as _select

    from app.models.knowledge_point import KnowledgePoint

    raw = (raw or "").strip()
    if not raw:
        return {"candidates": []}
    exact = (await db.execute(
        _select(KnowledgePoint).where(
            (KnowledgePoint.code == raw) | (KnowledgePoint.name == raw)
        ).limit(2)
    )).scalars().all()
    if len(exact) == 1:
        return {"code": exact[0].code}
    like = f"%{raw}%"
    rows = (await db.execute(
        _select(KnowledgePoint).where(
            KnowledgePoint.name.like(like) | KnowledgePoint.code.like(like)
        ).order_by(KnowledgePoint.code).limit(6)
    )).scalars().all()
    if rows:
        # 多命中取名最短（章节级/概括级知识点）；单命中即唯一
        best = min(rows, key=lambda r: len(r.name or ""))
        return {"code": best.code}
    return {"candidates": []}


async def _h_practice_generate(context: ToolExecutionContext, inp: dict) -> dict:
    """出题后台任务：payload {kp_code, count}（task_handlers.practice_generate 消费）。

    kp_code 支持传知识点名称（Planner/用户口语），这里解析为真实编码；
    零命中返回引导文案让用户换说法，不编造编码、不建任务。
    """
    db = context.db
    if db is None:
        raise RuntimeError("db_unavailable")
    resolved = await _resolve_kp_code(db, str(inp["kp_code"]))
    if "candidates" in resolved:
        return {
            "task_id": None,
            "kind": KIND_PRACTICE_GENERATE,
            "jump": None,
            "message": f"没有找到知识点「{inp['kp_code']}」，请换一个说法（如：集合的概念、三角函数）或在练题中心查看知识点列表",
        }
    return await _create_and_spawn(
        context,
        kind=KIND_PRACTICE_GENERATE,
        payload={"kp_code": resolved["code"], "count": inp["count"]},
        suffix=IDEM_SUFFIX_PRACTICE,
        message="已开始生成，完成后会通知你",
    )


async def _h_classroom_session(context: ToolExecutionContext, inp: dict) -> dict:
    """双师课堂后台任务：course_id 缺省不下单，返回提示性文本（工具内校验）。"""
    course_id = str(inp.get("course_id") or "").strip()
    if not course_id:
        return {
            "task_id": None,
            "kind": KIND_CLASSROOM_SESSION,
            "jump": None,
            "message": "请先在双师课堂选择或上传课程资料",
        }
    return await _create_and_spawn(
        context,
        kind=KIND_CLASSROOM_SESSION,
        payload={"course_id": course_id, "topic": (inp.get("topic") or "").strip() or None},
        suffix=IDEM_SUFFIX_CLASSROOM,
        message="已开始准备课堂，完成后会通知你",
    )


async def _h_socratic_autosolve(context: ToolExecutionContext, inp: dict) -> dict:
    """引导解题后台任务：question_text / file_id 至少一项，缺参返回提示不建任务。"""
    question_text = str(inp.get("question_text") or "").strip()
    file_id = str(inp.get("file_id") or "").strip()
    if not question_text and not file_id:
        return {
            "task_id": None,
            "kind": KIND_SOCRATIC_AUTOSOLVE,
            "jump": None,
            "message": "请把题目发给我，或上传题目图片",
        }
    payload: dict = {}
    if question_text:
        payload["question_text"] = question_text
    if file_id:
        payload["file_id"] = file_id
    return await _create_and_spawn(
        context,
        kind=KIND_SOCRATIC_AUTOSOLVE,
        payload=payload,
        suffix=IDEM_SUFFIX_SOCRATIC,
        message="已开始解题，完成后会通知你",
    )


# ==================== 定义与工厂 ====================


def _task_def(
    name: str, desc: str, handler, input_model: type[BaseModel]
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        version="1.0.0",
        description=desc,
        input_model=input_model,
        output_model=StudentTaskOut,
        risk=ToolRisk.LEARNING_ACTION,
        allowed_roles=frozenset({ActorRole.STUDENT}),
        allowed_scenes=frozenset({STUDENT_CHAT_SCENE}),
        timeout_s=20.0,
        idempotency_required=False,
        handler=handler,
    )


def register_student_task_tools(registry: ToolRegistry) -> None:
    """显式注册 3 个学生后台任务工具到指定 registry（合并进 M2 注册表）。

    description 会在 Planner 提示词里被截断到 80 字符（_TOOL_DESC_LIMIT），
    因此「何时使用 + 参数含义 + 缺参先澄清」全部前置。
    """
    registry.register(
        _task_def(
            "student.practice.generate",
            "代学生建后台出题任务：用户要练/出某知识点的题时用；"
            "kp_code=知识点编码，count=题量5~30缺省5；参数不全先向用户澄清再调用",
            _h_practice_generate,
            PracticeGenerateIn,
        )
    )
    registry.register(
        _task_def(
            "student.classroom.session",
            "代学生建双师课堂后台任务：要基于课程资料开课时用；"
            "course_id=已选课程资料，topic=主题可选；缺 course_id 返回提示不建任务",
            _h_classroom_session,
            ClassroomSessionIn,
        )
    )
    registry.register(
        _task_def(
            "student.socratic.autosolve",
            "代学生建引导解题后台任务：用户发来整道题目求讲解时用；"
            "question_text=题目文本，file_id=题目图片文件，至少一项；建完立即返回不等结果",
            _h_socratic_autosolve,
            SocraticAutosolveIn,
        )
    )


def build_student_task_registry() -> ToolRegistry:
    """M2 注册表 + 3 个学生任务工具（student.chat 场景专用工厂）。"""
    from app.butler.registry import build_m2_registry

    reg = build_m2_registry()
    register_student_task_tools(reg)
    return reg
