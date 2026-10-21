"""socratic_solver skill — 引导式解题（solver-then-guide，迭代02 v2）

架构：
1. draft：先查题库（RAG best-effort），命中的标准解析作为已验证底稿注入 solver（SRS F1 第一层）。
2. solver：LLM 两次独立解题（self-consistency），支持 TIR 工具集成推理——
   算不动的步骤输出代码块，SymPy 沙箱执行后回填续解（CRITIC 式外部信号纠错）；
   终答等价比对，不一致 → 第三次仲裁取多数；仍无一致 → 重试一轮 → 诚实降级。
3. verify：步骤级沙箱独立复算（一次 LLM 生成验证脚本 + 沙箱执行）；
   失败步骤修复一次，仍失败 → 诚实降级（绝不强行引导）。
4. guide：参考解只作为隐藏上下文落 tutor_sessions.plan，教学决策（提示阶梯
   Point→Teach→Bottom-out、判答推进、揭示答案）全部在本文件代码里，
   LLM 只生成当前级别的提示文本；学生可见文本真流式逐句下发。
5. 防泄题：所有发给学生的引导/提示文本逐句经 find_leak 程序检查（滑窗），
   命中 → 严指令重生成一次 → 仍命中 → 零泄露兜底模板。
6. 情绪：同一步连续答错 ≥2 次注入安抚降负指引（SRS §3.1.7）；难题自动拆小提问。

SSE 事件纪律：yield status/token/card/figure/_result_meta/error，禁 yield meta/citation/badge/done。
_result_meta 每次 run 只在最后发一次（主链路是覆盖式处理）。
内部事件 _solve_done/_once/_thinking 仅供生成器间传值，绝不流出本模块。

F13 可视化讲解：solver 成稿后按主题门控跑 figure planner（结构化图形指令 → 参数校验），
图形随 plan.steps 持久化；guide 阶段按场景限帧发射 figure 事件（引导/提示/纠错只给
构图帧防泄题，答对确认与总结给全帧），图形渲染失败静默丢弃绝不阻断讲解流。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncGenerator
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.models.tutor_session import (
    STATUS_ABANDONED,
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_DEGRADED,
    STATUS_REVEALED,
    TutorSession,
)
from app.providers.sandbox import check_equivalence, run_sandbox
from app.services.figure_renderer import derive_figure_frames, render_figure_frames
from app.skills.base import SkillContext, SkillExecutor
from app.skills.socratic_solver import prompts
from app.skills.socratic_solver.figures import (
    merge_figures_into_plan,
    parse_figure_plan,
    should_plan_figures,
)
from app.skills.socratic_solver.parsing import (
    extract_code_blocks,
    extract_math_expr,
    find_leak,
    parse_solver_output,
)

logger = structlog.get_logger(__name__)

MAX_HINT_LEVEL = 3  # 1 Point → 2 Teach → 3 Bottom-out（封顶）
_PARSE_RETRIES = 2  # solver 输出不合契约时的反馈重试次数
_TIR_MAX_ROUNDS = 2  # 单次求解最多回填执行代码的轮数
_TIR_MAX_BLOCKS = 2  # 每轮最多执行的代码块数
_TIR_BLOCK_TIMEOUT_MS = 6000
# v1.3 求解 token 预算（实测 2026-08-12：mimo-v2.5-pro 思考模型解 17 分三问证明题，
# 思考 6000 被吃光正文为零、直解 3000 也 finish_reason=length 截断——两档都不够，
# 重试又每次从零重想，5 分 17 秒 4 次调用零产出。改为：单档预算 + 截断续写）：
_SOLVE_THINKING_BUDGET = 6000  # 思考开：思考 2-3k + 正文余量
_SOLVE_DIRECT_BUDGET = 4000  # 思考关：单次正文预算（原 3000，长证明题不够）
_SOLVE_CONTINUATION_BUDGET = 4000  # 每次续写预算
_SOLVE_MAX_CONTINUATIONS = 3  # 截断续写上限（有效正文上限 ≈ 4k + 3×4k = 16k）
_MAX_RECENT_ATTEMPTS = 6  # plan.recent_attempts 环形缓冲上限
_SENTENCE_FLUSH_CHARS = 100  # 流式下发：无句号缓冲超过该长度强制 flush

_VERDICTS = {"correct", "partial", "wrong", "clarification", "new_problem", "off_topic"}
_MISCONCEPTIONS = {"concept", "formula", "calculation", "logic", "reading"}
_TEXTUAL_ANSWER_RE = re.compile(r"[一-鿿]")
_DRAFT_SOLUTION_RE = re.compile(r"(解|答|证明|步骤|解析)")
# 负向情绪措辞（迭代05 C-P2-11：受挫信号源之一，触发安抚降负）
_NEGATIVE_EMOTION_RE = re.compile(
    r"太难|好难|不会|不会做|做不出|想不到|放弃|不想|烦死|崩溃|算了|看不懂|完全没思路|一点思路都没"
)
# 短消息质疑/追问的确定性识别（v3.1 截图事故防复发："难道不对吗"曾被判 off_topic
# 后复读固定话术）。仅当消息很短且命中质疑/请求解释措辞时短路为 clarification；
# 带数学式的作答仍优先走 sympy 快速通道与 LLM 判答。
_CHALLENGE_RE = re.compile(
    r"难道|不对吗|不对吧|不是吗|真的吗|凭什么|为什么错|错在哪|哪里错|"
    r"我觉得没错|我没有错|我没错|我没说错|没错吧|这没错|为什么呀|为什么啊|"
    r"再讲讲|再讲一遍|再说一遍|没听懂|听不懂|什么意思"
)
_CHALLENGE_MAX_LEN = 40
# 句读切分：中文句末标点或换行
_SENTENCE_END_RE = re.compile(r".+?[。！？；\n]", re.DOTALL)


def _pop_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """从缓冲切出完整句子（标点保留在句尾），返回 (句子列表, 剩余缓冲)"""
    sentences: list[str] = []
    pos = 0
    for m in _SENTENCE_END_RE.finditer(buffer):
        sentences.append(m.group(0))
        pos = m.end()
    return sentences, buffer[pos:]


class SocraticSolverExecutor(SkillExecutor):
    """引导式解题 skill executor"""

    manifest = {
        "id": "socratic_solver",
        "name": "引导式解题",
        "description": (
            "引导式解题（苏格拉底式教学）。当用户发来一道数学题（含题目文本或公式）、"
            "请求求解/证明/讲解某道题、问某题怎么做、要解题思路或步骤、"
            "说“教我这道题”“带我做这道题”时使用。"
            "解题过程中的学生作答、要提示、要答案等多轮消息也在此技能内处理。"
        ),
        "version": "3.0.0",
        "roles": ["student"],
        "presentation": "card",
        "examples_positive": [
            "帮我解这道题",
            "这题怎么做",
            "x^2-2x-3=0 怎么解",
            "证明一下这个结论",
            "给我讲讲这道题",
        ],
        "examples_negative": ["给我出一套卷子", "帮我写作文"],
        "fallback": "chat",
    }

    # ========== 入口 ==========

    async def run(self, params: dict[str, Any], ctx: SkillContext) -> AsyncGenerator[dict, None]:
        question = (params.get("question") or params.get("message") or "").strip()
        tutor_action = params.get("tutor_action")
        tutor_step_no = params.get("tutor_step_no")
        regenerate = bool(params.get("_regenerate"))
        meta: dict[str, Any] = {"skill": "socratic_solver"}

        # 会话解析优先级（M2.2 regenerate 修复）：
        # ① 调用方显式指定的 tutor_session_id（regenerate 从原消息 envelope 恢复）；
        # ② 动作消息粘连最近会话（含已结束：结束后点「直接看答案」应幂等重发完整解答，
        #    而不是掉进新题入口幻觉出题）；
        # ③ 常规 active 粘连（学生作答路径）。
        session = None
        if params.get("tutor_session_id"):
            session = await self._get_session_by_id(ctx, params["tutor_session_id"])
        if session is None:
            if tutor_action:
                session = await self._get_latest_session(ctx, include_ended=True)
            else:
                session = await self._get_active_session(ctx)

        if tutor_action:
            meta["tutor_action"] = tutor_action
        if session is not None:
            if regenerate:
                async for event in self._regenerate_followup(
                    session, question, tutor_action, ctx, meta
                ):
                    yield event
            else:
                async for event in self._followup(
                    session, question, tutor_action, ctx, meta, tutor_step_no=tutor_step_no
                ):
                    yield event
        elif tutor_action:
            # 有动作无会话：诚实兜底，绝不幻觉出题
            yield {"type": "token", "data": {"text": prompts.NO_SESSION_ACTION_TEXT}}
            meta["confidence"] = 0.3
        elif not question:
            yield {
                "type": "token",
                "data": {"text": "请把你想解的题目发给我，我会一步一步带你做出来。"},
            }
            meta["confidence"] = 0.3
        else:
            # v1.11 上下文种子回落：消息无题干特征但指代既有题目（"基于刚才的题讲解"），
            # 取本会话最近题卡/引导题干作种子讲题；无种子诚实兜底，绝不幻觉编题。
            from app.skills.smart_quiz.main import (
                USER_QUESTION_DETECT_RE,
                recent_seed_question,
            )

            if not re.search(USER_QUESTION_DETECT_RE, question) and re.search(
                r"刚才|刚刚|这道|这题|那道|原题|上述|该题|错题", question
            ):
                seed = await recent_seed_question(
                    getattr(ctx, "db", None), getattr(ctx, "conversation_id", None)
                )
                if seed:
                    question = f"{seed}\n\n{question}"
                else:
                    yield {"type": "token", "data": {"text": prompts.CONTEXT_REF_NO_SEED_TEXT}}
                    meta["confidence"] = 0.3
                    yield {"type": "_result_meta", "data": meta}
                    return
            _image_file_ids = list(params.get("image_file_ids") or [])
            _image_sha = (
                await self._original_image_sha(_image_file_ids, ctx) if _image_file_ids else None
            )
            async for event in self._new_problem(
                # 迭代15 B7a 延迟治理：默认快速模式（实测 thinking 开 37s→关 ~10s），
                # 前端「思考模式」开关显式传 True 时才深度推理
                question,
                ctx,
                meta,
                thinking=params.get("thinking", False),
                image_file_ids=_image_file_ids,
                image_sha=_image_sha,
            ):
                yield event

        yield {"type": "_result_meta", "data": meta}

    # ========== 新题入口 ==========

    async def _new_problem(
        self,
        question: str,
        ctx: SkillContext,
        meta: dict[str, Any],
        *,
        thinking: bool = False,
        image_file_ids: list[str] | None = None,
        image_sha: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        # ① 题库底稿（RAG 可用时；SRS F1 第一层供给：命中即零幻觉底稿）
        draft = None
        if ctx.rag is not None:
            yield {
                "type": "status",
                "data": {"stage": "kb_search", "text": "正在检索题库，看有没有匹配的标准解析…"},
            }
            draft = await self._retrieve_draft(question, ctx)

        # ② 求解 + 验证（细粒度状态事件在生成器内部发出）
        # 思考模式开关（迭代15 B7a）：默认关闭（快速模式，实测 37s→~10s）；
        # 前端显式传 thinking=True 时开启（深度推理 + 思考面板）
        plan: dict | None = None
        solve_calls = 0
        degrade_reason = "no_consensus"
        async for ev in self._solve_verified(
            question, draft, ctx, thinking=thinking, image_file_ids=image_file_ids or []
        ):
            if ev["type"] == "_solve_done":
                plan = ev["data"]["plan"]
                solve_calls = ev["data"]["calls"]
                degrade_reason = ev["data"]["reason"] or "no_consensus"
            else:
                yield ev

        if plan is None:
            # 诚实降级：验证不过绝不强行引导
            session = TutorSession(
                user_id=ctx.user_id,
                conversation_id=ctx.conversation_id,
                question_text=question,
                status=STATUS_DEGRADED,
                plan={
                    "steps": [],
                    "final_answer": None,
                    "verified": False,
                    "solve_attempts": solve_calls,
                    "consistency": False,
                    "degrade_reason": degrade_reason,
                    "leak_blocked": 0,
                },
                completed_at=datetime.now(UTC),
            )
            ctx.db.add(session)
            await ctx.db.flush()
            # 降级会话同样要立刻持久化（断流保护；degraded 会话供动作消息幂等粘连）
            await ctx.db.commit()
            logger.info(
                "socratic.degraded",
                session_id=str(session.id),
                reason=degrade_reason,
                solve_calls=solve_calls,
            )
            yield {
                "type": "token",
                "data": {"text": prompts.DEGRADED_TEXTS.get(degrade_reason, prompts.DEGRADED_TEXT)},
            }
            yield {
                "type": "card",
                "data": {
                    "card_type": "socratic_degraded",
                    "session_id": str(session.id),
                    "reason": degrade_reason,
                },
            }
            meta.update(
                {
                    "degraded": True,
                    "degrade_reason": degrade_reason,
                    "confidence": 0.3,
                    "session_id": str(session.id),
                }
            )
            return

        session = TutorSession(
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            question_text=question,
            status=STATUS_ACTIVE,
            plan=plan,
            current_step=1,
            hint_level=0,
            attempts_on_step=0,
            hint_counts={"point": 0, "teach": 0, "bottom_out": 0},
            answer_requests=0,
            awaiting_attempt=False,
        )
        if image_sha:
            # N3 图形合同：原题图 SHA-256 绑进会话 plan，figure 事件随之携带，
            # 前端凭此显示"已核对原题图"徽标（重解析换图后旧图自动失效）
            plan["problem_source_sha256"] = image_sha
        ctx.db.add(session)
        await ctx.db.flush()
        # 断流保护：客户端提前断开 SSE（停止生成/刷新页面）会触发请求级事务回滚，
        # 未提交的 TutorSession 随之丢失 → 学生下一条作答掉粘连被当成新题重新规划
        # （实测事故：答错一步即重建 session + plan 失败降级）。此处显式提交，
        # 让会话在 plan 就绪瞬间即持久化，后续判答走 _followup 尾部的统一提交。
        await ctx.db.commit()

        steps_count = len(plan["steps"])
        difficulty = plan.get("difficulty", "medium")
        yield {
            "type": "card",
            "data": {
                "card_type": "socratic_start",
                "session_id": str(session.id),
                "steps_count": steps_count,
                "verified": plan["verified"],
                "difficulty": difficulty,
                "draft_source": plan.get("draft_source", "llm"),
            },
        }
        meta.update(
            {
                "confidence": 0.9,
                "session_id": str(session.id),
                "steps_count": steps_count,
                "verified": plan["verified"],
                "difficulty": difficulty,
                "draft_source": plan.get("draft_source", "llm"),
                "leak_blocked": 0,
                "figures": int(plan.get("figures_planned") or 0),
            }
        )

        # 首步引导：诊断式开场（Point 级约束，一次一问）
        yield {"type": "status", "data": {"stage": "guiding", "text": "正在准备引导提问…"}}
        step = plan["steps"][0]
        profile = await self._profile_text(ctx)
        async for event in self._stream_student_text(
            ctx,
            session,
            messages=[
                {
                    "role": "user",
                    "content": self._guide_content(
                        prompts.GUIDE_OPENING.format(
                            question=question,
                            profile=profile,
                            assertion=step["assertion"],
                            reason=step["reason"],
                            leak_rule=prompts.LEAK_RULE,
                        ),
                        session,
                        1,
                    ),
                }
            ],
            fallback=prompts.FALLBACK_GUIDE_TEXT,
            scene="socratic_guide",
            extra_note=prompts.GUIDE_HARD_NOTE if difficulty == "hard" else "",
        ):
            yield event
        # F13：第 1 步配图（仅构图帧——关键点标注=答案信息，引导阶段不给）
        async for event in self._figure_events(session, 1, frame_limit=1):
            yield event
        # 每步思路选项：引导文本已呈现后再下发，学生可直接点选而不必空手打字。
        # best-effort——生成失败/不足 2 项时不下发，行为与改造前完全一致。
        choices = await self._step_choices(question, step, ctx)
        if choices:
            yield {
                "type": "card",
                "data": {
                    "card_type": "socratic_step_choice",
                    "session_id": str(session.id),
                    "step_no": 1,
                    "options": choices,
                },
            }

    # ========== 多轮跟进状态机 ==========

    async def _followup(
        self,
        session: TutorSession,
        message: str,
        tutor_action: str | None,
        ctx: SkillContext,
        meta: dict[str, Any],
        *,
        _record: bool = True,
        tutor_step_no: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        plan = session.plan or {}
        meta.update(
            {
                "session_id": str(session.id),
                "steps_count": len(plan.get("steps") or []),
                "verified": bool(plan.get("verified")),
                "difficulty": plan.get("difficulty", "medium"),
            }
        )

        # 学生在已完成/已揭示的引导卡上回点历史选项：精确回到被点击的步骤继续，
        # 不能拿选项去开一道新题，也不能用最终步骤误判。
        if tutor_action == "attempt" and tutor_step_no and session.status != STATUS_ACTIVE:
            steps = plan.get("steps") or []
            if steps:
                session.status = STATUS_ACTIVE
                session.completed_at = None
                session.current_step = max(1, min(int(tutor_step_no), len(steps)))
                meta["resumed_step"] = session.current_step
        elif (
            tutor_action == "attempt"
            and tutor_step_no
            and session.status == STATUS_ACTIVE
            and int(tutor_step_no) != session.current_step
        ):
            # 活跃会话里点了历史步骤的选项：不误判当前步骤，重发当前步骤选项卡
            steps = plan.get("steps") or []
            if steps and 1 <= int(tutor_step_no) <= len(steps):
                step = steps[session.current_step - 1]
                choices = await self._step_choices(session.question_text, step, ctx)
                if choices:
                    yield {"type": "token", "data": {"text": f"我们先聚焦当前第 {session.current_step} 步。这一步可以从哪儿入手？"}}
                    yield {
                        "type": "card",
                        "data": {
                            "card_type": "socratic_step_choice",
                            "session_id": str(session.id),
                            "step": session.current_step,
                            "options": choices,
                        },
                    }
                    meta["refocused_step"] = session.current_step
                    return

        if tutor_action in ("hint", "answer", "answer_confirm") and session.status != STATUS_ACTIVE:
            # 已结束会话（revealed/completed）上的动作：幂等重发完整解答。
            # 纯读路径——不进入提示/确认状态机、不改 status/completed_at
            # （completed 会话不被改写成 revealed，outcome 与用时不被污染）；
            # 这是「结束后又点直接看答案」幻觉出题的修复分支（M2.2）。
            outcome = (
                "revealed"
                if session.status == STATUS_REVEALED
                else ("independent" if self._is_independent(session) else "guided")
            )
            async for event in self._stream_summary(session, outcome=outcome, ctx=ctx):
                yield event
            yield {"type": "card", "data": self._complete_card(session, outcome=outcome)}
            meta.update(
                {
                    "outcome": outcome,
                    "answer_requests": session.answer_requests,
                    "reemitted": True,
                }
            )
        elif tutor_action == "hint":
            async for event in self._on_hint(session, ctx, meta):
                yield event
        elif tutor_action == "answer":
            # _record=False（regenerate）：只重放确认话术，不重复计数/写埋点事件
            if _record:
                session.answer_requests += 1
                # answer_request 事件落 events 表（ADR-033 / SSOT §8.7：计入提示依赖度，迭代05 B-P1-16）
                try:
                    from app.models.event import Event

                    ctx.db.add(
                        Event(
                            user_id=uuid.UUID(ctx.user_id),
                            event="answer_request",
                            props={
                                "session_id": str(session.id),
                                "count": session.answer_requests,
                                "current_step": session.current_step,
                            },
                        )
                    )
                except Exception as e:  # 埋点失败不阻断主链路
                    logger.warning("socratic.answer_request_event_failed", error=str(e)[:100])
                await ctx.db.flush()
            yield {"type": "token", "data": {"text": prompts.CONFIRM_ANSWER_TEXT}}
            yield {
                "type": "card",
                "data": {"card_type": "socratic_confirm_answer", "session_id": str(session.id)},
            }
            meta["answer_requests"] = session.answer_requests
        elif tutor_action == "answer_confirm":
            async for event in self._on_reveal(session, ctx, meta):
                yield event
        else:
            async for event in self._on_attempt(session, message, ctx, meta, _record=_record):
                yield event

        meta.update(
            {
                "current_step": session.current_step,
                "hint_level": session.hint_level,
                "status": session.status,
                "leak_blocked": int(plan.get("leak_blocked") or 0),
            }
        )
        # 判答/提示/揭示后的状态推进持久化（断流保护，语义同 _new_problem 的提交点）
        try:
            await ctx.db.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("socratic.followup_commit_failed", error=str(e)[:150])

    async def _regenerate_followup(
        self,
        session: TutorSession,
        message: str,
        tutor_action: str | None,
        ctx: SkillContext,
        meta: dict[str, Any],
    ) -> AsyncGenerator[dict, None]:
        """重新生成语义：按原参数装配重跑状态机，但会话状态快照后恢复——
        重新生成是「同一输入的另一种回答」，不得重复计数（提示级别/答案请求）、
        不得推进状态机（步进/完成时间）、不得新建或放弃会话（M2.2 防状态污染）"""
        snapshot = {
            "status": session.status,
            "current_step": session.current_step,
            "hint_level": session.hint_level,
            "awaiting_attempt": session.awaiting_attempt,
            "attempts_on_step": session.attempts_on_step,
            "answer_requests": session.answer_requests,
            "hint_counts": deepcopy(session.hint_counts),
            "plan": deepcopy(session.plan),
            "completed_at": session.completed_at,
        }
        try:
            async for event in self._followup(
                session, message, tutor_action, ctx, meta, _record=False
            ):
                yield event
        finally:
            for field, value in snapshot.items():
                setattr(session, field, value)
            await ctx.db.flush()

    async def _on_hint(
        self, session: TutorSession, ctx: SkillContext, meta: dict[str, Any]
    ) -> AsyncGenerator[dict, None]:
        # 防提示滥用：给过提示后需先作答，再要提示只反问、不升级
        if session.awaiting_attempt:
            yield {"type": "token", "data": {"text": prompts.ASK_ATTEMPT_FIRST_TEXT}}
            meta["hint_blocked"] = True
            return

        session.hint_level = min(session.hint_level + 1, MAX_HINT_LEVEL)
        level_name = prompts.LEVEL_NAMES[session.hint_level]
        counts = dict(session.hint_counts or {})
        counts[level_name] = counts.get(level_name, 0) + 1
        session.hint_counts = counts
        session.awaiting_attempt = True
        self._bump_step_stat(session, "hints")

        step = session.plan["steps"][session.current_step - 1]
        template = {1: prompts.HINT_POINT, 2: prompts.HINT_TEACH, 3: prompts.HINT_BOTTOM}[
            session.hint_level
        ]
        async for event in self._stream_student_text(
            ctx,
            session,
            messages=[
                {
                    "role": "user",
                    "content": self._guide_content(
                        template.format(
                            question=session.question_text,
                            step_no=session.current_step,
                            assertion=step["assertion"],
                            reason=step["reason"],
                            leak_rule=prompts.LEAK_RULE,
                        ),
                        session,
                        session.current_step,
                    ),
                }
            ],
            fallback=prompts.FALLBACK_GUIDE_TEXT,
            scene="socratic_hint",
            extra_note=(
                prompts.GUIDE_HARD_NOTE if session.plan.get("difficulty") == "hard" else ""
            ),
        ):
            yield event
        # F13：本步配图（仅构图帧，三档提示均不给答案性标注）
        async for event in self._figure_events(
            session, session.current_step, frame_limit=1
        ):
            yield event
        await ctx.db.flush()
        yield {
            "type": "card",
            "data": {
                "card_type": "socratic_hint",
                "session_id": str(session.id),
                "level": session.hint_level,
                "level_name": level_name,
            },
        }

    async def _on_reveal(
        self, session: TutorSession, ctx: SkillContext, meta: dict[str, Any]
    ) -> AsyncGenerator[dict, None]:
        session.status = STATUS_REVEALED
        session.completed_at = datetime.now(UTC)
        await ctx.db.flush()
        async for event in self._stream_summary(session, outcome="revealed", ctx=ctx):
            yield event
        yield {
            "type": "card",
            "data": self._complete_card(session, outcome="revealed"),
        }
        meta.update({"outcome": "revealed", "answer_requests": session.answer_requests})

    async def _on_attempt(
        self,
        session: TutorSession,
        message: str,
        ctx: SkillContext,
        meta: dict[str, Any],
        *,
        _record: bool = True,
    ) -> AsyncGenerator[dict, None]:
        if not message:
            yield {"type": "token", "data": {"text": "说说你的想法吧，哪怕只有一点思路也行。"}}
            return

        plan = session.plan
        steps = plan.get("steps") or []
        if not steps or session.current_step > len(steps):
            yield {"type": "token", "data": {"text": "这道题我们已经走完了，要不要再换一道试试？"}}
            return
        step = steps[session.current_step - 1]

        verdict, judge = await self._judge(session, step, message, ctx)
        meta["verdict"] = verdict
        # 跑题连击计数：任何有效回应都清零（连续跑题时拉回文案逐级升级的依据）
        if verdict != "off_topic" and (session.plan or {}).get("off_topic_streak"):
            self._set_plan_key(session, "off_topic_streak", 0)
        if verdict not in {"clarification", "off_topic"}:
            self._record_attempt(session, message, verdict)
            self._bump_step_stat(session, "attempts")
        logger.info(
            "socratic.judged",
            session_id=str(session.id),
            step=session.current_step,
            verdict=verdict,
            misconception=judge.get("misconception"),
        )

        if (
            verdict == "new_problem"
            and _record
            and not self._looks_like_new_problem(message)
        ):
            # new_problem 硬闸：judge 偶发把"思路选项描述短句"误判成新题
            # （实测事故：点选"建立空间直角坐标系…"被当新题，弃 session 重规划，
            # plan 失败连锁降级）。无完整题干特征的消息一律降级为 partial 继续引导。
            logger.info(
                "socratic.new_problem_overruled",
                session_id=str(session.id),
                message=message[:60],
            )
            verdict, judge = "partial", {
                "verdict": "partial",
                "misconception": None,
                "feedback_hint": "学生选择了下一步思路，按方向可行性继续引导",
            }

        if verdict == "new_problem":
            if not _record:
                # 重新生成语义：不真放弃旧会话、不新建会话（防状态污染），
                # 诚实告知需单独发新题，不重复跑题话术（v3.1 反复读改造）
                yield {"type": "token", "data": {"text": prompts.NEW_PROBLEM_REGEN_TEXT}}
                return
            # 旧会话 abandoned，按新题入口流程处理
            session.status = STATUS_ABANDONED
            session.completed_at = datetime.now(UTC)
            await ctx.db.flush()
            yield {
                "type": "token",
                "data": {"text": "这是一道新题目，我们先把刚才的放下，来分析这道题。"},
            }
            async for event in self._new_problem(message, ctx, meta):
                yield event
            return

        if verdict == "correct":
            async for event in self._on_correct(session, steps, ctx, meta):
                yield event
        elif verdict == "partial":
            if len((message or "").strip()) >= 4:
                await self._record_attempt_error(session, message, ctx)
            session.awaiting_attempt = False
            await ctx.db.flush()
            async for event in self._stream_student_text(
                ctx,
                session,
                messages=[
                    {
                        "role": "user",
                        "content": self._guide_content(
                            prompts.PARTIAL_FOLLOWUP.format(
                                question=session.question_text,
                                step_no=session.current_step,
                                student_answer=message,
                                attempts_history=self._attempts_block(session),
                                feedback_hint=judge.get("feedback_hint") or "表述不完整",
                                assertion=step["assertion"],
                                reason=step["reason"],
                                leak_rule=prompts.LEAK_RULE,
                            ),
                            session,
                            session.current_step,
                        ),
                    }
                ],
                fallback="方向对了一半——能把你的想法再说具体一点吗？你打算对哪个式子动手？",
                scene="socratic_guide",
            ):
                yield event
            async for event in self._figure_events(
                session, session.current_step, frame_limit=1
            ):
                yield event
            # 2026-10-05 修复「选项一下有一一下没有」：
            # partial 停留在同一步（current_step 不推进，见 _on_correct:815），
            # 学生被要求"把想法再说具体一点"时若没有可点选项，只能空着打字框干瞪眼。
            # 首步（_new_problem）与答对后（_on_correct）都补发了选项，此处对齐同一契约。
            # _step_choices 自带重试 + 确定性兜底，失败静默不阻塞主链路。
            choices = await self._step_choices(session.question_text, step, ctx)
            if choices:
                yield {
                    "type": "card",
                    "data": {
                        "card_type": "socratic_step_choice",
                        "session_id": str(session.id),
                        "step_no": session.current_step,
                        "options": choices,
                    },
                }
        elif verdict == "wrong":
            async for event in self._on_wrong(session, step, message, judge, ctx, meta):
                yield event
        elif verdict == "clarification":
            async for event in self._stream_student_text(
                ctx,
                session,
                messages=[
                    {
                        "role": "user",
                        "content": self._guide_content(
                            prompts.CLARIFICATION_FOLLOWUP.format(
                                question=session.question_text,
                                step_no=session.current_step,
                                student_message=message,
                                attempts_history=self._attempts_block(session),
                                assertion=step["assertion"],
                                reason=step["reason"],
                                leak_rule=prompts.LEAK_RULE,
                            ),
                            session,
                            session.current_step,
                        ),
                    }
                ],
                fallback="你是在追问刚才的判断。先回到这一步的条件：它和你刚才说的理由能直接对应吗？",
                scene="socratic_guide",
            ):
                yield event
            # 2026-10-05 同 partial：追问澄清后同样不推进步数，补发选项避免学生空手打字。
            choices = await self._step_choices(session.question_text, step, ctx)
            if choices:
                yield {
                    "type": "card",
                    "data": {
                        "card_type": "socratic_step_choice",
                        "session_id": str(session.id),
                        "step_no": session.current_step,
                        "options": choices,
                    },
                }
        else:  # off_topic —— 人性化拉回，绝不连续重复同一句（截图事故防复发）
            streak = int((session.plan or {}).get("off_topic_streak") or 0) + 1
            self._set_plan_key(session, "off_topic_streak", streak)
            last_tutor_msg = self._last_tutor_question(session)
            text = ""
            if streak == 1:
                # 第一次跑题：LLM 生成带上下文的自然拉回（非流式短文本，便于查重）
                text = (
                    await self._chat(
                        ctx,
                        [
                            {
                                "role": "user",
                                "content": prompts.OFF_TOPIC_REANCHOR_USER.format(
                                    question=session.question_text,
                                    tutor_last_question=last_tutor_msg or "（刚提出引导问题）",
                                    student_message=message[:200],
                                    assertion=step["assertion"],
                                    reason=step["reason"],
                                    leak_rule=prompts.LEAK_RULE,
                                ),
                            }
                        ],
                        temperature=0.6,
                        max_tokens=300,
                        thinking=False,
                        scene="socratic_reanchor",
                    )
                    or ""
                ).strip()
            # 反重复闸门：生成失败/输出为空/疑似 JSON/与上一句复读 → 确定性轮换文案
            if (
                not text
                or text.startswith("{")
                or self._norm_reply(text) == self._norm_reply(last_tutor_msg)
            ):
                text = self._off_topic_variant(streak)
            yield {"type": "token", "data": {"text": text}}

        # 每轮判答后发进度卡（new_problem 已由新会话的 socratic_start 覆盖）
        if verdict != "new_problem":
            yield {"type": "card", "data": self._progress_card(session, verdict)}

    async def _on_correct(
        self,
        session: TutorSession,
        steps: list[dict],
        ctx: SkillContext,
        meta: dict[str, Any],
    ) -> AsyncGenerator[dict, None]:
        session.attempts_on_step = 0
        session.hint_level = 0  # 答对后提示级别回落
        session.awaiting_attempt = False

        if session.current_step >= len(steps):
            # 最后一步完成 → 总结
            session.status = STATUS_COMPLETED
            session.completed_at = datetime.now(UTC)
            outcome = "independent" if self._is_independent(session) else "guided"
            await ctx.db.flush()
            async for event in self._stream_summary(session, outcome=outcome, ctx=ctx):
                yield event
            yield {
                "type": "card",
                "data": self._complete_card(session, outcome=outcome),
            }
            meta["outcome"] = outcome
            return

        done_step = session.current_step
        session.current_step += 1
        await ctx.db.flush()
        next_step = steps[session.current_step - 1]
        async for event in self._stream_student_text(
            ctx,
            session,
            messages=[
                {
                    "role": "user",
                    "content": self._guide_content(
                        prompts.GUIDE_NEXT_STEP.format(
                            question=session.question_text,
                            done_step=done_step,
                            step_no=session.current_step,
                            steps_count=len(steps),
                            assertion=next_step["assertion"],
                            reason=next_step["reason"],
                            leak_rule=prompts.LEAK_RULE,
                        ),
                        session,
                        session.current_step,
                    ),
                }
            ],
            fallback=prompts.FALLBACK_GUIDE_TEXT,
            scene="socratic_guide",
            extra_note=(
                prompts.GUIDE_HARD_NOTE if session.plan.get("difficulty") == "hard" else ""
            ),
        ):
            yield event
        # F13：先展示刚完成步骤的完整图形（视觉确认，答对后不再有泄题约束），
        # 再给下一步的构图帧（引导阶段仅构图）
        async for event in self._figure_events(session, done_step, frame_limit=None):
            yield event
        async for event in self._figure_events(
            session, session.current_step, frame_limit=1
        ):
            yield event
        # 下一步的思路选项（与首步同一契约；失败静默，不下发）
        choices = await self._step_choices(session.question_text, next_step, ctx)
        if choices:
            yield {
                "type": "card",
                "data": {
                    "card_type": "socratic_step_choice",
                    "session_id": str(session.id),
                    "step_no": session.current_step,
                    "options": choices,
                },
            }

    async def _record_attempt_error(self, session: TutorSession, message: str, ctx: SkillContext) -> None:
        """om5 修复轮 D1：引导判错（wrong/partial）自动入错题本，带会话/消息溯源。

        partial 也收录：方法对结论错同样是错题；收录失败绝不阻塞引导主流程。
        """
        try:
            from app.gateway.student_router import _upsert_error_record

            await _upsert_error_record(
                ctx.db,
                uuid.UUID(ctx.user_id),
                question_text=session.question_text,
                answer_text=(message or "")[:500],
                source_channel="auto_judge",
                conversation_id=uuid.UUID(ctx.conversation_id),
                message_id=session.message_id,
                origin="socratic",
            )
            await ctx.db.flush()
        except Exception as e:
            logger.warning("socratic_error_record_failed", error=str(e)[:120])

    async def _on_wrong(
        self,
        session: TutorSession,
        step: dict,
        message: str,
        judge: dict,
        ctx: SkillContext,
        meta: dict[str, Any],
    ) -> AsyncGenerator[dict, None]:
        session.attempts_on_step += 1
        # 按错误次数自动升级提示级别：1 次 Point 纠偏，2 次 Teach，3 次 Bottom-out
        auto_level = min(session.attempts_on_step, MAX_HINT_LEVEL)
        session.hint_level = max(session.hint_level, auto_level)
        level_name = prompts.LEVEL_NAMES[auto_level]
        counts = dict(session.hint_counts or {})
        counts[level_name] = counts.get(level_name, 0) + 1
        session.hint_counts = counts
        session.awaiting_attempt = True
        self._bump_step_stat(session, "hints")
        await ctx.db.flush()

        await self._record_attempt_error(session, message, ctx)

        # 连续受挫 ≥2 次 → 注入安抚降负指引（SRS §3.1.7）
        # 迭代05 C-P2-11：补充负向措辞信号源（受挫情绪表达也触发安抚，不只看错误次数）
        negative_signal = bool(_NEGATIVE_EMOTION_RE.search(message or ""))
        extra_note = (
            prompts.SOOTHE_NOTE
            if (session.attempts_on_step >= 2 or negative_signal)
            else ""
        )
        if session.plan.get("difficulty") == "hard":
            extra_note += prompts.GUIDE_HARD_NOTE

        level_desc, level_task = prompts.WRONG_LEVEL_TASK[auto_level]
        async for event in self._stream_student_text(
            ctx,
            session,
            messages=[
                {
                    "role": "user",
                    "content": self._guide_content(
                        prompts.WRONG_FEEDBACK.format(
                            question=session.question_text,
                            step_no=session.current_step,
                            student_answer=message,
                            attempts_history=self._attempts_block(session),
                            misconception=judge.get("misconception") or "未分类",
                            feedback_hint=judge.get("feedback_hint") or "引导学生重新检查这一步",
                            assertion=step["assertion"],
                            reason=step["reason"],
                            leak_rule=prompts.LEAK_RULE,
                            level_desc=level_desc,
                            level_task=level_task,
                        ),
                        session,
                        session.current_step,
                    ),
                }
            ],
            fallback=prompts.FALLBACK_GUIDE_TEXT,
            scene="socratic_hint",
            extra_note=extra_note,
        ):
            yield event
        # F13：本步配图（仅构图帧——答错纠偏同样不给答案性标注）
        async for event in self._figure_events(
            session, session.current_step, frame_limit=1
        ):
            yield event
        yield {
            "type": "card",
            "data": {
                "card_type": "socratic_hint",
                "session_id": str(session.id),
                "level": auto_level,
                "level_name": level_name,
            },
        }
        # 2026-10-05 同 partial/clarification：答错纠偏后仍停在当前步（current_step 只在
        # _on_correct 推进），补发选项让学生可以直接点选纠偏方向，而不是对着提示空打字框。
        choices = await self._step_choices(session.question_text, step, ctx)
        if choices:
            yield {
                "type": "card",
                "data": {
                    "card_type": "socratic_step_choice",
                    "session_id": str(session.id),
                    "step_no": session.current_step,
                    "options": choices,
                },
            }

    # ========== 题库底稿（RAG best-effort） ==========

    async def _retrieve_draft(self, question: str, ctx: SkillContext) -> dict | None:
        """检索题库标准解析作为已验证底稿；任何失败静默返回 None（不影响主流程）

        P0-2 提速：整体 2.5s 超时兜底——draft 是"锦上添花"（命中省一次 LLM 求解），
        不能让它拖慢首响应（当前无向量路时 RAG 约 8~15s，首答 95s 的元凶之一）。
        """
        try:
            import asyncio as _asyncio

            result = await _asyncio.wait_for(
                ctx.rag.retrieve(
                    question,
                    db=ctx.db,
                    conversation_history=[],
                    conversation_id="",
                    request_id=ctx.request_id,
                    scope="student",  # 端隔离：引导底稿不得引用教师用书/超纲资料
                    content_type="textbook",  # 底稿只要教材正文解析，课标/参考无解题价值
                ),
                timeout=2.5,
            )
        except Exception as e:
            logger.info("socratic.draft_retrieve_timeout_or_failed", error=str(e)[:150])
            return None
        if not getattr(result, "answerable", False) or not result.chunks:
            return None
        top = result.chunks[0]
        content = (top.content or "").strip()
        # 底稿门槛：内容足够长且像一份解析（含解/答/证明/步骤字样）
        if len(content) < 50 or not _DRAFT_SOLUTION_RE.search(content):
            return None
        logger.info(
            "socratic.draft_hit",
            chunk_id=top.chunk_id,
            score=round(top.score, 3),
            source=top.doc_title,
        )
        return {
            "content": content[:2500],
            "source": top.doc_title or "题库",
            "chunk_id": top.chunk_id,
            "score": top.score,
        }

    # ========== F13 图形规划与发射 ==========

    async def _plan_figures(
        self,
        question: str,
        steps: list[dict],
        ctx: SkillContext,
        *,
        image_file_ids: list[str] | None = None,
    ) -> list[dict]:
        """调用 figure planner 生成图形计划；非法整体重试一次，仍失败返回 []（纯文字讲解）。

        v3.3：消息带原题图片时，先用多模态 MiMo 读原图得到结构化图形描述
        （顶点相对位置/虚实线/标注），注入 planner——几何体形状不再靠 OCR 文本猜。
        """
        steps_block = "\n".join(
            f"第{i}步：{s['assertion']}（{s.get('reason') or '—'}）"
            for i, s in enumerate(steps, start=1)
        )
        figure_desc_block = ""
        if image_file_ids:
            image_desc = await self._describe_original_figure(image_file_ids, ctx)
            if image_desc:
                figure_desc_block = (
                    "\n【原题图描述（视觉模型读学生上传的原题图所得，构图必须与该描述一致，"
                    "顶点相对位置/虚实线以此为准）】\n" + image_desc + "\n"
                )
        error = ""
        for attempt in range(2):
            user = (
                prompts.FIGURE_PLANNER_USER.format(
                    question=question, steps_block=steps_block
                )
                if attempt == 0
                else prompts.FIGURE_PLANNER_RETRY.format(question=question, error=error)
            )
            if figure_desc_block:
                user = user.replace("【分步参考解】", f"{figure_desc_block}\n【分步参考解】")
            raw = await self._chat(
                ctx,
                [
                    {"role": "system", "content": prompts.FIGURE_PLANNER_SYSTEM},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=1400,
                thinking=False,
                scene="socratic_figure_plan",
            )
            items, error = parse_figure_plan(raw or "", len(steps))
            if error is None:
                return items
            logger.info(
                "socratic.figure_plan_retry",
                attempt=attempt + 1,
                error=(error or "")[:120],
            )
        logger.warning("socratic.figure_plan_failed", error=(error or "")[:150])
        return []

    async def _original_image_sha(
        self, image_file_ids: list[str], ctx: SkillContext
    ) -> str | None:
        """原题图 SHA-256（N3 图形合同：图与题绑定；属主校验，任何失败静默 None）"""
        if not image_file_ids:
            return None
        try:
            from app.models.file import File

            ids = [uuid.UUID(str(x)) for x in image_file_ids[:1]]
            row = (
                await ctx.db.execute(
                    select(File.sha256).where(
                        File.id.in_(ids),
                        File.user_id == uuid.UUID(ctx.user_id),
                        File.deleted_at.is_(None),
                    )
                )
            ).first()
            return str(row[0]) if row and row[0] else None
        except Exception as e:
            logger.info("socratic.image_sha_failed", error=str(e)[:120])
            return None

    async def _describe_original_figure(
        self, image_file_ids: list[str], ctx: SkillContext
    ) -> str | None:
        """多模态 MiMo 读原题图 → 结构化图形描述（planner 构图依据）。

        直调小米 MiMo 全模态端点（provider 实例的 model 是 mimo-v2.5-pro 文本模型，
        视觉须用 settings.mimo_vision_model）；任何失败返回 None（配图退化为纯文字规划）。
        """
        import uuid as _uuid

        import httpx

        from app.config import settings as _settings
        from app.services.geogebra_figure import resolve_image_data_uri

        channel = _settings.vision_channel()
        if channel is None:
            return None
        _vision_url, _vision_key, _vision_model = channel
        data_uri = None
        for fid in image_file_ids[:1]:
            try:
                data_uri = await resolve_image_data_uri(
                    ctx.db, _uuid.UUID(str(fid)), _uuid.UUID(ctx.user_id)
                )
            except Exception:
                data_uri = None
            if data_uri:
                break
        if not data_uri:
            return None
        prompt = (
            "这是高中数学题的原题图。请只描述你看到的事实，不要解题、不要推理：\n"
            "1. 图形类型（多面体/棱锥/棱柱/圆/圆锥曲线/函数图像等）与全部顶点或关键点的字母标注；\n"
            "2. 各顶点/关键点的相对位置（谁在上谁在下、谁在左谁在右、谁在前谁在后）；\n"
            "3. 哪些线是实线、哪些是虚线（被遮挡的棱）；\n"
            "4. 图中标注的长度、角度、记号（如垂直记号、等长记号）。\n"
            "用简洁的中文分点输出，不超过 200 字。"
        )
        payload = {
            "model": _settings.mimo_vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 500,
            # glm-5.3-flash 拒绝 thinking.type=disabled（400）→ 按全局开关省略
            **({} if _settings.llm_omit_thinking_field else {"thinking": {"type": "disabled"}}),
        }
        payload["model"] = _vision_model
        headers = {
            "Authorization": f"Bearer {_vision_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_vision_url, json=payload, headers=headers)
                resp.raise_for_status()
                text = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            text = text.strip()
            if not text:
                return None
            logger.info("socratic.figure_vision_described", chars=len(text))
            return text[:400]
        except Exception as e:
            logger.warning("socratic.figure_vision_failed", error=str(e)[:150])
            return None

    async def _figure_events(
        self, session: TutorSession, step_no: int, *, frame_limit: int | None = None
    ) -> AsyncGenerator[dict, None]:
        """发射某一步的 figure 事件：无图/渲染异常/不变量 fatal → 静默跳过（只记日志）。

        frame_limit：最多取前 N 帧（1=只给构图帧防泄题；None=全帧）。
        """
        steps = (session.plan or {}).get("steps") or []
        if not 1 <= step_no <= len(steps):
            return
        fig = steps[step_no - 1].get("figure") if isinstance(steps[step_no - 1], dict) else None
        if not isinstance(fig, dict) or not fig.get("params"):
            return
        try:
            payload = render_figure_frames(
                fig["params"],
                step_no=step_no,
                caption=str(fig.get("caption") or ""),
                frame_limit=frame_limit,
            )
        except Exception as e:  # 图形失败只丢图，绝不阻断讲解流
            logger.warning(
                "socratic.figure_render_failed", step=step_no, error=str(e)[:150]
            )
            return
        # N3 图形合同：原题图 SHA-256 随载荷下发（前端"已核对原题图"徽标依据）
        src_sha = (session.plan or {}).get("problem_source_sha256")
        if src_sha:
            payload["problem_source_sha256"] = str(src_sha)
        yield {"type": "figure", "data": payload}

    def _figure_context_block(self, session: TutorSession, step_no: int) -> str:
        """本步配图时生成注入引导 prompt 的图形上下文块（无图返回空串）。"""
        steps = (session.plan or {}).get("steps") or []
        if not 1 <= step_no <= len(steps):
            return ""
        fig = steps[step_no - 1].get("figure") if isinstance(steps[step_no - 1], dict) else None
        if not isinstance(fig, dict) or not fig.get("params"):
            return ""
        try:
            frames = derive_figure_frames(fig["params"])[:1]  # 引导阶段只展示构图帧
        except Exception as e:
            logger.warning("socratic.figure_derive_failed", step=step_no, error=str(e)[:120])
            return ""
        return prompts.FIGURE_CONTEXT_BLOCK.format(
            figure_desc=str(fig.get("caption") or "数学图形"),
            frames_shown="、".join(fr["label"] for fr in frames),
        )

    def _guide_content(self, content: str, session: TutorSession, step_no: int) -> str:
        """引导 prompt 内容 + 本步图形上下文块（无图则原样返回）。"""
        block = self._figure_context_block(session, step_no)
        return content + block if block else content

    # ========== solver：self-consistency + TIR + 步骤级沙箱验证 ==========

    async def _solve_verified(
        self,
        question: str,
        draft: dict | None,
        ctx: SkillContext,
        *,
        thinking: bool = True,
        image_file_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """单路流式求解（M2 重构 D2：单路深度推理 + 思考过程实时下发）。

        删除旧架构的：双路并行求解 / 终答 SymPy 等价比对 / 第三次仲裁 / 整轮重试 /
        步骤级验证脚本 / 修复重验——实测（2026-08-11）easy 题 217.8s 且两轮全降级。
        新链路：题库底稿（可选）→ 单路 solver 流式求解（TIR 代码回填保留，
        SymPy 从"裁判"降级为"可选计算器"）→ 契约解析失败反馈重试 → 直接成稿。
        v1.3：正文被 length 截断时断点续写（不再整段重想）；失败原因区分
        llm_unavailable（服务错误）与 solution_incomplete（反复截断/不合契约）。
        """
        yield {
            "type": "status",
            "data": {"stage": "solving", "text": "正在分析题目并求解…"},
        }

        sol: dict | None = None
        calls = 0
        llm_failed = False
        thinking_buf: list[str] = []
        async for ev in self._solve_once(question, 0.3, ctx, draft=draft, thinking=thinking):
            if ev["type"] == "_once":
                sol = ev["data"]["sol"]
                calls = ev["data"]["calls"]
                llm_failed = ev["data"]["llm_failed"]
            elif ev["type"] == "_thinking":
                # 迭代15 L1-4：底稿推理不再外发——solver 的推理流含输出契约协商等
                # 内部独白（实测 758 事件/轮，"我们被问到…""契约说…我需要权衡"），
                # 学生看到的是提示词拉扯而非数学思考。模型内部仍深度推理（保质量），
                # 但不流式下发、不持久化到学生可见的 thinking 面板。
                thinking_buf.append(ev["data"]["text"])
                # v3.3 思考模式开关修复：用户显式开启思考模式时，思考流实时下发
                # （前端 messageModel.case 'thinking' 面板现成，此前后端无条件吞掉导致开关形同虚设）
                if thinking and ev["data"].get("text"):
                    yield {"type": "thinking", "data": {"text": ev["data"]["text"]}}
            else:
                yield ev  # TIR 等细粒度状态透传给前端

        if sol is None:
            # v1.3 诚实区分两类失败：流错误/零输出 → 服务不可用；
            # 反复截断/不合契约 → 题目超出单次解答能力（引导用户拆问），不再混为一谈
            reason = "llm_unavailable" if llm_failed else "solution_incomplete"
            yield {
                "type": "_solve_done",
                "data": {"plan": None, "calls": calls, "reason": reason},
            }
            return

        # 难度校准（保留：以步骤数为客观锚点单向向上修正，对抗难度自报失真）
        raw_difficulty = str(sol.get("difficulty") or "medium").lower()
        steps_n = len(sol["steps"])
        calibrated_difficulty = raw_difficulty
        if steps_n >= 12 and raw_difficulty in ("easy", "medium"):
            calibrated_difficulty = "hard"
        elif steps_n >= 8 and raw_difficulty == "easy":
            calibrated_difficulty = "medium"
        if calibrated_difficulty != raw_difficulty:
            logger.info(
                "socratic.difficulty_calibrated",
                raw=raw_difficulty,
                calibrated=calibrated_difficulty,
                steps=steps_n,
            )

        plan = {
            "steps": sol["steps"],
            "final_answer": sol["final_answer"],
            "difficulty": calibrated_difficulty,
            # verified 语义变化（M2 重构）：单路成稿即采信，诚实记录为 False（未经多路交叉），
            # 供前端"本题由 AI 单路解答"提示与评测口径区分；引导质量靠教学状态机保障
            "verified": False,
            "check_ran": False,
            "draft_source": "kb" if draft else "llm",
            "solve_attempts": calls,
            "consistency": True,  # 单路无交叉比对，记 True（不再有多路不一致概念）
            "tool_calls": sol.get("tool_calls", []),
            "alt_solution": sol.get("alt_solution") or "",
            # 迭代15 L1-4：底稿独白不入学生可见 thinking（保留缓冲便于调试日志，不下发）
            "thinking": "",
            "leak_blocked": 0,
            "recent_attempts": [],
            "step_stats": {},
            "figures_planned": 0,
        }

        # F13：图形规划（主题门控）。planner 失败静默降级为纯文字讲解，
        # 绝不阻塞求解主链路；图形参数随 plan.steps 持久化。
        if should_plan_figures(question):
            figure_items = await self._plan_figures(
                question, plan["steps"], ctx, image_file_ids=image_file_ids or []
            )
            if figure_items:
                merge_figures_into_plan(plan["steps"], figure_items)
                plan["figures_planned"] = len(figure_items)
            logger.info(
                "socratic.figures_planned",
                planned=plan["figures_planned"],
                topic_hit=True,
            )
        else:
            logger.info("socratic.figures_planned", planned=0, topic_hit=False)
        yield {
            "type": "_solve_done",
            "data": {"plan": plan, "calls": calls, "reason": ""},
        }

    async def _solve_once(
        self,
        question: str,
        temperature: float,
        ctx: SkillContext,
        *,
        draft: dict | None = None,
        initial_feedback: str | None = None,
        thinking: bool = True,
    ) -> AsyncGenerator[dict, None]:
        """单路流式求解（M2 重构）：LLM 流式生成，thinking 片段实时下发（_thinking 事件），
        TIR 代码回填保留（SymPy 作"计算器"而非"裁判"）。结果经 _once 事件传出。

        v1.3 截断续写与思考闩锁：finish_reason=length 且正文非空 → 以 assistant 部分回复
        + CONTINUATION_PROMPT 续写（关思考，最多 _SOLVE_MAX_CONTINUATIONS 次）；思考吃光预算
        导致正文为空 → 闩锁关闭思考重试，本次求解内不再重开（含解析反馈重试）。

        data: {"sol": {steps, final_answer, difficulty, alt_solution, tool_calls} | None,
               "calls": int, "llm_failed": bool}
        """
        calls = 0
        tool_calls: list[dict] = []
        draft_block = ""
        if draft:
            draft_block = prompts.DRAFT_BLOCK.format(
                source=draft["source"], draft_content=draft["content"]
            )

        feedback = initial_feedback
        # v1.3 思考闩锁：一旦思考吃光预算导致正文为空，本次求解全程闩锁关闭思考——
        # 杜绝旧策略"解析失败重试又开思考→再吃光→再空转"（实测 17 分三问证明题
        # 两次 106~117s 全量重思考 + 两次 45~47s 直解截断，5 分 17 秒 4 次调用零产出）。
        thinking_on = thinking
        for _ in range(_PARSE_RETRIES + 1):
            if feedback:
                user_text = prompts.SOLVER_USER_RETRY.format(question=question, feedback=feedback)
            else:
                user_text = prompts.SOLVER_USER.format(question=question, draft_block=draft_block)
            messages = [
                {"role": "system", "content": prompts.SOLVER_SYSTEM},
                {"role": "user", "content": user_text},
            ]

            # v1.3 预算策略：思考开 6000 / 思考关 4000；正文被 length 截断时做
            # 断点续写（assistant 已写部分 + 续写指令，最多 _SOLVE_MAX_CONTINUATIONS 次，
            # 有效正文上限 ≈16k）——替代旧策略"截断即整段重想"。
            token_budget = _SOLVE_THINKING_BUDGET if thinking_on else _SOLVE_DIRECT_BUDGET
            tir_round = 0
            no_content_retry = False
            while True:
                raw_parts: list[str] = []
                stream_failed = False
                finish_reason = "stop"
                try:
                    async for event in ctx.llm.chat_stream(
                        messages,
                        temperature=temperature,
                        max_tokens=token_budget,
                        thinking=thinking_on,
                        request_id=ctx.request_id,
                        scene="socratic_solver",
                        emit_thinking=thinking_on,
                    ):
                        if "token" in event:
                            raw_parts.append(event["token"])
                        elif "thinking" in event:
                            yield {"type": "_thinking", "data": {"text": event["thinking"]}}
                        elif "_finish" in event:
                            finish_reason = event["_finish"]
                        elif "_error" in event:
                            raise RuntimeError(event["_error"].get("message", "stream error"))
                except Exception as e:
                    logger.warning("socratic.solve_stream_failed", error=str(e)[:200])
                    stream_failed = True

                calls += 1
                if stream_failed:
                    yield {
                        "type": "_once",
                        "data": {"sol": None, "calls": calls, "llm_failed": True},
                    }
                    return

                raw = "".join(raw_parts).strip()

                if not raw:
                    if thinking_on and not no_content_retry:
                        # 正文被思考吃光（finish_reason=length 截断为空）→ 闩锁关思考重试
                        no_content_retry = True
                        thinking_on = False
                        token_budget = _SOLVE_DIRECT_BUDGET
                        logger.warning(
                            "socratic.solve_no_content_retry",
                            hint="thinking 消耗全部 token 预算，关闭思考重试（本次求解不再开启）",
                        )
                        yield {
                            "type": "status",
                            "data": {"stage": "solving", "text": "这道题比较复杂，换个更直接的方式继续解…"},
                        }
                        continue
                    # 直解也零输出 → 视为模型不可用
                    yield {
                        "type": "_once",
                        "data": {"sol": None, "calls": calls, "llm_failed": True},
                    }
                    return

                # v1.3 截断续写：正文非空但被 length 截断 → 带上已写部分续写，而非从零重想。
                # 续写一律关思考（本段思考已发生），直到 finish≠length 或续写次数用尽。
                continuations = 0
                while finish_reason == "length" and continuations < _SOLVE_MAX_CONTINUATIONS:
                    continuations += 1
                    yield {
                        "type": "status",
                        "data": {"stage": "solving", "text": "解答篇幅较长，正在续写…"},
                    }
                    cont_parts: list[str] = []
                    cont_failed = False
                    cont_messages = [
                        *messages,
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": prompts.CONTINUATION_PROMPT},
                    ]
                    try:
                        async for event in ctx.llm.chat_stream(
                            cont_messages,
                            temperature=temperature,
                            max_tokens=_SOLVE_CONTINUATION_BUDGET,
                            thinking=False,
                            request_id=ctx.request_id,
                            scene="socratic_solver",
                        ):
                            if "token" in event:
                                cont_parts.append(event["token"])
                            elif "_finish" in event:
                                finish_reason = event["_finish"]
                            elif "_error" in event:
                                raise RuntimeError(
                                    event["_error"].get("message", "stream error")
                                )
                    except Exception as e:
                        logger.warning("socratic.solve_stream_failed", error=str(e)[:200])
                        cont_failed = True
                    calls += 1
                    if cont_failed:
                        yield {
                            "type": "_once",
                            "data": {"sol": None, "calls": calls, "llm_failed": True},
                        }
                        return
                    chunk = "".join(cont_parts).strip()
                    if not chunk:
                        break  # 续写零产出，别再空转（交给下方契约解析/反馈重试）
                    raw = f"{raw}\n{chunk}"
                    logger.info(
                        "socratic.solve_continuation", round=continuations, chars=len(raw)
                    )

                blocks = extract_code_blocks(raw)
                if blocks and tir_round < _TIR_MAX_ROUNDS:
                    # TIR：执行代码块，回填真实计算结果续解
                    tir_round += 1
                    yield {
                        "type": "status",
                        "data": {"stage": "verify_compute", "text": "正在用程序验证关键计算…"},
                    }
                    results_text: list[str] = []
                    for i, code in enumerate(blocks[:_TIR_MAX_BLOCKS], start=1):
                        r = await run_sandbox(code, timeout_ms=_TIR_BLOCK_TIMEOUT_MS)
                        tool_calls.append({"code": code[:200], "exec_status": r["exec_status"]})
                        out = (
                            r["stdout"]
                            if r["exec_status"] == "pass"
                            else (r["error"] or "执行失败")
                        )
                        results_text.append(
                            f"代码块 {i}（状态：{r['exec_status']}）：\n{out[:800]}"
                        )
                    messages = [
                        *messages,
                        {"role": "assistant", "content": raw},
                        {
                            "role": "user",
                            "content": prompts.TIR_FEEDBACK.format(
                                results="\n\n".join(results_text)
                            ),
                        },
                    ]
                    continue

                # 无代码块（或 TIR 轮次用尽）→ 校验输出契约
                steps, final_answer, difficulty, alt_solution, error = parse_solver_output(raw)
                if error is None:
                    if blocks:
                        logger.info("socratic.final_code_stripped", blocks=len(blocks))
                    sol = {
                        "steps": steps,
                        "final_answer": final_answer,
                        "difficulty": difficulty,
                        "alt_solution": alt_solution,
                        "tool_calls": tool_calls,
                    }
                    yield {
                        "type": "_once",
                        "data": {"sol": sol, "calls": calls, "llm_failed": False},
                    }
                    return

                logger.info("socratic.solve_parse_retry", error=error[:120])
                feedback = error
                break  # 跳出 TIR 循环，进入下一次解析重试

        yield {"type": "_once", "data": {"sol": None, "calls": calls, "llm_failed": False}}

    # _reconcile / _verify_steps / _repair_solution / _equivalent 已随 M2 重构删除
    # （D1：SymPy 从"事后裁判"降级为"可选计算器"，多路交叉验证不再作为在线链路门槛）

    @staticmethod
    def _is_textual(answer: str) -> bool:
        """含中文的终答视为文本型（证明题/论述题），判答快速通道跳过 SymPy 比对"""
        return bool(_TEXTUAL_ANSWER_RE.search(answer or ""))

    @staticmethod
    def _is_challenge(message: str) -> bool:
        """短消息质疑/追问识别（确定性 clarification 短路用）"""
        msg = (message or "").strip()
        return bool(msg) and len(msg) <= _CHALLENGE_MAX_LEN and bool(_CHALLENGE_RE.search(msg))

    @staticmethod
    def _looks_like_new_problem(message: str) -> bool:
        """new_problem 判定的硬证据门槛：显式换题意图词，或带完整题干特征且足够长。

        思路选项描述（"建立空间直角坐标系…""用几何法证明…"）两者皆不满足，会被
        _on_attempt 的 new_problem 硬闸降级为 partial 继续引导。
        """
        msg = (message or "").strip()
        if not msg:
            return False
        if re.search(r"改做|换道题|换一?道|另一道|再来一题|做这题|新题", msg):
            return True
        if len(msg) < 30:
            return False
        return bool(re.search(r"已知|如图|求证|证明：|证明:|求：|求下列|若[a-zA-Z]", msg))

    @staticmethod
    def _norm_reply(text: str) -> str:
        """回复文本归一化（去空白与标点），反重复查重用"""
        return re.sub(r"[\s，。！？；：、,.!?;:～~“”\"'（）()【】\[\]]+", "", text or "")

    @staticmethod
    def _off_topic_variant(streak: int) -> str:
        """连续跑题时的确定性轮换拉回文案（逐级更具体，绝不连续重复）"""
        variants = prompts.OFF_TOPIC_REANCHOR_VARIANTS
        return variants[(max(int(streak), 1) - 1) % len(variants)]

    def _set_plan_key(self, session: TutorSession, key: str, value: Any) -> None:
        plan = dict(session.plan or {})
        plan[key] = value
        session.plan = plan

    def _last_tutor_question(self, session: TutorSession) -> str:
        """老师最近一句学生可见的话（判答上下文/反重复查重用）"""
        msgs = (session.plan or {}).get("recent_tutor_msgs") or []
        return str(msgs[-1]) if msgs else ""

    def _record_tutor_msg(self, session: TutorSession, text: str) -> None:
        """记录老师学生可见发言（环形缓冲 2 条），供判答上下文与反重复闸门使用"""
        t = (text or "").strip()
        if not t:
            return
        plan = dict(session.plan or {})
        msgs = list(plan.get("recent_tutor_msgs") or [])
        msgs.append(t[:180])
        plan["recent_tutor_msgs"] = msgs[-2:]
        session.plan = plan

    # ========== judge ==========

    # 快速通道变量门槛的函数名白名单（C-P2-13）
    _JUDGE_FN_TOKENS = frozenset(
        {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "sqrt", "pi", "arcsin", "arccos", "arctan"}
    )

    async def _judge(
        self, session: TutorSession, step: dict, message: str, ctx: SkillContext
    ) -> tuple[str, dict]:
        """判答：sympy 快速通道优先，短消息质疑确定性短路，否则 LLM JSON 判答
        （带本步历史作答 + 老师上一问上下文）"""
        student_expr = extract_math_expr(message)
        step_expr = extract_math_expr(step.get("assertion", ""))
        if student_expr and step_expr:
            # 快速通道门槛（迭代05 C-P2-13）：防"恰好等价的中间式/无关式"误判 correct：
            # 纯数值式（双方无字母 token）放行；含变量式要求变量集合有交集
            toks_s = set(re.findall(r"[A-Za-z]+", student_expr)) - self._JUDGE_FN_TOKENS
            toks_t = set(re.findall(r"[A-Za-z]+", step_expr)) - self._JUDGE_FN_TOKENS
            gate_ok = (not toks_s and not toks_t) or bool(toks_s & toks_t)
            if not gate_ok:
                logger.info("socratic.judge_fastpath_skipped", student=student_expr[:40], step=step_expr[:40])
            try:
                if gate_ok:
                    eq = await check_equivalence(student_expr, step_expr, timeout_ms=2000)
                    if eq.get("verdict") == "correct":
                        return "correct", {
                            "verdict": "correct",
                            "misconception": None,
                            "feedback_hint": None,
                            "via": "sympy",
                        }
            except Exception as e:
                logger.warning("socratic.judge_sympy_failed", error=str(e)[:150])

        # 确定性短路：短消息质疑/追问不走 LLM 判答（防"难道不对吗"被误判 off_topic
        # 后复读固定话术——2026-08 截图事故防复发）。带数学式的猜测已在上方快照通道处理。
        if self._is_challenge(message):
            logger.info("socratic.judge_challenge_rule", message=message[:40])
            return "clarification", {
                "verdict": "clarification",
                "misconception": None,
                "feedback_hint": None,
                "via": "rule_challenge",
            }

        tutor_block = ""
        last_tutor_msg = self._last_tutor_question(session)
        if last_tutor_msg:
            tutor_block = (
                "【老师上一句对学生说的话（学生的作答大概率是对这句话的回应）】\n"
                f"{last_tutor_msg}\n"
            )
        raw = await self._chat(
            ctx,
            [
                {"role": "system", "content": prompts.JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": prompts.JUDGE_USER.format(
                        question=session.question_text,
                        assertion=step["assertion"],
                        reason=step["reason"],
                        attempts_history=self._attempts_block(session),
                        tutor_block=tutor_block,
                        student_answer=message,
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=600,
            thinking=False,
            scene="socratic_judge",
        )
        return self._parse_judge(raw)

    def _parse_judge(self, raw: str | None) -> tuple[str, dict]:
        """解析 judge JSON；任何异常 → partial（同级追问澄清，安全降级）"""
        fallback = {"verdict": "partial", "misconception": None, "feedback_hint": None}
        if not raw:
            return "partial", fallback
        data: Any = None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except (json.JSONDecodeError, TypeError):
                    data = None
        if not isinstance(data, dict):
            return "partial", fallback
        verdict = data.get("verdict")
        if verdict not in _VERDICTS:
            verdict = "partial"
        misconception = data.get("misconception")
        if misconception not in _MISCONCEPTIONS:
            misconception = None
        return verdict, {
            "verdict": verdict,
            "misconception": misconception,
            "feedback_hint": data.get("feedback_hint"),
        }

    # ========== 学生可见文本生成（真流式 + 逐句防泄题） ==========

    async def _stream_student_text(
        self,
        ctx: SkillContext,
        session: TutorSession,
        *,
        messages: list[dict],
        fallback: str,
        scene: str,
        temperature: float = 0.7,
        max_tokens: int = 500,
        extra_note: str = "",
    ) -> AsyncGenerator[dict, None]:
        """生成发给学生看的文本：真流式逐句下发，每句经滑窗泄题检查。

        泄露处理：中断流 → 严指令重生成一次（非流式）→ 仍泄露 → 零泄露兜底模板
        （若此前已发出过干净句子，则不再追加兜底模板，直接止发）。
        """
        if extra_note:
            messages = [
                *messages[:-1],
                {**messages[-1], "content": messages[-1]["content"] + extra_note},
            ]

        buffer = ""
        prev_sentence = ""
        collected: list[str] = []
        emitted_any = False
        leaked = False

        try:
            async for event in ctx.llm.chat_stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=False,
                request_id=ctx.request_id,
                scene=scene,
            ):
                if "token" in event:
                    buffer += event["token"]
                    collected.append(event["token"])
                    sentences, buffer = _pop_complete_sentences(buffer)
                    # 长缓冲无句读 → 强制 flush 防卡顿
                    if not sentences and len(buffer) >= _SENTENCE_FLUSH_CHARS:
                        sentences, buffer = [buffer], ""
                    for sentence in sentences:
                        if find_leak(prev_sentence + sentence, session.plan, session.current_step):
                            leaked = True
                            break
                        yield {"type": "token", "data": {"text": sentence}}
                        prev_sentence = sentence
                        emitted_any = True
                    if leaked:
                        break
                elif "_error" in event:
                    raise RuntimeError(event["_error"].get("message", "stream error"))
        except Exception as e:
            logger.warning("socratic.stream_failed", scene=scene, error=str(e)[:200])
            # 流异常：已发干净句子则止；未发则兜底
            if not emitted_any:
                yield {"type": "token", "data": {"text": fallback}}
            else:
                self._record_tutor_msg(session, "".join(collected))
            return

        # 收尾：残余缓冲检查后下发
        if not leaked and buffer.strip():
            if find_leak(prev_sentence + buffer, session.plan, session.current_step):
                leaked = True
            else:
                yield {"type": "token", "data": {"text": buffer}}
                emitted_any = True

        if not leaked:
            if emitted_any:
                # 记录本次老师发言（判答上下文 + off_topic 反重复查重的数据源）
                self._record_tutor_msg(session, "".join(collected))
            else:
                yield {"type": "token", "data": {"text": fallback}}
            return

        # ===== 泄露拦截：严指令重生成一次 =====
        self._inc_leak_blocked(session)
        logger.info("socratic.leak_blocked", session_id=str(session.id), scene=scene, retry=1)
        full_text = "".join(collected)
        regen = await self._chat(
            ctx,
            [
                *messages,
                {"role": "assistant", "content": full_text},
                {"role": "user", "content": prompts.REGEN_STRICT_NOTE},
            ],
            temperature=0.4,
            max_tokens=max_tokens,
            thinking=False,
            scene=scene,
        )
        if regen and not find_leak(regen, session.plan, session.current_step):
            # 重生成干净：整段已检，按句下发
            sentences, remainder = _pop_complete_sentences(regen)
            for sentence in sentences:
                yield {"type": "token", "data": {"text": sentence}}
            if remainder.strip():
                yield {"type": "token", "data": {"text": remainder}}
            self._record_tutor_msg(session, regen)
            return

        if regen:
            self._inc_leak_blocked(session)
            logger.info("socratic.leak_blocked", session_id=str(session.id), scene=scene, retry=2)
        if not emitted_any:
            yield {"type": "token", "data": {"text": fallback}}
        # 已发过干净句子的场景：止发即可，不追加兜底模板

    async def _stream_summary(
        self, session: TutorSession, *, outcome: str, ctx: SkillContext
    ) -> AsyncGenerator[dict, None]:
        """完成/揭示总结：真流式直接下发（学生已解出或已选择查看，无泄题风险）"""
        plan = session.plan
        steps_text = "\n".join(
            f"第{i}步：{s['assertion']}（{s.get('reason') or '—'}）"
            for i, s in enumerate(plan.get("steps") or [], start=1)
        )
        outcome_desc = (
            "通过自己的努力"
            if outcome in ("independent", "guided")
            else "选择查看完整解答，现在对照解答"
        )
        independent_note = (
            "（他全程零提示零求答独立完成，请特别肯定这一点）" if outcome == "independent" else ""
        )
        messages = [
            {
                "role": "user",
                "content": prompts.COMPLETE_SUMMARY.format(
                    outcome_desc=outcome_desc,
                    question=session.question_text,
                    solution_text=steps_text or "（无步骤）",
                    final_answer=plan.get("final_answer") or "（见推导）",
                    independent_note=independent_note,
                ),
            }
        ]
        emitted = False
        try:
            async for event in ctx.llm.chat_stream(
                messages,
                temperature=0.6,
                max_tokens=500,
                thinking=False,
                request_id=ctx.request_id,
                scene="socratic_summary",
            ):
                if "token" in event:
                    emitted = True
                    yield {"type": "token", "data": {"text": event["token"]}}
                elif "_error" in event:
                    raise RuntimeError(event["_error"].get("message", "stream error"))
        except Exception as e:
            logger.warning("socratic.summary_failed", error=str(e)[:200])
        if not emitted:
            yield {
                "type": "token",
                "data": {
                    "text": "这道题我们就走完了。回头找一道类似的题练一练，把这个考点彻底拿下。"
                },
            }
        # F13：总结/揭示时按步骤顺序展示全部图形（完整帧——学生已拿到完整解答，无泄题约束）
        for step_no in range(1, len((session.plan or {}).get("steps") or []) + 1):
            async for event in self._figure_events(session, step_no, frame_limit=None):
                yield event

    # ========== LLM 调用封装 ==========

    async def _chat(
        self,
        ctx: SkillContext,
        messages: list[dict],
        *,
        temperature: float,
        max_tokens: int,
        thinking: bool,
        scene: str,
    ) -> str | None:
        try:
            result = await ctx.llm.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                request_id=ctx.request_id,
                scene=scene,
            )
            return (result.get("content") or "").strip() or None
        except Exception as e:
            logger.warning("socratic.llm_failed", scene=scene, error=str(e)[:200])
            return None

    # ========== 每步思路选项（对话内点选，避免学生空手打字） ==========

    # 与 smart_quiz v1.11 同源的转义腐蚀防护：\f \b \t \r 是合法 JSON 转义，
    # 但在数学 JSON 里它们几乎一定是模型单反斜杠 LaTeX（\frac \tan \nu…）漏转义，
    # strict loads 会静默产出 \x0c 等控制符（真机实锤：选项渲染成换页符+"rac{b}{2a}"）
    _STEP_ESCAPE_CORRUPT = ("\x08", "\x09", "\x0c", "\x0d")
    _STEP_ESCAPE_FIX_RE = re.compile(r"\\\\|\\(?=[a-zA-Z][a-zA-Z])|\\(?![\\/bfnrtu\"])")

    # 选项生成的确定性兜底（模型两次都不可用/不合规时）：通用思考方向、零泄题。
    # 目的：学生端"引导选项框一下有一一下没有"——选项必须永远有，宁可给通用方向。
    _FALLBACK_STEP_CHOICES = (
        "先梳理题目的已知条件和要证的结论",
        "回忆这道题涉及的核心定义或定理",
        "尝试画图或代入具体数值找规律",
    )

    @staticmethod
    def _loads_choice_json(raw: str):
        """step_choices JSON 解析：检测控制符腐蚀 → 补全单反斜杠重解析一次。"""

        def corrupt(v) -> bool:
            if isinstance(v, str):
                return any(c in v for c in SocraticSolverExecutor._STEP_ESCAPE_CORRUPT)
            if isinstance(v, dict):
                return any(corrupt(x) for x in v.values())
            if isinstance(v, list):
                return any(corrupt(x) for x in v)
            return False

        fixed = SocraticSolverExecutor._STEP_ESCAPE_FIX_RE.sub(
            lambda m: m.group(0) if len(m.group(0)) == 2 else "\\\\", raw
        )
        for candidate, is_original in ((raw, True), (fixed, False)):
            try:
                data = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if is_original and corrupt(data):
                continue
            return data
        return None

    @staticmethod
    def _parse_step_choices(raw: str | None) -> list[str]:
        """解析 STEP_CHOICES 的 JSON 数组；任何不合规都返回空列表。

        空列表的统一语义是「本次不下发选项」，调用方据此保持与改造前完全一致的行为。
        """
        if not raw:
            return []
        m = re.search(r"\[.*\]", raw.strip(), re.S)
        if not m:
            return []
        data = SocraticSolverExecutor._loads_choice_json(m.group(0))
        if not isinstance(data, list):
            return []
        out: list[str] = []
        for item in data:
            c = str(item or "").strip()
            # 容忍模型自行加的 "A." / "1、" 之类序号前缀
            c = re.sub(r"^\s*(?:[A-Da-d]|[0-9])[.、．)）:：]\s*", "", c).strip()
            if c and c not in out:
                out.append(c)
        return out[:4]

    async def _step_choices(
        self, question: str, step: dict, ctx: SkillContext
    ) -> list[str]:
        """为当前步骤生成「下一步思路」候选。

        选项是**思路方向**而非答案，学生点选后作为普通回答进入既有判定链路，
        因此后端判定逻辑无需改动。

        best-effort：任何失败/不足 2 项都返回空列表 → 调用方不下发选项，
        行为与改造前一致，绝不因选项生成失败影响主链路。
        """
        try:
            raw = await self._chat(
                ctx,
                [
                    {
                        "role": "user",
                        "content": prompts.STEP_CHOICES.format(
                            question=(question or "")[:800],
                            assertion=(step or {}).get("assertion", ""),
                            reason=(step or {}).get("reason", ""),
                        ),
                    }
                ],
                temperature=0.4,
                max_tokens=240,
                thinking=False,
                scene="socratic_step_choices",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("socratic.step_choices_failed", error=str(e)[:120])
            raw = None
        choices = self._parse_step_choices(raw)
        if len(choices) < 2:
            # 一次严格重试（2026-09-14 真机：模型偶发回解释性文字而非 JSON 数组，
            # 导致学生端"引导选项框一下有一一下没有"）；仍失败则走确定性兜底。
            logger.info("socratic.step_choices_retry", got=len(choices))
            try:
                raw2 = await self._chat(
                    ctx,
                    [
                        {
                            "role": "user",
                            "content": (
                                "只输出一个 JSON 数组，不要任何解释、不要 markdown 代码围栏。"
                                "数组内是 3 个「下一步怎么做」的思考方向短语（各 12~24 字，不能含最终答案）："
                                f"题目：{(question or '')[:300]}；本步要得出的结论：{(step or {}).get('assertion', '')[:120]}"
                            ),
                        }
                    ],
                    temperature=0.2,
                    max_tokens=180,
                    thinking=False,
                    scene="socratic_step_choices",
                )
                choices = self._parse_step_choices(raw2)
            except Exception as e:  # noqa: BLE001
                logger.warning("socratic.step_choices_retry_failed", error=str(e)[:120])
        if len(choices) < 2:
            # 确定性兜底：保证学生永远有选项可点（通用方向、零泄题）
            logger.info("socratic.step_choices_fallback", got=len(choices))
            choices = list(self._FALLBACK_STEP_CHOICES)
        return choices

    # ========== 状态与档案小工具 ==========

    async def _get_active_session(self, ctx: SkillContext) -> TutorSession | None:
        result = await ctx.db.execute(
            select(TutorSession)
            .where(
                TutorSession.conversation_id == ctx.conversation_id,
                TutorSession.user_id == ctx.user_id,
                TutorSession.status == STATUS_ACTIVE,
                TutorSession.deleted_at.is_(None),
            )
            .order_by(TutorSession.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_session_by_id(
        self, ctx: SkillContext, session_id: Any
    ) -> TutorSession | None:
        """按 id 取引导会话（regenerate 从原消息 envelope.meta.extra.session_id 恢复）。
        不限制状态机状态——revealed/completed 会话允许幂等重发；越权/跨会话一律 None"""
        try:
            sid = uuid.UUID(str(session_id))
        except (ValueError, AttributeError, TypeError):
            return None
        result = await ctx.db.execute(
            select(TutorSession).where(
                TutorSession.id == sid,
                TutorSession.conversation_id == ctx.conversation_id,
                TutorSession.user_id == ctx.user_id,
                TutorSession.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def _get_latest_session(
        self, ctx: SkillContext, *, include_ended: bool = False
    ) -> TutorSession | None:
        """动作消息粘连用：include_ended=True 时含 revealed/completed（最近更新优先）。
        degraded/abandoned 不算——无已验证 plan 可重发，避免空解答"""
        statuses = (
            [STATUS_ACTIVE, STATUS_REVEALED, STATUS_COMPLETED]
            if include_ended
            else [STATUS_ACTIVE]
        )
        result = await ctx.db.execute(
            select(TutorSession)
            .where(
                TutorSession.conversation_id == ctx.conversation_id,
                TutorSession.user_id == ctx.user_id,
                TutorSession.status.in_(statuses),
                TutorSession.deleted_at.is_(None),
            )
            .order_by(TutorSession.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _profile_text(self, ctx: SkillContext) -> str:
        """学生画像（如可得）：年级/水平/薄弱点，失败静默为空。
        v1.2 AI 管家：优先用学情画像卡（含掌握度/错题/节奏），失败回退基础档案。"""
        try:
            # 画像卡（L1 学情聚合，AI 全局知晓）
            try:
                from app.services.learning_profile import get_learning_profile_service

                card_text = (
                    await get_learning_profile_service().build_profile_card_text(
                        ctx.db, ctx.user_id, role=ctx.user_role or "student"
                    )
                ) or ""
                if card_text:
                    return card_text + "（提问深浅可据此调整）"
            except Exception:
                pass
            # 回退：基础档案
            from app.models.user_profile import UserProfile

            result = await ctx.db.execute(
                select(UserProfile).where(UserProfile.user_id == ctx.user_id)
            )
            profile = result.scalar_one_or_none()
            if profile is None:
                return ""
            parts: list[str] = []
            if profile.grade:
                parts.append(f"年级：{profile.grade}")
            if profile.level and profile.level != "unknown":
                parts.append(f"水平：{profile.level}")
            weak = profile.weak_points or []
            if weak:
                parts.append("薄弱点：" + "、".join(str(w) for w in weak[:3]))
            if not parts:
                return ""
            return "【学生画像】" + "；".join(parts) + "（提问深浅可据此调整）"
        except Exception:
            return ""

    def _inc_leak_blocked(self, session: TutorSession) -> None:
        plan = dict(session.plan or {})
        plan["leak_blocked"] = int(plan.get("leak_blocked") or 0) + 1
        session.plan = plan

    def _record_attempt(self, session: TutorSession, message: str, verdict: str) -> None:
        """记录学生作答历史（plan.recent_attempts 环形缓冲，判答/反馈 prompt 上下文用）"""
        plan = dict(session.plan or {})
        attempts = list(plan.get("recent_attempts") or [])
        attempts.append({"step": session.current_step, "text": message[:200], "verdict": verdict})
        plan["recent_attempts"] = attempts[-_MAX_RECENT_ATTEMPTS:]
        session.plan = plan

    def _attempts_block(self, session: TutorSession) -> str:
        """本步最近作答历史注入块（无则空串）"""
        attempts = [
            a
            for a in (session.plan.get("recent_attempts") or [])
            if a.get("step") == session.current_step
        ]
        if not attempts:
            return ""
        lines = "\n".join(
            f"- 第{i}次：{a['text'][:80]}（判定：{a['verdict']}）"
            for i, a in enumerate(attempts[-3:], start=1)
        )
        return prompts.ATTEMPTS_HISTORY_BLOCK.format(attempts_lines=lines)

    def _bump_step_stat(self, session: TutorSession, key: str) -> None:
        """每步作答/提示计数（plan.step_stats，完成卡"思考轨迹回放"数据源）"""
        plan = dict(session.plan or {})
        stats = dict(plan.get("step_stats") or {})
        entry = dict(stats.get(str(session.current_step)) or {"attempts": 0, "hints": 0})
        entry[key] = int(entry.get(key, 0)) + 1
        stats[str(session.current_step)] = entry
        plan["step_stats"] = stats
        session.plan = plan

    @staticmethod
    def _is_independent(session: TutorSession) -> bool:
        counts = session.hint_counts or {}
        total_hints = sum(int(v or 0) for v in counts.values())
        return total_hints == 0 and session.answer_requests == 0

    @staticmethod
    def _hint_stats(session: TutorSession) -> dict:
        counts = {"point": 0, "teach": 0, "bottom_out": 0}
        counts.update({k: int(v or 0) for k, v in (session.hint_counts or {}).items()})
        return {
            **counts,
            "total": sum(counts.values()),
            "answer_requests": session.answer_requests,
            "leak_blocked": int((session.plan or {}).get("leak_blocked") or 0),
        }

    def _complete_card(self, session: TutorSession, *, outcome: str) -> dict:
        """完成卡：思考轨迹回放数据（步骤 + 每步作答/提示统计 + 用时 + 难度 + 验证标）"""
        plan = session.plan or {}
        duration_s = None
        if session.completed_at and session.created_at:
            duration_s = max(0, int((session.completed_at - session.created_at).total_seconds()))
        return {
            "card_type": "socratic_complete",
            "session_id": str(session.id),
            "outcome": outcome,
            "steps": plan.get("steps", []),
            "final_answer": plan.get("final_answer"),
            "hint_stats": self._hint_stats(session),
            "difficulty": plan.get("difficulty", "medium"),
            "verified": bool(plan.get("verified")),
            "duration_s": duration_s,
            "step_stats": plan.get("step_stats") or {},
        }

    @staticmethod
    def _progress_card(session: TutorSession, verdict: str) -> dict:
        return {
            "card_type": "socratic_progress",
            "session_id": str(session.id),
            "current_step": session.current_step,
            "steps_count": len(session.plan.get("steps") or []),
            "hint_level": session.hint_level,
            "attempts_on_step": session.attempts_on_step,
            "verdict": verdict,
        }
