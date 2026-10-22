"""从教师端题库把人教 A 版学生教材切片导入学生端知识库（端内数据补齐）。

背景：学生端知识库模型（knowledge_docs/chunks）与教师端教材库
（textbook_sources/textbook_sections/textbook_chunks）是两套独立 schema。
教师端已按白名单批次完成人教 A 版 2019 学生教材的切片与入库，而学生端
本地/新部署环境的 chunks 表可能为空，导致 socratic draft 底稿检索、
smart_quiz 母题原型回落、双师课堂教材证据三条链路全部静默降级。

铁律（与 scripts/import_pep_textbooks.py 同一口径）：
1. 只导入 ``STUDENT_TEXTBOOK_BATCH_IDS`` 白名单内的五册学生教材；
   教师用书、其他出版社、课标/教研/培训资料一律不得进入学生证据域。
2. 不搬运教师端 embedding：教师端为 vector(768)，学生端
   ``chunks.embedding`` 是 vector(1024)，维度错配会直接入库失败；
   因此按学生端 EmbeddingProvider 重新计算 1024 维向量。
3. 幂等：同一 batch_id 的 KnowledgeDoc 已存在即跳过，可重复执行。
4. 先完成全部向量生成，再单事务写入，中途失败整体回滚。

用法：
    cd services/api
    python -m scripts.import_teacher_textbook_slices            # 干跑：只打印将导入的册/切片数
    python -m scripts.import_teacher_textbook_slices --apply    # 实跑：重算向量并写入学生端库
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_repo_env() -> None:
    """加载仓库根 .env（不覆盖已存在的环境变量；与 pnpm/uv 之外的裸跑习惯对齐）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    repo_root = Path(__file__).resolve().parents[3]
    load_dotenv(repo_root / ".env", override=False)


_load_repo_env()

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.models.database import async_session_factory  # noqa: E402
from app.models.knowledge_doc import KnowledgeDoc  # noqa: E402
from app.providers.embedding import EmbeddingProvider  # noqa: E402
from scripts.import_pep_textbooks import (  # noqa: E402
    EMBED_BATCH_SIZE,
    STUDENT_TEXTBOOK_BATCH_IDS,
    StudentTextbookBatch,
    TextbookChunk,
    ensure_pep_textbook_knowledge_points,
    persist_student_textbook_batch,
)


@dataclass(frozen=True)
class TeacherSlice:
    """教师端一个切片 + 所属章节上下文（导入学生端所需的最小字段）。"""

    chunk_id: str
    content: str
    section: str
    page_start: int | None
    page_end: int | None
    order: int


@dataclass
class TeacherBook:
    """教师端一册白名单教材的规范化视图。"""

    batch_id: str
    title: str
    grade: str
    volume: str
    publisher: str
    edition: str
    slices: list[TeacherSlice] = field(default_factory=list)


async def load_teacher_books(teacher_url: str) -> list[TeacherBook]:
    """从教师端库读取白名单教材（sources → sections → chunks）。"""
    engine = create_async_engine(teacher_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                (
                    await conn.execute(
                        text(
                            """
                            SELECT s.id, s.batch_id, s.publisher, s.edition,
                                   s.grade, s.volume,
                                   sec.id AS section_id, sec.title AS section_title,
                                   sec.page_start, sec.page_end,
                                   c.id AS chunk_id, c.text, c."order"
                            FROM textbook_sources s
                            JOIN textbook_sections sec ON sec.textbook_id = s.id
                            JOIN textbook_chunks c ON c.section_id = sec.id
                            WHERE s.batch_id = ANY(:batch_ids)
                            ORDER BY s.batch_id, sec."order", c."order"
                            """
                        ),
                        {"batch_ids": list(STUDENT_TEXTBOOK_BATCH_IDS)},
                    )
                )
                .mappings()
                .all()
            )
    finally:
        await engine.dispose()

    books: dict[str, TeacherBook] = {}
    for r in rows:
        book = books.get(r["batch_id"])
        if book is None:
            book = TeacherBook(
                batch_id=r["batch_id"],
                title=f"{r['publisher']}·{r['volume']}",
                grade=r["grade"],
                volume=r["volume"],
                publisher=r["publisher"],
                edition=r["edition"],
            )
            books[r["batch_id"]] = book
        book.slices.append(
            TeacherSlice(
                chunk_id=str(r["chunk_id"]),
                content=r["text"],
                section=r["section_title"],
                page_start=r["page_start"],
                page_end=r["page_end"],
                order=int(r["order"] or 0),
            )
        )
    return [books[b] for b in STUDENT_TEXTBOOK_BATCH_IDS if b in books]


