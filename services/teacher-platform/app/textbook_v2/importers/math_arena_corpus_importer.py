"""math_arena 真实语料 → teacher_platform.textbook_v2 幂等注入器（FROZEN-CONTRACT §G）。

背景（不可协商，见 docs/product/teacher-prep-center/FROZEN-CONTRACT.md §G）：
    Docker 卷丢失导致 teacher_platform.textbook_v2 全空；原唯一切片源
    ``D:\\知识库\\AC切片`` 不在线。可用真实语料已从 ``exports/20260914/math_arena.sql.gz``
    恢复到 ``math_arena`` 库：
        - knowledge_docs（5 本人教A版2019 数学教材）
        - chunks（792 条真实教材切片，meta 含 book_id/volume/section 等）
    本模块把这些**真实**行搬运到 textbook_v2 三层表（Source→Section→Chunk）。

与 slice_importer 的关系：
    slice_importer 从磁盘批次目录（manifest.json + chunks.jsonl）导入；本环境该目录不在线，
    只有已恢复的 DB 行。因此复用其**领域模型 / 常量 / 幂等哲学**（source 以内容 hash 为自然键、
    section/chunk get-or-create），但输入改为 math_arena 表，并补齐 §G 要求的
    ``section_path`` / ``chunk_start/chunk_end``（R12 direct-chunk 定位列）。

反幻觉红线（§G.2）：
    1. 只搬运真实数据：``chunks.content`` 原文照搬，**禁止**生成/补全/编造任何教材正文；
    2. 无真实印刷页码 → ``page_start=page_end=0`` 占位并在 ``provenance`` 记录 page_policy，
       **绝不编造页码**；
    3. ``file_sha256`` 由真实内容确定性派生；``source_path`` 用真实 ``knowledge_docs.file_uri``；
       ``source_repo`` 用真实 URI authority；``source_commit`` 无 git 语义，填真实 ``meta.batch_id``
       作为批次版本锚（provenance 标注语义），**不伪造**；
    4. 引用一律走 SourceLocator v1（source/section/chunk），**禁止** page_refs / pNN。

幂等：
    - ``textbook_sources`` 自然键 ``(school_id IS NULL, file_sha256)``；
    - ``textbook_sections`` 自然键 ``(textbook_id, order)``（order 由数据确定性派生）；
    - ``textbook_chunks`` 自然键 ``(section_id, order)``（order = 册内全局 1-based 行号）。
    重复执行不产生重复行。

CLI::

    DATABASE_URL="postgresql+asyncpg://teacher:teacher_dev_pw@127.0.0.1:54330/teacher_platform" \\
    MATH_ARENA_URL="postgresql+asyncpg://teacher:teacher_dev_pw@127.0.0.1:54330/math_arena" \\
    python -m app.textbook_v2.importers.math_arena_corpus_importer [--dry-run] [--book xbixi1]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db.session import session_factory
from app.textbook_v2.importers.slice_importer import CHUNKER_VERSION, RIGHTS_EVIDENCE_REF
from app.textbook_v2.models import TextbookChunk, TextbookSection, TextbookSource

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

#: math_arena 源库默认连接（真实恢复库；可经 MATH_ARENA_URL 覆盖）
DEFAULT_MATH_ARENA_URL = "postgresql+asyncpg://teacher:teacher_dev_pw@127.0.0.1:54330/math_arena"

#: 恢复批次标识（真实：语料于 20260914 从 math_arena.sql.gz 恢复；用于 provenance 审计）
RECOVERY_DUMP_REL = "exports/20260914/math_arena.sql.gz"

#: 本导入器版本（真实标签：本 writer 的版本，非源数据版本）
IMPORTER_VERSION = "math-arena-recovery-20260914"

#: 权利状态：已获队伍授权（§G.2.3 要求 ACTIVE）。证据引用复用 slice_importer 的既有标识。
RIGHTS_STATUS = "ACTIVE"

#: 无真实印刷页码时的占位整数（§G.2.2；不得编造页码）
PLACEHOLDER_PAGE_NO = 0
PAGE_POLICY = "no_printed_page_in_source; page_start=page_end=0 placeholder; use chunk_start/chunk_end (1-based row anchor)"

#: 空 section（meta.section 为空/纯空白）的展示占位标题（结构性占位，非教材正文）
EMPTY_SECTION_TITLE = "（未分节）"

#: 真实来源 authority（取自 knowledge_docs.file_uri 的 scheme+authority：local://knowledge-base）
SOURCE_REPO = "local://knowledge-base"

SUBJECT = "数学"

#: 编号章节前缀（如 3.1.2 / 1.1），用于派生 section_path
_SECTION_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")
#: 「真实小节」编号模式（BE-DATA2）：至少两级点分编号（3.1 / 3.1.2），且编号为独立 token。
#: 刻意排除正文条目「1. 范围」「2. 对称性」——它们是单级数字+句点+空格，非章节。
_REAL_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)(?=\s|$)")
#: markdown 标题前缀（内容里可能出现 "## 3.1.2 …"，section 值本身一般无）
_MD_HEADING_RE = re.compile(r"^#+\s*")
_WS_RE = re.compile(r"\s+")


# ─────────────────────────────────────────────────────────────────────────────
# 数据载体
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RawChunk:
    """math_arena.chunks 的一行（真实原文，原样搬运）。"""

    chunk_index: int
    content: str
    raw_section: str


@dataclass
class BookCorpus:
    """一本书（knowledge_docs 一行）的元数据 + 全量切片。"""

    doc_id: str
    doc_title: str
    file_uri: str | None
    book_id: str
    volume: str
    book_name: str
    publisher: str
    edition: str
    batch_id: str | None
    chunks: list[RawChunk] = field(default_factory=list)


@dataclass(frozen=True)
class ChunkPlan:
    """textbook_chunks 计划行。"""

    order: int  # 册内全局 1-based 行号（权威顺序锚）
    text: str
    content_hash: str


@dataclass
class SectionPlan:
    """textbook_sections 计划行（一个 meta.section 分组）。"""

    order: int  # 册内 1-based 顺序（按首次出现）
    title: str  # section 原文（空 → EMPTY_SECTION_TITLE）
    normalized_title: str
    section_path: str | None  # 编号如 3.1.2；非编号 → None
    raw_section: str
    chunk_start: int
    chunk_end: int
    chunks: list[ChunkPlan] = field(default_factory=list)


@dataclass
class SourcePlan:
    """textbook_sources 计划行 + 其下 section/chunk 计划。"""

    doc_id: str
    book_id: str
    publisher: str
    edition: str
    grade: str
    volume: str
    subject: str
    source_repo: str
    source_commit: str
    source_path: str
    file_sha256: str
    rights_status: str
    rights_evidence_ref: str
    parser_version: str
    batch_id: str | None
    manifest_sha256: str | None
    provenance: dict[str, Any]
    sections: list[SectionPlan]

    @property
    def chunk_count(self) -> int:
        return sum(len(s.chunks) for s in self.sections)


@dataclass
class ImportReport:
    """一次导入的可审计结果。"""

    sources_created: int = 0
    sources_reused: int = 0
    sections_created: int = 0
    sections_reused: int = 0
    sections_updated: int = 0  # 既有 section 定位/展示字段被就地校正
    sections_deleted: int = 0  # 非「真实小节」噪声 section 被淘汰（BE-DATA2）
    chunks_created: int = 0
    chunks_skipped: int = 0
    chunks_reordered: int = 0  # 既有 chunk 的 order 被校正回阅读顺序
    dedup_skipped: int = 0  # 同 section 重复内容跳过
    books: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources_created": self.sources_created,
            "sources_reused": self.sources_reused,
            "sections_created": self.sections_created,
            "sections_reused": self.sections_reused,
            "sections_updated": self.sections_updated,
            "sections_deleted": self.sections_deleted,
            "chunks_created": self.chunks_created,
            "chunks_skipped": self.chunks_skipped,
            "chunks_reordered": self.chunks_reordered,
            "dedup_skipped": self.dedup_skipped,
            "books": self.books,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 纯函数：规范化 / 派生
# ─────────────────────────────────────────────────────────────────────────────


def sha256_hex(data: str | bytes) -> str:
    """utf-8 文本 / 字节的 sha256 十六进制摘要。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_sha256(content: str) -> str:
    """单条 chunk 内容的 sha256（真实内容派生，非编造）。"""
    return sha256_hex(content)


