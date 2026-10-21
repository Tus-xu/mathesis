"""迭代20 · P1 对话内视频推荐 + P2 学情分析修复 + P3③ 管家×知识库打通（单元测试）

覆盖（定稿方案 testPlan 内核轨部分）：
(a) ChatSkill 视频流：mock bilibili.fetch_videos → card(video_cards) 事件、
    字段契约（7 字段来自 fetch 返回 + chat 轨自产 match_reason）、items 截断、
    status 前置、动作词护栏（「打开资源中心」不出卡）、解题负例、
    空结果默认精选词重试、再空只回文字、F4 互斥（视频轮无学情画像注入）；
(b) _butler_lookup 学情：触发面 = 旧关键词表（迭代18 最弱族等 22 词原样保留）
    ∪ kernel/router._PLATFORM_QUERY_RE（学习情况/学习状态/学习数据等新说法），
    逐句断言；空数据 → 「学情数据为空」空态引导段（非空串，不再静默返空）；
(c) _butler_lookup 知识库分支（P3③，v1 直查链）：真实文档行注入 + 空库话术；
(d) 路由护栏：_PLATFORM_QUERY_RE 增词后「讲题视频」不被 L0 抢进解题，
    「这道题怎么做」仍进解题（负例回归）；butler v2 工具 student.chat 场景可见。

纯单元测试：db / 画像服务 / bilibili 检索全部 mock，不写库、不外呼。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.butler.contracts import ActorRole
from app.butler.tools import build_domain_registry
from app.kernel.router import _PLATFORM_QUERY_RE, IntentRouter
from app.skills.base import SkillContext
from app.skills.chat.main import ChatSkill, _extract_video_keyword

# ------------------------------------------------------------------ #
#  公共桩件
# ------------------------------------------------------------------ #


def _ctx(db=None, llm=None) -> SkillContext:
    return SkillContext(
        user_id=str(uuid.uuid4()),
        user_role="student",
        conversation_id=str(uuid.uuid4()),
        request_id="req-iter20",
        db=db if db is not None else AsyncMock(),
        llm=llm,
    )


class _RecordingLLM:
    """记录收到的 messages（供 F4 互斥断言），流式吐一个 token。"""

    def __init__(self):
        self.seen_messages: list[dict] | None = None

    async def chat_stream(self, messages, **kwargs):
        self.seen_messages = [dict(m) for m in messages]
        yield {"token": "给你挑了几讲，见上方卡片 [视频1]"}


class _FakeProfileService:
    """学情画像服务桩：返回固定画像卡文本（空串模拟无数据）。"""

    def __init__(self, text: str):
        self._text = text

    async def build_profile_card_text(self, db, user_id, **kw):
        return self._text


class _ScalarsResult:
    """ctx.db.execute(...) 的最小结果对象：仅 .scalars().all()。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


# fetch_videos 返回的条目：恰好 7 个契约字段，刻意不含 match_reason
# （证明 match_reason 由 chat 轨自产，而非数据源携带）。
VIDEO_ITEMS = [
    {
        "bvid": f"BV1iter20{i}",
        "title": f"二次函数专题第{i}讲",
        "author": f"数学老师{i}",
        "cover": f"https://i0.hdslb.com/bfs/iter20/{i}.jpg",
        "duration": "12:34",
        "duration_sec": 754,
        "play": 10000 * i,
        "url": f"https://www.bilibili.com/video/BV1iter20{i}",
    }
    for i in range(1, 6)  # 5 条 → 卡片应截到 4
]

PROFILE_CARD = "综合分 62 ｜ 掌握度均值 0.55\n薄弱点：数列(0.41)、立体几何(0.45)\n累计错题 12 道"


async def _run_chat(question: str, fetch_side_effect=None) -> tuple[list[dict], _RecordingLLM]:
    """跑一遍 ChatSkill.run，返回 (事件列表, 记录型 LLM)。bilibili 检索与画像服务均 mock。"""
    skill = ChatSkill()
    llm = _RecordingLLM()
    ctx = _ctx(llm=llm)
    fetch = (
        AsyncMock(side_effect=fetch_side_effect)
        if fetch_side_effect
        else AsyncMock(return_value={"items": VIDEO_ITEMS, "source": "bilibili"})
    )
    with (
        patch("app.services.bilibili.fetch_videos", fetch),
        patch(
            "app.services.learning_profile.get_learning_profile_service",
            return_value=_FakeProfileService(PROFILE_CARD),
        ),
    ):
        events = [evt async for evt in skill.run({"question": question}, ctx)]
    events = [e for e in events if not e["type"].startswith("_")]  # 剔除内部 _result_meta
    return events, llm


def _cards(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "card"]


# ------------------------------------------------------------------ #
#  (a) P1 视频推荐流
# ------------------------------------------------------------------ #


