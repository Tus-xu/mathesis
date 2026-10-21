"""学生端「资源中心」视频路由（/api/student/videos）。

数据主源是讯飞星辰工作流 `wf_bili_video_recommend`（按学情推荐 B 站讲解视频）；
工作流不可用（未授权 / 超时 / 限流）时，回退到服务端实检 B 站（app/services/bilibili.py），
保证资源中心永远有封面和可点击的播放链接。前端不接触任何密钥。
"""

from __future__ import annotations

import re
import uuid

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.coursework import ErrorRecord
from app.models.database import get_db
from app.models.knowledge_point import KnowledgePoint
from app.models.user_profile import UserProfile
from app.providers.xingchen import run_workflow
from app.services import bilibili
from app.services import growth as growth_svc

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/student/videos", tags=["student-videos"])

RECOMMEND_FLOW = "wf_bili_video_recommend"

# 知识点 → B 站检索分类：工作流不可用时的本地兜底路由
_KP_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("导数", "函数", "值域", "最值", "单调", "零点"), "function"),
    (("三角", "正弦", "余弦", "解三角形"), "trig"),
    (("数列", "等差", "等比", "递推"), "sequence"),
    (("立体", "空间", "三棱", "体积", "异面"), "solid"),
    (("圆锥", "椭圆", "双曲线", "抛物线", "解析几何", "直线与圆"), "analytic"),
    (("概率", "统计", "分布", "排列组合", "计数"), "prob"),
    (("不等式", "集合", "恒成立", "基本不等"), "inequality"),
    (("复习", "模考", "高考"), "review"),
]


class RecommendRequest(BaseModel):
    kp_name: str = Field(default="", max_length=60)
    error_type: str = Field(default="", max_length=20)
    question_snippet: str = Field(default="", max_length=600)
    grade_hint: str = Field(default="", max_length=20)
    max_results: int = Field(default=6, ge=1, le=12)


def _to_int(value: object) -> int:
    digits = re.sub(r"\D", "", str(value if value is not None else ""))
    return int(digits) if digits else 0


def _duration_sec(text: str) -> int:
    total = 0
    for part in str(text or "").split(":"):
        if part.strip() == "":
            continue
        total = total * 60 + _to_int(part)
    return total


