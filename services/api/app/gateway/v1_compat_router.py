"""学生端 /api/v1 兼容路由（B1/B2「集成服务」下线后的能力归位）

背景（2026-09-12 审计 B 项）：
`D:\\frontend` 曾把 `/api/agent` 与 `/api/v1` 代理到 `127.0.0.1:8012` 的「集成服务」。
该服务源码已不在仓库、无法审计、无法复现构建，且浏览器对 :8000 的
`agent_router` / `skills` 命中数为 0 —— 即修复被写在死代码上。
现统一回落 :8000，本路由承接 `/api/v1` 的三个真实消费点。

端点与消费方（证据为前端调用点）：
- POST /api/v1/kp/guess       知识点确定性识别（ErrorsView.guessKp）
- GET  /api/v1/questions      学生题库列表（LibraryHomeView.loadQuestions）
- POST /api/v1/kb/ingest-text 资料文本入库（useFileUpload.scheduleKnowledgeIngest）

设计纪律：
- 只复用既有表（knowledge_points / question_bank / knowledge_docs / chunks），不新建存储。
- kp 识别为**确定性字符串匹配**（知识点名/别名命中题干），零幻觉；识别不到如实返回 unmatched。
- kb 入库沿用 kb_router 的红线：公式配对校验 + embedding 为 NULL 不入库（整批退回）。
- 拒绝不是失败：返回 `status="rejected"` + `reason`，让前端可展示真实原因而非静默降级。
"""

from __future__ import annotations

import re
import uuid

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.gateway.kb_router import _VALID_CONTENT_TYPES, _latex_paired
from app.models.chunk import Chunk
from app.models.database import get_db
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import BANK_DIFFICULTIES, BANK_Q_TYPES, QuestionBank
from app.providers.embedding import EmbeddingProvider, resolve_embedding_config

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1-compat"])

# 知识点候选名最短长度：单字名（如「概率」的别名「率」）误命中率过高，宁可不识别
_MIN_KP_ALIAS_LEN = 2
# 单次识别扫描的知识点上限（knowledge_points 为教学大纲级，量级千以内）
_KP_SCAN_LIMIT = 5000
# 入库切块参数：约 500 字/片，按段落边界切，不留 overlap（教材切片语义完整）
_INGEST_CHUNK_SIZE = 500
_INGEST_MIN_CONTENT = 80
_INGEST_MAX_CONTENT = 200_000
# 同源文件重复入库的判据字段（幂等）
_INGEST_SOURCE_KEY = "source_file_id"


# ========== 1. 知识点确定性识别 ==========


class KpGuessRequest(BaseModel):
    text: str = Field(default="", max_length=2000)


