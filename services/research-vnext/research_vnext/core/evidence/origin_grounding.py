"""RV07 · `origin` 的服务端真值裁决（跨轮接口对齐，2026-09-14）。

问题（已实测）
--------------
RV08 的 `surface/MessageComponent.vue` 会为卡片里的**每个元素**渲染 `origin` 徽标
（`research.component.origin.source` = "来源"、`user` = "用户"、`inference` = "模型推断"）。
但该 `origin` 目前是**模型自述**：

- `core/domain/proposal_handlers.py::_norm_originated` 只做"缺失/非法 ⇒ 降级 INFERENCE"，
  **不校验** `origin="SOURCE"` 背后是否真有证据；
- 词表 `ORIGINS = ("USER", "SOURCE", "INFERENCE")` 由 `llm/prompts/registry.py` 第 10 条
  提示模型自行标注。

后果：模型只要在 payload 里写 `origin="SOURCE"`，前端就会给该字段打上"**来源**"徽标 ——
等于让模型**自证**其内容有文献/资产背书。这正是 RV07 立轮时要拦的方向
（AI 只能 propose，落地与背书由服务端裁决）。

本模块提供**纯函数**形式的服务端裁决：把 `origin` 从"模型声明"改为"服务端推导"。

铁律
----
1. **只降级，不升级**：本模块永远不会把 `INFERENCE` 提升为 `SOURCE`/`USER`；
2. **依据必须可核**：`SOURCE` 必须带非空 `evidence_refs`，且**每一个** ref 都指向
   `ACTIVE` 且非 stale 的 RV07 卡（`is_grounded_card`）；
3. **纯函数**：不碰 DB / 网络；调用方负责把卡片公开投影喂进来。

接线（**已接线**，2026-09-14 人工授权）
-------------------------------------
`core/domain/proposal_handlers.py::prepare()` 在拿到 `content` 之后、
`_origin_summary(content)` 之前调用 `_ground_origins()`：

    grounded = grounded_card_ids_for_session(db, session_id=session_id)
    content, _report = ground_content_origins(
        content, grounded_ids=grounded, proposal_refs=proposal.evidence_refs
    )

domain → evidence 走**延迟 import**：`evidence/service.py` 顶层依赖
`domain/errors.py`，顶层反向 import 会成环（仓库既有惯例同此）。

**`USER` 的判定口径（2026-09-14 产品裁定 + 实现，D-USER-1）**
------------------------------------------------------------
裁定见 `RV07/DECISIONS-2026-09-14.md` §1。两级口径：

1. **服务端构造**：由服务端从用户输入直接构造的字段（当前**仅**
   `problemStatement`）免校验，恒为 `USER`。它们不参与本模块裁决，
   由 `ground_content_origins(server_authored_keys=...)` 整段跳过。
2. **模型声明 + 原文锚定**：模型对其他元素声明 `USER` 时**必须**附 `quote`
   （用户消息中的**逐字**片段）。服务端校验 `quote.strip()` 非空且是用户原文
   子串 → 保留 `USER`；否则降级 `INFERENCE`（缺失 → `USER_QUOTE_MISSING`，
   不在原文 → `USER_NOT_IN_MESSAGE`）。

**为什么锚定物是 `quote` 而不是 `text` 或 `source_span`**：

- 不能对 `text` 做子串校验 —— `text` 常是模型对用户原话的**改写**
  （例如服务端构造的 `problemStatement`），逐字比对必然误降级；
- 不能用字符偏移（`source_span`）—— LLM 数不准偏移，但能可靠地"原样抄一段"。
  所以锚定物必须是**可逐字抄写的独立片段**。

`server_authored_keys` 用于**整段跳过**服务端构造的字段：这些字段的 `origin`
由服务端赋值，不由模型决定，故不参与裁决。刻意**不**给元素打标记 ——
打标记会污染 `content` 与 `content_hash`（同一份内容因内部标记不同而哈希不同）。
"""

from __future__ import annotations

from typing import Any, Iterable

#: 与 `proposal_handlers.ORIGINS` 保持同词表（此处不 import，避免跨层依赖）。
ORIGINS: tuple[str, ...] = ("USER", "SOURCE", "INFERENCE")

#: 非法/缺失时的兜底方向：**永远**倾向"这是推断"，而不是"这是用户说的"。
FALLBACK_ORIGIN = "INFERENCE"