class TestVideoCardsFlow:
    async def test_video_question_emits_card_with_contract_fields(self):
        events, llm = await _run_chat("推荐几个二次函数的讲解视频")
        cards = _cards(events)
        assert len(cards) == 1, f"视频诉求应恰好出 1 张卡，实际 {len(cards)}"
        data = cards[0]["data"]
        assert data["type"] == "video_cards"
        assert data["source"] == "bilibili"
        assert data["query"] == "二次函数"
        assert len(data["items"]) == 4  # fetch 返回 5 条 → 截断到契约上限 4
        for it in data["items"]:
            assert set(it.keys()) == {
                "bvid",
                "title",
                "author",
                "cover",
                "duration",
                "duration_sec",
                "play",
                "url",
                "match_reason",
            }
            # url 恒由后端按 bvid 拼接
            assert it["url"] == f"https://www.bilibili.com/video/{it['bvid']}"
            # match_reason 为 chat 轨自产（mock 数据源不含该字段）
            assert it["match_reason"] == "按「二次函数」为你挑的B站讲解"
        # SSE 前置状态（契约：stage=video_search）
        statuses = [e for e in events if e["type"] == "status"]
        assert any(s["data"].get("stage") == "video_search" for s in statuses)
        # 卡片先于正文 token（确定性数据先落，模型再写推荐语）
        types = [e["type"] for e in events]
        assert types.index("card") < types.index("token")
        # messages 追加了视频注入段，含逐条指代
        system_texts = [m["content"] for m in (llm.seen_messages or []) if m["role"] == "system"]
        assert any("【B站视频推荐】" in t and "[视频1]" in t for t in system_texts)

    async def test_fetch_called_with_extracted_topic(self):
        fetch = AsyncMock(return_value={"items": VIDEO_ITEMS, "source": "bilibili"})
        skill = ChatSkill()
        ctx = _ctx(llm=_RecordingLLM())
        with (
            patch("app.services.bilibili.fetch_videos", fetch),
            patch(
                "app.services.learning_profile.get_learning_profile_service",
                return_value=_FakeProfileService(PROFILE_CARD),
            ),
        ):
            async for _ in skill.run({"question": "有没有B站讲数列的视频"}, ctx):
                pass
        assert fetch.await_args.kwargs["keyword"] == "数列"
        assert fetch.await_args.kwargs["page_size"] == 4

    async def test_f4_video_round_has_no_profile_injection(self):
        """F4 互斥：视频轮产卡后，本轮 system 注入不含学情画像强指令段。"""
        _, llm = await _run_chat("推荐几个二次函数的讲解视频")
        system_texts = [m["content"] for m in (llm.seen_messages or []) if m["role"] == "system"]
        assert any("【B站视频推荐】" in t for t in system_texts)
        assert not any(
            "真实学情画像" in t for t in system_texts
        ), "视频意图轮注入了学情画像强指令——违反 F4 互斥"

    async def test_solve_question_no_card(self):
        """负例：解题诉求不出视频卡、不触发检索。"""
        fetch = AsyncMock(return_value={"items": VIDEO_ITEMS, "source": "bilibili"})
        skill = ChatSkill()
        ctx = _ctx(llm=_RecordingLLM())
        with (
            patch("app.services.bilibili.fetch_videos", fetch),
            patch(
                "app.services.learning_profile.get_learning_profile_service",
                return_value=_FakeProfileService(PROFILE_CARD),
            ),
        ):
            events = [evt async for evt in skill.run({"question": "这道题怎么做"}, ctx)]
        assert _cards([e for e in events if not e["type"].startswith("_")]) == []
        fetch.assert_not_awaited()

    async def test_open_resource_center_no_card(self):
        """动作词护栏：「打开资源中心…」只走 open_page，不出视频卡。"""
        for question in ("打开资源中心", "打开资源中心看函数视频"):
            fetch = AsyncMock(return_value={"items": VIDEO_ITEMS, "source": "bilibili"})
            skill = ChatSkill()
            ctx = _ctx(llm=_RecordingLLM())
            with (
                patch("app.services.bilibili.fetch_videos", fetch),
                patch(
                    "app.services.learning_profile.get_learning_profile_service",
                    return_value=_FakeProfileService(PROFILE_CARD),
                ),
            ):
                events = [evt async for evt in skill.run({"question": question}, ctx)]
            plain = [e for e in events if not e["type"].startswith("_")]
            assert _cards(plain) == [], f"{question!r} 不应出视频卡"
            fetch.assert_not_awaited()

    async def test_empty_result_retries_default_topic(self):
        """空结果 → resolve_topic(None,None) 默认精选词重试一次。"""
        default_topic_items = VIDEO_ITEMS[:2]
        fetch = AsyncMock(
            side_effect=[
                {"items": [], "source": "empty"},  # 第一次：主题词检索为空
                {"items": default_topic_items, "source": "fallback"},  # 重试命中兜底数据
            ]
        )
        skill = ChatSkill()
        ctx = _ctx(llm=_RecordingLLM())
        with (
            patch("app.services.bilibili.fetch_videos", fetch),
            patch(
                "app.services.learning_profile.get_learning_profile_service",
                return_value=_FakeProfileService(PROFILE_CARD),
            ),
        ):
            events = [evt async for evt in skill.run({"question": "有没有B站讲数列的视频"}, ctx)]
        plain = [e for e in events if not e["type"].startswith("_")]
        cards = _cards(plain)
        assert len(cards) == 1
        assert cards[0]["data"]["source"] == "fallback"
        assert fetch.await_count == 2
        assert fetch.await_args_list[0].kwargs["keyword"] == "数列"
        assert fetch.await_args_list[1].kwargs["keyword"] == "一数 高中数学"  # 默认精选词

    async def test_both_empty_no_card_text_only(self):
        """两次检索均空 → 不出空卡，注入文字建议（引导去资源中心）。"""
        fetch = AsyncMock(return_value={"items": [], "source": "empty"})
        skill = ChatSkill()
        llm = _RecordingLLM()
        ctx = _ctx(llm=llm)
        with (
            patch("app.services.bilibili.fetch_videos", fetch),
            patch(
                "app.services.learning_profile.get_learning_profile_service",
                return_value=_FakeProfileService(PROFILE_CARD),
            ),
        ):
            events = [evt async for evt in skill.run({"question": "有没有B站讲数列的视频"}, ctx)]
        plain = [e for e in events if not e["type"].startswith("_")]
        assert _cards(plain) == []
        system_texts = [m["content"] for m in (llm.seen_messages or []) if m["role"] == "system"]
        assert any("资源中心" in t for t in system_texts)


