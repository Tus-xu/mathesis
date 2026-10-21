"""用户学情日统计离线回填脚本（M2 迭代16 第二批 W2）

把 submissions / events / error_records / mastery_snapshots 等既有数据
按 (user_id, date) 聚合并 upsert 到 user_daily_stats，供综合分/趋势图读取。

用法：
    cd services/api
    python -m scripts.backfill_user_daily_stats --days 30
    python -m scripts.backfill_user_daily_stats --days 30 --user-id <uuid>

参数：
    --days      回填最近 N 天（含今天），默认 30
    --user-id   只回填指定用户；缺省为全量用户（users 表，剔除软删，
                users 是全量用户权威源——所有学情表外键均指向 users.id）

聚合口径（与 growth 服务读取口径对齐）：
    answer_count / correct_count
        submission_items JOIN submissions，按 submission_items.created_at
        落在当天 [00:00, 24:00) UTC 统计，剔除软删；correct = verdict='correct'。
    hint_count
        events 中 event IN ('hint_used', 'answer_requested') 当天条数。
    error_count
        error_records 当天收录数（created_at 当天，剔除软删）。
    reviewed_count
        events 中 event = 'review_done' 当天条数。
    study_minutes
        无计时数据源，按 answer_count * 3 分钟估算（占位口径，
        待前端接入学习计时事件后改由事件聚合）。
    independent_rate
        1 - hint_count / max(1, answer_count)，下限截断为 0；
        当天无作答（answer_count = 0）则为 None。
    composite_score
        growth.composite_score(当天 mastery_snapshots 均值, hint_dependency, streak)；
        当天无掌握度快照则为 None。
        注意两个近似口径（数据模型限制，无历史流水可回溯）：
        - hint_dependency 为累计口径（mastery_records + tutor_sessions 全量），
          无法按历史日切片，回填区间内逐日取同一值；
        - streak 取 streaks 表当前 current_streak（单行/用户，无历史），
          回填区间内逐日取同一值。

幂等：INSERT ... ON CONFLICT (user_id, date) DO UPDATE，可重复执行。
健壮性：任一用户/单日失败仅记 warning 并继续，不整批中断。
"""

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.coursework import ErrorRecord, Submission, SubmissionItem
from app.models.database import async_session_factory, init_db
from app.models.event import Event
from app.models.growth import UserDailyStat
from app.models.mastery_snapshot import MasterySnapshot
from app.models.user import User
from app.services import growth as growth_svc


def day_bounds(d: date) -> tuple[datetime, datetime]:
    """当天 [00:00, 24:00) UTC 区间"""
    start = datetime.combine(d, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


async def aggregate_day(
    db, user_id: uuid.UUID, day: date, hint_dep: float, streak: int
) -> dict:
    """聚合单个 (user, day) 的一行 user_daily_stats 数据"""
    start, end = day_bounds(day)

    # 作答数 / 答对数（submission_items join submissions，按作答时间）
    rs = await db.execute(
        select(
            func.count(SubmissionItem.id),
            func.count(SubmissionItem.id).filter(SubmissionItem.verdict == "correct"),
        )
        .join(Submission, SubmissionItem.submission_id == Submission.id)
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            SubmissionItem.deleted_at.is_(None),
            SubmissionItem.created_at >= start,
            SubmissionItem.created_at < end,
        )
    )
    answer_count, correct_count = (int(v or 0) for v in rs.one())

    # 提示数（练习提示 + 直接要答案）
    rs = await db.execute(
        select(func.count(Event.id)).where(
            Event.user_id == user_id,
            Event.event.in_(["hint_used", "answer_requested"]),
            Event.created_at >= start,
            Event.created_at < end,
        )
    )
    hint_count = int(rs.scalar() or 0)

    # 错题收录数
    rs = await db.execute(
        select(func.count(ErrorRecord.id)).where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.deleted_at.is_(None),
            ErrorRecord.created_at >= start,
            ErrorRecord.created_at < end,
        )
    )
    error_count = int(rs.scalar() or 0)

    # 复习完成数
    rs = await db.execute(
        select(func.count(Event.id)).where(
            Event.user_id == user_id,
            Event.event == "review_done",
            Event.created_at >= start,
            Event.created_at < end,
        )
    )
    reviewed_count = int(rs.scalar() or 0)

    # 当天掌握度均值（mastery_snapshots 按日快照）
    rs = await db.execute(
        select(func.avg(MasterySnapshot.mastery)).where(
            MasterySnapshot.user_id == user_id, MasterySnapshot.date == day
        )
    )
    avg_mastery = rs.scalar()
    avg_mastery = float(avg_mastery) if avg_mastery is not None else None

    # 独立解题率：无作答则为 None；提示数理论上不超过作答数，下限截断防负
    independent_rate = (
        round(max(0.0, 1 - hint_count / max(1, answer_count)), 4)
        if answer_count > 0
        else None
    )

    # 学习时长：无计时数据源，按每题 3 分钟估算（占位口径，见模块 docstring）
    study_minutes = answer_count * 3

    # 综合分：无当天快照则无法评估掌握度，记 None
    composite = (
        growth_svc.composite_score(avg_mastery, hint_dep, streak)
        if avg_mastery is not None
        else None
    )

    return {
        "user_id": user_id,
        "date": day,
        "composite_score": composite,
        "independent_rate": independent_rate,
        "answer_count": answer_count,
        "correct_count": correct_count,
        "hint_count": hint_count,
        "study_minutes": study_minutes,
        "error_count": error_count,
        "reviewed_count": reviewed_count,
    }


