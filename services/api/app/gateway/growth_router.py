"""学情增长聚合路由（M2 迭代16，方案 §3 全部 24 个只读聚合端点）

端点清单：
- GET  /api/student/growth/overview — 用户全域学情聚合（§3.0.1）
- GET  /api/student/growth/panel — 右侧全局面板（§3.0.2）
- POST /api/agent/route-intent — 功能跳转意图识别（§3.0.3，agent_ext_router）
- GET  /api/student/growth/loop-progress — 当日对话 7 步闭环进度（模块1）
- GET  /api/student/practice/group-recommend — 今日训练组推荐（§3.2.1）
- GET  /api/student/practice/difficulty-mix — 难度配比统计（§3.2.2）
- GET  /api/student/practice/smart-score — SmartScore（§3.2.3）
- GET  /api/student/practice/summary — 训练总结（§3.2.4）
- GET  /api/student/error-records/memory-heatmap — FSRS 记忆稳定性热力图（§3.3.1）
- GET  /api/student/error-records/due-queue — 到期错题队列（§3.3.2）
- GET  /api/student/error-records/{record_id}/detail — 错题详情扩展（§3.3.3）
- GET  /api/student/error-records/filter — 错题多维筛选（§3.3.4）
- GET  /api/student/report/highlights — 本周亮点（§3.4.1）
- GET  /api/student/report/weak-points — 薄弱环节 Top4（§3.4.2）
- GET  /api/student/report/mastery-trend-forecast — 趋势 + 遗忘预测（§3.4.3）
- GET  /api/student/report/error-distribution — 12 类思维漏洞分布（§3.4.4）
- GET  /api/student/report/honesty — 诚实提示（§3.4.5）
- GET  /api/student/knowledge-graph/pie — ALEKS 学习版图（§3.5.1）
- GET  /api/student/knowledge-graph/tree — 章节树形视图（§3.5.2）
- GET  /api/student/knowledge-graph/nodes/{kp_code}/deps — 追根溯源依赖链（§3.5.3）
- GET  /api/student/knowledge-graph/nodes/{kp_code}/recommend — 节点学习推荐（§3.5.4）
- GET  /api/student/growth/today-3 — 今日 3 件事（模块6）
- GET  /api/student/growth/score-trend — 综合分 7 日趋势（模块6）
- GET  /api/student/growth/feature-entries — 功能入口聚合（模块6）

约定（对齐 student_router）：信封 {code:0, message:"ok", data:...}；
鉴权 get_current_user；无数据一律返回方案标注的空态默认值，绝不抛 500。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from functools import wraps
from inspect import isawaitable
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.gateway.redis import get_redis
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    ErrorRecord,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import get_db
from app.models.event import Event
from app.models.growth import KpPrerequisite, UserDailyStat
from app.models.knowledge_point import KnowledgePoint
from app.models.mastery_snapshot import MasterySnapshot
from app.models.tutor_session import TutorSession
from app.models.user_profile import UserProfile
from app.providers.router import get_model_router
from app.services import copy_polish, fsrs
from app.services import growth as growth_svc
from app.services.error_record_assets import normalize_error_assets


# 报告缓存：按 user_id + date 缓存到次日 0 点（按天刷新，不每次跳转重算）
def _report_ttl_seconds() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


async def _cached_report(user_id: UUID, key: str, factory):
    """按天缓存报告（key 例：highlights/weak-points）。失败/Redis 不可用直接走 factory 兜底。"""
    import json as _json

    cache_key = f"report:{user_id}:{datetime.now(UTC).strftime('%Y-%m-%d')}:{key}"
    try:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return _json.loads(cached)
    except Exception:
        pass
    data = factory()
    if isawaitable(data):
        data = await data
    with suppress(Exception):
        await get_redis().set(cache_key, _json.dumps(data, default=str), ex=_report_ttl_seconds())
    return data


def _cache_report(key: str):
    """端点装饰器：按 user_id + date 缓存报告响应到次日 0 点。"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user = kwargs.get("user")
            if user is None and args:
                user = args[0]
            if not isinstance(user, dict) or "sub" not in user:
                return await func(*args, **kwargs)
            user_id = UUID(user["sub"])
            return await _cached_report(user_id, key, lambda: func(*args, **kwargs))
        return wrapper
    return decorator


logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/student", tags=["growth"])
agent_ext_router = APIRouter(prefix="/api/agent", tags=["growth"])

# 真实数学知识点白名单前缀（与 student_router._REAL_KP_PREFIXES 对齐，迭代09 治理口径）
_REAL_KP_PREFIXES = ("MATH-", "MX", "BK", "TST")
# 错因五枚举（ADR-M2B-002）
_VALID_ERROR_TYPES = {"concept", "formula", "calculation", "logic", "reading"}
# 稳定性筛选三档（FSRS level 映射，§3.3.4）
_VALID_STABILITY = {"stable", "decaying", "critical"}
# 高考倒计时默认日期（可被 user_profiles.preferences.gaokao_date 覆盖）
_GAOKAO_DEFAULT = date(2027, 6, 7)
# 目标分缺省值（§3.0.1）
_TARGET_SCORE_DEFAULT = 75
# BKT 学习率线性外推系数：每题推进残余空间的 3%（§3.2.1，封顶 0.95）
_BKT_LEARN_RATE = 0.03
# 掌握度分级阈值（ALEKS 四态，§2）
_MASTERY_HIGH = 0.7
_MASTERY_LOW = 0.4

# 功能跳转意图识别规则表（§3.0.3）：关键词 → 前端路由
_ROUTES: list[tuple[tuple[str, ...], str, str]] = [
    (("错题本", "错题"), "/errors", "错题本"),
    (("练题", "练习", "刷题", "做题", "训练"), "/practice", "练题中心"),
    (("学情报告", "报告"), "/report", "学情报告"),
    (("知识图谱", "图谱"), "/graph", "知识图谱"),
    (("总览", "首页"), "/overview", "学情总览"),
    (("模考", "模拟考", "考试", "试卷"), "/exam", "模拟考试"),
    (("对话", "问老师"), "/chat", "对话学习"),
]
# 学情问答类关键词（命中后路由到报告页并附 data_hint 摘要）
_LEARNING_QA_KEYWORDS = ("哪部分最弱", "最弱", "薄弱", "哪里差", "做了多少题", "正确率", "学得怎么样", "掌握得")


# ==================== LLM 文案润色（M2 迭代16 第二批，GROWTH_LLM_POLISH 开关） ====================


async def _polish_copy(template: str, scene: str) -> str:
    """模板文案 LLM 润色（prompt 约束：≤40字、不编造数据、只润色）。

    开关关闭（默认）原样返回模板，行为零变化；开启后任何异常/超时（10s）回退模板。
    迭代18 性能修复：Redis 缓存（scene+template 为 key，TTL 24h）——原实现每次请求
    同步等 LLM（growth/panel 实测 P50 2.2s），同模板数据不变时零 LLM 调用。
    """
    if not settings.growth_llm_polish:
        return template
    import hashlib

    cache_key = f"growth:polish:{scene}:{hashlib.sha1(template.encode()).hexdigest()}"
    try:
        cached = await get_redis().get(cache_key)
        if cached:
            return cached
    except Exception:
        pass  # Redis 故障不阻断，走 LLM
    try:
        router = get_model_router()
        result = await asyncio.wait_for(
            router.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是文案润色助手。把给定的学生学情文案润色成一句自然、亲切的中文，"
                            "不超过 40 字。只润色表达：不得编造、修改或增删任何数字与事实，"
                            "不要输出解释，只输出润色后的文案本身。"
                        ),
                    },
                    {"role": "user", "content": template},
                ],
                temperature=0.7,
                max_tokens=80,
                request_id=f"polish-{uuid.uuid4().hex[:12]}",
                scene=scene,
            ),
            timeout=10,
        )
        text = (result.get("content") or "").strip().strip('"“”')
        text = text.splitlines()[0].strip() if text else ""
        # 2026-09-08 B1（V2 文档 bug 清单）：推理型模型会回声 prompt——回声一律回退模板且不写缓存
        if not text or any(m in text for m in ("用户要求", "润色", "一句自然", "改写成", "不超过 40 字", "不超过40字", "文案润色助手")):
            logger.info("polish_echo_fallback", scene=scene)
            return template
        with suppress(Exception):
            await get_redis().set(cache_key, text, ex=86400)
        return text or template
    except Exception as e:
        logger.info("growth_llm_polish_fallback", scene=scene, error=str(e)[:150])
        return template


# ==================== Schemas ====================


class RouteIntentRequest(BaseModel):
    text: str  # 用户自然语言输入，如"打开错题本"


# ==================== 通用小工具 ====================


def _ok(data) -> dict:
    """统一成功信封"""
    return {"code": 0, "message": "ok", "data": data}


def _is_real_kp(kp: KnowledgePoint) -> bool:
    """知识点是否为真实数学知识点（白名单前缀 + 高中 grade，对齐 student_router 口径）"""
    if not getattr(kp, "code", None):
        return False
    if not kp.code.startswith(_REAL_KP_PREFIXES):
        return False
    return not kp.grade or kp.grade.startswith("高")


_PREVIEW_MATH_RE = re.compile(r"\${1,2}(.+?)\${1,2}", re.S)
_PREVIEW_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_PREVIEW_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$")


_GREEK_PLAIN = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "phi": "φ", "omega": "ω", "Delta": "Δ", "Omega": "Ω",
}