#: 降级原因码（可对外登记，便于解释"为什么徽标变了"）。
REASON_INVALID = "ORIGIN_INVALID"
REASON_SOURCE_WITHOUT_EVIDENCE = "SOURCE_WITHOUT_GROUNDING"
REASON_SOURCE_UNVERIFIED_REF = "SOURCE_REF_NOT_GROUNDED"
REASON_USER_NOT_IN_MESSAGE = "USER_TEXT_NOT_IN_MESSAGE"
#: 模型声明 `USER` 却**未附** `quote`（或 `quote` 为纯空白）：无从核对 ⇒ fail-closed 降级。
REASON_USER_QUOTE_MISSING = "USER_QUOTE_MISSING"

_REF_KEYS = ("evidence_refs", "evidenceRefs", "evidence_ref")


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def is_grounded_card(public: Any) -> bool:
    """一张卡能否作为 `origin=SOURCE` 的**服务端依据**。

    判据（与 `frontend_projection.to_frontend_evidence_item` 的 `authorized` 同源）：
    `status == "ACTIVE"` 且 `stale_state != "STALE"`。
    """
    if not isinstance(public, dict):
        return False
    status = _as_str(public.get("status")).upper()
    stale_state = _as_str(public.get("stale_state")).upper() or "FRESH"
    return status == "ACTIVE" and stale_state != "STALE"


def grounded_card_ids(cards: Iterable[Any]) -> set[str]:
    """从一批卡片公开投影里筛出**可作为依据**的卡片 id。"""
    out: set[str] = set()
    for public in cards:
        if not is_grounded_card(public):
            continue
        card_id = _as_str((public or {}).get("id")).strip()
        if card_id:
            out.add(card_id)
    return out


def _element_refs(element: dict[str, Any]) -> list[str]:
    for key in _REF_KEYS:
        raw = element.get(key)
        if isinstance(raw, list):
            return [s for s in (_as_str(v).strip() for v in raw) if s]
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
    return []


def _clean_refs(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Iterable):
        return []
    return [s for s in (_as_str(v).strip() for v in raw) if s]


def ground_element_origin(
    element: dict[str, Any],
    *,
    grounded_ids: set[str],
    user_text: str | None = None,
    proposal_refs: Iterable[Any] | None = None,
) -> tuple[str, str]:
    """裁决单个元素的 `origin`，返回 `(origin, reason)`；`reason` 为空表示未降级。

    `user_text` 为 `None` 时**不**校验 `USER`（调用方拿不到用户原文，无法裁决，
    保持既有行为）；传入时，声明 `USER` 的元素**必须**附 `quote` ——
    用户消息中的**逐字**片段（不是改写后的 `text`）：
    非空且是 `user_text` 子串才保留 `USER`，否则降级，防止"模型替用户说话"。

    `proposal_refs` 是**提案级** `evidence_refs`。元素级 ref 缺失时回落到它：
    `proposal_handlers._norm_originated` 只保留 `{text, origin, quote}`，
    证据引用天然落在提案上（`Proposal.evidence_refs`），
    不回落的话任何 `SOURCE` 都会被判成"无依据"而全量降级。
    """
    claimed = _as_str(element.get("origin")).upper().strip()

    if claimed not in ORIGINS:
        return FALLBACK_ORIGIN, REASON_INVALID

    if claimed == "INFERENCE":
        return claimed, ""

    if claimed == "SOURCE":
        refs = _element_refs(element)
        if not refs:
            refs = _clean_refs(proposal_refs)
        if not refs:
            return FALLBACK_ORIGIN, REASON_SOURCE_WITHOUT_EVIDENCE
        if any(ref not in grounded_ids for ref in refs):
            return FALLBACK_ORIGIN, REASON_SOURCE_UNVERIFIED_REF
        return claimed, ""

    # claimed == "USER"
    if user_text is None:
        # 拿不到用户原文 ⇒ 无法裁决，保持既有行为（不校验）。
        return claimed, ""
    quote = _as_str(element.get("quote")).strip()
    if not quote:
        # 模型声明 USER 却不给出可逐字核对的片段 ⇒ fail-closed 降级。
        return FALLBACK_ORIGIN, REASON_USER_QUOTE_MISSING
    if quote in user_text:
        return claimed, ""
    return FALLBACK_ORIGIN, REASON_USER_NOT_IN_MESSAGE