async def upsert_row(db, row: dict) -> None:
    """幂等 upsert：ON CONFLICT (user_id, date) DO UPDATE"""
    stmt = pg_insert(UserDailyStat).values(**row)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_daily_stats_user_date",
        set_={
            "composite_score": stmt.excluded.composite_score,
            "independent_rate": stmt.excluded.independent_rate,
            "answer_count": stmt.excluded.answer_count,
            "correct_count": stmt.excluded.correct_count,
            "hint_count": stmt.excluded.hint_count,
            "study_minutes": stmt.excluded.study_minutes,
            "error_count": stmt.excluded.error_count,
            "reviewed_count": stmt.excluded.reviewed_count,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)


async def run(days: int, user_id: uuid.UUID | None) -> None:
    print("=" * 60)
    print("user_daily_stats 离线回填（M2 迭代16 W2）")
    print("=" * 60)

    # 确保表存在（开发用；生产由 Alembic 保证）
    await init_db()

    today = date.today()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]

    total_rows = 0
    failed_users = 0
    failed_days = 0

    async with async_session_factory() as db:
        # 目标用户：缺省全量（users 表为全量用户权威源，剔除软删）
        if user_id is not None:
            user_ids = [user_id]
        else:
            rs = await db.execute(select(User.id).where(User.deleted_at.is_(None)))
            user_ids = list(rs.scalars().all())
        print(f"[INFO] 回填区间 {day_list[0]} ~ {day_list[-1]}（{days} 天），用户 {len(user_ids)} 个")

        for uid in user_ids:
            # hint_dependency / streak 均为累计口径（无历史流水），逐日同值，按用户缓存一次
            try:
                hint_dep = await growth_svc.hint_dependency(db, uid)
                streak = await growth_svc.current_streak(db, uid)
            except Exception as exc:  # noqa: BLE001 - 单用户失败不整批崩
                print(f"[WARN] 用户 {uid} 预聚合失败，跳过该用户: {exc}")
                failed_users += 1
                await db.rollback()
                continue

            for d in day_list:
                try:
                    row = await aggregate_day(db, uid, d, hint_dep, streak)
                    await upsert_row(db, row)
                    total_rows += 1
                    print(
                        f"  [{d.isoformat()}] user={uid} "
                        f"ans={row['answer_count']} ok={row['correct_count']} "
                        f"hint={row['hint_count']} err={row['error_count']} "
                        f"rev={row['reviewed_count']} min={row['study_minutes']} "
                        f"indep={row['independent_rate']} score={row['composite_score']}"
                    )
                except Exception as exc:  # noqa: BLE001 - 单日失败跳过继续
                    print(f"[WARN] 用户 {uid} {d.isoformat()} 聚合失败，跳过: {exc}")
                    failed_days += 1
                    await db.rollback()
                    continue

            await db.commit()

    print("\n[DONE] 回填完成")
    print(f"  - 写入/更新行数: {total_rows}")
    print(f"  - 失败用户数: {failed_users}，失败 (user, day) 数: {failed_days}")
    print("  - 口径提示: study_minutes 为 answer_count*3 估算；"
          "hint_dependency/streak 为累计口径逐日同值（见脚本 docstring）")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="离线回填 user_daily_stats（幂等 upsert，可重复执行）"
    )
    parser.add_argument("--days", type=int, default=30, help="回填最近 N 天（含今天），默认 30")
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="只回填指定用户 UUID；缺省为全量用户（users 表）",
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days 必须为正整数")
    asyncio.run(run(args.days, args.user_id))


if __name__ == "__main__":
    main()
