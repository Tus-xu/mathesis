"""迭代05 四个新工作流接线测试（阶段 1.9，审计 A-P0-5 / B-P1-5/6/7/8）

覆盖（SSOT §4.7~4.10 / 星辰指南 §4~§7 / ADR-009/034）：
1. FLOW_REGISTRY 注册完整（10 个工作流）+ 超时键名统一 wf_course_preprocess
2. 输出模型基线字段与 SSOT 一致
3. wf_smart_quiz 调用点：工作流优先 → 本地降级（切换不抛异常）
4. wf_solution_pregrade 调用点：工作流优先（score/summary/error_type 消费）
5. wf_error_analysis 调用点：工作流优先 → 五枚举校验
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.coursework import Quiz, QuizItem
from app.providers import xingchen as xc

# ==================== 注册表与输出模型 ====================


class TestWorkflowRegistry:
    def test_registry_has_10_flows(self):
        """FLOW_REGISTRY 含 10 个工作流（6 既有 + 4 新增，ADR-034）"""
        expected = {
            "wf_socratic_chat", "wf_doc_understand", "wf_speech_to_latex",
            "wf_web_search", "wf_intent_router", "wf_verify_derivation",
            "wf_smart_quiz", "wf_solution_pregrade", "wf_error_analysis", "wf_course_preprocess",
        }
        assert expected.issubset(set(xc.FLOW_REGISTRY.keys()))

    def test_timeout_key_renamed(self):
        """超时键名统一文档定名 wf_course_preprocess（不再用 wf_dual_teacher_preprocess）"""
        assert "wf_course_preprocess" in xc._DEFAULT_TIMEOUTS
        assert "wf_dual_teacher_preprocess" not in xc._DEFAULT_TIMEOUTS

    def test_output_models_baseline_fields(self):
        """输出模型基线字段与 SSOT §4.7~4.10 一致"""
        q = xc.SmartQuizOut(question_text="t", options=["A"], answer="a", explanation="e", kp_code="k", difficulty="easy")
        assert q.question_text == "t" and q.difficulty == "easy"
        p = xc.SolutionPregradeOut(score=7.0, error_type="calculation", step_comments=[], summary="s")
        assert p.score == 7.0 and p.error_type == "calculation"
        e = xc.ErrorAnalysisOut(error_type="formula", kp_code=None, confidence=0.8)
        assert e.error_type == "formula"
        c = xc.CoursePreprocessOut(chapters=[{"title": "ch1"}], kp_codes=["k"], knowledge_cards=[])
        assert c.chapters[0]["title"] == "ch1"

    def test_output_models_ignore_extra_fields(self):
        """扩展字段（本地透传）不破坏基线校验（extra=ignore）"""
        q = xc.SmartQuizOut(question_text="t", answer="a", sympy_check_code="print(True)", self_check={})
        assert q.question_text == "t"


# ==================== wf_smart_quiz 调用点 ====================


class TestBlankMultiValueGate:
    """填空多值答案 sympify 校验（迭代05 冒烟实测修复：2 或 -2 不得误拒）"""

    @pytest.mark.asyncio
    async def test_blank_multivalue_passes(self):
        from app.skills.smart_quiz.main import run_quiz_gates

        for answer in ("2 或 -2", "x=2 或 x=3", "4 或 5 或 6 或 7", "π/6 或 5π/6", "±2", "-\\frac{4}{5}", "$\\frac{2\\pi}{9}$"):
            passed, failures, _ = await run_quiz_gates(
                {"q_type": "blank", "question_text": "求值", "answer": answer, "difficulty": "easy"}
            )
            assert passed, f"多值填空被误拒: {answer} {failures}"

    @pytest.mark.asyncio
    async def test_blank_invalid_rejected(self):
        """非解析值（含非法符号）：M2 重构后仅记 warning note，不再判失败重出"""
        from app.skills.smart_quiz.main import run_quiz_gates

        passed, failures, notes = await run_quiz_gates(
            {"q_type": "blank", "question_text": "求值", "answer": "??123abc!!", "difficulty": "easy"}
        )
        assert passed
        assert any("sympify" in n for n in notes)

    @pytest.mark.asyncio
    async def test_blank_interval_descriptive_allowed(self):
        """区间/描述性填空答案（x∈(0,1)/x≠0/纯字母描述）跳过 sympify 校验不误拒（冒烟实测）"""
        from app.skills.smart_quiz.main import run_quiz_gates

        for answer in ("x∈(0,1)", "x≠0", "no solution", "单调递增", "30°", "90° 或 270°", "x≤2", "±3", "2√2"):
            passed, failures, notes = await run_quiz_gates(
                {"q_type": "blank", "question_text": "求范围", "answer": answer, "difficulty": "hard"}
            )
            assert passed, f"区间/描述性填空被误拒: {answer} {failures}"
            # 非中文答案应记录跳过 note；中文答案走文本型跳过（无 note 也合法）
            if not any("一" <= ch <= "鿿" for ch in answer):
                assert any("跳过" in n or "sympify" in n for n in notes), f"应记录跳过 note: {answer}"


def _quiz(user_id) -> Quiz:
    return Quiz(user_id=user_id, source="ai_generated", title="t", kp_codes=["MATH-G1-TRIG-001"])


class TestSmartQuizCallSite:
    @pytest.mark.asyncio
    async def test_workflow_preferred(self):
        """星辰开启且工作流有效 → 使用工作流输出（explanation→answer_analysis 映射）"""
        from app.gateway import student_router as sr

        wf_out = {
            "question_text": "求 sin 30°",
            "options": ["A. 1/2", "B. 1", "C. 0", "D. -1"],
            "answer": "A",
            "explanation": "sin 30° = 1/2",
            "difficulty": "easy",
        }
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)) as m:
            data = await sr._generate_one_quiz_item(
                None, _quiz(uuid.uuid4()), "MATH-G1-TRIG-001", "三角函数", "easy", "choice"
            )
        m.assert_awaited_once()
        assert data["question_text"] == "求 sin 30°"
        assert data["answer_analysis"] == "sin 30° = 1/2"  # explanation → answer_analysis
        assert data["difficulty"] == "easy"

    @pytest.mark.asyncio
    async def test_workflow_failure_falls_back_local(self):
        """工作流抛异常 → 降级本地 generate_quiz_item（切换不抛异常，前端无感知）"""
        from app.gateway import student_router as sr

        # 本地输出需过质量四闸（迭代05 阶段3 闭环：生成后必过闸）
        local_out = ({
            "q_type": "choice",
            "question_text": "本地题：$1+1$ 的值是？",
            "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
            "answer": "B",
            "difficulty": "easy",
        }, "{}")
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(side_effect=RuntimeError("星辰挂了"))), \
             patch.object(sr, "generate_quiz_item", new=AsyncMock(return_value=local_out)) as m:
            data = await sr._generate_one_quiz_item(
                object(), _quiz(uuid.uuid4()), "k", "kp", "easy", "choice"
            )
        m.assert_awaited_once()
        assert data["question_text"] == "本地题：$1+1$ 的值是？"

    @pytest.mark.asyncio
    async def test_gate_failure_retries_then_raises(self):
        """质量闸未过 → 携失败原因重试 2 次（共 3 次生成），仍未过抛 QuizGenerationError（B-P1-8）"""
        from app.gateway import student_router as sr

        # 缺选项的 choice 题 → 闸 1 必失败
        bad_out = ({"q_type": "choice", "question_text": "题", "answer": "A"}, "{}")
        with patch.object(settings, "xingchen_enabled", False), \
             patch.object(sr, "generate_quiz_item", new=AsyncMock(return_value=bad_out)) as m, \
             pytest.raises(sr.QuizGenerationError):
            await sr._generate_one_quiz_item(
                object(), _quiz(uuid.uuid4()), "k", "kp", "easy", "choice"
            )
        assert m.await_count == 3  # 首次 + 重试 2 次

    @pytest.mark.asyncio
    async def test_xingchen_disabled_direct_local(self):
        """星辰关闭 → 直接本地路径（不调 run_workflow）"""
        from app.gateway import student_router as sr

        local_out = ({
            "q_type": "choice",
            "question_text": "本地题：$2+2$ 的值是？",
            "options": ["A. 2", "B. 3", "C. 4", "D. 5"],
            "answer": "C",
        }, "{}")
        with patch.object(settings, "xingchen_enabled", False), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock()) as wf, \
             patch.object(sr, "generate_quiz_item", new=AsyncMock(return_value=local_out)):
            await sr._generate_one_quiz_item(object(), _quiz(uuid.uuid4()), "k", "kp", "easy", "choice")
        wf.assert_not_awaited()


# ==================== wf_solution_pregrade 调用点 ====================


class TestPregradeCallSite:
    @pytest.mark.asyncio
    async def test_workflow_pregrade_consumed(self):
        """工作流返回 score/summary/error_type → pending_review + ai_pregraded"""
        from app.gateway import student_router as sr

        qi = QuizItem(
            quiz_id=uuid.uuid4(), item_no=1, q_type="solution",
            question_text="证明题", answer="标答", answer_analysis="解析",
        )
        wf_out = {"score": 7.0, "error_type": "logic", "step_comments": [], "summary": "思路有误"}
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)):
            verdict, score, extra = await sr._ai_pregrade_solution(qi, "学生作答", user_id="u1")
        assert verdict == "pending_review"
        assert score == 7.0
        assert extra["ai_pregraded"] is True
        assert extra["error_type"] == "logic"
        assert extra["comment"] == "思路有误"

    @pytest.mark.asyncio
    async def test_workflow_invalid_error_type_null(self):
        """工作流 error_type 非法 → 置 None（五枚举严格校验）"""
        from app.gateway import student_router as sr

        qi = QuizItem(quiz_id=uuid.uuid4(), item_no=1, q_type="solution", question_text="q", answer="a")
        wf_out = {"score": 5.0, "error_type": "strategy", "summary": ""}  # strategy 为历史非法值
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)):
            _, _, extra = await sr._ai_pregrade_solution(qi, "作答", user_id="u1")
        assert extra["error_type"] is None


# ==================== wf_error_analysis 调用点 ====================


class TestErrorAnalysisCallSite:
    @pytest.mark.asyncio
    async def test_workflow_error_type_returned(self):
        from app.gateway import student_router as sr

        wf_out = {"error_type": "formula", "kp_code": None, "confidence": 0.9}
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)):
            result = await sr._judge_error_type("题干", "作答", user_id="u1")
        assert result == "formula"

    @pytest.mark.asyncio
    async def test_workflow_uncertain_returns_none(self):
        """工作流拿不准（非法枚举）→ None，学生可手动选择（红线）"""
        from app.gateway import student_router as sr

        wf_out = {"error_type": "unknown", "confidence": 0.3}
        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(return_value=wf_out)):
            result = await sr._judge_error_type("题干", None, user_id="u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_workflow_failure_falls_back_local(self):
        """工作流异常 → 本地分类降级（mock LLM 返回 calculation）"""
        from app.gateway import student_router as sr

        class _FakeRouter:
            async def chat(self, **kwargs):
                return {"content": "calculation"}

        with patch.object(settings, "xingchen_enabled", True), \
             patch("app.providers.xingchen.run_workflow", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch.object(sr, "get_model_router", return_value=_FakeRouter()):
            result = await sr._judge_error_type("题干", "作答", user_id="u1")
        assert result == "calculation"
