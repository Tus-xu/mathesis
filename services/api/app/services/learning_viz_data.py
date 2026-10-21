"""学情可视化数据构建器（迭代21 · D 轨数据服务）

定位：把 FSRS 记忆曲线与 BKT 掌握度组装成**对话结构化卡片**（type=fsrs_retention /
mastery_bkt）所需的字段级契约数据。纯只读聚合层：
- 复用 growth.load_errors_fsrs / load_mastery_rows / kp_name_map（M2 迭代16 现成计算）；
- 复用 fsrs.retrievability / days_until / estimate_stability / fsrs_level（FSRS-4.5，零改动）；
- 复用 bkt.BKT_MIN_ANSWERS 门槛常量（任务8 决策线）。
本模块不做任何判分/写库，不引入新算法；曲线点全部由真实稳定度 S 现算，
稀疏数据返回可辨别的空态结构（empty_reason + message），绝不合成假曲线。

消费方：AI 管家 butler 工具（student.fsrs.retention.read /
student.mastery.snapshot.trend.read）→ chat 桥接 → envelope.blocks 结构化卡片。

契约要点（与前端 RetentionCurveCard / MasteryBktCard 对齐）：
- fsrs_retention.items[i]: {record_id, kp_code, kp_name, question_preview(≤60),
  review_count, wrong_count, stability(天), retrievability(0-1), level, is_due,
  hours_to_forget, next_review_at(ISO|null), current_t(天),
  curve{thresholds{due:0.85, decay:0.6}, points[{t,r}](t=0..horizon 逐日,
  horizon=clamp(ceil(days_until(S,0.6))+7, 7, 30))},
  stability_ladder[{review_count, stability}](0..当前档, 值=estimate_stability,
  前端如实标注「按复习次数推导」)}
- mastery_bkt: {model, min_answers_threshold, summary{avg_mastery, mastered_count(≥0.7),
  total_kps, weak_top1|null}, items[{kp_code, kp_name, mastery, state, practice_count,
  correct_count, delta|null, trend[{date, mastery|null}](近 N 天)}], history_days}
  * history_days（附加字段）：近 N 天有快照的天数；前端据 history_days<3 显示
    「数据积累中（第 N 天）」灰条，不阻塞正常渲染。
- mastery 趋势工具: {series:[{kp_code, kp_name, points:[{date, mastery|null}]}]}
  kp 选择=最新掌握度升序 ∪ 练习量前 N，去重（工具契约原文）。
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mastery_snapshot import MasterySnapshot
from app.services import bkt, fsrs, growth

# 掌握度状态阈值（对齐 growth_router.py:135-136 的 _MASTERY_LOW/_MASTERY_HIGH；
# 该文件本轮零改动，此处只读常量复制并注明来源，避免路由模块间 import）
_MASTERY_LOW = 0.4
_MASTERY_HIGH = 0.7

# FSRS 曲线视窗边界（方案 §contracts：horizon=clamp(ceil(days_until(S,0.6))+7, 7, 30)）
_CURVE_HORIZON_MIN = 7
_CURVE_HORIZON_MAX = 30

# 稳定度阶梯的档位上限（fsrs._BASE_STABILITY 共 5 档：0.4/1/3/7/15）
_MAX_LADDER_RUNG = len(fsrs._BASE_STABILITY) - 1  # noqa: SLF001 — 只读长度，不改状态


def _preview(text: str | None, limit: int = 60) -> str:
    """题干预览截断（对齐 growth_router._preview 的 60 字口径）"""
    if not text:
        return ""
    t = " ".join(text.split())
    return t if len(t) <= limit else t[:limit].rstrip(" ，。；;、") + "…"


def _mastery_state(mastery: float) -> str:
    """掌握度 → weak/improving/mastered（阈值对齐 growth_router.py:135-136）"""
    if mastery >= _MASTERY_HIGH:
        return "mastered"
    if mastery >= _MASTERY_LOW:
        return "improving"
    return "weak"


# ==================== FSRS 记忆保持率卡片 ====================


async def build_fsrs_retention(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int = 3,
    due_only: bool = True,
) -> dict:
    """FSRS 保持率卡片数据：R(t) 曲线逐日现算 + 稳定度阶梯。

    返回 {items:[...]}；空态返回 {items:[], empty_reason, message}：
    - 完全无错题 → empty_reason='no_error_records'
    - 有错题但无到期（due_only=True）→ 仅 message（与"无数据"空态区分，诚实呈现）
    """
    views = await growth.load_errors_fsrs(db, user_id)
    if not views:
        return {
            "items": [],
            "empty_reason": "no_error_records",
            "message": "完成一次练习或复习后，这里会出现你的记忆保持率曲线。",
        }

    ordered = sorted(views, key=lambda v: (not v["is_due"], v["retrievability"]))
    if due_only:
        due_views = [v for v in ordered if v["is_due"]]
        if not due_views:
            return {
                "items": [],
                "message": (
                    f"当前没有到期的错题——共 {len(views)} 条在册，"
                    "均处于记忆保持期内，保持最差的一道 R 也有 "
                    f"{round(min(v['retrievability'] for v in views) * 100)}%。"
                ),
            }
        ordered = due_views

    picked = ordered[: max(1, min(int(limit), 5))]
    name_map = await growth.kp_name_map(db, [v["record"].kp_code for v in picked])

    items: list[dict] = []
    for v in picked:
        rec = v["record"]
        stability = float(v["stability"])
        # 曲线视窗：衰减到 R=0.6 的天数 + 7 天观察余量，clamp [7, 30]（契约原文）
        horizon = int(
            min(
                _CURVE_HORIZON_MAX,
                max(_CURVE_HORIZON_MIN, ceil(fsrs.days_until(stability, fsrs.DECAY_THRESHOLD)) + 7),
            )
        )
        points = [
            {"t": t, "r": fsrs.retrievability(float(t), stability)} for t in range(0, horizon + 1)
        ]
        # 稳定度阶梯：0..当前复习档，每档值=estimate_stability(次数, 错次)——
        # 展示「每多复习一次 S 抬升一档」，如实标注按复习次数推导（非逐次实测）
        review_count = int(rec.review_count or 0)
        wrong_count = int(v["wrong_count"])
        ladder = [
            {
                "review_count": rc,
                "stability": fsrs.estimate_stability(rc, wrong_count),
            }
            for rc in range(0, min(review_count, _MAX_LADDER_RUNG) + 1)
        ]
        items.append(
            {
                "record_id": str(rec.id),
                "kp_code": rec.kp_code,
                "kp_name": name_map.get(rec.kp_code),
                "question_preview": _preview(rec.question_text, 60),
                "review_count": review_count,
                "wrong_count": wrong_count,
                "stability": stability,
                "retrievability": float(v["retrievability"]),
                "level": v["level"],
                "is_due": bool(v["is_due"]),
                "hours_to_forget": float(v["hours_to_forget"]),
                "next_review_at": (rec.next_review_at.isoformat() if rec.next_review_at else None),
                # 距上次记忆活动的天数（与 enrich_error_fsrs 同口径：updated_at 近似）
                "current_t": fsrs.days_since(rec.updated_at or rec.created_at),
                "curve": {
                    "thresholds": {
                        "due": fsrs.DUE_THRESHOLD,
                        "decay": fsrs.DECAY_THRESHOLD,
                    },
                    "points": points,
                },
                "stability_ladder": ladder,
            }
        )
    return {"items": items}


# ==================== BKT 掌握度卡片 ====================


async def _kp_snapshot_series(
    db: AsyncSession,
    user_id: uuid.UUID,
    codes: list[str],
    days: int,
) -> dict[str, dict[str, float | None]]:
    """近 N 天逐知识点快照：{kp_code: {iso_date: mastery}}（真实 mastery_snapshots 行）"""
    since = date.today() - timedelta(days=max(1, int(days)) - 1)
    rs = await db.execute(
        select(MasterySnapshot.kp_code, MasterySnapshot.date, MasterySnapshot.mastery)
        .where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.kp_code.in_(codes),
            MasterySnapshot.date >= since,
        )
        .order_by(MasterySnapshot.date)
    )
    out: dict[str, dict[str, float | None]] = {}
    for kp_code, d, mastery in rs.all():
        out.setdefault(kp_code, {})[d.isoformat()] = float(mastery)
    return out


def _continuous_trend(by_date: dict[str, float | None], days: int) -> list[dict]:
    """连续 N 天序列（无快照日填 null——前端如实断线，不插值不造数）"""
    since = date.today() - timedelta(days=days - 1)
    return [
        {
            "date": (since + timedelta(days=i)).isoformat(),
            "mastery": by_date.get((since + timedelta(days=i)).isoformat()),
        }
        for i in range(days)
    ]


async def build_mastery_bkt(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = 14,
    limit: int = 6,
) -> dict:
    """BKT 掌握度卡片数据：水平条 P(known) + 近 N 天真实快照趋势。

    - 掌握值全部来自 mastery_records（_update_mastery 的 BKT-lite 后验）；
    - 趋势点全部来自 mastery_snapshots 真实行；
    - model 标注如实：mastery_records 的值当前全部由 BKT-lite 更新式产生
      （bkt.py:8-9：标准四参数 BKT 为接口预留，_update_mastery 尚未切换），
      故 model 恒为 'bkt_lite'，min_answers_threshold 展示 bkt.py:49-54 的数据门槛——
      达标只说明数据量支持切换，不改变既有值的计算来源，如实标注不冒称。
    - 空态：无掌握度记录 → items=[] + empty_reason='no_mastery_data'。
    """
    rows = await growth.load_mastery_rows(db, user_id)
    base = {
        "model": "bkt_lite",
        "min_answers_threshold": bkt.BKT_MIN_ANSWERS,
    }
    if not rows:
        return {
            **base,
            "summary": {"avg_mastery": 0.0, "mastered_count": 0, "total_kps": 0, "weak_top1": None},
            "items": [],
            "history_days": 0,
            "empty_reason": "no_mastery_data",
            "message": "完成一次练习后，这里会出现你的知识点掌握度。",
        }

    days = max(1, min(int(days), 60))
    limit = max(1, min(int(limit), 12))
    since = date.today() - timedelta(days=days - 1)
    snaps = await _kp_snapshot_series(db, user_id, [r["kp_code"] for r in rows], days)
    # 近 N 天有真实快照的天数（稀疏数据提示「数据积累中」的依据）
    history_days = len({d for by_date in snaps.values() for d in by_date if by_date[d] is not None})

    ordered = sorted(rows, key=lambda r: r["mastery"])
    today_iso = date.today().isoformat()
    yesterday_iso = (date.today() - timedelta(days=1)).isoformat()

    items: list[dict] = []
    for r in ordered[:limit]:
        by_date = snaps.get(r["kp_code"], {})
        today_v = by_date.get(today_iso)
        yesterday_v = by_date.get(yesterday_iso)
        delta = (
            round(today_v - yesterday_v, 4)
            if today_v is not None and yesterday_v is not None
            else None
        )
        items.append(
            {
                "kp_code": r["kp_code"],
                "kp_name": r["kp_name"],
                "mastery": round(float(r["mastery"]), 4),
                "state": _mastery_state(float(r["mastery"])),
                "practice_count": int(r["practice_count"]),
                "correct_count": int(r["correct_count"]),
                # 今日快照 − 前一日快照；任一日无快照为 null（不造 0 假变化）
                "delta": delta,
                "trend": _continuous_trend(by_date, days),
            }
        )

    weakest = ordered[0]
    return {
        **base,
        "summary": {
            "avg_mastery": round(sum(float(r["mastery"]) for r in rows) / len(rows), 4),
            "mastered_count": sum(1 for r in rows if float(r["mastery"]) >= _MASTERY_HIGH),
            "total_kps": len(rows),
            "weak_top1": {
                "kp_code": weakest["kp_code"],
                "kp_name": weakest["kp_name"],
                "mastery": round(float(weakest["mastery"]), 4),
            },
        },
        "items": items,
        "history_days": history_days,
        "window_started_at": since.isoformat(),
    }


# ==================== 掌握度趋势序列（butler 趋势工具数据源） ====================


async def build_mastery_trend_series(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = 14,
    limit: int = 6,
) -> dict:
    """趋势工具契约数据：{series:[{kp_code, kp_name, points:[{date, mastery|null}]}]}。

    kp 选择=最新掌握度升序 ∪ 练习量前 N，去重（工具契约原文）。
    空态：{series:[], empty_reason:'no_mastery_data', message}。
    """
    rows = await growth.load_mastery_rows(db, user_id)
    if not rows:
        return {
            "series": [],
            "empty_reason": "no_mastery_data",
            "message": "完成一次练习后，这里会出现你的知识点掌握趋势。",
        }

    days = max(1, min(int(days), 60))
    limit = max(1, min(int(limit), 12))
    by_mastery = sorted(rows, key=lambda r: r["mastery"])[:limit]
    by_practice = sorted(rows, key=lambda r: -int(r["practice_count"]))[:limit]
    picked: list[dict] = []
    seen: set[str] = set()
    for r in by_mastery + by_practice:  # 升序∪练习量前N，去重（契约原文）
        if r["kp_code"] not in seen:
            seen.add(r["kp_code"])
            picked.append(r)

    snaps = await _kp_snapshot_series(db, user_id, [r["kp_code"] for r in picked], days)
    series = [
        {
            "kp_code": r["kp_code"],
            "kp_name": r["kp_name"],
            "mastery": round(float(r["mastery"]), 4),
            "points": _continuous_trend(snaps.get(r["kp_code"], {}), days),
        }
        for r in picked
    ]
    return {"series": series}