def canonical_book_sha256(chunk_hashes_in_order: list[str]) -> str:
    """由册内有序 chunk 内容 hash 派生稳定指纹（= textbook_sources.file_sha256）。

    规范（确定性、可复现）：
        对册内按 ``order``（1-based）升序的每条 chunk，依次写入
        ``<order>`` + ``0x1f`` + ``<chunk_content_sha256>`` + ``0x1e``，再取整体 sha256。
    该指纹只依赖真实内容与顺序，重复执行恒定；异内容必异指纹，满足 source 幂等键。
    """
    h = hashlib.sha256()
    for order, ch in enumerate(chunk_hashes_in_order, start=1):
        h.update(str(order).encode("ascii"))
        h.update(b"\x1f")
        h.update(ch.encode("ascii"))
        h.update(b"\x1e")
    return h.hexdigest()


def section_number(raw_section: str) -> str | None:
    """从 section 原文派生编号（如 ``3.1.2``）；非编号栏目返回 None（不伪造编号）。"""
    m = _SECTION_NUM_RE.match(raw_section or "")
    return m.group(1) if m else None


def real_section_number(raw_section: str) -> str | None:
    """「真实小节」编号（BE-DATA2）：至少两级点分编号（``3.1``/``3.1.2``），否则 None。

    排除正文条目（``1. 范围``/``2. 对称性`` 等单级数字+句点）与栏目噪声
    （``思考``/``探究``/``练习``/``● ●``/空串）。仅这些编号项作为 textbook_section。
    """
    m = _REAL_SECTION_RE.match(raw_section or "")
    return m.group(1) if m else None


