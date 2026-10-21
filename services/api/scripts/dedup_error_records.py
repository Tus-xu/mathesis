"""错题去重脚本（任务4）：清理现有重复错题记录。

策略（与 m2_016_error_record_dedup 迁移一致，可独立执行）：
- 分组键：同用户 + md5(去首尾空白后的题干)（活动记录，deleted_at IS NULL）；
- 每组保留 wrong_count 最大者（并列取最早创建），其余软删除（deleted_at=now）；
- 聚合：wrong_count/review_count 累加；答案/错因/知识点/备注/配图取最新非空；
  next_review_at 取最早；被删行的错因/知识点若保留行缺失则回填。

用法：
    cd services/api
    .venv\\Scripts\\python.exe -m scripts.dedup_error_records            # 实际执行
    .venv\\Scripts\\python.exe -m scripts.dedup_error_records --dry-run  # 只报告不落库
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _key(user_id: str, question_text: str, row_id: str) -> tuple:
    t = (question_text or "").strip()
    return (
        user_id,
        hashlib.md5(t.encode("utf-8")).hexdigest() if t else f"__empty__:{row_id}",
    )


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只报告重复组，不落库")
    args = parser.parse_args(argv)

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        rs = await db.execute(
            text(
                "SELECT id, user_id, question_text, wrong_count, review_count, "
                "answer_text, error_type, kp_code, note, image, next_review_at, created_at "
                "FROM error_records WHERE deleted_at IS NULL "
                "ORDER BY user_id, created_at ASC, id ASC"
            )
        )
        rows = [r._mapping for r in rs.fetchall()]

    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for r in rows:
        k = _key(str(r["user_id"]), r["question_text"], str(r["id"]))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)

    dup_groups = [(k, groups[k]) for k in order if len(groups[k]) >= 2]
    print(f"扫描活动错题 {len(rows)} 条，发现重复组 {len(dup_groups)} 个（涉及重复行 {sum(len(g) - 1 for _, g in dup_groups)} 条）")
    if not dup_groups:
        print("无需清理。")
        await engine.dispose()
        return 0

    for _dup_key, grp in dup_groups:  # noqa: B007
        keeper = max(grp, key=lambda r: int(r["wrong_count"] or 1))
        others = [r for r in grp if r["id"] != keeper["id"]]
        preview = (keeper["question_text"] or "")[:40].replace("\n", " ")
        print(f"\n[组] {preview}… ({len(grp)} 条)")
        for r in grp:
            mark = "KEEP" if r["id"] == keeper["id"] else "DEL "
            print(f"  {mark} id={r['id']} wrong_count={r['wrong_count']} created={r['created_at']}")

        if args.dry_run:
            continue

        wc = sum(int(r["wrong_count"] or 1) for r in grp)
        rc = sum(int(r["review_count"] or 0) for r in grp)
        merged: dict = {"wrong_count": wc, "review_count": rc}
        for col in ("answer_text", "error_type", "kp_code", "note"):
            latest = next((r[col] for r in reversed(grp) if r[col] is not None), None)
            merged[col] = latest if latest is not None else keeper[col]
        images = [r["image"] for r in reversed(grp) if r["image"]]
        merged["image"] = json.dumps(
            (images[0] if images else keeper["image"]) or [], ensure_ascii=False
        )
        nexts = [r["next_review_at"] for r in grp if r["next_review_at"] is not None]
        merged["next_review_at"] = min(nexts) if nexts else keeper["next_review_at"]

        async with factory() as db:
            await db.execute(
                text(
                    "UPDATE error_records SET wrong_count=:wc, review_count=:rc, "
                    "answer_text=:at, error_type=:et, kp_code=:kc, note=:nt, "
                    "image=:img, next_review_at=:nr WHERE id=:id"
                ),
                {
                    "id": keeper["id"], "wc": merged["wrong_count"], "rc": merged["review_count"],
                    "at": merged["answer_text"], "et": merged["error_type"],
                    "kc": merged["kp_code"], "nt": merged["note"],
                    "img": merged["image"], "nr": merged["next_review_at"],
                },
            )
            await db.execute(
                text("UPDATE error_records SET deleted_at=:now WHERE id = ANY(:ids)"),
                {"now": datetime.now(UTC), "ids": [r["id"] for r in others]},
            )
            await db.commit()
        print(f"  → 合并完成：wrong_count={wc}, review_count={rc}，软删除 {len(others)} 条")

    await engine.dispose()
    print("\n去重完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
