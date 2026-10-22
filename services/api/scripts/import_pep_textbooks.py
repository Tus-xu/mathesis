"""人教 A 版 2019 高中数学学生教材的本地导入准备。

只接受经过人工确认的五册学生教材切片；教师用书、其他出版社、题库和研究资料
必须走各自的导入批次，不能混进学生课堂的教材证据域。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.database import async_session_factory, init_db
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.providers.embedding import EmbeddingProvider, resolve_embedding_config

STUDENT_TEXTBOOK_BATCH_IDS = (
    "20260726-A-bixi1-01",
    "20260729-A-bixi2-01",
    "20260729-A-xbixi1-01",
    "20260729-A-xbixi2-01",
    "20260729-A-xbixi3-01",
)
# PLAN §7.1：原 D:\知识库\AC切片 已不在线（R4 §1.1 实测），缺省改授权镜像；TEXTBOOK_SLICE_ROOTS
# env 优先（os.pathsep 分隔，与教师端 textbook_v2/image_routes.py 同名同义）。
DEFAULT_PROCESSED_ROOT = Path(r"D:\ppt-rescue-control\20260909-R05\教材切片\AC切片镜像\processed")
EMBED_BATCH_SIZE = 16


def resolve_processed_root(explicit: Path | None = None) -> Path:
    """白名单批次根：显式参数 > TEXTBOOK_SLICE_ROOTS env 首个存在路径 > 镜像缺省。"""
    import os

    if explicit is not None:
        return explicit
    raw = os.environ.get("TEXTBOOK_SLICE_ROOTS", "").strip()
    for part in raw.split(os.pathsep) if raw else []:
        p = Path(part.strip())
        if part.strip() and p.is_dir():
            return p
    return DEFAULT_PROCESSED_ROOT


@dataclass(frozen=True)
class TextbookChunk:
    content: str
    meta: dict[str, str]


@dataclass(frozen=True)
class StudentTextbookBatch:
    batch_id: str
    title: str
    content_type: str
    scope: str
    document_meta: dict[str, str]
    chunks: list[TextbookChunk]


def _section_name(chunk: TextbookChunk) -> str:
    return str(chunk.meta.get("section") or chunk.meta.get("subsection") or "全书导言").strip()


def _book_kp_code(batch: StudentTextbookBatch) -> str:
    return f"MATH-PEP-{batch.document_meta['book_id'].upper()}"


def _section_kp_code(batch: StudentTextbookBatch, section: str) -> str:
    """教材节标题转稳定、可重跑的正式知识点 code（不依赖模型猜测）。"""
    digest = hashlib.sha1(section.encode("utf-8")).hexdigest()[:10].upper()
    return f"{_book_kp_code(batch)}-{digest}"


async def ensure_pep_textbook_knowledge_points(
    db: AsyncSession, batches: list[StudentTextbookBatch]
) -> dict[str, uuid.UUID]:
    """确保教材册、章节节点存在，并返回 source_chunk_id 到章节 KP 的映射。

    节点名称和别名只使用教材 manifest/chunks 的原始标题，避免由 LLM 臆造教学考点。
    """
    codes: list[str] = []
    code_to_payload: dict[str, tuple[str, str | None, list[str]]] = {}
    chunk_code: dict[str, str] = {}
    for batch in batches:
        book_code = _book_kp_code(batch)
        codes.append(book_code)
        code_to_payload[book_code] = (
            batch.title,
            None,
            [batch.document_meta["volume"], batch.document_meta["book_name"]],
        )
        for chunk in batch.chunks:
            section = _section_name(chunk)
            section_code = _section_kp_code(batch, section)
            codes.append(section_code)
            code_to_payload[section_code] = (section, book_code, [section])
            chunk_code[chunk.meta["source_chunk_id"]] = section_code

    rows = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code.in_(set(codes))))
    by_code = {row.code: row for row in rows.scalars().all()}
    for batch in batches:
        book_code = _book_kp_code(batch)
        if book_code not in by_code:
            name, _, aliases = code_to_payload[book_code]
            node = KnowledgePoint(
                code=book_code, name=name, parent_id=None, grade="高中", aliases=aliases
            )
            db.add(node)
            await db.flush()
            by_code[book_code] = node
        for chunk in batch.chunks:
            section = _section_name(chunk)
            section_code = _section_kp_code(batch, section)
            if section_code in by_code:
                continue
            name, parent_code, aliases = code_to_payload[section_code]
            node = KnowledgePoint(
                code=section_code,
                name=name,
                parent_id=by_code[parent_code].id if parent_code else None,
                grade="高中",
                aliases=aliases,
            )
            db.add(node)
            await db.flush()
            by_code[section_code] = node
    return {source_chunk_id: by_code[code].id for source_chunk_id, code in chunk_code.items()}


def discover_student_textbook_batches(root: Path) -> list[StudentTextbookBatch]:
    """从已处理目录中找出白名单内的人教 A 版 2019 学生教材。"""
    return [
        load_student_textbook_batch(root / batch_id)
        for batch_id in STUDENT_TEXTBOOK_BATCH_IDS
        if (root / batch_id).is_dir()
    ]


def load_student_textbook_batch(batch_dir: Path) -> StudentTextbookBatch:
    """读取一个白名单教材批次，并保留课堂展示所需的章节出处。"""
    manifest_path = batch_dir / "manifest.json"
    chunks_path = batch_dir / "chunks.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    batch_id = str(manifest.get("batch_id") or "")
    book_name = str(manifest.get("book_name") or "")
    if batch_id not in STUDENT_TEXTBOOK_BATCH_IDS:
        raise ValueError(f"不是学生教材白名单批次: {batch_id or batch_dir.name}")
    if manifest.get("book_type") != "textbook" or "人教A版2019" not in book_name:
        raise ValueError(f"不是人教 A 版 2019 学生教材: {batch_id}")
    if "教师用书" in book_name:
        raise ValueError(f"教师用书不可进入学生教材域: {batch_id}")

    document_meta = {
        "batch_id": batch_id,
        "book_id": str(manifest["book_id"]),
        "book_name": book_name,
        "volume": str(manifest["volume"]),
        "book_type": "textbook",
        "publisher": "人教A版",
        "edition": "2019",
        "scope": "student",
        "corpus_class": "student_textbook",
    }
    chunks: list[TextbookChunk] = []
    for line_number, line in enumerate(
        chunks_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValueError(f"{batch_id} 第 {line_number} 个切片内容为空")
        chunks.append(
            TextbookChunk(
                content=content,
                meta={
                    "source_chunk_id": str(item.get("chunk_id") or f"{batch_id}-{line_number}"),
                    "book_id": str(item.get("book_id") or document_meta["book_id"]),
                    "book_name": str(item.get("book_name") or book_name),
                    "volume": str(item.get("volume") or document_meta["volume"]),
                    "section": str(item.get("section") or ""),
                    "subsection": str(item.get("subsection") or ""),
                    # 切片级语料标记：qa_rag 等学生 QA 链路按 source_kind 过滤
                    # （TEXTBOOK,CURRICULUM_STANDARD）；缺失会被向量/全文/kp 三路
                    # 整体滤掉，表现为「知识库未找到相关内容」。实测修复（2026-10-22）：
                    # 真实链路 /api/agent/chat → qa_rag 检索为空，根因即缺此字段。
                    "source_kind": "TEXTBOOK",
                    # PLAN §3.3/§7.4.4：溯源标记契约与 import_textbook_slices 一致
                    # （origin=ac_slice / rights=authorized / batch_id），存量行由
                    # scripts/maintain_student_corpus.py backfill-pep-chunk-meta 幂等回填。
                    "origin": "ac_slice",
                    "rights": "authorized",
                    "batch_id": batch_id,
                },
            )
        )

    if not chunks:
        raise ValueError(f"{batch_id} 没有可导入切片")
    return StudentTextbookBatch(
        batch_id=batch_id,
        title=book_name,
        content_type="textbook",
        scope="student",
        document_meta=document_meta,
        chunks=chunks,
    )


async def persist_student_textbook_batch(
    db: AsyncSession,
    batch: StudentTextbookBatch,
    embeddings: list[list[float]],
    *,
    section_kp_ids: dict[str, uuid.UUID] | None = None,
    commit: bool = True,
) -> object:
    """将一册已验证教材及其向量一次性写入当前知识库模型。"""
    if len(embeddings) != len(batch.chunks):
        raise ValueError("embedding 数量与教材切片数量不一致")
    if any(len(vector) != 1024 for vector in embeddings):
        raise ValueError("教材 embedding 维度必须为 1024")
    if section_kp_ids is None:
        section_kp_ids = await ensure_pep_textbook_knowledge_points(db, [batch])

    existing = await db.execute(
        select(KnowledgeDoc).where(
            KnowledgeDoc.meta_["batch_id"].astext == batch.batch_id,
            KnowledgeDoc.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"教材批次已存在: {batch.batch_id}")

    doc = KnowledgeDoc(
        title=batch.title,
        source_type=batch.content_type,
        file_uri=f"local://knowledge-base/{batch.batch_id}/chunks.jsonl",
        status="ready",
        meta_=batch.document_meta,
    )
    db.add(doc)
    await db.flush()
    for index, (source_chunk, vector) in enumerate(zip(batch.chunks, embeddings, strict=True)):
        source_chunk_id = source_chunk.meta["source_chunk_id"]
        section_kp_id = section_kp_ids.get(source_chunk_id)
        if section_kp_id is None:
            raise ValueError(f"教材切片缺少章节知识点映射: {source_chunk_id}")
        db.add(
            Chunk(
                doc_id=doc.id,
                content=source_chunk.content,
                embedding=vector,
                kp_ids=[section_kp_id],
                meta_=source_chunk.meta,
                chunk_index=index,
            )
        )
    if commit:
        await db.commit()
    return doc.id


async def import_student_textbook_batches(
    db: AsyncSession,
    root: Path,
    embedder: EmbeddingProvider,
) -> list[dict[str, int | str]]:
    """先完成所有向量生成，再原子写入全部学生教材批次。"""
    batches = discover_student_textbook_batches(root)
    if not batches:
        raise ValueError("没有可导入的人教 A 版 2019 学生教材批次")

    prepared: list[tuple[StudentTextbookBatch, list[list[float]]]] = []
    for batch in batches:
        vectors: list[list[float]] = []
        for start in range(0, len(batch.chunks), EMBED_BATCH_SIZE):
            part = batch.chunks[start : start + EMBED_BATCH_SIZE]
            result = await embedder.embed([chunk.content for chunk in part], text_type="document")
            if len(result) != len(part) or any(len(vector) != 1024 for vector in result):
                raise ValueError(f"{batch.batch_id} embedding 返回条数或维度异常")
            vectors.extend(result)
        prepared.append((batch, vectors))

    imported: list[dict[str, int | str]] = []
    try:
        section_kp_ids = await ensure_pep_textbook_knowledge_points(db, batches)
        for batch, vectors in prepared:
            await persist_student_textbook_batch(
                db, batch, vectors, section_kp_ids=section_kp_ids, commit=False
            )
            imported.append({"batch_id": batch.batch_id, "chunks": len(batch.chunks)})
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return imported


async def run(root: Path, *, dry_run: bool) -> int:
    """命令行入口：确认嵌入健康后导入已确认的人教 A 版学生教材。"""
    batches = discover_student_textbook_batches(root)
    if len(batches) != len(STUDENT_TEXTBOOK_BATCH_IDS):
        found = {batch.batch_id for batch in batches}
        missing = sorted(set(STUDENT_TEXTBOOK_BATCH_IDS) - found)
        print(f"[FAIL] 缺少教材批次: {', '.join(missing)}")
        return 1
    print(
        f"[OK] 已识别 {len(batches)} 册学生教材，合计 {sum(len(b.chunks) for b in batches)} 个切片"
    )
    if dry_run:
        for batch in batches:
            print(f"  [DRY-RUN] {batch.batch_id}: {batch.title} ({len(batch.chunks)} 切片)")
        return 0

    await init_db()
    async with async_session_factory() as db:
        config = await resolve_embedding_config(db)
        embedder = EmbeddingProvider(config)
        health = await embedder.health_check()
        if not health.get("ok"):
            print(
                f"[FAIL] embedding 不可用，未写入任何教材: {health.get('error', 'unknown error')}"
            )
            return 1
        imported = await import_student_textbook_batches(db, root, embedder)
    print(f"[DONE] 已导入 {len(imported)} 册学生教材: {imported}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="导入人教 A 版 2019 高中数学学生教材")
    parser.add_argument(
        "--root", type=Path, default=None, help="批次根（缺省 TEXTBOOK_SLICE_ROOTS env > 镜像路径）"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅校验教材分类与切片数量，不调用 embedding 或写库"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(resolve_processed_root(args.root), dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
