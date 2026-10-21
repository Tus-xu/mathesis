"""B 站视频检索服务（学生端「资源中心」的数据源）。

为什么必须走服务端代理：
- 浏览器直连 B 站搜索接口会被 CORS 拦掉，网页端拿不到数据；
- 搜索接口强制 wbi 签名（w_rid），密钥不能下发到前端。

无登录态即可用：`nav` 在 code=-101（未登录）时仍返回 wbi_img 密钥，
`frontend/finger/spi` 可拿到 buvid3；带上这两样后 `wbi/search/type` 返回 code=0。

两级进程内缓存：wbi 密钥 1h、搜索结果 30min。远端不可用（超时/风控 412）时
回退到随包发布的 bilibili_fallback.json，保证资源中心不白屏。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

_API = "https://api.bilibili.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

# wbi 重排表（B 站前端固定常量，混入 img_key+sub_key 后取前 32 位作为 mixin key）
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_KEY_TTL = 3600.0
_BUVID_TTL = 86400.0
_RESULT_TTL = 1800.0
_TIMEOUT = 12.0

_FALLBACK_PATH = Path(__file__).with_name("bilibili_fallback.json")

_TAG_RE = re.compile(r"<[^>]+>")

# 高中数学知识点分类：每个分类一条检索式（服务端按此检索 B 站）
# up_mid 存在时改走「UP 主投稿」接口：比关键词搜索稳定，且内容必然出自该 UP 主
TOPICS: list[dict[str, str]] = [
    {
        "key": "featured",
        "label": "精选推荐",
        "icon": "⭐",
        "keyword": "一数 高中数学",
        "up_mid": "14229967",  # 一数
        "up_name": "一数",
        "desc": "一数系统课精选，从零打基础",
    },
    {
        "key": "function",
        "label": "函数与导数",
        "icon": "📈",
        "keyword": "高中数学 函数与导数 讲解",
        "desc": "单调性、极值、零点与导数应用",
    },
    {
        "key": "trig",
        "label": "三角函数",
        "icon": "🌊",
        "keyword": "高中数学 三角函数 讲解",
        "desc": "图像变换、恒等变换与解三角形",
    },
    {
        "key": "sequence",
        "label": "数列",
        "icon": "🔢",
        "keyword": "高中数学 数列 通项 求和",
        "desc": "等差等比、通项求法与数列不等式",
    },
    {
        "key": "solid",
        "label": "立体几何",
        "icon": "🧊",
        "keyword": "高中数学 立体几何 证明 讲解",
        "desc": "平行垂直证明、空间角与向量法",
    },
    {
        "key": "analytic",
        "label": "解析几何",
        "icon": "🎯",
        "keyword": "高中数学 圆锥曲线 解析几何 讲解",
        "desc": "椭圆双曲线抛物线、直线与曲线联立",
    },
    {
        "key": "prob",
        "label": "概率统计",
        "icon": "🎲",
        "keyword": "高中数学 概率统计 讲解",
        "desc": "排列组合、分布列与统计案例",
    },
    {
        "key": "inequality",
        "label": "集合与不等式",
        "icon": "⚖️",
        "keyword": "高中数学 集合 不等式 讲解",
        "desc": "基本不等式、恒成立与参数讨论",
    },
    {
        "key": "review",
        "label": "高考总复习",
        "icon": "🔥",
        "keyword": "高中数学 一轮复习 高频考点",
        "desc": "一轮复习与高频题型串讲",
    },
]

_TOPIC_BY_KEY = {t["key"]: t for t in TOPICS}
DEFAULT_TOPIC = TOPICS[0]["key"]

_lock = asyncio.Lock()
_wbi: tuple[str, float] | None = None
_buvid: tuple[str, float] | None = None
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_fallback_data: dict[str, list[dict[str, Any]]] | None = None


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_TAB)[:32]


def _sanitize(value: str) -> str:
    """wbi 规范：签名前剔除值里的 !'()* 字符。"""
    return str(value).translate(str.maketrans("", "", "!'()*"))


def _clean_text(value: Any) -> str:
    """去掉搜索结果高亮标签 <em class="keyword"> 并反转义实体。"""
    return html.unescape(_TAG_RE.sub("", str(value or ""))).strip()


