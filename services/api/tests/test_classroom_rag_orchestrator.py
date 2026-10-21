from app.domains.classroom.rag_orchestrator import (
    attach_classroom_grounding,
    build_classroom_retrieval_plan,
    attach_textbook_association_when_no_visual,
    derive_explicit_length_facts,
    build_right_trapezoid_pyramid_coordinate_witness,
    prefer_verified_coordinate_witness,
)
from types import SimpleNamespace
from app.domains.classroom.math_verifier import verify_geometry_claims


def test_manual_knowledge_point_uses_student_textbook_retrieval_plan():
    """手输知识点应只面向学生教材域检索，不落入全库泛搜。"""
    plan = build_classroom_retrieval_plan("利用导数判断函数单调性", parse_quality=None)

    assert plan.blocked is False
    assert plan.content_type == "textbook"
    assert plan.scope == "student"
    assert plan.query == "利用导数判断函数单调性"
    assert plan.reason == "manual_knowledge_point"


def test_photo_question_merges_confirmed_conditions_into_retrieval_query():
    """拍题的已知几何条件必须进入检索，而不能只用被截断的题干标题。"""
    plan = build_classroom_retrieval_plan(
        "在四棱锥 S-ABCD 中，求点 E 到平面 SBD 的距离",
        parse_quality={
            "provider": "mimo-v2.5",
            "confidence": 0.96,
            "conditions": ["底面 ABCD 为菱形", "SA 垂直于平面 ABCD", "E 是 AD 中点"],
            "diagram_entities": {"items": ["四棱锥 S-ABCD", "平面 SBD"]},
            "uncertainties": [],
            "needs_confirmation": False,
        },
    )

    assert plan.blocked is False
    assert "SA 垂直于平面 ABCD" in plan.query
    assert "四棱锥 S-ABCD" in plan.query
    assert plan.reason == "photo_question_with_confirmed_conditions"


def test_photo_question_accepts_frontend_diagram_entities_contract():
    """前端传递 camelCase 字段时，几何实体仍必须进入检索问题。"""
    plan = build_classroom_retrieval_plan(
        "求点到平面的距离",
        {
            "conditions": ["E 是 AD 中点"],
            "diagramEntities": {"items": ["四棱锥 S-ABCD", "平面 SBD"]},
        },
    )

    assert "四棱锥 S-ABCD" in plan.query


def test_condition_ledger_derives_only_explicit_chain_length_facts():
    """等式链可被化为坐标建系所需的已知长度，不能凭题型猜测额外边长。"""
    facts = derive_explicit_length_facts(
        [
            "AD = 2BC = 2CD",
            "SA = AD = 2（仅用于第(2)问）",
            "∠BCD = ∠ADC = ∠SAD = 90°",
            "四边形 ABCD 为梯形",
        ]
    )

    assert facts == {"SA": 2.0, "AD": 2.0, "BC": 1.0, "CD": 1.0}


def test_right_trapezoid_pyramid_witness_uses_confirmed_lengths_not_gold_values():
    """同一已知结构的不同尺寸必须得到相应坐标，不可写死某一道验收题。"""
    witness = build_right_trapezoid_pyramid_coordinate_witness(
        [
            "四边形 ABCD 为梯形，AD ∥ BC",
            "∠BCD = ∠ADC = ∠SAD = 90°",
            "平面 SAD ⊥ 平面 ABCD",
            "E 为线段 AD 的中点",
        ],
        {"AD": 6.0, "BC": 3.0, "CD": 3.0, "SA": 5.0},
    )

    assert witness is not None
    assert witness["coordinates"] == {
        "A": [0.0, 0.0, 0.0],
        "D": [6.0, 0.0, 0.0],
        "C": [6.0, 3.0, 0.0],
        "B": [3.0, 3.0, 0.0],
        "S": [0.0, 0.0, 5.0],
        "E": [3.0, 0.0, 0.0],
    }
    assert witness["metrics"]["lengths"] == {"AD": 6.0, "BC": 3.0, "CD": 3.0, "SA": 5.0}
    assert witness["perpendicular"] == {"line": ["B", "D"], "plane": "SAB"}
    assert verify_geometry_claims(witness)["status"] == "verified"


def test_right_trapezoid_witness_never_guesses_missing_required_length():
    assert build_right_trapezoid_pyramid_coordinate_witness(
        ["四边形 ABCD 为梯形，AD ∥ BC", "∠BCD = ∠ADC = ∠SAD = 90°"],
        {"AD": 6.0, "BC": 3.0, "CD": 3.0},
    ) is None


def test_verified_coordinate_witness_replaces_unverified_page_claims():
    """同题各页不得混用模型随写坐标；有已验证见证时统一复用该见证。"""
    witness = {"coordinates": {"A": [0.0, 0.0, 0.0]}, "source": "verified"}
    assert prefer_verified_coordinate_witness({"coordinates": {"A": [9, 9, 9]}}, witness) == witness
    assert prefer_verified_coordinate_witness({"coordinates": {"A": [9, 9, 9]}}, None) == {
        "coordinates": {"A": [9, 9, 9]}
    }


