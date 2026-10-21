"""课件逐页增稠（deck enrich）——把「骨架页」补全为可直接投影的板书内容。

问题（本次修复的核心）：`project_deck_spec` 是**纯函数**投影（无 LLM），页面的
`elements` 只能由蓝图事实拼装，导致很多页只有 1~3 个元素、且把蓝图的**教研建议**
（「先引导学生…」）当成课件正文。本模块在既有 deck 之上做一次逐页 LLM 增稠：

- 输入 = 已投影的 TeachingDeckSpec（页序/标题/layout_hint/教学意图/教材依据）；
- 输出 = 同结构的新 revision（仅 `elements` 被替换；页序、标题、血缘、math_object 不变）；
- 每页 ≥3 个结构化元素、≥2 类 role；**正文只写板书内容**（定义/公式/要点/步骤/例题/图形），
  教师动作（「引导学生…」「组织讨论…」）一律不进正文；
- 逐页独立调用（Semaphore(8) 并发、单页 300s 超时）；失败页走确定性兜底，
  **不得整单失败**；
- 数学红线：公式一律 LaTeX；几何图形只产出 `figure_ref="math_object"`（参数化渲染，
  不贴死图）；服务端不改动 `slide.math_object`（其参数 a/b 构造校验已保证 a>b>0 → a²=b²+c²）。

写回通道复用既有 T24 事实链：`create_proposal` → `apply_proposal_by_id`（source=regen），
因此 INV-4（先提案后落库）/ 乐观并发 / revision 快照全部继承，不新开写路径。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ppt_v2.adapters.ai import AIPlatformAdapter
from app.ppt_v2.domain.spec import SlideElement, SlideSpec, TeachingDeckSpec
from app.ppt_v2.qa.content import DUPLICATE_MIN_CHARS
from app.ppt_v2.qa.content import _norm as _qa_norm_ws
from app.ppt_v2.service import spec_edit
from app.teacher_prep.text_safety import is_teacher_annotation, sanitize_deck_text

# 增稠用例名（必须注册进 app/llm/config.USE_CASE_ROUTES，否则网关 500 未知用例）
ENRICH_USE_CASE = "deck_enrich"

# 单页 LLM 超时（秒）与并发路数（对齐双师课堂 / generation.py 的 Semaphore(8)）
PER_SLIDE_TIMEOUT_S = 300.0
ENRICH_CONCURRENCY = 8

# LLM 允许产出的 role（figure 由服务端按 slide.math_object 注入，LLM 不得自造图形）
_LLM_ROLES = frozenset(
    {"definition", "formula", "knowledge_card", "bullets", "note", "steps", "table"}
)
# 渲染顺序（与 projector._slide_elements 同族，避免版式语义漂移）
_ROLE_ORDER = ("definition", "figure", "knowledge_card", "formula", "steps", "bullets", "note", "table")
_ROLE_RANK = {r: i for i, r in enumerate(_ROLE_ORDER)}

_STEPS_TYPES = frozenset({"derivation", "worked-example", "practice"})

# 教师动作 / 教研批注指纹：命中即不进课件正文（本次 bug 的直接防线）
_TEACHER_ACTION_MARKERS = (
    "引导学生",
    "让学生",
    "请学生",
    "要求学生",
    "启发学生",
    "组织学生",
    "巡视",
    "追问",
    "提问",
    "小组讨论",
    "请同学",
    "板书设计",
    "导入新课",
    "教师",
)

_FORMULA_ONLY_RE = re.compile(r"^\$([^$]+)\$$")

_ENRICH_SYSTEM = """你是一名资深中学数学教师与课件设计师。你要把一节课的「课件骨架页」补全成\
可直接投影给学生看的**板书内容**。