def _fix_url(url: Any) -> str:
    u = str(url or "").strip()
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://"):
        return "https://" + u[len("http://") :]
    return u


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else 0


def _duration_sec(text: str) -> int:
    parts = [p for p in str(text or "").split(":") if p != ""]
    total = 0
    for p in parts:
        total = total * 60 + _to_int(p)
    return total


def _normalize(raw: dict[str, Any]) -> dict[str, Any] | None:
    """B 站搜索条目 → 前端卡片契约。无 bvid 的条目（番剧/合集等）直接丢弃。"""
    bvid = str(raw.get("bvid") or "").strip()
    if not bvid:
        return None
    duration = str(raw.get("duration") or "").strip()
    return {
        "bvid": bvid,
        "title": _clean_text(raw.get("title")),
        "author": _clean_text(raw.get("author")),
        "author_avatar": _fix_url(raw.get("upic")),
        "cover": _fix_url(raw.get("pic")),
        "duration": duration,
        "duration_sec": _duration_sec(duration),
        "play": _to_int(raw.get("play")),
        "danmaku": _to_int(raw.get("video_review")),
        "pubdate": _to_int(raw.get("pubdate")),
        "desc": _clean_text(raw.get("description"))[:140],
        "url": f"https://www.bilibili.com/video/{bvid}",
    }


