"""学生库（math_arena :8000）语料维护脚本（PLAN §3.3 / §7.4，B3）。

三个幂等数据动作（默认 dry-run，``--apply`` 才写库；物理数据一律保留）：

1. ``backfill-pep-chunk-meta``
   5 册白名单 PEP 学生教材 chunk 的 meta 补 ``origin/rights/batch_id`` 溯源标记
   （PLAN §3.3；标记契约对齐 ``scripts/import_textbook_slices.py`` 的
   ``origin=ac_slice / rights=authorized / batch_id``）。book_id 词表与
   ``import_pep_textbooks.STUDENT_TEXTBOOK_BATCH_IDS`` 同源。
2. ``soft-delete-test-pngs``
   测试 PNG 垃圾文档软删（R4 §3.3 实测 4 张：tmp_kb_question.png / kb-test-image.png /
   01_dialog_initial.png / 屏幕截图…；软删 = 置 deleted_at，行与 chunks 物理保留）。
3. ``clean-kp-prerequisites``
   ``kp_prerequisites`` 悬空边清理（R4 §3.3：9 条种子边引用 HS-01/DR-01 风格码，
   ``knowledge_points`` 一个都不存在；补边另立项）。

用法（cwd = services/api）::

    uv run python -m scripts.maintain_student_corpus backfill-pep-chunk-meta [--apply]
    uv run python -m scripts.maintain_student_corpus soft-delete-test-pngs [--apply]
    uv run python -m scripts.maintain_student_corpus clean-kp-prerequisites [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

# 与 import_pep_textbooks.STUDENT_TEXTBOOK_BATCH_IDS / 教师端 textbook_v2.integrity_check
# WHITELIST_BOOK_IDS 同源（跨服务禁止 import，修改必须三处同步）
WHITELIST_BOOK_IDS = ("bixi1", "bixi2", "xbixi1", "xbixi2", "xbixi3")

# R4 §3.3 实测的 4 张测试 PNG（title 精确匹配；屏幕截图标题取自 :54329 实测）
TEST_PNG_TITLES = (
    "tmp_kb_question.png",
    "kb-test-image.png",
    "01_dialog_initial.png",
    "屏幕截图 2026-06-11 114129.png",
)


async def backfill_pep_chunk_meta(*, apply: bool) -> int:
    from app.models.database import async_session_factory

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT c.meta->>'book_id' AS book_id, count(*)::int AS n,
                           count(*) FILTER (WHERE c.meta->>'origin' = 'ac_slice'
                                              AND c.meta->>'rights' = 'authorized'
                                              AND c.meta->>'batch_id' IS NOT NULL)::int AS marked
                    FROM chunks c
                    JOIN knowledge_docs d ON d.id = c.doc_id
                    WHERE c.deleted_at IS NULL AND d.deleted_at IS NULL
                      AND c.meta->>'book_id' = ANY(:book_ids)
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                {"book_ids": list(WHITELIST_BOOK_IDS)},
            )
        ).all()
        print("5 册白名单 chunk meta 现状（origin/rights/batch_id 齐备为 marked）:")
        for r in rows:
            print(f"  {r.book_id}: chunks={r.n} marked={r.marked} 待补={r.n - r.marked}")
        todo = sum(r.n - r.marked for r in rows)
        if not rows:
            print("[WARN] 学生库无白名单 book_id 语料（生产 162 需先重放 5 册，§10.5）")
            return 1
        if not todo:
            print("[OK] 全部已带标记（幂等：无需回填）")
            return 0
        if not apply:
            print(f"[DRY-RUN] 未写库；加 --apply 回填 {todo} 行")
            return 0
        result = await db.execute(
            text(
                """
                UPDATE chunks c
                SET meta = c.meta || jsonb_build_object(
                        'origin', 'ac_slice',
                        'rights', 'authorized',
                        'batch_id', d.meta->>'batch_id')
                FROM knowledge_docs d
                WHERE c.doc_id = d.id
                  AND d.deleted_at IS NULL
                  AND c.meta->>'book_id' = ANY(:book_ids)
                  AND (c.meta->>'origin' IS DISTINCT FROM 'ac_slice'
                       OR c.meta->>'rights' IS DISTINCT FROM 'authorized'
                       OR c.meta->>'batch_id' IS DISTINCT FROM d.meta->>'batch_id')
                """
            ),
            {"book_ids": list(WHITELIST_BOOK_IDS)},
        )
        await db.commit()
        print(f"[APPLIED] 已回填 {result.rowcount} 行（origin=ac_slice/rights=authorized/batch_id）")
        return 0


async def soft_delete_test_pngs(*, apply: bool) -> int:
    from app.models.database import async_session_factory

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, title, (deleted_at IS NOT NULL) AS deleted
                    FROM knowledge_docs
                    WHERE title = ANY(:titles)
                    """
                ),
                {"titles": list(TEST_PNG_TITLES)},
            )
        ).all()
        print(f"测试 PNG 文档（R4 §3.3 口径 {len(TEST_PNG_TITLES)} 张标题）:")
        todo = [r for r in rows if not r.deleted]
        for r in rows:
            print(f"  {'已删' if r.deleted else '待删'} {r.id} {r.title}")
        if not todo:
            print("[OK] 无待软删行（幂等：目标状态已达成）")
            return 0
        if not apply:
            print(f"[DRY-RUN] 未写库；加 --apply 软删 {len(todo)} 行（deleted_at 置位，物理保留）")
            return 0
        result = await db.execute(
            text(
                """
                UPDATE knowledge_docs
                SET deleted_at = now()
                WHERE title = ANY(:titles) AND deleted_at IS NULL
                """
            ),
            {"titles": list(TEST_PNG_TITLES)},
        )
        await db.commit()
        print(f"[APPLIED] 已软删 {result.rowcount} 行（物理保留）")
        return 0


