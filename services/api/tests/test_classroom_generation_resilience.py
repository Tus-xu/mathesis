"""双师课堂生成韧性回归：AI 短暂失败不得把学生留在空白或无限等待中。"""

import json

from app.domains.classroom import math_verifier
from app.domains.classroom.stage_router import _fallback_practice, _gen_practice


async def test_practice_retries_transient_ai_failure_before_returning_questions(monkeypatch):
    class Router:
        def __init__(self):
            self.calls = 0

        async def chat(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary provider outage")
            return {
                "content": json.dumps(
                    {
                        "basic": [{"question": "基础题", "options": ["A", "B", "C", "D"], "answer": "A", "analysis": "解析"}],
                        "advanced": [{"question": "进阶题", "options": ["A", "B", "C", "D"], "answer": "B", "analysis": "解析"}],
                        "challenge": [{"question": "挑战题", "options": ["A", "B", "C", "D"], "answer": "C", "analysis": "解析"}],
                    },
                    ensure_ascii=False,
                )
            }

    router = Router()
    monkeypatch.setattr("app.providers.router.get_model_router", lambda: router)
    async def no_sleep(*_args):
        return None

    monkeypatch.setattr("app.domains.classroom.stage_router.asyncio.sleep", no_sleep)

    practice = await _gen_practice("函数单调性", [], [], per_tier=1)

    assert router.calls == 2
    assert practice["basic"][0]["question"] == "基础题"
    assert practice["challenge"][0]["answer"] == 2


def test_fallback_practice_is_renderable_for_all_tiers_when_ai_is_unavailable():
    practice = _fallback_practice("椭圆的标准方程", [{"title": "定义与焦点"}], [])

    assert set(practice) == {"basic", "advanced", "challenge"}
    assert all(practice[tier] for tier in practice)
    assert all(len(practice[tier][0]["options"]) == 4 for tier in practice)
    assert all(0 <= practice[tier][0]["answer"] <= 3 for tier in practice)


def test_verifier_exception_degrades_to_needs_review_instead_of_failing_page(monkeypatch):
    """数学验证器自身出错时，页面生成必须继续走可恢复状态。"""
    def raise_verifier_error(*_args, **_kwargs):
        raise RuntimeError("simulated verifier failure")

    monkeypatch.setattr(math_verifier, "_verify_slide_impl", raise_verifier_error)

    result = math_verifier.verify_slide({"blocks": [{"kind": "text", "text": "正常讲解"}]})

    assert result["status"] == "needs_review"
    assert "数学校验器异常" in result["detail"]