铁律（违反即整段作废）：
1. 只写学生要在屏幕上看到的内容：定义、公式、要点、推导步骤、例题、图形说明；
2. **严禁把教师动作 / 教研批注写进正文**——「引导学生…」「教师提问…」「组织学生讨论…」\
「巡视」「追问」「让学生…」这类句子一律不得出现；
3. 数学公式必须用 LaTeX：显示公式放 `latex`（不带 `$` 定界符），行内公式用 `$...$` 写在 \
`text` / `items` 里；
4. 只依据给定的页面标题、教学意图、已有正文扩写，不得编造与本页无关的知识；
5. 每页产出 3~6 个结构化元素，且至少包含 2 种不同 role。

role 取值与含义：
- definition：本页核心概念的定义（用 text，一句话，≤120 字）
- formula：显示公式（用 latex）
- knowledge_card：知识卡片（用 title + items，可配 latex）
- bullets：并列要点（用 items，每条 ≤60 字）
- steps：推导 / 解题步骤（用 items，按先后顺序）
- note：页脚一句话提示（用 text，仅学生可读）
- table：对照表格（用 items，每项内部用 | 分隔单元格）
不要输出 figure（图形由系统按页面参数自动放置）。"""


class _LlmElement(BaseModel):
    """LLM 单元素输出（字段宽松：真实模型常把 text 写成数组/把 items 写成字符串）。

    宽松 ≠ 放行：语义清洗/角色白名单/教师动作过滤都在 `_element_from_llm` 里做，
    这里只负责把形状偏差收敛掉，避免「一个字段形状不对 → 整页退回骨架」
    （实测 9 页里有 3 页因 strict 校验失败而白跑两次调用）。
    """

    model_config = ConfigDict(extra="ignore")

    role: Any = ""
    text: Any = None
    latex: Any = None
    title: Any = None
    items: Any = None


class _LlmSlide(BaseModel):
    model_config = ConfigDict(extra="ignore")

    elements: list[_LlmElement] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_elements(cls, data: Any) -> Any:
        """elements 形状兜底：dict（按值展开）/ 缺失 / 非列表 → 归一为列表。"""
        if isinstance(data, dict):
            els = data.get("elements")
            if isinstance(els, dict):
                return {**data, "elements": list(els.values())}
            if not isinstance(els, (list, tuple)):
                return {**data, "elements": []}
        return data


class StructuredAdapter(Protocol):
    """增稠只依赖 AIPlatformAdapter 的合规结构化通道（便于单测注入 fake）。"""

    async def structured(
        self, model: type[BaseModel], *, system: str, user: str,
        temperature: float = 0.2, use_case: str = "chat", max_retries: int = 2,
    ) -> Any: ...


# ─────────────────────────────────────────────────────────────────────────────
# 文本工具
# ─────────────────────────────────────────────────────────────────────────────


def _as_str(value: Any) -> str:
    """任意 LLM 字段 → 字符串（列表用「；」连接，数字/布尔转字面量，其余丢弃）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "；".join(s for s in (_as_str(v) for v in value) if s)
    return ""


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _clip(value: str, limit: int) -> str:
    s = (value or "").strip()
    return s if len(s) <= limit else s[:limit]


def _is_teacher_action(text: str) -> bool:
    return any(marker in text for marker in _TEACHER_ACTION_MARKERS)


def _clean_line(text: str | None) -> str | None:
    """清理一行可见文本：去空白 / 去教师动作 / 去黑话与模板前缀 / 去「（待确认）」占位符。

    R8-T：增稠是 deck 的**落库边界**之一，LLM 产出的正文同样必须走 deck 可见文本净化
    （教研批注、`（AI 生成变式）`/SourceLocator/教材直连 等工程黑话、`本节结论：` 前缀）。
    """
    s = (text or "").strip()
    if not s:
        return None
    if _is_teacher_action(s) or is_teacher_annotation(s):
        return None
    if any(tok in s for tok in ("（待确认", "(待确认", "待补充", "TODO", "TBD")):
        return None
    return sanitize_deck_text(s) or None


