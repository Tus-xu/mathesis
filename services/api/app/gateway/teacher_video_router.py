"""教师端「资源中心」备课视频路由（/api/teacher/videos）。

2026-10-05 产品裁决：教师端资源中心**整体替换为 B 站备课视频推荐**——
原「我的资源/校本资源/公共资源/上传」那一套是备课产物聚合 + 假缩略图 + 前端假上传，
不是教师真正要的「资源」；教师备课要的是**与高中数学教学相关的视频资源**。
学生端已有稳定的 B 站实检服务（app/services/bilibili.py：wbi 签名 + 30min 缓存 +
99 条离线兜底），本路由直接复用，**只换检索词与分类**（教师备课视角）。

与学生端 /api/student/videos 的差异：
  - 无「按学情推荐」（那是学生侧薄弱点/错题驱动的，教师没有这个语义）；
  - 无讯飞工作流依赖，纯 B 站实检（live）+ 离线兜底（fallback）；
  - 分类全部换成**教师备课视角**（教材解读/专题微课/教法研讨/课堂实录…）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.gateway.auth import get_current_user
from app.services import bilibili

router = APIRouter(prefix="/api/teacher/videos", tags=["teacher-videos"])

# 教师备课视角分类：每项一条 B 站检索式（key/label/icon/keyword/desc 与学生端同构）。
# ⚠️ 只保留**有真实兜底数据**的 6 类（bilibili_fallback.json 里有对应检索词的条目）；
#    「教法研讨/课堂实录/教学工具」三类 live 被压空且无兜底时会白屏，故先不上，
#    待补充真实视频快照后再加。
TOPICS: list[dict[str, str]] = [
    {
        "key": "featured",
        "label": "备课精选",
        "icon": "⭐",
        "keyword": "高中数学 公开课 名师",
        "desc": "名师公开课与优质课实录，备课首选参考",
    },
    {
        "key": "lesson",
        "label": "教材解读",
        "icon": "📘",
        "keyword": "高中数学 教材解读 人教A版",
        "desc": "逐章逐节吃透教材编写意图",
    },
    {
        "key": "micro",
        "label": "专题微课",
        "icon": "🎬",
        "keyword": "高中数学 微课 精讲",
        "desc": "短平快知识点微课，适合嵌入课堂",
    },
    {
        "key": "method",
        "label": "解题方法",
        "icon": "✏️",
        "keyword": "高中数学 解题技巧 一题多解",
        "desc": "一题多解与通性通法，讲评课素材",
    },
    {
        "key": "review",
        "label": "高考备考",
        "icon": "🔥",
        "keyword": "高考数学 备考 命题趋势",
        "desc": "考向分析与备考策略",
    },
    {
        "key": "comp",
        "label": "竞赛拓展",
        "icon": "🏆",
        "keyword": "高中数学 竞赛 拓展",
        "desc": "强基与竞赛拓展，丰富校本资源",
    },
]

_TOPIC_BY_KEY = {t["key"]: t for t in TOPICS}
DEFAULT_TOPIC = TOPICS[0]["key"]


def _topic_list() -> list[dict[str, str]]:
    return [{k: t[k] for k in ("key", "label", "icon", "desc")} for t in TOPICS]


def _resolve(topic: str | None, keyword: str | None) -> dict[str, str]:
    """关键词优先（自定义搜索）；其次按 key 取分类；缺省取备课精选。"""
    kw = (keyword or "").strip()
    if kw:
        return {"key": "custom", "label": f"搜索「{kw}」", "icon": "🔍", "desc": "", "keyword": kw}
    t = _TOPIC_BY_KEY.get((topic or "").strip())
    return t or _TOPIC_BY_KEY[DEFAULT_TOPIC]


@router.get("")
async def list_teacher_videos(
    topic: str | None = Query(default=None, description="分类 key，见返回的 topics"),
    keyword: str | None = Query(default=None, max_length=50, description="自定义搜索词"),
    page: int = Query(default=1, ge=1, le=5),
    page_size: int = Query(default=12, ge=1, le=30),
    user: dict = Depends(get_current_user),
):
    """按备课分类（或关键词）浏览 B 站高中数学教学视频。"""
    meta = _resolve(topic, keyword)
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
            "topics": _topic_list(),
            "topic": {k: meta[k] for k in ("key", "label", "icon", "desc", "keyword")},
            "page": page,
            "page_size": page_size,
            "source": result["source"],
            "live": result["source"] in ("bilibili", "cache"),
            "items": result["items"],
        },
    }
