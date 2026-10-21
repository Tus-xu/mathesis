"""R17 授权选项来源：教材 / 章节选项只来自已授权教材切片（textbook_v2，direct-chunk）。

铁律（DECISIONS.md §3/§8）：
- 唯一教材源 = 已授权切片（rights_evidence_ref='队伍确认授权'）；不使用原 PDF / TextbookPage；
- 选项来自 DB 授权行（rights_status IN ('ACTIVE','STAGED') 且带授权证据），绝不编造；
- `selection_card` 的 options 只由此处产出；找不到 → 空 options + 显式提示（不猜）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.models import TextbookSection, TextbookSource

# 授权状态（R06 全部 STAGED；ACTIVE 由 rights 状态机放行——两者都带队伍确认授权证据）
_AUTHORIZED_RIGHTS = ("ACTIVE", "STAGED")

# 意图解析用「章节导览」：该册详情层（level>1 中行数最多者）的章节标题，让 LLM 知道
# 「这本书讲什么」而非只凭版本/册次瞎猜。控量避免 prompt 爆炸——每册最多 16 条、总长约 240 字符。
# 不用固定 level 1/2：实测教材切片层级不规整（一级多为「未分节」碎片，"椭圆/数列"等真实细目
# 落在 level=3），与 list_section_options 的既有口径一致。
_TOPIC_MAX_ITEMS = 16
_TOPIC_MAX_CHARS = 240

# LP-01：真实章节编号形如「3.1.1」「1.2」「4.3.2」。带编号 = 真实章节；不带编号且
# 又无 section_path 的 level=1 行是 PDF 切片碎片段（「未分节」「1. 范围」），不得入选项。
_SECTION_NO_RE = re.compile(r"^(\d+)(?:\.\d+)*[.、]?\s")


def _sort_key(text: str) -> tuple[int, ...]:
    """按教材编号自然序排序键（「3.1.1」< 「3.2.1」< 「10.1」）。无编号 → 末尾稳定序。"""
    m = re.match(r"^\s*(\d+(?:\.\d+)*)", text or "")
    if not m:
        return (9999,)
    return tuple(int(x) for x in m.group(1).split("."))


def label_of(*parts: str | None) -> str:
    """教材展示名：剔除空段与**重复段**后以 ` · ` 连接（顺序不变、字段不改写）。

    utils：导入源存在冗余（苏教版 edition == volume == 选择性必修第一册），逐字拼接会
    渲染出「选择性必修第一册 · 选择性必修第一册」（2026-09-21 截图实测）；
    同一段重复只保留首次出现。
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in parts:
        part = str(raw or "").strip()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return " · ".join(out)


async def list_textbook_options(
    session: AsyncSession,
    *,
    school_id: str | None = None,
    limit: int = 100,
    include_topics: bool = False,
) -> list[dict[str, str]]:
    """已授权教材选项（publisher/edition/grade/volume → label）。

    `include_topics=True` 时额外为每个选项追加 `topics`（章节导览，见 _topics_by_source）：
    仅意图解析 prompt 需要，选择卡（selection_card）契约只有 id/label，故默认关闭。
    """
    stmt = (
        select(TextbookSource)
        .where(TextbookSource.rights_status.in_(_AUTHORIZED_RIGHTS))
        .order_by(TextbookSource.publisher, TextbookSource.grade, TextbookSource.volume)
        .limit(min(limit, 200))
    )
    if school_id:
        from app.textbook_v2.models import TextbookSource as Src

        stmt = stmt.where((Src.school_id.is_(None)) | (Src.school_id == school_id))
    rows = (await session.execute(stmt)).scalars().all()
    options = [
        {
            "id": str(r.id),
            "label": label_of(r.publisher, r.edition, r.grade, r.volume),
        }
        for r in rows
    ]
    if include_topics and options:
        topics = await _topics_by_source(session, [r.id for r in rows])
        for opt in options:
            opt["topics"] = topics.get(opt["id"], "")
    return options


