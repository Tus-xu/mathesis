"""知识库试点路由（SSOT §5.8 / API 文档 §5，迭代05 补齐 B-P1-14）

端点（统一鉴权：JWT + teacher/researcher，学生端不开放）：
- POST /api/kb/docs/import — 批次导入（整批退回制）
- GET /api/kb/docs — 文档列表
- GET /api/kb/docs/{doc_id}/chunks — 切片列表
- POST /api/kb/retrieve — 试点检索台（对齐 /tools/retrieve）
- GET /api/kb/eval/recall — 评测结果（读 kb_eval_runs 最新一行，ADR-016）

入库纪律（SSOT §5.8）：
- 交接三件套（license/数据等级 L0-L3/版权灯）不齐 → 整批退回
- embedding 为 NULL 不允许入库（红线）
- 公式配对复检（$ 偶数、$$ 成对）100% 通过才入库（f4 §5 红线）
- kp_codes 必须存在于 knowledge_points 表
"""

from __future__ import annotations

import json
import re
import uuid

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import require_role
from app.models.chunk import Chunk
from app.models.database import get_db
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.models.m2_logs import KbEvalRun
from app.providers.embedding import EmbeddingProvider, resolve_embedding_config
from app.providers.storage import get_storage

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])

# content_type 七枚举（SSOT §5.8 / API 文档 §5）
_VALID_CONTENT_TYPES = {
    "textbook",
    "question",
    "standard",
    "lesson_plan",
    "method_card",
    "term",
    "paper",
}
_DATA_LEVELS = {"L0", "L1", "L2", "L3"}
_COPYRIGHT_LIGHTS = {"green", "yellow", "red"}

# LaTeX 配对校验（f4 §5 红线 / ADR-024 入库纪律）
_DOLLAR_PAIR_RE = re.compile(r"(?<!\$)\$(?!\$)")


def _latex_paired(text: str) -> bool:
    """公式配对检查：$$ 成对、单 $ 偶数（ADR-024）"""
    dd = text.count("$$")
    if dd % 2 != 0:
        return False
    body = text.replace("$$", "")
    return len(_DOLLAR_PAIR_RE.findall(body)) % 2 == 0


class KbImportRequest(BaseModel):
    batch_id: str = Field(..., max_length=64)  # <日期>-<来源简称>-<序号>
    manifest: dict  # {license, data_level, copyright_light}
    chunks_file_url: str = Field(..., max_length=512)


