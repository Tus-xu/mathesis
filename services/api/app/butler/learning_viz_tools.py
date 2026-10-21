"""学情可视化只读工具（stu-r2 管线通电轨）

两个 READ 工具，供 student.chat 场景的确定性计划（fixed_plan）真实调工具取数：
- student.fsrs.retention.read      FSRS 记忆保持率：对真实 error_records 的 stability
                                    用 fsrs.retrievability(t, S) 现算 R(t) 曲线；
- student.mastery.snapshot.trend.read  BKT 掌握度：mastery_records 最新值 +
                                    mastery_snapshots 近 N 天真实趋势。

纪律（对齐 app/butler/tools.py 既有工具约定）：
- handler 只用 context.request.actor.user_id 取身份，禁止 LLM 传 user_id；
- handler 不 commit/rollback/开第二事务（Runtime 管理事务）；
- db=None → ToolUnavailableError → Executor 转稳定 ToolResult；
- 全部 risk=READ、allowed_scenes 含 student.chat；
- 输出数字 100% 来自 DB 行 + fsrs/bkt 现算，无任何合成/默认序列；
- 空数据如实返回空 items（卡片层负责诚实空态，不编造）。

卡片组装（card_for_tool_result）：把 ToolResult.data 包装为前端同构数据卡
（type='fsrs_retention' | 'mastery_bkt'），供 ResultComposer（floating-butler 信封
blocks）与 chat_bridge（对话 SSE card 事件）共用同一组装逻辑——单一逻辑源。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.butler.contracts import ActorRole, ToolRisk, ToolResult
from app.butler.executor import ToolExecutionContext
from app.butler.registry import ToolDefinition, ToolRegistry
from app.models.mastery_snapshot import MasterySnapshot
from app.services import bkt, fsrs
from app.services import growth as growth_svc

# 工具名（chat_bridge / composer 按名组装卡片，单一逻辑源）
TOOL_FSRS_RETENTION = "student.fsrs.retention.read"
TOOL_MASTERY_TREND = "student.mastery.snapshot.trend.read"

# 学情数据轨拥有 student_router.py；本文件零改动 growth.py/fsrs.py/bkt.py，只复用。

_STUDENT_ONLY = frozenset({ActorRole.STUDENT})


def _require_db(context: ToolExecutionContext) -> AsyncSession:
    if context.db is None:
        from app.butler.tools import ToolUnavailableError

        raise ToolUnavailableError("database unavailable")
    return context.db


# ==================== 输入/输出模型（Planner-facing，extra=forbid） ====================


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FsrsRetentionReadInput(_Strict):
    limit: int = Field(default=3, ge=1, le=5)
    due_only: bool = True


class CurvePoint(_Strict):
    t: int  # 距上次复习天数
    r: float  # 保持率 0-1（fsrs.retrievability 现算）


class StabilityLadderStep(_Strict):
    review_count: int
    stability: float


class CurvePayload(_Strict):
    thresholds: dict[str, float]  # {"due": 0.85, "decay": 0.6}
    points: list[CurvePoint]
    stability_ladder: list[StabilityLadderStep]


class RetentionItem(_Strict):
    record_id: str
    kp_code: str | None = None
    kp_name: str
    question_preview: str
    review_count: int
    wrong_count: int
    stability: float  # 记忆稳定度 S（天）
    retrievability: float  # 当前保持率 R 0-1
    level: str  # new/lv1..lv4
    is_due: bool
    hours_to_forget: float
    next_review_at: str | None = None
    current_t: float  # 距上次复习已过天数
    curve: CurvePayload


class FsrsRetentionReadOutput(_Strict):
    items: list[RetentionItem]


class TrendPoint(_Strict):
    date: str  # YYYY-MM-DD
    mastery: float | None  # 当日快照缺失为 None（诚实空档，不插值）


class MasteryTrendItem(_Strict):
    kp_code: str
    kp_name: str
    mastery: float
    practice_count: int
    correct_count: int


class MasteryTrendSummary(_Strict):
    avg_mastery: float
    mastered_count: int  # mastery >= 0.7
    total_kps: int


class MasterySeriesRow(_Strict):
    kp_code: str
    kp_name: str
    points: list[TrendPoint]


class MasteryTrendReadInput(_Strict):
    days: int = Field(default=14, ge=1, le=60)
    limit: int = Field(default=6, ge=1, le=12)


class MasteryTrendReadOutput(_Strict):
    series: list[MasterySeriesRow]
    items: list[MasteryTrendItem]  # 掌握度升序（薄弱在前）
    summary: MasteryTrendSummary
    snapshot_days: int  # 窗口内真实快照天数（<3 → 卡片标注「数据积累中」）
    max_practice_count: int  # 选中 kp 的最大答题量（bkt.should_use_bkt 门槛判定）


# ==================== handlers ====================


def _curve_for(stability: float, wrong_count: int, review_count: int) -> CurvePayload:
    """对真实 S 现算 R(t) 曲线与稳定度阶梯（无任何合成数据）。"""
    horizon = int(min(30, max(7, fsrs.days_until(stability, fsrs.DECAY_THRESHOLD) + 7)))
    points = [
        CurvePoint(t=t, r=fsrs.retrievability(float(t), stability))
        for t in range(0, horizon + 1)
    ]
    # 稳定度阶梯：0..当前复习档，值 = estimate_stability（如实按复习次数推导）
    ladder = [
        StabilityLadderStep(
            review_count=i, stability=fsrs.estimate_stability(i, wrong_count)
        )
        for i in range(0, max(0, review_count) + 1)
    ]
    return CurvePayload(
        thresholds={"due": fsrs.DUE_THRESHOLD, "decay": fsrs.DECAY_THRESHOLD},
        points=points,
        stability_ladder=ladder,
    )


async def read_fsrs_retention(
    db: AsyncSession, user_id, *, limit: int = 3, due_only: bool = True
) -> dict[str, Any]:
    """FSRS 记忆保持率查询：真实 error_records → fsrs 现算，保持率最低（最易忘）在前。"""
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    views = [v for v in error_views if v["is_due"]] if due_only else list(error_views)
    # 保持率升序：R 最低 = 离遗忘最近 = 最该先复习
    views.sort(key=lambda v: v["retrievability"])
    views = views[: max(1, limit)]

    name_map = await growth_svc.kp_name_map(
        db, [v["record"].kp_code for v in views]
    )
    items: list[dict[str, Any]] = []
    for v in views:
        rec = v["record"]
        stability = float(v["stability"])
        wrong_count = int(v["wrong_count"])
        review_count = int(rec.review_count or 0)
        from datetime import datetime as _dt

        last_activity = rec.updated_at or rec.created_at
        elapsed = fsrs.days_since(last_activity)
        next_review = None
        if rec.next_review_at is not None:
            nr = rec.next_review_at
            next_review = nr.isoformat() if isinstance(nr, _dt) else str(nr)
        items.append(
            {
                "record_id": str(rec.id),
                "kp_code": rec.kp_code,
                "kp_name": name_map.get(rec.kp_code) or (rec.kp_code or "未关联知识点"),
                "question_preview": (rec.question_text or "").replace("\n", " ").strip()[:60],
                "review_count": review_count,
                "wrong_count": wrong_count,
                "stability": round(stability, 3),
                "retrievability": round(float(v["retrievability"]), 4),
                "level": v["level"],
                "is_due": bool(v["is_due"]),
                "hours_to_forget": float(v["hours_to_forget"]),
                "next_review_at": next_review,
                "current_t": round(elapsed, 2),
                "curve": _curve_for(stability, wrong_count, review_count).model_dump(),
            }
        )
    return {"items": items}


async def read_mastery_trend(
    db: AsyncSession, user_id, *, days: int = 14, limit: int = 6
) -> dict[str, Any]:
    """BKT 掌握度查询：mastery_records 最新值 + mastery_snapshots 近 N 天真实趋势。"""
    rows = await growth_svc.load_mastery_rows(db, user_id)
    total_kps = len(rows)
    avg_mastery = round(sum(r["mastery"] for r in rows) / total_kps, 4) if rows else 0.0
    mastered_count = sum(1 for r in rows if r["mastery"] >= 0.7)

    # 选点：最新掌握度升序（最薄弱优先）∪ 练习量前 N，去重（对齐定稿契约）
    by_mastery = sorted(rows, key=lambda r: r["mastery"])
    by_practice = sorted(rows, key=lambda r: r["practice_count"], reverse=True)
    selected: dict[str, dict] = {}
    for r in by_mastery[:limit] + by_practice[:limit]:
        selected.setdefault(r["kp_code"], r)
    selected_rows = sorted(selected.values(), key=lambda r: r["mastery"])

    today = date.today()
    since = today - timedelta(days=max(1, days) - 1)
    snapshot_stmt = select(
        MasterySnapshot.kp_code, MasterySnapshot.date, MasterySnapshot.mastery
    ).where(MasterySnapshot.user_id == user_id, MasterySnapshot.date >= since)
    if selected:
        snapshot_stmt = snapshot_stmt.where(
            MasterySnapshot.kp_code.in_(list(selected.keys()))
        )
    snapshot_rows = (await db.execute(snapshot_stmt)).all()
    snap_map: dict[tuple[str, date], float] = {}
    snapshot_dates: set[date] = set()
    for kp_code, d, mastery in snapshot_rows:
        snap_map[(kp_code, d)] = float(mastery)
        snapshot_dates.add(d)

    all_dates = [since + timedelta(days=i) for i in range(max(1, days))]
    series: list[dict[str, Any]] = []
    for r in selected_rows:
        points = [
            {
                "date": d.isoformat(),
                "mastery": round(snap_map[(r["kp_code"], d)], 4)
                if (r["kp_code"], d) in snap_map
                else None,
            }
            for d in all_dates
        ]
        series.append({"kp_code": r["kp_code"], "kp_name": r["kp_name"], "points": points})

    return {
        "series": series,
        "items": [
            {
                "kp_code": r["kp_code"],
                "kp_name": r["kp_name"],
                "mastery": round(r["mastery"], 4),
                "practice_count": int(r["practice_count"]),
                "correct_count": int(r["correct_count"]),
            }
            for r in selected_rows
        ],
        "summary": {
            "avg_mastery": avg_mastery,
            "mastered_count": mastered_count,
            "total_kps": total_kps,
        },
        # 窗口内有任一 kp 快照的真实天数（稀疏数据诚实标注，不造假曲线）
        "snapshot_days": len(snapshot_dates),
        "max_practice_count": max((int(r["practice_count"]) for r in selected_rows), default=0),
    }


async def _h_fsrs_retention_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await read_fsrs_retention(
        db,
        context.request.actor.user_id,
        limit=validated_input["limit"],
        due_only=validated_input["due_only"],
    )


async def _h_mastery_trend_read(
    context: ToolExecutionContext, validated_input: dict[str, Any]
) -> dict[str, Any]:
    db = _require_db(context)
    return await read_mastery_trend(
        db,
        context.request.actor.user_id,
        days=validated_input["days"],
        limit=validated_input["limit"],
    )


# ==================== 注册 ====================


def register_learning_viz_tools(registry: ToolRegistry) -> None:
    """注册 2 个学情可视化只读工具（student.chat 场景；不并入 9 领域工具注册表，
    tests/test_butler_domain_tools.py:232 断言 domain registry 恰为 9 个）。"""
    registry.register(_FSRS_RETENTION_DEF)
    registry.register(_MASTERY_TREND_DEF)


_FSRS_RETENTION_DEF = ToolDefinition(
    name=TOOL_FSRS_RETENTION,
    version="1.0.0",
    description="查询学生错题的 FSRS 记忆保持率：R(t) 遗忘曲线、稳定度 S、距遗忘天数、复习阶梯（真实错题记录现算）",
    input_model=FsrsRetentionReadInput,
    output_model=FsrsRetentionReadOutput,
    risk=ToolRisk.READ,
    allowed_roles=_STUDENT_ONLY,
    allowed_scenes=frozenset({"student.chat", "student.dashboard", "student.errors", "student.review"}),
    timeout_s=8.0,
    handler=_h_fsrs_retention_read,
)

_MASTERY_TREND_DEF = ToolDefinition(
    name=TOOL_MASTERY_TREND,
    version="1.0.0",
    description="查询学生 BKT 掌握度：各知识点掌握概率 P(known)、近 N 天真实快照趋势、薄弱点排名（mastery_records/snapshots 现算）",
    input_model=MasteryTrendReadInput,
    output_model=MasteryTrendReadOutput,
    risk=ToolRisk.READ,
    allowed_roles=_STUDENT_ONLY,
    allowed_scenes=frozenset({"student.chat", "student.dashboard", "student.profile", "student.report"}),
    timeout_s=8.0,
    handler=_h_mastery_trend_read,
)


# ==================== 卡片组装（composer 与 chat_bridge 共用的单一逻辑源） ====================

#: 掌握度三态阈值（对齐 growth_router._MASTERY_LOW/_MASTERY_HIGH：0.4 / 0.7）
_MASTERY_LOW = 0.4
_MASTERY_HIGH = 0.7

#: 快照稀疏标注线（<3 天 → 「数据积累中」）
SPARSE_SNAPSHOT_DAYS = 3

EMPTY_NO_ERRORS = "还没有错题数据——完成一次练习或复习后，这里会出现你的记忆曲线。"
EMPTY_NO_MASTERY = "暂无掌握度数据——先去做几道题建立学情基线，再来看分析。"
TOOL_DOWN_MESSAGE = "查询暂时不可用，请稍后再试。"


def _card_base(tool_name: str) -> dict[str, Any]:
    return {
        "type": "fsrs_retention" if tool_name == TOOL_FSRS_RETENTION else "mastery_bkt",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": f"butler.{tool_name}",
    }


def _mastery_state(mastery: float) -> str:
    if mastery < _MASTERY_LOW:
        return "weak"
    if mastery >= _MASTERY_HIGH:
        return "mastered"
    return "improving"


def _delta_from_trend(points: list[dict[str, Any]]) -> float | None:
    """今日快照 - 前一日快照（取最后两个非空点；无两个非空点 → None，不编造）。"""
    values = [p.get("mastery") for p in points if p.get("mastery") is not None]
    if len(values) < 2:
        return None
    return round(float(values[-1]) - float(values[-2]), 4)


def _fsrs_card(result: ToolResult, *, degraded: bool) -> dict[str, Any]:
    card = _card_base(TOOL_FSRS_RETENTION)
    if not result.ok:
        # 工具失败：诚实降级卡，不编造任何曲线
        card.update({"degraded": True, "items": [], "message": TOOL_DOWN_MESSAGE})
        return card
    items = list((result.data or {}).get("items") or [])
    card["degraded"] = bool(degraded or result.degraded)
    card["items"] = items
    if not items:
        card["empty_reason"] = "no_error_records"
        card["message"] = EMPTY_NO_ERRORS
        return card
    due_n = sum(1 for it in items if it.get("is_due"))
    top = items[0]
    card["message"] = (
        f"当前展示 {len(items)} 道错题的记忆状态，其中 {due_n} 道已到复习期；"
        f"最需巩固：「{top.get('kp_name') or '未关联知识点'}」，"
        f"记忆保持率 {round(float(top.get('retrievability') or 0) * 100)}%。"
    )
    return card


def _mastery_card(result: ToolResult, *, degraded: bool) -> dict[str, Any]:
    card = _card_base(TOOL_MASTERY_TREND)
    if not result.ok:
        card.update({"degraded": True, "items": [], "message": TOOL_DOWN_MESSAGE})
        return card
    data = result.data or {}
    items_raw = list(data.get("items") or [])
    series_map = {
        s.get("kp_code"): list(s.get("points") or []) for s in data.get("series") or []
    }
    summary_raw = data.get("summary") or {}
    snapshot_days = int(data.get("snapshot_days") or 0)
    max_practice = int(data.get("max_practice_count") or 0)

    card["degraded"] = bool(degraded or result.degraded)
    # 模型标注如实：答题量达到 bkt.should_use_bkt 门槛才标「标准 BKT」，
    # 否则如实标注 BKT-lite（bkt.py:49-54 决策线）
    card["model"] = "bkt" if bkt.should_use_bkt(max_practice) else "bkt_lite"
    card["min_answers_threshold"] = bkt.BKT_MIN_ANSWERS

    if not items_raw:
        card["empty_reason"] = "no_mastery_data"
        card["summary"] = {
            "avg_mastery": 0.0,
            "mastered_count": 0,
            "total_kps": 0,
            "weak_top1": None,
        }
        card["items"] = []
        card["message"] = EMPTY_NO_MASTERY
        return card

    items: list[dict[str, Any]] = []
    for it in sorted(items_raw, key=lambda x: float(x.get("mastery") or 0)):
        trend = series_map.get(it.get("kp_code")) or []
        items.append(
            {
                "kp_code": it.get("kp_code"),
                "kp_name": it.get("kp_name"),
                "mastery": round(float(it.get("mastery") or 0), 4),
                "state": _mastery_state(float(it.get("mastery") or 0)),
                "practice_count": int(it.get("practice_count") or 0),
                "correct_count": int(it.get("correct_count") or 0),
                "delta": _delta_from_trend(trend),
                "trend": trend,
            }
        )
    top = items[0]
    card["items"] = items
    card["summary"] = {
        "avg_mastery": round(float(summary_raw.get("avg_mastery") or 0), 4),
        "mastered_count": int(summary_raw.get("mastered_count") or 0),
        "total_kps": int(summary_raw.get("total_kps") or 0),
        "weak_top1": {
            "kp_code": top.get("kp_code"),
            "kp_name": top.get("kp_name"),
            "mastery": top.get("mastery"),
        },
    }
    parts: list[str] = []
    if 0 < snapshot_days < SPARSE_SNAPSHOT_DAYS:
        parts.append(f"数据积累中（已积累 {snapshot_days} 天快照）")
    parts.append(
        f"共 {card['summary']['total_kps']} 个知识点，平均掌握度 "
        f"{round(card['summary']['avg_mastery'] * 100)}%，已掌握 {card['summary']['mastered_count']} 个；"
        f"最薄弱：「{top.get('kp_name') or '未关联知识点'}」（{round(float(top.get('mastery') or 0) * 100)}%）"
    )
    card["message"] = "；".join(parts) + "。"
    return card


def card_for_tool_result(
    tool_name: str, result: ToolResult, *, degraded: bool = False
) -> dict[str, Any] | None:
    """工具结果 → 前端同构数据卡；非可视化工具返回 None（composer 走原文本路径）。

    grounding 纪律：卡片数字字段全部来自 ToolResult.data 序列化，模板不产生新数字；
    空数据 → 诚实空态卡（empty_reason），绝不合成假曲线/假掌握度。
    """
    if tool_name == TOOL_FSRS_RETENTION:
        return _fsrs_card(result, degraded=degraded)
    if tool_name == TOOL_MASTERY_TREND:
        return _mastery_card(result, degraded=degraded)
    return None
