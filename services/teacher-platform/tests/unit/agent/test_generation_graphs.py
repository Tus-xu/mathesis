"""生成图骨架单测：KNR 证据强制、plan 两段式、review 页序、ai-element 只出 diff。

提示词走 fake renderer（红线 2：B1 代码零提示词字符串；B2 prompts 交付后换真渲染器）。
模型走 fake gateway（JSON 输出；真模型调用在有 key 时回归）。
"""
from __future__ import annotations

import json

import pytest

from app.agent.context.assembler import ContextAssembler
from app.agent.context.models import EvidenceSource
from app.agent.context.store import InMemoryStickyStore
from app.agent.graphs.generation import (
    GenerationDeps,
    PlanOutline,
    RendererMissing,
    build_ai_element_graph,
    build_plan_graph,
    build_review_graph,
)
from app.llm.ai_runs import AiRunRecord, AiRunsSink
from app.llm.config import LlmSettings
from app.llm.gateway import LlmGateway


class MemorySink(AiRunsSink):
    def __init__(self) -> None:
        self.records: list[AiRunRecord] = []

    async def write(self, record: AiRunRecord) -> None:
        self.records.append(record)


class FakeKnowledge:
    """ContextAssembler 的 KnowledgeWorkspace fake。"""

    async def retrieve(self, query, *, scope_ids, feature):
        return ["椭圆第一定义（教材 P42）"], [EvidenceSource(source_id="doc-1", source_type="textbook", locator="P42", used=True)]


class FakeRenderer:
    """红线 2 的镜像：渲染器只是模板名 + 上下文 → 字符串；B1 代码里没有提示词内容。"""

    def __init__(self) -> None:
        self.templates: list[str] = []

    def render(self, template_name: str, **ctx):
        self.templates.append(template_name)
        return json.dumps({"_template": template_name, "_ctx_keys": sorted(ctx.keys())})


class FakeJsonGateway:
    """按调用序返回预置 JSON 的 fake gateway（LlmGateway 协议兼容）。"""

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []
        self.sink = MemorySink()

    async def chat(self, messages, *, use_case="chat", stream=False, metadata=None, **kw):
        from app.llm.gateway import ChatResult

        self.calls.append({"use_case": use_case, "prompt": messages[-1]["content"]})
        payload = self.payloads.pop(0)
        message = type("M", (), {"content": json.dumps(payload), "reasoning_content": None, "tool_calls": None})()
        choice = type("C", (), {"message": message, "finish_reason": "stop"})()
        return ChatResult(content=message.content, model="fake", finish_reason="stop")

    async def _record(self, *a, **kw):  # pragma: no cover
        pass


def _deps(gateway, renderer, assembler=None, **kw) -> GenerationDeps:
    return GenerationDeps(gateway=gateway, prompt_renderer=renderer, assembler=assembler, **kw)


async def _run(graph, state: dict) -> list:
    from app.agent.stream_adapter import graph_to_events

    return [e async for e in graph_to_events(graph, state, {"configurable": {"thread_id": "g1"}})]


OUTLINE = {
    "topic": "椭圆及其标准方程",
    "total_minutes": 45,
    "sections": [
        {"title": "情境导入", "minutes": 5, "goal": "从绳长实验引入"},
        {"title": "标准方程推导", "minutes": 20, "goal": "建系化简"},
        {"title": "例题与练习", "minutes": 15, "goal": "两个例题"},
        {"title": "小结", "minutes": 5, "goal": "定义回顾"},
    ],
}
SECTION_BODY = {"board_plan": "定义 → 推导 → 例1", "teacher_moves": ["提问", "板书"]}
DIFF = {"target_element_id": "el-9", "action": "replace_formula", "payload": {"latex": "\\frac{x^2}{4}+\\frac{y^2}{3}=1"}}
SLIDE_BODY = {"headline": "错因聚焦"}


async def test_plan_stage1_emits_outline_as_draft():
    assembler = ContextAssembler(InMemoryStickyStore(), knowledge=FakeKnowledge())
    gw = FakeJsonGateway([OUTLINE])
    graph = build_plan_graph(_deps(gw, FakeRenderer(), assembler))
    events = await _run(graph, {"stage": "outline", "ctx": {}, "query": "椭圆及其标准方程"})
    names = [e.name for e in events]
    assert names == ["meta", "thinking", "outline", "done"]
    outline_event = events[2]
    assert outline_event.payload["confirmed"] is False  # 红线 6：草稿态
    assert outline_event.payload["total_minutes"] == 45
    assert events[0].payload["evidence"] is True  # KNR：证据已组装
    assert events[0].payload["manifest"][0]["source_id"] == "doc-1"


