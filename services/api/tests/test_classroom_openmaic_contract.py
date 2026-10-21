"""双师课堂的教材证据与 OpenMAIC 文档契约。

这些测试只验证通用协议，不依赖某一道金标准题或外部模型服务：
- 没有题目坐标/函数表达式时，后端不能为“凑图”制造占位几何或函数；
- 已生成课堂必须可以导出为 OpenMAIC 认可的 Stage/Scene 文档，且场景只承载
  课堂实际生成的文本与公式。
"""

from app.domains.classroom.openmaic_adapter import (
    build_openmaic_document,
    format_textbook_evidence,
    retrieve_textbook_evidence,
)
from app.domains.classroom.math_verifier import verify_geometry_claims
from app.domains.classroom.stage_router import (
    _append_grounded_ggb_block,
    _gen_outline,
    _gen_slide_content,
    _programmatic_patch_blocks,
    resolve_generation_topic,
)


def test_missing_visual_data_never_becomes_a_fake_figure():
    """缺少真实图形数据时，只返回原内容和复核原因，绝不补默认图。"""
    items = [{"kind": "text", "text": "请先核对题目条件。"}]
    patched = _programmatic_patch_blocks(
        items,
        {
            "title": "空间几何例题",
            "required_blocks": ["geometry", "plot2d", "latex", "example"],
            "figure_kind": "geometry_3d_solid",
        },
        {"geometry", "plot2d", "latex", "example"},
    )

    assert patched == items


def test_textbook_evidence_is_explicit_and_bounded():
    evidence = format_textbook_evidence(
        [
            {
                "chunk_id": "chunk-1",
                "doc_title": "人教A版必修第一册·函数的单调性",
                "content": "利用导数研究函数单调性时，先确定定义域，再讨论导数的正负。",
                "score": 0.92,
            }
        ]
    )

    assert evidence["status"] == "grounded"
    assert evidence["citations"] == [
        {"id": "chunk-1", "title": "人教A版必修第一册·函数的单调性", "score": 0.92}
    ]
    assert "教材证据 1" in evidence["prompt_context"]
    assert "导数的正负" in evidence["prompt_context"]


async def test_retrieval_uses_student_scoped_hybrid_rag():
    class Result:
        answerable = True
        chunks = [
            {
                "chunk_id": "chunk-2",
                "doc_title": "人教A版选择性必修第二册",
                "content": "函数单调性的判定可通过导数的符号进行。",
                "score": 0.86,
            }
        ]

    class RAG:
        def __init__(self):
            self.kwargs = None

        async def retrieve(self, query, **kwargs):
            self.kwargs = {"query": query, **kwargs}
            return Result()

    rag = RAG()
    evidence = await retrieve_textbook_evidence("利用导数判断单调性", db=object(), rag=rag)

    assert rag.kwargs["query"] == "利用导数判断单调性"
    assert rag.kwargs["mode"] == "hybrid"
    assert rag.kwargs["scope"] == "student"
    assert rag.kwargs["content_type"] == "textbook"
    assert evidence["status"] == "grounded"
    assert evidence["citations"][0]["id"] == "chunk-2"


def test_stage_scene_document_keeps_the_actual_lesson_content():
    document = build_openmaic_document(
        session_id="lesson-001",
        title="函数单调性的判定",
        mode="topic",
        slides=[
            {
                "order": 1,
                "title": "利用导数判定单调性",
                "narration": "先确定定义域，再通过导数的符号判断单调区间。",
                "blocks": [
                    {"kind": "latex", "latex": "f'(x)>0\\Rightarrow f(x)\\text{递增}"},
                    {"kind": "text", "text": "临界点将定义域分成若干区间。"},
                ],
                "source_evidence": {"citations": [{"id": "chunk-1", "title": "教材"}]},
            }
        ],
        evidence={"citations": [{"id": "chunk-1", "title": "教材"}]},
    )

    assert document["stage"]["id"] == "lesson-001"
    assert document["stage"]["languageDirective"] == "zh-CN"
    assert len(document["scenes"]) == 1
    scene = document["scenes"][0]
    assert scene["stageId"] == "lesson-001"
    assert scene["type"] == scene["content"]["type"] == "slide"
    canvas = scene["content"]["canvas"]
    assert canvas["viewportSize"] == 1000
    rendered = " ".join(element["content"] for element in canvas["elements"])
    assert "f'(x)&gt;0" in rendered
    assert "默认" not in rendered