def is_real_section(raw_section: str) -> bool:
    """是否真实小节（见 real_section_number）。"""
    return real_section_number(raw_section) is not None


def normalize_title(raw_section: str) -> str:
    """规范化标题（检索用）：去 markdown 前缀、压缩空白；空则用占位标题。"""
    t = _MD_HEADING_RE.sub("", (raw_section or "").strip())
    t = _WS_RE.sub(" ", t).strip()
    return t or EMPTY_SECTION_TITLE


def display_title(raw_section: str) -> str:
    """展示标题 = section 原文（去首尾空白）；空则用结构性占位标题。"""
    t = (raw_section or "").strip()
    return t or EMPTY_SECTION_TITLE


def grade_for_volume(volume: str) -> str:
    """volume → 学段（NOT NULL 列，无真实值时给保守可溯源值）。

    与 slice_importer._infer_grade 同口径：选择性必修→高二；必修→高一；否则通用。
    """
    v = volume or ""
    if "选择性必修" in v:
        return "高二"
    if "必修" in v:
        return "高一"
    return "通用"


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


# ─────────────────────────────────────────────────────────────────────────────
# 读取 math_arena（真实源）
# ─────────────────────────────────────────────────────────────────────────────

_CORPUS_SQL = text(
    """
    SELECT c.chunk_index::int          AS chunk_index,
           c.content                   AS content,
           c.doc_id::text              AS doc_id,
           d.title::text               AS doc_title,
           d.file_uri::text            AS file_uri,
           d.meta::text                AS doc_meta,
           c.meta->>'section'          AS raw_section
    FROM chunks c
    JOIN knowledge_docs d ON d.id = c.doc_id
    WHERE c.deleted_at IS NULL
      AND d.deleted_at IS NULL
    ORDER BY d.title, c.chunk_index
    """
)


