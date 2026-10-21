"""共享解答题 AI 初批管线（tel D9：自 student_router._ai_pregrade_solution 行为保持抽取）。

双端复用：
- 学生自练路径（student_router._grade_item → 本模块）：AI 初批留痕，verdict=pending_review
  待教师确认；LLM 不可用降级，不伪造分数。
- 教师批改台建议分（domains/teacher/grading.suggest_grade，显式 POST 触发）：
  同步调用本管线，失败/超时/输出非法降级 0.0/0.3/manual_review。

行为保持说明（抽取回归守卫 = tests/test_iter05_student.py 15 例全绿）：
- 星辰 wf_solution_pregrade 优先（SSOT §4.8）→ 本地 ModelRouter JSON 直调降级；
- 输入裁剪 / 分数 clamp / error_type 白名单 / 日志事件名与抽取前逐字一致；
- 成功 extra 新增 ``confidence``（管线置信，供教师建议分展示；学生路径忽略该键）。

测试铁律：测试禁止真实 LLM/外网——本模块是评分域唯一 AI 入口，monkeypatch
``app.services.solution_pregrade.pregrade_solution`` 即可 stub 全部 AI 行为。
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coursework import QuizItem
from app.providers.router import get_model_router
from app.skills.smart_quiz.main import parse_quiz_json

logger = structlog.get_logger(__name__)

# 与 student_router._ITEM_SCORE 同源：quiz_items 无分值字段的 M2 约定（10 分制）
_ITEM_SCORE = 10.0
# 错因五枚举（ADR-M2B-002），与 student_router._VALID_ERROR_TYPES 同源
_VALID_ERROR_TYPES = {"concept", "formula", "calculation", "logic", "reading"}

# 解答题 AI 初批 prompt（输出 JSON：score/max_score/comment/error_type）
# 与抽取前 student_router._SOLUTION_GRADE_PROMPT 逐字一致。
_SOLUTION_GRADE_PROMPT = """\
你是数学阅卷老师。请为学生的解答题作答按步骤评分。

【题目】
{question}

【标准答案与解析】
{reference}

【学生作答】
{student}

【评分要求】
- 满分 {max_score} 分，按解题步骤给分（步骤对给步骤分，终答错扣终答分）
- error_type 只能是 concept/formula/calculation/logic/reading 之一或 null（无错）
- 只输出 JSON，不要其他文字

【输出格式】
{{"score": 数字, "max_score": {max_score}, "comment": "100字内评语", "error_type": null}}
"""

# AI 初批成功解析后的管线置信（教师建议分展示用；学生留痕路径不消费）
_PIPELINE_CONFIDENCE = 0.7


async def pregrade_solution(
    quiz_item: QuizItem | None,
    answer_text: str | None,
    user_id: str = "",
    db: AsyncSession | None = None,
    question_text: str | None = None,
    expected_answer: str = "",
    max_score: float | None = None,
    allow_xingchen: bool = True,
) -> tuple[str, float | None, dict]:
    """解答题 AI 初批（留痕待教师确认）

    迭代05 1.9 接线：wf_solution_pregrade 优先（SSOT §4.8）→ 本地直调降级；
    LLM 不可用/输出非法 → pending_review 占位降级（不伪造分数）。
    db 传入时按三层解析有效配置（管理后台配置即时生效），缺省走 env。
    quiz_item 为 None（对话内 AI 出题）时，题干/参考答案回退到 question_text / expected_answer。
    max_score：卷型满分（模拟试卷按分值规格；None 回退 10 分制）。
    allow_xingchen：False 时跳过星辰 wf_solution_pregrade，直接本地预评分
    （Butler 本地降级路径使用，避免降级链再次进入星辰）。
    """
    item_max = max_score if max_score is not None and max_score > 0 else _ITEM_SCORE
    if not answer_text or not answer_text.strip():
        return "wrong", 0.0, {}
    q_text = quiz_item.question_text if quiz_item else (question_text or "")
    reference = (
        f"{quiz_item.answer}\n{quiz_item.answer_analysis or ''}".strip()
        if quiz_item
        else (expected_answer or "")
    )
    if not q_text or not reference:
        return "pending_review", None, {"degraded": "no_reference"}

    # 星辰 wf_solution_pregrade 优先（allow_xingchen=False 时本地降级不再进星辰）
    from app.providers.xingchen import resolve_effective_xingchen_config

    xcfg = await resolve_effective_xingchen_config(db, user_id or None)
    if xcfg.enabled and allow_xingchen:
        try:
            from app.providers.xingchen import run_workflow

            wf_result = await run_workflow(
                "wf_solution_pregrade",
                uid=user_id or "system",
                parameters={
                    "AGENT_USER_INPUT": "请批改这道解答题",
                    "question": q_text[:2000],
                    "reference": reference[:2000],
                    "student_answer": answer_text.strip()[:2000],
                    "max_score": str(int(item_max)),
                },
                config=xcfg,
            )
            wf_score = wf_result.get("score")
            if wf_score is not None:
                wf_error_type = wf_result.get("error_type")
                if wf_error_type not in _VALID_ERROR_TYPES:
                    wf_error_type = None
                return (
                    "pending_review",
                    max(0.0, min(float(wf_score), item_max)),
                    {
                        "ai_pregraded": True,
                        "comment": str(wf_result.get("summary") or "")[:500],
                        "error_type": wf_error_type,
                        "max_score": item_max,
                        "confidence": _PIPELINE_CONFIDENCE,
                    },
                )
            logger.info("wf_solution_pregrade_invalid_output_fallback")
        except Exception as e:
            logger.info("wf_solution_pregrade_fallback_local", error=str(e)[:150])

    # 本地降级直调
    prompt = _SOLUTION_GRADE_PROMPT.format(
        question=q_text,
        reference=reference,
        student=answer_text.strip(),
        max_score=int(item_max),
    )
    try:
        router = get_model_router()
        result = await router.chat(
            messages=[
                {"role": "system", "content": "你是严谨的数学阅卷老师，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1500,
            request_id=f"pregrade-{uuid.uuid4().hex[:12]}",
            scene="ai_pregrade",
        )
        data = parse_quiz_json(result.get("content", ""))
        if not data or "score" not in data:
            raise ValueError("AI 初批 JSON 解析失败")
        max_score_val = float(data.get("max_score") or item_max)
        if max_score_val <= 0:
            max_score_val = item_max
        score = max(0.0, min(float(data.get("score") or 0.0), max_score_val))
        error_type = data.get("error_type")
        if error_type not in _VALID_ERROR_TYPES:
            error_type = None
        # 留痕：AI 初批分数入 score，verdict=pending_review 待教师确认
        return (
            "pending_review",
            score,
            {
                "ai_pregraded": True,
                "comment": str(data.get("comment", ""))[:500],
                "error_type": error_type,
                "max_score": max_score_val,
                "confidence": _PIPELINE_CONFIDENCE,
            },
        )
    except Exception as e:
        logger.warning("ai_pregrade_degraded", error=str(e)[:200])
        return "pending_review", None, {"degraded": "llm_unavailable"}