def test_geogebra_visual_is_only_attached_to_verified_grounded_slide():
    """课堂只能消费已验证页的真实 GeoGebra 构造，不能把失败图或默认图塞回去。"""
    slide = {
        "title": "利用导数判定单调性",
        "blocks": [{"kind": "plot2d", "expr": "x^3-3*x", "x0": -3, "x1": 3}],
    }
    ggb = {"commands": ["# perspective: 2d", "f(x)=x^3-3*x"], "view": "2d"}

    assert _append_grounded_ggb_block(
        slide,
        ggb=ggb,
        evidence={"status": "grounded", "citations": [{"id": "chunk-1", "title": "教材"}]},
        verification_status="verified",
    ) is True
    item = slide["blocks"][-1]
    assert item["kind"] == "ggb"
    assert item["ggb"]["commands"] == ggb["commands"]
    assert item["visual_verification"]["status"] == "needs_review"
    assert not any(block.get("kind") == "plot2d" for block in slide["blocks"])


def test_geogebra_visual_is_skipped_without_grounded_verified_content():
    slide = {"title": "函数图像", "blocks": [{"kind": "plot2d", "expr": "x^2"}]}
    assert _append_grounded_ggb_block(
        slide,
        ggb={"commands": ["f(x)=x^2"], "view": "2d"},
        evidence={"status": "unavailable", "citations": []},
        verification_status="verified",
    ) is False
    assert all(block.get("kind") != "ggb" for block in slide["blocks"])


async def test_slide_generation_preserves_geometry_claims_for_verification(monkeypatch):
    """模型给出的结构化坐标断言必须进入后续验证器，不能在物化 blocks 时丢失。"""
    import json

    class Router:
        async def chat(self, **_kwargs):
            return {
                "content": json.dumps(
                    {
                        "blocks": [
                            {"kind": "text", "text": "建立坐标系。"},
                            {
                                "kind": "geometry",
                                "figure": {
                                    "solids": [
                                        {
                                            "kind": "polyhedron",
                                            "vertices": [
                                                {"name": "A", "pos": [0, 0, 0]},
                                                {"name": "E", "pos": [0, 0, 1]},
                                                {"name": "C", "pos": [1, 0, 0]},
                                                {"name": "D", "pos": [1, 1, 0]},
                                            ],
                                            "edges": [["A", "E"], ["A", "C"], ["C", "D"]],
                                        }
                                    ]
                                },
                            },
                        ],
                        "geometry_claims": {
                            "coordinates": {
                                "A": [0, 0, 0],
                                "E": [0, 0, 1],
                                "C": [1, 0, 0],
                                "D": [1, 1, 0],
                            }
                        },
                    },
                    ensure_ascii=False,
                )
            }

    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    content = await _gen_slide_content(
        {"order": 2, "title": "建立坐标系", "required_blocks": ["text", "geometry"]},
        "",
    )

    assert content["geometry_claims"]["coordinates"]["A"] == [0, 0, 0]


async def test_outline_cannot_lower_a_body_page_below_three_block_kinds(monkeypatch):
    """LLM 的大纲建议只能补充，不能把正文页降级成 text+geometry 两类块。"""
    import json

    class Router:
        async def chat(self, **_kwargs):
            return {
                "content": json.dumps(
                    {
                        "title": "空间几何",
                        "slides": [
                            {"title": "导入"},
                            {
                                "title": "建系",
                                "required_blocks": ["text", "geometry"],
                                "figure_kind": "geometry_3d_solid",
                            },
                            {"title": "小结"},
                        ],
                    },
                    ensure_ascii=False,
                )
            }

    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    _, slides, _ = await _gen_outline(
        None, 3, "topic", "", "", topic="空间几何"
    )

    assert len(slides[1]["required_blocks"]) >= 3
    assert "text" in slides[1]["required_blocks"]


async def test_outline_uses_plot2d_not_geometry_for_a_function_slide(monkeypatch):
    """函数页指定 plot2d 时，不得再强制要求无关的 geometry 块。

    否则模型会被要求同时构造二维函数图与立体几何图，触发无效重试，
    既拖慢真实课堂生成，也会让视觉内容脱离本页教学目标。
    """
    import json

    class Router:
        async def chat(self, **_kwargs):
            return {
                "content": json.dumps(
                    {
                        "title": "导数与单调性",
                        "slides": [
                            {"title": "导入"},
                            {
                                "title": "函数图像分析",
                                "required_blocks": ["text", "latex", "plot2d", "plot2d_function"],
                                "figure_kind": "plot2d_function",
                            },
                            {"title": "小结"},
                        ],
                    },
                    ensure_ascii=False,
                )
            }

    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    _, slides, _ = await _gen_outline(None, 3, "topic", "", "", topic="导数与单调性")

    assert slides[1]["figure_kind"] == "plot2d_function"
    assert {"text", "latex", "plot2d"}.issubset(slides[1]["required_blocks"])
    assert "geometry" not in slides[1]["required_blocks"]
    assert "plot2d_function" not in slides[1]["required_blocks"]


