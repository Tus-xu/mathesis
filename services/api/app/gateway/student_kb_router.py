"""学生端知识库页面直传链路（/api/v1/kb/*，契约补齐）

背景（2026-10-05 诊断）：
KnowledgeView.vue 页面按「直传文件 + 进度状态机」契约实现，但其调用的
upload / documents / delete 三端点在 /api/v1 能力归位（2026-09-12）时被遗漏，
页面 404 被前端当作「未建库空态」静默吞掉 —— 表现为「知识库无法使用」。
本路由补齐学生端直传入库能力（方案 A：后端补齐、复用既有管线）。

复用纪律（不新建存储、不改既有端点）：
- 解析引擎：files 域 `_resolve_engine` / `_dispatch_parse`
  （pdf=pymupdf / docx·pptx=pandoc / image=rapidocr→spark_vl 兜底 / md·txt=direct）
- 入库纪律：与 v1_compat_router.kb_ingest_text 同款 —— `_split_paragraphs` 约 500 字
  段落切片、`_latex_paired` 公式配对红线（ADR-024）、embedding NULL 拒入（fail-closed）
- 存储：dev 本地落盘（files 域 LOCAL_UPLOAD_ROOT 约定）/ 生产 StorageProvider.put_bytes
- 文件元数据：复用 files 域 File 表（uq_files_user_sha256 去重唯一键天然防重）

状态机（前端 KnowledgeView 5s 轮询已就绪）：
  pending → parsing → ready | failed（失败原因写 meta.error，前端如实展示）

身份纪律：uploader_id 以 JWT sub 为准；客户端传的 user_id 仅作参考存 meta.client_user_id。

超长策略：解析文本超 _INGEST_MAX_CONTENT 时截断并标记 meta.truncated=true（诚实降级，
不整篇拒收——教材级 PDF 文本量远超聊天附件）。

P3 改造（2026-10-10）：
- 图片闸门分流：图片不再因 OCR 文本不足 80 字拒收（题目原图已在 files 域落盘，
  立体几何等图形题 OCR 极易失真，以原图展示为准、文字仅作检索辅助）。OCR ≥80 字
  照常切片/向量化（meta.index_state="full"）；<80 或为空 → 跳过向量化（零 Chunk），
  doc 仍 ready、index_state="partial"、meta.ocr_len 如实记录。非图片类型 80 字闸不变。
- 分类（P3②）：预设 教材/教辅/笔记/试卷/题目/其他，落 meta_.category（无表结构变更，
  存量缺省「其他」）；upload 可选 category（超白名单回落），PATCH /documents/{id} 显式改类，
  GET /documents 支持 category 筛选。
- 管家打通（P3③）：meta_.category 即对话侧数据面契约字段——AI 管家 _butler_lookup
  知识库分支直查 KnowledgeDoc（uploader_id=本人）如实转述 标题/分类/状态。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, UploadFile
from fastapi import File as FastAPIFile
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.files.router import (
    _dispatch_parse,
    _local_file_path,
    _read_file_bytes,
    _resolve_engine,
)
from app.gateway.auth import get_current_user
from app.gateway.kb_router import _latex_paired
from app.gateway.v1_compat_router import _INGEST_MAX_CONTENT, _split_paragraphs
from app.models.chunk import Chunk
from app.models.database import get_db
from app.models.file import File, FileAsset
from app.models.knowledge_doc import KnowledgeDoc
from app.providers.embedding import EmbeddingProvider, resolve_embedding_config
from app.providers.storage import get_storage_for_user

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["v1-compat-kb"])

# 页面承诺 ≤30MB（KnowledgeView.vue MAX_MB），独立于 files 域 20MB 聊天附件上限
_KB_MAX_MB = 30
# 扩展名白名单（对齐页面 ACCEPT：'.pdf,.docx,.pptx,.md,.txt,.png,.jpg,.jpeg'）
_KB_ALLOWED_EXTS = {".pdf", ".docx", ".pptx", ".md", ".txt", ".png", ".jpg", ".jpeg"}
_EXT_TO_FILE_TYPE = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".md": "md",
    ".txt": "txt",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}
# kb_name → source_type 映射（'default' = 不过滤，全部返回）
_QUESTION_KB_NAMES = {"question", "questions", "题库"}

_EMBED_BATCH = 32  # 向量化批大小（对齐 kb_router.docs_import）
_MIN_TEXT_LEN = 80  # 与 _INGEST_MIN_CONTENT 一致：过短不入库（仅非图片类型硬闸）
_KB_MAX_DOCS_PER_USER = 200  # 轻量防滥用护栏
_ORIGIN = "kb-upload"
_PROGRESS_BY_STATUS = {"pending": 10, "parsing": 50, "ready": 100, "failed": 100}

# P3② 分类（2026-10-10）：预设六类，落 knowledge_docs.meta_.category（JSONB，无表结构变更）。
# 存量文档无该键 → 列表读取时缺省「其他」，不需要回填迁移。
KB_CATEGORIES = ("教材", "教辅", "笔记", "试卷", "题目", "其他")
_KB_DEFAULT_CATEGORY = "其他"


def _normalize_category(raw: str | None) -> str:
    """上传侧宽松归一：空白 / 超白名单一律回落「其他」（契约：超白名单回落不报错）。

    PATCH 端点不走这里——显式修改传非法值必须如实报 40001。
    """
    cat = (raw or "").strip()
    return cat if cat in KB_CATEGORIES else _KB_DEFAULT_CATEGORY


def _source_type_from_kb_name(kb_name: str) -> str:
    return "question" if (kb_name or "").strip().lower() in _QUESTION_KB_NAMES else "textbook"


async def _fail(db: AsyncSession, doc: KnowledgeDoc, meta: dict, reason: str) -> None:
    """如实失败：状态置 failed + 原因写 meta.error（拒绝假装成功）。"""
    meta["error"] = reason
    doc.status = "failed"
    doc.meta_ = meta
    await db.commit()
    logger.warning("v1_kb_upload_ingest_failed", doc_id=str(doc.id), reason=reason[:150])


async def _load_asset_text(db: AsyncSession, file_id: uuid.UUID) -> str | None:
    """读 files 域既有解析产物（markdown 优先，text 兜底）；无则 None。"""
    rows = await db.execute(
        select(FileAsset.asset_type, FileAsset.content)
        .where(
            FileAsset.file_id == file_id,
            FileAsset.deleted_at.is_(None),
            FileAsset.asset_type.in_(["markdown", "text"]),
        )
        .order_by(FileAsset.asset_type.desc())  # markdown > text
    )
    assets = rows.all()
    for _t, content in assets:
        if content and content.strip():
            return content
    return None


async def _save_asset(db: AsyncSession, file_obj: File, engine: str, content: str) -> None:
    """解析产物回写 FileAsset（幂等 upsert，对齐 files 域 _run_parse_task 口径），
    让「重新解析」走产物快路径而不必重跑 OCR/转换。"""
    asset_type = "markdown" if engine != "rapidocr" else "text"
    try:
        await db.execute(
            pg_insert(FileAsset.__table__)
            .values(file_id=file_obj.id, asset_type=asset_type, page_no=1, content=content)
            .on_conflict_do_update(
                index_elements=["file_id", "asset_type", "page_no"],
                set_={"content": content, "deleted_at": None},
            )
        )
    except Exception as e:  # 产物回写失败不影响入库主链路
        logger.warning("v1_kb_asset_write_failed", file_id=str(file_obj.id), error=str(e)[:120])


async def _extract_text(doc: KnowledgeDoc, file_obj: File | None, db: AsyncSession) -> tuple[str | None, str]:
    """提取文本：优先既有解析产物（reparse 快路径），否则按引擎现场解析。

    图片特殊：RapidOCR 有效文字不足时升级 spark_vl 云轨（对齐 files 域双轨兜底）。
    """
    if file_obj is not None:
        cached = await _load_asset_text(db, file_obj.id)
        if cached:
            return cached, "asset-reuse"
        engine = _resolve_engine(file_obj.file_type, "auto")
        text = await _dispatch_parse(file_obj, engine, "kb_ingest", config=None)
        if (
            file_obj.file_type == "image"
            and (not text or len(text.strip()) < 20)  # _RAPIDOCR_MIN_TEXT_LEN 同口径
        ):
            cloud_text, _conf = await _dispatch_parse(file_obj, "spark_vl", "kb_ingest", config=None) or (None, None)
            if cloud_text:
                text, engine = cloud_text, "spark_vl"
        if text and text.strip():
            await _save_asset(db, file_obj, engine, text)
        return (text or None), engine

    # ingest-text 来源的旧文档：file_uri 即源文件 id，直接找产物
    raw = (doc.file_uri or "").strip()[:64]
    try:
        fid = uuid.UUID(raw)
    except ValueError:
        return None, "missing-file"
    cached = await _load_asset_text(db, fid)
    return (cached, "asset-reuse") if cached else (None, "missing-file")


async def _run_kb_ingest_task(doc_id: str, file_id: str) -> None:
    """后台：解析 → 切片 → 向量化 → 落库（独立 session，对齐 files 域后台任务口径）。

    纪律：公式配对红线 + embedding NULL 拒入（脏向量比不入库更有害）。
    """
    from app.models.database import background_session_factory

    try:
        async with background_session_factory() as db:
            doc = await db.get(KnowledgeDoc, uuid.UUID(doc_id))
            if doc is None or doc.deleted_at:
                return
            file_obj = await db.get(File, uuid.UUID(file_id)) if file_id else None

            doc.status = "parsing"
            await db.commit()

            meta = dict(doc.meta_ or {})

            # 页数（PDF）：给列表页「N 页」展示用，失败不影响主链路
            if file_obj is not None and file_obj.file_type == "pdf":
                try:
                    import fitz  # PyMuPDF

                    _d = fitz.open(stream=_read_file_bytes(file_obj), filetype="pdf")
                    meta["page_count"] = _d.page_count
                    _d.close()
                except Exception:  # noqa: S110 — 页数为增强信息，静默跳过
                    pass

            text, engine = await _extract_text(doc, file_obj, db)
            meta["parse_engine"] = engine
            text = (text or "").strip()

            # P3① 图片闸门分流（2026-10-10）：图片不再因 OCR 文本不足 80 字拒收。
            # 理由：原图已在 files 域落盘（File 表，file_uri 可回取），题目以原图展示为准
            # （立体几何等图形题 OCR 极易失真），文字仅作检索辅助；拒收等于丢用户资料。
            #   OCR 文本 ≥80 字 → 照常切片/向量化，meta.index_state="full"；
            #   OCR 文本 <80 或为空 → 跳过向量化（零 Chunk 不进 RAG，防碎片噪音），
            #     doc 仍 ready、meta.index_state="partial"、meta.ocr_len 如实记录、error=None；
            #   非图片类型保持 80 字闸与既有失败文案（如实失败）。
            is_image = (file_obj is not None and file_obj.file_type == "image") or (
                doc.meta_ or {}
            ).get("file_type") == "image"
            if len(text) < _MIN_TEXT_LEN:
                if is_image:
                    # 旧切片软删（reparse 幂等）：本轮未重建索引，不得残留旧切片造成
                    # 「partial 却仍可检索」的假状态
                    await db.execute(
                        update(Chunk)
                        .where(Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None))
                        .values(deleted_at=func.now())
                    )
                    meta["index_state"] = "partial"
                    meta["ocr_len"] = len(text)
                    meta["chunk_count"] = 0
                    meta["error"] = None
                    doc.status = "ready"
                    doc.meta_ = meta
                    await db.commit()
                    logger.info(
                        "v1_kb_upload_ingest_partial",
                        doc_id=str(doc.id),
                        ocr_len=len(text),
                        engine=engine,
                    )
                    return
                await _fail(db, doc, meta, f"未解析出有效文本（不足 {_MIN_TEXT_LEN} 字）。扫描版 PDF/图片可能识别失败，可改用文字版重传")
                return

            truncated = False
            if len(text) > _INGEST_MAX_CONTENT:
                text = text[:_INGEST_MAX_CONTENT]
                truncated = True

            if not _latex_paired(text):
                await _fail(db, doc, meta, "解析文本公式配对检查未通过（$/$$ 不配对，ADR-024），已拒入；可删除后改用 Markdown/文字版重传")
                return

            chunks = _split_paragraphs(text)
            if not chunks:
                await _fail(db, doc, meta, "切片后无有效内容")
                return

            embedder = EmbeddingProvider(await resolve_embedding_config(db))
            vectors: list = []
            try:
                for start in range(0, len(chunks), _EMBED_BATCH):
                    vectors.extend(await embedder.embed(chunks[start : start + _EMBED_BATCH]))
            except Exception as e:
                await _fail(db, doc, meta, f"向量化服务不可用，本次未入库（可点「重新解析」稍后重试）：{str(e)[:60]}")
                return
            if len(vectors) != len(chunks) or any(v is None or len(v) == 0 for v in vectors):
                await _fail(db, doc, meta, "向量化结果不完整，本次未入库（可点「重新解析」稍后重试）")
                return

            # 旧切片软删（reparse 幂等）+ 新切片落库
            await db.execute(
                update(Chunk)
                .where(Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None))
                .values(deleted_at=func.now())
            )
            for idx, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                # 切片级出处（tools/corpus_integrity 契约）：chunks.meta->>'source_kind'
                # 必须与文档级声明一致（本路由在建档时固定 TEXTBOOK，见下方 meta_）。
                # 迭代20 门禁修复：此前漏写，造成语料库 ratchet
                # （tests/test_corpus_integrity.py 的 chunks_missing_source_kind==0）回归。
                db.add(
                    Chunk(
                        doc_id=doc.id,
                        content=chunk,
                        embedding=vector,
                        kp_ids=[],
                        chunk_index=idx,
                        meta_={"source_kind": "TEXTBOOK"},
                    )
                )

            meta["chunk_count"] = len(chunks)
            meta["truncated"] = truncated
            meta["index_state"] = "full"
            meta["ocr_len"] = len(text)
            meta["error"] = None
            doc.status = "ready"
            doc.meta_ = meta
            await db.commit()
            logger.info("v1_kb_upload_ingest_ok", doc_id=doc_id, chunks=len(chunks), engine=engine, truncated=truncated)
    except Exception as e:
        logger.error("v1_kb_upload_task_failed", doc_id=doc_id, error=str(e)[:300])
        try:  # 兜底：把异常如实写回文档（独立 session，尽力而为）
            async with background_session_factory() as db:
                doc = await db.get(KnowledgeDoc, uuid.UUID(doc_id))
                if doc is not None:
                    await _fail(db, doc, dict(doc.meta_ or {}), f"解析管线异常：{str(e)[:120]}")
        except Exception:
            pass


# ==================== 端点 ====================


@router.post("/upload")
async def kb_upload(
    background: BackgroundTasks,
    file: UploadFile = FastAPIFile(...),
    kb_name: str = Form("default"),
    category: str = Form("其他"),
    user_id: str = Form(""),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """直传文件入库（页面主入口）。

    流程：校验 → sha256 幂等 → 字节落盘（File 记录）→ KnowledgeDoc(pending) 立即可见
    → 后台解析-切片-向量化 → ready/failed。前端 5s 轮询 GET /documents 看进度。
    category 可选（教材/教辅/笔记/试卷/题目/其他），超白名单回落「其他」不报错。
    """
    filename = (file.filename or "").strip() or "未命名资料"
    ext = Path(filename).suffix.lower()
    if ext not in _KB_ALLOWED_EXTS:
        return {"code": 40001, "message": f"不支持的文件类型「{ext or filename}」：支持 PDF / Word / PPT / Markdown / 图片"}

    data = await file.read()
    if not data:
        return {"code": 40001, "message": "文件内容为空"}
    if len(data) > _KB_MAX_MB * 1024 * 1024:
        return {"code": 40001, "message": f"文件超过 {_KB_MAX_MB}MB 上限"}

    sha = hashlib.sha256(data).hexdigest()
    sub = user["sub"]
    uploader = uuid.UUID(sub)

    total = (
        await db.execute(
            select(func.count())
            .select_from(KnowledgeDoc)
            .where(KnowledgeDoc.uploader_id == uploader, KnowledgeDoc.deleted_at.is_(None))
        )
    ).scalar() or 0
    if total >= _KB_MAX_DOCS_PER_USER:
        return {"code": 40001, "message": f"知识库文档已达 {_KB_MAX_DOCS_PER_USER} 篇上限，请先清理不再需要的资料"}

    # 幂等：同一用户同一 sha 已有文档 → 直接返回（断点续传语义，不产生重复语料）
    existing = (
        await db.execute(
            select(KnowledgeDoc)
            .where(
                KnowledgeDoc.uploader_id == uploader,
                KnowledgeDoc.meta_["sha256"].astext == sha,
                KnowledgeDoc.deleted_at.is_(None),
            )
            .order_by(KnowledgeDoc.created_at.desc())
        )
    ).scalars().first()
    if existing is not None:
        return {"code": 0, "data": {"duplicate": True, "doc_id": str(existing.id), "status": existing.status}}

    mime = (file.content_type or "application/octet-stream")[:100]
    file_type = _EXT_TO_FILE_TYPE[ext]

    # files 域 File 记录（uq_files_user_sha256 去重；解析引擎按 file_type 路由）
    file_obj = (
        await db.execute(
            select(File).where(File.user_id == uploader, File.sha256 == sha, File.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if file_obj is None:
        file_obj = File(
            user_id=uploader,
            filename=filename[:255],
            mime=mime,
            size_bytes=len(data),
            sha256=sha,
            file_type=file_type,
            status="uploaded",
        )
        db.add(file_obj)
        await db.flush()

    # 字节落盘：dev 本地（files 域 LOCAL_UPLOAD_ROOT 约定）；生产对象存储（缺对象才补写）
    if settings.app_env == "development":
        path = _local_file_path(file_obj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        file_obj.storage_uri = f"local:kb-{file_obj.id}"
    else:
        storage = await get_storage_for_user(sub, db)
        key = file_obj.storage_uri if (file_obj.storage_uri and not file_obj.storage_uri.startswith("local:")) else None
        if key is None:
            key = storage.generate_object_key(sub, file_obj.filename)
            storage.put_bytes(key, data, mime)
        else:
            try:
                storage.get_bytes(key)
            except Exception:
                storage.put_bytes(key, data, mime)
        file_obj.storage_uri = key

    doc = KnowledgeDoc(
        title=filename[:255],
        source_type=_source_type_from_kb_name(kb_name),
        file_uri=str(file_obj.id),
        uploader_id=uploader,
        status="pending",
        meta_={
            "origin": _ORIGIN,
            "sha256": sha,
            "chunk_count": 0,
            "client_user_id": (user_id or "")[:64],
            # 端隔离：RAG 的 scope 过滤是 meta->>'scope' 对请求 scope 拆分后的 IN 精确匹配
            # （rag.py:529），必须写**单值** 'student'——写 'student,teacher' 整串将永远不匹配，
            # 文档对检索不可见（2026-10-05 实测修复；teacher/researcher 的请求 scope 拆分后
            # 均包含 'student'，故单值 student 语义 = 学生上传对全端可见）。
            "scope": "student",
            "source_kind": "TEXTBOOK",
            "kb_name": (kb_name or "default")[:32],
            # P3② 分类（无迁移，落 meta JSONB）；file_type 供列表判定图片原图展示、
            # 且作为后台任务闸门分流的兜底依据（File 行缺失时）
            "category": _normalize_category(category),
            "file_type": file_type,
        },
    )
    db.add(doc)
    await db.commit()

    background.add_task(_run_kb_ingest_task, str(doc.id), str(file_obj.id))
    return {"code": 0, "data": {"duplicate": False, "doc_id": str(doc.id), "status": "pending"}}


@router.get("/documents")
async def kb_documents(
    kb_name: str = Query("default"),
    category: str = Query(""),
    q: str = Query("", max_length=100),
    user_id: str = Query(""),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我的知识库文档列表（属主过滤，JWT 为准）。

    kb_name 传 textbook/question 时按 source_type 过滤；'default' 返回全部。
    category 传非空值时按 meta_.category 精确匹配（P3② 分类 chips）；空值不过滤。
    q 只搜标题；404 永不出现——空库就是空列表（旧前端把 404 当空态的歧义在此根治）。
    """
    uploader = uuid.UUID(user["sub"])
    conditions = [KnowledgeDoc.uploader_id == uploader, KnowledgeDoc.deleted_at.is_(None)]
    if (kb_name or "").strip().lower() in _QUESTION_KB_NAMES:
        conditions.append(KnowledgeDoc.source_type == "question")
    elif (kb_name or "").strip().lower() == "textbook":
        conditions.append(KnowledgeDoc.source_type == "textbook")
    if (category or "").strip():
        conditions.append(KnowledgeDoc.meta_["category"].astext == category.strip())
    if q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(KnowledgeDoc.title.ilike(f"%{term}%", escape="\\"))

    total = (
        await db.execute(select(func.count()).select_from(KnowledgeDoc).where(*conditions))
    ).scalar() or 0
    rows = await db.execute(
        select(KnowledgeDoc)
        .where(*conditions)
        .order_by(KnowledgeDoc.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = []
    for d in rows.scalars().all():
        meta = d.meta_ or {}
        status = d.status or "pending"
        items.append(
            {
                "id": str(d.id),
                "title": d.title,
                "status": status,
                "progress": _PROGRESS_BY_STATUS.get(status, 10),
                "page_count": meta.get("page_count"),
                "chunk_count": meta.get("chunk_count") or 0,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "file_uri": d.file_uri,
                "error": meta.get("error"),
                "source_type": d.source_type,
                # P3②④ 增量字段（既有字段不变）：分类 / 原图引用 / 图片闸门状态。
                # file_id = file_uri 存的 files 域 File id，前端 filesApi.contentUrl(file_id)
                # 直接回原图（图片类文档以原图展示为准，文字解析仅作检索辅助）。
                # index_state=partial（图片 OCR 文本不足，已按原图保存，仅辅助检索）；
                # 缺省 category 回落「其他」兼容存量数据。
                "category": meta.get("category") or _KB_DEFAULT_CATEGORY,
                "file_id": d.file_uri,
                "file_type": meta.get("file_type"),
                "ocr_len": meta.get("ocr_len"),
                "index_state": meta.get("index_state"),
                "parse_engine": meta.get("parse_engine"),
            }
        )
    return {"code": 0, "data": {"total": total, "items": items, "offset": offset, "limit": limit}}


@router.get("/documents/{doc_id}/chunks")
async def kb_document_chunks(
    doc_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """文档切片预览（点击资料卡后的详情抽屉用，属主过滤）。

    图片 OCR 稀疏（index_state=partial）或仍在解析时切片为 0——如实返回空列表，
    由前端提示「按原图保存，文字仅辅助检索」，不伪造解析内容。
    """
    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None or doc.deleted_at:
        return {"code": 40400, "message": "文档不存在"}
    if doc.uploader_id != uuid.UUID(user["sub"]):
        return {"code": 40301, "message": "只能查看自己上传的资料"}

    conditions = [Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None)]
    total = (
        await db.execute(select(func.count()).select_from(Chunk).where(*conditions))
    ).scalar() or 0
    rows = await db.execute(
        select(Chunk.id, Chunk.chunk_index, Chunk.content)
        .where(*conditions)
        .order_by(Chunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    meta = doc.meta_ or {}
    items = [
        {
            "id": str(cid),
            "index": idx,
            "content": content,
            "char_count": len(content or ""),
        }
        for cid, idx, content in rows.all()
    ]
    return {
        "code": 0,
        "data": {
            "doc_id": str(doc.id),
            "title": doc.title,
            "status": doc.status,
            "category": meta.get("category") or _KB_DEFAULT_CATEGORY,
            "index_state": meta.get("index_state"),
            "page_count": meta.get("page_count"),
            "chunk_count": total,
            "items": items,
            "offset": offset,
            "limit": limit,
        },
    }


@router.delete("/documents/{doc_id}")
async def kb_document_delete(
    doc_id: uuid.UUID,
    user_id: str = Query(""),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除文档（属主校验 + 软删文档与切片，切片从 RAG 检索中消失）。"""
    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None or doc.deleted_at:
        return {"code": 40400, "message": "文档不存在"}
    if doc.uploader_id != uuid.UUID(user["sub"]):
        return {"code": 40301, "message": "只能删除自己上传的资料"}

    now = func.now()
    await db.execute(update(KnowledgeDoc).where(KnowledgeDoc.id == doc_id).values(deleted_at=now))
    await db.execute(
        update(Chunk).where(Chunk.doc_id == doc_id, Chunk.deleted_at.is_(None)).values(deleted_at=now)
    )
    await db.commit()
    logger.info("v1_kb_doc_deleted", doc_id=str(doc_id), by=user["sub"])
    return {"code": 0, "data": {"deleted": True, "doc_id": str(doc_id)}}


class _KbCategoryPatchBody(BaseModel):
    """PATCH /documents/{doc_id} 请求体：只改分类（P3②）。"""

    category: str = ""


@router.patch("/documents/{doc_id}")
async def kb_document_patch(
    doc_id: uuid.UUID,
    body: _KbCategoryPatchBody,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """修改文档分类（属主校验；落 meta_.category，无表结构变更）。

    错误码沿用本文件既有约定：40400 文档不存在（delete 同款）/ 40301 非属主（delete、
    reparse 同款口径）/ 40001 非法分类（upload 扩展名同款码）。PATCH 是显式修改，
    非法值如实报错（与 upload 的静默回落不同）。
    """
    cat = (body.category or "").strip()
    if cat not in KB_CATEGORIES:
        return {"code": 40001, "message": f"非法分类「{cat or '(空)'}」：可选 {'/'.join(KB_CATEGORIES)}"}

    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None or doc.deleted_at:
        return {"code": 40400, "message": "文档不存在"}
    if doc.uploader_id != uuid.UUID(user["sub"]):
        return {"code": 40301, "message": "只能修改自己上传的资料"}

    meta = dict(doc.meta_ or {})
    meta["category"] = cat
    doc.meta_ = meta  # 整体重赋值，确保 JSONB 变更被 SQLAlchemy 追踪
    await db.commit()
    logger.info("v1_kb_doc_category_patched", doc_id=str(doc_id), category=cat, by=user["sub"])
    return {"code": 0, "data": {"doc_id": str(doc_id), "category": cat}}


@router.post("/documents/{doc_id}/reparse")
async def kb_document_reparse(
    doc_id: uuid.UUID,
    background: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重新解析（后端重跑管线）。

    替代旧契约里「前端 fetch file_uri 重传」——file_uri 是存储引用，浏览器不可达。
    有解析产物时走快路径（不重跑 OCR/转换），只重做切片与向量化。
    """
    doc = await db.get(KnowledgeDoc, doc_id)
    if doc is None or doc.deleted_at:
        return {"code": 40400, "message": "文档不存在"}
    if doc.uploader_id != uuid.UUID(user["sub"]):
        return {"code": 40301, "message": "只能重新解析自己上传的资料"}
    if (doc.status or "") in ("pending", "parsing"):
        return {"code": 40001, "message": "该资料正在解析中，请稍候"}

    raw = (doc.file_uri or "").strip()[:64]
    try:
        fid = uuid.UUID(raw)
    except ValueError:
        return {"code": 40001, "message": "该资料没有可用的原文文件引用，无法重新解析（可删除后重传）"}
    file_obj = await db.get(File, fid)
    if file_obj is None or file_obj.deleted_at:
        return {"code": 40400, "message": "原文文件已不存在，无法重新解析（可删除后重传）"}

    doc.status = "pending"
    meta = dict(doc.meta_ or {})
    meta["error"] = None
    doc.meta_ = meta
    await db.commit()

    background.add_task(_run_kb_ingest_task, str(doc.id), str(file_obj.id))
    return {"code": 0, "data": {"doc_id": str(doc_id), "status": "pending"}}