async def _session_keys(client: httpx.AsyncClient) -> str:
    """确保 buvid3 cookie 与 wbi 密钥就绪，返回 mixin key。"""
    global _wbi, _buvid
    now = time.time()
    async with _lock:
        fresh_wbi = _wbi is not None and _wbi[1] > now
        fresh_buvid = _buvid is not None and _buvid[1] > now
        if fresh_wbi and fresh_buvid:
            client.cookies.set("buvid3", _buvid[0], domain=".bilibili.com")
            return _wbi[0]

        if not fresh_buvid:
            resp = await client.get(f"{_API}/x/frontend/finger/spi")
            b3 = (resp.json().get("data") or {}).get("b_3")
            if b3:
                _buvid = (str(b3), now + _BUVID_TTL)
        if _buvid:
            client.cookies.set("buvid3", _buvid[0], domain=".bilibili.com")

        if not fresh_wbi:
            resp = await client.get(f"{_API}/x/web-interface/nav")
            wbi_img = (resp.json().get("data") or {}).get("wbi_img") or {}
            img_key = str(wbi_img.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
            sub_key = str(wbi_img.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
            if not (img_key and sub_key):
                raise RuntimeError("B 站 nav 未返回 wbi_img 密钥")
            _wbi = (_mixin_key(img_key + sub_key), now + _KEY_TTL)

        return _wbi[0]


async def _get_signed(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> Any:
    """按 wbi 规范签名后请求，返回 data 字段。"""
    mixin = await _session_keys(client)
    signed = {k: _sanitize(v) for k, v in params.items()}
    signed["wts"] = str(int(time.time()))
    # 手工拼查询串：w_rid 必须与实际发出的 query 逐字节一致，不能让 httpx 重编码
    query = urllib.parse.urlencode(sorted(signed.items()))
    w_rid = hashlib.md5((query + mixin).encode()).hexdigest()
    resp = await client.get(f"{_API}{path}?{query}&w_rid={w_rid}")
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"B 站 {path} 返回 code={payload.get('code')} {payload.get('message')}"
        )
    return payload.get("data")


async def _search(keyword: str, page: int, page_size: int) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        data = await _get_signed(
            client,
            "/x/web-interface/wbi/search/type",
            {
                "search_type": "video",
                "keyword": keyword,
                "page": str(page),
                "page_size": str(page_size),
                "order": "totalrank",
            },
        )
        raw_items = (data or {}).get("result") or []
        items = [n for n in (_normalize(it) for it in raw_items if isinstance(it, dict)) if n]
        # B 站会无视偏小的 page_size 固定回 20 条，这里按调用方请求截断
        return items[:page_size]


def _normalize_arc(raw: dict[str, Any]) -> dict[str, Any] | None:
    """UP 主投稿条目 → 前端卡片契约（字段名与搜索结果不同）。"""
    return _normalize(
        {
            "bvid": raw.get("bvid"),
            "title": raw.get("title"),
            "author": raw.get("author"),
            "upic": raw.get("upic"),
            "pic": raw.get("pic"),
            "duration": _duration_text(raw.get("length")),
            "play": raw.get("play"),
            "video_review": raw.get("video_review"),
            "pubdate": raw.get("created"),
            "description": raw.get("description"),
        }
    )


def _duration_text(length: Any) -> str:
    """投稿接口给的是秒数，转成与搜索接口一致的 mm:ss 文本。"""
    secs = _to_int(length)
    if secs <= 0:
        return ""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _up_videos(
    mid: str, page: int, page_size: int, order: str = "click"
) -> list[dict[str, Any]]:
    """UP 主投稿列表（按播放量排序），比关键词搜索稳定。"""
    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        data = await _get_signed(
            client,
            "/x/space/wbi/arc/search",
            {
                "mid": str(mid),
                "ps": str(page_size),
                "pn": str(page),
                "order": order,
                "tid": "0",
                "platform": "web",
            },
        )
        vlist = ((data or {}).get("list") or {}).get("vlist") or []
        items = [n for n in (_normalize_arc(it) for it in vlist if isinstance(it, dict)) if n]
        return items[:page_size]


def _fallback(keyword: str) -> list[dict[str, Any]]:
    global _fallback_data
    if _fallback_data is None:
        try:
            _fallback_data = json.loads(_FALLBACK_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # 兜底文件缺失不应让接口 500
            logger.warning("bilibili.fallback_load_failed", error=str(exc)[:200])
            _fallback_data = {}
    return list(_fallback_data.get(keyword) or [])


async def fetch_videos(
    keyword: str = "",
    page: int = 1,
    page_size: int = 12,
    up_mid: str | None = None,
) -> dict[str, Any]:
    """取一组视频；返回 {items, source}。

    up_mid 有值时走「UP 主投稿（按播放量）」接口，否则按关键词检索。
    source: bilibili（实时）/ cache（命中缓存）/ stale（远端失败用旧缓存）/
            fallback（远端失败用随包数据）/ empty（既无关键词也无 UP 主）
    """
    keyword = (keyword or "").strip()
    up_mid = (up_mid or "").strip()
    if not keyword and not up_mid:
        return {"items": [], "source": "empty"}

    cache_key = f"up:{up_mid}|{page}|{page_size}" if up_mid else f"kw:{keyword}|{page}|{page_size}"
    now = time.time()
    hit = _cache.get(cache_key)
    if hit and hit[0] > now:
        return {"items": hit[1], "source": "cache"}

    try:
        if up_mid:
            items = await _up_videos(up_mid, page, page_size)
        else:
            items = await _search(keyword, page, page_size)
        if items:
            _cache[cache_key] = (now + _RESULT_TTL, items)
            return {"items": items, "source": "bilibili"}
        raise RuntimeError("返回结果为空")
    except Exception as exc:
        logger.warning(
            "bilibili.fetch_failed",
            keyword=keyword,
            up_mid=up_mid,
            error=str(exc)[:200],
        )

    if hit:
        return {"items": hit[1], "source": "stale"}
    # 兜底文件按关键词索引；UP 主分类退回其关键词那一组
    return {"items": _fallback(keyword), "source": "fallback"}


def resolve_topic(topic: str | None, keyword: str | None) -> dict[str, str]:
    """确定本次请求的分类：显式 keyword 优先，其次按 topic key，最后默认精选。"""
    kw = (keyword or "").strip()
    if kw:
        return {"key": "custom", "label": "搜索结果", "icon": "🔍", "keyword": kw, "desc": ""}
    topic_meta = _TOPIC_BY_KEY.get(topic or "") or _TOPIC_BY_KEY[DEFAULT_TOPIC]
    return dict(topic_meta)


def topic_list() -> list[dict[str, str]]:
    return [
        {"key": t["key"], "label": t["label"], "icon": t["icon"], "desc": t["desc"]}
        for t in TOPICS
    ]
