"""ContextAssembler 单测：K1 会话隔离、K2 单轮引用、EvidencePack 紧凑清单。"""
from __future__ import annotations

import pytest

from app.agent.context.assembler import EVIDENCE_REQUIRED_FEATURES, ContextAssembler
from app.agent.context.models import EvidenceSource, TurnReference
from app.agent.context.store import InMemoryStickyStore


class FakeKnowledge:
    """B2 KnowledgeWorkspace 的协议级 fake（红线：B1 不自建检索实现）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def retrieve(self, query, *, scope_ids, feature):
        self.calls.append({"query": query, "scope_ids": scope_ids, "feature": feature})
        facts = ["椭圆的第一定义：平面内到两定点距离之和为常数（大于两焦点间距）"]
        sources = [
            EvidenceSource(
                source_id="doc-1",
                source_type="textbook",
                locator="P42",
                retrieval_score=0.91,
                content_hash="ab12",
                used=True,
            )
        ]
        return facts, sources


class FakeLearning:
    async def learning_state(self, class_id: str, *, topic: str | None = None) -> dict:
        return {"correct_rate": 0.58, "error_cluster": ["离心率"], "recent_attempts": 12, "class_distribution": {}}


def _assembler() -> tuple[ContextAssembler, InMemoryStickyStore, FakeKnowledge]:
    store = InMemoryStickyStore()
    knowledge = FakeKnowledge()
    assembler = ContextAssembler(store, knowledge=knowledge, learning=FakeLearning())
    return assembler, store, knowledge


async def test_k1_seed_copied_once_and_sessions_isolated() -> None:
    assembler, store, _ = _assembler()
    s1 = await assembler.get_sticky("s1", "teacher-1")
    s1.textbook_id = "人教A版"
    s1.class_id = "class-1"
    s1.retrieval_scope_ids = ["doc-1"]
    await store.upsert(s1)

    # 新会话种子拷贝一次
    s2 = await assembler.get_sticky("s2", "teacher-1")
    assert s2.textbook_id == "人教A版"
    assert s2.class_id == "class-1"
    assert s2.retrieval_scope_ids == ["doc-1"]

    # s2 的后续修改不回灌 s1（互不串台）
    s2.chapter_path = "第二章 圆锥曲线"
    await store.upsert(s2)
    s1_again = await assembler.get_sticky("s1", "teacher-1")
    assert s1_again.chapter_path is None


async def test_get_sticky_without_identity_raises() -> None:
    assembler, _, _ = _assembler()
    with pytest.raises(ValueError, match="teacher_id"):
        await assembler.get_sticky("ghost")


async def test_upsert_from_request_merges_without_clobbering() -> None:
    assembler, store, _ = _assembler()
    base = await assembler.get_sticky("s1", "teacher-1")
    base.textbook_id = "人教A版"
    base.sticky = {"pace": "slow"}
    await store.upsert(base)

    merged = await assembler.upsert_from_request(
        "s1", "teacher-1", {"deck_id": "deck-9", "textbook_id": None, "sticky": {"tone": "同事"}}
    )
    assert merged.textbook_id == "人教A版"  # None 不覆盖
    assert merged.deck_id == "deck-9"
    assert merged.sticky == {"pace": "slow", "tone": "同事"}


async def test_assemble_builds_evidence_pack_and_compact_manifest() -> None:
    assembler, _, knowledge = _assembler()
    ctx = await assembler.get_sticky("s1", "teacher-1")
    ctx.class_id = "class-1"
    ctx.retrieval_scope_ids = ["doc-1", "doc-2"]
    pack = await assembler.assemble(ctx, "椭圆的离心率", feature="deck")

    assert knowledge.calls[0]["scope_ids"] == ["doc-1", "doc-2"]
    assert pack.feature == "deck"
    assert pack.textbook_facts and "第一定义" in pack.textbook_facts[0]
    assert pack.class_context["correct_rate"] == 0.58

    manifest = pack.to_manifest()
    assert manifest == [
        {
            "source_id": "doc-1",
            "source_type": "textbook",
            "locator": "P42",
            "retrieval_score": 0.91,
            "content_hash": "ab12",
            "used": True,
        }
    ]
    assert pack.evidence_source_ids == ["doc-1"]
    # 收口④：manifest 不含原文
    import json

    assert "第一定义" not in json.dumps(manifest, ensure_ascii=False)


async def test_turn_refs_are_turn_only_and_never_persisted() -> None:
    """K2：TURN_ONLY 引用进当轮 EvidencePack，但不写入粘性存储。"""
    assembler, store, _ = _assembler()
    ctx = await assembler.get_sticky("s1", "teacher-1")
    refs = [TurnReference(kind="slide", ref_id="slide-7", label="当前页")]
    pack = await assembler.assemble(ctx, "这页讲什么", feature="butler", turn_refs=refs)
    assert pack.sources[-1].source_type == "turn_reference"
    stored = await store.get("s1")
    assert "turn_reference" not in json_repr(stored)
    assert refs[0].scope == "TURN_ONLY"


def test_evidence_required_features_cover_six_generators() -> None:
    """KNR 红线：六类生成任务必须 assemble_evidence；butler 对话不强制。"""
    assert EVIDENCE_REQUIRED_FEATURES == {"deck", "plan", "variant", "review", "classroom_ai", "quiz_gen"}
    assert "butler" not in EVIDENCE_REQUIRED_FEATURES


def json_repr(value) -> str:
    import json

    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o), ensure_ascii=False)
