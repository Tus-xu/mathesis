"""生成类图骨架（M3.3）：plan 两段式 / review-pack / ai-element。

共同纪律：
- KNR 红线 9：deck/plan/variant/review/classroom_ai/quiz_gen 六类生成的第一节点
  必须是 assemble_evidence（EvidencePack → ai_runs 紧凑 manifest 由调用方落账）；
- 红线 2：提示词不进 B1 代码——`prompt_renderer` seam 必须注入（B2 `app/prompts/`
  交付前传 fake，交付后换 Jinja2 渲染器），缺失时显式报错，绝不内联业务提示词；
- 红线 6：产物一律草稿态（confirmed=False 字段随载荷返回，落库走各域确认端点）；
- R5：ai-element 只出 diff（V3ElementDiff），绝不直接改 deck；
- SSE 序列（§7.4）：plan=meta→outline→section*→done；review=meta→slide*→done；
  ai-element=meta→suggest→diff→done。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Protocol, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, model_validator

from app.agent.context.assembler import EVIDENCE_REQUIRED_FEATURES, ContextAssembler
from app.llm.gateway import AiRunMeta


class PromptRenderer(Protocol):
    """B2 提示词渲染 seam：`render(template_name, **ctx) -> str`。"""

    def render(self, template_name: str, **ctx: Any) -> str: ...


class RendererMissing(RuntimeError):
    """红线 2：B2 prompts 未接入时禁止编造提示词。"""


class PlanSection(BaseModel):
    title: str
    minutes: int
    goal: str
    key_points: list[str] = Field(default_factory=list)


class PlanOutline(BaseModel):
    topic: str
    total_minutes: int
    sections: list[PlanSection]

    @model_validator(mode="after")
    def _minutes_sum(self) -> "PlanOutline":
        # L3 反套话验收：环节时长总和必须等于课时总长（45 分钟课程序断言）
        if sum(s.minutes for s in self.sections) != self.total_minutes:
            raise ValueError("环节时长加总 ≠ 课时总长（反套话清单：时长闭合断言）")
        if self.total_minutes not in (40, 45):
            raise ValueError("课时总长仅支持 40/45 分钟")
        return self


class ElementDiff(BaseModel):
    """V3ElementDiff 草稿：只描述对既有元素的变更，绝不含整页坐标（R5）。"""

    target_element_id: str
    action: str  # replace_text/replace_formula/add_note…
    payload: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False  # 红线 6：草稿态


class GenState(TypedDict, total=False):
    stage: str
    ctx: dict[str, Any]
    form: dict[str, Any]  # 五件套结构化输入（lesson/*.j2 模板契约，B2）
    query: str
    evidence: dict[str, Any] | None
    outline: dict[str, Any]
    sections: list[dict[str, Any]]  # 第二段逐板块累计（落库用）
    retrieval: dict[str, Any]  # 确定性兜底的检索上下文（GenState 与 DeckState 共用）
    assignment_id: str
    stats: dict[str, Any]
    ranked: list[dict[str, Any]]
    element: dict[str, Any]
    hint: str
    diff: dict[str, Any]


class DeckState(TypedDict, total=False):
    """deck 两段式图状态（M2-B）。StateGraph(dict) 不保通道键——必须用 TypedDict。"""
    stage: str
    ctx: dict[str, Any]
    form: dict[str, Any]
    query: str
    slots: dict[str, Any]
    evidence: dict[str, Any] | None
    outline: dict[str, Any]
    slides: list[dict[str, Any]]
    retrieval: dict[str, Any]  # 确定性兜底的检索上下文（真实语料 chunk/真题）
    rich_slides: list[dict[str, Any]]  # 学生双师课堂 RichLessonSlide v1（教师投影适配入口）
    rich_quality: dict[str, Any]
    quality: dict[str, Any]


def _emit(name: str, data: dict) -> None:
    try:
        get_stream_writer()({"event": name, "data": data})
    except RuntimeError:
        pass  # LangGraph 上下文外调用（直接 Python 测试/Worker 非流式路径）——静默跳过


class GenerationDeps:
    """生成图共享依赖。`assembler` 为 B1 ContextAssembler（KNR 组装）；B4 批改统计
    经 stats_provider 注入（缺席走 state.stats）。"""

    def __init__(
        self,
        *,
        gateway=None,
        assembler: ContextAssembler | None = None,
        prompt_renderer: PromptRenderer | None = None,
        memory=None,
        stats_provider=None,
    ) -> None:
        self.gateway = gateway
        self.assembler = assembler
        self.prompt_renderer = prompt_renderer
        self.memory = memory
        self.stats_provider = stats_provider

    def require_renderer(self) -> PromptRenderer:
        if self.prompt_renderer is None:
            raise RendererMissing("B2 app/prompts 未接入：拒绝内联业务提示词（红线 2），注入 prompt_renderer 后重试")
        return self.prompt_renderer


async def _chat_json(deps: GenerationDeps, *, use_case: str, prompt: str, feature: str) -> str:
    """长文/结构化生成统一走网关；ai_runs 由网关落账（feature/prompt 版本随 meta 传）。

    内部走流式聚合（M2-B 根因修复 2026-09-08）：Spark MaaS 非流式通道有服务端缓冲，
    一句话也要 51s；流式 + thinking disabled 单句 7.8s。结构化 JSON 由本函数聚合后再解析。
    """
    if deps.gateway is None:
        raise RendererMissing("gateway 未注入：无法调用模型")
    parts: list[str] = []
    result = await deps.gateway.chat(
        [{"role": "user", "content": prompt}], stream=True, use_case=use_case,
        metadata=AiRunMeta(feature=feature),
    )
    if hasattr(result, "__aiter__"):  # 真网关流式（Spark 走此路：非流式通道服务端缓冲 51s+/句）
        thinking_parts: list[str] = []
        async for chunk in result:
            if chunk.kind == "token" and chunk.text:
                parts.append(chunk.text)
            elif chunk.kind == "thinking" and chunk.text:
                thinking_parts.append(chunk.text)
        content = "".join(parts)
        if content.strip():
            return content
        # MaaS spark-x2.5-4b 无视 thinking:disabled：大提示词正文全落 reasoning
        # （并行会话 a838e66 非流式同款结论）——reasoning 里的 JSON 同样可用，回退取之
        thinking_text = "".join(thinking_parts)
        if thinking_text.strip():
            return thinking_text
        return ""
    return result.content  # 测试 FakeBackend：ChatResult 直返


def _retrieval_sections(state: GenState, retrieval: dict) -> dict:
    """检索组装教案板块（主路径）：教材原句 + 真题 → 三栏内容（teacher_note 溯源）。"""
    outline = state.get("outline") or {}
    sections = outline.get("sections") or []
    chunks = retrieval.get("chunks") or []
    questions = retrieval.get("questions") or []
    accumulated: list[dict[str, Any]] = []
    q_used = 0

    for i, section in enumerate(sections):
        name = str(section.get("name") or section.get("title") or f"环节 {i + 1}")
        minutes = int(section.get("minutes") or 0) or 5
        goal = str(section.get("goal") or "")
        points: list[str] = []
        formula = None
        source_note = ""

        if "例题" in name or "练习" in name or "检测" in name:
            if q_used < len(questions):
                q = questions[q_used]
                q_used += 1
                stem = q.get("stem") or ""
                points = [s.strip() for s in re.split(r"[。；;\n]", stem) if 6 <= len(s.strip()) <= 40][:3]
                if not points:
                    points = [stem[:40]]
                source_note = f"[检索组装] 真题：{q.get('source') or '题库'}"
            else:
                points = [f"{name}：基于本课要点当堂练习"]
                source_note = "[检索组装] 题库真题已用完"
        else:
            for c in chunks:
                text = c.get("content") or ""
                for s in re.split(r"[。；;\n]", text):
                    s = s.strip()
                    if 8 <= len(s) <= 40 and s not in points:
                        points.append(s)
                    if len(points) >= 4:
                        break
                if len(points) >= 4:
                    break
            if not points:
                points = [f"{name}：围绕课题展开教学"]
            for c in chunks:
                m = re.search(r"\$\$?(.+?)\$\$?", c.get("content") or "")
                if m and 3 <= len(m.group(1)) <= 120:
                    formula = m.group(1)
                    break
            source_note = f"[检索组装] 依据：{'、'.join(c.get('doc_ref', '') for c in chunks[:2])}" if chunks else "[检索组装] 无检索命中"

        content = {
            "board": name,
            "teacher_activity": [f"{name}（{minutes} 分钟）：围绕要点讲解并提问"],
            "student_activity": ["听讲、记录要点、参与互动"],
            "design_intent": [f"达成目标：{goal}" if goal else "落实本环节教学目标"],
            "key_points_backend": points,
        }
        if formula:
            content["formula_hint"] = formula
        if source_note:
            content["source_note"] = source_note
        item = {**section, "content": content, "confirmed": False}
        accumulated.append(item)
        _emit("section", item)

    return {"sections": accumulated}


def build_plan_graph(deps: GenerationDeps):
    """plan 两段式：第一段 outline（不落库），教师确认后携 outline 跑第二段 sections。

    第一段图输入：{stage:'outline', ctx, query}；第二段：{stage:'sections', ctx, outline}。
    """
    async def assemble_node(state: GenState) -> dict:
        # KNR 红线 9：plan 属六类强制证据特征——无 EvidencePack 不得调 LLM
        assert "plan" in EVIDENCE_REQUIRED_FEATURES
        if deps.assembler is None:
            raise RuntimeError("KNR 红线 9：plan 生成缺少 EvidencePack 组装器（ContextAssembler 未注入）")
        ctx = state.get("ctx") or {}
        pack = await deps.assembler.assemble(ctx, state.get("query", ""), feature="plan")
        _emit("meta", {"stage": state.get("stage", "outline"), "evidence": True, "manifest": pack.to_manifest()})
        return {"evidence": pack.model_dump()}

    async def outline_node(state: GenState) -> dict:
        renderer = deps.require_renderer()
        # B2 模板契约（lesson/outline.j2）：form 五件套 + evidence 证据包
        prompt = renderer.render(
            "lesson/outline.j2",
            form=state.get("form") or {},
            evidence=state.get("evidence"),
        )
        _emit("thinking", {"text": "按课标与学情先搭环节骨架，再逐环节展开。"})
        raw = await _chat_json(deps, use_case="long_form", prompt=prompt, feature="plan_gen")
        data = _extract_json(raw)
        # B2 输出（boards/objectives/key_points，十板块契约）→ 前端 V3PlanOutline 形状桥接。
        # 不走 PlanOutline 严格校验（模型可能违反分钟闭合；大纲本就要教师增删调整）。
        form = state.get("form") or {}
        total = int(form.get("duration") or 45)
        sections = []
        for i, b in enumerate(data.get("boards") or []):
            sections.append({
                "id": f"sec-{i + 1}",
                "name": b.get("name") or "",
                "minutes": int(b.get("minutes") or 0),
                "goal": b.get("goal") or "",
                "example_hint": b.get("example_hint"),
            })
        # 分钟闭合兜底：模型违反"时长总和=课时"硬约束时确定性修正（差额记到最长环节，不静默展示矛盾）
        drift = total - sum(s["minutes"] for s in sections)
        if drift != 0 and sections:
            target = max(sections, key=lambda s: s["minutes"])
            target["minutes"] = max(1, target["minutes"] + drift)
        # V3PlanOutline 契约形状（teacherV3.ts:267）：example_suggestion、duration、
        # objectives string[]、keypoints{major,hard}（审计 #6 键位漂移归一）
        for i, s in enumerate(sections):
            s["example_suggestion"] = s.pop("example_hint", None)
        objs = data.get("objectives") or {}
        kp = data.get("key_points") or {}
        outline = {
            "topic": form.get("topic") or state.get("query", ""),
            "duration": total,
            "total_minutes": total,
            "sections": sections,
            "notes": [str(x) for x in (data.get("missing_evidence") or [])],
            "objectives": _flat_strs(objs),
            "keypoints": {
                "major": [str(kp["重点"])] if kp.get("重点") else [],
                "hard": [str(kp["难点"])] if kp.get("难点") else [],
            },
        }
        _emit("outline", {**outline, "confirmed": False})
        return {"outline": outline}

    async def section_node(state: GenState) -> dict:
        # 主路径：检索组装（实时可用、真实语料溯源）；Spark 大提示词推理爆炸不可交互
        retrieval = state.get("retrieval") or {}
        if retrieval.get("chunks") or retrieval.get("questions"):
            return _retrieval_sections(state, retrieval)
        renderer = deps.require_renderer()
        form = state.get("form") or {}
        outline = state.get("outline") or {}
        accumulated: list[dict[str, Any]] = []
        for section in outline.get("sections", []):
            # 前端确认大纲只回传 {id,name,minutes}——board 字段补默认值（模板严格模式缺键即抛）
            board = {
                "name": section.get("name") or "",
                "minutes": int(section.get("minutes") or 0),
                "goal": section.get("goal") or "",
                "example_hint": section.get("example_hint"),
            }
            # B2 模板契约（lesson/section.j2）：form + outline(goals/key_points) + board + evidence
            prompt = renderer.render(
                "lesson/section.j2",
                form=form,
                outline={
                    "goals": (outline.get("objectives") or {}),
                    "key_points": outline.get("key_points") or {},
                },
                board=board,
                evidence=state.get("evidence"),
            )
            body = _extract_json(await _chat_json(deps, use_case="long_form", prompt=prompt, feature="plan_gen"))
            item = {**board, "content": body, "confirmed": False}
            accumulated.append(item)
            _emit("section", item)
        return {"sections": accumulated}

    builder = StateGraph(GenState)
    builder.add_node("assemble_evidence", assemble_node)
    builder.add_node("outline", outline_node)
    builder.add_node("sections", section_node)
    builder.add_edge(START, "assemble_evidence")
    builder.add_conditional_edges(
        "assemble_evidence",
        lambda s: "sections" if s.get("stage") == "sections" else "outline",
        {"outline": "outline", "sections": "sections"},
    )
    builder.add_edge("outline", END)
    builder.add_edge("sections", END)
    return builder.compile()


def build_review_graph(deps: GenerationDeps):
    """review-pack：批改统计（B4 stats_provider）→ 错误率排序 → 讲评页序列（§7.4）。"""

    async def rank_node(state: GenState) -> dict:
        _emit("meta", {"stage": "review", "assignment_id": state.get("assignment_id")})
        if deps.stats_provider is not None:
            stats = await deps.stats_provider(state.get("assignment_id"))
        else:
            stats = state.get("stats") or {}
        ranked = sorted(stats.get("questions", []), key=lambda q: -q.get("error_rate", 0.0))
        return {"ranked": ranked}

    async def pages_node(state: GenState) -> dict:
        renderer = deps.require_renderer()
        for page_type, questions in (
            ("chart", []),
            ("error_cause", state["ranked"][:3]),
            ("advice", state["ranked"][:2]),
            ("variant", state["ranked"][:3]),
        ):
            prompt = renderer.render(f"review_{page_type}.txt", questions=questions)
            raw = await _chat_json(deps, use_case="long_form", prompt=prompt, feature="review")
            _emit("slide", {"page_type": page_type, "payload": _extract_json(raw), "confirmed": False})
        return {}

    builder = StateGraph(GenState)
    builder.add_node("rank", rank_node)
    builder.add_node("pages", pages_node)
    builder.add_edge(START, "rank")
    builder.add_edge("rank", "pages")
    builder.add_edge("pages", END)
    return builder.compile()


def build_ai_element_graph(deps: GenerationDeps):
    """ai-element：slide 元素 + hint → V3ElementDiff 草稿（suggest→diff→done，R5）。"""

    async def suggest_node(state: GenState) -> dict:
        _emit("meta", {"stage": "ai-element"})
        renderer = deps.require_renderer()
        _emit("suggest", {"note": state.get("hint", "")})
        prompt = renderer.render("ai_element_diff.txt", element=state.get("element"), hint=state.get("hint", ""))
        raw = await _chat_json(deps, use_case="chat", prompt=prompt, feature="deck")
        diff = ElementDiff.model_validate({**_extract_json(raw), "confirmed": False})
        _emit("diff", diff.model_dump())
        return {"diff": diff.model_dump()}

    builder = StateGraph(GenState)
    builder.add_node("suggest", suggest_node)
    builder.add_edge(START, "suggest")
    builder.add_edge("suggest", END)
    return builder.compile()


def _flat_strs(value) -> list[str]:
    """内嵌结构（dict/list/str 混合）→ 扁平字符串数组（V3PlanOutline objectives 等契约口径）。"""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(_flat_strs(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_flat_strs(v))
        return out
    return []


def _extract_json(raw: str) -> dict:
    """从模型输出提取 JSON（容忍 ```json 围栏与尾随文本）；解析失败抛错交由重试/降级层。"""
    import json

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    candidate = fenced.group(1) if fenced else raw
    start = candidate.find("{")
    if start == -1:
        raise ValueError(f"模型输出不含 JSON：{raw[:120]}")
    try:
        obj, _end = json.JSONDecoder().raw_decode(candidate[start:])  # 容忍 JSON 后的尾随说明文字
        return obj
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型输出 JSON 解析失败：{exc}｜原文片段：{candidate[start:start+160]}") from exc


def build_deck_graph(deps: GenerationDeps):
    """deck 两段式（M2-B 生死 Gate，2026-09-08）：
    assemble_evidence（KNR 红线 9）→ outline（页面结构计划，deck/outline.j2）
    → slides（逐页 SlideSemanticSpec，deck/slide.j2 + IFC-001 布局引擎）。

    SSE：meta → outline{pages} → slide*{index,title,note} → done。
    教师确认大纲后以 stage='slides' + outline 进入第二段。
    """
    from app.pagination.layout import Element, LayoutError, layout_slide
    from app.pagination.schemas import SlideSemanticSpec

    async def assemble_node(state: dict) -> dict:
        assert "deck" in EVIDENCE_REQUIRED_FEATURES
        if deps.assembler is None:
            raise RuntimeError("KNR 红线 9：deck 生成缺少 EvidencePack 组装器")
        pack = await deps.assembler.assemble(state.get("ctx") or {}, state.get("query", ""), feature="deck")
        _emit("meta", {"stage": state.get("stage", "outline"), "evidence": True, "manifest": pack.to_manifest()})
        return {"evidence": pack.model_dump()}

    def _slot_lines(slots: dict | None) -> list[str]:
        out: list[str] = []
        if isinstance(slots, dict):
            for pt, s in slots.items():
                if pt.startswith("_") or not isinstance(s, dict):
                    continue
                out.append(
                    f"{pt}（{s.get('pedagogical_role', '')}；max_text={s.get('max_text', 5)}；"
                    f"formula_capacity={s.get('formula_capacity', 1)}；visual_capacity={s.get('visual_capacity', 1)}）"
                )
        return out or ["concept_intro（概念新授）", "worked_example（例题精讲）", "summary（小结）"]

    async def outline_node(state: dict) -> dict:
        renderer = deps.require_renderer()
        prompt = renderer.render(
            "deck/outline.j2",
            form=state.get("form") or {},
            slot_schemas=_slot_lines(state.get("slots")),
            evidence=state.get("evidence"),
        )
        _emit("thinking", {"text": "先规划页面结构（每页只讲一件事），教师确认后再逐页生成。"})
        raw = await _chat_json(deps, use_case="long_form", prompt=prompt, feature="deck_gen")
        data = _extract_json(raw)
        outline = {
            "pages": data.get("pages") or [],
            "page_type_budget": data.get("page_type_budget") or {},
            "missing_evidence": data.get("missing_evidence") or [],
        }
        _emit("outline", {"pages": [p.get("title", "") for p in outline["pages"]], "items": [p.get("title", "") for p in outline["pages"]], **outline})
        return {"outline": outline}

    async def slides_node(state: dict) -> dict:
        """逐页并发生成（slide.j2 单页模板 × Semaphore(8)，借鉴双师课堂并发架构）。

        相对旧「全文一次成稿」（单次 LLM 调用全部页，420s 预算易超时）：
        - 每页独立调用模型（流式聚合单页 ~8-30s），页间并行 8 路 → 总耗时≈单页耗时×页数/8；
        - 每页产出 SlideSemanticSpec → layout_slide 确定性布局 → V3Element（前端编辑器兼容）；
        - 确定性修复优先于丢弃：超长要点截断、非法 visual 剥离；坏页诚实标注跳过。
        """
        # 学生双师课堂已经产出结构化 blocks/claims/evidence；教师端只复用语义与
        # 数学载荷，再走自己的 16:9 layout，不把 DualView 的双栏 UI 带进课件。
        retrieval = state.get("retrieval") or {}
        evidence = state.get("evidence") or {}

        def _rich_page_sources(raw_slide: dict) -> list[dict[str, Any]]:
            packed = evidence.get("sources") if isinstance(evidence, dict) else None
            if isinstance(packed, list) and packed:
                return copy.deepcopy(packed)
            derived: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for chunk in retrieval.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                source_id = str(chunk.get("doc_ref") or "").strip()
                if source_id and source_id not in seen_ids:
                    seen_ids.add(source_id)
                    derived.append({"source_id": source_id, "source_type": "textbook",
                                    "locator": str(chunk.get("locator") or source_id), "used": True})
            for question in retrieval.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                source = str(question.get("source") or "题库").strip()
                source_id = f"quiz:{source}"
                if source_id not in seen_ids:
                    seen_ids.add(source_id)
                    derived.append({"source_id": source_id, "source_type": "quiz",
                                    "locator": source, "used": True})
            # 学生/模型自报的 citation 只作为线索，不能越过教师端真实检索证据门禁。
            return derived

        rich_slides = state.get("rich_slides")
        if isinstance(rich_slides, list) and rich_slides:
            from app.domains.generation.rich_lesson import enrich_rich_slide, project_rich_lesson

            grounded = [
                enrich_rich_slide(slide, source_evidence=_rich_page_sources(slide))
                for slide in rich_slides
                if isinstance(slide, dict)
            ]
            projected = project_rich_lesson(grounded, slots=state.get("slots") or {})
            for index, slide in enumerate(projected["slides"]):
                _emit("slide", {"index": index, "title": slide.get("elements", [{}])[0].get("html", ""), "rich": True})
            quality = {
                "status": projected["status"],
                "reasons": projected["quality_reasons"],
                "ir_version": projected["ir_version"],
            }
            return {"slides": projected["slides"], "quality": quality, "rich_quality": quality}

        renderer = deps.require_renderer()
        form = state.get("form") or {}
        outline = state.get("outline") or {}
        pages = outline.get("pages") or []
        slot_map = state.get("slots") if isinstance(state.get("slots"), dict) else {}
        def _page_sources(raw_slide: dict) -> list[dict[str, Any]]:
            """每页都保留可追溯来源；模型没回 rich evidence 时从已组装证据/兜底检索补齐。"""
            packed = evidence.get("sources") if isinstance(evidence, dict) else None
            if isinstance(packed, list) and packed:
                return copy.deepcopy(packed)
            derived: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for chunk in retrieval.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                source_id = str(chunk.get("doc_ref") or "").strip()
                if source_id and source_id not in seen_ids:
                    seen_ids.add(source_id)
                    derived.append({"source_id": source_id, "source_type": "textbook",
                                    "locator": str(chunk.get("locator") or source_id), "used": True})
            for question in retrieval.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                source = str(question.get("source") or "题库").strip()
                source_id = f"quiz:{source}"
                if source_id not in seen_ids:
                    seen_ids.add(source_id)
                    derived.append({"source_id": source_id, "source_type": "quiz",
                                    "locator": source, "used": True})
            # 模型自报的 source_evidence 不能替代服务器组装/检索的真实来源。
            return derived

        import asyncio as _asyncio

        from app.domains.generation.deterministic import _wrap_math, compose_slide

        # 逐页独立产出（页间无依赖）：每页一次 slide.j2 渲染 + 一次 LLM 调用
        page_failures: dict[int, str] = {}

        async def _gen_one(idx: int, page: dict) -> tuple[int, dict | None]:
            pt = str(page.get("page_type") or "concept_intro")
            try:
                page_prompt = renderer.render(
                    "deck/slide.j2",
                    form=form,
                    page={**page, "ord": page.get("ord") or idx + 1},
                    slot_schemas=_slot_lines(slot_map),
                    evidence=evidence,
                )
                # 单页预算 120s（逐页并行的单页等待上限；超时转确定性兜底）
                raw = await _asyncio.wait_for(
                    _chat_json(deps, use_case="long_form", prompt=page_prompt, feature="deck_gen"),
                    timeout=300.0,
                )
                data = _extract_json(raw)
                return idx, data if isinstance(data, dict) else None
            except Exception as _page_exc:  # noqa: BLE001 —— 单页失败/超时：交给确定性兜底
                page_failures[idx] = f"{type(_page_exc).__name__}: {str(_page_exc)[:180]}"
                try:
                    with open(r"C:\Users\<user>\AppData\Local\Temp\deck_slide_err.log", "a", encoding="utf-8") as _f:
                        _f.write(f"idx={idx} page_type={page.get('page_type')} err={type(_page_exc).__name__}:{str(_page_exc)[:300]}\n")
                except Exception:
                    pass
                return idx, None

        # 8 路并发限流（对齐双师课堂 Semaphore(8)）
        _sem = _asyncio.Semaphore(8)

        async def _gen_page(idx: int, page: dict) -> tuple[int, dict | None]:
            async with _sem:
                return await _gen_one(idx, page)

        if pages:
            results = await _asyncio.gather(*[_gen_page(i, p) for i, p in enumerate(pages)])
        else:
            results = []
        raw_slides: dict[int, dict] = {}
        for idx, data in results:
            if isinstance(data, dict) and (data.get("points") or data.get("blocks")):
                raw_slides[idx + 1] = data

        # 确定性兜底：LLM 失败/空结果页 → 检索组装（真题/教材 chunk，词面检索）
        if retrieval:
            used_questions: set[int] = set()
            for i, page in enumerate(pages):
                if i + 1 in raw_slides:
                    continue
                composed = compose_slide(
                    {**page, "page_type": str(page.get("page_type") or "concept_intro")},
                    retrieval,
                    used_questions=used_questions,
                )
                if composed is not None:
                    raw_slides[i + 1] = composed
                else:
                    page_failures.setdefault(i, "检索无命中，无法生成有证据内容")
        # 模型若直接返回学生双师课堂的 RichLessonSlide，必须先走共享语义适配器，
        # 禁止被下方 legacy points 清洗器压扁。全页 rich 时保持原顺序和教师投影分页。
        rich_inputs = []
        for i, page in enumerate(pages):
            candidate = raw_slides.get(i + 1)
            if isinstance(candidate, dict) and isinstance(candidate.get("blocks"), list) and candidate.get("blocks"):
                rich_inputs.append({
                    **candidate,
                    "order": candidate.get("order") or i + 1,
                    "page_type": candidate.get("page_type") or page.get("page_type") or "concept_intro",
                    "title": candidate.get("title") or page.get("title") or f"第 {i + 1} 页",
                })
        if rich_inputs and len(rich_inputs) == len(pages):
            from app.domains.generation.rich_lesson import enrich_rich_slide, project_rich_lesson

            grounded = [
                enrich_rich_slide(slide, source_evidence=_page_sources(slide))
                for slide in rich_inputs
            ]
            projected = project_rich_lesson(grounded, slots=slot_map)
            for index, slide in enumerate(projected["slides"]):
                _emit("slide", {"index": index, "title": slide.get("title", ""), "rich": True})
            quality = {
                "status": projected["status"],
                "reasons": projected["quality_reasons"],
                "ir_version": projected["ir_version"],
            }
            return {"slides": projected["slides"], "quality": quality, "rich_quality": quality}
        # 无 LLM、无检索材料时不再伪造“待补充”成品页；缺页由质量结果和 SSE 明确暴露。
        from app.domains.generation.rich_lesson import dedupe_formula_fields

        formula_pages = dedupe_formula_fields(
            [raw_slides[i + 1] for i in range(len(pages)) if i + 1 in raw_slides]
        )
        formula_index = 0
        for i in range(len(pages)):
            if i + 1 in raw_slides:
                raw_slides[i + 1] = formula_pages[formula_index]
                formula_index += 1
        for i in range(len(pages)):
            if i + 1 not in raw_slides:
                page_failures.setdefault(i, "页面没有可验证的生成内容")

        produced = []
        for i, page in enumerate(pages):
            pt = str(page.get("page_type") or "concept_intro")
            raw_slide = raw_slides.get(i + 1)
            if not isinstance(raw_slide, dict):
                _emit("slide", {"index": i, "title": str(page.get("title") or ""),
                                "note": f"该页缺失于生成结果，已跳过：{page_failures.get(i, '未知原因')}", "skipped": True})
                continue
            # 确定性修复：points 截断到 5 条/40 字；visual 校验失败剥离；禁字段直接不传
            points = [str(x)[:40] for x in (raw_slide.get("points") or []) if str(x).strip()][:5]
            clean = {
                "page_type": pt,
                "title": str(raw_slide.get("title") or page.get("title") or "")[:60] or f"第 {i + 1} 页",
                "points": points,
                "formula": raw_slide.get("formula") if isinstance(raw_slide.get("formula"), str) and raw_slide.get("formula").strip() else None,
                "teacher_note": raw_slide.get("teacher_note") if isinstance(raw_slide.get("teacher_note"), str) else None,
            }
            task = raw_slide.get("task") if isinstance(raw_slide.get("task"), dict) else None
            v = raw_slide.get("visual")
            if isinstance(v, dict) and v.get("kind") == "preset" and v.get("preset"):
                clean["visual"] = {"kind": "preset", "preset": str(v["preset"]),
                                   "params": {k: float(x) for k, x in (v.get("params") or {}).items() if isinstance(x, (int, float))}}
            elif isinstance(v, dict) and v.get("kind") == "figure3d" and isinstance(v.get("scene"), dict):
                clean["visual"] = {"kind": "figure3d", "scene": v["scene"]}
            elif isinstance(v, dict) and v.get("kind") == "boardJson":
                board_json = v.get("boardJson") or v.get("board_json")
                if not isinstance(board_json, dict):
                    board_json = {key: value for key, value in v.items() if key != "kind"}
                if board_json:
                    clean["visual"] = {"kind": "boardJson", "boardJson": copy.deepcopy(board_json)}
            # 例题页注入：worked_example/practice 页优先用题库真题（题干入 points、
            # 答案/解析/来源进 teacher_note；有 stem_image 则作为例题配图 visual 落位）。
            if pt in ("worked_example", "practice") and retrieval:
                _qpool = [q for q in retrieval.get("questions") or [] if q.get("stem")]
                if _qpool:
                    _q = _qpool[0]
                    if not any(_q["stem"][:12] in (p or "") for p in points):
                        _stem_pts = [str(_q["stem"])[:200]]
                        if _q.get("stem_image"):
                            # 图片题：题干即图，points 放图注，visual 挂 stem_image 供前端 image 元素渲染
                            _stem_pts = [f"（例题配图，题面见图片）{_q['stem'][:80]}"]
                            clean["visual"] = {"kind": "boardJson",
                                               "boardJson": {"__stem_image__": _q["stem_image"]}}
                        note_parts = [str(_q.get("answer") or "")[:60]]
                        if _q.get("analysis"):
                            note_parts.append(f"解析：{_q['analysis'][:80]}")
                        note_parts.append(f"题目来源：{_q.get('source') or '题库'}")
                        clean["teacher_note"] = "\n".join(x for x in note_parts if x)
                        clean["points"] = (_stem_pts + points)[:5]
            try:
                spec = SlideSemanticSpec.model_validate(clean)
            except Exception as e:  # noqa: BLE001 —— 结构性坏页：诚实跳过
                _emit("slide", {"index": i, "title": clean["title"],
                                "note": f"该页未通过规格校验（{e}），已跳过——请在编辑器中手工补页", "skipped": True})
                continue
            slots = slot_map.get(pt) or slot_map.get("_default") or {}
            # LLM 要点含裸 LaTeX → $..$ 定界（导出 OMML 混排 + 前端 KaTeX 共用约定）
            spec = spec.model_copy(update={"points": [_wrap_math(p_text) for p_text in spec.points]})
            try:
                elements = [e.model_dump() for e in layout_slide(spec, slots)]
            except LayoutError:
                # 槽位超员：确定性裁点重排（宁加页不缩字的页内兜底）；仍失败则诚实跳过该页
                max_text = int(slots.get("max_text", 5) or 5)
                trimmed = spec.model_copy(update={"points": spec.points[:max_text]})
                try:
                    elements = [e.model_dump() for e in layout_slide(trimmed, slots)]
                    spec = trimmed
                except LayoutError as e2:
                    _emit("slide", {"index": i, "title": spec.title,
                                    "note": f"该页与模板槽位不兼容（{e2}），已跳过——请在编辑器中手工补页", "skipped": True})
                    continue
            slide_payload = {
                "id": f"slide-{i + 1}",
                "layout": _page_type_to_layout(pt),
                "elements": elements,
                "notes": spec.teacher_note or "",
                "page_type": pt,
                "ir_version": raw_slide.get("ir_version") or "teacher-slide-v1",
                "semantic_blocks": copy.deepcopy(raw_slide.get("blocks") or []) if isinstance(raw_slide.get("blocks"), list) else [],
                "source_evidence": _page_sources(raw_slide),
                "verification_result": copy.deepcopy(raw_slide.get("verification_result"))
                if isinstance(raw_slide.get("verification_result"), dict)
                else {"status": "needs_review", "reasons": ["模型未返回数学验证结果"]},
            }
            if task:
                slide_payload["task"] = {
                    key: str(task[key])[:200]
                    for key in ("goal", "context", "student_action", "expected_output", "teacher_prompt", "feedback")
                    if task.get(key)
                }
            produced.append(slide_payload)
            _emit("slide", {"index": i, "done": len(produced),
                            "note": f"{spec.title}（{len(elements)} 个元素，未确认草稿）"})
        quality_reasons: list[str] = []
        if not evidence.get("sources") and not retrieval.get("chunks") and not retrieval.get("questions"):
            quality_reasons.append("无可用教材/题库证据")
        quality_reasons.extend(
            f"第{i + 1}页：{reason}" for i, reason in sorted(page_failures.items())
        )
        for i, slide_payload in enumerate(produced):
            if not slide_payload.get("source_evidence"):
                quality_reasons.append(f"第{i + 1}页：缺少 source_evidence")
            verification = slide_payload.get("verification_result")
            status = verification.get("status") if isinstance(verification, dict) else ""
            if status != "verified":
                quality_reasons.append(f"第{i + 1}页：数学验证状态不是 verified")
        has_task = any(
            isinstance(slide_payload.get("task"), dict)
            and all(str(slide_payload["task"].get(key) or "").strip()
                    for key in ("goal", "context", "student_action", "expected_output"))
            for slide_payload in produced
        )
        if produced and not has_task:
            quality_reasons.append("课件缺少可执行课堂任务（目标/学生行动/可检查产出）")
        return {
            "slides": produced,
            "quality": {
                "status": "ready" if produced and not quality_reasons else "needs_review",
                "reasons": quality_reasons,
            },
        }

    builder = StateGraph(DeckState)
    builder.add_node("assemble_evidence", assemble_node)
    builder.add_node("outline", outline_node)
    builder.add_node("slides", slides_node)
    builder.add_edge(START, "assemble_evidence")
    builder.add_conditional_edges(
        "assemble_evidence",
        lambda s: "slides" if s.get("stage") == "slides" else "outline",
        {"outline": "outline", "slides": "slides"},
    )
    builder.add_edge("outline", END)
    builder.add_edge("slides", END)
    return builder.compile()


_PAGE_TYPE_TO_LAYOUT = {
    "concept_intro": "definition",
    "visual_definition": "definition",
    "derivation": "derivation",
    "formula_focus": "keypoints",
    "worked_example": "example",
    "compare": "definition",
    "common_error": "keypoints",
    "practice": "variation",
    "summary": "summary",
}


def _page_type_to_layout(pt: str) -> str:
    """九种页型 → 前端 V3SlideLayout（teacherV3.ts:11）。"""
    return _PAGE_TYPE_TO_LAYOUT.get(pt, "definition")