# ------------------------------------------------------------------ #
#  主题词剥离
# ------------------------------------------------------------------ #


class TestExtractVideoKeyword:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("推荐几个二次函数的讲解视频", "二次函数"),
            ("有没有B站讲数列的视频", "数列"),
            ("有没有讲高中数学的视频", "高中数学"),
            ("b站有没有立体几何教程", "立体几何"),
            ("三角函数的看课推荐", "三角函数"),
        ],
    )
    def test_extracts_topic(self, message, expected):
        assert _extract_video_keyword(message) == expected

    @pytest.mark.parametrize("message", ["推荐视频", "视频", "帮我推荐一下资源吧", "我想看课"])
    def test_returns_empty_without_topic(self, message):
        assert _extract_video_keyword(message) == ""


# ------------------------------------------------------------------ #
#  (b) P2 学情分支：旧表 ∪ regex 并集 + 空态
# ------------------------------------------------------------------ #


class TestButlerLookupProfile:
    @pytest.mark.parametrize(
        "question",
        [
            "分析我的学习情况",  # regex 新词（旧表无「学情」连续子串——回归根因句）
            "我的学习状态如何",  # regex 新词（本轮 router.py 增词）
            "我的学习数据怎么样",  # regex 新词
            "我的成绩怎么样",  # regex 新词
            "哪里比较薄弱",  # 旧表保住词（迭代18 前就在）
            "我哪部分最弱",  # 旧表保住词（迭代18 修复的「最弱」族）
            "我的短板是什么",  # 旧表保住词
            "这部分我没掌握",  # 旧表保住词
        ],
    )
    async def test_union_triggers_profile_injection(self, question):
        ctx = _ctx()
        with patch(
            "app.services.learning_profile.get_learning_profile_service",
            return_value=_FakeProfileService(PROFILE_CARD),
        ):
            text = await ChatSkill()._butler_lookup(question, ctx)
        assert "【管家查询：该学生的真实学情画像】" in text, f"{question!r} 未触发学情注入"
        assert "综合分 62" in text  # 注入的是真实画像卡文本

    async def test_empty_data_returns_guidance_not_blank(self):
        """无学习记录 → 空态引导段（修复点：此前返回空串，模型只能推诿）。"""
        ctx = _ctx()
        with patch(
            "app.services.learning_profile.get_learning_profile_service",
            return_value=_FakeProfileService(""),
        ):
            text = await ChatSkill()._butler_lookup("分析我的学习情况", ctx)
        assert text.strip() != ""
        assert "【管家查询：学情数据为空】" in text
        assert "练题" in text  # 引导先做练题
        assert "编造" in text  # 禁编造

    async def test_video_intent_skips_profile_branch(self):
        """F4 三重保险之二：_butler_lookup 自身对视频消息不出学情注入。"""
        ctx = _ctx()
        with patch(
            "app.services.learning_profile.get_learning_profile_service",
            return_value=_FakeProfileService(PROFILE_CARD),
        ):
            text = await ChatSkill()._butler_lookup("推荐几个二次函数的讲解视频", ctx)
        assert text == ""

    async def test_action_verb_skips_lookup(self):
        ctx = _ctx()
        with patch(
            "app.services.learning_profile.get_learning_profile_service",
            return_value=_FakeProfileService(PROFILE_CARD),
        ):
            assert await ChatSkill()._butler_lookup("打开学情报告", ctx) == ""


