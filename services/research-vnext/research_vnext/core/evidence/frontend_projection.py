"""RV07 → RV08 前端契约投影（跨轮接口对齐，2026-09-14）。

存在理由
--------
RV08 的 Workspace 已经建好了**展示层**，且其 testid 明确指向本轮：
`artifacts/evidence/EvidenceCard.vue`（`data-testid="rv07-evidence-card"`）与
`artifacts/reader/ResearchReaderFocus.vue`（`data-testid="rv07-reader-focus"`）。
但 RV07 的 `card_public_dict()` 输出的是**本域内部形状**（`source_ref` 嵌套对象、
`locator` 是对象、`rights_policy` 等），与前端 `ResearchEvidenceItem` / `ReaderAnchor`
**字段名与类型都不一致**：

| 前端字段 | 类型 | RV07 侧 | 差异 |
|---|---|---|---|
| `ResearchEvidenceItem.locator` | `string` | `locator` 是**对象** | ❌ 类型冲突 |
| `ResearchEvidenceItem.source` | `string` | `source_ref.provider` 嵌套 | ⚠️ 需推导 |
| `ResearchEvidenceItem.label` | `string` | `labels[]` 是数组 | ⚠️ 需推导 |
| `ResearchEvidenceItem.assetVersion` | `string` | 藏在 `locator.payload` | ⚠️ 需推导 |
| `ResearchEvidenceItem.authorized` | `boolean` | 由 `status`/`stale_state` 推导 | ⚠️ 需推导 |
| `ReaderAnchor.*` | 扁平 | `ResolvedLocator` 形状不同 | ❌ 需投影 |

本模块**只做投影**，不新增业务判断、不改动任何既有输出：
- 输入是 `card_public_dict()` 的返回值与 `ResolvedLocator.to_dict()` 的返回值；
- 输出严格等于前端 `types/research.ts` 里已声明的字段集合；
- **不臆造字段**：拿不到就给空值/`None`，绝不猜。

铁律
----
1. **投影不放宽权利**：`snippet` 直接取 `card_public_dict()` 的结果，本模块
   **不再**做二次裁剪，也**绝不**从别处补回被 rights 清空的原文；
2. **stale 不静默**：`stale_state=STALE` 时 `authorized=False`，前端据此展示"未授权/不可回源"，
   不得让它看起来像新鲜证据；
3. **纯函数**：不碰 DB、不碰网络、不抛业务异常。
"""

from __future__ import annotations

from typing import Any

#: 前端 `ReaderAnchorType` 的七个取值（`types/research.ts`）。
READER_ANCHOR_TYPES: frozenset[str] = frozenset(
    {"asset", "page", "bbox", "section", "table", "figure", "formula"}
)

#: 无法识别锚点类型时的兜底（前端有该枚举项，渲染为"整份资产"）。
FALLBACK_ANCHOR_TYPE = "asset"

#: RV07 `status` 中**可对外展示**的唯一取值。
_ACTIVE = "ACTIVE"

