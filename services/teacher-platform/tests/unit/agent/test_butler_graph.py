"""butler_graph M2-C 单测：L0 路由表驱动（mock 样句）+ 各意图事件流 + 确认流红线。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agent.events import skeleton_of
from app.agent.graphs.router import route_intent
from app.agent.tools.protocol import ToolRegistry, ToolSpec, invoke_tool
from app.domains.butler.service import ButlerMessage, ButlerService


class QuizImportParams(BaseModel):
    kp_name: str
    q_type: str
    difficulty: str


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def handler(params: QuizImportParams, ctx) -> dict:
        handler.calls.append(params.model_dump())
        return {"ok": True, "question_id": "q-1"}

    handler.calls = []  # type: ignore[attr-defined]
    reg.register(
        ToolSpec(
            name="quiz_import_question",
            label="题目入库",
            kind="write",
            description="识别/编辑后的题目结构化写入题库（教师确认后执行）",
            params_schema=QuizImportParams,
            confirm_required=True,
            domain="quiz",
        ),
        handler,
    )
    return reg


class FakeSearchWeb:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, query: str) -> list[dict]:
        self.calls.append(query)
        return [
            {"title": "人教A版选择性必修一 · 2.2 椭圆", "url": "https://www.pep.com.cn/gzsx/xrjdgzsx/ssl", "snippet": "教材原文：平面内与两个定点的距离的和等于常数…"},
            {"title": "课程标准 · 圆锥曲线", "url": "http://www.moe.gov.cn", "snippet": "经历从具体情境中抽象出椭圆的过程…"},
        ]


def _service(reg=None, search=None, gateway=None) -> ButlerService:
    return ButlerService(gateway=gateway, registry=reg, search_web=search)


async def _events(service: ButlerService, msg: ButlerMessage) -> list:
    return [e async for e in service.stream_butler(msg)]


# ── L0 路由表驱动（样句含 mock 全部意图分支 + 边界）──


@pytest.mark.parametrize(
    ("message", "expected", "kwargs"),
    [
        ("帮我做一份《椭圆及其标准方程》的课件", "deck_generate", {}),
        ("做一份圆锥曲线PPT", "deck_generate", {}),
        ("生成课件：二面角", "deck_generate", {}),
        ("我明天要备课", "deck_generate", {}),
        ("做个演示文稿吧", "deck_generate", {}),
        ("生成一个关于导数的ppt", "deck_generate", {}),
        ("存入题库", "quiz_import", {"has_images": True}),
        ("把这道题存到题库里", "quiz_import", {"has_images": True}),
        ("入库", "quiz_import", {"has_images": True}),
        ("存入错题本", "quiz_import", {"has_images": True}),
        ("帮我找几道椭圆的题", "find_resource", {}),
        ("找些素材", "find_resource", {}),
        ("找点资源", "find_resource", {}),
        ("推荐几个讲解视频", "find_resource", {}),
        ("备课找题", "find_resource", {}),  # 资源优先于课件（mock C2 规则）
        ("画一个椭圆的动态图", "draw_figure", {}),
        ("画个函数图像演示", "draw_figure", {}),
        ("帮我构图", "draw_figure", {}),
        ("做个课件封面", "deck_generate", {}),  # 含「课件」不进画图，落课件生成
        ("把这个公式插入到第3页", "insert_request", {}),
        ("添加到画布上", "insert_request", {}),
        ("放到课件里", "insert_request", {}),
        ("放进页面", "insert_request", {}),
        ("联网查一查最新的课标", "web_search", {}),
        ("搜索椭圆的第二定义", "web_search", {}),
        ("帮我联网找资料", "web_search", {}),
        ("看最新的高考真题", "web_search", {}),
        ("根号下x平方加y平方", "formula_parse", {}),
        ("二分之一加上三分之一的平方", "formula_parse", {}),
        ("sin30度等于多少的绝对值", "formula_parse", {}),
        ("x的极限和导数", "formula_parse", {}),
        ("你好", "math_chat", {}),
        ("椭圆的第一定义是什么？", "math_chat", {}),
        ("今天天气不错", "math_chat", {}),
    ],
)
def test_route_intent_table(message, expected, kwargs):
    assert route_intent(message, **kwargs) == expected


def test_route_force_web_search_flag():
    assert route_intent("椭圆定义", force_web_search=True) == "web_search"


# ── 图事件流 ──


async def test_deck_generate_flow(registry):
    service = _service(registry)
    events = await _events(service, ButlerMessage(session_id="s1", teacher_id="t1", message="帮我做一份《椭圆》的课件"))
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "thinking", "token", "card", "action", "done"]
    card = next(e for e in events if e.name == "card")
    assert card.payload["type"] == "link"
    assert card.payload["query"]["topic"] == "椭圆"
    action = next(e for e in events if e.name == "action")
    assert action.payload["action"] == "navigate"
    assert events[-1].payload == {"finish_reason": "stop"}


async def test_formula_card(registry):
    service = _service(registry)
    events = await _events(service, ButlerMessage(session_id="s2", teacher_id="t1", message="根号下x加1"))
    card = next(e for e in events if e.name == "card")
    assert card.payload["type"] == "formula"
    assert card.payload["latex"] == "\\sqrt{x+1}"
    assert card.payload["confidence"] == 0.95
    assert card.payload["source"] == "chat"


async def test_formula_card_low_confidence_on_residual_chinese(registry):
    """中文数字等未命中规则 → 残留中文包 \\text{} 且置信度降档（M3 接 L2/L3）。"""
    service = _service(registry)
    events = await _events(service, ButlerMessage(session_id="s2b", teacher_id="t1", message="根号下x加一"))
    card = next(e for e in events if e.name == "card")
    assert "一" in card.payload["latex"]  # 残留保留，不编造转换
    assert card.payload["confidence"] == 0.4


async def test_web_search_with_citations(registry):
    search = FakeSearchWeb()
    service = _service(registry, search=search)
    events = await _events(service, ButlerMessage(session_id="s3", teacher_id="t1", message="联网搜一下椭圆的定义"))
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "tool_call", "tool_result", "token", "citation", "done"]
    citation = next(e for e in events if e.name == "citation")
    assert citation.payload["sources"][0]["url"] == "https://www.pep.com.cn/gzsx/xrjdgzsx/ssl"
    assert search.calls == ["联网搜一下椭圆的定义"]


async def test_web_search_without_backend_is_honest(registry):
    service = _service(registry, search=None)
    events = await _events(service, ButlerMessage(session_id="s3b", teacher_id="t1", message="联网查课标"))
    tokens = "".join(e.payload["text"] for e in events if e.name == "token")
    assert "不能编造" in tokens
    assert all(e.name != "citation" for e in events)  # 无真实来源绝不出引用


async def test_math_chat_without_llm_emits_honest_boundary(registry):
    """无 key/无 gateway：mock 同款诚实边界文案（反假执行，不冒充回答）。"""
    service = _service(registry)
    events = await _events(service, ButlerMessage(session_id="s4", teacher_id="t1", message="椭圆的第一定义是什么？"))
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "thinking", "token", "done"]
    text = "".join(e.payload["text"] for e in events if e.name == "token")
    assert "规则引擎" in text


async def test_math_chat_with_llm_streams_tokens(registry):
    class FakeStreamGateway:
        async def chat(self, messages, **kw):
            async def gen():
                from app.llm.gateway import ChatChunk

                yield ChatChunk(kind="thinking", text="想想")
                yield ChatChunk(kind="token", text="椭圆的第一定义")
                yield ChatChunk(kind="token", text="是到两定点距离之和为常数的轨迹。")

            return gen()

    service = _service(registry, gateway=FakeStreamGateway())
    events = await _events(service, ButlerMessage(session_id="s5", teacher_id="t1", message="椭圆的第一定义是什么？"))
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "thinking", "token", "done"]
    text = "".join(e.payload["text"] for e in events if e.name == "token")
    assert "两定点" in text


async def test_math_chat_with_history_injects_prior_turns():
    """多轮上下文：history 必须进入 LLM messages（system + 历史 + 当前消息）。"""
    class FakeStreamGateway:
        def __init__(self) -> None:
            self.calls: list[list[dict]] = []

        async def chat(self, messages, **kw):
            self.calls.append(messages)

            async def gen():
                from app.llm.gateway import ChatChunk

                yield ChatChunk(kind="token", text="收到")

            return gen()

    gateway = FakeStreamGateway()
    service = _service(gateway=gateway)
    history = [
        {"role": "user", "text": "上一轮：椭圆的第一定义是什么？"},
        {"role": "assistant", "text": "是到两定点距离之和为常数的轨迹。"},
    ]
    await _events(service, ButlerMessage(session_id="s8", teacher_id="t1", message="再解释一遍", history=history))
    assert gateway.calls, "gateway 应收到 LLM 调用"
    msgs = gateway.calls[0]
    # 当前消息必须在最后
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "再解释一遍"
    # 历史轮次必须注入
    user_contents = [m["content"] for m in msgs if m["role"] == "user"]
    assert any("上一轮：椭圆的第一定义是什么？" in c for c in user_contents)
    assistant_contents = [m["content"] for m in msgs if m.get("role") == "assistant"]
    assert any("是到两定点距离之和为常数的轨迹。" in c for c in assistant_contents)


async def test_math_chat_injects_memories_and_context():
    """跨会话记忆：memories_provider 产出注入 system prompt；context 一并注入。"""
    from app.prompts.registry import default_registry

    class FakeStreamGateway:
        def __init__(self) -> None:
            self.calls: list[list[dict]] = []

        async def chat(self, messages, **kw):
            self.calls.append(messages)

            async def gen():
                from app.llm.gateway import ChatChunk

                yield ChatChunk(kind="token", text="记得")

            return gen()

    async def memories_provider(teacher_id: str, session_id: str) -> list[str]:
        return ["张老师偏好用真题引入课堂", "张老师常用高二(5)班"]

    gateway = FakeStreamGateway()
    service = ButlerService(
        gateway=gateway,
        memories_provider=memories_provider,
        prompt_renderer=default_registry(),
    )
    await _events(service, ButlerMessage(
        session_id="s9", teacher_id="t1", message="你好",
        context={"route_title": "课件工坊", "class_name": "高二(5)班"},
    ))
    assert gateway.calls, "gateway 应收到 LLM 调用"
    sys_msg = gateway.calls[0][0]["content"]
    assert "张老师偏好用真题引入课堂" in sys_msg, "memories 必须注入 system prompt"
    assert "高二(5)班" in sys_msg, "context 必须注入 system prompt"


# ── write 工具确认流（红线 1 / G8）──


async def test_quiz_import_confirm_flow(registry):
    service = _service(registry)
    sid = "s6"
    events = await _events(service, ButlerMessage(session_id=sid, teacher_id="t1", message="把这道椭圆题存入题库", has_images=True))
    names = [e.name for e in events]
    assert skeleton_of(names) == ["meta", "thinking", "tool_call", "tool_result", "card", "action", "done"]
    card = next(e for e in events if e.name == "card")
    assert card.payload["type"] == "action" and card.payload["confirm_required"] is True
    assert card.payload["tool"] == "quiz_import_question"
    # 未批准：不落库
    assert not registry.get("quiz_import_question").handler.calls  # type: ignore[attr-defined]

    resumed = await _events(service, ButlerMessage(session_id=sid, teacher_id="t1", message="", resume={"approved": True, "by": "t1"}))
    tool_results = [e for e in resumed if e.name == "tool_result" and e.payload["tool"] == "quiz_import_question"]
    assert tool_results and tool_results[0].payload["ok"] is True
    assert registry.get("quiz_import_question").handler.calls == [  # type: ignore[attr-defined]
        # params_schema 校验会剥掉 schema 外的字段（message 不入库参数）——协议预期行为
        {"kp_name": "圆锥曲线", "q_type": "solve", "difficulty": "medium"}
    ]


async def test_quiz_import_cancel_path(registry):
    service = _service(registry)
    sid = "s7"
    await _events(service, ButlerMessage(session_id=sid, teacher_id="t1", message="存入题库", has_images=True))
    resumed = await _events(service, ButlerMessage(session_id=sid, teacher_id="t1", message="", resume={"approved": False}))
    tokens = "".join(e.payload["text"] for e in resumed if e.name == "token")
    assert "已取消" in tokens
    assert not registry.get("quiz_import_question").handler.calls  # type: ignore[attr-defined]


async def test_session_isolation_between_threads(registry):
    """checkpointer thread_id=session_id：两会话互不串台（K1 的图侧面）。"""
    service = _service(registry)
    await _events(service, ButlerMessage(session_id="sA", teacher_id="t1", message="根号下x"))
    await _events(service, ButlerMessage(session_id="sB", teacher_id="t1", message="帮我做课件"))
    # sA 继续 resume 不应触发 sB 的意图状态
    events = await _events(service, ButlerMessage(session_id="sA", teacher_id="t1", message="你好"))
    assert events[0].payload["session_id"] == "sA"


async def test_invoke_tool_direct_write_still_denied():
    """红线 1：绕过确认流直调 write 工具在协议层被拒（与图无关）。"""
    reg = ToolRegistry()

    async def handler(params, ctx):  # pragma: no cover
        return {"ok": True}

    reg.register(
        ToolSpec(
            name="quiz_import_question",
            label="题目入库",
            kind="write",
            description="x",
            params_schema=QuizImportParams,
            confirm_required=True,
        ),
        handler,
    )
    from app.agent.tools.protocol import ToolDenied

    with pytest.raises(ToolDenied):
        await invoke_tool(
            "quiz_import_question",
            {"kp_name": "x", "q_type": "solve", "difficulty": "low"},
            {},
            registry=reg,
        )