def _latex_to_plain(value: str) -> str:
    """把公式源码压成可读纯文本（供列表摘要，不暴露 $/反斜杠语法）。"""
    t = _PREVIEW_MATH_RE.sub(lambda m: m.group(1), value)

    def _frac(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        return f"({a})/({b})" if any(ch in a + b for ch in "+-") else f"{a}/{b}"

    t = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", _frac, t)
    t = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", t)
    for name, symbol in _GREEK_PLAIN.items():
        t = re.sub(rf"\\{name}\b", symbol, t)
    replacements = (
        (r"\\times\b", "×"),
        (r"\\cdot\b", "·"),
        (r"\\div\b", "÷"),
        (r"\\pm\b", "±"),
        (r"\\mp\b", "∓"),
        (r"\\leq?\b", "≤"),
        (r"\\geq?\b", "≥"),
        (r"\\neq\b", "≠"),
        (r"\\infty\b", "∞"),
        (r"\\rightarrow\b|\\to\b", "→"),
        (r"\\left\b|\\right\b", ""),
        (r"\\(?:qquad|quad|;|,|!)", " "),
        (r"\\begin\{[^{}]*\}|\\end\{[^{}]*\}", " "),
    )
    for pattern, repl in replacements:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"\\([A-Za-z]+)", r"\1", t)
    t = t.replace("{", "").replace("}", "").replace("$", "")
    return t


def _preview(text: str | None, limit: int = 60) -> str:
    """列表摘要：去掉 Markdown 表格/公式标记后再截断。

    旧实现对题干直接做字符截断，`| 1 | 2 |` 表格和未闭合的 `$...` 会原样漏到
    首页复习卡（2026-10-21 用户反馈的「乱码」根因）。这里先按行剔除表格，
    再把公式压成可读纯文本，最后做 Unicode 安全截断，保证预览永远不出现
    Markdown/LaTeX 语法残片。
    """
    if not text:
        return ""
    kept: list[str] = []
    for raw_line in str(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _PREVIEW_TABLE_ROW_RE.match(line) or _PREVIEW_TABLE_SEP_RE.match(line):
            continue
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "［图］", line)
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[>*+-]\s+", "", line)
        kept.append(line)
    if not kept:
        return "（题目含数据表，请打开原题查看）"
    t = " ".join(" ".join(kept).split())
    t = _latex_to_plain(t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit].rstrip(" ，。；;、") + "…"


def _mastery_state(mastery: float | None) -> str:
    """掌握度 → ALEKS 四态（无记录 = unlearned）"""
    if mastery is None:
        return "unlearned"
    if mastery >= _MASTERY_HIGH:
        return "mastered"
    if mastery >= _MASTERY_LOW:
        return "improving"
    return "weak"


def _today_start() -> datetime:
    return datetime.combine(date.today(), time.min, tzinfo=UTC)


async def _real_kps(db: AsyncSession) -> list[KnowledgePoint]:
    """全部真实数学知识点（过滤测试占位点）"""
    rs = await db.execute(select(KnowledgePoint))
    return [k for k in rs.scalars().all() if _is_real_kp(k)]


async def _snapshot_avg(db: AsyncSession, user_id: uuid.UUID, day: date) -> float | None:
    """某日 mastery_snapshots 均值（无快照返回 None）"""
    rs = await db.execute(
        select(func.avg(MasterySnapshot.mastery)).where(
            MasterySnapshot.user_id == user_id, MasterySnapshot.date == day
        )
    )
    v = rs.scalar()
    return float(v) if v is not None else None


async def _snapshot_near(
    db: AsyncSession, user_id: uuid.UUID, kp_code: str, ref: date, direction: str
) -> float | None:
    """某 kp 在 ref 日期前/后最近的快照掌握度（§3.2.4 训练前后对比）"""
    q = select(MasterySnapshot.mastery).where(
        MasterySnapshot.user_id == user_id, MasterySnapshot.kp_code == kp_code
    )
    if direction == "before":
        q = q.where(MasterySnapshot.date < ref)
    else:
        q = q.where(MasterySnapshot.date >= ref)
    q = q.order_by(MasterySnapshot.date.desc()).limit(1)
    rs = await db.execute(q)
    v = rs.scalar()
    return float(v) if v is not None else None


async def _loop_step_count(db: AsyncSession, user_id: uuid.UUID, step: str, days: int = 0) -> int:
    """loop_step_done 事件计数（props.step 匹配；days=0 表示仅今日）"""
    since = _today_start() - timedelta(days=days)
    rs = await db.execute(
        select(func.count(Event.id)).where(
            Event.user_id == user_id,
            Event.event == "loop_step_done",
            Event.created_at >= since,
            Event.props["step"].as_string() == step,
        )
    )
    return int(rs.scalar() or 0)


async def _target_score(db: AsyncSession, user_id: uuid.UUID) -> int:
    """目标分：user_profiles.preferences.target_score，缺省 75"""
    rs = await db.execute(select(UserProfile.preferences).where(UserProfile.user_id == user_id))
    prefs = rs.scalar() or {}
    try:
        return int(prefs.get("target_score", _TARGET_SCORE_DEFAULT))
    except (TypeError, ValueError):
        return _TARGET_SCORE_DEFAULT


