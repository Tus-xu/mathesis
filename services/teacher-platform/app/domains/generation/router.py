# coding: utf-8
"""generation 域路由（教案两段式 SSE / AI 元素建议 SSE / deck 大纲）。

KNR 红线 9：六类生成任务必须先 assemble_evidence 再调 LLM——图内已强制；
本路由负责注入 ContextAssembler / PromptRegistry（B2）/ LlmGateway（Spark 主通道）。

诚实边界：deck 生成失败时返回明确错误，不把无证据/未验证骨架标记为可用课件。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...agent.context.assembler import ContextAssembler
from ...agent.graphs.generation import GenerationDeps, build_ai_element_graph, build_deck_graph, build_plan_graph
from ...core.deps import get_current_user, get_session
from ...core.errors import ApiError, ErrorCode
from ...core.response import ok
from ...core.security import UserToken
from ...core.sse import sse_response
from ...db.session import session_factory

router = APIRouter()

_deps: GenerationDeps | None = None

_DECK_PAGE_TYPE_ALIASES = {
    # 教师前端降级大纲的展示词 → IFC-001 九种页型；避免失败重试时把例题当普通定义页。
    "cover": "visual_definition",
    "definition": "concept_intro",
    "example": "worked_example",
    "variation": "practice",
    "keypoints": "formula_focus",
}


def _normalize_deck_page_type(value: object) -> str:
    page_type = str(value or "concept_intro").strip()
    return _DECK_PAGE_TYPE_ALIASES.get(page_type, page_type)


def get_generation_deps() -> GenerationDeps:
    global _deps
    if _deps is None:
        from ...agent.context.assembler import ContextAssembler
        from ...agent.context.store import PostgresStickyStore
        from ...agent.context.teacher_evidence import build_default_teacher_knowledge_evidence
        from ...db.session import session_factory
        from ...llm.ai_runs import PostgresAiRunsSink
        from ...llm.gateway import LlmGateway
        from ...prompts.registry import default_registry

        _deps = GenerationDeps(
            gateway=LlmGateway(PostgresAiRunsSink(session_factory())),
            assembler=ContextAssembler(
                PostgresStickyStore(session_factory()),
                knowledge=build_default_teacher_knowledge_evidence(session_factory()),
            ),
            prompt_renderer=default_registry(),
        )
    return _deps


def _event_of(chunk):
    """langgraph custom 事件 → (name, payload)。兼容两种形状：
    - 图内 get_stream_writer()({"event": name, "data": ...})（generation 图 _emit 写法）
    - AgentEvent(name, payload)（butler 等图）
    返回 (None, None) = values 状态帧（调用方跳过）。
    """
    if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] != "values":
        chunk = chunk[1]
    if isinstance(chunk, dict):
        return chunk.get("event"), chunk.get("data", {})
    if hasattr(chunk, "name"):
        return chunk.name, getattr(chunk, "payload", {})
    return None, None


def _sse(agent_events, *, finalize=None):
    """AgentEvent → SSE 帧；finalize(final_state) 在流结束时落库。"""
    import json

    async def gen():
        final: dict = {}
        async for chunk in agent_events:
            if isinstance(chunk, dict) and "__final_state__" in chunk:
                final = chunk["__final_state__"]
                continue
            name, payload = _event_of(chunk)
            if name:
                yield {"event": name, "data": json.dumps(payload, ensure_ascii=False, default=str)}
        if finalize is not None:
            await finalize(final)
        yield {"event": "done", "data": json.dumps({"finish_reason": "stop"}, ensure_ascii=False)}
    return gen()


@router.post("/generation/plan/outline")
async def plan_outline(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> dict:
    """教案两段式·第一段：五件套 → 大纲（环节/时长/目标草案/例题建议），不落库。"""
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise ApiError(ErrorCode.VALIDATION, "topic 必填")
    deps = get_generation_deps()
    graph = build_plan_graph(deps)
    query = _plan_query(body)
    config = {"configurable": {"thread_id": f"plan-outline-{uuid.uuid4().hex[:10]}"}}
    outline: dict | None = None
    async for chunk in graph.astream(
        {"stage": "outline", "ctx": _plan_ctx(body, user), "form": _plan_form(body), "query": query}, config,
        stream_mode=["custom", "values"],
    ):
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "values":
            state = chunk[1]
            outline = state.get("outline") or outline
    if not outline:
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "大纲生成失败（LLM 通道或提示词渲染异常），请重试")
    return ok(outline, request_id=request.state.request_id)


@router.post("/generation/plan")
async def plan_generate(request: Request, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """教案两段式·第二段（SSE）：meta → outline → section* → done；完成落 plans 草稿行。"""
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise ApiError(ErrorCode.VALIDATION, "topic 必填")
    deps = get_generation_deps()
    graph = build_plan_graph(deps)
    config = {"configurable": {"thread_id": f"plan-{uuid.uuid4().hex[:10]}"}}
    # outline 归一化：前端传 sections 数组（teacherV3.ts:248），图内 section_node 期望 dict
    outline_in = body.get("outline")
    if isinstance(outline_in, list):
        outline_in = {"sections": outline_in}
    from .deterministic import _topic_keywords
    from .deterministic import retrieve as _det_retrieve

    retrieval = await _det_retrieve(session_factory()(), _topic_keywords(topic))
    payload = {
        "stage": "sections",
        "ctx": _plan_ctx(body, user),
        "form": _plan_form(body),
        "query": _plan_query(body),
        "outline": outline_in or {},
        "retrieval": retrieval,
    }

    async def finalize(state: dict) -> str:
        async with session_factory()() as ss:
            from ...domains.contents.models import Plan

            row = Plan(
                topic=topic,
                class_id=_uuid_or_none(body.get("class_id")),
                lesson_type=str(body.get("lesson_type") or "new"),
                boards=state.get("sections") or [],
                outline=body.get("outline") or state.get("outline") or None,
                confirmed=False,
                created_by=_uuid_or_none(user.id),
            )
            ss.add(row)
            await ss.commit()
            return str(row.id)

    import json as _json

    async def gen():
        final: dict = {}
        try:
            async for chunk in graph.astream(payload, config, stream_mode=["custom", "values"]):
                if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "values":
                    final = chunk[1]
                    continue
                name, ev_payload = _event_of(chunk)
                if name:
                    yield {"event": name, "data": _json.dumps(ev_payload, ensure_ascii=False, default=str)}
        except Exception as e:  # 生成链异常 → 诚实 error 事件（提示词缺失/LLM 失败/JSON 解析失败），不假成功
            yield {"event": "error", "data": _json.dumps({"code": 50000, "message": f"生成失败：{e}"}, ensure_ascii=False)}
            yield {"event": "done", "data": _json.dumps({"finish_reason": "error"})}
            return
        yield {"event": "__plan_final__", "data": _json.dumps({"topic": topic}, ensure_ascii=False)}
        plan_id = await finalize(final)
        # 契约（S6 复核修正 2026-09-07）：done 必须带 plan_id——前端 PrepOutlineConfirm
        # 只在 done && data.plan_id 时 resolve，缺它则 Promise 永久悬挂（"正在生成完整教案"卡死）。
        yield {"event": "done", "data": _json.dumps({"finish_reason": "stop", "plan_id": plan_id})}

    return sse_response(gen())


@router.post("/generation/decks/{deck_id}/ai-element")
async def ai_element(request: Request, deck_id: str, body: dict, user: UserToken = Depends(get_current_user)) -> EventSourceResponse:
    """元素级 AI 建议（SSE）：suggest → diff → done。只出草稿 diff，不覆写（R5）。"""
    hint = (body.get("hint") or "").strip()
    slide_id = (body.get("slide_id") or "").strip()
    if not hint or not slide_id:
        raise ApiError(ErrorCode.VALIDATION, "slide_id 与 hint 必填")
    deps = get_generation_deps()
    graph = build_ai_element_graph(deps)
    config = {"configurable": {"thread_id": f"aiel-{uuid.uuid4().hex[:10]}"}}
    payload = {
        "ctx": {"deck_id": deck_id, "teacher_id": user.id},
        "element": {"slide_id": slide_id},
        "hint": hint,
    }
    return sse_response(_sse(graph.astream(payload, config, stream_mode=["custom", "values"])))


async def _deck_slots(template_id: str | None, session) -> dict:
    """取模板九页型槽位骨架（deck/outline.j2 与 layout_slide 共同消费）。模板缺失→种子默认模板。"""
    from sqlalchemy import select as _select

    from app.domains.contents.models import DeckTemplate
    from app.domains.contents.templates_seed import ensure_deck_templates

    await ensure_deck_templates(session)
    row = None
    if template_id:
        row = await session.get(DeckTemplate, _uuid_or_none(template_id))
    if row is None:
        row = (await session.execute(_select(DeckTemplate).order_by(DeckTemplate.id).limit(1))).scalar_one_or_none()
    return (row.slots if row and isinstance(row.slots, dict) else {}) or {}


@router.post("/generation/deck-outline")
async def deck_outline(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """课件大纲 Gate（两段式第一段，真 LLM）：页面结构计划 → 教师增删/调整后才逐页生成。"""
    topic = (body.get("topic") or "").strip()
    if not topic:
        raise ApiError(ErrorCode.VALIDATION, "topic 必填")
    deps = get_generation_deps()
    graph = build_deck_graph(deps)
    slots = await _deck_slots(body.get("template_id"), session)
    form = {
        "topic": topic,
        "textbook_version": str(body.get("textbook_version") or ""),
        "chapter": str(body.get("chapter") or ""),
        "lesson_type": str(body.get("course_type") or "新授课"),
        "page_budget": int(body.get("page_budget") or 12),
        "class_name": str(body.get("class_name") or ""),
        "extra_requirements": "；".join(str(r) for r in (body.get("requirements") or []) if r),
    }
    config = {"configurable": {"thread_id": f"deck-outline-{uuid.uuid4().hex[:10]}"}}
    outline: dict | None = None
    async for chunk in graph.astream(
        {"stage": "outline", "ctx": _plan_ctx(body, user), "form": form,
         "query": topic, "slots": slots}, config, stream_mode=["custom", "values"],
    ):
        if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "values":
            outline = (chunk[1] or {}).get("outline") or outline
    if not outline or not outline.get("pages"):
        # LLM 大纲不可用（超时/推理爆炸）→ 课程标准结构骨架兜底（用户预授权；内容仍走真实语料检索组装）
        from .deterministic import scaffold_outline

        outline = {"pages": scaffold_outline(topic, str(body.get("class_name") or "")),
                   "page_type_budget": {}, "missing_evidence": [], "scaffold": True}
    if not outline or not outline.get("pages"):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, "课件大纲生成失败，请重试")
    return ok({
        "outline": [
            {"title": str(p.get("title") or ""), "kind": str(p.get("page_type") or "concept_intro"),
             "ord": p.get("ord") or i + 1}
            for i, p in enumerate(outline["pages"])
        ],
        "matched": True,
        "note": "大纲由 AI 基于课题与检索证据生成（" + str(len(outline["pages"])) + " 页）——可增删/排序后确认",
        "reqs": [],
    }, request_id=request.state.request_id)


@router.post("/generation/deck")
async def deck_generate(request: Request, body: dict, user: UserToken = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> EventSourceResponse:
    """课件逐页生成（两段式第二段，SSE）：meta → outline → slide* → done{deck_id}。

    逐页：LLM 出 SlideSemanticSpec（IFC-001，禁像素字段）→ layout_slide 确定性布局 →
    元素 teacher_confirmed=False（R5 草稿态）→ decks 落库草稿行。
    """
    topic = (body.get("topic") or "").strip()
    outline_in = body.get("outline") or []
    if not topic or not isinstance(outline_in, list) or len(outline_in) < 1:
        raise ApiError(ErrorCode.VALIDATION, "topic 与 outline（确认后的页面结构）必填")
    deps = get_generation_deps()
    graph = build_deck_graph(deps)
    slots = await _deck_slots(body.get("template_id"), session)
    pages = [
        {"ord": i + 1,
         "page_type": _normalize_deck_page_type(o.get("kind") or o.get("page_type")),
         "title": str(o.get("title") or f"第 {i + 1} 页"),
         "intent": str(o.get("intent") or ""),
         "carries": list(o.get("carries") or [])}
        for i, o in enumerate(outline_in)
    ]
    form = {
        "topic": topic,
        "textbook_version": str(body.get("textbook_version") or ""),
        "chapter": str(body.get("chapter") or ""),
        "lesson_type": str(body.get("course_type") or "新授课"),
        "page_budget": len(pages),
        "class_name": str(body.get("class_name") or ""),
        "extra_requirements": str(body.get("material_name") or ""),
    }
    # 确定性兜底的检索上下文（真实教材 chunk + 真题，词面检索；embedding 通道失效不影响）
    from .deterministic import _topic_keywords
    from .deterministic import retrieve as _det_retrieve

    retrieval = await _det_retrieve(session, _topic_keywords(topic, pages[0]["title"] if pages else ""))
    from .rich_lesson import _rich_slides_from_body

    payload = {"stage": "slides", "ctx": _plan_ctx(body, user), "form": form,
               "query": topic, "slots": slots, "outline": {"pages": pages},
               "retrieval": retrieval, "rich_slides": _rich_slides_from_body(body)}
    config = {"configurable": {"thread_id": f"deck-{uuid.uuid4().hex[:10]}"}}

    import json as _json

    async def gen():
        final: dict = {}
        yield {"event": "meta", "data": _json.dumps({"stage": "slides", "total": len(pages)}, ensure_ascii=False)}
        # 前端大纲事件契约：data.items（页标题列表，进度显示用）
        yield {"event": "outline", "data": _json.dumps({"items": [p["title"] for p in pages]}, ensure_ascii=False)}
        try:
            async for chunk in graph.astream(payload, config, stream_mode=["custom", "values"]):
                if isinstance(chunk, tuple) and len(chunk) == 2 and chunk[0] == "values":
                    final = chunk[1] or {}
                    continue
                name, ev_payload = _event_of(chunk)
                if name and name != "outline":  # outline 已按契约先发
                    yield {"event": name, "data": _json.dumps(ev_payload, ensure_ascii=False, default=str)}
        except Exception as e:  # 诚实 error，不假成功
            yield {"event": "error", "data": _json.dumps({"code": 50000, "message": f"课件生成失败：{e}"}, ensure_ascii=False)}
            yield {"event": "done", "data": _json.dumps({"finish_reason": "error"})}
            return
        slides = final.get("slides") or []
        if not slides:
            yield {"event": "error", "data": _json.dumps({"code": 50300, "message": "全部页面未通过生成校验——请调整大纲后重试"}, ensure_ascii=False)}
            yield {"event": "done", "data": _json.dumps({"finish_reason": "error"})}
            return
        quality = final.get("quality") or final.get("rich_quality") or {}
        from .rich_lesson import quality_is_ready

        if not quality_is_ready(quality):
            yield {"event": "error", "data": _json.dumps({
                "code": 42201,
                "message": "课件尚未通过内容质量门禁，未保存为可用课件",
                "quality": quality,
            }, ensure_ascii=False)}
            yield {"event": "done", "data": _json.dumps({"finish_reason": "quality_gate"}, ensure_ascii=False)}
            return
        # decks 草稿行落库（source='AI'；元素 teacher_confirmed=False 由布局引擎保证，R5）
        async with session_factory()() as ss:
            from ...domains.contents.models import Deck

            row = Deck(
                title=topic,
                template_id=_uuid_or_none(body.get("template_id")),
                source="AI",
                class_id=_uuid_or_none(body.get("class_id")),
                slides=slides,
                created_by=_uuid_or_none(user.id),
            )
            ss.add(row)
            await ss.commit()
            deck_id = str(row.id)
        yield {"event": "done", "data": _json.dumps({"finish_reason": "stop", "deck_id": deck_id, "pages": len(slides)}, ensure_ascii=False)}

    return sse_response(gen())


def _plan_form(body: dict) -> dict:
    """五件套结构化输入（lesson/*.j2 模板契约变量）。前端契约发 duration（S14 漂移：同时收 duration_minutes）。"""
    return {
        "textbook_version": str(body.get("textbook_version") or body.get("grade_group") or ""),
        "chapter": str(body.get("chapter") or ""),
        "topic": str(body.get("topic") or ""),
        "lesson_no": body.get("lesson_no") or 1,
        "duration": body.get("duration_minutes") or body.get("duration") or 45,
        "lesson_type": str(body.get("lesson_type") or "新授课"),
        "key_points": str(body.get("key_points") or ""),
        "difficult_points": str(body.get("difficult_points") or ""),
        "extra_requirements": str(body.get("extra_requirements") or body.get("extra_requirements_text") or ""),
        "example_source": str(body.get("example_source") or ""),
    }


def _plan_ctx(body: dict, user: UserToken) -> dict:
    return {
        "teacher_id": user.id,
        "class_id": body.get("class_id"),
        "chapter": body.get("chapter"),
        "grade_group": body.get("grade_group") or "高中",
    }


def _plan_query(body: dict) -> str:
    parts = [str(body.get("topic") or "")]
    if body.get("duration_minutes"):
        parts.append(f"课时{body['duration_minutes']}分钟")
    if body.get("key_points"):
        parts.append(f"重点:{body['key_points']}")
    if body.get("difficult_points"):
        parts.append(f"难点:{body['difficult_points']}")
    if body.get("extra_requirements"):
        parts.append(str(body["extra_requirements"]))
    return "；".join(p for p in parts if p)


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _json_dumps(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