def _fix_url(url: object) -> str:
    u = str(url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://") :]
    return u


def _normalize_workflow_video(raw: dict) -> dict | None:
    """工作流输出 {title,url,bvid,cover_url,up_name,duration,view_count,match_reason}
    → 与 /student/videos 一致的卡片契约。"""
    url = _fix_url(raw.get("url"))
    bvid = str(raw.get("bvid") or "").strip()
    if not bvid:
        match = re.search(r"(BV[0-9A-Za-z]+)", str(raw.get("url") or ""))
        bvid = match.group(1) if match else ""
    if not bvid and not url:
        return None
    duration = str(raw.get("duration") or "").strip()
    return {
        "bvid": bvid,
        "title": str(raw.get("title") or "").strip(),
        "author": str(raw.get("up_name") or "").strip(),
        "cover": _fix_url(raw.get("cover_url")),
        "duration": duration,
        "duration_sec": _duration_sec(duration),
        "play": _to_int(raw.get("view_count")),
        "match_reason": str(raw.get("match_reason") or "").strip(),
        "url": url or f"https://www.bilibili.com/video/{bvid}",
        "source": "xingchen",
    }


def _topic_key_for(kp_name: str) -> str:
    for needles, key in _KP_HINTS:
        if any(n in kp_name for n in needles):
            return key
    return bilibili.DEFAULT_TOPIC


def _grade_hint_of(grade: str | None) -> str:
    g = (grade or "").strip()
    if not g:
        return "高中"
    return g if g.startswith("高中") else f"高中{g}"


async def _resolve_inputs(
    req: RecommendRequest, user_id: uuid.UUID, db: AsyncSession
) -> dict[str, object]:
    """把请求补全成工作流的完整入参：缺知识点取最薄弱项，缺题干取该知识点最近一道错题。"""
    kp_name = req.kp_name.strip()
    kp_code: str | None = None

    if not kp_name:
        rows = await growth_svc.load_mastery_rows(db, user_id)
        if rows:
            weakest = min(rows, key=lambda r: r["mastery"])
            kp_code = weakest["kp_code"]
            kp = (
                await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code))
            ).scalar_one_or_none()
            if kp is not None:
                kp_name = kp.name

    question_snippet = req.question_snippet.strip()
    error_type = req.error_type.strip()
    if kp_code and not (question_snippet and error_type):
        latest = (
            await db.execute(
                select(ErrorRecord)
                .where(
                    ErrorRecord.user_id == user_id,
                    ErrorRecord.kp_code == kp_code,
                    ErrorRecord.deleted_at.is_(None),
                )
                .order_by(ErrorRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            question_snippet = question_snippet or (latest.question_text or "")[:500]
            error_type = error_type or (latest.error_type or "")

    grade_hint = req.grade_hint.strip()
    if not grade_hint:
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
        ).scalar_one_or_none()
        grade_hint = _grade_hint_of(profile.grade if profile else None)

    return {
        "kp_name": kp_name,
        "error_type": error_type,
        "question_snippet": question_snippet,
        "grade_hint": grade_hint,
    }


async def _local_recommend(kp_name: str, max_results: int) -> list[dict]:
    """兜底：按知识点归类的检索式实检 B 站。"""
    topic_key = _topic_key_for(kp_name)
    meta = bilibili.resolve_topic(topic_key, None)
    result = await bilibili.fetch_videos(
        meta["keyword"],
        page=1,
        page_size=max_results,
        up_mid=meta.get("up_mid"),
    )
    return [{**item, "match_reason": f"按「{kp_name}」匹配的{meta['label']}讲解"} for item in result["items"]]


@router.post("/recommend")
async def recommend_videos(
    req: RecommendRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """按学情推荐 B 站讲解视频。

    优先走讯飞星辰工作流；unavailable 为 true 时表示工作流未生效（未授权/超时/限流），
    此时返回本地 B 站检索结果，前端可提示「本地推荐」。
    """
    user_id = uuid.UUID(user["sub"])
    inputs = await _resolve_inputs(req, user_id, db)
    kp_name = str(inputs["kp_name"] or "")

    notice = ""
    try:
        out = await run_workflow(
            RECOMMEND_FLOW,
            uid=str(user_id),
            parameters={
                # 讯飞工作流起始节点强制要求该字段，用知识点作为用户输入语义
                "AGENT_USER_INPUT": kp_name or "高中数学",
                **inputs,
                "max_results": req.max_results,
            },
            read_timeout=45.0,
        )
        payload = out.get("data") if isinstance(out.get("data"), dict) else out
        raw_videos = (payload or {}).get("videos") or []
        items = [n for n in (_normalize_workflow_video(v) for v in raw_videos if isinstance(v, dict)) if n]
        if not items:
            raise RuntimeError(f"工作流未返回可用视频（raw={str(out)[:200]}）")
        return {
            "code": 0,
            "message": "ok",
            "data": {
                "kp_name": kp_name,
                "items": items[: req.max_results],
                "source": "xingchen",
                "unavailable": False,
                "notice": "",
                "confidence": payload.get("confidence"),
            },
        }
    except Exception as exc:  # noqa: BLE001 — 兜底优先：工作流任何失败都不许白屏
        notice = str(exc)[:160]
        logger.warning("videos.recommend_workflow_failed", kp_name=kp_name, error=notice)

    items = await _local_recommend(kp_name or "高中数学", req.max_results)
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "kp_name": kp_name,
            "items": items,
            "source": "local",
            "unavailable": True,
            "notice": notice,
            "confidence": None,
        },
    }


@router.get("")
async def list_videos(
    topic: str | None = Query(default=None, description="分类 key，见返回的 topics"),
    keyword: str | None = Query(default=None, max_length=50, description="自定义搜索词"),
    page: int = Query(default=1, ge=1, le=5),
    page_size: int = Query(default=12, ge=1, le=30),
    user: dict = Depends(get_current_user),
):
    """按分类（或关键词）浏览 B 站高中数学讲解视频（资源中心的「按知识点浏览」）。"""
    meta = bilibili.resolve_topic(topic, keyword)
    result = await bilibili.fetch_videos(
        meta.get("keyword", ""),
        page=page,
        page_size=page_size,
        up_mid=meta.get("up_mid"),
    )
    return {
        "code": 0,
        "message": "ok",
        "data": {
            "topics": bilibili.topic_list(),
            "topic": {k: meta[k] for k in ("key", "label", "icon", "desc", "keyword")},
            "page": page,
            "page_size": page_size,
            "source": result["source"],
            "live": result["source"] in ("bilibili", "cache"),
            "items": result["items"],
        },
    }