def test_attach_grounding_preserves_source_audit_and_records_plan():
    """课堂会话必须同时保留原 OCR 审计、检索计划和教材引用。"""
    original = {
        "file_id": "photo-1",
        "parse_quality": {"confirmed_by_user": True, "uncertainties": ["虚线已人工核对"]},
    }
    grounded = attach_classroom_grounding(
        original,
        {
            "status": "grounded",
            "citations": [{"chunk_id": "c-1", "title": "选择性必修第一册"}],
            "plan": {"reason": "photo_question_confirmed_by_user", "scope": "student"},
        },
    )

    assert grounded["parse_quality"]["confirmed_by_user"] is True
    assert grounded["retrieval_plan"]["reason"] == "photo_question_confirmed_by_user"
    assert grounded["textbook_evidence"]["citations"][0]["chunk_id"] == "c-1"


def test_no_reliable_visual_uses_traceable_textbook_association_not_default_solid():
    """图形生成失败或无需图时，仅追加真实教材来源卡，不构造虚假的默认立体。"""
    slide = {"title": "空间向量求距离", "blocks": [{"kind": "text", "text": "建立空间直角坐标系"}]}
    added = attach_textbook_association_when_no_visual(
        slide,
        {
            "status": "grounded",
            "citations": [
                {
                    "chunk_id": "c-1",
                    "title": "选择性必修第一册",
                    "book": "人教 A 版",
                    "section": "空间向量与立体几何",
                }
            ],
        },
        visual_generation="no_verified_visual_context",
    )

    assert added is True
    association = slide["blocks"][-1]
    assert association["kind"] == "textbook_association"
    assert association["citations"][0]["section"] == "空间向量与立体几何"
    assert not any(block.get("kind") == "geometry" for block in slide["blocks"])


def test_real_interactive_visual_does_not_add_textbook_association_block():
    slide = {"blocks": [{"kind": "ggb", "ggb": {"commands": ["A=(0,0)"]}}]}
    added = attach_textbook_association_when_no_visual(
        slide,
        {"status": "grounded", "citations": [{"chunk_id": "c-1"}]},
        visual_generation="attached",
    )

    assert added is False
    assert len(slide["blocks"]) == 1


def test_photo_question_with_uncertain_condition_is_blocked_before_generation():
    """图中文字或条件不确定时，课堂不能把猜测题设送去生成。"""
    plan = build_classroom_retrieval_plan(
        "多面体题",
        parse_quality={
            "confidence": 0.72,
            "uncertainties": ["图中线段是虚线还是实线无法确认"],
            "needs_confirmation": True,
        },
    )

    assert plan.blocked is True
    assert plan.block_reason == "photo_conditions_need_confirmation"


def test_user_confirmation_unlocks_the_same_photo_with_audit_reason():
    """学生已在界面核对后，才能解锁同一题图；疑点仍保留在来源记录中。"""
    plan = build_classroom_retrieval_plan(
        "多面体题",
        {
            "confidence": 0.72,
            "uncertainties": ["图中虚线不清晰"],
            "needs_confirmation": True,
            "confirmed_by_user": True,
        },
    )

    assert plan.blocked is False
    assert plan.reason == "photo_question_confirmed_by_user"


async def test_confirmed_photo_retrieval_uses_existing_student_textbook_rag():
    """编排层应复用 RAGPipeline，并把条件、教材域和学生 scope 一并传入。"""
    from app.domains.classroom.rag_orchestrator import retrieve_classroom_evidence

    class RAG:
        kwargs = None

        async def retrieve(self, query, **kwargs):
            self.kwargs = {"query": query, **kwargs}
            return SimpleNamespace(
                answerable=True,
                chunks=[
                    {
                        "chunk_id": "chunk-1",
                        "doc_title": "数学选择性必修第一册",
                        "content": "空间向量可用于研究立体几何中的距离问题。",
                        "score": 0.91,
                    }
                ],
            )

    rag = RAG()
    evidence = await retrieve_classroom_evidence(
        build_classroom_retrieval_plan(
            "求点到平面的距离",
            {"conditions": ["E 是 AD 中点"], "needs_confirmation": False},
        ),
        db=object(),
        rag=rag,
    )

    assert "E 是 AD 中点" in rag.kwargs["query"]
    assert rag.kwargs["mode"] == "hybrid"
    assert rag.kwargs["scope"] == "student"
    assert rag.kwargs["content_type"] == "textbook"
    assert evidence["status"] == "grounded"
    assert evidence["plan"]["reason"] == "photo_question_with_confirmed_conditions"


async def test_blocked_photo_never_calls_rag():
    """低置信题图不能进入检索/生成，避免模型用猜测条件组织课堂。"""
    from app.domains.classroom.rag_orchestrator import retrieve_classroom_evidence

    class RAG:
        async def retrieve(self, *_args, **_kwargs):
            raise AssertionError("未确认题图不应调用 RAG")

    evidence = await retrieve_classroom_evidence(
        build_classroom_retrieval_plan(
            "多面体题",
            {"needs_confirmation": True, "uncertainties": ["图中虚线不清晰"]},
        ),
        db=object(),
        rag=RAG(),
    )

    assert evidence["status"] == "blocked"
    assert evidence["block_reason"] == "photo_conditions_need_confirmation"
