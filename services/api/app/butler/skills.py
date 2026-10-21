"""管家技能（Butler Skills）— 规则骨架 + LLM 润色（M2 迭代17）

范式（对齐方案 §4.4）：规则保证数据正确与前置依赖，LLM 只润色/编排文案，
数字一律来自规则骨架，LLM prompt 强约束"只润色不改数字"。

每个技能：计算规则骨架 → 调 butler.llm.generate / copy_polish 润色 → 返回
{dict}。任何异常回退规则模板（generate/polish 内部已兜底，永不抛）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler import llm as butler_llm
from app.butler.tools import (
    query_due_errors,
    query_profile,
    query_weak_points,
    recommend_path,
)
from app.models.coursework import ErrorRecord
from app.models.knowledge_point import KnowledgePoint
from app.services import growth as growth_svc
from app.services.error_record_assets import has_usable_figure, normalize_error_assets
from app.services.geogebra_figure import build_ggb_payload, generate_ggb, resolve_image_data_uri

logger = structlog.get_logger(__name__)

# 管家人格统一"小婷"（对齐方案 §4.7），system prompt 强约束不编造数字
_PERSONA = (
    "你是学生的学习管家「小婷」。用亲切自然的第二人称（你）口吻，"
    "只基于给定数据表达，不得编造、修改或增删任何数字与事实。"
    "不替代思考、不直接给答案、不制造焦虑、不评判人格。"
)


# ==================== 今日 3 件事 ====================


async def daily_plan(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """今日 3 件事：规则骨架一次构建（FSRS 到期 + 薄弱 Top1 + 打卡维持）→ 整页最多一次 LLM 润色。

    性能护栏（阶段 1 Task 2）：禁止逐卡串行润色（曾 3 卡 × 3 字段 = 9 次串行模型调用，
    整页 33 秒）。现卡片全部使用规则数据（数字来自领域服务，不依赖 LLM），
    仅开场白一次 LLM 润色（失败回退模板），整页模型调用 <= 1 次。
    """
    due = await query_due_errors(db, user_id, limit=10)
    weak = await query_weak_points(db, user_id, limit=2)
    profile = await query_profile(db, user_id)

    top_weak = weak[0] if weak else None
    streak = profile["streak_days"]
    due_n = len(due)

    # 规则骨架三件事（一次构建；title/why/benefit 直接为规则值，禁止卡片级 LLM 润色）
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
            "why": f"掌握度 {round(top_weak['mastery'] * 100)}%，当前最弱" if top_weak else "先摸底建立基线",
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

    # 开场白：最多一次 LLM 润色（generate 内部失败回退模板；再加防御层，模型异常不阻塞页面）
    try:
        greeting = await proactive_greeting(db, user_id, profile)
    except Exception as e:  # noqa: BLE001
        logger.warning("daily_plan_greeting_fallback", user_id=str(user_id), error=str(e)[:150])
        greeting = (
            f"今天你有 {due_n} 道错题该复习了，最薄弱的是「{top_weak['kp_name']}」，"
            "建议先用 15 分钟定点突破，要不要现在开始？"
            if (due_n or top_weak)
            else "今天想从哪开始？练题、复习错题，还是做套模拟卷？"
        )

    return {"tasks": tasks, "greeting": greeting}


# ==================== 主动开场白 ====================


async def proactive_greeting(db: AsyncSession, user_id: uuid.UUID, profile: dict | None = None) -> str:
    """主动开场白：画像卡 + 今日 3 件事数据 → 一句自然的"今天做什么"。"""
    profile = profile or await query_profile(db, user_id)
    weak = profile["weak_points"][0]["kp_name"] if profile["weak_points"] else "当前进度"
    tpl = (
        f"今天你有 {profile['error_due']} 道错题该复习了，最薄弱的是「{weak}」，"
        f"建议先用 15 分钟定点突破，要不要现在开始？"
        if (profile["error_due"] or profile["weak_points"])
        else "今天想从哪开始？练题、复习错题，还是做套模拟卷？"
    )
    return await butler_llm.generate(
        scene=butler_llm.SCENE_PROACTIVE,
        system_prompt=_PERSONA + "生成一句不超过 60 字的开场白。",
        user_prompt=f"开场白模板：{tpl}。直接输出开场白。",
        fallback=tpl,
        user_id=user_id,
        data_fingerprint=f"greeting|{profile.get('error_due')}|{weak}",
        max_tokens=120,
    )


# ==================== 周报 ====================


async def weekly_report(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """本周亮点 + 薄弱点 + 诚实提示 → 一段 150 字以内的"小婷的周报"。"""
    total, correct = await growth_svc.week_answer_stats(db, user_id, days=7)
    accuracy = round(correct / total * 100) if total else 0
    trend = await growth_svc.daily_mastery_avg(db, user_id, days=7)
    weak = await query_weak_points(db, user_id, limit=3)
    profile = await query_profile(db, user_id)

    # 本周亮点（规则骨架）：进步最快的知识点
    mastery_vals = [d["mastery"] for d in trend if d["mastery"] is not None]
    delta = round((mastery_vals[-1] - mastery_vals[0]) * 100) if len(mastery_vals) >= 2 else 0

    weak_names = "、".join(w["kp_name"] for w in weak) if weak else "暂无"
    data_block = (
        f"本周做题 {total} 道，正确率 {accuracy}%（{correct} 对），"
        f"掌握度均值周内变化 {delta:+d} 个百分点，"
        f"薄弱点：{weak_names}，综合分 {profile['composite_score']}，连续学习 {profile['streak_days']} 天。"
    )
    tpl = f"本周你做了 {total} 道题，正确率 {accuracy}%，继续保持。"
    narrative = await butler_llm.generate(
        scene=butler_llm.SCENE_WEEKLY_REPORT,
        system_prompt=_PERSONA + "写一段 150 字以内的周报总结，先肯定进步，再温和点出薄弱点与下周建议。",
        user_prompt=f"真实数据：{data_block}\n请基于这些数据写周报（不要改动任何数字）。",
        fallback=tpl,
        user_id=user_id,
        data_fingerprint=data_block,
        max_tokens=400,
    )

    return {
        "narrative": narrative,
        "data": {
            "answer_count": total,
            "accuracy": accuracy,
            "mastery_delta": delta,
            "weak_points": weak,
            "composite_score": profile["composite_score"],
            "streak_days": profile["streak_days"],
        },
    }


# ==================== 错因解读 ====================


async def error_diagnosis(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID) -> dict:
    """AI 错因诊断：12 类思维漏洞归类 → 根因 + 记忆口诀 + 补救建议。"""
    record = await db.get(ErrorRecord, record_id)
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"error": "错题记录不存在"}

    subtype, subtype_zh, parent = growth_svc.classify_subtype(
        record.error_type, (record.question_text or "") + "\n" + (record.note or "")
    )
    kp_name = ""
    if record.kp_code:
        from app.models.knowledge_point import KnowledgePoint

        kp = await db.execute(
            select(KnowledgePoint).where(KnowledgePoint.code == record.kp_code)
        )
        kp_row = kp.scalar_one_or_none()
        kp_name = kp_row.name if kp_row else ""

    data_block = (
        f"错因类型：{subtype_zh}（{parent}）\n"
        f"知识点：{kp_name or record.kp_code or '未标注'}\n"
        f"题干：{(record.question_text or '')[:200]}\n"
        f"学生答案：{(record.answer_text or '未作答')[:100]}\n"
        f"学生自述错因：{(record.note or '未提供')[:200]}"
    )
    tpl = f"这题主要问题是{subtype_zh}。建议回到「{kp_name or '相关知识点'}」重新梳理概念，再做一道同类题检验。"
    text = await butler_llm.generate(
        scene=butler_llm.SCENE_ERROR_DIAGNOSIS,
        system_prompt=(
            _PERSONA
            + "做错题诊断。分三行输出：①根因（一句话，别只说错因标签）；②记忆口诀（一句可操作的提醒）；"
            "③补救建议（一句话）。总长不超过 100 字。"
            + "诚实纪律：只能依据给出的数据诊断。若学生答案缺失、无意义（如占位字母 X）或看不出思考过程，"
            "①必须如实说明'作答信息不足，无法定位具体错误过程'，"
            "严禁编造学生并未发生过的具体错误情节（如'代入时算错'）。"
        ),
        user_prompt=f"错题数据：{data_block}\n请基于数据做诊断（不要改动数字与事实；数据不足以定位就如实说明）。",
        fallback=tpl,
        user_id=user_id,
        data_fingerprint=f"diag|{record_id}|{len(record.note or '')}",
        max_tokens=300,
    )

    return {
        "record_id": str(record_id),
        "error_type": parent,
        "subtype": subtype,
        "subtype_zh": subtype_zh,
        "kp_code": record.kp_code,
        "kp_name": kp_name,
        "diagnosis": text,
    }


# ==================== 学习路径规划 ====================


async def path_plan(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """学习路径规划：薄弱点 TopN + 前置依赖规则骨架 → LLM 编排文案。"""
    path = await recommend_path(db, user_id, top_n=3)
    if not path:
        return {"path": [], "narrative": "先做几道题建立掌握度基线，我再为你规划路径。"}

    steps_desc = "\n".join(
        f"第{i + 1}步：{s['kp_name']}（掌握度 {round(s['mastery'] * 100)}%，前置 {s['prereqs'] or '无'}）"
        for i, s in enumerate(path)
    )
    tpl = f"建议先攻「{path[0]['kp_name']}」，再依次巩固后续薄弱点。"
    narrative = await butler_llm.generate(
        scene=butler_llm.SCENE_PATH_PLAN,
        system_prompt=_PERSONA + "解释这个学习顺序的安排理由，不超过 120 字。",
        user_prompt=f"学习路径（规则已保证前置依赖正确）：\n{steps_desc}\n请说明为什么这样安排。",
        fallback=tpl,
        user_id=user_id,
        data_fingerprint="|".join(s["kp_code"] for s in path),
        max_tokens=300,
    )

    return {"path": path, "narrative": narrative}


# ==================== 错题 AI 生成正解（解决"暂无正解文本"） ====================


async def build_solution_figure(db: AsyncSession, user_id: uuid.UUID, record: ErrorRecord) -> list[dict]:
    """正解示意图 best-effort 生成（仅图形语义题触发；任何失败返回空列表不抛出）。

    复用错题动态图形同一内核（generate_ggb：视觉通道看原图 → 文本通道按题干构造），
    但默认静态、不加滑杆动画——正解图是讲解配图，不是交互教具。
    """
    try:
        image_data_uri = (
            await resolve_image_data_uri(db, record.file_id, user_id) if record.file_id else None
        )
        ggb = await generate_ggb(
            record.question_text,
            figure_hint=record.note,
            interactive=False,
            image_data_uri=image_data_uri,
            user_id=str(user_id),
            db=db,
        )
        if not ggb:
            return []
        payload = build_ggb_payload(ggb["commands"], ggb["view"], caption="正解示意图")
        return normalize_error_assets([payload], alt="正解示意图")
    except Exception as e:  # noqa: BLE001 —— 图形失败只降级为空图，不影响正解保存
        logger.warning("butler.solution_figure_failed", record_id=str(record.id), error=str(e)[:200])
        return []


async def error_detail(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID) -> dict:
    """错题 AI 详情：原题 + 学生答案 + 错因 + AI 生成正解（Khanmigo 风格的完整解答）。

    om8 缓存语义：正解首次生成后持久化到 error_records.generated_answer，之后命中
    缓存直接返回（cached=True，零模型调用）。行锁（with_for_update）串行化并发打开
    同一错题的两个请求——后到者在锁上等待，醒来时读到前者提交的缓存。
    图形 best-effort：失败只存空 solution_figure，绝不回滚已生成的正解。
    """
    record = await db.get(ErrorRecord, record_id, with_for_update=True)
    if record is None or record.deleted_at or record.user_id != user_id:
        await db.commit()  # 释放行锁（只读未改，commit 即解锁）
        return {"error": "错题记录不存在"}

    kp_name = ""
    if record.kp_code:
        kp = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == record.kp_code))
        kp_row = kp.scalar_one_or_none()
        kp_name = kp_row.name if kp_row else ""

    subtype, subtype_zh, parent = growth_svc.classify_subtype(record.error_type, record.question_text or "")

    # 命中缓存：不调模型、不重生成图形（学生主动重生成走 /error-records/{id}/figure）
    if record.generated_answer:
        await db.commit()  # 释放行锁
        return {
            "record_id": str(record_id),
            "kp_code": record.kp_code,
            "kp_name": kp_name,
            "error_type": parent,
            "subtype": subtype,
            "subtype_zh": subtype_zh,
            "question_text": record.question_text or "",
            "student_answer": record.answer_text or "",
            "generated_answer": record.generated_answer,
            "solution_figure": normalize_error_assets(record.solution_figure, alt="正解示意图"),
            "cached": True,
            "source_channel": record.source_channel,
            "wrong_count": int(record.wrong_count or 1),
            "review_count": int(record.review_count or 0),
        }

    tpl = (
        f"本题属于「{kp_name or '数学'}」相关知识。\n"
        f"解答：先明确已知条件，再利用{kp_name or '相关定理'}分步推导，最后验算。\n"
        f"答案：由具体计算得出。"
    )
    data_block = (
        f"题目：{(record.question_text or '')[:500]}\n"
        f"学生答案：{(record.answer_text or '未作答')[:200]}\n"
        f"错因类型：{subtype_zh}\n"
        f"关联知识点：{kp_name or record.kp_code or '未关联'}"
    )
    text = await butler_llm.generate(
        scene=butler_llm.SCENE_ERROR_DETAIL,
        system_prompt=(
            "你是一位高中数学老师。根据题目、错因类型、关联知识点，给出本题的完整解答。"
            "格式：①分步骤写出推理过程（每步一行，1) 2) 3)）；②最终答案。"
            "要求：严格按真实数学推理，不得编造数字/公式；不超 500 字。"
        ),
        user_prompt=f"真实数据：{data_block}\n请基于这些数据给出完整解答。",
        fallback=tpl,
        user_id=user_id,
        data_fingerprint=f"detail|{record_id}",
        max_tokens=600,
    )

    # 正解示意图：仅"已有原图/含图形语义"的题触发（普通代数题不加装饰图）；
    # 生成抛错 → 降级空图继续保存正解（图形失败不回滚正解，设计红线）
    solution_figure: list = []
    if has_usable_figure(record.question_text or "", normalize_error_assets(getattr(record, "image", None))):
        try:
            solution_figure = await build_solution_figure(db, user_id, record)
        except Exception as e:  # noqa: BLE001
            logger.warning("butler.solution_figure_error", record_id=str(record_id), error=str(e)[:200])
            solution_figure = []

    # 原子写回：文本 + 图形 + 时间同一事务；图形失败已降级为 []，此处必成功
    record.generated_answer = text
    record.solution_figure = solution_figure
    record.solution_generated_at = datetime.now(UTC)
    await db.commit()

    return {
        "record_id": str(record_id),
        "kp_code": record.kp_code,
        "kp_name": kp_name,
        "error_type": parent,
        "subtype": subtype,
        "subtype_zh": subtype_zh,
        "question_text": record.question_text or "",
        "student_answer": record.answer_text or "",
        "generated_answer": text,
        "solution_figure": normalize_error_assets(solution_figure, alt="正解示意图"),
        "cached": False,
        "source_channel": record.source_channel,
        "wrong_count": int(record.wrong_count or 1),
        "review_count": int(record.review_count or 0),
    }


# ==================== 错题 AI 答疑（Khanmigo 苏格拉底引导式 chat） ====================


async def error_tutor(
    db: AsyncSession,
    user_id: uuid.UUID,
    record_id: uuid.UUID,
    student_message: str,
    history: list | None = None,
) -> dict:
    """错题 AI 答疑：苏格拉底引导式 chat，**强约束不直给答案**（Khanmigo 模式）。"""
    record = await db.get(ErrorRecord, record_id)
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"error": "错题记录不存在"}

    kp_name = ""
    if record.kp_code:
        kp = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == record.kp_code))
        kp_row = kp.scalar_one_or_none()
        kp_name = kp_row.name if kp_row else ""

    subtype, subtype_zh, parent = growth_svc.classify_subtype(record.error_type, record.question_text or "")

    history_text = ""
    if history:
        for h in history[-6:]:  # 最近 6 轮，避免上下文过长
            role = "学生" if h.get("role") == "user" else "老师"
            content = (h.get("content") or "")[:300]
            history_text += f"{role}：{content}\n"

    system_prompt = (
        "你是 Khanmigo 风格的数学导师，用苏格拉底引导法教学生。\n"
        "严格规则（不可违反）：\n"
        "1. 即使学生明确索要答案（如「直接告诉我吧」），**绝对不要直接给答案**——用「我想帮你独立解决这个问题，我们先从你已经知道的开始」巧妙引导。\n"
        "2. 每次回复先问「你目前是怎么想的？」确认学生思路。\n"
        "3. 学生卡住时，拆成更小的一步，只给推进这一步的最小提示。\n"
        "4. 学生答错时，不要直接说「错」，指出「哪一步值得再检查」，问一个能让学生自己发现问题的反问。\n"
        "5. 学生连续两次没思路时，给一个类似但更简单的例题先做。\n"
        "6. 学生做对时，让学生用自己的话解释为什么对（费曼检验），再继续。\n"
        "7. **每轮回复不超过 5 句话**——导师话少，学生话多。"
    )

    data_block = (
        f"题目：{(record.question_text or '')[:400]}\n"
        f"学生答案：{(record.answer_text or '未作答')[:200]}\n"
        f"错因类型：{subtype_zh}\n"
        f"关联知识点：{kp_name or record.kp_code or ''}"
    )
    user_prompt = (
        f"{data_block}\n\n历史对话：\n{history_text}\n学生最新说：{(student_message or '')[:300]}"
    )

    reply = await butler_llm.generate(
        scene=butler_llm.SCENE_ERROR_TUTOR,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        fallback="我们先从你已经知道的开始。这道题，你觉得应该先处理哪一步？为什么？",
        user_id=user_id,
        data_fingerprint=f"tutor|{record_id}|{len(history) if history else 0}|{(student_message or '')[:50]}",
        max_tokens=300,
    )

    return {"record_id": str(record_id), "tutor_reply": reply}
