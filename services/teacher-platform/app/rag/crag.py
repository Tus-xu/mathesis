"""CragPipeline —— 纠错式 RAG（CRAG, arXiv:2401.15884）自实现（B2 红线 5：不引入外部运行时）。

流程：retrieve top-k → 检索质量评估（assessor）→ 低置信：query 重写一次（rewriter，
P0 需 LLM，由 B1 网关注入；未注入则跳过）→ 仍低：search_web 兜底并标 used_web=true。
P0 默认 assessor = 精排分数启发式；CRAG 论文的「轻量 LLM 打分」经 assessor 参数注入
（B1 网关就绪后替换）。answer() 的 text 生成同样经 llm_chat 注入，未注入时 text=None
（证据与 citations 照常返回）——绝不伪造回答文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional, Sequence

from app.rag.retriever import RetrievedChunk, Retriever, TenantScope
from app.rag.websearch import WebResult, search_web

Assessor = Callable[[str, list[RetrievedChunk]], Awaitable["CragAssessment"]]
Rewriter = Callable[[str, "CragAssessment"], Awaitable[str]]
LlmChat = Callable[[str, list[RetrievedChunk]], Awaitable[str]]


@dataclass
class CragAssessment:
    quality: Literal["correct", "ambiguous", "incorrect"]
    score: float
    reason: str


@dataclass
class CragAnswer:
    text: Optional[str]
    citations: list[dict] = field(default_factory=list)
    used_web: bool = False
    assessment: Optional[CragAssessment] = None
    chunks: list[RetrievedChunk] = field(default_factory=list)
    rewritten_query: Optional[str] = None
    verdict: Literal["correct", "ambiguous", "incorrect"] = "ambiguous"


async def heuristic_assessor(query: str, chunks: list[RetrievedChunk]) -> CragAssessment:
    """P0 规则评估器：top-3 精排分均值。assessor 注入点保留给 LLM 打分。"""
    _ = query
    if not chunks:
        return CragAssessment(quality="incorrect", score=0.0, reason="零召回")
    top = sorted((c.score for c in chunks), reverse=True)[:3]
    score = sum(top) / len(top)
    if score >= 0.7:
        quality = "correct"
    elif score >= 0.35:
        quality = "ambiguous"
    else:
        quality = "incorrect"
    return CragAssessment(quality=quality, score=round(score, 4),
                          reason=f"top{len(top)} 精排分均值（启发式）")


def _citations(chunks: list[RetrievedChunk], web_results: list[WebResult]) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(chunks, start=1):
        out.append({
            "index": i,
            "title": c.title or c.doc_ref,
            "url": None,
            "snippet": c.content[:200],
            "source_type": "web" if c.collection == "web_cache" else c.collection,
            "ref": c.locator(),
            "doc_ref": c.doc_ref,
            "content_hash": c.content_hash,
        })
    base = len(out)
    for j, w in enumerate(web_results, start=base + 1):
        out.append({
            "index": j,
            "title": w.title,
            "url": w.url,
            "snippet": w.snippet[:200],
            "source_type": "web",
            "ref": w.site,
            "doc_ref": None,
            "content_hash": None,
        })
    return out


class CragPipeline:
    def __init__(
        self,
        retriever: Retriever,
        *,
        assessor: Optional[Assessor] = None,
        rewriter: Optional[Rewriter] = None,
        llm_chat: Optional[LlmChat] = None,
        web_search: Optional[Callable[..., Awaitable[list[WebResult]]]] = None,
    ) -> None:
        self._retriever = retriever
        self._assessor = assessor or heuristic_assessor
        self._rewriter = rewriter
        self._llm_chat = llm_chat
        self._web_search = web_search

    async def _generate(self, query: str, chunks: list[RetrievedChunk]) -> Optional[str]:
        if self._llm_chat is None:
            return None
        return await self._llm_chat(query, chunks)

    async def answer(
        self,
        query: str,
        *,
        collections: Sequence[str],
        top_k: int = 6,
        tenant: Optional[TenantScope] = None,
    ) -> CragAnswer:
        """CRAG 三态三动作（arXiv:2401.15884，对照 repos/CRAG/CRAG_Inference.py:246-258）：
        correct → 仅用内部证据；ambiguous → 内部 + web 拼接并用；incorrect → 弃内部、web 兜底。
        incorrect 先重写一次（rewriter 需 LLM，未注入则直接进 web 段）。
        decompose-then-recompose（句级 strip 过滤）为 P1：先用精排分近似。"""
        chunks = await self._retriever.retrieve(
            query, collections=collections, top_k=top_k, rerank=True, tenant=tenant
        )
        assessment = await self._assessor(query, chunks)
        rewritten: Optional[str] = None

        if assessment.quality == "correct":
            return CragAnswer(await self._generate(query, chunks), _citations(chunks, []),
                              False, assessment, chunks, None, "correct")

        # incorrect → 重写一次再评估（ambiguous 不重写：证据部分可用，直接并取 web）
        if assessment.quality == "incorrect" and self._rewriter is not None:
            rewritten = await self._rewriter(query, assessment)
            if rewritten and rewritten != query:
                chunks2 = await self._retriever.retrieve(
                    rewritten, collections=collections, top_k=top_k, rerank=True, tenant=tenant
                )
                assessment2 = await self._assessor(rewritten, chunks2)
                if assessment2.score > assessment.score:
                    chunks, assessment = chunks2, assessment2
                if assessment.quality == "correct":
                    return CragAnswer(await self._generate(rewritten, chunks),
                                      _citations(chunks, []), False, assessment, chunks,
                                      rewritten, "correct")

        # web 段：incorrect 只用 web；ambiguous 内部+web 并用
        effective_query = rewritten or query
        web_results: list[WebResult] = []
        web_failed = ""
        if self._web_search is not None:
            try:
                web_results = await self._web_search(effective_query)
            except Exception as exc:  # web 故障不炸链路，诚实标注
                web_failed = f"；web 兜底失败: {type(exc).__name__}"
                assessment.reason += web_failed
        web_chunks = [
            RetrievedChunk(
                collection="web_cache", doc_ref=f"web:{w.url}", chunk_index=i,
                content=w.snippet or w.title, score=0.0,
                metadata={"locator": w.site, "title": w.title},
            )
            for i, w in enumerate(web_results)
        ]
        verdict: Literal["correct", "ambiguous", "incorrect"] = assessment.quality
        if verdict == "incorrect":
            # 弃内部证据：citations 仅 web；web 也不可用时如实返回空证据
            text_chunks = web_chunks
            citations = _citations([], web_results)
        else:
            text_chunks = chunks + web_chunks
            citations = _citations(chunks, web_results)
            if web_failed and not web_results:
                text_chunks = chunks  # web 不可用，ambiguous 退回仅内部
                citations = _citations(chunks, [])
        return CragAnswer(await self._generate(effective_query, text_chunks),
                          citations, True, assessment, text_chunks, rewritten, verdict)