async def _composite_view(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """综合分视图（§3.0.1 公式 + 周环比换算），overview/panel/score-trend 共用"""
    rows = await growth_svc.load_mastery_rows(db, user_id)
    avg_mastery = sum(r["mastery"] for r in rows) / len(rows) if rows else 0.0
    hint_dep = await growth_svc.hint_dependency(db, user_id)
    streak = await growth_svc.current_streak(db, user_id)
    score = growth_svc.composite_score(avg_mastery, hint_dep, streak)
    # 环比：mastery_snapshots 7 天前均值换算为综合分（无快照则按当前掌握度，delta=0）
    prev_avg = await _snapshot_avg(db, user_id, date.today() - timedelta(days=7))
    last_week_score = growth_svc.composite_score(
        prev_avg if prev_avg is not None else avg_mastery, hint_dep, streak
    )
    return {
        "rows": rows,
        "avg_mastery": avg_mastery,
        "hint_dep": hint_dep,
        "streak": streak,
        "score": score,
        "last_week_score": last_week_score,
        "score_delta": score - last_week_score,
    }


def _difficulty_mix(count: int) -> dict[str, int]:
    """3:2:1 难度配比（count 参数化时按比例取整，hard/medium 保底 1）"""
    hard = max(1, round(count / 6))
    medium = max(1, round(count / 3))
    easy = max(0, count - hard - medium)
    return {"easy": easy, "medium": medium, "hard": hard}


def _urgency_label(r: float) -> str:
    """到期紧迫度分档（retrievability 越低越急）"""
    if r < fsrs.DECAY_THRESHOLD:
        return "紧急"
    if r < 0.7:
        return "高"
    if r < 0.8:
        return "中"
    return "低"


# ==================== 模块0 全局基础能力 ====================


@router.get("/growth/overview")
async def growth_overview(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户全域学情聚合（§3.0.1）"""
    user_id = uuid.UUID(user["sub"])
    view = await _composite_view(db, user_id)
    rows = view["rows"]

    # 错题数 / FSRS 到期数
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    error_total = len(error_views)
    error_due_count = sum(1 for v in error_views if v["is_due"])

    # 掌握/总知识点数（ALEKS 口径：≥0.7 为已掌握）
    mastered_kp_count = sum(1 for r in rows if r["mastery"] >= _MASTERY_HIGH)
    total_kp_count = len(await _real_kps(db))

    week_answer_count, week_correct_count = await growth_svc.week_answer_stats(db, user_id, 7)
    target = await _target_score(db, user_id)

    # 迭代17 AI 管家：管家消息（"小婷的话"，数据驱动模板 + LLM 润色，异常回退模板）
    accuracy = round(week_correct_count / week_answer_count * 100) if week_answer_count else 0
    butler_tpl = (
        f"本周你做了 {week_answer_count} 道题，正确率 {accuracy}%，"
        f"还有 {error_due_count} 道错题该复习了。"
    )
    butler_message = await copy_polish.polish(
        copy_polish.SCENE_OVERVIEW_GREETING,
        butler_tpl,
        data_fingerprint=f"overview|{week_answer_count}|{accuracy}|{error_due_count}",
        user_id=str(user_id),
    )

    return _ok(
        {
            "composite_score": view["score"],
            "score_delta_week": view["score_delta"],
            "last_week_score": view["last_week_score"],
            "target_score": target,
            "independent_rate": round(1 - min(view["hint_dep"], 1.0), 4),
            "streak_days": view["streak"],
            "error_total": error_total,
            "error_due_count": error_due_count,
            "mastered_kp_count": mastered_kp_count,
            "total_kp_count": total_kp_count,
            "week_answer_count": week_answer_count,
            "week_correct_count": week_correct_count,
            "butler_message": butler_message,
        }
    )


@router.get("/growth/panel")
async def growth_panel(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """右侧全局面板（§3.0.2）：黄金窗口 + 今日行动 + 本周简报 + 鼓励语 + 高考倒计时"""
    user_id = uuid.UUID(user["sub"])
    view = await _composite_view(db, user_id)
    rows = view["rows"]

    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    due_count = sum(1 for v in error_views if v["is_due"])
    weakest = min(rows, key=lambda r: r["mastery"]) if rows else None

    # 老师作业：定向到本人的已发布任务数
    hw_rs = await db.execute(
        select(func.count(Assignment.id))
        .join(AssignmentTarget, AssignmentTarget.assignment_id == Assignment.id)
        .where(
            AssignmentTarget.target_type == "student",
            AssignmentTarget.target_id == user_id,
            AssignmentTarget.deleted_at.is_(None),
            Assignment.status == "published",
            Assignment.deleted_at.is_(None),
        )
    )
    homework_count = int(hw_rs.scalar() or 0)

    today_actions = [
        {
            "key": "review_errors",
            "title": "复习错题",
            "count": due_count,
            "route": "/errors",
        },
        {
            "key": "variant_training",
            "title": "变式训练",
            "count": 5 if weakest else 0,
            "route": f"/practice?kp={weakest['kp_code']}" if weakest else "/practice",
        },
        {"key": "challenge", "title": "压轴挑战", "count": 1, "route": "/practice?mode=challenge"},
        {"key": "homework", "title": "老师作业", "count": homework_count, "route": "/assignments"},
    ]

    week_brief = {
        "independent_rate": round(1 - min(view["hint_dep"], 1.0), 4),
        "score_delta": view["score_delta"],
        "streak_days": view["streak"],
    }

    # 数据驱动模板鼓励语（GROWTH_LLM_POLISH 开启时 LLM 润色，失败回退模板）
    if view["streak"] >= 7:
        encouragement = f"已连续学习 {view['streak']} 天，节奏很稳，继续保持！"
    elif due_count > 0:
        encouragement = f"今天有 {due_count} 道错题到期，先清掉它们再学新内容。"
    elif view["score_delta"] > 0:
        encouragement = f"本周综合分 +{view['score_delta']}，进步看得见。"
    else:
        encouragement = "每天进步一点点，从今日 3 件事开始。"
    encouragement = await _polish_copy(encouragement, "growth_panel_encouragement")

    # 高考倒计时：preferences.gaokao_date 可覆盖默认 2027-06-07
    prefs_rs = await db.execute(
        select(UserProfile.preferences).where(UserProfile.user_id == user_id)
    )
    prefs = prefs_rs.scalar() or {}
    exam_date = _GAOKAO_DEFAULT
    raw_date = prefs.get("gaokao_date")
    if isinstance(raw_date, str):
        with suppress(ValueError):
            exam_date = date.fromisoformat(raw_date)
    countdown = max(0, (exam_date - date.today()).days)

    return _ok(
        {
            "golden_window": {"start": "19:00", "end": "21:00", "label": "晚间黄金记忆窗口"},
            "today_actions": today_actions,
            "week_brief": week_brief,
            "encouragement": encouragement,
            "gaokao_countdown": {"days": countdown, "exam_date": exam_date.isoformat()},
        }
    )


@agent_ext_router.post("/route-intent")
async def route_intent(
    req: RouteIntentRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """功能跳转意图识别（§3.0.3）：关键词规则表 + 知识点别名命中；未命中交对话主链路兜底"""
    user_id = uuid.UUID(user["sub"])
    text = (req.text or "").strip()
    if not text:
        return _ok({"matched": False, "route": None, "route_name": None, "query": {}, "reply": "没听清，再说一次？"})

    # 知识点别名/名称命中（knowledge_points.aliases）→ query.kp
    kp_hit: KnowledgePoint | None = None
    kp_rs = await db.execute(select(KnowledgePoint))
    for kp in kp_rs.scalars().all():
        if kp.name and kp.name in text:
            kp_hit = kp
            break
        if any(a and a in text for a in (kp.aliases or [])):
            kp_hit = kp
            break
    query = {"kp": kp_hit.name} if kp_hit else {}

    # 学情问答类：路由到报告页并附 data_hint 摘要（现算，不调大模型）
    if any(k in text for k in _LEARNING_QA_KEYWORDS):
        rows = await growth_svc.load_mastery_rows(db, user_id)
        week_total, week_correct = await growth_svc.week_answer_stats(db, user_id, 7)
        weakest = min(rows, key=lambda r: r["mastery"]) if rows else None
        hint_parts = []
        if weakest:
            hint_parts.append(
                f"当前最薄弱的是「{weakest['kp_name']}」（掌握度 {round(weakest['mastery'] * 100)}%）"
            )
        if week_total:
            hint_parts.append(
                f"近 7 天作答 {week_total} 题、正确率 {round(week_correct / week_total * 100)}%"
            )
        data_hint = "；".join(hint_parts) or "还没有足够的学习数据，先去做几道题吧"
        return _ok(
            {
                "matched": True,
                "route": "/report",
                "route_name": "学情报告",
                "query": query,
                "data_hint": data_hint,
                "reply": f"{data_hint}，详情见学情报告。",
            }
        )

    # 关键词规则表匹配
    for keywords, route, route_name in _ROUTES:
        if any(k in text for k in keywords):
            reply = f"好的，带你去{route_name}。"
            # 错题本附到期数（读取时 FSRS 计算）
            if route == "/errors":
                error_views = await growth_svc.load_errors_fsrs(db, user_id)
                due = sum(1 for v in error_views if v["is_due"])
                reply = f"好的，带你去错题本，今天有 {due} 道到期。"
            return _ok(
                {"matched": True, "route": route, "route_name": route_name, "query": query, "reply": reply}
            )

    # 仅命中知识点：带去练题中心定向练习
    if kp_hit:
        return _ok(
            {
                "matched": True,
                "route": "/practice",
                "route_name": "练题中心",
                "query": query,
                "reply": f"好的，带你去练「{kp_hit.name}」的题。",
            }
        )

    return _ok(
        {"matched": False, "route": None, "route_name": None, "query": {}, "reply": "这个问题交给 AI 老师来回答吧。"}
    )


# ==================== 模块1 对话学习页 ====================


@router.get("/growth/loop-progress")
async def growth_loop_progress(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当日对话 7 步闭环进度：遇题→引导→作答→判分→变式→收录→行动"""
    user_id = uuid.UUID(user["sub"])
    since = _today_start()

    # 当日引导会话（encounter / socratic 数据源）
    ts_rs = await db.execute(
        select(TutorSession.hint_counts, TutorSession.answer_requests, TutorSession.hint_level).where(
            TutorSession.user_id == user_id,
            TutorSession.deleted_at.is_(None),
            TutorSession.created_at >= since,
        )
    )
    ts_rows = ts_rs.all()
    encounter_count = len(ts_rows)
    socratic_count = sum(
        1
        for hc, ar, hl in ts_rows
        if int(hl or 0) > 0 or int(ar or 0) > 0 or any(int(v or 0) > 0 for v in (hc or {}).values())
    )

    # 当日作答/判分（submission_items 聚合）
    si_rs = await db.execute(
        select(SubmissionItem.verdict)
        .join(Submission, SubmissionItem.submission_id == Submission.id)
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            SubmissionItem.deleted_at.is_(None),
            SubmissionItem.created_at >= since,
        )
    )
    verdicts = [v for (v,) in si_rs.all()]
    answer_count = len(verdicts)
    judge_count = sum(1 for v in verdicts if v in ("correct", "wrong"))

    # 当日错题收录
    err_rs = await db.execute(
        select(func.count(ErrorRecord.id)).where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.deleted_at.is_(None),
            ErrorRecord.created_at >= since,
        )
    )
    record_count = int(err_rs.scalar() or 0)

    # 变式/行动步：loop_step_done 埋点（§4.3 事件约定）
    variant_count = await _loop_step_count(db, user_id, "variant")
    action_count = await _loop_step_count(db, user_id, "action")

    steps = [
        {"key": "encounter", "done": encounter_count > 0, "count": encounter_count},
        {"key": "socratic", "done": socratic_count > 0, "count": socratic_count},
        {"key": "answer", "done": answer_count > 0, "count": answer_count},
        {"key": "judge", "done": judge_count > 0, "count": judge_count},
        {"key": "variant", "done": variant_count > 0, "count": variant_count},
        {"key": "record", "done": record_count > 0, "count": record_count},
        {"key": "action", "done": action_count > 0, "count": action_count},
    ]
    return _ok({"steps": steps, "done_count": sum(1 for s in steps if s["done"]), "total": 7})


# ==================== 模块2 练题中心 ====================


