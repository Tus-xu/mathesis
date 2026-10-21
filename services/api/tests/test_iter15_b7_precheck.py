"""迭代15 B7 · L1-1 全局状态机预检管线 + L1-3 路由确定性 回归测试

覆盖：
- 预检优先级：情绪 > 考试 > 变式（情绪求助永远最先响应）
- 情绪词库：强痛苦无条件命中；轻度受挫仅在无任务内容时命中（不劫持解题）
- 考试/变式既有行为不变（迁入内核后回归）
- 同句多次预检结果完全一致（L1-3 确定性）
- 澄清兜底选项候选感知（六分类确认式兜底）
"""

from app.kernel.precheck import detect_emotion, run_precheck
from app.kernel.router import IntentRouter, RouteDecision

# ------------------------------------------------------------------ #
# 情绪阶段（L1-5 种子：词库规则先行）
# ------------------------------------------------------------------ #


class TestEmotionStage:
    def test_strong_distress_always_wins(self):
        """强痛苦信号：即使带任务内容也优先响应情绪（先处理人再处理题）"""
        assert detect_emotion("我不想学了，压力好大") == "焦虑"
        assert detect_emotion("考砸了，感觉自己没救了") == "挫败"
        assert detect_emotion("崩溃了") == "低落"

    def test_mild_frustration_only_without_task(self):
        """轻度受挫：无任务内容时是情绪求助；带着具体题目时不劫持解题"""
        assert detect_emotion("唉，好烦") == "低落"
        assert detect_emotion("好烦啊，求函数 y=x^2+1 的最小值") is None
        assert detect_emotion("这题好烦，怎么做") is None

    def test_normal_messages_no_emotion(self):
        assert detect_emotion("举一反三") is None
        assert detect_emotion("什么是导数") is None
        assert detect_emotion("") is None

    def test_emotion_beats_exam_and_variant(self):
        """优先级：情绪 > 考试 > 变式——混合消息情绪最先"""
        r = run_precheck("我不想学了，别给我出模拟卷了")
        assert r is not None and r.stage == "emotion"
        assert r.kind == "route"
        assert r.decision.skill_id == "chat"
        assert r.decision.params["emotion"]

    def test_emotion_route_carries_label(self):
        r = run_precheck("学不进去，好烦")
        assert r.stage == "emotion"
        assert r.decision.skill_id == "chat"
        assert r.decision.params["emotion"] in ("挫败", "焦虑", "厌倦", "低落")


# ------------------------------------------------------------------ #
# 考试/变式阶段（迁入内核后行为不变）
# ------------------------------------------------------------------ #


class TestExamAndVariantStages:
    def test_exam_intent_still_intercepted(self):
        r = run_precheck("来一场 60 分钟全真模拟")
        assert r is not None and r.kind == "practice_intent" and r.stage == "exam"
        assert r.practice_intent["to"]

    def test_variant_trigger_routes_smart_quiz(self):
        r = run_precheck("举一反三")
        assert r is not None and r.stage == "variant"
        assert r.decision.skill_id == "smart_quiz"
        assert r.decision.confidence >= 0.97

    def test_explain_with_context_routes_socratic(self):
        r = run_precheck("请基于刚才的题给我举一反三讲解，并出变式确认掌握")
        assert r is not None and r.stage == "variant"
        assert r.decision.skill_id == "socratic_solver"

    def test_pinned_socratic_guard(self):
        """pinned 显式 UI 动作 + 讲解意图 → 确定性走 pinned，不被劫持"""
        r = run_precheck("讲解这道错题并举一反三", pinned=["socratic_solver"])
        assert r is not None and r.decision.skill_id == "socratic_solver"
        r2 = run_precheck("再来几道变式", pinned=["socratic_solver"])
        assert r2 is None  # 非讲解意图不劫持，交回路由层

    def test_no_hit_returns_none(self):
        assert run_precheck("什么是导数的定义") is None
        assert run_precheck("") is None


# ------------------------------------------------------------------ #
# L1-3 确定性：同句多次结果一致
# ------------------------------------------------------------------ #


class TestDeterminism:
    def test_same_message_same_result(self):
        samples = [
            "举一反三",
            "来一场 60 分钟全真模拟",
            "我不想学了",
            "什么是导数",
        ]
        for msg in samples:
            results = [run_precheck(msg) for _ in range(5)]
            first = results[0]
            for r in results[1:]:
                if first is None:
                    assert r is None
                else:
                    assert r is not None
                    assert (r.kind, r.stage) == (first.kind, first.stage)
                    if first.decision:
                        assert r.decision.skill_id == first.decision.skill_id
                        assert r.decision.confidence == first.decision.confidence


# ------------------------------------------------------------------ #
# 澄清兜底：候选感知选项（L1-3 确认式兜底）
# ------------------------------------------------------------------ #


class TestClarifyFallback:
    def _gate(self, skill_id: str) -> RouteDecision:
        router = IntentRouter()
        low = RouteDecision(skill_id=skill_id, confidence=0.3, params={"question": "x"})
        return router._apply_confidence_gate(low, "一道关于函数的题")

    def test_clarify_options_candidate_aware(self):
        d = self._gate("socratic_solver")
        assert d.need_clarify
        assert "帮我引导式解这道题" in d.clarify_options

        d2 = self._gate("smart_quiz")
        assert "帮我出几道练习题" in d2.clarify_options

        d3 = self._gate("qa_rag")
        assert "解释这个数学概念" in d3.clarify_options

    def test_clarify_options_default_for_unknown_skill(self):
        d = self._gate("some_other_skill")
        assert d.need_clarify
        assert "只是随便聊聊" in d.clarify_options

    def test_high_confidence_not_clarified(self):
        router = IntentRouter()
        high = RouteDecision(skill_id="smart_quiz", confidence=0.9, params={})
        assert router._apply_confidence_gate(high, "出题").need_clarify is False