def _parse_json_obj(raw: Any) -> dict[str, Any]:
    """稳健解析 JSONB（可能已是 dict，也可能是 json 字符串）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def load_corpus(source_engine: AsyncEngine, *, book_filter: str | None = None) -> list[BookCorpus]:
    """从 math_arena 读取真实语料，按 doc 聚合为 BookCorpus 列表。"""
    async with source_engine.connect() as conn:
        rows = (await conn.execute(_CORPUS_SQL)).mappings().all()

    books: dict[str, BookCorpus] = {}
    order: list[str] = []
    for r in rows:
        doc_id = _as_text(r["doc_id"])
        if doc_id not in books:
            meta = _parse_json_obj(r["doc_meta"])
            books[doc_id] = BookCorpus(
                doc_id=doc_id,
                doc_title=_as_text(r["doc_title"]),
                file_uri=r["file_uri"] if isinstance(r["file_uri"], str) else None,
                book_id=_as_text(meta.get("book_id")),
                volume=_as_text(meta.get("volume")),
                book_name=_as_text(meta.get("book_name")),
                publisher=_as_text(meta.get("publisher")) or "人教A版",
                edition=_as_text(meta.get("edition")) or "2019",
                batch_id=_as_text(meta.get("batch_id")) or None,
            )
            order.append(doc_id)
        books[doc_id].chunks.append(
            RawChunk(
                chunk_index=int(r["chunk_index"]),
                content=_as_text(r["content"]),
                raw_section=_as_text(r["raw_section"]),
            )
        )

    result = [books[d] for d in order]
    if book_filter:
        result = [b for b in result if b.book_id == book_filter]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 计划构建（纯函数，dry-run 与执行共用）
# ─────────────────────────────────────────────────────────────────────────────


def build_plan(books: list[BookCorpus]) -> list[SourcePlan]:
    """把真实语料编译为确定性写入计划（无 DB 副作用）。

    两条硬规则：

    1. **chunk 的 ``order`` = 册内阅读顺序**（全局 1-based 行号，按源 ``chunk_index`` 升序），
       **不**按 section 分组后再编号——因为同一栏目（思考/探究/练习…）在册内多处穿插出现，
       分组会打乱阅读顺序。
    2. **BE-DATA2 去噪**：只有「真实小节」（``^\\d+(\\.\\d+)+``，如 ``3.1.2``）才生成
       ``textbook_sections``；栏目/正文噪声（``思考``/``探究``/``练习``/``● ●``/空串/
       ``1. 范围`` 等）**不生成章节**，其 chunk 挂到**最近的编号小节**（阅读顺序上前一个），
       从而章节选择卡干净、且**不丢任何 chunk**。

    ``chunk_start/chunk_end`` = 该 section 成员的 order 最小/最大值（R12「覆盖 chunk 区间」）。
    """
    plans: list[SourcePlan] = []
    for book in books:
        # 阅读顺序：按源 chunk_index 升序 → 全局 1-based 行号（权威顺序锚）
        chunks = sorted(book.chunks, key=lambda c: c.chunk_index)
        reading_order: list[tuple[int, RawChunk]] = list(enumerate(chunks, start=1))

        sections: list[SectionPlan] = []
        by_raw: dict[str, SectionPlan] = {}
        leading_noise: list[ChunkPlan] = []  # 首个编号小节之前的噪声 chunk
        sec_seq = 0
        for order, c in reading_order:
            cp = ChunkPlan(order=order, text=c.content, content_hash=content_sha256(c.content))
            if is_real_section(c.raw_section):
                sp = by_raw.get(c.raw_section)
                if sp is None:
                    sec_seq += 1
                    sp = SectionPlan(
                        order=sec_seq,
                        title=display_title(c.raw_section),
                        normalized_title=normalize_title(c.raw_section),
                        section_path=real_section_number(c.raw_section),
                        raw_section=c.raw_section,
                        chunk_start=order,
                        chunk_end=order,
                        chunks=[],
                    )
                    sections.append(sp)
                    by_raw[c.raw_section] = sp
                sp.chunks.append(cp)
            elif sections:
                sections[-1].chunks.append(cp)  # 噪声 chunk 归入「最近的编号小节」
            else:
                leading_noise.append(cp)  # 尚未出现编号小节 → 暂存

        if leading_noise:
            if sections:
                sections[0].chunks.extend(leading_noise)
            else:  # 极端兜底：整册无编号小节 → 归入单一「其他」（不丢内容）
                all_orders = [cp.order for cp in leading_noise]
                sections.append(
                    SectionPlan(
                        order=1,
                        title="其他",
                        normalized_title="其他",
                        section_path=None,
                        raw_section="",
                        chunk_start=min(all_orders),
                        chunk_end=max(all_orders),
                        chunks=leading_noise,
                    )
                )

        # 归一：成员按 order 升序；chunk_start/end 取真实最小/最大
        for sp in sections:
            sp.chunks.sort(key=lambda cp: cp.order)
            orders = [cp.order for cp in sp.chunks]
            sp.chunk_start = min(orders)
            sp.chunk_end = max(orders)

        # 册指纹按阅读顺序（1-based）派生，与写入顺序一致
        ordered_chunk_hashes = [content_sha256(c.content) for _, c in reading_order]
        file_sha256 = canonical_book_sha256(ordered_chunk_hashes)
        source_path = book.file_uri or f"math_arena://knowledge_docs/{book.doc_id}"
        provenance: dict[str, Any] = {
            "origin": "math_arena.public.knowledge_docs",
            "origin_table": "math_arena.public.chunks",
            "doc_id": book.doc_id,
            "doc_title": book.doc_title,
            "book_id": book.book_id,
            "file_uri": book.file_uri,
            "batch_id": book.batch_id,
            "recovered_from": RECOVERY_DUMP_REL,
            "importer_version": IMPORTER_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "content_sha256_canonical": file_sha256,
            "content_canonicalization": (
                "sha256 over (order \x1f chunk_content_sha256 \x1e)* in ascending order"
            ),
            "source_commit_semantics": "meta.batch_id (batch version anchor; NOT a git commit)",
            "page_policy": PAGE_POLICY,
            "section_grouping": (
                "BE-DATA2: only real sections (^\\d+(\\.\\d+)+) become textbook_sections; "
                "noise/columns (思考/探究/练习/● ●/empty/1. 范围) attach to nearest preceding section"
            ),
        }
        plans.append(
            SourcePlan(
                doc_id=book.doc_id,
                book_id=book.book_id,
                publisher=book.publisher,
                edition=book.edition,
                grade=grade_for_volume(book.volume),
                volume=book.volume,
                subject=SUBJECT,
                source_repo=SOURCE_REPO,
                source_commit=book.batch_id or "n/a",
                source_path=source_path,
                file_sha256=file_sha256,
                rights_status=RIGHTS_STATUS,
                rights_evidence_ref=RIGHTS_EVIDENCE_REF,
                parser_version=IMPORTER_VERSION,
                batch_id=book.batch_id,
                manifest_sha256=None,  # 无真实 manifest 文件 → 如实留空
                provenance=provenance,
                sections=sections,
            )
        )
    return plans


# ─────────────────────────────────────────────────────────────────────────────
# DB 写入（get-or-create，幂等）
# ─────────────────────────────────────────────────────────────────────────────


async def _get_or_create_source(session: AsyncSession, plan: SourcePlan) -> tuple[TextbookSource, bool]:
    """按 (school_id IS NULL, file_sha256) 取或建 source。返回 (source, created)。"""
    existing = (
        (
            await session.execute(
                select(TextbookSource).where(
                    TextbookSource.school_id.is_(None),
                    TextbookSource.file_sha256 == plan.file_sha256,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing, False

    source = TextbookSource(
        school_id=None,  # 平台级种子：对所有租户可见
        publisher=plan.publisher,
        edition=plan.edition,
        grade=plan.grade,
        volume=plan.volume,
        subject=plan.subject,
        source_repo=plan.source_repo,
        source_commit=plan.source_commit,
        source_path=plan.source_path,
        file_sha256=plan.file_sha256,
        rights_status=plan.rights_status,
        rights_evidence_ref=plan.rights_evidence_ref,
        parser_version=plan.parser_version,
        source_kind="direct-chunk",
        batch_id=plan.batch_id,
        manifest_sha256=plan.manifest_sha256,
        provenance=plan.provenance,
    )
    session.add(source)
    await session.flush()
    return source, True


async def import_corpus(session: AsyncSession, plans: list[SourcePlan]) -> ImportReport:
    """把计划幂等写入 textbook_v2（get-or-create；重复执行零新增行）。"""
    from app.textbook_v2.legacy_guards import guard_textbook_page_read_only

    report = ImportReport()
    for plan in plans:
        source, created = await _get_or_create_source(session, plan)
        report.sources_created += int(created)
        report.sources_reused += int(not created)

        # 既有 section → 稳定索引（按 section_path）。BE-DATA2 去噪：非「真实小节」
        # （无 section_path，或已不在计划内）视为栏目/正文噪声 → 删除；其 chunk 由 DB 级联
        # 删除后，会在下方按内容重新挂到正确的编号小节，**零内容丢失**。
        existing_rows = (
            (await session.execute(select(TextbookSection).where(TextbookSection.textbook_id == source.id)))
            .scalars()
            .all()
        )
        planned_paths = {sp.section_path for sp in plan.sections if sp.section_path}
        existing_by_path: dict[str, TextbookSection] = {}
        original_order: dict[str, int] = {}
        to_delete: list[TextbookSection] = []
        for s in existing_rows:
            # 命中「真实小节」计划路径**且唯一** → 复用（保留其 id，最小化改动）；
            # 否则（栏目/正文噪声，或同路径重复行）→ 删除。
            if (
                s.section_path
                and s.section_path in planned_paths
                and s.section_path not in existing_by_path
            ):
                existing_by_path[s.section_path] = s
                original_order[s.section_path] = s.order
            else:
                to_delete.append(s)

        book_row: dict[str, Any] = {
            "book_id": plan.book_id,
            "volume": plan.volume,
            "doc_id": plan.doc_id,
            "source_id": str(source.id),
            "file_sha256": plan.file_sha256,
            "sections": len(plan.sections),
            "chunks": plan.chunk_count,
            "sections_created": 0,
            "sections_reused": 0,
            "sections_updated": 0,
            "sections_deleted": len(to_delete),
            "chunks_created": 0,
            "chunks_skipped": 0,
            "chunks_reordered": 0,
        }

        # 阶段 A：先删除噪声/重复 section 并**单独 flush**，确保 DB 真正释放其
        # (textbook_id, order)。否则 SQLAlchemy 可能把重编号 UPDATE 排在 DELETE 之前，
        # 触发 uq_tbsection_textbook_order 瞬时冲突（BE-DATA2 修复的根因）。
        if to_delete:
            for s in to_delete:
                await session.delete(s)  # AsyncSession.delete 为协程（SQLAlchemy 2.0）→ 必须 await
            await session.flush()
            report.sections_deleted += len(to_delete)

        # 阶段 B：若复用 section 的 order 与计划不一致（含互换），先整体移入负数区间腾空唯一键，
        # 再于阶段 C 落回正序；无变化时跳过，保持重跑零改动（幂等）。
        need_renumber = any(
            existing_by_path[sp.section_path].order != sp.order
            for sp in plan.sections
            if sp.section_path and sp.section_path in existing_by_path
        )
        if need_renumber:
            for i, s in enumerate(existing_by_path.values(), start=1):
                s.order = -i
            await session.flush()

        plan_secs: list[tuple[SectionPlan, TextbookSection]] = []
        for sp in plan.sections:
            sec = existing_by_path.get(sp.section_path) if sp.section_path else None
            if sec is None:
                sec = TextbookSection(
                    textbook_id=source.id,
                    parent_id=None,  # 扁平 level=2 列表（无真实章节树）
                    level=2,
                    order=sp.order,
                    title=sp.title,
                    normalized_title=sp.normalized_title,
                    page_start=PLACEHOLDER_PAGE_NO,  # 无真实页码 → 占位 0（见 provenance.page_policy）
                    page_end=PLACEHOLDER_PAGE_NO,
                    chunk_start=sp.chunk_start,  # R12：权威定位列 = 册内 1-based chunk 行号区间
                    chunk_end=sp.chunk_end,
                    section_path=sp.section_path,
                    intro_strategy=None,
                    derivation_chain=None,
                    summary=None,
                )
                session.add(sec)
                if sp.section_path:
                    existing_by_path[sp.section_path] = sec
                book_row["sections_created"] += 1
                report.sections_created += 1
            else:
                # 自愈：既有 section 的定位/展示字段与计划不一致 → 就地校正（收敛到权威计划）
                changed = (
                    sec.title != sp.title
                    or sec.normalized_title != sp.normalized_title
                    or sec.section_path != sp.section_path
                    or sec.chunk_start != sp.chunk_start
                    or sec.chunk_end != sp.chunk_end
                    or sec.page_start != PLACEHOLDER_PAGE_NO
                    or sec.page_end != PLACEHOLDER_PAGE_NO
                    or sec.level != 2
                    or original_order.get(sp.section_path) != sp.order
                )
                sec.title = sp.title
                sec.normalized_title = sp.normalized_title
                sec.section_path = sp.section_path
                sec.chunk_start = sp.chunk_start
                sec.chunk_end = sp.chunk_end
                sec.page_start = PLACEHOLDER_PAGE_NO
                sec.page_end = PLACEHOLDER_PAGE_NO
                sec.level = 2
                sec.order = sp.order
                if changed:
                    book_row["sections_updated"] += 1
                    report.sections_updated += 1
                book_row["sections_reused"] += 1
                report.sections_reused += 1
            plan_secs.append((sp, sec))

        await session.flush()  # 新建 section 取得 id；删除的噪声 section 及其 chunk 已落库

        # 既有 chunk 以 (section_id, content_hash) 为**身份键**（= DB 唯一约束
        # uq_tbchunk_section_hash）；顺序瑕疵（order 漂移）由自愈逻辑校正。
        existing_chunks = {
            (str(c.section_id), c.content_hash): c
            for c in (
                (
                    await session.execute(
                        select(TextbookChunk)
                        .join(TextbookSection, TextbookSection.id == TextbookChunk.section_id)
                        .where(TextbookSection.textbook_id == source.id)
                    )
                )
                .scalars()
                .all()
            )
        }

        seen_hashes: set[tuple[str, str]] = set()
        for sp, sec in plan_secs:
            for cp in sp.chunks:
                hkey = (str(sec.id), cp.content_hash)
                if hkey in seen_hashes:  # 本次运行内同 section 重复内容 → 跳过
                    report.dedup_skipped += 1
                    continue
                seen_hashes.add(hkey)

                row = existing_chunks.get(hkey)
                if row is not None:
                    # 已存在同一内容 → 幂等；若 order 漂移则校正到权威阅读顺序
                    if row.order != cp.order:
                        row.order = cp.order
                        book_row["chunks_reordered"] += 1
                        report.chunks_reordered += 1
                    book_row["chunks_skipped"] += 1
                    report.chunks_skipped += 1
                    continue

                chunk = TextbookChunk(
                    section_id=sec.id,
                    text=cp.text,  # 真实原文，原样搬运
                    order=cp.order,  # 册内全局 1-based 阅读顺序
                    page_range=None,  # 无真实页码信息 → 留空（不编造）
                    unit_ids=None,
                    embedding=None,
                    chunker_version=CHUNKER_VERSION,
                    content_hash=cp.content_hash,
                    index_version=0,
                )
                session.add(chunk)
                existing_chunks[hkey] = chunk
                book_row["chunks_created"] += 1
                report.chunks_created += 1

        report.books.append(book_row)

    guard_textbook_page_read_only(session)  # R12：dormant 表零新写入守卫
    await session.commit()
    return report


# ─────────────────────────────────────────────────────────────────────────────
# 计划摘要（dry-run 输出）
# ─────────────────────────────────────────────────────────────────────────────


def plan_summary(plans: list[SourcePlan]) -> dict[str, Any]:
    """把计划汇总为可打印摘要（dry-run 用）。"""
    return {
        "sources": len(plans),
        "sections": sum(len(p.sections) for p in plans),
        "chunks": sum(p.chunk_count for p in plans),
        "books": [
            {
                "book_id": p.book_id,
                "volume": p.volume,
                "doc_id": p.doc_id,
                "publisher": p.publisher,
                "edition": p.edition,
                "grade": p.grade,
                "file_sha256": p.file_sha256,
                "source_path": p.source_path,
                "sections": len(p.sections),
                "chunks": p.chunk_count,
                "has_target_3_1_2": any(
                    (s.section_path or "").startswith("3.1.2") for s in p.sections
                ),
            }
            for p in plans
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.textbook_v2.importers.math_arena_corpus_importer",
        description="math_arena 真实语料 → textbook_v2 幂等注入（FROZEN-CONTRACT §G）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只读源库并打印计划，不写目标库")
    parser.add_argument("--book", default=None, help="只处理指定 book_id（如 xbix1/xbixi1）")
    parser.add_argument("--math-arena-url", default=None, help="覆盖 math_arena 连接串")
    return parser


async def _run(args: argparse.Namespace) -> int:
    math_arena_url = args.math_arena_url or os.environ.get("MATH_ARENA_URL") or DEFAULT_MATH_ARENA_URL

    source_engine = create_async_engine(math_arena_url, pool_pre_ping=True)
    try:
        books = await load_corpus(source_engine, book_filter=args.book)
    finally:
        await source_engine.dispose()

    if not books:
        print(json.dumps({"error": "math_arena 无匹配语料", "book": args.book}, ensure_ascii=False))
        return 2

    plans = build_plan(books)
    summary = plan_summary(plans)

    if args.dry_run:
        payload = {"mode": "dry-run", "target": "teacher_platform.textbook_v2", **summary}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    async with session_factory()() as session:
        report = await import_corpus(session, plans)

    payload = {"mode": "apply", "target": "teacher_platform.textbook_v2", "plan": summary, "report": report.as_dict()}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口（同步包装 asyncio.run）。"""
    args = _build_arg_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
