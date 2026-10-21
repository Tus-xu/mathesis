"""RAG 管线（kernel/rag.py）

三路召回（向量 + 全文 + 知识点标签）→ RRF 融合 → Rerank → 拒答闸门；
可选第 4 路：云知识库（system_configs["cloud_kb"]/env 启用时，并行检索进同一 RRF）。
降级策略：Embedding 不可用跳过向量路；Reranker 不可用用 RRF 排序 + 原始分闸门；
云通道失败/超时（≤8s）静默跳过，绝不拖垮本地三路。
拒答闸门分源判定（修复分度失配）：
- reranker 生效 → 用 rerank 分（0~1）对 settings.rag_refuse_threshold
- 降级路径 → 用 top 原始相关分 raw_score 对 settings.rag_raw_threshold
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chunk import Chunk
from app.models.database import async_session_factory
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.providers.embedding import EmbeddingConfig
from app.providers.router import get_model_router

logger = structlog.get_logger()

# RRF 融合参数
RRF_K = 60
# 各路召回数量
RECALL_TOP_K = 20
# 融合后取 top
FUSED_TOP_K = 10
# 精排后取 top
RERANK_TOP_K = 4
# Embedding 可用性缓存 TTL（避免每查询一次健康检查往返）
EMBEDDING_HEALTH_TTL_S = 60.0
# Reranker 可用性缓存 TTL（避免每查询一次健康检查 HTTP 往返，~50-200ms）
RERANK_HEALTH_TTL_S = 60.0
_rerank_health_cache: dict = {"ok": False, "ts": 0.0}
# 云知识库第 4 路超时上限（秒）：超时静默跳过该通道
CLOUD_KB_TIMEOUT_S = 8.0


def _embedding_config_fingerprint(cfg: EmbeddingConfig) -> str:
    """Embedding 配置指纹：provider/base_url/model/dimension + API key 摘要。

    用于让 60s 健康缓存随配置变化立即失效；key 只取摘要，禁止明文入指纹。
    """
    key_digest = hashlib.sha256(cfg.api_key.encode("utf-8")).hexdigest()[:16] if cfg.api_key else ""
    raw = f"{cfg.provider}|{cfg.base_url}|{cfg.model}|{cfg.dimension}|{key_digest}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

REWRITE_PROMPT = """\
请将以下多轮对话中的最新问题改写为一个独立的、完整的问题。
要求：补全指代（"它"、"这个"、"那第3个"等），使问题脱离上下文也能理解。
只输出改写后的问题，不要解释。

对话历史：
{history}

最新问题：{question}

