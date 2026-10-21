"""迭代10 v1.11 修复回归测试（test_iter10_v111_fixes.py）

三个实机实锤问题的修复：
K1 题干乱码：JSON 合法转义字母恰是 LaTeX 命令首字母（\frac \binom \tan \nu \rho），
   模型偶发单反斜杠时 strict loads "成功"但静默腐蚀成 \x0c/\x08/\t/\r 控制字符。
   修复 = LaTeX 感知反斜杠补全 + 解析后控制字符腐蚀检测（\n 字面换行豁免）。
K2 讲解丢失：「基于刚才的题举一反三讲解」被变式前置路由劫去 smart_quiz 直接出题，
   跳过引导式讲解。修复 = 讲解意图 + 题目指代 → socratic 先讲解；前端按钮 pinned 不再被劫持。
K3 变式跑偏：种子回落只看 tutor_session，题卡场景拿到陈旧引导题干（极限题变出函数题）。
   修复 = recent_seed_question 共享助手（最近题卡 vs 最近引导，取时间更近者）。
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

# ==================== K1. LaTeX 转义腐蚀 ====================


class TestV111EscapeCorruption:
    def test_frac_single_backslash_not_corrupted(self):
        """模型漏转义的 \\frac（\\f 是合法 JSON 转义 \x0c）必须还原为 LaTeX，不再乱码"""
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = '{"question_text": "已知 $a_{n+1} = \\frac{a_n}{1+a_n}$，求极限", "answer": "C"}'
        data = _json_loads_lenient(raw)
        assert data is not None
        assert "\\frac" in data["question_text"]
        assert "\x0c" not in data["question_text"]

    def test_tan_nu_rho_binom_not_corrupted(self):
        """\\t→TAB、\\r→CR、\\b→BS 同型腐蚀一网打尽"""
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = '{"question_text": "$\\tan x + \\nu + \\rho + \\binom{n}{k}$"}'
        data = _json_loads_lenient(raw)
        qt = data["question_text"]
        assert "\\tan" in qt and "\\nu" in qt and "\\rho" in qt and "\\binom" in qt
        for c in ("\x08", "\x09", "\x0c", "\x0d"):
            assert c not in qt

    def test_properly_escaped_untouched(self):
        """规范双反斜杠 \\\\frac 正确解码为单反斜杠，不被错补"""
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = '{"question_text": "$\\\\frac{a}{b}$"}'
        data = _json_loads_lenient(raw)
        assert data["question_text"] == "$\\frac{a}{b}$"

    def test_legit_newline_escape_preserved(self):
        """\\n 字面换行是 answer_analysis 合法内容——豁免腐蚀判定，且修复候选不误伤（后跟 CJK）"""
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = '{"answer_analysis": "第一步：化简\\n第二步：求导"}'
        data = _json_loads_lenient(raw)
        assert data["answer_analysis"] == "第一步：化简\n第二步：求导"

    def test_invalid_escape_still_repaired(self):
        """旧行为回归：非法转义 \\l（\\lim）仍被补全；合法 \\\\to 原样保留"""
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = '{"question_text": "求 $\\lim_{n\\\\to\\\\infty} a_n$"}'
        data = _json_loads_lenient(raw)
        assert data is not None
        assert "\\lim" in data["question_text"]
        assert "\\to" in data["question_text"]


# ==================== K2. 讲解优先路由 + pinned 不劫持 ====================


class TestV111VariantRouteExplainFirst:
    def test_explain_plus_variant_goes_socratic(self):
        """「基于刚才的题举一反三讲解」→ socratic 先引导讲解，而非直接出题"""
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision("请基于刚才的题给我逐一举一反三讲解，并出变式确认掌握")
        assert d is not None
        assert d.skill_id == "socratic_solver"

    def test_explain_with_inline_stem_goes_socratic(self):
        """讲解意图 + 消息自带题干 → socratic"""
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision("举一反三：已知函数 $f(x)=x^2$，求导数，先给我讲讲思路")
        assert d is not None
        assert d.skill_id == "socratic_solver"

    def test_pinned_not_hijacked(self):
        """前端按钮显式点亮（pinned）+ 讲解模式 → 确定性走 pinned 技能（迭代15 B2 修订）。

        旧语义：return None 交给 L2 软偏好——但 L2 高置信可覆盖 pinned，
        「讲解这道错题·举一反三」按钮被劫去 chat 自由对话（实测幻觉根因）。
        新语义：前置路由直接产出 pinned 技能决策，L2 不参与，杜绝劫持。
        """
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision(
            "讲解这道错题并帮我举一反三：已知函数 $f(x)=x^2$",
            pinned=["socratic_solver"],
        )
        assert d is not None
        assert d.skill_id == "socratic_solver"

    def test_pinned_non_explain_still_soft(self):
        """pinned 但消息非讲解模式（如纯闲聊词）→ 仍交 L2 软偏好（不硬劫持）"""
        from app.gateway.agent_router import _variant_route_decision

        # 含变式触发词但无讲解意图：pinned 时不劫持，交 L2
        assert _variant_route_decision("再来几道变式", pinned=["socratic_solver"]) is None

    def test_pure_variant_still_smart_quiz(self):
        """纯变式触发词（无讲解意图）→ smart_quiz 变式链（既有行为不变）"""
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision("举一反三")
        assert d is not None and d.skill_id == "smart_quiz"

    def test_quiz_with_analysis_stays_smart_quiz(self):
        """「出几道变式并附讲解」要的是带解析的题卡，不是引导讲解原题"""
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision("出几道变式并附讲解")
        assert d is not None and d.skill_id == "smart_quiz"


# ==================== K3. 题卡种子回落 ====================


def _msg_result(rows):
    r = MagicMock()
    r.__iter__.return_value = iter(rows)
    return r


def _tutor_result(row):
    r = MagicMock()
    r.first.return_value = row
    return r


def _quiz_envelope(stem: str) -> dict:
    return {
        "blocks": [
            {"type": "markdown", "content": "出好了"},
            {
                "type": "card",
                "data": {
                    "type": "quiz_set",
                    "items": [{"question_text": stem, "options": ["A", "B"]}],
                },
            },
        ]
    }


class TestV111RecentSeedQuestion:
    async def test_quiz_card_preferred_when_newer(self):
        """题卡比引导新 → 用题卡题干（实机根因：极限题卡被旧函数引导种子带偏）"""
        from app.skills.smart_quiz.main import recent_seed_question

        now = datetime.now(UTC)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _msg_result([(_quiz_envelope("  极限题卡题干  "), now)]),
                _tutor_result(("旧函数引导题干", now - timedelta(hours=1))),
            ]
        )
        q = await recent_seed_question(db, uuid.uuid4())
        assert q == "极限题卡题干"

    async def test_tutor_preferred_when_newer(self):
        """引导比题卡新 → 用引导题干"""
        from app.skills.smart_quiz.main import recent_seed_question

        now = datetime.now(UTC)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _msg_result([(_quiz_envelope("旧题卡题干"), now - timedelta(hours=2))]),
                _tutor_result(("新引导题干", now)),
            ]
        )
        q = await recent_seed_question(db, uuid.uuid4())
        assert q == "新引导题干"

    async def test_tutor_only_when_no_card(self):
        """会话内无题卡 → 回落引导题干（v1.4 既有行为保留）"""
        from app.skills.smart_quiz.main import recent_seed_question

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _msg_result([({"blocks": [{"type": "markdown", "content": "你好"}]}, datetime.now(UTC))]),
                _tutor_result(("  引导题干  ", datetime.now(UTC))),
            ]
        )
        q = await recent_seed_question(db, uuid.uuid4())
        assert q == "引导题干"

    async def test_none_when_nothing(self):
        from app.skills.smart_quiz.main import recent_seed_question

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_msg_result([]), _tutor_result(None)])
        assert await recent_seed_question(db, uuid.uuid4()) is None

    async def test_none_without_db(self):
        from app.skills.smart_quiz.main import recent_seed_question

        assert await recent_seed_question(None, uuid.uuid4()) is None
        assert await recent_seed_question(AsyncMock(), None) is None


# ==================== K3b. 变式链原题抽取（seed 显式传入） ====================


class TestV111VariantStemExtraction:
    def _extract(self, executor, message, seed=None):
        """复刻 _run_user_variant_chain 的原题抽取段（纯字符串逻辑，无需跑生成器）"""
        q = (seed or "").strip()
        if not q:
            lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
            stem_lines = [
                ln
                for ln in lines
                if not (len(ln) <= 30 and any(t in ln for t in executor._VARIANT_TRIGGERS))
            ]
            q = "\n".join(stem_lines or lines)
        return q[:400]

    def test_seed_used_directly(self):
        """显式 seed 不再被「取最后一行」截断成触发词（旧 bug：原题='举一反三'）"""
        from app.skills.smart_quiz.main import SmartQuizExecutor

        q = self._extract(SmartQuizExecutor(), "举一反三", seed="已知数列 $\\frac{1}{n}$，求极限")
        assert q == "已知数列 $\\frac{1}{n}$，求极限"

    def test_trailing_trigger_line_dropped(self):
        """用户贴题多行 + 末尾单写触发词 → 触发词行剔除，题干行保留"""
        from app.skills.smart_quiz.main import SmartQuizExecutor

        msg = "已知函数 $f(x)=x^2$\n求其导数\n举一反三"
        q = self._extract(SmartQuizExecutor(), msg)
        assert "已知函数" in q and "求其导数" in q and "举一反三" not in q


# ==================== K3c. socratic 上下文种子守卫 ====================


def _no_session_ctx():
    sess_result = MagicMock()
    sess_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=sess_result)
    ctx = MagicMock()
    ctx.db = db
    ctx.conversation_id = uuid.uuid4()
    ctx.user_id = uuid.uuid4()
    return ctx


class TestV111SocraticContextSeed:
    async def test_no_seed_honest_fallback(self, monkeypatch):
        """指代上下文但找不到任何题目 → 诚实请学生发题，绝不幻觉编题"""
        import app.skills.smart_quiz.main as sq
        from app.skills.socratic_solver import prompts
        from app.skills.socratic_solver.main import SocraticSolverExecutor

        monkeypatch.setattr(sq, "recent_seed_question", AsyncMock(return_value=None))
        ctx = _no_session_ctx()

        events = [
            e
            async for e in SocraticSolverExecutor().run(
                {"question": "基于刚才的题给我讲解一下"}, ctx
            )
        ]
        tokens = "".join(e["data"].get("text", "") for e in events if e["type"] == "token")
        assert tokens == prompts.CONTEXT_REF_NO_SEED_TEXT
        metas = [e for e in events if e["type"] == "_result_meta"]
        assert metas and metas[0]["data"]["confidence"] == 0.3

    async def test_seed_found_prepends_to_question(self, monkeypatch):
        """找到种子 → 种子题干 + 原请求一起进新题引导"""
        import app.skills.smart_quiz.main as sq
        from app.skills.socratic_solver.main import SocraticSolverExecutor

        monkeypatch.setattr(
            sq, "recent_seed_question", AsyncMock(return_value="已知极限题干 $a_n=1/n$")
        )
        captured: dict = {}

        async def fake_new_problem(self, question, ctx, meta, *, thinking=True, **kwargs):
            # N3 起会透传 image_file_ids/image_sha 等参数，桩用 **kwargs 兼容
            captured["question"] = question
            if False:  # pragma: no cover - 仅为构成 async generator
                yield

        monkeypatch.setattr(SocraticSolverExecutor, "_new_problem", fake_new_problem)
        ctx = _no_session_ctx()

        events = [
            e
            async for e in SocraticSolverExecutor().run(
                {"question": "基于刚才的题给我讲解一下"}, ctx
            )
        ]
        assert captured["question"].startswith("已知极限题干 $a_n=1/n$")
        assert "基于刚才的题给我讲解一下" in captured["question"]
        assert any(e["type"] == "_result_meta" for e in events)

    async def test_normal_stem_message_bypasses_guard(self, monkeypatch):
        """自带题干的正常消息不进种子回落（零影响主路径）"""
        import app.skills.smart_quiz.main as sq
        from app.skills.socratic_solver.main import SocraticSolverExecutor

        seed_mock = AsyncMock(return_value="不应被用到的种子")
        monkeypatch.setattr(sq, "recent_seed_question", seed_mock)
        captured: dict = {}

        async def fake_new_problem(self, question, ctx, meta, *, thinking=True, **kwargs):
            # N3 起会透传 image_file_ids/image_sha 等参数，桩用 **kwargs 兼容
            captured["question"] = question
            if False:  # pragma: no cover
                yield

        monkeypatch.setattr(SocraticSolverExecutor, "_new_problem", fake_new_problem)
        ctx = _no_session_ctx()

        async for _ in SocraticSolverExecutor().run(
            {"question": "已知函数 $f(x)=x^2$，求其导数"}, ctx
        ):
            pass
        assert captured["question"] == "已知函数 $f(x)=x^2$，求其导数"
        seed_mock.assert_not_called()