# ------------------------------------------------------------------ #
#  (c) P3③ 知识库分支（v1 直查链）
# ------------------------------------------------------------------ #


def _kb_doc(title, status="ready", category=None, created="2026-10-09 21:00:00"):
    return SimpleNamespace(
        title=title,
        status=status,
        meta_=({} if category is None else {"category": category}),
        created_at=created,
    )


class TestButlerLookupKnowledgeBase:
    @pytest.mark.parametrize(
        "question",
        [
            "我知识库里有什么资料",
            "我的资料有哪些",
            "我上传的资料",
            "我传的文件",
            "资料库里都有啥",
        ],
    )
    async def test_lists_real_docs(self, question):
        db = AsyncMock()
        db.execute.return_value = _ScalarsResult(
            [
                _kb_doc("圆锥曲线笔记手写版", category="笔记", created="2026-10-09 21:00:00"),
                _kb_doc("立体几何大题图", status="pending", created="2026-10-10 09:12:00"),
            ]
        )
        text = await ChatSkill()._butler_lookup(question, _ctx(db))
        assert "【管家查询：我的知识库资料】" in text
        assert "1. 圆锥曲线笔记手写版｜分类 笔记｜已就绪｜2026-10-09" in text
        assert "2. 立体几何大题图｜分类 其他｜解析中｜2026-10-10" in text
        # 直查链：只查本人未删除文档，按创建时间倒序，最多 5 条
        stmt = db.execute.call_args.args[0]
        sql = str(stmt)
        assert "knowledge_docs" in sql
        assert "uploader_id" in sql
        assert "deleted_at" in sql
        assert "DESC" in sql
        assert "LIMIT" in sql

    async def test_empty_kb_honest_answer(self):
        db = AsyncMock()
        db.execute.return_value = _ScalarsResult([])
        text = await ChatSkill()._butler_lookup("我知识库里有什么资料", _ctx(db))
        assert text.strip() != ""
        assert "知识库" in text and "为空" in text

    async def test_failed_status_rendered_chinese(self):
        db = AsyncMock()
        db.execute.return_value = _ScalarsResult([_kb_doc("解析失败的扫描件", status="failed")])
        text = await ChatSkill()._butler_lookup("我的知识库里有什么", _ctx(db))
        assert "解析失败" in text


# ------------------------------------------------------------------ #
#  (d) 路由护栏与 butler v2 场景扩展
# ------------------------------------------------------------------ #


class TestRouterGuardAndButlerScene:
    def test_platform_query_regex_extended(self):
        # 本轮新增词命中
        assert _PLATFORM_QUERY_RE.search("我的学习状态如何")
        assert _PLATFORM_QUERY_RE.search("有没有B站讲数列的视频")
        assert _PLATFORM_QUERY_RE.search("推荐几个二次函数的讲题视频")
        # 旧表保住词不在 regex 中——并集的意义（F3：纯替换会复发迭代18 事故）
        assert not _PLATFORM_QUERY_RE.search("哪里比较薄弱")
        assert not _PLATFORM_QUERY_RE.search("我哪部分最弱")
        assert not _PLATFORM_QUERY_RE.search("我的短板是什么")

    def test_l0_video_topic_not_hijacked_by_jiangti(self):
        """「…讲题视频」含 :345「讲题」，但护栏扩词后不得抢进 socratic。"""
        decision = IntentRouter()._check_l0("推荐几个二次函数的讲题视频", "")
        assert decision is None

    def test_l0_solve_negative_regression(self):
        """解题负例回归：「这道题怎么做」仍进 socratic_solver。"""
        decision = IntentRouter()._check_l0("这道题怎么做", "")
        assert decision is not None and decision.skill_id == "socratic_solver"

    def test_butler_tools_visible_in_student_chat_scene(self):
        """P2 前瞻：三个学情工具在 student.chat 场景对学生可见（tools.py 扩面）。"""
        reg = build_domain_registry()
        visible = set(reg.visible_to(ActorRole.STUDENT, "student.chat"))
        for name in (
            "student.profile.read",
            "student.errors.due.read",
            "student.mastery.weak_points.read",
        ):
            assert name in visible, f"{name} 未在 student.chat 场景可见"