async def _topics_by_source(
    session: AsyncSession, source_ids: Sequence[uuid.UUID]
) -> dict[str, str]:
    """每个 source 的章节导览字符串（供 LLM 判别「这本书讲什么」）。

    一次批量查询，避免 N+1。详情层 = level>1 中行数最多者（与 list_section_options 同口径；
    无 level>1 则退回 level 1），按 section_path/order 排序取前 _TOPIC_MAX_ITEMS 条、
    总长截断到 _TOPIC_MAX_CHARS，保持 prompt 有界。无章节的 source 返回空串（不编造）。
    """
    if not source_ids:
        return {}
    rows = (
        await session.execute(
            select(
                TextbookSection.textbook_id,
                TextbookSection.level,
                TextbookSection.title,
            )
            .where(TextbookSection.textbook_id.in_(list(source_ids)))
            .order_by(
                TextbookSection.textbook_id,
                TextbookSection.level,
                TextbookSection.section_path,
                TextbookSection.order,
            )
        )
    ).all()
    by_source: dict[str, dict[int, list[str]]] = {}
    for textbook_id, level, title in rows:
        label = (title or "").strip()
        if not label:
            continue
        by_source.setdefault(str(textbook_id), {}).setdefault(level, []).append(label)
    topics: dict[str, str] = {}
    for textbook_id, by_level in by_source.items():
        detail_levels = {lvl: items for lvl, items in by_level.items() if lvl > 1}
        if detail_levels:
            labels = max(detail_levels.items(), key=lambda kv: (len(kv[1]), -kv[0]))[1]
        else:
            labels = by_level.get(1, [])
        picked: list[str] = []
        total = 0
        for label in labels[:_TOPIC_MAX_ITEMS]:
            if picked and total + len(label) + 3 > _TOPIC_MAX_CHARS:
                break
            picked.append(label)
            total += len(label) + 3
        topics[textbook_id] = " / ".join(picked)
    return topics