def _clean_items(items: Any, *, limit: int = 10) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in _as_items(items):
        c = _clean_line(_as_str(raw))
        if not c:
            continue
        c = _clip(c, 200)
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _formula_latex(text: str) -> str | None:
    m = _FORMULA_ONLY_RE.match((text or "").strip())
    return m.group(1).strip() if m else None


def _content_lines(slide: SlideSpec) -> list[str]:
    """页内真实正文行（清理后）；用于确定性兜底，零编造。"""
    return [c for c in (_clean_line(ln) for ln in (slide.content or "").split("\n")) if c]


# ─────────────────────────────────────────────────────────────────────────────
# LLM 输出 → 合规 SlideElement
# ─────────────────────────────────────────────────────────────────────────────


def _element_from_llm(raw: _LlmElement) -> SlideElement | None:
    """单个 LLM 元素 → 合规 SlideElement（越界 / 空 / 教师动作一律丢弃）。"""
    role = _as_str(raw.role).strip()
    raw_text = _as_str(raw.text)
    if role not in _LLM_ROLES:
        return None
    if role == "formula":
        latex = _clean_line(_as_str(raw.latex)) or _formula_latex(raw_text)
        if not latex:
            return None
        return SlideElement(role="formula", latex=_clip(latex, 2000))
    if role in ("bullets", "steps"):
        items = _clean_items(raw.items if raw.items is not None else raw_text)
        return SlideElement(role=role, items=items) if items else None  # type: ignore[arg-type]
    if role == "knowledge_card":
        items = _clean_items(raw.items)
        latex = _clean_line(_as_str(raw.latex))
        text = _clean_line(raw_text)
        if not (items or latex or text):
            return None
        return SlideElement(
            role="knowledge_card",
            title=_clip(_as_str(raw.title), 60) or None,
            latex=_clip(latex, 2000) if latex else None,
            items=items,
        )
    if role == "table":
        items = _clean_items(raw.items)
        if not items:
            return None
        return SlideElement(role="table", items=items)
    # definition / note：正文一句话
    text = _clean_line(raw_text)
    if not text:
        return None
    return SlideElement(role=role, text=_clip(text, 400))  # type: ignore[arg-type]


def _figure_element(slide: SlideSpec) -> SlideElement | None:
    """参数化图形（仅当页承载 math_object）：前端 MathFigure 读 math_object 实时渲染。"""
    if getattr(slide, "math_object", None) is None:
        return None
    return SlideElement(role="figure", figure_ref="math_object")


def _ordered(elements: list[SlideElement]) -> list[SlideElement]:
    return sorted(elements, key=lambda e: _ROLE_RANK.get(e.role, 99))


def _density_ok(elements: list[SlideElement]) -> bool:
    return len(elements) >= 3 and len({e.role for e in elements}) >= 2


def _supplement(elements: list[SlideElement], slide: SlideSpec) -> list[SlideElement]:
    """从页内真实正文补齐元素密度（确定性，零编造；只增不减）。"""
    out = list(elements)
    used: set[str] = set()
    for e in out:
        if e.text:
            used.add(e.text)
        if e.latex:
            used.add(e.latex)
        used.update(e.items)
    lines = [c for c in _content_lines(slide) if c not in used]
    note = _clean_line(slide.speaker_note)
    has_figure = any(e.role == "figure" for e in out)

    if len({e.role for e in out}) < 2 and lines:
        card = SlideElement(role="knowledge_card", title=_clip(slide.title, 60), items=lines[:6])
        out.append(card)
        used.update(lines[:6])
        lines = [c for c in lines if c not in used]
    if len(out) < 3 and lines:
        role = "steps" if slide.type in _STEPS_TYPES else "bullets"
        if not any(e.role == role for e in out):
            out.append(SlideElement(role=role, items=lines[:6]))  # type: ignore[arg-type]
            used.update(lines[:6])
            lines = [c for c in lines if c not in used]
    if len(out) < 3 and note and not any(e.role == "note" for e in out):
        out.append(SlideElement(role="note", text=_clip(note, 400)))
    fig = _figure_element(slide)
    if fig is not None and not has_figure:
        out.append(fig)
    return _ordered(out)