#: 只有这些来源字段可代表"这条证据来自哪里"，按可信度优先排列。
_SOURCE_KEYS: tuple[tuple[str, str], ...] = (
    ("source_ref", "provider"),
    ("provenance", "provider"),
    ("source_ref", "doi"),
    ("source_ref", "url"),
    ("source_ref", "record_kind"),
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _pick_source(public: dict[str, Any]) -> str:
    """按可信度优先级挑出"来源"展示串。"""
    for section, key in _SOURCE_KEYS:
        candidate = _as_str(_as_dict(public.get(section)).get(key)).strip()
        if candidate:
            return candidate
    return ""


def _pick_label(public: dict[str, Any]) -> str:
    """`labels[0]` 优先；否则退回证据类型（前端 `label` 与 `source` 二选一展示）。"""
    labels = public.get("labels")
    if isinstance(labels, list):
        for item in labels:
            text = _as_str(item).strip()
            if text:
                return text
    return _as_str(public.get("type"))


def locator_summary(locator: Any, resolved: Any = None) -> str:
    """把 locator **对象**压成一行人类可读串（前端 `locator` 字段是 `string`）。

    ANCHOR → `page 4 · bbox 72,120,480,300 · 第三章/3.2 导数`
    EXTERNAL → `DOI: 10.2139/ssrn.5967774`
    信息不足时返回 `""`（**不编造**）。

    `resolved`（`ResolvedLocator.to_dict()`）**可选但常需**：卡片的
    `locator.payload` 只带 `anchor_id`/`anchor_type`，**页码/bbox/section_path 在
    anchor 行上**，必须由回源结果补。缺 `resolved` 时退回 `anchor_type + anchor_id`。
    """
    data = _as_dict(locator)
    res = _as_dict(resolved)
    if not data and not res:
        return ""

    kind = (_as_str(data.get("kind")) or _as_str(res.get("kind"))).upper()
    is_anchor = kind == "ANCHOR" or (not kind and bool(res.get("anchor_type")))
    if is_anchor:
        parts: list[str] = []

        page = data.get("page")
        if page in (None, ""):
            page = res.get("page")
        if page not in (None, ""):
            parts.append(f"page {page}")

        bbox = data.get("bbox") or res.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            parts.append("bbox " + ",".join(_as_str(v) for v in bbox))

        section_path = data.get("section_path") or res.get("section_path")
        if isinstance(section_path, list) and section_path:
            parts.append("/".join(_as_str(s) for s in section_path))

        if not parts:
            # 没有页码/区域可讲时，至少说清"锚在什么类型的片段上"
            anchor_type = _as_str(data.get("anchor_type") or res.get("anchor_type")).strip()
            anchor_id = _as_str(data.get("anchor_id")).strip()
            if anchor_type:
                parts.append(anchor_type)
            if anchor_id:
                parts.append(f"anchor {anchor_id}")
        return " · ".join(parts)

    dimension = _as_str(data.get("dimension")).strip()
    for key in ("doi", "url", "asset_id", "anchor_id"):
        value = _as_str(data.get(key)).strip()
        if value:
            return f"{dimension}: {value}" if dimension else value
    return dimension


def to_frontend_evidence_item(
    public: dict[str, Any], *, resolved: Any = None
) -> dict[str, Any]:
    """RV07 卡片公开投影 → 前端 `ResearchEvidenceItem`（**严格 7 字段 + 4 加法字段**）。

    `authorized` 语义（前端据此决定是否展示摘录/定位）：
    卡片必须 `status=ACTIVE` **且** `stale_state != STALE`。
    rights 造成的摘录清空由 `card_public_dict()` 以 `snippet=""` 表达，
    本函数**不**把它翻译成 `authorized=False` —— 两者是不同的诚实态。

    `resolved` 可选：传入时能把 `locator` 串补成带页码/区域的可读定位。
    """
    locator = _as_dict(public.get("locator"))
    rights_policy = _as_dict(public.get("rights_policy"))

    status = _as_str(public.get("status"))
    stale_state = _as_str(public.get("stale_state")) or "FRESH"

    return {
        "id": _as_str(public.get("id")),
        "source": _pick_source(public),
        "label": _pick_label(public),
        "locator": locator_summary(locator, resolved),
        "snippet": _as_str(public.get("snippet")),
        "assetVersion": _as_str(locator.get("asset_version")),
        "authorized": status == _ACTIVE and stale_state != "STALE",
        # 以下为**加法**信息，前端当前不渲染（`asArray` 直通不报错）。
        # 登记在此是为了让"为什么 snippet 为空"可解释，而不是让它变成谜。
        "snippetRedacted": bool(public.get("snippet_redacted")),
        "rightsStatus": _as_str(_as_dict(public.get("rights")).get("rights_status")),
        "citationEligible": bool(public.get("citation_eligible")),
        "readerFulltextVisible": bool(rights_policy.get("reader_fulltext_visible", False)),
    }


def to_frontend_reader_anchor(
    public: dict[str, Any], resolved: dict[str, Any]
) -> dict[str, Any]:
    """RV07 卡片 + `ResolvedLocator` → 前端 `ReaderAnchor`。

    两个输入都必需：`ResolvedLocator` 不含资产身份（`resolve_from_anchor` 把
    `external` 置 `None`），资产 id/版本/解析器版本要从卡片侧取。

    `assetAvailable` 直接取 `resolved.verifiable`：stale 即"不可回源"，
    前端 `canOpen` 会因此拒绝打开，**不会**静默指向新内容。
    """
    locator = _as_dict(public.get("locator"))
    provenance = _as_dict(public.get("provenance"))
    external = _as_dict(resolved.get("external"))

    raw_type = _as_str(resolved.get("anchor_type")).strip().lower()
    anchor_type = raw_type if raw_type in READER_ANCHOR_TYPES else FALLBACK_ANCHOR_TYPE

    section_path = resolved.get("section_path")
    section = "/".join(_as_str(s) for s in section_path) if isinstance(section_path, list) else ""

    bbox = resolved.get("bbox")
    normalized_bbox = [float(v) for v in bbox] if isinstance(bbox, list) and len(bbox) == 4 else None

    return {
        "type": anchor_type,
        "assetId": _as_str(locator.get("asset_id") or external.get("asset_id")),
        "assetVersion": _as_str(
            locator.get("asset_version") or external.get("asset_version")
        ),
        "parserVersion": _as_str(provenance.get("parser_version")),
        "assetHash": _as_str(
            provenance.get("source_hash")
            or resolved.get("region_hash")
            or resolved.get("text_hash")
        ),
        "page": resolved.get("page"),
        "bbox": normalized_bbox,
        "section": section or None,
        "assetAvailable": bool(resolved.get("verifiable", False)),
    }


__all__ = [
    "FALLBACK_ANCHOR_TYPE",
    "READER_ANCHOR_TYPES",
    "locator_summary",
    "to_frontend_evidence_item",
    "to_frontend_reader_anchor",
]
