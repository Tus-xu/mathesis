"""Butler 主图（M2-C：L0 规则路由 + math_chat 流式 + web_search 带引用 +
write 工具 interrupt/resume 确认流 + navigate/工具面板卡 + 诚实边界文案）。

事件载荷逐字段对齐 mock teacherV3Server.ts（红线 4）：meta{session_id,intent,note?} /
thinking{text} / token{text} / tool_call{tool,label} / tool_result{tool,ok,summary} /
card{type:'action'|'tool'|'link'|'formula', id, title, ...} / action{action,route,query,toast} /
citation{sources[{index,title,url,snippet}]} / done{finish_reason:'stop'}。

替换点（B2/B4 未交付，协议级 fake 注入）：
- search_web：B2 的博查检索；fake 只回固定来源（测试/演示），URL 真实性红线在 M3 接 B2 时生效。
- registry：B4 的 quiz_import_question 等工具；write 执行必须经确认流（红线 1）。
- math_chat 检索：可选 CRAG（B2）；接入前闲聊路径零检索调用（K6）。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.tools.protocol import ToolRegistry, invoke_tool
from app.llm.gateway import AiRunMeta, LlmGateway

from .router import ButlerState, route_intent
from .voice_rules import rule_confidence, voice_to_latex

_FALLBACK_REPLY = (
    "我是原型阶段的规则引擎（未接入大模型），当前能可靠执行这些事："
    "①「帮我做一份《××》课件」→ 预填跳转工坊；② 口述公式（说\"根号下 x 加一\"）；"
    "③ 附题目照片说「存入题库」；④ 联网检索演示。"
    "数学问答与跨工件插入将在真实服务接入后开放——我不想用编造的回答占用你的时间。"
)


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _emit(name: str, data: dict) -> None:
    get_stream_writer()({"event": name, "data": data})


def _topic_of(message: str) -> str:
    match = re.search(r"[《「\"“]([^》」\"”]+)[》」\"”]", message)
    if match:
        return match.group(1)
    stripped = re.sub(r"帮|我|请|做|个|一|份|生成|课件|ppt|演示文稿|关于|的|主题", "", message)
    return stripped.strip()[:18] or "椭圆及其标准方程"


def build_butler_graph(
    *,
    gateway: LlmGateway | None = None,
    registry: ToolRegistry | None = None,
    search_web: Any = None,  # B2 的 search_web；None = 未接入（web_search 走边界文案）
    prompt_renderer: Any = None,  # B2 PromptRegistry；None = chat 无 system prompt（禁止上线形态）
    memories_provider: Any = None,  # async (teacher_id, session_id) -> list[str]；None = 不注入记忆
):
    """构造 butler 主图（checkpointer 由调用方在 compile 前设置，thread_id=session_id）。"""

    async def route_node(state: ButlerState) -> dict:
        intent = route_intent(
            state.get("message", ""),
            has_images=state.get("has_images", False),
            force_web_search=state.get("force_web_search", False),
        )
        note = {
            "quiz_import": "图片已附带，走拍照识别链路",
            "deck_generate": "跳转课件工坊并预填",
            "web_search": None,
            "formula_parse": None,
        }.get(intent)
        meta: dict[str, Any] = {"session_id": state["session_id"], "intent": intent}
        if note:
            meta["note"] = note
        _emit("meta", meta)
        return {"intent": intent, "meta": meta}

    # ── write 工具确认流（quiz_import）──
    async def quiz_import_node(state: ButlerState) -> dict:
        message = state.get("message", "")
        kp = (
            "圆锥曲线"
            if re.search(r"圆锥|椭圆|双曲线|抛物线", message)
            else "导数及其应用"
            if re.search(r"函数|导数", message)
            else "三角函数"
            if re.search(r"三角", message)
            else "数列"
        )
        _emit("thinking", {"text": "先解读题目照片，再生成入库草稿；原图保留对照，入库前由教师确认。"})
        # 审计 #10 修正：OCR sidecar 未部署——不得硬编码 ok:True 谎称"已识别"。
        # 草稿以教师消息文本为题干底稿，识别块为空；识别管线上线后此处接真实 OCR。
        _emit("tool_call", {"tool": "photo_recognize", "label": "识别题目照片"})
        _emit("tool_result", {"tool": "photo_recognize", "ok": False, "summary": f"OCR 识别服务未部署，未能自动识别照片——以你的文字描述作为入库底稿（知识点建议：{kp}），题干与答案请在题库中补全后确认"})
        card = {
            "type": "action",
            "id": _uid("act"),
            "title": "存入题库",
            "confirm_required": True,
            "status": "pending",
            "summary": f"将识别的题目写入「{kp}」分类，题干与解析可在题库中继续编辑",
            "params": {"kp_name": kp, "q_type": "solve", "difficulty": "medium",
                       "stem_latex": message, "answer": ""},
            "tool": "quiz_import_question",
            "message_id": state["session_id"],
        }
        # G8：确认卡先落 butler_actions（UNIQUE(message_id, tool_name)）——表未收编前由
        # record_action 钩子承担（MIGRATION_REQUEST-001 后续请求），此处幂等键已进卡。
        decision = interrupt(card)
        approved = bool(decision.get("approved"))
        if not approved:
            _emit("token", {"text": "已取消，未写入题库。识别结果保留在会话里，需要时再说「存入题库」。"})
            return {"executed": False, "decision": decision}
        result = await invoke_tool(
            "quiz_import_question",
            {**card["params"], "message": message},
            {"teacher_id": state.get("teacher_id"), "session_id": state["session_id"]},
            confirmed=True,
            registry=registry,
        )
        ok = bool(result.get("ok"))
        _emit(
            "tool_result",
            {"tool": "quiz_import_question", "ok": ok, "summary": "已写入题库草稿，可在题库中继续编辑" if ok else f"入库失败：{result.get('error')}"},
        )
        _emit(
            "token",
            {"text": "已执行入库。题干与解析可在题库中查看修改；原图与识别结果一并保留。" if ok else "入库未完成，请稍后重试或手动录入。"},
        )
        return {"executed": ok, "decision": decision}

    # ── 跳转/预填（frontend 动作：后端不代劳，前端 Action Registry 执行）──
    async def deck_node(state: ButlerState) -> dict:
        topic = _topic_of(state.get("message", ""))
        _emit("thinking", {"text": "生成课件需要教师控制模板与大纲，跳转课件工坊并预填表单最合适。"})
        _emit("token", {"text": f"好的，将前往课件工坊并预填：主题「{topic}」、班级高二(3)班、模板学术蓝。模板与大纲仍由您在工坊内选定确认。"})
        _emit(
            "card",
            {
                "type": "link",
                "id": _uid("lnk"),
                "title": f"课件工坊 · 主题生成（{topic}）",
                "route": "/teacher-v3/slides",
                "query": {"mode": "topic", "topic": topic, "class_id": "c2-03", "template_id": "tpl-academic-blue"},
                "note": "预填后仍需您确认模板与大纲",
            },
        )
        _emit(
            "action",
            {
                "action": "navigate",
                "route": "/teacher-v3/slides",
                "query": {"mode": "topic", "topic": topic, "class_id": "c2-03", "template_id": "tpl-academic-blue"},
                "toast": f"已预填主题「{topic}」",
            },
        )
        return {}

    async def resource_node(state: ButlerState) -> dict:
        _emit("thinking", {"text": "找资源应该带着当前课题/环节去伴随资源台，候选有来源和推荐理由，而不是在聊天里丢几道题。"})
        _emit("token", {"text": "已按当前页面上下文为你准备资源候选：按教学意图分镜头、来源分四层（我的/备课组/教材官方/外部），插入前可预览，插入后可撤销。"})
        _emit("card", {"type": "tool", "id": _uid("tl"), "tool": "resource", "title": "打开伴随资源", "summary": "按当前上下文找题 / 素材 / 视频卡"})
        return {}

    async def draw_node(state: ButlerState) -> dict:
        _emit("thinking", {"text": "数学图形应在绘图工作台里做成结构化对象（函数 / 构图导演 / 手写公式），我可以直接帮你打开。"})
        _emit("token", {"text": "已打开数学绘图：完成后点「插入」，图形会回到你当前的工作位置；关掉也不丢，会进暂存图形。"})
        _emit("card", {"type": "tool", "id": _uid("tl"), "tool": "draw", "title": "打开数学绘图", "summary": "函数绘图 / 构图导演 / 手写公式"})
        return {}

    async def insert_node(state: ButlerState) -> dict:
        message = state.get("message", "")
        page_match = re.search(r"第\s*(\d+)\s*页", message)
        target = f"第 {page_match.group(1)} 页" if page_match else "目标页"
        _emit("thinking", {"text": f"插入到课件{target}需要\"目标课件 + 页码 + 内容\"三者都明确，当前会话缺少已打开的课件上下文。"})
        _emit(
            "token",
            {
                "text": f"这个插入操作我暂时不能直接执行（需要目标课件上下文，完整执行与回执在管家动作版接入）。"
                f"现在可以：① 打开目标课件后，在资源中心对该素材点「插入课件」并选页；"
                f"② 或把公式/图形卡从本面板直接拖入画布。{f'已记住你要的是{target}。' if page_match else ''}"
            },
        )
        _emit("card", {"type": "link", "id": _uid("lnk"), "title": "课件工坊 · 打开目标课件后可插入", "route": "/teacher-v3/slides", "query": {}, "note": "在编辑器内通过「资源/管家卡片拖入」完成插入"})
        return {}

    # ── web_search（B2 缺席 → 注入 fake/real 均走同一路径；citation 只来自真实返回）──
    async def search_node(state: ButlerState) -> dict:
        _emit("tool_call", {"tool": "search_web", "label": "联网检索"})
        if search_web is None:
            _emit("tool_result", {"tool": "search_web", "ok": False, "summary": "检索服务未接入（B2），已跳过"})
            _emit("token", {"text": "联网检索服务还没接入，我不能编造检索结果。先帮你把问题记下来，接入后再回来补这一轮。"})
            return {}
        results = await search_web(state.get("message", ""))
        _emit("tool_result", {"tool": "search_web", "ok": True, "summary": f"检索到 {len(results)} 条相关来源"})
        summary = "；".join(str(r.get("snippet", ""))[:60] for r in results[:2]) if results else ""
        _emit("token", {"text": f"结合检索结果：{summary}" if summary else "检索完成，暂无高相关来源。"})
        _emit(
            "citation",
            {
                "sources": [
                    {"index": i + 1, "title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("snippet", "")}
                    for i, r in enumerate(results)
                ]
            },
        )
        return {}

    # ── 公式卡（L1 规则层，与语音公式同源）──
    async def formula_node(state: ButlerState) -> dict:
        message = state.get("message", "")
        latex = voice_to_latex(message)
        confidence = rule_confidence(message, latex)
        _emit("token", {"text": "识别为如下公式，可拖入课件或点开编辑："})
        _emit("card", {"type": "formula", "id": _uid("fml"), "latex": latex, "confidence": confidence, "source": "chat"})
        return {}

    # ── math_chat / chitchat（LLM 流式；无 key 时诚实边界文案，不编造回答）──
    async def chat_node(state: ButlerState) -> dict:
        message = state.get("message", "")
        route_title = (state.get("ctx") or {}).get("route_title")
        system_prompt = ""
        if prompt_renderer is not None:
            try:
                ctx = dict(state.get("ctx") or {})
                # 字段归一化：system.j2 用 context.route；兼容前端历史字段 route_title
                if not ctx.get("route") and ctx.get("route_title"):
                    ctx["route"] = ctx["route_title"]
                # system.j2 以 strict undefined 渲染：缺失键会抛错致 system prompt 为空，
                # 必须补齐模板引用到的全部键（前端可只传有的）
                for _key in ("route", "deck_title", "class_name", "textbook", "chapter", "selection"):
                    ctx.setdefault(_key, "")
                ctx.setdefault("slide_index", None)
                memories: list[str] = []
                if memories_provider is not None:
                    try:
                        memories = list(await memories_provider(state.get("teacher_id"), state.get("session_id"))) or []
                    except Exception:
                        memories = []  # 记忆读取失败不拦对话（上线形态必须修）
                system_prompt = prompt_renderer.render(
                    "butler/system.j2",
                    context=ctx,
                    memories=memories[:8],
                    turn_reference=ctx.get("turn_reference"),
                )
            except Exception:
                system_prompt = ""  # 渲染失败不拦对话，但上线形态必须修
        if gateway is not None:
            try:
                messages: list[dict] = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
                # 多轮上下文：history（user/assistant 交替）→ 按 role 注入，当前消息收尾
                for turn in state.get("history") or []:
                    role = turn.get("role")
                    text = str(turn.get("text") or "")
                    if role in ("user", "assistant") and text:
                        messages.append({"role": role, "content": text})
                messages.append({"role": "user", "content": message})
                stream = await gateway.chat(
                    messages,
                    stream=True,
                    use_case="chat",
                    metadata=AiRunMeta(feature="butler", user_id=state.get("teacher_id")),
                )
                emitted = False
                async for chunk in stream:
                    if chunk.kind == "thinking":
                        _emit("thinking", {"text": chunk.text})
                    else:
                        emitted = True
                        _emit("token", {"text": chunk.text})
                if emitted:
                    return {}
            except Exception:
                _emit("token", {"text": "模型调用失败了，我先不编造回答。"})
                return {}
        if route_title:
            _emit("thinking", {"text": f"结合当前页面（{route_title}）判断意图。"})
        else:
            _emit("thinking", {"text": "判断意图。"})
        for segment in re.findall(r"[\s\S]{1,14}", _FALLBACK_REPLY):
            _emit("token", {"text": segment})
        return {}

    builder = StateGraph(ButlerState)
    builder.add_node("route", route_node)
    builder.add_node("quiz_import", quiz_import_node)
    builder.add_node("deck_generate", deck_node)
    builder.add_node("find_resource", resource_node)
    builder.add_node("draw_figure", draw_node)
    builder.add_node("insert_request", insert_node)
    builder.add_node("web_search", search_node)
    builder.add_node("formula_parse", formula_node)
    builder.add_node("math_chat", chat_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        lambda state: state.get("intent", "math_chat"),
        {
            "quiz_import": "quiz_import",
            "deck_generate": "deck_generate",
            "find_resource": "find_resource",
            "draw_figure": "draw_figure",
            "insert_request": "insert_request",
            "web_search": "web_search",
            "formula_parse": "formula_parse",
            "math_chat": "math_chat",
            "chitchat": "math_chat",
        },
    )
    for node in ("quiz_import", "deck_generate", "find_resource", "draw_figure", "insert_request", "web_search", "formula_parse", "math_chat"):
        builder.add_edge(node, END)
    return builder