@router.get("/practice/group-recommend")
async def practice_group_recommend(
    count: int = Query(default=5, ge=1, le=30),
    kp_code: str | None = Query(default=None, description="指定知识点（不传=薄弱 Top1）"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日训练组推荐（§3.2.1）：薄弱 Top1（或指定 kp）+ 3:2:1 配比 + BKT 学习率外推"""
    user_id = uuid.UUID(user["sub"])
    rows = await growth_svc.load_mastery_rows(db, user_id)
    mix = _difficulty_mix(count)

    if not rows and not kp_code:
        # 空态：无任何掌握度记录的新用户。
        # 迭代18 修复（5题vs1题不一致）：前端主卡展示"count 题变式"，但无 kp_code 时
        # start() 会落到 daily 每日一题（1 题），卡片与实际题量矛盾。空态改为返回
        # 默认摸底知识点（集合），前端走 special 定向出 count 题，所见即所得。
        from app.models.knowledge_point import KnowledgePoint

        default_kp_row = (
            await db.execute(
                select(KnowledgePoint).where(KnowledgePoint.code == "MATH-G1-SET-101")
            )
        ).scalar_one_or_none()
        if default_kp_row:
            return _ok(
                {
                    "title": "今日训练组 · 摸底训练",
                    "kp_code": default_kp_row.code,
                    "kp_name": default_kp_row.name,
                    "count": count,
                    "est_minutes": count * 4,
                    "mix": mix,
                    "mastery_now": 0.0,
                    "mastery_forecast": round(min(0.95, _BKT_LEARN_RATE * count), 4),
                    "reason": "还没有练习数据，先从集合基础摸底开始，完成后再为你定制训练组。",
                }
            )
        return _ok(
            {
                "title": "今日训练组",
                "kp_code": None,
                "kp_name": None,
                "count": count,
                "est_minutes": count * 4,
                "mix": mix,
                "mastery_now": None,
                "mastery_forecast": None,
                "reason": "还没有练习数据，先完成一组摸底练习，我们再为你定制训练组。",
            }
        )

    # 显式指定 kp_code：按指定知识点查 mastery（没记录按 0 处理）+ 取 kp_name
    if kp_code:
        from app.models.knowledge_point import KnowledgePoint

        kp_row = (
            await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
        ).scalar_one_or_none()
        kp_name = kp_row.name if kp_row else kp_code
        rec = next((r for r in rows if r["kp_code"] == kp_code), None)
        mastery_now = float(rec["mastery"]) if rec else 0.0
        mastery_forecast = round(min(0.95, mastery_now + (1 - mastery_now) * _BKT_LEARN_RATE * count), 4)
        return _ok(
            {
                "title": f"今日训练组 · {kp_name}",
                "kp_code": kp_code,
                "kp_name": kp_name,
                "count": count,
                "est_minutes": count * 4,
                "mix": mix,
                "mastery_now": round(mastery_now, 4),
                "mastery_forecast": mastery_forecast,
                "reason": f"你选了「{kp_name}」（当前掌握度 {round(mastery_now * 100)}%），按 3:2:1 难度配比定向练习。",
            }
        )

    weakest = min(rows, key=lambda r: (r["mastery"], -r["practice_count"]))
    mastery_now = weakest["mastery"]
    # BKT 学习率线性外推：每题推进残余空间的 3%，封顶 0.95
    mastery_forecast = round(
        min(0.95, mastery_now + (1 - mastery_now) * _BKT_LEARN_RATE * count), 4
    )
    return _ok(
        {
            "title": f"今日训练组 · {weakest['kp_name']}",
            "kp_code": weakest["kp_code"],
            "kp_name": weakest["kp_name"],
            "count": count,
            "est_minutes": count * 4,
            "mix": mix,
            "mastery_now": round(mastery_now, 4),
            "mastery_forecast": mastery_forecast,
            "reason": f"「{weakest['kp_name']}」当前掌握度 {round(mastery_now * 100)}%，是最薄弱环节，按 3:2:1 难度配比定向突破。",
        }
    )


@router.get("/practice/difficulty-mix")
async def practice_difficulty_mix(
    count: int = Query(default=5, ge=1, le=30),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """难度配比统计（§3.2.2）：3:2:1 配比 + 近 21 天作答行为解释"""
    user_id = uuid.UUID(user["sub"])
    mix = _difficulty_mix(count)
    items = [
        {"level": level, "count": mix[level], "ratio": round(mix[level] / count, 4)}
        for level in ("easy", "medium", "hard")
    ]
    total, correct = await growth_svc.week_answer_stats(db, user_id, 21)
    if total:
        explanation = (
            f"近 21 天你作答 {total} 题、正确率 {round(correct / total * 100)}%，"
            "按 3:2:1 由易到难配比，先稳基础再冲压轴。"
        )
    else:
        explanation = "暂无近期作答数据，先按标准 3:2:1 配比开练，系统会随你的表现动态调整。"
    return _ok({"items": items, "explanation": explanation})


@router.get("/practice/smart-score")
async def practice_smart_score(
    submission_id: str = Query(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SmartScore（§3.2.3）：100*(0.5*正确率 + 0.3*(1-提示依赖) + 0.2*连对系数)"""
    user_id = uuid.UUID(user["sub"])
    try:
        sid = uuid.UUID(submission_id)
    except ValueError:
        return {"code": 40001, "message": "非法 submission_id"}

    sub = await db.get(Submission, sid)
    # 越权不泄露存在性（对齐 student_router 纪律）
    if sub is None or sub.deleted_at or sub.user_id != user_id:
        return {"code": 40400, "message": "提交记录不存在"}

    items_rs = await db.execute(
        select(SubmissionItem.verdict)
        .where(SubmissionItem.submission_id == sid, SubmissionItem.deleted_at.is_(None))
        .order_by(SubmissionItem.item_no)
    )
    verdicts = [v for (v,) in items_rs.all()]
    total = len(verdicts)
    correct = sum(1 for v in verdicts if v == "correct")
    accuracy = correct / total if total else 0.0

    # 连对系数：本次提交内连续正确最长段 / 题数
    longest = run = 0
    for v in verdicts:
        run = run + 1 if v == "correct" else 0
        longest = max(longest, run)
    streak_coef = longest / total if total else 0.0

    # 独立性：1 - 用户历史提示依赖度（mastery_summary 口径）
    hint_dep = await growth_svc.hint_dependency(db, user_id)
    independence = 1 - min(hint_dep, 1.0)

    smart_score = max(0, min(100, round(100 * (0.5 * accuracy + 0.3 * independence + 0.2 * streak_coef))))
    return _ok(
        {
            "smart_score": smart_score,
            "breakdown": {
                "accuracy": round(accuracy, 4),
                # speed 维度无计时数据，以连对系数近似（前端 ± 规则按 breakdown 换算）
                "speed": round(streak_coef, 4),
                "independence": round(independence, 4),
            },
        }
    )


@router.get("/practice/summary")
async def practice_summary(
    quiz_id: str = Query(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """训练总结（§3.2.4）：该 quiz 涉及 kp 的训练前后快照差值分级"""
    user_id = uuid.UUID(user["sub"])
    try:
        qid = uuid.UUID(quiz_id)
    except ValueError:
        return {"code": 40001, "message": "非法 quiz_id"}

    quiz = await db.get(Quiz, qid)
    if quiz is None or quiz.deleted_at or quiz.user_id != user_id:
        return {"code": 40400, "message": "题组不存在"}

    # quiz_items 涉及的 kp（优先取题目级标注，兜底题组级 kp_codes）
    items_rs = await db.execute(
        select(QuizItem.kp_code).where(
            QuizItem.quiz_id == qid,
            QuizItem.deleted_at.is_(None),
            QuizItem.kp_code.is_not(None),
        )
    )
    kp_codes = sorted({c for (c,) in items_rs.all() if c})
    if not kp_codes:
        kp_codes = sorted({c for c in (quiz.kp_codes or []) if c})

    quiz_date = (quiz.created_at or datetime.now(UTC)).date()
    name_map = await growth_svc.kp_name_map(db, kp_codes)

    upgraded, flat, downgraded = [], [], []
    for code in kp_codes:
        before = await _snapshot_near(db, user_id, code, quiz_date, "before")
        after = await _snapshot_near(db, user_id, code, quiz_date, "after")
        # 无训练前快照 → BKT 先验 0.5；无训练后快照 → 视为持平
        from_v = before if before is not None else 0.5
        to_v = after if after is not None else from_v
        entry = {
            "kp_code": code,
            "kp_name": name_map.get(code),
            "from": round(from_v, 4),
            "to": round(to_v, 4),
        }
        delta = to_v - from_v
        if delta > 0.05:
            upgraded.append(entry)
        elif delta < -0.05:
            downgraded.append(entry)
        else:
            flat.append(entry)

    if downgraded:
        recommendation = f"「{downgraded[0]['kp_name'] or downgraded[0]['kp_code']}」掌握度出现回退，建议先到错题本复习相关错题，再针对性巩固。"
    elif upgraded:
        recommendation = f"本轮训练有效，「{upgraded[0]['kp_name'] or upgraded[0]['kp_code']}」提升明显，明天可以继续推进相邻知识点。"
    else:
        recommendation = "本轮掌握度基本持平，建议加大题量或提高难度档再练一轮。"

    return _ok(
        {"upgraded": upgraded, "flat": flat, "downgraded": downgraded, "recommendation": recommendation}
    )


# ==================== 模块3 错题本（FSRS 增强层） ====================


@router.get("/error-records/memory-heatmap")
async def error_memory_heatmap(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """FSRS 记忆稳定性热力图（§3.3.1）：以今日为终点的 4 周 × 7 天网格

    每道未删错题按最近活动时间 + stability 推算"衰减到 R=0.6 的到期日"落格；
    格内 level 取该日落点 R 最低值定级；record_ids 全收。
    """
    user_id = uuid.UUID(user["sub"])
    views = await growth_svc.load_errors_fsrs(db, user_id)

    today = date.today()
    start = today - timedelta(days=27)  # 4 周 × 7 天，今日收尾
    # 落点聚合：格索引 → {min_s, ids}
    # 格子等级按稳定度 S 分级（R 在到期日落点恒等于阈值，按 R 分级整图恒为 decay）
    buckets: dict[int, dict] = {}
    for v in views:
        rec = v["record"]
        stability = v["stability"]
        base_dt = rec.updated_at or rec.created_at
        base_date = base_dt.date() if base_dt else today
        # 到期日落点：从最近活动日起，R 衰减到 0.6 所需天数
        due_date = base_date + timedelta(days=round(fsrs.days_until(stability, fsrs.DECAY_THRESHOLD)))
        # 超出网格的落点钳制进网格（早已过期的归首格，未来到期的归今日格），保证不丢题
        clamped = min(max(due_date, start), today)
        idx = (clamped - start).days
        cell = buckets.setdefault(idx, {"min_s": float("inf"), "ids": []})
        cell["min_s"] = min(cell["min_s"], stability)
        cell["ids"].append(str(rec.id))

    cells = []
    for i in range(28):
        d = start + timedelta(days=i)
        bucket = buckets.get(i)
        if bucket:
            level = fsrs.fsrs_level(bucket["min_s"])
            record_ids = bucket["ids"]
        else:
            level = "empty"
            record_ids = []
        cells.append(
            {
                "week": i // 7,
                "weekday": d.weekday(),  # 周一=0
                "level": level,
                "record_ids": record_ids,
                "count": len(record_ids),
            }
        )
    return _ok({"weeks": 4, "days": 7, "cells": cells})


@router.get("/error-records/due-queue")
async def error_due_queue(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """到期错题队列（§3.3.2）：retrievability 升序，"再不做就忘"程度最高在前"""
    user_id = uuid.UUID(user["sub"])
    views = await growth_svc.load_errors_fsrs(db, user_id)
    due = [v for v in views if v["is_due"]]
    due.sort(key=lambda v: (v["retrievability"], v["hours_to_forget"]))

    name_map = await growth_svc.kp_name_map(db, [v["record"].kp_code for v in due])
    items = []
    for seq, v in enumerate(due, start=1):
        rec = v["record"]
        items.append(
            {
                "record_id": str(rec.id),
                "seq": seq,
                "question_preview": _preview(rec.question_text, 60),
                "kp_code": rec.kp_code,
                "kp_name": name_map.get(rec.kp_code),
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
                "wrong_count": v["wrong_count"],
                "review_count": int(rec.review_count or 0),
                "retrievability": v["retrievability"],
                "hours_to_forget": v["hours_to_forget"],
                "urgency_label": _urgency_label(v["retrievability"]),
            }
        )
    return _ok({"total": len(items), "items": items})


@router.get("/error-records/{record_id}/detail")
async def error_record_detail(
    record_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题详情扩展（§3.3.3）：原有字段 + FSRS 视图"""
    user_id = uuid.UUID(user["sub"])
    record = await db.get(ErrorRecord, record_id)
    # 越权不泄露存在性（对齐 student_router 纪律）
    if record is None or record.deleted_at or record.user_id != user_id:
        return {"code": 40400, "message": "错题记录不存在"}

    view = growth_svc.enrich_error_fsrs(record)
    name_map = await growth_svc.kp_name_map(db, [record.kp_code])
    kp_name = name_map.get(record.kp_code)
    variants_hint = (
        f"建议针对「{kp_name}」做 3 道变式题巩固。"
        if kp_name
        else "建议围绕本题考点做 3 道变式题巩固。"
    )
    return _ok(
        {
            "record_id": str(record.id),
            "question_text": record.question_text,
            "image": normalize_error_assets(record.image),  # 题目配图（规范 image / ggb）
            "file_id": str(record.file_id) if record.file_id else None,  # 手动拍照入本的原图（前端按需解析内容 URL）
            "answer_text": record.answer_text,
            "error_type": record.error_type,
            "kp_code": record.kp_code,
            "kp_name": kp_name,
            "source_channel": record.source_channel,
            "origin": getattr(record, "origin", None),  # om5：来源细分（自测/引导/模考/变式…）
            "ai_judged": record.ai_judged,
            "note": record.note,
            "next_review_at": record.next_review_at.isoformat() if record.next_review_at else None,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            # ===== FSRS 扩展字段 =====
            "entered_at": record.created_at.isoformat() if record.created_at else None,
            "wrong_count": view["wrong_count"],
            "review_count": int(record.review_count or 0),
            "memory_stability": view["stability"],
            "retrievability": view["retrievability"],
            "fsrs_level": view["level"],
            "variants_hint": variants_hint,
        }
    )


@router.get("/error-records/filter")
async def error_record_filter(
    error_type: str | None = Query(default=None),
    kp_code: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    stability: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=50),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """错题多维筛选（§3.3.4）：错因/知识点/日期区间/稳定性三档 + 分页"""
    user_id = uuid.UUID(user["sub"])

    # 参数校验（沿用项目错误码规约）
    if error_type is not None and error_type not in _VALID_ERROR_TYPES:
        return {"code": 40001, "message": f"无效错因类型: {error_type}"}
    if stability is not None and stability not in _VALID_STABILITY:
        return {"code": 40001, "message": f"无效稳定性档位: {stability}，仅支持 stable/decaying/critical"}
    try:
        d_from = date.fromisoformat(date_from) if date_from else None
        d_to = date.fromisoformat(date_to) if date_to else None
    except ValueError:
        return {"code": 40001, "message": "日期格式须为 YYYY-MM-DD"}

    conds = [ErrorRecord.user_id == user_id, ErrorRecord.deleted_at.is_(None)]
    if error_type:
        conds.append(ErrorRecord.error_type == error_type)
    if kp_code:
        conds.append(ErrorRecord.kp_code == kp_code)
    if d_from:
        conds.append(ErrorRecord.created_at >= datetime.combine(d_from, time.min, tzinfo=UTC))
    if d_to:
        conds.append(ErrorRecord.created_at < datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=UTC))

    rs = await db.execute(select(ErrorRecord).where(*conds).order_by(ErrorRecord.created_at.desc()))
    records = rs.scalars().all()

    # FSRS 读取时计算；stability 档位在计算后过滤（列缓存未全量回填前保证口径一致）
    views = [growth_svc.enrich_error_fsrs(rec) for rec in records]
    if stability:
        views = [v for v in views if fsrs.level_to_filter(v["level"]) == stability]

    total = len(views)
    page_views = views[(page - 1) * size : page * size]
    name_map = await growth_svc.kp_name_map(db, [v["record"].kp_code for v in page_views])
    items = [
        {
            "record_id": str(v["record"].id),
            "question_preview": _preview(v["record"].question_text, 120),
            "error_type": v["record"].error_type,
            "kp_code": v["record"].kp_code,
            "kp_name": name_map.get(v["record"].kp_code),
            "created_at": v["record"].created_at.isoformat() if v["record"].created_at else None,
            "review_count": int(v["record"].review_count or 0),
            "retrievability": v["retrievability"],
            "fsrs_level": v["level"],
            "stability": fsrs.level_to_filter(v["level"]),
        }
        for v in page_views
    ]
    return _ok({"items": items, "total": total, "page": page, "size": size})


# ==================== 模块4 学情报告 ====================


@router.get("/report/highlights")
@_cache_report("highlights")
async def report_highlights(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """本周亮点（§3.4.1）：候选规则按证据强度取 3，每项附真实数字"""
    user_id = uuid.UUID(user["sub"])
    candidates: list[dict] = []

    # ① 周环比掌握度提升最大的 kp（近 7 天均值 vs 前 7 天均值）
    today = date.today()
    snap_rs = await db.execute(
        select(MasterySnapshot.kp_code, MasterySnapshot.date, MasterySnapshot.mastery).where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.date >= today - timedelta(days=13),
        )
    )
    cur: dict[str, list[float]] = {}
    prev: dict[str, list[float]] = {}
    for code, d, m in snap_rs.all():
        (cur if d >= today - timedelta(days=6) else prev).setdefault(code, []).append(float(m))
    best_code, best_gain = None, 0.0
    for code, cur_vals in cur.items():
        if code in prev and prev[code]:
            gain = sum(cur_vals) / len(cur_vals) - sum(prev[code]) / len(prev[code])
            if gain > best_gain:
                best_code, best_gain = code, gain
    if best_code:
        name_map = await growth_svc.kp_name_map(db, [best_code])
        candidates.append(
            {
                "icon": "进步趋势",
                "title": "掌握度提升最快",
                "desc": f"「{name_map.get(best_code) or best_code}」掌握度周环比 +{round(best_gain * 100)}%。",
            }
        )

    # ② 独立解题率周环比提升（提示/要答案事件 ÷ 作答数）
    hint7 = await growth_svc.event_count(db, user_id, ["hint_used", "answer_requested"], 7)
    hint14 = await growth_svc.event_count(db, user_id, ["hint_used", "answer_requested"], 14)
    ans7, _ = await growth_svc.week_answer_stats(db, user_id, 7)
    ans14, _ = await growth_svc.week_answer_stats(db, user_id, 14)
    rate_now = 1 - hint7 / max(1, ans7)
    rate_prev = 1 - (hint14 - hint7) / max(1, ans14 - ans7) if ans14 > ans7 else None
    if rate_prev is not None and rate_now - rate_prev > 0.01:
        candidates.append(
            {
                "icon": "独立解题",
                "title": "独立解题率提升",
                "desc": f"本周独立解题率 {round(rate_now * 100)}%，较上周 +{round((rate_now - rate_prev) * 100)}%。",
            }
        )

    # ③ 连击 ≥3 天
    streak = await growth_svc.current_streak(db, user_id)
    if streak >= 3:
        candidates.append(
            {
                "icon": "连续学习",
                "title": "学习连击",
                "desc": f"已连续学习 {streak} 天，节奏稳定。",
            }
        )

    # ④ 错题复习完成率 100%（在册有错 && 当前无 FSRS 到期）
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    if error_views and not any(v["is_due"] for v in error_views):
        candidates.append(
            {
                "icon": "复习完成",
                "title": "错题清零",
                "desc": f"在册 {len(error_views)} 道错题全部在记忆安全区，到期复习完成率 100%。",
            }
        )

    # ⑤ 单 kp 正确率突破 0.7（练习量 ≥5 防小样本噪声）
    rows = await growth_svc.load_mastery_rows(db, user_id)
    for r in rows:
        if r["practice_count"] >= 5 and r["correct_count"] / r["practice_count"] >= 0.7:
            candidates.append(
                {
                    "icon": "正确率突破",
                    "title": "正确率突破",
                    "desc": f"「{r['kp_name']}」累计正确率达 {round(r['correct_count'] / r['practice_count'] * 100)}%（{r['practice_count']} 题样本）。",
                }
            )
            break

    # 空态兜底：3 条通用模板（前端空态展示约定）
    if not candidates:
        candidates = [
            {"icon": "进步趋势", "title": "开始积累", "desc": "还没有足够数据，先完成今天的练习吧。"},
            {"icon": "连续学习", "title": "建立节奏", "desc": "连续学习 3 天即可点亮连击亮点。"},
            {"icon": "复习完成", "title": "错题复盘", "desc": "收录并复习错题，这里会展示你的复习成果。"},
        ]
    return _ok({"items": candidates[:3]})


@router.get("/report/weak-points")
@_cache_report("weak-points")
async def report_weak_points(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """薄弱环节 Top4（§3.4.2）：掌握度升序取弱项，强项卡仅在不足 4 个时补齐"""
    user_id = uuid.UUID(user["sub"])
    rows = await growth_svc.load_mastery_rows(db, user_id)
    rows.sort(key=lambda r: r["mastery"])

    weak = [r for r in rows if r["mastery"] < _MASTERY_HIGH][:4]
    if len(weak) < 4:
        # 强项卡补齐（掌握度 ≥0.7，降序取强）
        strong = sorted((r for r in rows if r["mastery"] >= _MASTERY_HIGH), key=lambda r: -r["mastery"])
        weak.extend(strong[: 4 - len(weak)])

    items = []
    for r in weak:
        code = r["kp_code"]
        mastery = r["mastery"]
        level = "err" if mastery < _MASTERY_LOW else ("warn" if mastery < _MASTERY_HIGH else "ok")

        # ai_reason：该 kp 错题五类错因众数 + 细分关键词模板（默认离线模板；
        # GROWTH_LLM_POLISH 开启时 LLM 润色，失败回退模板）
        err_rs = await db.execute(
            select(ErrorRecord.error_type, ErrorRecord.question_text, ErrorRecord.note).where(
                ErrorRecord.user_id == user_id,
                ErrorRecord.kp_code == code,
                ErrorRecord.deleted_at.is_(None),
            )
        )
        err_rows = err_rs.all()
        if err_rows:
            type_counts: dict[str, int] = {}
            corpus_parts = []
            for et, qt, note in err_rows:
                if et in _VALID_ERROR_TYPES:
                    type_counts[et] = type_counts.get(et, 0) + 1
                corpus_parts.append(" ".join(p for p in (qt, note) if p))
            if type_counts:
                mode_type = max(type_counts, key=lambda k: type_counts[k])
                _, subtype_zh, _ = growth_svc.classify_subtype(mode_type, " ".join(corpus_parts))
                ai_reason = (
                    f"该知识点错题集中在「{growth_svc.ERROR_TYPE_ZH[mode_type]}」（{subtype_zh}），"
                    f"共 {len(err_rows)} 道在册，建议先回归概念再刷题。"
                )
            else:
                ai_reason = f"该知识点有 {len(err_rows)} 道在册错题但错因未标注，建议先复习错题归类。"
        else:
            ai_reason = f"练习样本 {r['practice_count']} 次，掌握度 {round(mastery * 100)}%，建议针对性练习巩固。"
        ai_reason = await _polish_copy(ai_reason, "weak_point_reason")

        items.append(
            {
                "kp_code": code,
                "kp_name": r["kp_name"],
                "mastery": round(mastery, 4),
                "level": level,
                "ai_reason": ai_reason,
                "primary_action": {"label": "针对练习", "route": f"/practice?kp={code}", "minutes": 15},
                "secondary_action": {"label": "查看图谱", "route": f"/graph?kp={code}"},
            }
        )
    return _ok({"items": items})


@router.get("/report/mastery-trend-forecast")
async def report_mastery_trend_forecast(
    days: int = Query(default=14, ge=1, le=60),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """14 天趋势 + 遗忘预测（§3.4.3）：history 日均值；forecast 按 FSRS 遗忘曲线推算 7 天"""
    user_id = uuid.UUID(user["sub"])
    history = await growth_svc.daily_mastery_avg(db, user_id, days)

    # R0：最近一个有快照的日均值；无快照退回当前掌握度均值；再退 0
    r0 = 0.0
    for point in reversed(history):
        if point["mastery"] is not None:
            r0 = point["mastery"]
            break
    else:
        rows = await growth_svc.load_mastery_rows(db, user_id)
        if rows:
            r0 = sum(r["mastery"] for r in rows) / len(rows)

    # S 取用户错题 stability 中位数（无错题默认 14 天）
    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    stabilities = sorted(v["stability"] for v in error_views)
    if stabilities:
        mid = len(stabilities) // 2
        s_median = (
            stabilities[mid]
            if len(stabilities) % 2
            else (stabilities[mid - 1] + stabilities[mid]) / 2
        )
    else:
        s_median = 14.0

    today = date.today()
    forecast = [
        {
            "date": (today + timedelta(days=t)).isoformat(),
            "mastery": round(r0 * fsrs.retrievability(t, s_median), 4),
        }
        for t in range(1, 8)
    ]
    return _ok({"history": history, "forecast": forecast})


@router.get("/report/error-distribution")
@_cache_report("error-distribution")
async def report_error_distribution(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """12 类思维漏洞分布（§3.4.4）：五类权威分类 → 展示层 12 细分归桶，按 count 降序"""
    user_id = uuid.UUID(user["sub"])
    rs = await db.execute(
        select(ErrorRecord.error_type, ErrorRecord.question_text, ErrorRecord.note).where(
            ErrorRecord.user_id == user_id, ErrorRecord.deleted_at.is_(None)
        )
    )
    counts: dict[tuple[str, str, str], int] = {}
    total = 0
    for error_type, question_text, note in rs.all():
        subtype, subtype_zh, parent = growth_svc.classify_subtype(
            error_type, " ".join(p for p in (question_text, note) if p)
        )
        counts[(subtype, subtype_zh, parent)] = counts.get((subtype, subtype_zh, parent), 0) + 1
        total += 1

    items = [
        {
            "type": subtype,
            "type_zh": subtype_zh,
            "count": cnt,
            "ratio": round(cnt / total, 4) if total else 0.0,
            "parent_type": parent,
        }
        for (subtype, subtype_zh, parent), cnt in counts.items()
    ]
    items.sort(key=lambda x: -x["count"])
    return _ok({"items": items, "total": total})


@router.get("/report/honesty")
@_cache_report("honesty")
async def report_honesty(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """诚实提示（§3.4.5）：提示依赖客观陈述，模板化不评判"""
    user_id = uuid.UUID(user["sub"])

    # 提示数：events 埋点 + tutor_sessions 提示阶梯/直接要答案
    async def _tutor_hints(since: datetime) -> int:
        rs = await db.execute(
            select(TutorSession.hint_counts, TutorSession.answer_requests).where(
                TutorSession.user_id == user_id,
                TutorSession.deleted_at.is_(None),
                TutorSession.created_at >= since,
            )
        )
        total = 0
        for hc, ar in rs.all():
            total += sum(int(v or 0) for v in (hc or {}).values()) + int(ar or 0)
        return total

    since7 = _today_start() - timedelta(days=6)
    since14 = _today_start() - timedelta(days=13)
    hint7 = await growth_svc.event_count(db, user_id, ["hint_used", "answer_requested"], 7) + await _tutor_hints(since7)
    hint14 = await growth_svc.event_count(db, user_id, ["hint_used", "answer_requested"], 14) + await _tutor_hints(since14)

    ans7, _ = await growth_svc.week_answer_stats(db, user_id, 7)
    ans14, _ = await growth_svc.week_answer_stats(db, user_id, 14)
    ans_prev = max(0, ans14 - ans7)

    rate_now = max(0.0, min(1.0, 1 - hint7 / max(1, ans7)))
    rate_prev = max(0.0, min(1.0, 1 - (hint14 - hint7) / max(1, ans_prev))) if ans_prev else rate_now
    fluctuation = round(rate_now - rate_prev, 4)

    message = f"近 7 天使用提示或直接要答案共 {hint7} 次，独立解题率 {round(rate_now * 100)}%。"
    if rate_now < 0.5:
        suggestion = "建议先独立思考 3 分钟再要提示，提示只看不抄思路更稳。"
    elif fluctuation < -0.1:
        suggestion = "独立性较上周有所下降，做题时可以先遮住提示区试试。"
    else:
        suggestion = "独立思考节奏不错，继续保持。"

    return _ok(
        {
            "hint_count_week": hint7,
            "independent_rate_now": round(rate_now, 4),
            "independent_rate_prev": round(rate_prev, 4),
            "fluctuation": fluctuation,
            "message": message,
            "suggestion": suggestion,
        }
    )


# ==================== 模块5 知识图谱 ====================


async def _kp_prereq_edges(db: AsyncSession) -> list[tuple[str, str]]:
    """全部前置依赖边 (kp_code, prereq_code)"""
    rs = await db.execute(select(KpPrerequisite.kp_code, KpPrerequisite.prereq_code))
    return [(a, b) for a, b in rs.all()]


def _prereq_chain(edges: list[tuple[str, str]], kp_code: str, max_depth: int = 4) -> list[str]:
    """沿 kp_prerequisites 反向 BFS（深度 ≤4），返回有序去重的前置链（不含起点）"""
    adj: dict[str, list[str]] = {}
    for code, prereq in edges:
        adj.setdefault(code, []).append(prereq)
    chain: list[str] = []
    visited = {kp_code}
    frontier = [kp_code]
    for _ in range(max_depth):
        nxt = []
        for node in frontier:
            for pre in adj.get(node, []):
                if pre not in visited:
                    visited.add(pre)
                    chain.append(pre)
                    nxt.append(pre)
        if not nxt:
            break
        frontier = nxt
    return chain


@router.get("/knowledge-graph/pie")
async def knowledge_graph_pie(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """ALEKS 学习版图（§3.5.1）：四态分级 + 掌握速率外推 ETA"""
    user_id = uuid.UUID(user["sub"])
    kps = await _real_kps(db)
    total = len(kps)

    rows = await growth_svc.load_mastery_rows(db, user_id)
    mastery_by_code = {r["kp_code"]: r["mastery"] for r in rows}

    mastered = consolidating = critical = 0
    for kp in kps:
        m = mastery_by_code.get(kp.code)
        if m is None:
            continue
        if m >= _MASTERY_HIGH:
            mastered += 1
        elif m >= _MASTERY_LOW:
            consolidating += 1
        else:
            critical += 1
    unlearned = total - mastered - consolidating - critical

    def _bucket(n: int) -> dict:
        return {"count": n, "ratio": round(n / total, 4) if total else 0.0}

    center_text = f"已掌握 {round(mastered / total * 100)}%" if total else "暂无知识点"

    # ETA：近 14 天每周新掌握节点速率外推（新掌握 = 窗口内出现 ≥0.7 快照且窗口前从未 ≥0.7）
    today = date.today()
    recent_rs = await db.execute(
        select(MasterySnapshot.kp_code)
        .where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.date >= today - timedelta(days=13),
            MasterySnapshot.mastery >= _MASTERY_HIGH,
        )
        .group_by(MasterySnapshot.kp_code)
    )
    recent_mastered = {c for (c,) in recent_rs.all()}
    before_rs = await db.execute(
        select(MasterySnapshot.kp_code)
        .where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.date < today - timedelta(days=13),
            MasterySnapshot.mastery >= _MASTERY_HIGH,
        )
        .group_by(MasterySnapshot.kp_code)
    )
    before_mastered = {c for (c,) in before_rs.all()}
    rate_per_week = len(recent_mastered - before_mastered) / 2  # 14 天 → 每周

    def _eta(target_ratio: float) -> int | None:
        if rate_per_week <= 0 or not total:
            return None  # 无速率：前端展示缺省文案
        remaining = max(0.0, target_ratio * total - mastered)
        return round(remaining / rate_per_week + 0.5)  # 向上取整（周）

    return _ok(
        {
            "total": total,
            "mastered": _bucket(mastered),
            "consolidating": _bucket(consolidating),
            "critical": _bucket(critical),
            "unlearned": _bucket(unlearned),
            "center_text": center_text,
            "eta": {"to_50pct_weeks": _eta(0.5), "to_80pct_weeks": _eta(0.8)},
        }
    )


@router.get("/knowledge-graph/tree")
async def knowledge_graph_tree(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """章节树形视图（§3.5.2）：章节分组 + 四态状态色 + 依赖出入度定 shape

    注：knowledge_points 无独立章节字段，章节取 parent_id 链的根祖先节点。
    """
    user_id = uuid.UUID(user["sub"])
    kps = await _real_kps(db)
    by_id = {kp.id: kp for kp in kps}

    # parent_id 链 → 根祖先（章节）；防御环，最多上溯 10 层
    def _root(kp: KnowledgePoint) -> KnowledgePoint:
        node, depth = kp, 0
        while node.parent_id in by_id and depth < 10:
            node = by_id[node.parent_id]
            depth += 1
        return node

    rows = await growth_svc.load_mastery_rows(db, user_id)
    mastery_by_code = {r["kp_code"]: r["mastery"] for r in rows}

    # shape 规则（kp_prerequisites 出入度）：无前置 circle；有前置且被依赖 diamond；其余 hex
    edges = await _kp_prereq_edges(db)
    has_prereq = {code for code, _ in edges}
    depended_on = {prereq for _, prereq in edges}

    def _shape(code: str) -> str:
        if code not in has_prereq:
            return "circle"
        if code in depended_on:
            return "diamond"
        return "hex"

    chapters_map: dict[str, dict] = {}
    for kp in kps:
        root = _root(kp)
        mastery = mastery_by_code.get(kp.code)
        node = {
            "kp_code": kp.code,
            "name": kp.name,
            "mastery": round(mastery, 4) if mastery is not None else None,
            "state": _mastery_state(mastery),
            "shape": _shape(kp.code),
        }
        ch = chapters_map.setdefault(root.code, {"chap": root.code, "title": root.name, "nodes": []})
        ch["nodes"].append(node)

    chapters = []
    for ch in sorted(chapters_map.values(), key=lambda c: c["chap"]):
        nodes = sorted(ch["nodes"], key=lambda n: n["kp_code"])
        mastered_n = sum(1 for n in nodes if n["state"] == "mastered")
        chapters.append(
            {
                "chap": ch["chap"],
                "title": ch["title"],
                "count_text": f"已掌握 {mastered_n}/{len(nodes)}",
                "nodes": nodes,
            }
        )
    return _ok({"chapters": chapters})


@router.get("/knowledge-graph/nodes/{kp_code}/deps")
async def knowledge_node_deps(
    kp_code: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """追根溯源依赖链（§3.5.3）：kp_prerequisites 反向 BFS 深度 ≤4，取链上掌握度最低节点"""
    user_id = uuid.UUID(user["sub"])
    kp_rs = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    if kp_rs.scalar_one_or_none() is None:
        return {"code": 40400, "message": "知识点不存在"}

    edges = await _kp_prereq_edges(db)
    chain_codes = _prereq_chain(edges, kp_code)

    rows = await growth_svc.load_mastery_rows(db, user_id)
    mastery_by_code = {r["kp_code"]: r["mastery"] for r in rows}
    name_map = await growth_svc.kp_name_map(db, chain_codes)

    chain = []
    weakest_prereq = None
    weakest_val = 2.0  # 掌握度最大 1.0，未学按 0 计
    for code in chain_codes:
        mastery = mastery_by_code.get(code)
        chain.append(
            {
                "kp_code": code,
                "kp_name": name_map.get(code),
                "mastery": round(mastery, 4) if mastery is not None else None,
                "state": _mastery_state(mastery),
            }
        )
        val = mastery if mastery is not None else 0.0
        if val < weakest_val:
            weakest_val = val
            weakest_prereq = code
    return _ok({"kp_code": kp_code, "chain": chain, "weakest_prereq": weakest_prereq})


@router.get("/knowledge-graph/nodes/{kp_code}/recommend")
async def knowledge_node_recommend(
    kp_code: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """节点学习推荐（§3.5.4）：存在掌握度 <0.4 的前置 → prereq_first，否则 direct_practice"""
    user_id = uuid.UUID(user["sub"])
    kp_rs = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    kp = kp_rs.scalar_one_or_none()
    if kp is None:
        return {"code": 40400, "message": "知识点不存在"}

    edges = await _kp_prereq_edges(db)
    chain_codes = _prereq_chain(edges, kp_code)
    rows = await growth_svc.load_mastery_rows(db, user_id)
    mastery_by_code = {r["kp_code"]: r["mastery"] for r in rows}
    name_map = await growth_svc.kp_name_map(db, chain_codes)

    # 链上最弱前置（未学按 0 计）
    weakest_code, weakest_val = None, 2.0
    for code in chain_codes:
        val = mastery_by_code.get(code)
        val = val if val is not None else 0.0
        if val < weakest_val:
            weakest_val, weakest_code = val, code

    if weakest_code is not None and weakest_val < _MASTERY_LOW:
        prereq_name = name_map.get(weakest_code) or weakest_code
        return _ok(
            {
                "strategy": "prereq_first",
                "reason": f"前置「{prereq_name}」掌握度仅 {round(weakest_val * 100)}%，直接攻本节点效率低，建议先补前置。",
                "action_label": f"先学「{prereq_name}」",
                "route": f"/practice?kp={weakest_code}",
                "minutes": 20,
            }
        )
    return _ok(
        {
            "strategy": "direct_practice",
            "reason": "前置均已达标，可以直接针对本节点开练。",
            "action_label": "开始针对练习",
            "route": f"/practice?kp={kp_code}",
            "minutes": 15,
        }
    )


@router.get("/knowledge-graph/nodes/{kp_code}/errors")
async def knowledge_node_errors(
    kp_code: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """节点关联错题（任务书 §20 error_related 关系）：学生在本知识点下的错题。

    精确码匹配（error_records.kp_code == 节点码）；存量父码错题（derivative 等）
    不属任何教材节点，不出现在本面板——它们仍在错题本列表/筛选中，绝不模糊猜测归属。
    """
    user_id = uuid.UUID(user["sub"])
    kp_rs = await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
    if kp_rs.scalar_one_or_none() is None:
        return {"code": 40400, "message": "知识点不存在"}

    rows = await db.execute(
        select(ErrorRecord)
        .where(
            ErrorRecord.user_id == user_id,
            ErrorRecord.deleted_at.is_(None),
            ErrorRecord.kp_code == kp_code,
        )
        .order_by(ErrorRecord.created_at.desc())
        .limit(20)
    )
    items = [
        {
            "record_id": str(r.id),
            "question_text": (r.question_text or "")[:80],
            "error_type": r.error_type,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows.scalars()
    ]
    return _ok({"kp_code": kp_code, "total": len(items), "items": items})


# ==================== 模块6 学情总览 ====================


@router.get("/growth/today-3")
async def growth_today_3(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日 3 件事（模块6）：组1 = 到期错题 + 薄弱 Top1 变式 + 压轴挑战；组2 备选"""
    user_id = uuid.UUID(user["sub"])

    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    due = sorted(
        (v for v in error_views if v["is_due"]),
        key=lambda v: v["retrievability"],
    )
    rows = await growth_svc.load_mastery_rows(db, user_id)
    weak_rows = sorted(rows, key=lambda r: r["mastery"])

    # done 判定：当日事件/作答（复习步=loop_step_done[review]，练习步=当日有提交，挑战步=loop_step_done[challenge]）
    done_review = await _loop_step_count(db, user_id, "review") > 0
    done_challenge = await _loop_step_count(db, user_id, "challenge") > 0
    since = _today_start()
    ans_rs = await db.execute(
        select(func.count(SubmissionItem.id))
        .join(Submission, SubmissionItem.submission_id == Submission.id)
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            SubmissionItem.deleted_at.is_(None),
            SubmissionItem.created_at >= since,
        )
    )
    done_practice = int(ans_rs.scalar() or 0) > 0

    # 组1：FSRS 到期错题 + 薄弱 Top1 变式 + 压轴挑战
    due_n = len(due)
    top_weak = weak_rows[0] if weak_rows else None
    group1 = [
        {
            "key": "review_errors",
            "title": "复习到期错题",
            "why": f"{due_n} 道错题即将遗忘" if due_n else "今日无到期错题",
            "est_minutes": 10,
            "benefit": "稳住记忆曲线，防止回生",
            "route": "/errors",
            "done": done_review,
        },
        {
            "key": "variant_top1",
            "title": f"「{top_weak['kp_name']}」变式训练" if top_weak else "摸底练习",
            "why": (
                f"掌握度仅 {round(top_weak['mastery'] * 100)}%，是当前最弱点"
                if top_weak
                else "还没有练习数据，先摸底"
            ),
            "est_minutes": 15,
            "benefit": "定向突破最薄弱知识点",
            "route": f"/practice?kp={top_weak['kp_code']}" if top_weak else "/practice",
            "done": done_practice,
        },
        {
            "key": "challenge",
            "title": "压轴挑战",
            "why": "冲刺高分题感",
            "est_minutes": 12,
            "benefit": "拉开区分度，冲击目标分",
            "route": "/practice?mode=challenge",
            "done": done_challenge,
        },
    ]

    # 组2 备选：次薄弱 kp + 次到期错题 + 图谱回顾
    second_weak = weak_rows[1] if len(weak_rows) > 1 else None
    second_due = due[1] if due_n > 1 else None
    group2 = [
        {
            "key": "variant_second",
            "title": f"「{second_weak['kp_name']}」变式训练" if second_weak else "自由练习",
            "why": (
                f"掌握度 {round(second_weak['mastery'] * 100)}%，次薄弱点"
                if second_weak
                else "保持题感"
            ),
            "est_minutes": 15,
            "benefit": "巩固第二薄弱环节",
            "route": f"/practice?kp={second_weak['kp_code']}" if second_weak else "/practice",
            "done": False,
        },
        {
            "key": "review_second",
            "title": "复习次急错题",
            "why": (
                f"1 道错题可提取性降至 {round(second_due['retrievability'] * 100)}%"
                if second_due
                else "无更多到期错题"
            ),
            "est_minutes": 8,
            "benefit": "提前清理遗忘风险",
            "route": "/errors",
            "done": False,
        },
        {
            "key": "graph_review",
            "title": "图谱回顾",
            "why": "查看知识版图整体进度",
            "est_minutes": 5,
            "benefit": "明确下一步主攻方向",
            "route": "/graph",
            "done": False,
        },
    ]

    # ===== 迭代17 AI 管家：today-3 文案 AI 化（规则骨架 + LLM 润色，异常回退模板）=====
    # 组1 每件事补 ai_title/ai_why/ai_benefit（复用 copy_polish，10s 超时 + 回退模板）
    uid = str(user_id)
    for t in group1:
        fp = f"{t['key']}|{t['title']}|{t['why']}"
        t["ai_title"] = await copy_polish.polish(
            copy_polish.SCENE_TODAY3_REASON, t["title"], data_fingerprint=fp + "|t", user_id=uid
        )
        t["ai_why"] = await copy_polish.polish(
            copy_polish.SCENE_TODAY3_REASON, t["why"], data_fingerprint=fp + "|w", user_id=uid
        )
        t["ai_benefit"] = await copy_polish.polish(
            copy_polish.SCENE_TODAY3_REASON, t["benefit"], data_fingerprint=fp + "|b", user_id=uid
        )

    # 开场白（复用管家主动开场，本地导入避免启动期循环依赖）
    ai_intro = "今天从这三件事开始吧。"
    try:
        from app.butler.skills import proactive_greeting

        ai_intro = await proactive_greeting(db, user_id)
    except Exception as e:  # noqa: BLE001
        logger.info("today3_intro_fallback", error=str(e)[:120])

    # 鼓励语（数据驱动模板兜底）
    encourage_tpl = (
        f"你今天有 {due_n} 道错题该复习了，稳扎稳打就能看到进步。"
        if due_n
        else "错题清完了，今天可以挑战一下压轴题。"
    )
    ai_encourage = await copy_polish.polish(
        copy_polish.SCENE_PANEL_ENCOURAGEMENT,
        encourage_tpl,
        data_fingerprint=f"today3|due:{due_n}",
        user_id=uid,
    )

    return _ok({"groups": [group1, group2], "ai_intro": ai_intro, "ai_encourage": ai_encourage})


@router.get("/growth/score-trend")
async def growth_score_trend(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """综合分 7 日趋势（模块6）：user_daily_stats 优先，缺数据日用 mastery_snapshots 折算"""
    user_id = uuid.UUID(user["sub"])
    view = await _composite_view(db, user_id)
    target = await _target_score(db, user_id)

    today = date.today()
    since = today - timedelta(days=6)
    stat_rs = await db.execute(
        select(UserDailyStat.date, UserDailyStat.composite_score).where(
            UserDailyStat.user_id == user_id, UserDailyStat.date >= since
        )
    )
    stat_by_date = {d: float(s) for d, s in stat_rs.all() if s is not None}

    # 缺数据日：mastery_snapshots 日均值按综合分公式折算；快照也无 → None
    snap_rs = await db.execute(
        select(MasterySnapshot.date, func.avg(MasterySnapshot.mastery))
        .where(MasterySnapshot.user_id == user_id, MasterySnapshot.date >= since)
        .group_by(MasterySnapshot.date)
    )
    snap_by_date = {d: float(v) for d, v in snap_rs.all()}

    daily = []
    for i in range(7):
        d = since + timedelta(days=i)
        score = stat_by_date.get(d)
        if score is None and d in snap_by_date:
            score = growth_svc.composite_score(snap_by_date[d], view["hint_dep"], view["streak"])
        daily.append({"date": d.isoformat(), "score": round(score) if score is not None else None})

    return _ok(
        {
            "score": view["score"],
            "delta_week": view["score_delta"],
            "target": target,
            "daily": daily,
        }
    )


@router.get("/growth/feature-entries")
async def growth_feature_entries(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """功能入口聚合（模块6）：各模块计数一处拉取"""
    user_id = uuid.UUID(user["sub"])
    view = await _composite_view(db, user_id)

    error_views = await growth_svc.load_errors_fsrs(db, user_id)
    error_total = len(error_views)
    due_count = sum(1 for v in error_views if v["is_due"])
    week_answer_count, _ = await growth_svc.week_answer_stats(db, user_id, 7)
    rows = view["rows"]
    mastered = sum(1 for r in rows if r["mastery"] >= _MASTERY_HIGH)
    total_kp = len(await _real_kps(db))

    entries = [
        {
            "key": "errors",
            "title": "错题本",
            "stat_text": f"{error_total} 道在册",
            "badge": due_count or None,
            "route": "/errors",
        },
        {
            "key": "practice",
            "title": "练题中心",
            "stat_text": f"本周作答 {week_answer_count} 题",
            "badge": None,
            "route": "/practice",
        },
        {
            "key": "report",
            "title": "学情报告",
            "stat_text": f"综合分 {view['score']}",
            "badge": None,
            "route": "/report",
        },
        {
            "key": "graph",
            "title": "知识图谱",
            "stat_text": f"已掌握 {mastered}/{total_kp}",
            "badge": None,
            "route": "/graph",
        },
        {
            "key": "exam",
            "title": "模拟考试",
            "stat_text": "随时开考",
            "badge": None,
            "route": "/exam",
        },
        {
            "key": "chat",
            "title": "对话学习",
            "stat_text": f"连击 {view['streak']} 天",
            "badge": None,
            "route": "/chat",
        },
    ]
    return _ok({"entries": entries})
