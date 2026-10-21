"""R12 语料对账：磁盘批次（processed/quarantine）↔ 历史 DB source（P0）。

铁律（任务验收）：不得把磁盘批次数直接等同数据库 source 数。
输出逐批映射（batch_id → manifest/file_sha256 → DB source_id/rights_status）+ UNKNOWN。

模式：
- 有 DB session：按 (school_id IS NULL, file_sha256)（R06 幂等键）匹配 source；
  MATCHED → 携带 db_source_id/rights_status；磁盘有、DB 无 → UNKNOWN(missing-in-db)。
- 无 DB session（安全 preflight）：全部 UNKNOWN(no-db)，不伪造成功。

输出 JSON 形状见 evidence-runs（CORPUS-RECONCILIATION.json 由调用方落盘）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.backfill_metadata import (
    DEFAULT_SLICE_ROOT,
    _read_batch,
    iter_batch_dirs,
)
from app.textbook_v2.models import TextbookSource

#: 兼容别名：旧引用点默认值语义（权威取值请用 slice_roots()；PLAN §7.4.1 改 env）
AC_SLICE_ROOT = DEFAULT_SLICE_ROOT


@dataclass
class BatchRecon:
    """单个批次的对账结果。"""

    batch_dir: str
    batch_id: str
    book_name: str
    volume: str
    book_type: str
    file_sha256: str
    manifest_sha256: str
    status: str  # MATCHED | UNKNOWN
    reason: str
    db_source_id: str | None = None
    db_rights_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_dir": self.batch_dir,
            "batch_id": self.batch_id,
            "book_name": self.book_name,
            "volume": self.volume,
            "book_type": self.book_type,
            "file_sha256": self.file_sha256,
            "manifest_sha256": self.manifest_sha256,
            "status": self.status,
            "reason": self.reason,
            "db_source_id": self.db_source_id,
            "db_rights_status": self.db_rights_status,
        }


async def reconcile_corpus(
    session: AsyncSession | None,
    *,
    root: Path | None = None,
    batch_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """对账 processed/quarantine 批次与 DB source；返回可序列化报告。

    session=None → 安全 preflight：全部批次标记 UNKNOWN(no-db-preflight)。
    root=None / batch_dirs=None 时按 TEXTBOOK_SLICE_ROOTS env 解析（PLAN §7.4.1）。
    """
    if batch_dirs is not None:
        pairs: list[tuple[Path, Path]] = [(batch_dirs[0].parent, d) for d in batch_dirs]
    elif root is not None:
        pairs = [(root, d) for d in iter_batch_dirs([root])]
    else:
        pairs = list(iter_batch_dirs())
    batches: list[BatchRecon] = []

    for r, d in pairs:
        info = _read_batch(d)
        rel = str(d.relative_to(r)) if r in d.parents else d.name
        if info is None:
            batches.append(
                BatchRecon(
                    batch_dir=rel,
                    batch_id=d.name,
                    book_name="",
                    volume="",
                    book_type="",
                    file_sha256="",
                    manifest_sha256="",
                    status="UNKNOWN",
                    reason="missing-manifest-or-chunks",
                )
            )
            continue
        m, file_sha256, m_sha256 = info
        batch = BatchRecon(
            batch_dir=rel,
            batch_id=m.get("batch_id", d.name),
            book_name=m.get("book_name", ""),
            volume=m.get("volume", ""),
            book_type=m.get("book_type", ""),
            file_sha256=file_sha256,
            manifest_sha256=m_sha256,
            status="UNKNOWN",
            reason="no-db-preflight",
        )
        if session is not None:
            source = (
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
            if source is not None:
                batch.status = "MATCHED"
                batch.reason = "matched-by-file_sha256"
                batch.db_source_id = str(source.id)
                batch.db_rights_status = source.rights_status
            else:
                batch.status = "UNKNOWN"
                batch.reason = "missing-in-db"
        batches.append(batch)

    status_counts: dict[str, int] = {}
    for b in batches:
        status_counts[b.status] = status_counts.get(b.status, 0) + 1
    dir_counts = {"processed": 0, "quarantine": 0}
    for b in batches:
        top = Path(b.batch_dir).parts[0] if b.batch_dir else ""
        if top in dir_counts:
            dir_counts[top] += 1

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "batch_dir_root": ";".join(str(r) for r in dict.fromkeys(r for r, _d in pairs)),
        "db_available": session is not None,
        "batches_total": len(batches),
        "summary": {
            **dir_counts,
            **status_counts,
            "db_sources_total": None,
        },
        "batches": [b.to_dict() for b in batches],
    }
    if session is not None:
        report["summary"]["db_sources_total"] = len(
            (await session.execute(select(TextbookSource.id))).scalars().all()
        )
    return report


def reconcile_corpus_json(report: dict[str, Any]) -> str:
    """稳定 JSON 序列化（无敏感内容）。"""
    return json.dumps(report, ensure_ascii=False, indent=2)