@router.post("/kp/guess")
async def kp_guess(
    req: KpGuessRequest,
    _user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从题干文本识别知识点（确定性词表匹配，零幻觉）。

    返回 `{matched, code, name}`；未命中时 `matched=false` 且 code/name 为空串，
    由前端提示「没认出知识点，请手动选择」——不猜、不编。
    """
    text = (req.text or "").strip()[:500]
    if not text:
        return {"code": 0, "data": {"matched": False, "code": "", "name": ""}}

    rows = await db.execute(
        select(KnowledgePoint.code, KnowledgePoint.name, KnowledgePoint.aliases)
        .order_by(KnowledgePoint.code)
        .limit(_KP_SCAN_LIMIT)
    )

    best_score = 0
    best_code = ""
    best_name = ""
    for code, name, aliases in rows.all():
        # 候选 = 正式名 + 全部别名；命中越长的名字越具体，取最长命中
        for candidate in [name, *(aliases or [])]:
            cand = (candidate or "").strip()
            if len(cand) < _MIN_KP_ALIAS_LEN or cand not in text:
                continue
            if len(cand) > best_score:
                best_score, best_code, best_name = len(cand), code, name

    if not best_code:
        logger.info("v1_kp_guess_unmatched", text_len=len(text))
        return {"code": 0, "data": {"matched": False, "code": "", "name": ""}}

    return {"code": 0, "data": {"matched": True, "code": best_code, "name": best_name}}


# ========== 2. 学生题库列表 ==========


async def _kp_name_map(db: AsyncSession, codes: list[str]) -> dict[str, str]:
    """知识点 code → 中文名（题库列表/详情共用；缺失 code 不编造名称）。"""
    wanted = {c for c in codes if c}
    if not wanted:
        return {}
    rows = await db.execute(
        select(KnowledgePoint.code, KnowledgePoint.name).where(KnowledgePoint.code.in_(wanted))
    )
    return {code: name for code, name in rows.all() if name}


@router.get("/questions")
async def list_questions(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q_type: str = Query(default=""),
    difficulty: str = Query(default=""),
    kp_code: str = Query(default=""),
    q: str = Query(default="", max_length=100),
    _user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生可见题库（`question_bank` 结构化题库，只读）。

    仅返回 `scope='student'` 且未软删的题目；非法筛选值忽略（不报错——前端
    筛选器传空串属正常路径）。`q` 只搜题干，`kp_code` 走数组包含，按创建时间倒序。
    """
    conditions = [
        QuestionBank.deleted_at.is_(None),
        QuestionBank.scope == "student",
    ]
    if q_type in BANK_Q_TYPES:
        conditions.append(QuestionBank.q_type == q_type)
    if difficulty in BANK_DIFFICULTIES:
        conditions.append(QuestionBank.difficulty == difficulty)
    if kp_code.strip():
        conditions.append(QuestionBank.kp_codes.contains([kp_code.strip()]))
    if q.strip():
        # 搜索覆盖题干 + 来源 + 知识点名：搜「导数」也能命中仅以公式呈现的导数题。
        # escape 用户输入里的 %/_，避免变成通配符扫描全表。
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        kp_codes_hit = (
            (
                await db.execute(
                    select(KnowledgePoint.code).where(
                        KnowledgePoint.name.ilike(pattern, escape="\\")
                    )
                )
            )
            .scalars()
            .all()
        )
        or_terms = [
            QuestionBank.stem.ilike(pattern, escape="\\"),
            QuestionBank.source.ilike(pattern, escape="\\"),
        ]
        if kp_codes_hit:
            or_terms.append(QuestionBank.kp_codes.overlap(kp_codes_hit))
        conditions.append(or_(*or_terms))

    total = (
        await db.execute(select(func.count()).select_from(QuestionBank).where(*conditions))
    ).scalar() or 0

    rows = await db.execute(
        select(
            QuestionBank.id,
            QuestionBank.stem,
            QuestionBank.q_type,
            QuestionBank.difficulty,
            QuestionBank.source,
            QuestionBank.kp_codes,
            QuestionBank.year,
            QuestionBank.is_real_exam,
        )
        .where(*conditions)
        .order_by(QuestionBank.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    raw_items = rows.all()
    name_map = await _kp_name_map(
        db,
        [
            code
            for _qid, _stem, _qt, _diff, _src, codes, _year, _real in raw_items
            for code in (codes or [])
        ],
    )
    items = [
        {
            "id": str(qid),
            "stem": stem,
            "q_type": qt,
            "difficulty": diff,
            "source": source,
            "kp_codes": list(kp_codes or []),
            "kp_names": [name_map[c] for c in (kp_codes or []) if name_map.get(c)],
            "year": year,
            "is_real_exam": bool(is_real),
        }
        for qid, stem, qt, diff, source, kp_codes, year, is_real in raw_items
    ]
    return {"code": 0, "data": {"total": total, "items": items, "offset": offset, "limit": limit}}


@router.get("/questions/{question_id}")
async def get_question(
    question_id: uuid.UUID,
    _user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生题库单题详情（含选项/答案/解析，供知识库点开查看与直接发起练习）。"""
    row = await db.get(QuestionBank, question_id)
    if row is None or row.deleted_at is not None or row.scope != "student":
        return {"code": 40400, "message": "题目不存在"}
    codes = list(row.kp_codes or [])
    name_map = await _kp_name_map(db, codes)
    return {
        "code": 0,
        "data": {
            "id": str(row.id),
            "stem": row.stem,
            "q_type": row.q_type,
            "options": row.options or {},
            "answer": row.answer or "",
            "analysis": row.analysis or "",
            "difficulty": row.difficulty,
            "source": row.source,
            "year": row.year,
            "is_real_exam": bool(row.is_real_exam),
            "kp_codes": codes,
            "kp_names": [name_map[c] for c in codes if name_map.get(c)],
            "image": row.image or [],
        },
    }


# ========== 3. 资料文本入库 ==========


class IngestTextRequest(BaseModel):
    user_id: str = Field(default="", max_length=64)
    title: str = Field(default="未命名资料", max_length=255)
    content: str = Field(default="")
    source_file_id: str | None = Field(default=None, max_length=128)
    purpose: str = Field(default="textbook", max_length=32)


def _split_paragraphs(text: str, size: int = _INGEST_CHUNK_SIZE) -> list[str]:
    """按段落边界切块：优先在空行处断开，超长段落硬切。空片丢弃。"""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks: list[str] = []
    buf = ""
    for block in blocks:
        if len(block) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(block[i : i + size] for i in range(0, len(block), size))
            continue
        if len(buf) + len(block) + 1 > size and buf:
            chunks.append(buf)
            buf = block
        else:
            buf = f"{buf}\n{block}" if buf else block
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


def _reject(status: str, reason: str) -> dict:
    """统一的入库拒绝响应。

    顶层同时给出 `status`/`reason`（前端 `useFileUpload` 原生 fetch 直接读这两个字段）
    与规范信封 `code`/`data`，避免为兼容一个旧契约而破坏信封一致性。
    """
    logger.info("v1_kb_ingest_rejected", reason=reason[:120])
    return {
        "code": 0,
        "status": status,
        "reason": reason,
        "data": {"status": status, "reason": reason},
    }


@router.post("/kb/ingest-text")
async def kb_ingest_text(
    req: IngestTextRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """把上传资料解析出的文本切块入库（`knowledge_docs` + `chunks`）。

    遵守 kb_router 的入库纪律：公式配对校验 + embedding 为 NULL 不入库。
    embedding 服务不可用时**拒入**而非写入半成品——脏向量比不入库更有害。
    """
    content = (req.content or "").strip()
    if len(content) < _INGEST_MIN_CONTENT:
        return _reject("rejected", f"内容过短（<{_INGEST_MIN_CONTENT} 字），不建议入库")
    if len(content) > _INGEST_MAX_CONTENT:
        return _reject("rejected", f"内容过长（>{_INGEST_MAX_CONTENT} 字），请拆分后上传")
    if not _latex_paired(content):
        return _reject("rejected", "公式配对检查未通过（$/$$ 不配对，ADR-024）")

    chunks = _split_paragraphs(content)
    if not chunks:
        return _reject("rejected", "切块后无有效内容")

    uploader_id = uuid.UUID(user["sub"])

    # 幂等：同一来源文件已入库则直接返回既有文档（重复上传不产生重复语料）
    if req.source_file_id:
        existing = await db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.meta_[_INGEST_SOURCE_KEY].astext == req.source_file_id,
                KnowledgeDoc.deleted_at.is_(None),
            )
        )
        old_doc = existing.scalars().first()
        if old_doc is not None:
            return {
                "code": 0,
                "status": "ok",
                "reason": None,
                "doc_id": str(old_doc.id),
                "chunk_count": (old_doc.meta_ or {}).get("chunk_count", 0),
                "data": {"status": "ok", "doc_id": str(old_doc.id), "reused": True},
            }

    embedder = EmbeddingProvider(await resolve_embedding_config(db))
    try:
        vectors = await embedder.embed(chunks)
    except Exception as e:
        logger.warning("v1_kb_ingest_embed_failed", error=str(e)[:150], chunks=len(chunks))
        return _reject("error", "向量化服务不可用，本次未入库（可稍后重试）")

    if len(vectors) != len(chunks) or any(v is None or len(v) == 0 for v in vectors):
        return _reject("error", "向量化结果不完整，本次未入库（可稍后重试）")

    source_type = req.purpose if req.purpose in _VALID_CONTENT_TYPES else "textbook"
    try:
        doc = KnowledgeDoc(
            title=req.title[:255] or "未命名资料",
            source_type=source_type,
            file_uri=(req.source_file_id or "")[:512] or None,
            uploader_id=uploader_id,
            status="ready",
            meta_={
                "origin": "ingest-text",
                _INGEST_SOURCE_KEY: req.source_file_id,
                "chunk_count": len(chunks),
                "client_user_id": req.user_id,
                # 端隔离：RAG 的 scope 过滤是 meta->>'scope' 对请求 scope 拆分后的 IN 精确匹配
                # （rag.py:529），必须写**单值** 'student'。写 'student,teacher' 整串永远不匹配，
                # 文档对检索不可见（2026-10-05 实测修复：此前聊天入库的 11 篇资料全部检索不到）。
                "scope": "student",
                "source_kind": "TEXTBOOK",
            },
        )
        db.add(doc)
        await db.flush()
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
            db.add(
                Chunk(
                    doc_id=doc.id,
                    content=chunk,
                    embedding=vector,
                    kp_ids=[],
                    chunk_index=idx,
                )
            )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("v1_kb_ingest_persist_failed", error=str(e)[:200])
        return _reject("error", f"落库失败：{str(e)[:80]}")

    logger.info("v1_kb_ingest_ok", doc_id=str(doc.id), chunks=len(chunks))
    return {
        "code": 0,
        "status": "ok",
        "reason": None,
        "doc_id": str(doc.id),
        "chunk_count": len(chunks),
        "data": {"status": "ok", "doc_id": str(doc.id), "chunk_count": len(chunks)},
    }
