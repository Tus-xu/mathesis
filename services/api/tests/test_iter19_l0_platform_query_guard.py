"""L0 承接词护栏 · 平台数据查询不被解题劫持（2026-10-08 修复回归）

真机事故（生产 router_eval_logs 留痕）：
    学生问「帮我分析一下我的学情可以吗」
    → L0 承接词 "帮我分析" 命中 → socratic_solver
    → AI 把学情查询当成一道数学题做"苏格拉底引导"
      （"如果把它当成一道题来审，你觉得它更像是在考哪个数学知识点…"）
    → 影子评测同时记录 local=socratic_solver / xc=chat / agree=f（两路分歧）

根因：宽口径承接词（帮我分析/讲解一下/讲一下…）**自身不含"题"的指代**，
单独命中不足以判定"用户在问一道题"，却会短路 L2 Function Calling（L0 fastpath），
把管家查询类消息（学情/错题本/任务/掌握度）吞进解题技能。

修复（kernel/router.py `_check_l0`）：
  ① 平台数据查询意图（_PLATFORM_QUERY_RE）一律不劫持 → 交回 L2 走 chat 管家查询链路；
  ② 宽口径承接词必须另有「题目指代」（_PROBLEM_REF_RE）才判解题。

本测试同时锁定**不得回退**的既有行为：
  - 2026-09-14 修复的「可以为我讲解一下吗」（附件题图）仍须进 socratic_solver；
  - 「这道题怎么做/讲解一下这道题」等明确题目指代仍进 socratic_solver。
"""

import pytest

from app.kernel.router import IntentRouter


@pytest.fixture()
def router() -> IntentRouter:
    return IntentRouter()


def _l0(router: IntentRouter, message: str):
    """取 L0 决策（无命中返回 None）"""
    return router._check_l0(message, "")


# ------------------------------------------------------------------ #
# ① 平台数据查询：不得被判进解题（本次修复的核心）
# ------------------------------------------------------------------ #


class TestPlatformQueryNotHijacked:
    """管家查询类消息（学情/错题/任务/掌握度）不得被 L0 承接词抢进 socratic_solver"""

    @pytest.mark.parametrize(
        "message",
        [
            "帮我分析一下我的学情可以吗",  # ← 真机事故原句
            "帮我分析一下我的学情",
            "分析一下我的学情",
            "分析一下我的错题规律",
            "帮我分析一下我的错题",
            "帮我分析一下我的掌握度",
            "帮我分析我的薄弱点",
            "讲解一下我的学习情况",
            "帮我看看我的学习报告",
            "分析一下我的做题记录",
            "帮我分析一下我的正确率",
            "帮我分析一下我的任务",
            "讲一下我的作业情况",
        ],
    )
    def test_platform_query_goes_to_l2_not_socratic(self, router, message):
        """L0 必须放行（返回 None）→ 交 L2 路由，由 chat 管家查询链路处理"""
        decision = _l0(router, message)
        assert decision is None, (
            f"{message!r} 被 L0 判成 {decision.skill_id}——"
            "平台数据查询被解题承接词劫持（学情事故回归）"
        )

    def test_regression_exact_production_utterance(self, router):
        """真机原句回归：这条消息曾把学生送进苏格拉底解题"""
        assert _l0(router, "帮我分析一下我的学情可以吗") is None


# ------------------------------------------------------------------ #
# ② 明确题目指代：仍须进解题（不得因修复而回退）
# ------------------------------------------------------------------ #


class TestProblemReferentStillRoutesToSocratic:
    """带明确题目指代的讲解/分析请求，必须继续进引导式解题"""

    @pytest.mark.parametrize(
        "message",
        [
            "帮我分析这道题",
            "分析一下这道题的解法",
            "帮我分析一下这题为什么错",
            "帮我分析一下第3题",
            "讲解一下这道题",
            "讲解一下刚才那道题",
            "讲讲这道题",
            "讲题",
            "帮我讲讲这道题",
        ],
    )
    def test_referent_routes_to_socratic(self, router, message):
        decision = _l0(router, message)
        assert decision is not None, f"{message!r} 未进解题——题目指代承接词被误伤"
        assert decision.skill_id == "socratic_solver"

    def test_attachment_case_from_20260914_not_regressed(self, router):
        """2026-09-14 修复不得回退：附件题图场景「可以为我讲解一下吗」。

        该句本身无"题"字，靠上游把附件解析文本拼进 route_message 提供指代；
        此处直接验证带附件指代的形态仍进解题。
        """
        decision = _l0(router, "可以为我讲解一下吗")
        # 无任何指代时不再武断判解题（本次收紧），但一旦上游注入附件指代必须命中
        assert decision is None
        decision2 = _l0(router, "可以为我讲解一下吗\n\n[附件内容]\n如图，求函数的最小值")
        assert decision2 is not None and decision2.skill_id == "socratic_solver"


# ------------------------------------------------------------------ #
# ③ 既有 L0 行为不回归
# ------------------------------------------------------------------ #


class TestExistingL0BehaviourPreserved:
    def test_slash_commands_unchanged(self, router):
        assert _l0(router, "/解题 求 x^2=4 的解").skill_id == "socratic_solver"
        assert _l0(router, "/qa 什么是导数").skill_id == "qa_rag"
        assert _l0(router, "/chat 你好").skill_id == "chat"

    def test_plain_question_not_l0(self, router):
        assert _l0(router, "什么是导数的定义") is None

    def test_closing_expressions_not_hijacked(self, router):
        """完结/闲聊表达不进解题"""
        assert _l0(router, "这道题我会了") is None
        assert _l0(router, "讲解一下这题，不用了") is None

    def test_page_intent_untouched(self, router):
        """「打开错题本」属页面直达（预检 page 阶段），L0 不应抢"""
        assert _l0(router, "帮我打开错题本可以吗") is None

    def test_determinism(self, router):
        """L1-3 确定性：同句多次结果一致"""
        for msg in ("帮我分析一下我的学情可以吗", "帮我分析这道题", "讲题"):
            results = {(_l0(router, msg) or type("X", (), {"skill_id": None})).skill_id for _ in range(5)}
            assert len(results) == 1, f"{msg!r} 多次判定不一致：{results}"