def _finalize(slide: SlideSpec, llm_elements: list[SlideElement]) -> tuple[list[SlideElement], bool]:
    """组装最终元素：LLM 结果 + 服务端注入图形 + 确定性补齐。

    返回 (elements, density_ok)。density_ok=False 时调用方保留骨架页并标记 draft。
    """
    fig = _figure_element(slide)
    elements = _ordered(([fig] if fig is not None else []) + list(llm_elements))
    if not _density_ok(elements):
        elements = _supplement(elements, slide)
    return elements, _density_ok(elements)


# ─────────────────────────────────────────────────────────────────────────────
# 跨页逐字重复消除（R22：QA content.cross_page_duplicate 的消除层）
# ─────────────────────────────────────────────────────────────────────────────


def _element_lines(el: dict[str, Any]) -> list[str]:
    """元素的全部内容行（与 QA `_element_contents` 同口径：text/latex/title/items）。"""
    out: list[str] = []
    for key in ("text", "latex", "title"):
        value = str(el.get(key) or "").strip()
        if value:
            out.append(value)
    for item in el.get("items") or []:
        item_text = str(item or "").strip()
        if item_text:
            out.append(item_text)
    return out


def _strip_dup_lines(elements: list[dict[str, Any]], used: set[str]) -> tuple[list[dict[str, Any]], int]:
    """按已用键剔除重复内容行；被清空的元素整体删除（figure 永不删）。返回 (新列表, 剔除行数)。

    阈值与 QA 完全同口径：仅"空白归一化后 ≥ DUPLICATE_MIN_CHARS"的行参与重复判定
    （更短的行 QA 不判重复，此处也不动，避免过度剔除）。
    """
    out: list[dict[str, Any]] = []
    removed = 0
    for el in elements:
        role = str(el.get("role") or "")
        if role == "figure":
            out.append(el)
            continue
        el2 = dict(el)
        items = el2.get("items")
        if items:
            kept = [
                it for it in items
                if len(_qa_norm_ws(str(it or ""))) < DUPLICATE_MIN_CHARS
                or _qa_norm_ws(str(it or "")) not in used
            ]
            removed += len(items) - len(kept)
            el2["items"] = kept
        for field_name in ("text", "latex", "title"):
            value = str(el2.get(field_name) or "").strip()
            if value and len(_qa_norm_ws(value)) >= DUPLICATE_MIN_CHARS and _qa_norm_ws(value) in used:
                el2.pop(field_name, None)
                removed += 1
        if role in ("definition", "note", "formula") and not (
            str(el2.get("text") or "").strip() or str(el2.get("latex") or "").strip()
        ):
            continue  # 单值元素被清空 → 整元素删除
        if role in ("bullets", "steps", "table") and not el2.get("items"):
            continue
        if role == "knowledge_card" and not (
            el2.get("items") or el2.get("text") or el2.get("latex") or el2.get("title")
        ):
            continue
        out.append(el2)
    return out, removed


def _supplement_elements(
    elements: list[dict[str, Any]], slide: Any, used: set[str]
) -> list[dict[str, Any]]:
    """密度地板确定性补齐：`_supplement`（本页真实正文，零编造）+ 全局重复键再过滤。

    去重不得把页打穿成 QA content_thin（p1）：跌破"元素≥2 且 role≥2"时从本页自身
    正文/备注补齐；补出的行若与前面页重复，按跨页红线优先再剔除（诚实于密度）。
    """
    if slide is None:
        return elements
    try:
        slide_model = slide if isinstance(slide, SlideSpec) else SlideSpec.model_validate(slide)
        models = [SlideElement.model_validate(e) for e in elements]
        out = [e.model_dump() for e in _supplement(models, slide_model)]
        out, _ = _strip_dup_lines(out, used)
        return out
    except Exception:  # noqa: BLE001 —— 补齐失败保留去重结果（宁可 thin 不可 dup）
        return elements