改写后的问题："""


@dataclass
class ScoredChunk:
    """带分数的切片"""

    chunk_id: str
    doc_id: str
    content: str
    doc_title: str = ""
    score: float = 0.0  # 排序分（RRF 分或 rerank 分）
    raw_score: float = 0.0  # 召回路原始相关分（wsim/cosine/kp），拒答闸门用
    kp_ids: list[str] = field(default_factory=list)
    # 切片级元数据（chunks.meta_）：教材切片带 book_name/volume/section/
    # subsection/source_kind/source_chunk_id 等出处键，引用回链（册/章/节）依赖它。
    meta: dict = field(default_factory=dict)


@dataclass
class RAGResult:
    """RAG 检索结果"""

    chunks: list[ScoredChunk] = field(default_factory=list)
    answerable: bool = True
    refuse_reason: str = ""
    rewritten_query: str = ""
    # G6 错误语义（SA01）：召回链路发生过 provider 级故障时填充。
    # 语义约定：`NO_RESULT`（真没资料）≠ `PROVIDER_UNAVAILABLE/TIMEOUT/ERROR`（检索挂了）。
    # 调用方据此决定是「诚实拒答」还是「提示服务暂不可用」——禁止把故障伪装成空结果。
    degraded_reason: str = ""  # PROVIDER_UNAVAILABLE | PROVIDER_TIMEOUT | PROVIDER_ERROR
    degraded_detail: str = ""


class RAGPipeline:
    """RAG 检索管线"""

    def __init__(self) -> None:
        self._embedding_ok: bool = False
        self._embedding_checked_at: float = 0.0
        self._embedding_cfg_fingerprint: str | None = None
        # G6：单次 retrieve 内的降级状态（每次 retrieve 开头重置）
        self._degraded_reason: str = ""
        self._degraded_detail: str = ""

    def _record_degrade(self, exc: Exception) -> None:
        """把召回链路的 provider 级异常归类为降级原因（保留首个 = 最根因）。

        分类：PROVIDER_UNAVAILABLE（连不上）/ PROVIDER_TIMEOUT（超时）/
        PROVIDER_ERROR（其他）。仅供调用方区分「真没资料」与「检索挂了」。
        """
        if self._degraded_reason:
            return
        try:
            import httpx as _httpx

            if isinstance(exc, _httpx.ConnectError):
                reason = "PROVIDER_UNAVAILABLE"
            elif isinstance(exc, (_httpx.ReadTimeout, _httpx.WriteTimeout, asyncio.TimeoutError)):
                reason = "PROVIDER_TIMEOUT"
            else:
                reason = "PROVIDER_ERROR"
        except Exception:  # noqa: BLE001
            reason = "PROVIDER_ERROR"
        self._degraded_reason = reason
        self._degraded_detail = f"{type(exc).__name__}: {str(exc)[:160]}"
        logger.warning("rag.degraded", reason=reason, detail=self._degraded_detail)

    def _degrade_fields(self) -> dict:
        """供 retrieve 的各返回点展开，保证任何出口都携带降级状态。"""
        return {
            "degraded_reason": self._degraded_reason,
            "degraded_detail": self._degraded_detail,
        }

    async def retrieve(
        self,
        question: str,
        *,
        db: AsyncSession,
        conversation_history: list[dict] | None = None,
        conversation_id: str = "",
        request_id: str = "",
        mode: str = "hybrid",
        content_type: str | None = None,
        kp_codes: list[str] | None = None,
        scope: str | None = None,
        source_kinds: str | None = None,
    ) -> RAGResult:
        """标准 RAG 流程：
        1. 改写：LLM 指代补全，temperature=0
        2. 三路召回并行：pgvector + pg_trgm(word_similarity) + kp_tags
           （云知识库启用时追加第 4 路，hybrid 模式生效，失败/超时静默跳过）
        3. RRF 融合（k=60）取 top10
        4. bge-reranker 精排取 top4（降级：RRF 排序直取）
        5. 拒答闸门（分源判定，见模块 docstring）

        mode=hybrid（默认）三路全开；vector/fulltext/kp 为单路调试（/tools/retrieve 用）。
        content_type/kp_codes 为元数据过滤（迭代05 接线，SSOT §5.9：先过滤再向量匹配）。
        scope 为端隔离过滤（student/teacher/research，多端用逗号分隔如 "student,teacher"；
        为空不过滤。student 端只可见 student，teacher 可见 student+teacher，researcher 全可见）。
        source_kinds 为切片级语料过滤（chunks.meta->>'source_kind'，逗号分隔多值；
        为空不过滤全量混检。用于学生对话排除非目标语料，如英文 ADVANCED_REFERENCE）。
        """
        log = logger.bind(request_id=request_id)

        # Step 1: 查询改写（指代补全，串行 —— 评估后决定不做并行化，详见优化文档）
        # P0-2 提速：无历史且无对话摘要时跳过改写（省一次 LLM 调用，首响应关键路径）
        # （改写只在多轮有指代时才必要；首轮提问直接召回，语序完整性损失可忽略）
        # 注：条件用 conversation_history（真实参数名），不能用 history（不存在 → NameError）
        rewritten = question
        if conversation_history or conversation_id:
            rewritten = await self._rewrite_query(
                question,
                conversation_history or [],
                conversation_id=conversation_id,
                db=db,
                request_id=request_id,
            )
        log.info("rag.rewritten", original=question[:50], rewritten=rewritten[:50])

        # Step 1.5: 元数据过滤解析（SSOT §5.9：先过滤再匹配；迭代05 接线）
        filter_kp_ids: list[str] | None = None
        if kp_codes:
            kp_rows = await db.execute(
                select(KnowledgePoint.id).where(KnowledgePoint.code.in_(kp_codes))
            )
            filter_kp_ids = [str(r) for r in kp_rows.scalars().all()]
            if not filter_kp_ids:
                log.info("rag.meta_filter_no_kp_match", kp_codes=kp_codes)
                return RAGResult(
                    chunks=[],
                    answerable=False,
                    refuse_reason="no_knowledge",
                    rewritten_query=rewritten,
                    **self._degrade_fields(),
                )
        if content_type or filter_kp_ids:
            log.info(
                "rag.meta_filter", content_type=content_type, kp_count=len(filter_kp_ids or [])
            )

        # Step 2: 三路并行召回（每路独立 session，避免共享 AsyncSession 并发不安全）
        async def _run_vector_search():
            async with async_session_factory() as session:
                return await self._vector_search(
                    rewritten, session, content_type, filter_kp_ids, scope, embedding_cfg,
                    source_kinds=source_kinds,
                )

        async def _run_trgm_search():
            async with async_session_factory() as session:
                return await self._trgm_search(
                    rewritten, session, content_type, filter_kp_ids, scope,
                    source_kinds=source_kinds,
                )

        async def _run_kp_search():
            async with async_session_factory() as session:
                return await self._kp_tag_search(
                    rewritten, session, content_type, filter_kp_ids, scope,
                    source_kinds=source_kinds,
                )

        # 云知识库第 4 路（仅 hybrid 全开模式启用；单路调试不受影响）
        # 配置解析在并行任务启动前串行完成（复用调用方 session，避免并发不安全）；
        # 解析失败/disabled 时 cloud_cfg=None，全程与现状零差异
        cloud_cfg = None
        if mode not in ("vector", "fulltext", "kp"):
            try:
                from app.providers.cloud_kb import resolve_cloud_kb_config

                cloud_cfg = await resolve_cloud_kb_config(db)
            except Exception as e:
                log.warning("rag.cloud_kb_config_failed", error=str(e)[:100])
                cloud_cfg = None

        # Embedding 配置同样在并行任务启动前串行解析（阶段 5b P0 修复）：
        # resolve_embedding_config 内部 begin_nested()（SAVEPOINT）在共享 session 上
        # 并发调用会与 trgm/kp 查询冲突，故先串行解析再传入向量路；
        # 解析失败时 embedding_cfg=None，EmbeddingProvider 回退 env 无参构造
        embedding_cfg: EmbeddingConfig | None = None
        if mode not in ("fulltext", "kp"):
            try:
                from app.providers.embedding import resolve_embedding_config

                embedding_cfg = await resolve_embedding_config(db)
            except Exception as e:
                log.warning("rag.embedding_config_failed", error=str(e)[:100])
                embedding_cfg = None

        async def _run_cloud_kb_search():
            """云端检索转 ScoredChunk 列表；任何失败/超时静默返回 []"""
            if cloud_cfg is None or not cloud_cfg.enabled:
                return []
            try:
                from app.providers.cloud_kb import retrieve_cloud_kb

                records = await asyncio.wait_for(
                    retrieve_cloud_kb(cloud_cfg, rewritten), timeout=CLOUD_KB_TIMEOUT_S
                )
            except Exception as e:
                log.warning("rag.cloud_kb_failed", error=str(e)[:100])
                return []
            return [
                self._cloud_record_to_scored_chunk(cloud_cfg.provider, r)
                for r in records
                if r.get("content")
            ]

        # mode=hybrid 三路全开；vector/fulltext/kp 单路调试只跑对应一路（未知值回退全开）
        route_runners = {
            "vector": _run_vector_search,
            "fulltext": _run_trgm_search,
            "kp": _run_kp_search,
        }
        active_routes = [mode] if mode in route_runners else list(route_runners)
        # 云通道仅在配置启用时作为第 4 路加入并行召回
        cloud_active = bool(cloud_cfg and cloud_cfg.enabled)
        route_names = active_routes + (["cloud_kb"] if cloud_active else [])

        tasks = [asyncio.create_task(route_runners[name]()) for name in active_routes]
        if cloud_active:
            tasks.append(asyncio.create_task(_run_cloud_kb_search()))
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理异常（某路失败不影响其他路）
        vec_results: list[ScoredChunk] = []
        trgm_results: list[ScoredChunk] = []
        kp_results: list[ScoredChunk] = []
        cloud_results: list[ScoredChunk] = []
        for name, res in zip(route_names, gathered, strict=True):
            if isinstance(res, Exception):
                log.warning(f"rag.{name}_failed", error=str(res)[:100])
                continue
            if name == "vector":
                vec_results = res
            elif name == "fulltext":
                trgm_results = res
            elif name == "kp":
                kp_results = res
            else:
                cloud_results = res

        log.info(
            "rag.recall",
            vec_count=len(vec_results),
            trgm_count=len(trgm_results),
            kp_count=len(kp_results),
            cloud_count=len(cloud_results),
        )

        # 如果各召回路都为空，直接返回不可答
        if not vec_results and not trgm_results and not kp_results and not cloud_results:
            return RAGResult(
                chunks=[],
                answerable=False,
                refuse_reason="no_knowledge",
                rewritten_query=rewritten,
                **self._degrade_fields(),
            )

        # Step 3: RRF 融合（云通道未启用时 cloud_results=[] 对结果零影响）
        fused = self._rrf_fuse([vec_results, trgm_results, kp_results, cloud_results], k=RRF_K)[
            :FUSED_TOP_K
        ]

        # Step 4: Rerank（降级：RRF 排序直取）
        reranked, used_reranker = await self._rerank(rewritten, fused, request_id=request_id)
        final_chunks = reranked[:RERANK_TOP_K]

        # Step 5: 拒答闸门（分源判定）
        if not final_chunks:
            return RAGResult(
                chunks=[],
                answerable=False,
                refuse_reason="no_knowledge",
                rewritten_query=rewritten,
                **self._degrade_fields(),
            )
        if used_reranker:
            relevant = final_chunks[0].score >= settings.rag_refuse_threshold
            gate_score = final_chunks[0].score
        else:
            # 降级路径分源判定：各路原始分量纲不同（cosine / wsim / kp 命中），
            # 不能用单一阈值卡 RRF 融合后的 top1（top1 可能来自弱相关路，
            # 而强相关路的命中被压在后面——rrf 同分时向量路排序靠前）。
            # 任一路自身达标即判相关：
            # - vector/cloud：余弦类分数 ≥ rag_raw_threshold（0.45，BGE-M3 实测校准）
            # - trgm：wsim ≥ rag_trgm_gate（0.35，精确文本命中信号）
            # - kp：结构化标签命中即相关
            relevant = (
                (bool(vec_results) and vec_results[0].raw_score >= settings.rag_raw_threshold)
                or (bool(trgm_results) and trgm_results[0].raw_score >= settings.rag_trgm_gate)
                or bool(kp_results)
                or (bool(cloud_results) and cloud_results[0].raw_score >= settings.rag_raw_threshold)
            )
            gate_score = final_chunks[0].raw_score
        if not relevant:
            log.info("rag.refused", gate_score=gate_score, used_reranker=used_reranker)
            return RAGResult(
                chunks=[],
                answerable=False,
                refuse_reason="low_relevance",
                rewritten_query=rewritten,
                **self._degrade_fields(),
            )

        log.info("rag.success", chunks=len(final_chunks), gate_score=gate_score)
        return RAGResult(
            chunks=final_chunks,
            answerable=True,
            rewritten_query=rewritten,
            **self._degrade_fields(),
        )

    async def _rewrite_query(
        self,
        question: str,
        history: list[dict],
        *,
        request_id: str = "",
        conversation_id: str = "",
        db: AsyncSession | None = None,
    ) -> str:
        """查询改写：指代补全（读取对话滚动摘要 + 最近 4 条历史）"""
        # 无历史且无对话摘要时直接返回原问题
        if not history and not conversation_id:
            return question

        # 取最近 4 条历史用于改写
        recent = history[-4:] if history else []
        history_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content'][:100]}" for m in recent
        )

        # 读取对话摘要（如果有）
        summary_text = ""
        if conversation_id and db:
            try:
                from app.models.conversation import Conversation

                conv_result = await db.execute(
                    select(Conversation.summary).where(Conversation.id == conversation_id)
                )
                summary_text = conv_result.scalar() or ""
            except Exception:
                pass  # 摘要读取失败不影响改写

        # 动态构建改写 prompt（含摘要上下文）
        prompt_parts = ["请将以下多轮对话中的最新问题改写为一个独立的、完整的问题。"]
        prompt_parts.append(
            '要求：补全指代（"它"、"这个"、"那第3个"等），使问题脱离上下文也能理解。'
        )
        prompt_parts.append("只输出改写后的问题，不要解释。")
        if summary_text:
            prompt_parts.append(f"\n对话摘要：\n{summary_text[:500]}")
        if history_text:
            prompt_parts.append(f"\n对话历史：\n{history_text}")
        prompt_parts.append(f"\n最新问题：{question}")
        prompt_parts.append("\n改写后的问题：")
        prompt = "\n".join(prompt_parts)

        try:
            router = get_model_router()
            result = await router.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                request_id=request_id,
                scene="rag_rewrite",
            )
            rewritten = result["content"].strip()
            return rewritten if rewritten else question
        except Exception as e:
            logger.warning("rag.rewrite_failed", error=str(e)[:100])
            return question

    async def _embedding_available(self, embedding_cfg: EmbeddingConfig | None = None) -> bool:
        """Embedding 服务可用性（60s 缓存 + 配置指纹失效，避免每查询一次健康检查往返）

        配置由调用方经 resolve_embedding_config(db) 串行解析后传入（阶段 5b P0 修复）：
        并行任务启动前串行解析，避免共享 session 上并发 begin_nested()（SAVEPOINT）冲突。
        embedding_cfg=None 时回退 env 无参构造（向后兼容）。
        60s 缓存绑定配置指纹：provider/base_url/model/dimension/key 摘要变化 → 立即重新检查。
        """
        now = time.monotonic()
        fingerprint = (
            _embedding_config_fingerprint(embedding_cfg)
            if embedding_cfg is not None
            else None
        )
        if (
            now - self._embedding_checked_at < EMBEDDING_HEALTH_TTL_S
            and fingerprint == self._embedding_cfg_fingerprint
        ):
            return self._embedding_ok
        try:
            from app.providers.embedding import EmbeddingProvider

            provider = (
                EmbeddingProvider(embedding_cfg)
                if embedding_cfg is not None
                else EmbeddingProvider()
            )
            health = await provider.health_check()
            self._embedding_ok = bool(health.get("ok"))
        except Exception as e:  # noqa: BLE001
            self._embedding_ok = False
            # G6：embedding 不可用必须可被上层区分（否则学生会看到「没有相关内容」的假象）
            self._record_degrade(e)
        self._embedding_checked_at = now
        self._embedding_cfg_fingerprint = fingerprint
        return self._embedding_ok

    async def _vector_search(
        self,
        query: str,
        db: AsyncSession,
        content_type: str | None = None,
        kp_ids: list[str] | None = None,
        scope: str | None = None,
        embedding_cfg: EmbeddingConfig | None = None,
        source_kinds: str | None = None,
    ) -> list[ScoredChunk]:
        """向量路召回（pgvector cosine）

        降级：Embedding 服务不可用，返回空列表。
        content_type/kp_ids 元数据过滤（SSOT §5.9 先过滤再匹配）。
        embedding_cfg 由 retrieve() 串行解析后传入（阶段 5b P0 修复）。
        """
        if not await self._embedding_available(embedding_cfg):
            return []

        try:
            from app.providers.embedding import EmbeddingProvider

            provider = (
                EmbeddingProvider(embedding_cfg)
                if embedding_cfg is not None
                else EmbeddingProvider()
            )
            vectors = await provider.embed([query])
            if not vectors or not vectors[0]:
                return []

            query_vec = vectors[0]
            distance = Chunk.embedding.cosine_distance(query_vec).label("dist")
            # 修复（迭代19 待查项）：select 必须包含 distance 列，否则 result.all()
            # 只返回 (chunk, title) 二元组，下方三元解包触发
            # "not enough values to unpack" → 向量路恒空、退化到 trgm 兜底。
            stmt = (
                select(Chunk, KnowledgeDoc.title, distance)
                .join(KnowledgeDoc, Chunk.doc_id == KnowledgeDoc.id)
                .where(Chunk.deleted_at.is_(None), Chunk.embedding.isnot(None))
            )
            if content_type:
                stmt = stmt.where(KnowledgeDoc.source_type == content_type)
            if kp_ids:
                stmt = stmt.where(Chunk.kp_ids.overlap(kp_ids))
            if scope:
                stmt = stmt.where(
                    KnowledgeDoc.meta_["scope"].astext.in_(scope.split(","))
                )
            if source_kinds:
                stmt = stmt.where(
                    Chunk.meta_["source_kind"].astext.in_(source_kinds.split(","))
                )
            result = await db.execute(stmt.order_by(distance).limit(RECALL_TOP_K))
            return [
                self._to_scored_chunk(
                    chunk,
                    default_score=1.0 - float(dist),
                    raw_score=1.0 - float(dist),
                    doc_title=title or "",
                )
                for chunk, title, dist in result.all()
            ]
        except Exception as e:
            logger.warning("rag.vector_error", error=str(e)[:200])
            self._record_degrade(e)  # G6
            return []

    async def _trgm_search(
        self,
        query: str,
        db: AsyncSession,
        content_type: str | None = None,
        kp_ids: list[str] | None = None,
        scope: str | None = None,
        source_kinds: str | None = None,
    ) -> list[ScoredChunk]:
        """全文路召回（pg_trgm word_similarity）

        短查询 vs 长文档必须用 word_similarity（similarity 在此场景
        得分量级过低，会被默认阈值全部过滤 —— M1 审查实测证实）。
        content_type/kp_ids 元数据过滤（SSOT §5.9）。
        """
        try:
            where_clauses = [
                "c.deleted_at IS NULL",
                "word_similarity(:query, c.content) > :threshold",
            ]
            params: dict = {
                "query": query,
                "threshold": settings.rag_trgm_threshold,
                "limit": RECALL_TOP_K,
            }
            if content_type:
                where_clauses.append("d.source_type = :content_type")
                params["content_type"] = content_type
            if kp_ids:
                where_clauses.append("c.kp_ids && CAST(:kp_ids AS uuid[])")
                params["kp_ids"] = kp_ids
            if scope:
                where_clauses.append("d.meta->>'scope' = ANY(CAST(:scope AS text[]))")
                params["scope"] = scope.split(",")
            if source_kinds:
                where_clauses.append("c.meta->>'source_kind' = ANY(CAST(:source_kinds AS text[]))")
                params["source_kinds"] = source_kinds.split(",")
            # 全文检索可降级，但其 SQL 错误不能把调用方的事务一并标记为 aborted。
            async with db.begin_nested():
                result = await db.execute(
                    text(f"""
                        SELECT c.id, c.doc_id, c.content, c.kp_ids, c.meta,
                               word_similarity(:query, c.content) as wsim,
                               COALESCE(d.title, '教材') as doc_title
                        FROM chunks c
                        LEFT JOIN knowledge_docs d ON c.doc_id = d.id
                        WHERE {' AND '.join(where_clauses)}
                        ORDER BY wsim DESC
                        LIMIT :limit
                    """),
                    params,
                )
            rows = result.fetchall()
            return [
                ScoredChunk(
                    chunk_id=str(row[0]),
                    doc_id=str(row[1]),
                    content=row[2],
                    kp_ids=row[3] if row[3] else [],
                    score=float(row[5]),
                    raw_score=float(row[5]),
                    doc_title=row[6],
                    meta=row[4] if row[4] else {},
                )
                for row in rows
            ]
        except Exception as e:
            logger.warning("rag.trgm_error", error=str(e)[:200])
            self._record_degrade(e)  # G6：有 ILIKE 兜底，但结果可能不全，须可区分
            # 降级：使用 ILIKE 模糊搜索
            return await self._fallback_text_search(query, db)

    async def _fallback_text_search(self, query: str, db: AsyncSession) -> list[ScoredChunk]:
        """降级文本搜索（当 pg_trgm 不可用时）：查询前缀 ILIKE

        用户查询中的 %/_/反斜杠须转义，否则通配符注入导致结果失真。
        """
        escaped = query[:20].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        result = await db.execute(
            select(Chunk)
            .where(
                Chunk.deleted_at.is_(None),
                Chunk.content.ilike(f"%{escaped}%", escape="\\"),
            )
            .limit(RECALL_TOP_K)
        )
        chunks = result.scalars().all()
        return [self._to_scored_chunk(c, 0.5, raw_score=0.5) for c in chunks]

    async def _kp_tag_search(
        self,
        query: str,
        db: AsyncSession,
        content_type: str | None = None,
        kp_ids: list[str] | None = None,
        scope: str | None = None,
        source_kinds: str | None = None,
    ) -> list[ScoredChunk]:
        """知识点标签路召回：查询文本包含知识点别名即命中；支持元数据过滤（SSOT §5.9）"""
        try:
            alias_hit = text(
                "EXISTS (SELECT 1 FROM unnest(aliases) AS a " "WHERE :query ILIKE '%' || a || '%')"
            ).bindparams(query=query)
            kp_result = await db.execute(select(KnowledgePoint).where(alias_hit).limit(5))
            matched_kps = kp_result.scalars().all()

            if not matched_kps:
                return []

            # 通过 kp_ids 找关联的 chunks；元数据过滤时取交集
            hit_ids = [str(kp.id) for kp in matched_kps]
            if kp_ids is not None:
                hit_ids = [i for i in hit_ids if i in set(kp_ids)]
                if not hit_ids:
                    return []
            stmt = (
                select(Chunk, KnowledgeDoc.title)
                .join(KnowledgeDoc, Chunk.doc_id == KnowledgeDoc.id)
                .where(
                    Chunk.deleted_at.is_(None),
                    Chunk.kp_ids.overlap(hit_ids),
                )
            )
            if content_type:
                stmt = stmt.where(KnowledgeDoc.source_type == content_type)
            if scope:
                stmt = stmt.where(KnowledgeDoc.meta_["scope"].astext.in_(scope.split(",")))
            if source_kinds:
                stmt = stmt.where(
                    Chunk.meta_["source_kind"].astext.in_(source_kinds.split(","))
                )
            chunk_result = await db.execute(stmt.limit(RECALL_TOP_K))
            rows = chunk_result.all()
            return [
                self._to_scored_chunk(c, 0.7, raw_score=0.7, doc_title=t or "") for c, t in rows
            ]
        except Exception as e:
            logger.warning("rag.kp_error", error=str(e)[:200])
            return []

    def _rrf_fuse(self, result_lists: list[list[ScoredChunk]], k: int = 60) -> list[ScoredChunk]:
        """RRF（Reciprocal Rank Fusion）融合多路召回结果。

        score = RRF 分（排序用）；raw_score = 各路原始相关分的最大值（闸门用）。
        """
        scores: dict[str, float] = {}
        raw_scores: dict[str, float] = {}
        chunk_map: dict[str, ScoredChunk] = {}

        for results in result_lists:
            for rank, chunk in enumerate(results):
                rrf_score = 1.0 / (k + rank + 1)
                raw_scores[chunk.chunk_id] = max(
                    raw_scores.get(chunk.chunk_id, 0.0), chunk.raw_score
                )
                if chunk.chunk_id in scores:
                    scores[chunk.chunk_id] += rrf_score
                else:
                    scores[chunk.chunk_id] = rrf_score
                    chunk_map[chunk.chunk_id] = chunk

        # 按 RRF 分数排序
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        result = []
        for cid in sorted_ids:
            chunk = chunk_map[cid]
            chunk.score = scores[cid]
            chunk.raw_score = raw_scores.get(cid, 0.0)
            result.append(chunk)
        return result

    async def _rerank_health_ok(self) -> bool:
        """Reranker 服务可用性（TTL 缓存，避免每次请求 HTTP 健康检查往返）"""
        now = time.monotonic()
        if now - _rerank_health_cache["ts"] < RERANK_HEALTH_TTL_S:
            logger.debug("rag.rerank_health_cache_hit", ok=_rerank_health_cache["ok"])
            return _rerank_health_cache["ok"]
        logger.debug("rag.rerank_health_cache_miss")
        try:
            from app.providers.reranker import RerankProvider

            health = await RerankProvider().health_check()
            _rerank_health_cache["ok"] = bool(health.get("ok"))
        except Exception:
            _rerank_health_cache["ok"] = False
        _rerank_health_cache["ts"] = now
        return _rerank_health_cache["ok"]

    async def _rerank(
        self, query: str, chunks: list[ScoredChunk], *, request_id: str
    ) -> tuple[list[ScoredChunk], bool]:
        """Rerank 精排。

        返回 (chunks, used_reranker)。Reranker 不可用时降级为 RRF 排序直取。
        健康检查使用 TTL 缓存，避免每次请求都发起 HTTP 往返。
        """
        if chunks:
            try:
                from app.providers.reranker import RerankProvider

                if await self._rerank_health_ok():
                    reranker = RerankProvider()
                    pairs = await reranker.rerank(
                        query, [c.content for c in chunks], request_id=request_id
                    )
                    reranked: list[ScoredChunk] = []
                    for idx, score in pairs:
                        chunk = chunks[idx]
                        chunk.score = score
                        chunk.raw_score = max(chunk.raw_score, score)
                        reranked.append(chunk)
                    return reranked, True
            except Exception as e:
                logger.warning("rag.rerank_failed", error=str(e)[:200])

        # 降级：直接按 RRF 分数排序返回
        return sorted(chunks, key=lambda c: c.score, reverse=True), False

    def _to_scored_chunk(
        self, chunk: Chunk, default_score: float, raw_score: float = 0.0, doc_title: str = ""
    ) -> ScoredChunk:
        """将 ORM Chunk 转为 ScoredChunk"""
        return ScoredChunk(
            chunk_id=str(chunk.id),
            doc_id=str(chunk.doc_id),
            content=chunk.content or "",
            score=default_score,
            raw_score=raw_score,
            kp_ids=chunk.kp_ids if chunk.kp_ids else [],
            doc_title=doc_title,
            meta=dict(chunk.meta_) if chunk.meta_ else {},
        )

    @staticmethod
    def _cloud_record_to_scored_chunk(provider: str, record: dict) -> ScoredChunk:
        """云端记录转 ScoredChunk（作为第 4 路进 RRF 融合，k=60 逻辑不变）

        chunk_id 用内容 sha1 前缀生成（云记录无本地 chunk 主键），score/raw_score
        取云端相关度得分（已归一化 0~1）。
        引用链安全（已核实）：qa_rag 的 citations 由 ScoredChunk 字段直接构造、
        guard 仅用 len(valid_chunk_ids) 校验【N】序号范围、citation 事件与信封
        落库/幂等重放均不反查 chunk_id —— 云 chunk 全程不触发 DB 查询，不会 500。
        kp/content_type 元数据过滤不适用云通道（云端记录无本地 kp_ids/文档分类）。
        """
        content = record.get("content") or ""
        score = float(record.get("score") or 0.0)
        return ScoredChunk(
            chunk_id=f"cloudkb-{hashlib.sha1(content.encode('utf-8')).hexdigest()[:12]}",
            doc_id="cloud_kb",
            content=content,
            doc_title=f"云知识库·{provider}",
            score=score,
            raw_score=score,
            kp_ids=[],
        )


# ---- 全局单例 ----
_rag_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    """获取全局 RAGPipeline 单例"""
    global _rag_pipeline
    if _rag_pipeline is None:
        _rag_pipeline = RAGPipeline()
    return _rag_pipeline
