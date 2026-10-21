"""对话 → Butler 内核桥接（stu-r2 管线通电轨）

修复「规划→执行断链」与「通电率低」的收口点：

机制本体（诊断已核实）：主对话链 DialogView→/api/agent/chat（SSE）→ kernel/router
把数据类意图送进 chat 技能 → _butler_lookup 直接 select ErrorRecord 拼文本——
流量不进内核、agent_runs 账本为 0、数据绕过工具层。

本桥接（对齐 Anthropic tool_choice 思路，编排层硬规则而非提示词）：
1. 【确定性计划】data_intent_of() 命中学情/记忆数据意图 → build_fixed_plan() 构造
   固定 ActionPlan（绕过 LLM planner）→ ButlerRuntime.run(fixed_plan=...)；
   fixed_plan 仍走 PolicyGate 十步校验与 Registry 输入校验，工具 handler 从
   context.request.actor.user_id 取身份——LLM 无参数可编。
2. 【账本即证据】数据意图 run 必然写 AgentRun + 5×AgentStep + ToolInvocation
   （runtime 既有记账，独立 session commit）。
3. 【结构化数据卡】composer 把 ToolResult.data 组装为前端同构卡
   （type='fsrs_retention' | 'mastery_bkt'），经既有 card 事件管线（agent_router
   落 envelope.blocks → messageModel 归约 → 历史回放）——agent_router/useChat/sse
   零改动。
4. 【fail-safe】桥接任何一步异常 → 调用方回退旧 _butler_lookup 直查行为；
   受 settings.butler_v2_enabled 门控（测试环境 conftest 钉 false，行为不变）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import structlog

from app.butler.contracts import (
    ActionPlan,
    ActorContext,
    ActorRole,
    ButlerRequest,
    PlannedAction,
)
from app.butler.learning_viz_tools import TOOL_FSRS_RETENTION, TOOL_MASTERY_TREND
from app.config import settings

logger = structlog.get_logger(__name__)

#: 记忆/FSRS 意图（『看看我的记忆状态/哪些快忘了』→ fsrs_retention 卡）
_MEMORY_INTENT_RE = re.compile(
    r"记忆状态|记忆曲线|记忆情况|记忆等级|记忆保持|保持率|遗忘曲线|复习曲线|"
    r"快忘|快要忘|忘了哪|哪些该复习|该复习哪些|fsrs|FSRS"
)

#: 学情/掌握度意图（『分析一下我的薄弱点/我的学情』→ mastery_bkt 卡）
_MASTERY_INTENT_RE = re.compile(
    r"学情|薄弱|短板|知识漏洞|漏洞|掌握度|掌握情况|掌握得?怎?么样|正确率|得分率|"
    r"学习情况|学习状态|学习数据|学习报告|分析我的学习"
)

_SCENE = "student.chat"


def data_intent_of(message: str) -> str | None:
    """识别数据类意图 → 卡片类型；纯聊天/视频/跳转/未开启 v2 → None（不受影响）。

    门控与护栏顺序（fail-safe）：
    - settings.butler_v2_enabled=false → None（走旧 _butler_lookup，既有测试零影响）；
    - 跳转动词（打开/跳转…）→ None（走 action 事件）；
    - 视频意图 → None（video_cards 链路优先，互斥护栏 F4 同源）。
    """
    if not settings.butler_v2_enabled:
        return None
    q = (message or "").strip()
    if not q:
        return None
    from app.skills.chat.main import ChatSkill, _VIDEO_INTENT_RE

    if any(v in q for v in ChatSkill._BUTLER_ACTION_VERBS):
        return None
    if _VIDEO_INTENT_RE.search(q):
        return None
    if _MEMORY_INTENT_RE.search(q):
        return "fsrs_retention"
    if _MASTERY_INTENT_RE.search(q):
        return "mastery_bkt"
    return None


def build_fixed_plan(intent: str, message: str) -> ActionPlan:
    """数据意图 → 确定性 ActionPlan（不依赖 LLM；仍过 PolicyGate/Registry 校验）。"""
    q = (message or "").strip()
    if intent == "fsrs_retention":
        # 「哪些快忘了/该复习哪些」→ 只看到期；泛问「记忆状态」→ 全量按保持率升序
        due_only = bool(re.search(r"快忘|到期|该复习|危险|紧急", q))
        return ActionPlan(
            intent="fsrs_retention",
            goal="已通过 AI 管家内核查询你的错题记忆状态（FSRS 算法实时计算）",
            actions=[
                PlannedAction(
                    tool_name=TOOL_FSRS_RETENTION,
                    arguments={"limit": 3, "due_only": due_only},
                    reason="记忆/复习数据查询：对真实错题记录现算保持率曲线",
                )
            ],
            response_mode="cards",
            needs_web_search=False,
        )
    if intent == "mastery_bkt":
        return ActionPlan(
            intent="mastery_bkt",
            goal="已通过 AI 管家内核计算你的学情概览（BKT 掌握度 + 近 14 天真实快照）",
            actions=[
                PlannedAction(
                    tool_name=TOOL_MASTERY_TREND,
                    arguments={"days": 14, "limit": 6},
                    reason="学情分析数据查询：mastery_records/snapshots 真实取数",
                )
            ],
            response_mode="cards",
            needs_web_search=False,
        )
    raise ValueError(f"unknown data intent: {intent}")


# ==================== student.chat 共享 Runtime 工厂 ====================

#: 学生 planner 提示词（自 butler_router 迁移至此，floating-butler 与桥接共用一份）
STUDENT_PLANNER_PROMPT = (
    "你是高中数学学生端 AI 管家。只输出一个 JSON 对象，禁止输出任何解释、markdown 栅栏或多余字符。"
    "你可以代用户创建练习出题/双师课堂/引导解题后台任务（student.* 工具），"
    "一次只创建用户明确要求的一个任务（禁止批量创建多个任务）；"
    "任务参数不全时先向用户澄清（actions 留空，response_mode 用 direct），不要编造参数；"
    "student.practice.generate 的 kp_code 支持直接传用户提到的知识点名称（如「集合」「圆锥曲线」，工具会自动解析为编码），"
    "count 是题量（5~30，用户没说就用 5）；其余动作只能选择当前场景列出的工具。"
    '输出结构：{"intent":str,"goal":str,"actions":[{"tool_name":str,'
    '"arguments":{},"reason":str}],"response_mode":"direct"|"cards"|'
    '"socratic"|"degraded","needs_web_search":false}。'
    '示例：{"intent":"practice","goal":"出5道集合练习","actions":[{"tool_name":"student.practice.generate",'
    '"arguments":{"kp_code":"集合","count":5},"reason":"用户要出集合练习"}],"response_mode":"cards","needs_web_search":false}'
)


async def build_student_chat_runtime(user_id, db):
    """student.chat 场景共享 Runtime（butler_router 与 chat_bridge 共用同一工厂）。

    注册表 = M2 统一注册表 + 3 个学生后台任务工具 + 2 个学情可视化只读工具；
    PolicyGate 放行 READ + LEARNING_ACTION（无 WRITE/EXTERNAL 副作用）。
    """
    return await _build_runtime(user_id, db)


async def _build_runtime(user_id, db):
    from app.butler.contracts import ButlerBudget, ToolRisk
    from app.butler.executor import ButlerExecutor
    from app.butler.model_adapter import ButlerModelAdapter, build_planner
    from app.butler.policy import PolicyGate
    from app.butler.registry import ToolRegistry
    from app.butler.runtime import ButlerRuntime
    from app.butler.tools import register_student_chat_tools
    from app.providers import router as provider_router

    registry = ToolRegistry()
    register_student_chat_tools(registry)
    policy = PolicyGate(
        registry,
        allowed_risks=frozenset({ToolRisk.READ, ToolRisk.LEARNING_ACTION}),
    )
    budget = ButlerBudget()
    model_router = await provider_router.get_model_router_for_user(str(user_id), db)
    adapter = ButlerModelAdapter(model_router, budget=budget)
    planner = build_planner(
        adapter,
        registry,
        budget=budget,
        system_prompt=STUDENT_PLANNER_PROMPT,
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


# ==================== 对话技能桥接（SSE card 事件） ====================

#: 但段状态事件 stage 名（前端 MessageBubble STAGE_ZH 映射用）
STAGE_BUTLER_QUERY = "butler_query"
STAGE_BUTLER_DEGRADED = "butler_degraded"


def client_request_id_for(request_id: str) -> str:
    """桥接 run 的幂等键（≤128 对齐 agent_runs.client_request_id 列宽）。"""
    return f"chatbridge-{request_id}"[:128]


def _cards_from_envelope(envelope) -> list[dict[str, Any]]:
    return [
        b.get("data")
        for b in (envelope.blocks or [])
        if isinstance(b, dict) and b.get("type") == "card" and isinstance(b.get("data"), dict)
    ]


def system_message_for_cards(cards: list[dict], *, degraded: bool = False) -> str:
    """把卡片摘要注入为 system 段（与视频卡先例同构）：数字只能来自卡片，禁编造。"""
    if not cards:
        return ""
    lines = [
        "【AI 管家内核查询】（本轮已随消息插入上方数据卡片，卡片数字全部由内核工具"
        "对你的真实学习记录实时计算。回复纪律：正文 1-3 句，直接解读卡片中的数字与结论；"
        "禁止编造任何卡片之外的数字、知识点或曲线；卡片为空时如实说明还没有数据，"
        "并引导先去练题/收录错题；不要把卡片信息重复罗列成清单。）"
    ]
    for card in cards:
        msg = card.get("message")
        if msg:
            lines.append(f"- {msg}")
    if degraded:
        lines.append("（注意：本次查询经降级通道，结果可能不完整，请向学生如实说明。）")
    return "\n".join(lines)


async def run_data_intent_events(
    *,
    question: str,
    user_id,
    request_id: str,
    messages: list[dict],
) -> AsyncIterator[dict]:
    """chat 技能内调用：数据意图 → 内核真实调工具 → yield card 事件 + 注入 system 段。

    - 独立 session 跑内核并 commit：AgentRun/AgentStep/ToolInvocation 落账本，
      与请求主 session 隔离（不干扰 SSE 主链路事务）；
    - 任何异常向上抛出，由调用方捕获后回退旧 _butler_lookup 直查（fail-safe）。
    """
    intent = data_intent_of(question)
    if intent is None:
        return
    yield {
        "type": "status",
        "data": {"stage": STAGE_BUTLER_QUERY, "text": "正在通过 AI 管家内核查询你的学习数据…"},
    }

    from app.models.database import async_session_factory

    envelope = None
    async with async_session_factory() as db:
        runtime = await build_student_chat_runtime(user_id, db)
        butler_request = ButlerRequest(
            actor=ActorContext(
                user_id=user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id)),
                role=ActorRole.STUDENT,
            ),
            message=question,
            scene=_SCENE,
            conversation_id=None,
            client_request_id=client_request_id_for(request_id),
        )
        envelope = await runtime.run(
            butler_request, db, fixed_plan=build_fixed_plan(intent, question)
        )
        await db.commit()

    cards = _cards_from_envelope(envelope)
    if envelope is not None and envelope.degraded:
        yield {
            "type": "status",
            "data": {
                "stage": STAGE_BUTLER_DEGRADED,
                "text": "AI 分析通道波动，以下为降级结果",
            },
        }
    for card in cards:
        yield {"type": "card", "data": card}

    text = system_message_for_cards(cards, degraded=bool(envelope.degraded if envelope else True))
    if text:
        messages.append({"role": "system", "content": text})
