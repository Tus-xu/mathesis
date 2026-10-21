"""教师用书 STAGED/ACTIVE → QUARANTINED（PLAN §3.3 / §7.4.2，B3；tel F2 修复漏网）。

用户红线：教师用书永不入库、已入库者必须 QUARANTINED（PLAN §7.1）。
判别（双规则取并集，tel F2 起不再单靠路径启发式——tbixi1 批次镜像目录在
``processed\\`` 下（CORPUS-RECONCILIATION.json tbixi1 行 MATCHED），按
``source_path LIKE '%quarantine%'`` 漏网，教师用书以 STAGED 泄入新目录端点与
备课检索）：教师用书共 5 批 tbixi1/tbixi2/txbixi1/txbixi2/txbixi3：
1. batch_id 形态：``rights.is_teacher_guide_batch_id``（tbixi/txbixi）；
2. source_path 含 ``quarantine``（既有口径保留）。

铁律：**走 rights.py 状态机函数，不裸 UPDATE**——非法迁移直接抛错；
幂等：已是 QUARANTINED 的行跳过；默认 dry-run 只报告，``--apply`` 才写库。

用法（cwd = services/teacher-platform）::

    uv run python scripts/quarantine_teacher_guides.py            # dry-run
    uv run python scripts/quarantine_teacher_guides.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import session_factory  # noqa: E402
from app.textbook_v2.models import TextbookSource  # noqa: E402
from app.textbook_v2.rights import (  # noqa: E402
    RightsStatus,
    assert_can_transition,
    is_teacher_guide_batch_id,
)

_PATH_MARK = "quarantine"


def _is_target(row: TextbookSource) -> bool:
    """教师用书红线目标 = 教师用书批次 ∪ quarantine 路径行（任一命中即算）。"""
    return is_teacher_guide_batch_id(row.batch_id) or _PATH_MARK in (row.source_path or "")


async def run(*, apply: bool) -> int:
    async with session_factory()() as session:
        # textbook_sources 为小表（数十行）：全量取回内存过滤（batch_id 正则在 SQL 侧
        # 无法直接复用 Python 判别函数，避免两套口径漂移）
        all_rows = (
            (await session.execute(select(TextbookSource))).scalars().all()
        )
        related = [r for r in all_rows if _is_target(r)]
        targets = [r for r in related if r.rights_status != RightsStatus.QUARANTINED.value]
        targets.sort(key=lambda r: (r.batch_id or "", r.source_path or ""))
        print(f"教师用书红线扫描: 全库 {len(all_rows)} 行，命中教师用书/quarantine 路径 "
              f"{len(related)} 行，待隔离 {len(targets)} 行（apply={apply}）")
        changed = 0
        for row in targets:
            # 教师用书白名单外事实：只允许 STAGED/ACTIVE → QUARANTINED（状态机校验，非法即抛）
            assert_can_transition(row.rights_status, RightsStatus.QUARANTINED.value)
            print(f"  -> {row.batch_id or '(batch_id 空)'} {row.volume} "
                  f"{row.rights_status} -> QUARANTINED  (source_path={row.source_path})")
            row.rights_status = RightsStatus.QUARANTINED.value
            changed += 1
        if apply and changed:
            await session.commit()
            print(f"[APPLIED] 已隔离 {changed} 行（rights 状态机校验通过）")
        elif changed:
            print(f"[DRY-RUN] 未写库；加 --apply 执行（将隔离 {changed} 行）")
        else:
            print("[OK] 无待隔离行（幂等：目标状态已达成）")
        skipped = len(related) - len(targets)
        if skipped:
            print(f"[SKIP] 已是 QUARANTINED 的行: {skipped}")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="教师用书 STAGED→QUARANTINED（rights 状态机；批次形态+路径双规则）")
    parser.add_argument("--apply", action="store_true", help="实际写库（缺省 dry-run）")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