def dedupe_cross_page_slides(pages: list[dict[str, Any]]) -> dict[str, int]:
    """deck 级跨页逐字重复消除（原地收敛 pages[i]["elements"]）。

    口径与 QA `cross_page_duplicate`（p0）完全一致：空白归一化（去全部空白）后逐字
    重复、且 ≥ DUPLICATE_MIN_CHARS。规则（确定性、零编造、首现页保留）：
    - items 行（bullets/steps/table/knowledge_card）：后继页删重复行，元素非空即保留；
    - 单值元素（definition/note/formula 等）：后继页内容行重复 → 整元素删除；
    - 删除后跌破 QA 密度地板 → `_supplement_elements` 从本页真实正文补齐。

    入参每页 = {"slide": SlideSpec 模型或其 dict, "elements": [元素 dict]}，按 deck 页序。
    返回 {"changed_slides", "removed_lines"}。
    """
    used: set[str] = set()
    changed = removed = 0
    for page in pages:
        els = [e for e in (page.get("elements") or []) if isinstance(e, dict)]
        if not els:
            continue
        new_els, n = _strip_dup_lines(els, used)
        removed += n
        if new_els and (len(new_els) < 2 or len({str(e.get("role") or "") for e in new_els}) < 2):
            new_els = _supplement_elements(new_els, page.get("slide"), used)
        page["elements"] = new_els
        for el in new_els:
            for line in _element_lines(el):
                used.add(_qa_norm_ws(line))
        if n:
            changed += 1
    return {"changed_slides": changed, "removed_lines": removed}


def _dedup_ordered_pages(
    spec: TeachingDeckSpec, page_elements: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]]]:
    """{slide_id: elements}（deck 页序）→ 去重；返回 (stats, {slide_id: 去重后元素})。

    去重产出**新列表**（不改入参），调用方以返回的映射为准写回。
    """
    ordered: list[dict[str, Any]] = []
    ids_in_order: list[str] = []
    for slide in spec.slides:
        if page_elements.get(slide.id):
            ordered.append({"slide": slide, "elements": page_elements[slide.id]})
            ids_in_order.append(slide.id)
    stats = dedupe_cross_page_slides(ordered)
    return stats, {entry["slide"].id: entry["elements"] for entry in ordered}