def _short_book_id(batch_id: str) -> str:
    """batch_id → KP code 用的短册号。

    knowledge_points.code 是 varchar(32)：``MATH-PEP-<BOOK>-<10位digest>`` 的
    节级 code 必须 ≤32，故 book_id 不能直接展开完整 batch_id
    （``20260726_A_BIXI1_01`` 展开后节级 code 达 42 字符，入库即截断报错）。
    取批次号第三段（如 ``20260726-A-bixi1-01`` → ``BIXI1``）保证节级 code 25 字符。
    """
    parts = [p for p in batch_id.split("-") if p]
    short = parts[2] if len(parts) >= 3 else batch_id
    return short.upper()


def to_student_batch(book: TeacherBook) -> StudentTextbookBatch:
    """教师端一册 → 学生端导入契约（与 import_pep_textbooks 同构，保证 KP code 口径一致）。"""
    chunks = [
        TextbookChunk(
            content=s.content,
            meta={
                # ensure_pep_textbook_knowledge_points 以此映射章节 KP，必须唯一
                "source_chunk_id": s.chunk_id,
                "batch_id": book.batch_id,
                "section": s.section,
                "page_start": s.page_start,
                "page_end": s.page_end,
                "order": s.order,
                "origin": "teacher_platform",
            },
        )
        for s in book.slices
    ]
    return StudentTextbookBatch(
        batch_id=book.batch_id,
        title=book.title,
        content_type="textbook",
        scope="student",
        document_meta={
            "batch_id": book.batch_id,
            "book_id": _short_book_id(book.batch_id),
            "book_name": book.title,
            "volume": book.volume,
            "grade": book.grade,
            "publisher": book.publisher,
            "edition": book.edition,
            "scope": "student",
            "origin": "teacher_platform",
        },
        chunks=chunks,
    )


async def run(*, apply: bool) -> int:
    teacher_url = os.environ.get("TEACHER_DATABASE_URL", "").strip()
    if not teacher_url:
        print("[FAIL] 缺少 TEACHER_DATABASE_URL（教师端库连接），请在 .env 配置后重试")
        return 2

    books = await load_teacher_books(teacher_url)
    if not books:
        print("[FAIL] 教师端未找到白名单教材批次，检查 TEACHER_DATABASE_URL 指向的库")
        return 2

    print(f"[OK] 教师端白名单命中 {len(books)} 册：")
    total = 0
    for book in books:
        print(f"  - {book.batch_id}  {book.title}  切片 {len(book.slices)}")
        total += len(book.slices)
    print(f"[OK] 合计 {total} 个切片（embedding 将按学生端 1024 维重算，不搬运教师端 768 维）")

    # 干跑：同时报告学生端哪些批次已存在（幂等跳过）
    existing: set[str] = set()
    async with async_session_factory() as db:
        rows = await db.execute(
            select(KnowledgeDoc.meta_["batch_id"].astext.label("batch_id")).where(
                KnowledgeDoc.source_type == "textbook",
                KnowledgeDoc.deleted_at.is_(None),
            )
        )
        existing = {r for r in rows.scalars().all() if r}
    pending = [b for b in books if b.batch_id not in existing]
    for book in books:
        if book.batch_id in existing:
            print(f"  [SKIP] {book.batch_id} 学生端已存在")
    if not pending:
        print("[OK] 学生端已具备全部白名单教材，无需导入")
        return 0
    if not apply:
        print(f"[CHECK] 干跑结束：将导入 {len(pending)} 册；加 --apply 执行写入")
        return 0

    provider = EmbeddingProvider()
    health = await provider.health_check()
    if not health.get("ok"):
        print(f"[FAIL] embedding 不可用，未写入任何教材: {health.get('error', 'unknown error')}")
        return 3

    prepared: list[tuple[StudentTextbookBatch, list[list[float]]]] = []
    for book in pending:
        batch = to_student_batch(book)
        vectors: list[list[float]] = []
        for start in range(0, len(batch.chunks), EMBED_BATCH_SIZE):
            part = batch.chunks[start : start + EMBED_BATCH_SIZE]
            result = await provider.embed([c.content for c in part], text_type="document")
            if len(result) != len(part) or any(len(v) != 1024 for v in result):
                print(f"[FAIL] {book.batch_id} embedding 返回条数或维度异常")
                return 3
            vectors.extend(result)
        prepared.append((batch, vectors))
        print(f"  [EMBED] {book.batch_id} {len(vectors)} 条向量完成")

    try:
        async with async_session_factory() as db:
            section_kp_ids = await ensure_pep_textbook_knowledge_points(
                db, [b for b, _ in prepared]
            )
            for batch, vectors in prepared:
                await persist_student_textbook_batch(
                    db, batch, vectors, section_kp_ids=section_kp_ids, commit=False
                )
                print(f"  [WRITE] {batch.batch_id} {len(batch.chunks)} 个切片")
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - 顶层入口统一报告
        print(f"[FAIL] 写入失败，已整体回滚: {type(exc).__name__}: {exc}")
        return 4

    print(f"[DONE] 导入完成：{len(prepared)} 册 / {sum(len(b.chunks) for b, _ in prepared)} 个切片")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="教师端白名单教材切片 → 学生端知识库")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认干跑）")
    args = parser.parse_args()
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