@router.post("/docs/import")
async def kb_docs_import(
    req: KbImportRequest,
    user: dict = Depends(require_role("teacher", "researcher")),
    db: AsyncSession = Depends(get_db),
):
    """批次导入（整批退回制，SSOT §5.8）"""
    # 1. 交接三件套校验
    manifest = req.manifest or {}
    missing = [k for k in ("license", "data_level", "copyright_light") if not manifest.get(k)]
    if missing:
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": f"manifest 缺字段: {','.join(missing)}（整批退回）",
            },
        }
    if (
        manifest["data_level"] not in _DATA_LEVELS
        or manifest["copyright_light"] not in _COPYRIGHT_LIGHTS
    ):
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": "data_level 须为 L0-L3、copyright_light 须为 green/yellow/red（整批退回）",
            },
        }

    # 幂等：批次已存在 → 拒绝重复导入（scripts/import_gaokao.py --force 可删旧批次重导）
    existing = await db.execute(
        select(KnowledgeDoc).where(
            KnowledgeDoc.meta_["batch_id"].astext == req.batch_id,
            KnowledgeDoc.deleted_at.is_(None),
        )
    )
    old_doc = existing.scalar_one_or_none()
    if old_doc is not None:
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": str(old_doc.id),
                "accepted": False,
                "rejected_reason": f"批次已存在: {req.batch_id}（幂等保护，不重复导入）",
            },
        }

    # 2. 读取 chunks.jsonl
    try:
        storage = get_storage()
        raw = storage.get_bytes(req.chunks_file_url)
        lines = [ln for ln in raw.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    except Exception as e:
        logger.warning("kb_import_read_failed", error=str(e)[:200])
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": f"chunks 文件读取失败: {str(e)[:100]}（整批退回）",
            },
        }
    if not lines:
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": "chunks 文件为空（整批退回）",
            },
        }

    # 3. 逐条解析与校验（任一条不过 → 整批退回）
    parsed: list[dict] = []
    for idx, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except json.JSONDecodeError:
            return {
                "code": 0,
                "data": {
                    "batch_id": req.batch_id,
                    "doc_id": None,
                    "accepted": False,
                    "rejected_reason": f"第 {idx + 1} 行 JSON 非法（整批退回）",
                },
            }
        content = (item.get("content") or "").strip()
        if not content:
            return {
                "code": 0,
                "data": {
                    "batch_id": req.batch_id,
                    "doc_id": None,
                    "accepted": False,
                    "rejected_reason": f"第 {idx + 1} 条 content 为空（整批退回）",
                },
            }
        content_type = item.get("content_type") or "question"
        if content_type not in _VALID_CONTENT_TYPES:
            return {
                "code": 0,
                "data": {
                    "batch_id": req.batch_id,
                    "doc_id": None,
                    "accepted": False,
                    "rejected_reason": f"第 {idx + 1} 条 content_type 非法: {content_type}（整批退回）",
                },
            }
        if not _latex_paired(content):
            return {
                "code": 0,
                "data": {
                    "batch_id": req.batch_id,
                    "doc_id": None,
                    "accepted": False,
                    "rejected_reason": f"第 {idx + 1} 条公式配对检查未通过（$/$$ 不配对，整批退回，ADR-024）",
                },
            }
        parsed.append(item)

    # 4. kp_codes 存在性校验 + 转 kp_ids
    all_codes = list({c for item in parsed for c in (item.get("kp_codes") or [])})
    kp_map: dict[str, uuid.UUID] = {}
    if all_codes:
        rows = await db.execute(
            select(KnowledgePoint.id, KnowledgePoint.code).where(KnowledgePoint.code.in_(all_codes))
        )
        kp_map = {code: kp_id for kp_id, code in rows.all()}
    unknown = [c for c in all_codes if c not in kp_map]
    if unknown:
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": f"kp_codes 不存在于 knowledge_points: {','.join(unknown[:5])}（整批退回）",
            },
        }

    # 5. embedding 生成/校验（红线：NULL 不入库）
    # 缺 embedding 的条目集中批量生成（32 条/批，对齐 scripts/import_gaokao.py；
    # 844 题从 844 次往返降为 ~27 次；服务不可用则整批退回）
    # embedding 提供商可配置：system_configs["embedding"] 优先 → env 兜底（配置缺失自动回退本地）
    embedder = EmbeddingProvider(await resolve_embedding_config(db))
    need_idx = [i for i, item in enumerate(parsed) if not item.get("embedding")]
    for start in range(0, len(need_idx), 32):
        batch_idx = need_idx[start : start + 32]
        try:
            vectors = await embedder.embed([parsed[i]["content"] for i in batch_idx])
        except Exception as e:
            logger.warning("kb_import_embed_failed", batch=start // 32, error=str(e)[:150])
            vectors = []
        for j, i in enumerate(batch_idx):
            parsed[i]["_embedding"] = vectors[j] if j < len(vectors) else None
    for idx, item in enumerate(parsed):
        emb = item.get("embedding") or item.get("_embedding")
        if not emb:
            return {
                "code": 0,
                "data": {
                    "batch_id": req.batch_id,
                    "doc_id": None,
                    "accepted": False,
                    "rejected_reason": f"第 {idx + 1} 条 embedding 为 NULL（红线拒入，整批退回）",
                },
            }
        item["_embedding"] = emb

    # 6. 全部通过 → 落库（knowledge_docs + chunks）
    title = parsed[0].get("doc_title") or req.batch_id
    try:
        doc = KnowledgeDoc(
            title=str(title)[:255],
            source_type=parsed[0].get("content_type") or "question",
            file_uri=req.chunks_file_url[:512],
            uploader_id=uuid.UUID(user["sub"]),
            status="ready",
            meta_={"batch_id": req.batch_id, "manifest": manifest, "chunk_count": len(parsed)},
        )
        db.add(doc)
        await db.flush()
        for idx, item in enumerate(parsed):
            db.add(
                Chunk(
                    doc_id=doc.id,
                    content=item["content"],
                    embedding=item["_embedding"],
                    kp_ids=[kp_map[c] for c in (item.get("kp_codes") or [])],
                    chunk_index=idx,
                )
            )
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error("kb_import_persist_failed", error=str(e)[:200])
        return {
            "code": 0,
            "data": {
                "batch_id": req.batch_id,
                "doc_id": None,
                "accepted": False,
                "rejected_reason": f"落库失败（embedding 维度/字段异常）: {str(e)[:80]}（整批退回）",
            },
        }
    logger.info("kb_import_accepted", batch_id=req.batch_id, chunks=len(parsed))

    return {
        "code": 0,
        "data": {
            "batch_id": req.batch_id,
            "doc_id": str(doc.id),
            "accepted": True,
            "rejected_reason": None,
        },
    }


@router.get("/docs")
async def kb_docs_list(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    _: dict = Depends(require_role("teacher", "researcher", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """文档列表（API 文档 §5.2；admin 检索试验台可见）"""
    total = (
        await db.execute(
            select(func.count()).select_from(KnowledgeDoc).where(KnowledgeDoc.deleted_at.is_(None))
        )
    ).scalar() or 0
    rows = await db.execute(
        select(KnowledgeDoc)
        .where(KnowledgeDoc.deleted_at.is_(None))
        .order_by(KnowledgeDoc.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = [
        {
            "doc_id": str(d.id),
            "title": d.title,
            "batch_id": (d.meta_ or {}).get("batch_id"),
            "content_type": d.source_type,
            "status": d.status,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows.scalars().all()
    ]
    return {"code": 0, "data": {"total": total, "items": items}}


@router.get("/docs/{doc_id}/chunks")
async def kb_doc_chunks(
    doc_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    _: dict = Depends(require_role("teacher", "researcher", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """切片列表（API 文档 §5.3；kp_ids 回填 code 便于调试）"""
    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None or doc.deleted_at:
        return {"code": 40400, "message": "知识库文档不存在"}

    total = (
        await db.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc_id, Chunk.deleted_at.is_(None))
        )
    ).scalar() or 0
    rows = await db.execute(
        select(Chunk)
        .where(Chunk.doc_id == doc_id, Chunk.deleted_at.is_(None))
        .order_by(Chunk.chunk_index)
        .offset((page - 1) * size)
        .limit(size)
    )
    chunks = rows.scalars().all()

    # kp_ids → code 回填
    kp_ids = list({k for c in chunks for k in (c.kp_ids or [])})
    kp_code_map: dict = {}
    if kp_ids:
        kp_rows = await db.execute(
            select(KnowledgePoint.id, KnowledgePoint.code).where(KnowledgePoint.id.in_(kp_ids))
        )
        kp_code_map = dict(kp_rows.all())

    items = [
        {
            "chunk_id": str(c.id),
            "temp_id": f"{doc_id}-{c.chunk_index}",
            "content_type": doc.source_type,
            "kp_codes": [kp_code_map.get(k, str(k)) for k in (c.kp_ids or [])],
            "has_embedding": c.embedding is not None,
            "source_ref": {"doc_id": str(doc_id), "chunk_index": c.chunk_index},
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in chunks
    ]
    return {"code": 0, "data": {"total": total, "items": items}}


class KbRetrieveRequest(BaseModel):
    query: str = Field(..., max_length=500)
    content_type: str | None = None
    kp_codes: list[str] | None = None
    top_k: int = Field(default=4, le=10)
    scope: str | None = None  # 端隔离过滤：student/teacher/research（逗号分隔；缺省=全量）


@router.post("/retrieve")
async def kb_retrieve(
    req: KbRetrieveRequest,
    user: dict = Depends(require_role("student", "teacher", "researcher", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """试点检索台（API 文档 §5.4；响应对齐 /tools/retrieve）

    端隔离（scope）：请求方显式传 scope 时用之；未传时按角色默认——
    student → student；teacher → student,teacher；researcher/admin → 全量。
    """
    if req.content_type and req.content_type not in _VALID_CONTENT_TYPES:
        return {"code": 40001, "message": f"content_type 非法: {req.content_type}"}
    try:
        from app.kernel.rag import get_rag_pipeline

        roles = (user.get("roles") or []) + [user.get("active_role") or ""]
        scope = req.scope
        if not scope:
            if "researcher" in roles or "admin" in roles:
                scope = "student,teacher,research"
            elif "teacher" in roles:
                scope = "student,teacher"
            else:
                scope = "student"

        result = await get_rag_pipeline().retrieve(
            req.query,
            db=db,
            content_type=req.content_type,
            kp_codes=req.kp_codes,
            scope=scope,
        )
        chunks = [
            {
                "chunk_id": c.chunk_id,
                "content": c.content[:500],
                "score": round(c.score, 4),
                "raw_score": round(c.raw_score, 4),
                "kp_codes": c.kp_ids,
                "doc_title": c.doc_title,
                "scope": scope,
            }
            for c in result.chunks[: req.top_k]
        ]
        return {
            "code": 0,
            "data": {
                "chunks": chunks,
                "answerable": result.answerable,
                "scope": scope,
                "gate": {
                    "top1_score": round(result.chunks[0].raw_score, 4) if result.chunks else 0.0,
                    "threshold": 0.35,
                },
            },
        }
    except Exception as e:
        logger.error("kb_retrieve_failed", error=str(e)[:200])
        return {"code": 50001, "message": "检索失败，请稍后重试"}


@router.get("/eval/recall")
async def kb_eval_recall(
    _: dict = Depends(require_role("teacher", "researcher", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """评测结果（ADR-016：读 kb_eval_runs 最新一行）"""
    row = await db.execute(select(KbEvalRun).order_by(KbEvalRun.run_at.desc()).limit(1))
    run = row.scalar_one_or_none()
    if run is None:
        return {"code": 0, "data": None}
    return {
        "code": 0,
        "data": {
            "eval_set": run.eval_set,
            "recall_at_5": float(run.recall_at_5),
            "mrr": float(run.mrr),
            "run_at": run.run_at.isoformat() if run.run_at else None,
            "meta": run.meta or {},
        },
    }