async def test_outline_preserves_confirmed_photo_conditions_in_every_body_slide(monkeypatch):
    """已确认题图条件必须进入大纲和每页内容契约，不能被自由改写。"""
    import json

    prompts = []

    class Router:
        async def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return {
                "content": json.dumps(
                    {
                        "title": "空间几何综合",
                        "slides": [{"title": "导入"}, {"title": "证明"}, {"title": "小结"}],
                    },
                    ensure_ascii=False,
                )
            }

    condition = "四边形 ABCD 为梯形，AD∥BC，且 AD=2BC=2CD"
    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    _, slides, _ = await _gen_outline(
        None,
        3,
        "topic",
        "",
        "",
        topic="立体几何综合题",
        condition_ledger=[condition],
    )

    assert condition in prompts[0]
    assert slides[1]["source_conditions"] == [condition]


def test_photo_generation_uses_complete_parsed_source_not_truncated_session_title():
    """拍题课堂必须使用原始解析文本，不能因数据库标题长度丢掉后续小问。"""
    complete_question = (
        "（1）证明：BD 垂直于平面 SAB；\n"
        "（2）若 SA=AD=2，求点 E 到平面 SBD 的距离。"
    )

    topic = resolve_generation_topic(
        "（1）证明：BD 垂直于平面 SAB；",
        complete_question,
    )

    assert topic == complete_question
    assert "（2）" in topic


async def test_outline_carries_only_mechanically_derived_length_facts(monkeypatch):
    """坐标建系页可使用等式链已推出的长度，不能把未知边伪装成题设。"""
    import json

    class Router:
        async def chat(self, **_kwargs):
            return {
                "content": json.dumps(
                    {"title": "立体几何", "slides": [{"title": "导入"}, {"title": "建系"}, {"title": "小结"}]},
                    ensure_ascii=False,
                )
            }

    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    _, slides, _ = await _gen_outline(
        None,
        3,
        "topic",
        "",
        "",
        topic="空间几何综合题",
        condition_ledger=["AD = 2BC = 2CD", "SA = AD = 2"],
    )

    assert slides[1]["coordinate_facts"] == {"SA": 2.0, "AD": 2.0, "BC": 1.0, "CD": 1.0}


async def test_slide_regeneration_receives_verifier_feedback(monkeypatch):
    """验证器发现坐标矛盾时，重试必须看见通用的失败原因。"""
    import json

    prompts = []

    class Router:
        async def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return {
                "content": json.dumps(
                    {"blocks": [{"kind": "text", "text": "按题设重新核对坐标。"}]},
                    ensure_ascii=False,
                )
            }

    monkeypatch.setattr("app.providers.router.get_model_router", lambda: Router())
    await _gen_slide_content(
        {"order": 2, "title": "建系", "required_blocks": ["text"]},
        "",
        verification_feedback="坐标表与题设度量不一致，请逐条复核已确认条件。",
    )

    assert "坐标表与题设度量不一致" in prompts[0]


def test_geometry_verifier_rejects_a_claimed_perpendicular_pair_when_coordinates_disagree():
    """线线垂直关系应由坐标点积独立验证，不能只依赖模型的文字宣称。"""
    result = verify_geometry_claims(
        {
            "coordinates": {
                "A": [0, 0, 0],
                "E": [0, 0, 3],
                "C": [4, 3, 0],
                "D": [0, 3, 3],
            },
            "line_perpendicular": [{"line1": ["A", "E"], "line2": ["C", "D"]}],
        }
    )

    assert result["status"] == "failed"


def test_geometry_verifier_reports_the_specific_metric_mismatch_for_regeneration():
    """坐标度量失败需给出可复核的差异，供通用生成链路修正而非猜测。"""
    result = verify_geometry_claims(
        {
            "coordinates": {
                "A": [0, 0, 0],
                "B": [1, 0, 0],
                "C": [1, 1, 0],
                "D": [0, 2, 0],
                "S": [0, 0, 2],
            },
            "metrics": {
                "lengths": {"AD": 2, "BC": 1, "CD": 1},
                "angle_deg": {"BCD": 90},
                "apex_height": 2,
            },
        }
    )

    assert result["status"] == "failed"
    assert "CD" in result["detail"]
    assert "1.414" in result["detail"]
    assert "BCD" in result["detail"]
