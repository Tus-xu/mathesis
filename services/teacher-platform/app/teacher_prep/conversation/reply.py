"""R2 对话去表单机：assistant **prose** 生成（G5：先回应老师这一句，再给结构化卡）。

契约（PROMPT.md §4 对话契约第 1/6 条）：
- 任何 assistant 卡消息必须携带针对**老师这一句**的 prose 正文；只回卡 = FAIL；
- 闲聊 / 问知识点 / 质疑都必须有针对该句的回应，不得复读教材、不得模板腔；
- prose 只是回应层，不承载事实性业务数据（教材名/章节/选项来自授权投影）。

实现：
- 优先 LLM（use_case=`teacher_prep_reply`，见 app/llm/config.py USE_CASE_ROUTES）；
- LLM 不可用 / 超时 / test 环境 → **确定性兜底**：把老师原话引入回应（天然"针对该句"），
  不编造任何教学事实；失败绝不阻断主链路（prose 缺失时前端也不渲染空泡）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ..text_safety import truncate_math_aware

logger = logging.getLogger("teacher.prep.conversation.reply")

_MAX_REPLY_CHARS = 300


def quote_of(message: str | None, limit: int = 24) -> str:
    """老师原话的有界引用（确定性兜底回应里"针对该句"的锚点）。"""
    text = " ".join((message or "").split())
    return text[:limit]


def _fallback(situation: str, teacher_message: str | None, state: dict[str, Any] | None) -> str:
    """确定性兜底回应：引入老师原话 + 当前真实主题（缺省不造数）。"""
    q = quote_of(teacher_message)
    topic = str((state or {}).get("title") or "").strip()
    topic_part = f"《{topic}》" if topic else "这次备课"
    if situation == "question":
        return (
            f"你问的「{q}」我记下了——这个问题我会结合{topic_part}的学情来把握。"
            "想把某个考法落进教学方案的话，直接告诉我，比如「把这类题作为例题」。"
        )
    if situation == "feedback":
        return (
            f"听到你的反馈了：「{q}」。你直接告诉我想要的调整（比如「压缩到 3 个环节」"
            "或「改成 2 个课时」），我就按新要求更新方案。"
        )
    if situation == "confirm":
        return f"好，就按这个方向继续。{topic_part}的教学方案已确认，接下来生成教学流程与课件、教案。"
    if situation == "confirm_noop":
        return (
            f"关于「{q}」：当前没有待确认的教学方案。想继续备课的话，直接说要求就行"
            "（比如「把重点放在函数单调性的应用上」）。"
        )
    if situation == "blueprint_proposal":
        return (
            f"收到你说的「{q}」。我按这个方向拟好了{topic_part}的教学方案，"
            "先看下面的备课信息和教学意图；没问题就点「确认，继续生成」，要调整直接说。"
        )
    if situation == "node_revision":
        return (
            f"关于「{q}」：我只调整了这一个教学环节，其它环节保持不变。"
            "可以看下面的修订摘要，不合适直接说要怎么改。"
        )
    if situation == "selection_pending":
        # 宪法 §20：兜底是老师的话，不说「还差X才能继续备课」这类系统机制话术；
        # 随后真的会给出对应选择卡（教材 / 章节），文案与卡一致。
        if str((state or {}).get("missing") or "") == "textbook":
            return "收到。还差一步就能开始：先在这一步选定教材，我在下面列出了可选的几本。"
        return "收到。还差一步就能开始：在这一册里选一章，我在下面列出了可选的章节。"
    if situation == "selection_repeat":
        return (
            "收到，这个选择我已经记下了，不用再点一遍。"
            "接下来直接告诉我这节课想讲什么（一个知识点或一道例题都行），我们继续往前走。"
        )
    if situation == "basic_mode":
        return f"这轮没能完成，你说的「{q}」我先记着。可以点重试，或直接补充/修改要求后重新发送。"
    return f"收到你说的「{q}」。我会按这个方向继续处理{topic_part}。"


def _clean_reply(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl >= 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = " ".join(raw.split())
    # 输出边界：禁 HTML / 禁组件名上屏（与 catalog 同一铁律；prose 是纯文本）
    if "<" in raw[:500] and re.search(r"</?[a-zA-Z]", raw):
        return ""
    # 数学感知截断：截断点不切进 $…$ 定界符内部（防未闭合 $ 外泄到前端）；只减不增。
    return truncate_math_aware(raw, _MAX_REPLY_CHARS)


def _llm_disabled() -> bool:
    """test 环境 / pytest 进程不打真实 LLM（单测确定性；prose 走兜底模板）。"""
    from app.core.config import get_settings

    return get_settings().environment == "test" or "PYTEST_CURRENT_TEST" in os.environ


_PROMPT_HEAD = (
    "你是高中数学教师的备课助手。老师刚对你说了一句话，请用 1~3 句口语化中文**直接回应这句话**"
    "（先接住老师这句话本身，再说你接下来做什么），不要列表、不要 JSON、不要任何标记语言，"
    "不超过 100 字。不要复读教材原文，不要编造教材页码。"
)


async def compose_reply(
    *,
    situation: str,
    teacher_message: str | None = None,
    state: dict[str, Any] | None = None,
) -> str:
    """生成针对老师该句的 prose 回应（LLM 优先，确定性兜底；永不抛错）。"""
    if _llm_disabled():
        return _fallback(situation, teacher_message, state)
    try:
        from app.db.session import session_factory
        from app.llm.ai_runs import PostgresAiRunsSink
        from app.llm.gateway import LlmGateway

        lines = [_PROMPT_HEAD, f"老师的话：{quote_of(teacher_message, 400)}"]
        title = (state or {}).get("title")
        if title:
            lines.append(f"当前备课主题：{title}")
        focus = (state or {}).get("focus")
        if focus:
            lines.append(f"当前教学重点：{focus}")
        if situation == "question":
            lines.append("这句话是提问：请先直接回答这个问题（可给 1 个具体考法/理解角度），再轻轻带一句可落进方案。")
        elif situation == "feedback":
            lines.append("这句话是对现有方案的质疑：请先回应他的顾虑，再给 1~2 个可选调整方向（不要直接改方案）。")
        elif situation == "confirm":
            lines.append("这句话是确认继续：请简短确认，并说明接下来生成教学流程与课件、教案。")
        elif situation == "blueprint_proposal":
            lines.append("你刚按他的要求拟好了一份教学蓝图提案：请先回应他这句话，再请他看确认卡。")
        elif situation == "node_revision":
            lines.append("这句话是对某个教学环节的修改要求：你只调整了那一个环节，请简短确认改了什么。")
        elif situation == "selection_pending":
            # 选择卡前置 prose：先回应老师这句，再自然说明缺什么 + 随后附选择卡。
            missing = str((state or {}).get("missing") or "")
            if missing == "textbook":
                lines.append(
                    "这句话之后备课还缺教材：请先回应老师这句话，再自然地说明需要先定一本教材，"
                    "你会在随后附上一张教材选择卡。禁止使用「还差X才能继续备课」这类系统腔，"
                    "不要罗列具体选项（卡里已有）。"
                )
            else:
                lines.append(
                    "这句话之后备课还缺章节：请先回应老师这句话，再自然地说明需要在这一册里定一章，"
                    "你会在随后附上一张章节选择卡。禁止使用「还差X才能继续备课」这类系统腔，"
                    "不要罗列具体选项（卡里已有）。"
                )
            source_label = str((state or {}).get("source_label") or "")
            if source_label:
                lines.append(f"当前已选教材：{source_label}。")
            if (state or {}).get("book_changed"):
                lines.append("刚按老师的要求换了教材，原章节属于旧册、不能沿用。")
        elif situation == "selection_repeat":
            lines.append(
                "老师重复了同一个选择，当前备课选择没有变化，上一张选择卡仍在会话里、不再重发："
                "请简短确认已经记下他的选择，并引导他直接说出这节课的要求（知识点、例题或课时都行）。"
            )
        factory = session_factory()
        gateway = LlmGateway(PostgresAiRunsSink(factory))
        result = await gateway.chat(
            messages=[{"role": "user", "content": "\n".join(lines)}],
            use_case="teacher_prep_reply",
            temperature=0.4,
        )
        text = _clean_reply(getattr(result, "content", "") or "")
        return text or _fallback(situation, teacher_message, state)
    except Exception:  # noqa: BLE001 —— prose 失败绝不阻断主链路
        logger.warning("reply LLM failed, using deterministic fallback", exc_info=True)
        return _fallback(situation, teacher_message, state)


__all__ = ["compose_reply", "quote_of"]
