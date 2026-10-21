"""教师端教材 meta 回填 CLI（PLAN §11-B3 scripts：meta 回填）。

包装 R12 既有幂等回填（``app/textbook_v2/backfill_metadata.py``）：

1. ``backfill_source_metadata``：source 溯源列（source_kind/batch_id/manifest_sha256/
   provenance）——按 (school_id IS NULL, file_sha256) 匹配磁盘批次；
2. ``backfill_section_chunk_range``：section chunk_start/chunk_end（direct-chunk 定位列）。

批次根经 ``TEXTBOOK_SLICE_ROOTS`` env 解析（PLAN §7.4.1：原 D:\\知识库\\AC切片 已不在线）。
默认 dry-run（只 preflight 扫描，不写库）；``--apply`` 才连库写入。幂等：重复运行逐行相同。

用法（cwd = services/teacher-platform）::

    uv run python scripts/backfill_textbook_meta.py            # dry-run preflight
    uv run python scripts/backfill_textbook_meta.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def run(*, apply: bool) -> int:
    from app.textbook_v2.backfill_metadata import (
        backfill_section_chunk_range,
        backfill_source_metadata,
        iter_batch_dirs,
    )

    dirs = [d for _r, d in iter_batch_dirs()]
    print(f"批次根扫描: {len(dirs)} 个批次目录（TEXTBOOK_SLICE_ROOTS env 解析）")

    if not apply:
        stats = await backfill_source_metadata(None)  # preflight：不写库、不伪造成功
        print("[DRY-RUN] preflight:", json.dumps(stats.summary(), ensure_ascii=False))
        return 0

    from app.db.session import session_factory

    async with session_factory()() as session:
        source_stats = await backfill_source_metadata(session, batch_dirs=dirs)
        print("[APPLIED] source 溯源列:", json.dumps(source_stats.summary(), ensure_ascii=False))
        section_stats = await backfill_section_chunk_range(session)
        print("[APPLIED] section 定位列:", json.dumps(section_stats.summary(), ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="教师端教材 meta 幂等回填（R12 列）")
    parser.add_argument("--apply", action="store_true", help="实际写库（缺省 dry-run preflight）")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
