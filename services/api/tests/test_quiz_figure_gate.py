"""出题配图归属回归（2026-10-21）。

用户反馈：纯计算变式的上方出现一张原题的分段函数图，且点“动态演示”会生成一张
无动画、与题目无关的 GeoGebra 画板。根因是 smart_quiz 把 figure 作为独立事件
在题卡之前发射，并把原题图的视觉描述注入每道变式。

本测试锁定修复后的契约：
1. 纯计算题不触发出题配图（LLM 不得被调用）；
2. 几何/图象题生成 item.figure，role=question，且不带 step_no（不是讲解步骤）；
3. 配图随题卡下发，前端渲染在题干内部。
"""

import asyncio
import json

from app.skills.smart_quiz.main import SmartQuizExecutor, _quiz_question_needs_figure


class _LLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0

    async def chat(self, **_kwargs):
        self.calls += 1
        return {"content": self.content}


class _Ctx:
    request_id = "test-quiz-figure"

    def __init__(self, content: str = ""):
        self.llm = _LLM(content)


def _plan() -> str:
    return json.dumps(
        [
            {
                "step": 1,
                "caption": "抛物线开口与对称轴",
                "figure": {
                    "version": 1,
                    "type": "function",
                    "params": {
                        "curves": [{"expr": "x**2-2*x", "label": "y=x²-2x", "domain": [-2, 4]}],
                        "x_range": [-2, 4],
                        "y_range": [-2, 8],
                    },
                },
            }
        ],
        ensure_ascii=False,
    )


def test_figure_gate_distinguishes_computation_from_visual_question():
    assert _quiz_question_needs_figure("已知函数 f(x)=3x^2-2x+1，求 f'(2)") is False
    assert _quiz_question_needs_figure("如图，正方体 ABCD-A1B1C1D1 的棱长为 2") is True
    assert _quiz_question_needs_figure("函数 y=f(x) 的图象大致为（  ）") is True


def test_pure_computation_never_calls_figure_planner():
    executor = SmartQuizExecutor()
    ctx = _Ctx(_plan())
    payload = asyncio.run(
        executor._quiz_figure_payload(
            "已知函数 f(x)=3x^2-2x+1，求 f'(2)",
            "求导后代入 x=2。",
            ctx,
        )
    )
    assert payload is None
    assert ctx.llm.calls == 0


def test_visual_question_figure_is_question_role_without_step_badge():
    executor = SmartQuizExecutor()
    ctx = _Ctx(_plan())
    payload = asyncio.run(
        executor._quiz_figure_payload(
            "如图，抛物线 y=x²-2x 与 x 轴交于 A、B 两点，求 AB 的长度。",
            "第1步：令 y=0 求交点。",
            ctx,
        )
    )
    assert payload is not None
    assert payload["role"] == "question"
    assert "step_no" not in payload
    assert payload["frames"], "题目配图必须带可渲染帧"
