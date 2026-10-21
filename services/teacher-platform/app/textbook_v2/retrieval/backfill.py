"""R08 顺序回填：textbook_chunks.order = 切片 chunks.jsonl 全局 1-based 行号。

背景（R07 实测教训）：DB chunk 无顺序列，created_at 同事务相等，DB 侧无法恢复切片顺序。
权威顺序 = D:\\知识库\\AC切片\\processed\\<batch>\\chunks.jsonl 的行序（与 R06
build_section_tree / R07 输入流同口径）；切片源不产生 TextbookPage（D-002），
order 即页序锚（与 R07 page_ref 同口径）。

幂等：order 由 (section_id, content_hash) 确定性推导（content_hash = sha256(content)，
与 R06 同一幂等键）；重跑结果逐行相同、不新增行、不覆盖其他列。R06 同 section
完全重复段落被去重 → DB 无对应行，跳过并计数（不影响后续行号）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.importers.slice_importer import build_section_tree, load_chunks, sha256_bytes
from app.textbook_v2.models import TextbookChunk, TextbookSection


@dataclass
class BackfillResult:
    """一次回填的统计（无敏感内容，供报告）。"""

    source_id: Any
    batch_dir: str
    file_rows: int = 0
    updated: int = 0  # order 实际发生变化的行数
    skipped_missing_chunk: int = 0  # R06 同 section 重复内容去重 → 文件行无 DB 行
    skipped_missing_section: int = 0  # 理论不发生（文件章节树与 DB 不一致才出现）
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "source_id": str(self.source_id),
            "batch_dir": self.batch_dir,
            "file_rows": self.file_rows,
            "updated": self.updated,
            "skipped_missing_chunk": self.skipped_missing_chunk,
            "skipped_missing_section": self.skipped_missing_section,
            "issues": self.issues,
        }


async def backfill_chunk_order_from_slice(
    session: AsyncSession,
    *,
    source_id: Any,
    batch_dir: Path,
) -> BackfillResult:
    """对一个 source 幂等回填 order（只写 order 列，不触碰其他列）。"""
    result = BackfillResult(source_id=source_id, batch_dir=str(batch_dir))
    rows = load_chunks(batch_dir)
    result.file_rows = len(rows)
    if not rows:
        result.issues.append("chunks.jsonl 为空，跳过")
        return result

    # 与 R06 staged_import 同函数重建章节树 → 同 order/title/level（顺序权威）
    sections, mapping = build_section_tree(rows)

    db_sections = (
        (await session.execute(select(TextbookSection.id, TextbookSection.order).where(TextbookSection.textbook_id == source_id)))
        .all()
    )
    order_to_section: dict[int, Any] = {order: sec_id for sec_id, order in db_sections}
    if not order_to_section:
        result.issues.append("source 无任何 section（可能未导入），跳过")
        return result

    section_ids = list(order_to_section.values())
    db_chunks = (
        (await session.execute(select(TextbookChunk.id, TextbookChunk.section_id, TextbookChunk.content_hash).where(
            TextbookChunk.section_id.in_(section_ids)
        )))
        .all()
    )
    key_to_chunk: dict[tuple[Any, str], Any] = {
        (sec_id, content_hash): cid for cid, sec_id, content_hash in db_chunks
    }
    existing_order = {
        cid: ord_
        for cid, ord_ in (
            await session.execute(select(TextbookChunk.id, TextbookChunk.order).where(
                TextbookChunk.section_id.in_(section_ids)
            ))
        ).all()
        if ord_ is not None
    }

    updates: list[tuple[Any, int]] = []
    assigned: set[Any] = set()  # 已被更早文件行分配 order 的 DB chunk（R06 去重行不产生新行）
    for i, row in enumerate(rows, start=1):  # 全局 1-based 行号 = order
        section = sections[mapping[i - 1]]
        db_section = order_to_section.get(section.order)
        if db_section is None:
            result.skipped_missing_section += 1
            continue
        content_hash = sha256_bytes((row.get("content") or "").encode("utf-8"))
        chunk_id = key_to_chunk.get((db_section, content_hash))
        if chunk_id is None:
            result.skipped_missing_chunk += 1  # R06 同 section 重复内容去重 → 无独立 DB 行
            continue
        if chunk_id in assigned:
            result.skipped_missing_chunk += 1  # 该行是重复内容，DB 行已被首现行分配
            continue
        assigned.add(chunk_id)
        if existing_order.get(chunk_id) != i:
            updates.append((chunk_id, i))

    for chunk_id, order in updates:
        await session.execute(update(TextbookChunk).where(TextbookChunk.id == chunk_id).values(order=order))
    result.updated = len(updates)
    await session.commit()
    return result