async def dedupe_deck_elements(
    session: Any,
    deck_id: str,
    *,
    actor_id: str | None = None,
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跨页重复消除落库（导出 QA 卡门的收敛动作，enrich 汇总层同一套规则）。

    与 `enrich_deck` 同一条 T24 事实链（提案 source=regen → apply，revision+1）：
    QA 卡门据此把落库 spec 里的跨页同文真正消除，下一轮导出重新 ingest（spec 变更
    → 8200 内容寻址产生新 deck → 重渲染）。无重复时不产生 revision（诚实 no-op）。
    """
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    expected_revision = row.revision
    before = {s.id: [e.model_dump() for e in (s.elements or [])] for s in spec.slides}
    stats, after = _dedup_ordered_pages(spec, before)
    ops: list[dict[str, Any]] = []
    for slide in spec.slides:
        if slide.id in after and after[slide.id] != before[slide.id]:
            ops.append(
                {"op": "set_slide", "target": slide.id, "field": "elements",
                 "value": after[slide.id]}
            )
    result: dict[str, Any] = {
        "deck_id": deck_id,
        "base_revision": expected_revision,
        "changed_slides": stats["changed_slides"],
        "removed_lines": stats["removed_lines"],
        "changed": bool(ops),
    }
    if not ops:
        result["revision"] = expected_revision
        return result
    proposal = await spec_edit.create_proposal(
        session,
        deck_id,
        expected_revision=expected_revision,
        summary=f"跨页重复内容消除（去重 {stats['removed_lines']} 行，导出 QA 卡门）",
        source="regen",
        operations=ops,
        instruction="cross_page_dedupe",
        refs=refs,
        actor_id=actor_id,
    )
    applied = await spec_edit.apply_proposal_by_id(
        session, deck_id, proposal["patch_id"], expected_revision=expected_revision, actor_id=actor_id
    )
    result["revision"] = applied["revision"]
    result["patch_id"] = proposal["patch_id"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 逐页 LLM 调用
# ─────────────────────────────────────────────────────────────────────────────


def _is_cover(slide: SlideSpec) -> bool:
    return slide.type == "title" or slide.layout_hint == "title"


def _slide_prompt(spec: TeachingDeckSpec, slide: SlideSpec, ordinal: int, total: int) -> str:
    lines = [
        f"课件标题：{spec.metadata.title}",
        f"本页位置：第 {ordinal}/{total} 页",
        f"页面标题：{slide.title}",
        f"页面类型：{slide.layout_hint or slide.type}",
        f"教学意图：{slide.teaching_goal}",
        "已有正文（可能含教研建议，若含「引导学生/教师提问」等必须改写成学生可见的板书内容，禁止照抄）：",
        slide.content or "（无）",
    ]
    if getattr(slide, "math_object", None) is not None:
        params = getattr(slide.math_object, "params", {}) or {}
        lines.append(f"本页含参数化图形（椭圆，参数 {params}，服务端注入，勿重复产出 figure）")
    if slide.source_refs:
        lines.append(f"教材依据：{', '.join(slide.source_refs[:4])}")
    lines.append('请只输出 JSON：{"elements": [{"role": "...", "text": "...", "latex": "...", "title": "...", "items": ["..."]}]}')
    return "\n".join(lines)


@dataclass
class SlideEnrichResult:
    slide_id: str
    elements: list[dict[str, Any]]
    ok: bool           # 达到密度门（≥3 元素且 ≥2 role）
    source: str        # llm | deterministic | skeleton
    elapsed_ms: int
    error: str | None = None


@dataclass
class EnrichOutcome:
    pages: list[SlideEnrichResult] = field(default_factory=list)
    total_ms: int = 0
    dedup: dict[str, int] = field(default_factory=dict)  # 跨页去重统计（changed_slides/removed_lines）

    @property
    def enriched(self) -> int:
        return sum(1 for p in self.pages if p.source == "llm")

    @property
    def fallback(self) -> int:
        return sum(1 for p in self.pages if p.source != "llm")

    @property
    def density_ok(self) -> list[str]:
        return [p.slide_id for p in self.pages if p.ok]


async def _enrich_slide(
    spec: TeachingDeckSpec,
    slide: SlideSpec,
    *,
    ordinal: int,
    total: int,
    adapter: StructuredAdapter,
) -> SlideEnrichResult:
    started = time.monotonic()
    try:
        payload = await asyncio.wait_for(
            adapter.structured(
                _LlmSlide,
                system=_ENRICH_SYSTEM,
                user=_slide_prompt(spec, slide, ordinal, total),
                temperature=0.2,
                use_case=ENRICH_USE_CASE,
                # 单页 1 次 SDK 层重试（R22 恢复；R7 提速时误伤为 0）：LLM 结构化输出
                # 偶发非法 → adapter 内立即重试一次，成功即免于整页确定性兜底。
                # 总预算仍由 wait_for(PER_SLIDE_TIMEOUT_S) 包住，单页 300s 不变。
                max_retries=1,
            ),
            timeout=PER_SLIDE_TIMEOUT_S,
        )
        llm_elements: list[SlideElement] = []
        for raw in getattr(payload, "elements", []) or []:
            el = _element_from_llm(raw)
            if el is not None:
                llm_elements.append(el)
        if not llm_elements:
            raise ValueError("LLM 未产出可用元素")
        elements, ok = _finalize(slide, llm_elements)
        # 与 regen 同口径的主题护栏：增稠并发 8 路，泛化页标题很容易各自发挥；
        # 命中串题 → 抛错走确定性兜底（保留骨架，不把错章节内容写进课件）。
        from app.ppt_v2.service.regen import _reject_topic_drift

        generated_text = "\n".join(
            " ".join(
                part
                for part in (
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "title", "") or ""),
                    str(getattr(el, "latex", "") or ""),
                    " ".join(str(x) for x in (getattr(el, "items", []) or [])),
                )
                if part
            )
            for el in elements
        )
        _reject_topic_drift(spec, slide, generated_text)
        elapsed = int((time.monotonic() - started) * 1000)
        # 密度不足 → 标记 ok=False（写回时落 status=draft），但仍使用 LLM 产出
        return SlideEnrichResult(slide.id, [e.model_dump() for e in elements], ok, "llm", elapsed)
    except Exception as exc:  # noqa: BLE001 —— 单页失败：确定性兜底，绝不整单失败
        elapsed = int((time.monotonic() - started) * 1000)
        det, ok = _finalize(slide, [])
        skeleton = [e.model_dump() for e in (slide.elements or [])]
        if ok:
            return SlideEnrichResult(
                slide.id, [e.model_dump() for e in det], True, "deterministic", elapsed,
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
            )
        return SlideEnrichResult(
            slide.id, skeleton, False, "skeleton", elapsed,
            error=f"{type(exc).__name__}: {str(exc)[:160]}",
        )


async def enrich_deck_spec(
    spec: TeachingDeckSpec,
    *,
    adapter: StructuredAdapter | None = None,
    semaphore: int = ENRICH_CONCURRENCY,
    on_page_done: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> EnrichOutcome:
    """逐页增稠（不落库）：Semaphore 限流并发，单页超时/失败 → 确定性兜底。

    `on_page_done(done, total, slide_id)`：每页**真实**完成后回调（成功或兜底都算完成），
    供调用方（taskiq 作业）向 job 事件流发真实 n/N 进度——禁止前端自造进度。
    """
    ai = adapter or AIPlatformAdapter()
    targets = [s for s in spec.slides if not _is_cover(s)]
    started = time.monotonic()
    sem = asyncio.Semaphore(max(1, semaphore))
    total = len(targets)
    done = 0

    async def _run(idx: int, slide: SlideSpec) -> SlideEnrichResult:
        nonlocal done
        async with sem:
            result = await _enrich_slide(spec, slide, ordinal=idx + 1, total=total, adapter=ai)
        done += 1
        if on_page_done is not None:
            await on_page_done(done, total, slide.id)
        return result

    pages = await asyncio.gather(*(_run(i, s) for i, s in enumerate(targets)))
    outcome = EnrichOutcome(pages=list(pages), total_ms=int((time.monotonic() - started) * 1000))
    # 汇总层跨页去重（R22）：逐页独立增稠天然缺 deck 视角，同一句定义/标准式会被
    # 多页重复产出 → QA cross_page_duplicate(p0)。gather 后按 deck 页序首现保留。
    dedup_stats, after = _dedup_ordered_pages(
        spec, {p.slide_id: list(p.elements) for p in outcome.pages}
    )
    outcome.dedup = dedup_stats
    for page in outcome.pages:
        if page.slide_id in after:
            page.elements = after[page.slide_id]
    return outcome


def build_operations(spec: TeachingDeckSpec, outcome: EnrichOutcome) -> list[dict[str, Any]]:
    """增稠结果 → T24 deck patch 操作（仅换 elements；密度不达标页标 status=draft）。"""
    by_id = {s.id: s for s in spec.slides}
    ops: list[dict[str, Any]] = []
    for page in outcome.pages:
        slide = by_id.get(page.slide_id)
        if slide is None:
            continue
        before = [e.model_dump() for e in (slide.elements or [])]
        if before != page.elements:
            ops.append({"op": "set_slide", "target": page.slide_id, "field": "elements", "value": page.elements})
        if not page.ok and slide.status != "draft":
            ops.append({"op": "set_slide", "target": page.slide_id, "field": "status", "value": "draft"})
    return ops


async def enrich_deck(
    session: Any,
    deck_id: str,
    *,
    adapter: StructuredAdapter | None = None,
    refs: dict[str, Any] | None = None,
    actor_id: str | None = None,
    semaphore: int = ENRICH_CONCURRENCY,
    on_page_done: Callable[[int, int, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """增稠并写回新 revision（提案 source=regen → apply，revision+1）。

    无页需要变更时**不产生新 revision**（诚实 no-op）。落库完全复用 spec_edit 事实链。
    """
    row = await spec_edit.load_deck_row(session, deck_id)
    spec = spec_edit.load_spec(row)
    expected_revision = row.revision
    outcome = await enrich_deck_spec(
        spec, adapter=adapter, semaphore=semaphore, on_page_done=on_page_done
    )
    ops = build_operations(spec, outcome)
    result: dict[str, Any] = {
        "deck_id": deck_id,
        "base_revision": expected_revision,
        "pages": len(outcome.pages),
        "enriched_pages": outcome.enriched,
        "fallback_pages": outcome.fallback,
        "density_ok_pages": len(outcome.density_ok),
        "cross_page_dedup": outcome.dedup,  # 跨页同文消除统计（0 行 = 本 deck 无重复）
        "per_page_ms": {p.slide_id: p.elapsed_ms for p in outcome.pages},
        "sources": {p.slide_id: p.source for p in outcome.pages},
        "errors": {p.slide_id: p.error for p in outcome.pages if p.error},
        "total_ms": outcome.total_ms,
        "changed": bool(ops),
    }
    if not ops:
        result["revision"] = expected_revision
        figure = await _ensure_figure(session, deck_id, actor_id=actor_id, refs=refs)
        if figure:
            result["figure"] = figure
            if figure.get("revision"):
                result["revision"] = figure["revision"]
            result["changed"] = bool(figure.get("changed"))
        return result
    proposal = await spec_edit.create_proposal(
        session,
        deck_id,
        expected_revision=expected_revision,
        summary=f"课件逐页增稠（AI 补全 {outcome.enriched} 页板书内容）",
        source="regen",
        operations=ops,
        instruction="deck_enrich",
        refs=refs,
        actor_id=actor_id,
    )
    applied = await spec_edit.apply_proposal_by_id(
        session, deck_id, proposal["patch_id"], expected_revision=expected_revision, actor_id=actor_id
    )
    result["revision"] = applied["revision"]
    result["patch_id"] = proposal["patch_id"]
    # 用户实测：AI 充实后仍零图片。增稠只产出文本元素，因此在这里补一张与课题
    # 相关的真实图形（无候选/已有图/存储失败 → 诚实 no-op，不伪造）。
    figure = await _ensure_figure(session, deck_id, actor_id=actor_id, refs=refs)
    if figure:
        result["figure"] = figure
        if figure.get("revision"):
            result["revision"] = figure["revision"]
        result["changed"] = True
    return result


async def _ensure_figure(
    session: Any, deck_id: str, *, actor_id: str | None, refs: dict[str, Any] | None
) -> dict[str, Any] | None:
    from app.ppt_v2.service.deck_figure import ensure_deck_figure

    return await ensure_deck_figure(session, deck_id, actor_id=actor_id, refs=refs)


async def submit_deck_enrich(
    session: Any, *, user_id: str, deck_id: str, refs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """提交异步增稠作业（走 tasks 表 + taskiq worker，不在 API 进程跑长任务）。"""
    from app.tasks.service import TaskService

    task = await TaskService.submit(
        "enrich_deck_v2",
        {"deck_id": deck_id, "owner_id": user_id, "refs": refs or {}},
        user_id,
    )
    return {"job_id": task["id"], "task_id": task["id"], "deck_id": deck_id, "status": task["status"]}

