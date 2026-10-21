"""Routing regressions from the demo accounts review (2026-10-21).

1. A file name like `三角函数恒等变换_错题整理.md` must not trigger the
   "open error book" page intent merely because it contains 「错题」+「整理」.
2. "先诊断薄弱点，再出变式" is a topic request, not a user-pasted problem;
   it must not enter the "based on your previous question" variant chain.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.kernel.precheck import run_precheck
from app.kernel.router import IntentRouter, RouteDecision
from app.services.platform_context import match_platform_item
from app.skills.base import SkillContext
from app.skills.chat.main import ChatSkill
from app.skills.smart_quiz.main import SmartQuizExecutor

DOC_MESSAGE = (
    "请基于我上传的资料《三角函数恒等变换_错题整理.md》帮我梳理核心知识点，"
    "再用苏格拉底式提问引导我理解。"
)
DIAGNOSE_VARIANT_MESSAGE = (
    "请针对「函数单调性与极值」先诊断我的薄弱环节，再出 3 道难度递进的变式题带我巩固。"
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def test_filename_words_do_not_trigger_page_intent():
    assert match_platform_item(DOC_MESSAGE) is None
    assert run_precheck(DOC_MESSAGE) is None


def test_explicit_open_error_book_still_works():
    item = match_platform_item("帮我打开错题本")
    assert item is not None
    assert item["key"] == "error-book"


def test_content_task_with_explicit_navigation_keeps_page_intent():
    item = match_platform_item("打开错题本，帮我整理一下资料里的错题")
    assert item is not None and item["key"] == "error-book"


def test_diagnosis_plus_variant_is_not_a_pasted_question():
    assert SmartQuizExecutor._detect_user_variant(DIAGNOSE_VARIANT_MESSAGE) is False


def test_real_question_plus_variant_is_still_detected():
    assert SmartQuizExecutor._detect_user_variant("举一反三：已知函数 $f(x)=x^2$，求其导数") is True


async def test_personal_kb_guard_beats_l0_socratic():
    """「我上传的资料 + 苏格拉底」必须先归 chat，不能直通 socratic（读不到文件）。"""
    router = IntentRouter()
    router._check_l0 = lambda message, surface: RouteDecision(  # type: ignore[method-assign]
        skill_id="socratic_solver",
        confidence=0.99,
        params={"question": message},
    )
    decision = await router.route(DOC_MESSAGE, db=AsyncMock(), user_id=str(uuid.uuid4()))
    assert decision.skill_id == "chat"


async def test_kb_content_request_injects_real_chunks():
    doc = SimpleNamespace(
        id=uuid.uuid4(),
        title="三角函数恒等变换_错题整理.md",
        status="ready",
        meta_={"category": "笔记"},
        created_at="2026-10-21 15:00:00",
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Scalars([doc]),
            _Scalars(["同角三角函数的基本关系：sin²α+cos²α=1", "诱导公式：奇变偶不变，符号看象限"]),
        ]
    )
    ctx = SkillContext(
        user_id=str(uuid.uuid4()),
        user_role="student",
        conversation_id=str(uuid.uuid4()),
        request_id="req-kb-content",
        db=db,
    )
    text = await ChatSkill()._butler_lookup(DOC_MESSAGE, ctx)
    assert "【管家查询：我的知识库资料内容】" in text
    assert "三角函数恒等变换_错题整理.md" in text
    assert "诱导公式" in text
    assert "苏格拉底" in text


async def test_quoted_topic_preserved_over_generic_function():
    """「函数单调性与极值」不能被词表里的「函数」降级成泛函数出题。"""
    from app.skills.smart_quiz.main import SmartQuizExecutor

    ctx = SkillContext(
        user_id=str(uuid.uuid4()),
        user_role="student",
        conversation_id=str(uuid.uuid4()),
        request_id="req-quoted-kp",
        db=AsyncMock(),
    )
    with patch(
        "app.skills.smart_quiz.main.load_kp_map",
        new=AsyncMock(return_value={"function": "函数", "derivative": "导数的概念与运算"}),
    ):
        code, name, *_ = await SmartQuizExecutor()._parse_request(DIAGNOSE_VARIANT_MESSAGE, ctx)
    assert name == "函数单调性与极值"
    assert code == "function"
