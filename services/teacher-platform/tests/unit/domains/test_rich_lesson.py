"""教师端 rich lesson 适配器行为测试。

这些测试固定学生双师课堂的语义输入和教师投影输出之间的边界；不测试
DualView 的布局，也不把实现细节当作契约。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.context.assembler import ContextAssembler
from app.agent.context.store import InMemoryStickyStore
from app.agent.graphs.generation import GenerationDeps, build_deck_graph
from app.domains.generation.deterministic import compose_slide
from app.domains.generation.rich_lesson import (
    _rich_slides_from_body,
    dedupe_formula_fields,
    normalize_latex,
    project_rich_lesson,
    quality_is_ready,
    verify_rich_slide,
)
from app.rag.retriever import RetrievedChunk
from app.agent.context.teacher_evidence import TeacherKnowledgeEvidence
from app.domains.generation import router as generation_router
from app.domains.generation import deterministic as deterministic_generation
from app.prompts.registry import PromptRegistry


SLOTS = {
    "_default": {
        "layout_type": "definition",
        "pedagogical_role": "概念",
        "required_slots": [],
        "optional_slots": ["formula", "visual"],
        "max_text": 5,
        "formula_capacity": 1,
        "visual_capacity": 1,
        "locked_elements": [],
    },
}


def _rich_slide(*, order: int = 1, formula: str | None = None) -> dict:
    blocks: list[dict] = [
        {"kind": "theorem", "title": "点到平面距离公式", "content": "点到平面的距离可由法向量计算。"},
        {"kind": "text", "content": "先确定平面方程，再代入距离公式。"},
    ]
    if formula:
        blocks.append({"kind": "latex", "latex": formula, "role": "definition"})
    return {
        "order": order,
        "title": "点到平面距离",
        "subtitle": "从法向量到距离",
        "blocks": blocks,
        "source_evidence": [{"source_id": "textbook:p42", "locator": "P42", "used": True}],
        "verification_result": {"status": "verified", "checks": [{"item": "math", "ok": True}]},
    }


def test_rich_blocks_become_teacher_editable_content_and_visible_task():
    slide = _rich_slide(formula=r"\frac{|Ax_0+By_0+Cz_0+D|}{\sqrt{A^2+B^2+C^2}}")
    slide["blocks"].append(
        {
            "kind": "example",
            "question": "求点 E 到平面 SBD 的距离。",
            "task_goal": "掌握点面距离公式的代入条件",
            "student_action": "列出平面方程并独立代入",
            "expected_output": "写出距离值和关键计算式",
            "teacher_prompt": "先让学生指出法向量，再追问分子来源。",
            "feedback": "检查平面方程符号与根号下各项。",
        }
    )

    result = project_rich_lesson([slide], slots=SLOTS)

    assert result["status"] == "ready"
    assert result["slides"]
    task_pages = [page for page in result["slides"] if page.get("task")]
    assert len(task_pages) == 1
    task_page = task_pages[0]
    visible_text = "\n".join(
        str(element.get("html") or "")
        for element in task_page["elements"]
        if element.get("type") == "text"
    )
    assert "学生行动" in visible_text
    assert "可检查产出" in visible_text
    assert "先让学生指出法向量" in task_page["notes"]
    assert "检查平面方程符号" in task_page["notes"]
    assert task_page["semantic_blocks"][0]["kind"] == "example"
    assert task_page["source_evidence"][0]["source_id"] == "textbook:p42"


def test_student_example_analysis_and_answer_become_teacher_solution_pages():
    slide = _rich_slide(formula=r"x^2+y^2=1")
    slide["blocks"].append({
        "kind": "example",
        "question": "判断点 P 是否在圆上。",
        "analysis": "① 代入点坐标得到 $$x^2+y^2$$；② 与 1 比较并下结论。",
        "answer": "点 P 在圆上。",
    })

    result = project_rich_lesson([slide], slots=SLOTS)

    continuation = [page for page in result["slides"] if page.get("anchor_bar")]
    visible_text = "\n".join(
        str(element.get("html") or "")
        for page in continuation
        for element in page["elements"]
        if element.get("type") == "text"
    )
    assert continuation
    assert "代入点坐标" in visible_text
    assert "点 P 在圆上" in visible_text


def test_duplicate_normalized_formula_is_removed_but_derivation_variant_remains():
    canonical = r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1"
    repeated = r" \displaystyle \frac { x ^ 2 } { a ^ 2 } + \frac{y^2}{b^2}=1 "
    first = _rich_slide(order=1, formula=canonical)
    second = _rich_slide(order=2, formula=repeated)
    second["blocks"].append(
        {
            "kind": "latex",
            "latex": r"x^2b^2+y^2a^2=a^2b^2",
            "role": "derivation",
            "context": "两边同乘 a^2b^2，得到无分母形式。",
        }
    )

    result = project_rich_lesson([first, second], slots=SLOTS)

    formula_values = [
        element.get("latex")
        for page in result["slides"]
        for element in page["elements"]
        if element.get("type") == "formula"
    ]
    assert formula_values.count(canonical) == 1
    assert r"x^2b^2+y^2a^2=a^2b^2" in formula_values
    assert all("\u005cdisplaystyle" not in value for value in formula_values if value)


def test_normalize_latex_collapses_equivalent_superscript_braces():
    assert normalize_latex(r"x^{2}+y^{2}=1") == normalize_latex(r"x^2+y^2=1")


def test_student_grounding_citations_are_normalized_to_teacher_evidence_sources():
    slide = _rich_slide()
    slide["blocks"].append({"kind": "task", "question": "指出距离公式中的法向量。"})
    slide["source_evidence"] = {
        "status": "grounded",
        "citations": [{"id": "chunk-42", "title": "解析几何教材", "score": 0.91}],
    }

    result = project_rich_lesson([slide], slots=SLOTS)

    assert result["status"] == "ready"
    assert result["slides"][0]["source_evidence"] == [{
        "source_id": "chunk-42",
        "source_type": "textbook",
        "locator": "解析几何教材",
        "retrieval_score": 0.91,
        "used": True,
    }]
    assert "chunk-42" in result["slides"][0]["notes"]


def test_student_source_evidence_list_shape_is_normalized_to_teacher_sources():
    slide = _rich_slide()
    slide["blocks"].append({"kind": "task", "question": "指出距离公式中的法向量。"})
    slide["source_evidence"] = [{"id": "chunk-43", "title": "解析几何教材", "score": 0.88}]

    result = project_rich_lesson([slide], slots=SLOTS)

    assert result["status"] == "ready"
    assert result["slides"][0]["source_evidence"] == [{
        "source_id": "chunk-43",
        "source_type": "textbook",
        "locator": "解析几何教材",
        "retrieval_score": 0.88,
        "used": True,
    }]


def test_missing_evidence_or_failed_verification_never_becomes_ready():
    missing = _rich_slide()
    missing["source_evidence"] = []
    failed = _rich_slide(order=2)
    failed["verification_result"] = {"status": "needs_review", "reason": "公式未通过校验"}

    result = project_rich_lesson([missing, failed], slots=SLOTS)

    assert result["status"] == "needs_review"
    assert result["quality_reasons"]
    assert all(page["verification_result"]["status"] != "ready" for page in result["slides"])


def test_rich_lesson_without_classroom_task_never_becomes_ready():
    result = project_rich_lesson([_rich_slide()], slots=SLOTS)

    assert result["status"] == "needs_review"
    assert any("课堂任务" in reason for reason in result["quality_reasons"])


@pytest.mark.asyncio
async def test_deck_graph_accepts_student_rich_slides_without_flattening_them():
    slide = _rich_slide(formula=r"\frac{|Ax_0+By_0+Cz_0+D|}{\sqrt{A^2+B^2+C^2}}")
    slide["blocks"].append(
        {
            "kind": "example",
            "question": "求点 E 到平面 SBD 的距离。",
            "task_goal": "掌握点面距离公式的代入条件",
            "student_action": "列出平面方程并独立代入",
            "expected_output": "写出距离值和关键计算式",
        }
    )
    graph = build_deck_graph(
        GenerationDeps(assembler=ContextAssembler(InMemoryStickyStore()))
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "点到平面距离",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "点到平面距离"}]},
            "rich_slides": [slide],
        },
        {"configurable": {"thread_id": "rich-deck-test"}},
    )

    assert result["slides"]
    assert any(page.get("semantic_blocks") for page in result["slides"])
    assert any(page.get("task") for page in result["slides"])


def test_retrieved_example_exposes_an_executable_classroom_task():
    result = compose_slide(
        {"page_type": "worked_example", "title": "例题精讲"},
        {
            "chunks": [],
            "questions": [
                {
                    "stem": "在四棱锥中，求点 E 到平面 SBD 的距离。",
                    "answer": "\\frac{1}{2}",
                    "analysis": "先求平面方程，再代入距离公式。",
                    "source": "题库·2026",
                    "difficulty": 2,
                }
            ],
        },
        used_questions=set(),
    )

    assert result is not None
    assert result["task"]["context"] == "在四棱锥中，求点 E 到平面 SBD 的距离。"
    assert result["task"]["goal"]
    assert result["task"]["student_action"]
    assert result["task"]["expected_output"]
    assert any("学生行动" in point for point in result["points"])


def test_deck_request_forwards_only_structured_rich_slides():
    rich = _rich_slide(formula=r"x^2+y^2=1")
    body = {"rich_slides": [rich, "ignore-me", {"title": "no blocks"}]}

    assert _rich_slides_from_body(body) == [rich]
    assert _rich_slides_from_body({}) == []


def test_needs_review_quality_cannot_be_persisted_as_ready():
    assert quality_is_ready({"status": "ready"}) is True
    assert quality_is_ready({"status": "needs_review", "reasons": ["缺证据"]}) is False
    assert quality_is_ready({}) is False


def test_legacy_formula_fields_are_deduped_across_teacher_pages():
    canonical = r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1"
    pages = [
        {"title": "定义", "formula": canonical},
        {"title": "辨析", "formula": r"\displaystyle \frac { x ^ 2 } { a ^ 2 } + \frac{y^2}{b^2}=1"},
        {"title": "推导变形", "formula": canonical, "formula_role": "derivation"},
    ]

    result = dedupe_formula_fields(pages)

    assert result[0]["formula"] == canonical
    assert result[1]["formula"] is None
    assert result[2]["formula"] == canonical
    assert result[1]["formula_reuse"][0]["normalized"] == canonical


def test_teacher_slide_prompt_requests_rich_blocks_and_task_contract():
    rendered = PromptRegistry().render(
        "deck/slide.j2",
        form={"topic": "点到平面距离"},
        page={"ord": 1, "page_type": "worked_example", "title": "例题", "intent": "练习", "carries": []},
        slot_schemas=[],
        evidence={"textbook_facts": [], "examples": []},
    )

    assert '"blocks"' in rendered
    assert '"task_goal"' in rendered
    assert '"student_action"' in rendered
    assert '"expected_output"' in rendered


def test_rich_plot2d_becomes_editable_teacher_function_plot():
    slide = _rich_slide()
    slide["blocks"].append(
        {
            "kind": "plot2d",
            "expr": "y=x^2",
            "domain": [-2, 2],
            "params": {},
        }
    )

    result = project_rich_lesson([slide], slots=SLOTS)

    assert any(
        element.get("type") == "functionPlot" and element.get("expr") == "y=x^2"
        for page in result["slides"]
        for element in page["elements"]
    )


def test_rich_table_is_visible_in_teacher_projection_instead_of_metadata_only():
    slide = _rich_slide()
    slide["blocks"].append({
        "kind": "table",
        "caption": "单调性判断表",
        "headers": ["x", "f'(x)", "f(x)"],
        "rows": [["(-∞,-1)", "+", "增"], ["-1", "0", "极大值"]],
    })

    result = project_rich_lesson([slide], slots=SLOTS)

    visible_text = "\n".join(
        element.get("html") or ""
        for page in result["slides"]
        for element in page["elements"]
        if element.get("type") == "text"
    )
    assert "单调性判断表" in visible_text
    assert "f'(x)" in visible_text
    assert "(-∞,-1)" in visible_text


def test_student_plot2d_x0_x1_bounds_survive_teacher_projection():
    slide = _rich_slide()
    slide["blocks"].append({"kind": "plot2d", "expr": "y=x^2", "x0": -2, "x1": 2})

    result = project_rich_lesson([slide], slots=SLOTS)

    plot = next(
        element
        for page in result["slides"]
        for element in page["elements"]
        if element.get("type") == "functionPlot"
    )
    assert plot["domain"] == [-2.0, 2.0]


@pytest.mark.asyncio
async def test_teacher_knowledge_provider_converts_retrieval_chunks_to_evidence():
    class _Retriever:
        async def retrieve(self, query, *, collections, top_k, rerank, tenant):
            return [RetrievedChunk(
                collection="kp_graph",
                doc_ref="textbook:analytic-geometry",
                chunk_index=2,
                content="点到平面的距离由法向量和代入点确定。",
                score=0.91,
                metadata={"locator": "P42"},
            )]

    provider = TeacherKnowledgeEvidence(_Retriever(), collections=("kp_graph",))
    facts, sources = await provider.retrieve("点到平面距离", scope_ids=[], feature="deck")

    assert facts == ["点到平面的距离由法向量和代入点确定。"]
    assert sources[0].source_id == "textbook:analytic-geometry#2"
    assert sources[0].locator == "P42"
    assert sources[0].used is True


def test_long_solution_steps_are_split_into_teacher_continuation_pages():
    slide = _rich_slide()
    slide["blocks"].append(
        {
            "kind": "solution",
            "steps": [{"content": f"第 {index} 步：完成关键计算"} for index in range(1, 8)],
        }
    )

    result = project_rich_lesson([slide], slots=SLOTS)

    assert len(result["slides"]) >= 2
    rendered_text = "\n".join(
        element.get("html") or ""
        for page in result["slides"]
        for element in page["elements"]
        if element.get("type") == "text"
    )
    for index in range(1, 8):
        assert f"第 {index} 步" in rendered_text
    assert any(page.get("anchor_bar", "").startswith("接上页") for page in result["slides"][1:])


class _FailingGateway:
    async def chat(self, *args, **kwargs):
        raise TimeoutError("provider unavailable")


class _PromptRenderer:
    def render(self, template_name: str, **context):
        return "{}"


class _PayloadGateway:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)

    async def chat(self, *args, **kwargs):
        from app.llm.gateway import ChatResult

        return ChatResult(
            content=json.dumps(self.payloads.pop(0), ensure_ascii=False),
            model="fake",
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_deck_graph_does_not_return_ready_skeleton_when_generation_has_no_evidence():
    pages = [
        {"ord": 1, "page_type": "concept_intro", "title": "真实内容页", "intent": "讲授定义"},
    ]
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_FailingGateway(),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "不存在的主题",
            "slots": SLOTS,
            "outline": {"pages": pages},
            "retrieval": {},
        },
        {"configurable": {"thread_id": "empty-evidence-test"}},
    )

    assert result["slides"] == []
    assert result["quality"]["status"] == "needs_review"
    assert "provider" in " ".join(result["quality"]["reasons"])


@pytest.mark.asyncio
async def test_deck_graph_dedupes_repeated_formula_before_layout():
    canonical = r"\frac{x^2}{a^2}+\frac{y^2}{b^2}=1"
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway(
                [
                    {"points": ["定义"], "formula": canonical},
                    {"points": ["辨析"], "formula": r"\displaystyle \frac { x ^ 2 } { a ^ 2 } + \frac{y^2}{b^2}=1"},
                ]
            ),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )
    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "椭圆标准方程",
            "slots": SLOTS,
            "outline": {
                "pages": [
                    {"ord": 1, "page_type": "formula_focus", "title": "定义"},
                    {"ord": 2, "page_type": "formula_focus", "title": "辨析"},
                ]
            },
            "retrieval": {"chunks": [{"content": "教材定义原句", "doc_ref": "textbook:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "formula-dedup-test"}},
    )

    formula_values = [
        element.get("latex")
        for page in result["slides"]
        for element in page["elements"]
        if element.get("type") == "formula"
    ]
    assert formula_values.count(canonical) == 1


@pytest.mark.asyncio
async def test_deck_graph_projects_rich_blocks_returned_by_generation_model():
    rich = _rich_slide(formula=r"x^2+y^2=1")
    rich["blocks"].append(
        {
            "kind": "example",
            "question": "求点 E 到平面的距离。",
            "task_goal": "掌握代入条件",
            "student_action": "独立列式",
            "expected_output": "距离值和关键式",
        }
    )
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([rich]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "点面距离",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "点面距离"}]},
            "retrieval": {"chunks": [{"content": "教材定义原句", "doc_ref": "textbook:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "rich-model-output-test"}},
    )

    assert result["quality"]["status"] == "ready"
    assert any(page.get("task") for page in result["slides"])
    assert any(page.get("semantic_blocks") for page in result["slides"])


@pytest.mark.asyncio
async def test_deck_graph_keeps_task_metadata_in_deterministic_question_fallback():
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_FailingGateway(),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )
    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "点面距离",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "例题"}]},
            "retrieval": {
                "chunks": [],
                "questions": [{
                    "stem": "在四棱锥中，求点 E 到平面 SBD 的距离。",
                    "stem_latex": "",
                    "answer": "1/2",
                    "analysis": "先求平面方程，再代入距离公式。",
                    "source": "题库·2026",
                    "difficulty": 2,
                    "stem_image": "",
                    "solution_image": "",
                }],
            },
        },
        {"configurable": {"thread_id": "task-fallback-test"}},
    )

    page = result["slides"][0]
    assert page["task"]["context"].startswith("在四棱锥中")
    assert any("学生行动" in (element.get("html") or "") for element in page["elements"])
    assert "答案：1/2" in page["notes"]
    assert page["source_evidence"][0]["source_id"] == "quiz:题库·2026"
    assert page["verification_result"]["status"] == "needs_review"
    assert result["quality"]["status"] == "needs_review"
    assert page["task"]["teacher_prompt"]
    assert page["task"]["feedback"]
    assert "教师引导：" in page["notes"]
    assert "讲评要点：" in page["notes"]


@pytest.mark.asyncio
async def test_legacy_model_without_verified_result_cannot_be_ready_even_with_retrieval_evidence():
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([{"points": ["教材定义"]}]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )
    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "椭圆",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "concept_intro", "title": "定义"}]},
            "retrieval": {"chunks": [{"content": "教材定义原句", "doc_ref": "textbook:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "legacy-unverified-test"}},
    )

    assert result["quality"]["status"] == "needs_review"
    assert any("验证" in reason for reason in result["quality"]["reasons"])


@pytest.mark.asyncio
async def test_legacy_points_deck_without_classroom_task_cannot_be_ready():
    """旧 points 输出也必须满足课堂任务门禁，避免回到无任务模板课件。"""
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([{
                "points": ["椭圆由到两定点距离差定义"],
                "formula": r"\frac{x^2}{a^2}-\frac{y^2}{b^2}=1",
                "source_evidence": [{"source_id": "textbook:ellipse:p42", "used": True}],
                "verification_result": {"status": "verified", "checks": [{"item": "math", "ok": True}]},
            }]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "椭圆标准方程",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "concept_intro", "title": "椭圆定义"}]},
            "retrieval": {"chunks": [{"content": "椭圆定义教材原句", "doc_ref": "textbook:ellipse:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "legacy-task-gate-test"}},
    )

    assert result["quality"]["status"] == "needs_review"
    assert any("课堂任务" in reason for reason in result["quality"]["reasons"])


@pytest.mark.asyncio
async def test_legacy_visual_board_json_is_projected_to_editable_function_plot():
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([{
                "points": ["由函数表达式观察图像变化"],
                "visual": {
                    "kind": "boardJson",
                    "boardJson": {"type": "plot2d", "expr": "y=x^2", "x0": -2, "x1": 2},
                },
                "task": {
                    "goal": "理解函数图像",
                    "context": "观察二次函数图像",
                    "student_action": "拖动参数并记录变化",
                    "expected_output": "写出图像变化结论",
                },
                "source_evidence": [{"source_id": "textbook:function:p42", "used": True}],
                "verification_result": {"status": "verified"},
            }]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "二次函数图像",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "concept_intro", "title": "函数图像"}]},
            "retrieval": {"chunks": [{"content": "函数图像教材原句", "doc_ref": "textbook:function:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "legacy-board-json-test"}},
    )

    assert result["quality"]["status"] == "ready"
    assert any(
        element.get("type") == "functionPlot" and element.get("expr") == "y=x^2"
        for slide in result["slides"]
        for element in slide["elements"]
    )


@pytest.mark.asyncio
async def test_legacy_points_do_not_invent_3d_figure_from_title_only():
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([{
                "points": ["四棱锥的定义与顶点关系"],
                "task": {
                    "goal": "识别几何体结构",
                    "context": "根据题目条件判断顶点关系",
                    "student_action": "标出顶点并说明依据",
                    "expected_output": "写出顶点关系表",
                },
                "source_evidence": [{"source_id": "textbook:solid:p42", "used": True}],
                "verification_result": {"status": "verified"},
            }]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "四棱锥",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "concept_intro", "title": "四棱锥"}]},
            "retrieval": {"chunks": [{"content": "四棱锥教材原句", "doc_ref": "textbook:solid:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "no-title-figure-test"}},
    )

    assert result["quality"]["status"] == "ready"
    assert not any(
        element.get("type") in {"geometry", "figure3d", "functionPlot"}
        for slide in result["slides"]
        for element in slide["elements"]
    )


@pytest.mark.asyncio
async def test_normal_topic_rich_output_is_grounded_and_verified_without_body_rich_slides():
    rich = _rich_slide(formula=r"x^2+y^2=1")
    rich.pop("source_evidence")
    rich.pop("verification_result")
    rich["blocks"].append({
        "kind": "task",
        "question": "判断点是否在曲线上。",
        "task_goal": "理解方程的判定含义",
        "student_action": "代入点坐标检验",
        "expected_output": "写出判定结果和关键式",
    })
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([rich]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "圆的标准方程",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "圆的标准方程"}]},
            "retrieval": {"chunks": [{"content": "圆的标准方程教材原句", "doc_ref": "textbook:circle:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "normal-topic-rich-grounding-test"}},
    )

    assert result["quality"]["status"] == "ready"
    slide = result["slides"][0]
    assert slide["source_evidence"][0]["source_id"] == "textbook:circle:p42"
    assert slide["verification_result"]["status"] == "verified"


@pytest.mark.asyncio
async def test_normal_topic_does_not_trust_model_verified_for_invalid_formula():
    rich = _rich_slide(formula=r"\\notARealCommand{x}")
    rich["blocks"].append({
        "kind": "task",
        "question": "判断点是否在曲线上。",
        "task_goal": "理解方程的判定含义",
        "student_action": "代入点坐标检验",
        "expected_output": "写出判定结果和关键式",
    })
    rich["verification_result"] = {"status": "verified", "detail": "模型自报已验证"}
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([rich]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "圆的标准方程",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "圆的标准方程"}]},
            "retrieval": {"chunks": [{"content": "圆的标准方程教材原句", "doc_ref": "textbook:circle:p42"}], "questions": []},
        },
        {"configurable": {"thread_id": "normal-topic-untrusted-verification-test"}},
    )

    assert result["quality"]["status"] == "needs_review"
    assert any("LaTeX" in reason or "验证" in reason for reason in result["quality"]["reasons"])


@pytest.mark.asyncio
async def test_normal_rich_model_only_evidence_never_becomes_ready():
    """模型自报 citation 不能替代教师端真实检索证据。"""
    rich = _rich_slide(formula=r"x^2+y^2=1")
    rich["source_evidence"] = [{"id": "model-only", "title": "模型自报教材", "score": 0.99}]
    rich["verification_result"] = {"status": "verified", "detail": "模型自报"}
    rich["blocks"].append({
        "kind": "task",
        "question": "判断点是否在曲线上。",
        "task_goal": "理解方程的判定含义",
        "student_action": "代入点坐标检验",
        "expected_output": "写出判定结果和关键式",
    })
    graph = build_deck_graph(
        GenerationDeps(
            gateway=_PayloadGateway([rich]),
            prompt_renderer=_PromptRenderer(),
            assembler=ContextAssembler(InMemoryStickyStore()),
        )
    )

    result = await graph.ainvoke(
        {
            "stage": "slides",
            "ctx": {},
            "query": "圆的标准方程",
            "slots": SLOTS,
            "outline": {"pages": [{"ord": 1, "page_type": "worked_example", "title": "圆的标准方程"}]},
            "retrieval": {"chunks": [], "questions": []},
        },
        {"configurable": {"thread_id": "normal-model-only-evidence-test"}},
    )

    assert result["quality"]["status"] == "needs_review"
    assert any("source_evidence" in reason for reason in result["quality"]["reasons"])


def test_deterministic_verifier_can_promote_upstream_needs_review_when_proof_passes():
    slide = _rich_slide(formula=r"x^2+y^2=1")
    slide["verification_result"] = {"status": "needs_review", "reason": "模型无法自证"}

    result = verify_rich_slide(slide)

    assert result["status"] == "verified"
    assert result["method"] == "teacher-rich-deterministic-v1"


@pytest.mark.asyncio
async def test_deck_endpoint_normal_topic_without_body_rich_slides_persists_ready_rich_deck(monkeypatch):
    """真实教师入口不传 rich_slides，也应生成富语义、带任务且可持久化的课件。"""
    first = _rich_slide(formula=r"x^2+y^2=1")
    first["source_evidence"] = [{"source_id": "model:self-reported", "used": True}]
    first["verification_result"] = {"status": "needs_review", "reason": "模型未执行教师端校验"}
    first["blocks"].append({
        "kind": "task",
        "question": "判断点是否在曲线上。",
        "task_goal": "理解方程的判定含义",
        "student_action": "代入点坐标检验",
        "expected_output": "写出判定结果和关键式",
    })
    second = _rich_slide(order=2, formula=r"\displaystyle x^2+y^2=1")
    second["source_evidence"] = [{"source_id": "model:self-reported", "used": True}]
    second["verification_result"] = {"status": "needs_review", "reason": "模型未执行教师端校验"}

    deps = GenerationDeps(
        gateway=_PayloadGateway([first, second]),
        prompt_renderer=_PromptRenderer(),
        assembler=ContextAssembler(InMemoryStickyStore()),
    )
    monkeypatch.setattr(generation_router, "get_generation_deps", lambda: deps)

    async def _slots(_template_id, _session):
        return SLOTS

    monkeypatch.setattr(generation_router, "_deck_slots", _slots)

    async def _retrieve(_session, _keywords, **_kwargs):
        return {"chunks": [{
            "content": "圆的标准方程教材原句",
            "doc_ref": "textbook:circle:p42",
            "locator": "P42",
        }], "questions": []}

    monkeypatch.setattr(deterministic_generation, "retrieve", _retrieve)

    class _CaptureSession:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

        def add(self, row):
            row.id = row.id or uuid4()
            self.row = row

        async def commit(self):
            return None

    captured = _CaptureSession()

    class _SessionFactory:
        def __call__(self):
            return captured

    monkeypatch.setattr(generation_router, "session_factory", lambda: _SessionFactory())

    body = {
        "topic": "圆的标准方程",
        "outline": [
            {"title": "圆的定义", "kind": "definition"},
            {"title": "课堂判断任务", "kind": "example"},
        ],
    }
    assert "rich_slides" not in body
    request = SimpleNamespace(state=SimpleNamespace(request_id="route-rich-test"))
    user = SimpleNamespace(id="teacher-route-test")

    response = await generation_router.deck_generate(request, body, user, None)
    events = []
    async for event in response.body_iterator:
        events.append(event)

    done = json.loads(events[-1]["data"])
    assert done["finish_reason"] == "stop"
    assert done["deck_id"]
    assert captured.row is not None
    assert any(slide.get("task") for slide in captured.row.slides)
    assert any(slide.get("page_type") == "worked_example" for slide in captured.row.slides)
    assert all(
        slide["source_evidence"][0]["source_id"] == "textbook:circle:p42"
        for slide in captured.row.slides
        if slide.get("source_evidence")
    )
    formula_values = [
        element.get("latex")
        for slide in captured.row.slides
        for element in slide.get("elements", [])
        if element.get("type") == "formula"
    ]
    assert formula_values.count(r"x^2+y^2=1") == 1