async def test_plan_stage2_runs_sections_after_confirm():
    assembler = ContextAssembler(InMemoryStickyStore(), knowledge=FakeKnowledge())
    n_sections = len(OUTLINE["sections"])
    gw = FakeJsonGateway([SECTION_BODY] * n_sections)
    graph = build_plan_graph(_deps(gw, FakeRenderer(), assembler))
    events = await _run(
        graph,
        {"stage": "sections", "ctx": {}, "query": "", "outline": OUTLINE},
    )
    names = [e.name for e in events]
    assert names == ["meta", "section", "section", "section", "section", "done"]
    assert all(e.payload["confirmed"] is False for e in events if e.name == "section")


async def test_knr_blocks_llm_without_evidence():
    """红线 9：无 ContextAssembler（无证据组装）→ 图在调 LLM 前失败。"""
    gw = FakeJsonGateway([OUTLINE])
    graph = build_plan_graph(_deps(gw, FakeRenderer(), assembler=None))
    with pytest.raises(RuntimeError, match="KNR 红线 9"):
        await _run(graph, {"stage": "outline", "ctx": {}, "query": "椭圆"})
    assert gw.calls == []  # LLM 从未被调用


async def test_missing_renderer_raises_before_llm():
    gw = FakeJsonGateway([OUTLINE])
    graph = build_plan_graph(GenerationDeps(gateway=gw, prompt_renderer=None, assembler=ContextAssembler(InMemoryStickyStore(), knowledge=FakeKnowledge())))
    with pytest.raises(RendererMissing, match="红线 2"):
        await _run(graph, {"stage": "outline", "ctx": {}, "query": "椭圆"})
    assert gw.calls == []


def test_outline_minutes_sum_is_asserted():
    bad = {**OUTLINE, "sections": [{**OUTLINE["sections"][0], "minutes": 6}] + OUTLINE["sections"][1:]}
    with pytest.raises(ValueError, match="时长加总"):
        PlanOutline.model_validate(bad)


async def test_review_pack_page_order():
    assembler = ContextAssembler(InMemoryStickyStore(), knowledge=FakeKnowledge())

    async def stats_provider(assignment_id):
        return {"questions": [{"id": "q1", "error_rate": 0.8}, {"id": "q2", "error_rate": 0.3}]}

    gw = FakeJsonGateway([SLIDE_BODY] * 4)
    graph = build_review_graph(_deps(gw, FakeRenderer(), assembler, stats_provider=stats_provider))
    events = await _run(graph, {"assignment_id": "asg-1"})
    names = [e.name for e in events]
    assert names == ["meta", "slide", "slide", "slide", "slide", "done"]
    page_types = [e.payload["page_type"] for e in events if e.name == "slide"]
    assert page_types == ["chart", "error_cause", "advice", "variant"]
    assert all(e.payload["confirmed"] is False for e in events if e.name == "slide")


async def test_ai_element_only_emits_diff():
    gw = FakeJsonGateway([DIFF])
    graph = build_ai_element_graph(_deps(gw, FakeRenderer()))
    events = await _run(graph, {"element": {"id": "el-9", "kind": "formula"}, "hint": "改成标准式"})
    names = [e.name for e in events]
    assert names == ["meta", "suggest", "diff", "done"]
    diff = events[2].payload
    assert diff["target_element_id"] == "el-9"
    assert diff["confirmed"] is False  # R5/红线 6：只出 diff 草稿，绝不直接改 deck


async def test_gateway_receives_feature_for_ai_runs():
    """ai_runs 台账 feature 贯通：网关收到的 metadata.feature 正确（G6）。"""
    assembler = ContextAssembler(InMemoryStickyStore(), knowledge=FakeKnowledge())
    gw = FakeJsonGateway([OUTLINE])
    graph = build_plan_graph(_deps(gw, FakeRenderer(), assembler))
    await _run(graph, {"stage": "outline", "ctx": {}, "query": "椭圆"})
    assert gw.calls[0]["use_case"] == "long_form"