async def list_section_options(
    session: AsyncSession,
    *,
    source_id: str,
    level: int | None = None,
    limit: int = 200,
    knowledge_points: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """指定教材下的已授权章节选项（label + 分组 + 推荐标记）。

    已授权切片的层级并不规整：实测 22 个有章节的教材源里只有 9 个存在 level=2，
    其余（含全部「教师用书」卷）的章节落在 level=1/3。因此指定层级取不到行时，回退到
    该教材行数最多的 level>1，否则章节选择卡会给出空 options、教师无法继续选章。
    任何层级都找不到时仍返回空（不猜、不编造）。

    2026-09-23 主控修正（产品缺陷 LP-01「笨管家」）：
    - 过滤切片噪声：level=1 的「未分节」「1. 范围」「2. 对称性」等是 PDF 切片的碎片段，
      不是真实章节，出现在选择卡里会让教师无从下手；
    - 按知识点相关性排序并打 `recommended` 标记：教师说「我要备椭圆」，
      「3.1.1 椭圆的标准方程」必须排第一，而不是按物理行号排到直线的点斜式方程之后；
    - 按章号分组（label 由真实编号推导为「第 N 章」，不编造章节名）。
    """
    # 调用方传进来的 source_id 常来自 JSON 快照（字符串）。UUID 列的通用处理器要求
    # uuid.UUID（SQLite/通用方言下字符串会以 'str' has no attribute 'hex' 失败），
    # 因此在此统一归一，避免每个调用点各转一次。
    source_uid = source_id if isinstance(source_id, uuid.UUID) else uuid.UUID(str(source_id))

    async def _rows(level_filter: int | None) -> list[TextbookSection]:
        stmt = (
            select(TextbookSection)
            .where(TextbookSection.textbook_id == source_uid)
            .order_by(TextbookSection.level, TextbookSection.order)
            .limit(min(limit, 400))
        )
        if level_filter is not None:
            stmt = stmt.where(TextbookSection.level == level_filter)
        return list((await session.execute(stmt)).scalars().all())

    rows = await _rows(level)
    if not rows and level is not None:
        n = func.count().label("n")
        dominant = (
            await session.execute(
                select(TextbookSection.level, n)
                .where(TextbookSection.textbook_id == source_uid, TextbookSection.level > 1)
                .group_by(TextbookSection.level)
                .order_by(n.desc(), TextbookSection.level)
                .limit(1)
            )
        ).first()
        if dominant is not None:
            rows = await _rows(dominant[0])

    entries: list[dict[str, Any]] = []
    kws = [k for k in (knowledge_points or []) if str(k or "").strip()]
    for r in rows:
        path = (r.section_path or "").strip()
        title = (r.title or "").strip()
        if not title:
            continue
        # LP-01：剔除切片噪声。真实章节标题必带「N.N…」编号；level=1 且无 path 且无编号的
        # 条目是 PDF 切片的碎片段（「未分节」「1. 范围」「2. 对称性」），不得进选择卡。
        if not path and not _SECTION_NO_RE.match(title) and (r.level or 1) <= 1:
            continue
        # BE-DATA2：title 已含编号（如「3.1.2 椭圆的简单几何性质」）时不再重复拼接
        # section_path，避免出现「3.1.2 3.1.2 椭圆的简单几何性质」的重复编号。
        if path and title.startswith(path):
            label = title
        elif path:
            label = f"{path} {title}".strip()
        else:
            label = title
        hay = normalize_text(f"{path} {title}")
        matched = [k for k in kws if normalize_text(k) and normalize_text(k) in hay]
        entries.append(
            {
                "id": str(r.id),
                "label": label,
                "group": f"第 {_SECTION_NO_RE.match(title).group(1)} 章" if _SECTION_NO_RE.match(title) else None,
                "recommended": bool(matched),
                "_matched": len(matched),
                "_sort": _sort_key(path or title),
            }
        )
    # 推荐的排前（命中多者优先），其余保持教材原有先后次序——排序是显式的，
    # 便于选择卡直接按数组顺序渲染，不必二次排序。
    entries.sort(key=lambda e: (0 if e["recommended"] else 1, -e["_matched"], e["_sort"]))
    for e in entries:
        e.pop("_matched", None)
        e.pop("_sort", None)
    return entries


async def is_authorized_source(session: AsyncSession, value: str | None) -> bool:
    """value 是否命中已授权教材（TextbookSource）主键（rights 与教材选择卡同源）。

    selection_ids 直传自愈归正用：教材卡选项 id 实为 TextbookSource 主键，前端历史
    版本会把它误放进 section_id（脏值经 intent 直写 srow.intent 造成死局）。仅当该 id
    确实是已授权教材时才允许归正——不猜、不越权；未授权/未知 id 一律 False。
    """
    if not value:
        return False
    try:
        uid = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    row = (
        await session.execute(
            select(TextbookSource.id).where(
                TextbookSource.id == uid,
                TextbookSource.rights_status.in_(_AUTHORIZED_RIGHTS),
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def section_belongs_to_source(
    session: AsyncSession, *, source_id: str | None, section_id: str | None
) -> bool:
    """section_id 是否属于当前 source 的**授权**章节（同册 + 教材 rights 有效）。

    非法组合（LLM 幻觉 id / 前端误传 / 历史脏数据）不得落 srow.intent——否则后续
    每轮都带着死组合，教师无路可走。编排层按 deltas 语义把非法 section_id 视为
    「本轮未提供」→ 交回选择卡。source 缺失或 id 形状非法一律 False。
    """
    if not source_id or not section_id:
        return False
    try:
        src_uid = source_id if isinstance(source_id, uuid.UUID) else uuid.UUID(str(source_id))
        sec_uid = section_id if isinstance(section_id, uuid.UUID) else uuid.UUID(str(section_id))
    except (ValueError, AttributeError, TypeError):
        return False
    row = (
        await session.execute(
            select(TextbookSection.id)
            .join(TextbookSource, TextbookSource.id == TextbookSection.textbook_id)
            .where(
                TextbookSection.id == sec_uid,
                TextbookSection.textbook_id == src_uid,
                TextbookSource.rights_status.in_(_AUTHORIZED_RIGHTS),
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def resolve_source_title(session: AsyncSession, source_id: str) -> str | None:
    # 与 list_section_options 同因：UUID 列的通用处理器要求 uuid.UUID（SQLite/通用
    # 方言下字符串绑定会以 'str' has no attribute 'hex' 失败），统一归一。
    try:
        source_uid = source_id if isinstance(source_id, uuid.UUID) else uuid.UUID(str(source_id))
    except (ValueError, AttributeError, TypeError):
        return None
    row = (
        (
            await session.execute(
                select(TextbookSource).where(TextbookSource.id == source_uid)
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        return None
    return f"{row.publisher} · {row.edition} · {row.grade} · {row.volume}"


async def resolve_section_title(session: AsyncSession, section_id: str) -> tuple[str | None, str | None]:
    """返回 (title, section_path)；找不到 → (None, None)。"""
    import uuid as _uuid

    try:
        section_uuid = _uuid.UUID(str(section_id))
    except (ValueError, AttributeError, TypeError):
        return None, None
    row = (
        (
            await session.execute(
                select(TextbookSection).where(TextbookSection.id == section_uuid)
            )
        )
        .scalar_one_or_none()
    )
    if row is None:
        return None, None
    return row.title, row.section_path


# ── 意图选定册次的确定性一致性校正（内容正确性兜底）───────────────────────────
# 背景：LLM 只能凭「版本·册次」标签猜册次，实测「高二椭圆」被解析到选择性必修第二册
# （数列/导数），而椭圆在选择性必修第一册。以下为可复现、可单测的兜底校正。


def normalize_text(text: str) -> str:
    """归一化匹配键：全角→半角（含全角空格）+ 大小写折叠 + 去除全部空白。"""
    if not text:
        return ""
    buf: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:  # 全角可打印字符 → ASCII
            code -= 0xFEE0
        elif code == 0x3000:  # 全角空格
            code = 0x20
        buf.append(chr(code))
    return "".join("".join(buf).casefold().split())


def _matched_keywords(texts: Sequence[str], keywords: Sequence[str]) -> list[str]:
    """章节文本（title/section_path）中命中的知识点关键词（归一化后子串匹配）。"""
    haystack = [normalize_text(t) for t in texts]
    hits: list[str] = []
    for kw in keywords:
        needle = normalize_text(kw)
        if needle and any(needle in hay for hay in haystack):
            hits.append(kw)
    return hits


def choose_corrected_source(
    section_texts: Mapping[str, Sequence[str]],
    *,
    source_id: str | None,
    knowledge_points: Sequence[str] | None,
) -> str | None:
    """确定性校正（纯函数）：返回应改用的 source_id；无需校正返回 None。

    规则（与产品约定一致，不猜、不扩大改动）：
    - knowledge_points 为空 / source_id 缺失 → None（保持 LLM 原选择）；
    - 当前 source 的章节文本命中任一关键词 → None（LLM 选对了）；
    - 否则统计其它 source 的命中：恰好 1 个候选命中 → 返回该 source；0 或 ≥2 → None。
    """
    if not source_id or not knowledge_points:
        return None
    if _matched_keywords(section_texts.get(source_id, []), knowledge_points):
        return None
    candidates = [
        sid
        for sid, texts in section_texts.items()
        if sid != source_id and _matched_keywords(texts, knowledge_points)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def apply_source_correction(
    selection: Mapping[str, Any], correction: Mapping[str, Any]
) -> dict[str, Any]:
    """把校正落到选择（纯函数，返回新 dict）：换册 + 清空跨书遗留的 section_id。"""
    fixed = dict(selection)
    fixed["source_id"] = correction["to"]
    fixed["section_id"] = None  # 旧册 section 属于旧 source，不可跨书沿用
    return fixed


async def load_source_section_texts(
    session: AsyncSession, *, limit_sources: int = 200
) -> dict[str, list[str]]:
    """已授权 source → 章节文本（title 与 section_path），供确定性校正匹配知识点。

    与 list_textbook_options 同源（rights_status IN ACTIVE/STAGED），不引入新数据源。
    """
    source_ids = (
        (
            await session.execute(
                select(TextbookSource.id)
                .where(TextbookSource.rights_status.in_(_AUTHORIZED_RIGHTS))
                .order_by(TextbookSource.publisher, TextbookSource.grade, TextbookSource.volume)
                .limit(min(limit_sources, 200))
            )
        )
        .scalars()
        .all()
    )
    if not source_ids:
        return {}
    rows = (
        await session.execute(
            select(
                TextbookSection.textbook_id,
                TextbookSection.title,
                TextbookSection.section_path,
            ).where(TextbookSection.textbook_id.in_(list(source_ids)))
        )
    ).all()
    out: dict[str, list[str]] = {str(sid): [] for sid in source_ids}
    for textbook_id, title, path in rows:
        bucket = out.setdefault(str(textbook_id), [])
        if title and title.strip():
            bucket.append(title)
        if path and path.strip():
            bucket.append(path)
    return out


async def reconcile_textbook_selection(
    session: AsyncSession,
    *,
    source_id: str | None,
    knowledge_points: Sequence[str] | None,
) -> dict[str, Any] | None:
    """LLM 选定 source 后的一致性校正（DB 版）；无需校正返回 None。

    返回留痕 dict：{"from": 原 source, "to": 校正后 source, "matched": 命中关键词}。
    """
    if not source_id or not knowledge_points:
        return None
    section_texts = await load_source_section_texts(session)
    if source_id not in section_texts:
        return None  # 未知/未授权 source：不越权改写，交由既有 fail-closed 校验处理
    target = choose_corrected_source(
        section_texts, source_id=source_id, knowledge_points=knowledge_points
    )
    if target is None:
        return None
    return {
        "from": source_id,
        "to": target,
        "matched": _matched_keywords(section_texts.get(target, []), knowledge_points),
    }


__all__ = [
    "list_textbook_options",
    "list_section_options",
    "resolve_source_title",
    "resolve_section_title",
    "normalize_text",
    "choose_corrected_source",
    "apply_source_correction",
    "load_source_section_texts",
    "reconcile_textbook_selection",
    "is_authorized_source",
    "section_belongs_to_source",
]