def _ground_deep(
    value: Any,
    *,
    grounded_ids: set[str],
    user_text: str | None,
    proposal_refs: Iterable[Any] | None,
    skip_keys: set[str],
    report: dict[str, Any],
    key: str = "",
) -> Any:
    """递归裁决**嵌套**元素里的 `origin`（RV09 起启用）。

    为什么必须递归：`research_map` 的视角把交付物/不确定性/停止条件放在
    `perspectives[]` 的**下一层**。只裁决顶层列表会让嵌套元素保留模型自述的
    `SOURCE` 徽标 —— 那正是 `ground_element_origin` 要拦的事，只是藏深了一层。
    """
    if isinstance(value, list):
        return [
            _ground_deep(
                v,
                grounded_ids=grounded_ids,
                user_text=user_text,
                proposal_refs=proposal_refs,
                skip_keys=skip_keys,
                report=report,
                key=key,
            )
            for v in value
        ]
    if not isinstance(value, dict):
        return value

    if "origin" in value and key not in skip_keys:
        origin, reason = ground_element_origin(
            value, grounded_ids=grounded_ids, user_text=user_text, proposal_refs=proposal_refs
        )
        if reason:
            report["downgraded"] = report.get("downgraded", 0) + 1
            reasons = report.setdefault("reasons", {})
            reasons[reason] = reasons.get(reason, 0) + 1
            return {**value, "origin": origin}

    out: dict[str, Any] = {}
    for k, v in value.items():
        if k in skip_keys:
            out[k] = v
            continue
        out[k] = _ground_deep(
            v,
            grounded_ids=grounded_ids,
            user_text=user_text,
            proposal_refs=proposal_refs,
            skip_keys=skip_keys,
            report=report,
            key=k,
        )
    return out


def ground_content_origins(
    content: dict[str, Any],
    *,
    grounded_ids: set[str],
    user_text: str | None = None,
    proposal_refs: Iterable[Any] | None = None,
    server_authored_keys: Iterable[str] = frozenset(),
    deep: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """改写 `content` 中**所有**带 `origin` 的列表元素，返回 `(新 content, 报告)`。

    **不改原对象**（返回浅拷贝的新 dict / 新 list），便于调用方对比与回滚。
    报告形如 `{"downgraded": 2, "reasons": {"SOURCE_WITHOUT_GROUNDING": 2}}`。

    `server_authored_keys` 命中的字段**整段跳过**裁决：其 `origin` 由服务端赋值
    （如 `problemStatement`），不由模型决定，因此不参与本函数裁决。
    刻意**不**给元素打"服务端构造"标记 —— 打标记会污染 `content` 与 `content_hash`。
    """
    out: dict[str, Any] = dict(content)
    downgraded = 0
    reasons: dict[str, int] = {}
    skip_keys = set(server_authored_keys)

    if deep:
        report: dict[str, Any] = {}
        walked = {
            k: (v if k in skip_keys else _ground_deep(
                v,
                grounded_ids=grounded_ids,
                user_text=user_text,
                proposal_refs=proposal_refs,
                skip_keys=skip_keys,
                report=report,
                key=k,
            ))
            for k, v in content.items()
        }
        stats = {"downgraded": report.get("downgraded", 0), "reasons": report.get("reasons", {})}
        return walked, stats

    for key, value in content.items():
        if key in skip_keys:
            # 服务端构造的字段：其 origin 由服务端赋值，不由模型决定，因此不参与裁决。
            out[key] = value
            continue
        if not isinstance(value, list):
            continue
        new_items: list[Any] = []
        touched = False
        for item in value:
            if not isinstance(item, dict) or "origin" not in item:
                new_items.append(item)
                continue
            origin, reason = ground_element_origin(
                item,
                grounded_ids=grounded_ids,
                user_text=user_text,
                proposal_refs=proposal_refs,
            )
            if reason:
                downgraded += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                new_items.append({**item, "origin": origin})
                touched = True
            else:
                new_items.append(item)
        out[key] = new_items if touched else value

    return out, {"downgraded": downgraded, "reasons": reasons}


__all__ = [
    "FALLBACK_ORIGIN",
    "ORIGINS",
    "REASON_INVALID",
    "REASON_SOURCE_UNVERIFIED_REF",
    "REASON_SOURCE_WITHOUT_EVIDENCE",
    "REASON_USER_NOT_IN_MESSAGE",
    "REASON_USER_QUOTE_MISSING",
    "ground_content_origins",
    "ground_element_origin",
    "grounded_card_ids",
    "is_grounded_card",
]