async def clean_kp_prerequisites(*, apply: bool) -> int:
    from app.models.database import async_session_factory

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT pr.id, pr.kp_code, pr.prereq_code,
                           (kp.id IS NULL) AS kp_missing,
                           (pre.id IS NULL) AS pre_missing
                    FROM kp_prerequisites pr
                    LEFT JOIN knowledge_points kp ON kp.code = pr.kp_code
                    LEFT JOIN knowledge_points pre ON pre.code = pr.prereq_code
                    WHERE kp.id IS NULL OR pre.id IS NULL
                    """
                )
            )
        ).all()
        print(f"kp_prerequisites 悬空边（任一端 code 不在 knowledge_points）: {len(rows)} 条")
        for r in rows:
            print(f"  id={r.id} {r.kp_code} <- {r.prereq_code} "
                  f"(kp_missing={r.kp_missing}, pre_missing={r.pre_missing})")
        if not rows:
            print("[OK] 无悬空边（幂等：目标状态已达成）")
            return 0
        if not apply:
            print(f"[DRY-RUN] 未写库；加 --apply 删除 {len(rows)} 条悬空边（补边另立项）")
            return 0
        result = await db.execute(
            text(
                """
                DELETE FROM kp_prerequisites pr
                WHERE NOT EXISTS (SELECT 1 FROM knowledge_points kp WHERE kp.code = pr.kp_code)
                   OR NOT EXISTS (SELECT 1 FROM knowledge_points pre WHERE pre.code = pr.prereq_code)
                """
            )
        )
        await db.commit()
        print(f"[APPLIED] 已删除 {result.rowcount} 条悬空边")
        return 0


ACTIONS = {
    "backfill-pep-chunk-meta": backfill_pep_chunk_meta,
    "soft-delete-test-pngs": soft_delete_test_pngs,
    "clean-kp-prerequisites": clean_kp_prerequisites,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="学生库语料维护（PLAN §3.3，幂等 dry-run）")
    parser.add_argument("action", choices=sorted(ACTIONS), help="维护动作")
    parser.add_argument("--apply", action="store_true", help="实际写库（缺省 dry-run）")
    args = parser.parse_args()
    sys.exit(asyncio.run(ACTIONS[args.action](apply=args.apply)))


if __name__ == "__main__":
    main()
