"""R12 元数据回填：direct-chunk 定位列 + Source 溯源列的 additive 幂等回填（P0）。

来源权威：授权教材切片镜像（processed/quarantine 批次目录，切片不可变镜像）。
批次根经环境变量 ``TEXTBOOK_SLICE_ROOTS``（os.pathsep 分隔，可多根）配置，
缺省为本机镜像根；原 ``D:\\知识库\\AC切片`` 已不在线（R4 §1.1 实测）——
PLAN §7.1：代码中所有写死引用一律改 env。

三类回填（全部只写 R12 新列，不触碰其他列）：
1. source 溯源列：source_kind='direct-chunk'、batch_id、manifest_sha256、provenance
   ——按 (school_id IS NULL, file_sha256) 匹配磁盘批次（与 R06 幂等键一致）；
2. section chunk_start/chunk_end：与 page_start/page_end 同值（切片源顺序锚）；
3. problem/unit chunk_ref：把 page_ref/page_from 行号映射到同 section 的
   chunk.order → 生成 canonical v1 locator ref（SourceLocator）。

幂等：先查后写，目标值相同则跳过；重复运行结果逐行相同、不新增行。
无 DB/权限：session=None 只产出 preflight 计划（dry-run），不写库、不伪造成功。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.importers.slice_importer import (
    manifest_sha256 as _manifest_sha256,
)
from app.textbook_v2.locator import chunk_locator
from app.textbook_v2.models import TextbookChunk, TextbookContentUnit, TextbookProblem, TextbookSection, TextbookSource

# 磁盘批次根（R06/R08/R12 共用权威；缺失/不可访问 → preflight 不伪造成功）。
# PLAN §7.1/§7.4.1：原 D:\知识库\AC切片 已不在线，缺省改为授权镜像根；
# 优先读 TEXTBOOK_SLICE_ROOTS env（os.pathsep 分隔，先例 image_routes.py）。
DEFAULT_SLICE_ROOT = Path(r"D:\ppt-rescue-control\20260909-R05\教材切片\AC切片镜像")
_BATCH_ROOTS = ("processed", "quarantine")

#: 兼容别名（旧代码引用 AC_SLICE_ROOT 的默认值语义；权威取值请用 slice_roots()）
AC_SLICE_ROOT = DEFAULT_SLICE_ROOT


def slice_roots() -> list[Path]:
    """解析切片批次根列表：TEXTBOOK_SLICE_ROOTS env 优先（os.pathsep 分隔），缺省镜像根。"""
    raw = os.environ.get("TEXTBOOK_SLICE_ROOTS", "").strip()
    if raw:
        return [Path(p.strip()) for p in raw.split(os.pathsep) if p.strip()]
    return [DEFAULT_SLICE_ROOT]


def iter_batch_dirs(roots: list[Path] | None = None) -> list[tuple[Path, Path]]:
    """扫描各根下 processed/quarantine 批次目录 → [(所属根, 批次目录)]（无目录 → 空列表，不抛）。

    兼容两种根形态：镜像根（含 processed/quarantine 子目录）与直接指向
    processed 容器的根（子目录含 manifest.json 即视为批次）。
    """
    if roots is None:
        roots = slice_roots()
    out: list[tuple[Path, Path]] = []
    for root in roots:
        if not root.is_dir():
            continue
        present = [sub for sub in _BATCH_ROOTS if (root / sub).is_dir()]
        if present:
            for sub in present:
                out.extend((root, p) for p in sorted((root / sub).iterdir()) if p.is_dir())
        else:  # 根本身即 processed 容器
            out.extend(
                (root, p)
                for p in sorted(root.iterdir())
                if p.is_dir() and (p / "manifest.json").is_file()
            )
    return out


def _iter_batch_dirs(root: Path) -> list[Path]:
    """旧签名兼容包装：单根 → 批次目录列表。"""
    return [d for _r, d in iter_batch_dirs([root])]


@dataclass
class BackfillStats:
    """一次回填的统计（无敏感内容）。"""

    source_updates: int = 0
    source_skipped: int = 0
    section_updates: int = 0
    section_skipped: int = 0
    ref_updates: int = 0
    ref_skipped: int = 0
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "source_updates": self.source_updates,
            "source_skipped": self.source_skipped,
            "section_updates": self.section_updates,
            "section_skipped": self.section_skipped,
            "ref_updates": self.ref_updates,
            "ref_skipped": self.ref_skipped,
            "issues": self.issues,
        }


def _guard_no_page_writes(session: AsyncSession) -> None:
    """R12：dormant 表零新写入守卫（提交前强制）。"""
    from app.textbook_v2.legacy_guards import guard_textbook_page_read_only

    guard_textbook_page_read_only(session)


def _iter_batch_dirs(root: Path) -> list[Path]:
    """扫描 processed/quarantine 下的批次目录（无目录 → 空列表，不抛）。"""
    if not root.is_dir():
        return []
    out: list[Path] = []
    for sub in _BATCH_ROOTS:
        d = root / sub
        if d.is_dir():
            out.extend(sorted(p for p in d.iterdir() if p.is_dir()))
    return out


def _read_batch(batch_dir: Path) -> tuple[dict[str, Any], str, str] | None:
    """读取批次 manifest + 计算 file_sha256/manifest_sha256；缺文件 → None。"""
    manifest_path = batch_dir / "manifest.json"
    chunks_path = batch_dir / "chunks.jsonl"
    if not manifest_path.is_file() or not chunks_path.is_file():
        return None
    import hashlib
    import json

    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    file_sha256 = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    return m, file_sha256, _manifest_sha256(m)


async def _source_by_sha(session: AsyncSession, file_sha256: str) -> TextbookSource | None:
    return (
        (
            await session.execute(
                select(TextbookSource).where(
                    TextbookSource.school_id.is_(None),
                    TextbookSource.file_sha256 == file_sha256,
                )
            )
        )
        .scalars()
        .first()
    )


async def backfill_source_metadata(
    session: AsyncSession | None,
    *,
    root: Path | None = None,
    batch_dirs: list[Path] | None = None,
) -> BackfillStats:
    """对存量 source 回填溯源列（source_kind/batch_id/manifest_sha256/provenance）。

    session=None → 只做磁盘侧 preflight 扫描（无 DB 写入），统计跳过项标注 SKIP-DRYRUN。
    幂等：目标列已与批次一致 → skipped；不一致 → 仅写 R12 新列。
    root=None 时按 TEXTBOOK_SLICE_ROOTS env 解析（PLAN §7.4.1）。
    """
    stats = BackfillStats()
    if batch_dirs is not None:
        dirs: list[Path] = batch_dirs
    elif root is not None:
        dirs = _iter_batch_dirs(root)
    else:
        dirs = [d for _r, d in iter_batch_dirs()]
    if session is None:
        for d in dirs:
            info = _read_batch(d)
            if info is None:
                stats.issues.append(f"preflight: 批次缺 manifest/chunks —— {d.name}")
                continue
            m, file_sha256, m_sha256 = info
            stats.source_skipped += 1  # dry-run：不写库，计入 skipped 并标注
        stats.issues.append(f"preflight: 无 DB session，仅扫描 {len(dirs)} 个批次，未写入任何行")
        return stats

    for d in dirs:
        info = _read_batch(d)
        if info is None:
            stats.issues.append(f"批次缺 manifest/chunks —— {d.name}")
            continue
        m, file_sha256, m_sha256 = info
        source = await _source_by_sha(session, file_sha256)
        if source is None:
            stats.issues.append(f"磁盘批次无对应 DB source（file_sha256 未命中）—— {m.get('batch_id', d.name)}")
            stats.source_skipped += 1
            continue
        batch_id = m.get("batch_id")
        desired = {
            "source_kind": "direct-chunk",
            "batch_id": batch_id,
            "manifest_sha256": m_sha256,
        }
        changed = False
        for col, val in desired.items():
            if getattr(source, col) != val:
                setattr(source, col, val)
                changed = True
        if source.source_kind == "direct-chunk" and source.provenance is None:
            source.provenance = {
                "source_repo": source.source_repo,
                "source_commit": source.source_commit,
                "source_path": source.source_path,
                "parser_version": source.parser_version,
            }
            changed = True
        if changed:
            stats.source_updates += 1
        else:
            stats.source_skipped += 1
    if dirs:
        _guard_no_page_writes(session)
        await session.commit()
    return stats


async def backfill_section_chunk_range(
    session: AsyncSession | None,
    *,
    source_ids: list[Any] | None = None,
) -> BackfillStats:
    """section chunk_start/chunk_end：与 page_start/page_end 同值（切片源顺序锚）。

    幂等：目标值相同则跳过；只写 chunk_start/chunk_end 两列。
    """
    stats = BackfillStats()
    if session is None:
        stats.source_skipped = 0
        stats.issues.append("preflight: 无 DB session，未写入任何行")
        return stats

    q = select(TextbookSection)
    if source_ids:
        q = q.where(TextbookSection.textbook_id.in_(source_ids))
    sections = list((await session.execute(q)).scalars().all())
    for sec in sections:
        if sec.chunk_start == sec.page_start and sec.chunk_end == sec.page_end:
            stats.section_skipped += 1
            continue
        sec.chunk_start = sec.page_start
        sec.chunk_end = sec.page_end
        stats.section_updates += 1
    if sections:
        _guard_no_page_writes(session)
        await session.commit()
    return stats


async def backfill_chunk_refs(
    session: AsyncSession | None,
    *,
    source_ids: list[Any] | None = None,
) -> BackfillStats:
    """problem/unit chunk_ref：page_ref/page_from（chunk 行号）→ canonical v1 locator ref。

    映射规则：行号 == 同 section 内 chunk.order（全局 1-based 行号）；未命中保持
    chunk_ref=None 并计数 skipped（R06 同 section 重复内容去重 → 无独立行，合法）。
    """
    stats = BackfillStats()
    if session is None:
        stats.issues.append("preflight: 无 DB session，未写入任何行")
        return stats

    q = select(TextbookSection.id, TextbookSection.textbook_id)
    if source_ids:
        q = q.where(TextbookSection.textbook_id.in_(source_ids))
    rows = list((await session.execute(q)).all())
    if not rows:
        stats.issues.append("无目标 section")
        return stats
    section_ids = [r[0] for r in rows]
    source_of = {r[0]: r[1] for r in rows}

    chunks = (
        (
            await session.execute(
                select(TextbookChunk.id, TextbookChunk.section_id, TextbookChunk.order).where(
                    TextbookChunk.section_id.in_(section_ids)
                )
            )
        )
        .all()
    )
    chunk_by_section_order: dict[tuple[Any, int], Any] = {
        (sec_id, order): cid for cid, sec_id, order in chunks if order is not None
    }

    def _ref(source_id: Any, section_id: Any, order: int) -> str | None:
        chunk_id = chunk_by_section_order.get((section_id, order))
        if chunk_id is None:
            return None
        return chunk_locator(source_id, section_id, chunk_id).to_ref()

    problems = (
        (await session.execute(select(TextbookProblem).where(TextbookProblem.section_id.in_(section_ids))))
        .scalars()
        .all()
    )
    for p in problems:
        ref = _ref(source_of[p.section_id], p.section_id, p.page_ref)
        if ref is None:
            stats.ref_skipped += 1
            continue
        if p.chunk_ref == ref:
            stats.ref_skipped += 1
            continue
        p.chunk_ref = ref
        stats.ref_updates += 1

    units = (
        (await session.execute(select(TextbookContentUnit).where(TextbookContentUnit.section_id.in_(section_ids))))
        .scalars()
        .all()
    )
    for u in units:
        ref = _ref(source_of[u.section_id], u.section_id, u.page_from)
        if ref is None:
            stats.ref_skipped += 1
            continue
        if u.chunk_ref == ref:
            stats.ref_skipped += 1
            continue
        u.chunk_ref = ref
        stats.ref_updates += 1

    if problems or units:
        _guard_no_page_writes(session)
        await session.commit()
    return stats
