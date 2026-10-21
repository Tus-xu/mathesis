"""EvidencePack 供给 API —— B1 ContextAssembler 的检索入口（V2.1 KNR，架构 §4.10）。

B1 的六类生成图第一个节点 assemble_evidence 调 assemble_evidence_pack()：
按 scope（教材章节/课标条目/例题/基准/学情/教师偏好）分 collection 检索，
每个 RetrievedChunk 带 doc_ref 定位与 content_hash（EvidenceManifest 漂移检测）。

租户铁律不变：tenant=None 时只召回 platform 语料。

注意边界：class_context / teacher_preferences 不是本模块检索产物——
class_context 由 B1 的 LocalTeachingEvidenceProvider 聚合业务表（架构 §4.10），
teacher_preferences 由 B1 TeacherMemory.recall 产出（embedding 归本方 Embedder）。
本模块只负责知识侧四路：textbook_facts / curriculum_constraints / examples / benchmarks。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.rag.retriever import RetrievedChunk, Retriever, TenantScope
from app.rag.collections import (
    COLLECTION_KP_GRAPH,
    COLLECTION_CURRICULUM,
    COLLECTION_QUIZ_BANK,
    COLLECTION_LESSON_BENCH,
    COLLECTION_PPT_SLOTS,
)


@dataclass
class KnowledgeEvidence:
    """知识侧证据（B1 EvidencePack 的 knowledge 子集）。"""

    textbook_facts: list[RetrievedChunk] = field(default_factory=list)
    curriculum_constraints: list[RetrievedChunk] = field(default_factory=list)
    examples: list[RetrievedChunk] = field(default_factory=list)
    benchmarks: list[RetrievedChunk] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.textbook_facts or self.curriculum_constraints
                    or self.examples or self.benchmarks)


async def retrieve_by_scope(
    retriever: Retriever,
    *,
    textbook_query: str,
    curriculum_query: Optional[str] = None,
    examples_query: Optional[str] = None,
    benchmark_query: Optional[str] = None,
    tenant: Optional[TenantScope] = None,
    top_per_scope: int = 3,
) -> KnowledgeEvidence:
    """按 scope 分 collection 检索（KNR「先读资料，再生成」的证据供给侧）。

    - textbook_query：教材/知识点事实（kp_graph，教材章节定位）
    - curriculum_query：课标约束（curriculum；缺省复用 textbook_query）
    - examples_query：例题（quiz_bank；缺省复用 textbook_query）
    - benchmark_query：基准 few-shot（lesson_bench+ppt_slots；缺省不检索）
    """
    ev = KnowledgeEvidence()
    if textbook_query:
        ev.textbook_facts = await retriever.retrieve(
            textbook_query, collections=[COLLECTION_KP_GRAPH],
            top_k=top_per_scope, tenant=tenant)
    if curriculum_query or textbook_query:
        ev.curriculum_constraints = await retriever.retrieve(
            curriculum_query or textbook_query, collections=[COLLECTION_CURRICULUM],
            top_k=top_per_scope, tenant=tenant)
    if examples_query or textbook_query:
        ev.examples = await retriever.retrieve(
            examples_query or textbook_query, collections=[COLLECTION_QUIZ_BANK],
            top_k=top_per_scope, tenant=tenant)
    if benchmark_query:
        ev.benchmarks = await retriever.retrieve(
            benchmark_query, collections=[COLLECTION_LESSON_BENCH, COLLECTION_PPT_SLOTS],
            top_k=top_per_scope, tenant=tenant)
    return ev


def evidence_to_jinja(ev: KnowledgeEvidence) -> dict:
    """转 B1 §3.7 约定的 evidence Jinja2 变量段（模板直接消费的形状）。"""

    def _ser(chunks: list[RetrievedChunk]) -> list[dict]:
        return [{"content": c.content, "locator": c.locator(), "doc_ref": c.doc_ref,
                 "score": round(c.score, 4), "content_hash": c.content_hash}
                for c in chunks]

    return {
        "textbook_facts": _ser(ev.textbook_facts),
        "curriculum_constraints": _ser(ev.curriculum_constraints),
        "examples": _ser(ev.examples),
        "benchmarks": _ser(ev.benchmarks),
        # class_context / teacher_preferences 由 B1 注入（见模块 docstring 边界）
    }
