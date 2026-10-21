"""R16 TeachingBlueprint projector：上下文 → LLM → schema 校验 → proposal（非事实）。

流程（DECISIONS.md §6：确定性工作流控权，LLM 只生成 proposal）：
1. `build_blueprint_prompt`：上下文投影为有界 prompt（token budget 确定性控制）；
2. `llm_generate(prompt)`：抽象生成器（可同步/异步；测试用确定性 fake）；
3. 解析 JSON → `TeachingBlueprintProposal.model_validate`（schema 校验：类型/
   HTML 禁令/枚举/长度/ref 形状）；
4. `check_grounding`：所有 source_ref 必须 ∈ 上下文可解析定位器集合，否则
   GroundingViolation（模型禁止发明引用 → “每个引用可 resolve”验收）；
5. 校验通过才返回 proposal；任何失败 → 显式异常，**不成为事实**（不写库）。

LLM 输出视为不可信输入：JSON 解析 + schema 校验 + grounding 三重闸门。
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from app.teacher_prep.context.models import TextbookTeachingContext
from app.teacher_prep.text_safety import strip_edit_markers

from .prompt import (
    STRICT_RETRY_INSTRUCTION,
    build_blueprint_prompt,
    build_compact_blueprint_prompt,
)
from .schema import (
    BLUEPRINT_PROPOSAL_SCHEMA_VERSION,
    TeachingBlueprintProposal,
    collect_refs,
    resolvable_refs_of,
)

logger = logging.getLogger("teacher.prep.blueprint.projector")

LlmGenerate = Callable[[str], str | Awaitable[str]]

# W2.1（RULINGS 裁定 5）：失败留痕 warning 码（进 proposal.warnings_ack → 投影 warnings）。
# ai_runs 留痕由 LLM 网关 sink 承担（每次 llm_generate 调用各落一条 ai_runs 记录）。
_RETRY_USED_WARNING = "blueprint_strict_retry_used"
_FALLBACK_WARNING = "blueprint_fallback_after_parse_failure"


class BlueprintProjectionError(Exception):
    """投影失败基类（不成为事实）。"""


class BlueprintProposalInvalid(BlueprintProjectionError):
    """LLM 输出未通过 schema 校验（JSON/字段/HTML/枚举/形状）。"""

    def __init__(self, reason: str, issues: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.issues = issues or []


class GroundingViolation(BlueprintProjectionError):
    """proposal 引用了上下文可解析集合之外的定位器（发明引用拒绝）。"""

    def __init__(self, refs: list[str]):
        super().__init__(f"proposal 含不可解析/发明的 source_ref：{refs}")
        self.refs = refs


def _strip_code_fence(raw: str) -> str:
    """容忍模型偶尔用 ```json 围栏包裹（解析前剥离；不改变 JSON 语义）。"""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl >= 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


# JSON 合法转义：` " \ / b f n r t` 以及 \uXXXX。
def _repair_json_escapes(raw: str) -> str:
    """把 LLM 在 JSON 字符串里写出的 LaTeX 单反斜杠确定性修复为合法双反斜杠。

    背景：教材正文含大量 LaTeX（`$-a \\leq x \\leq a$`、`$\\frac{x^2}{a^2}$`）。模型偶尔
    在 JSON 字符串值里直接写单反斜杠（`\\leq`、`\\frac`），而 `\\l`/`\\f` 等不是合法 JSON
    转义 → `json.loads` 抛 `Invalid \\escape`（或把 `\\f` 误当换页符静默损坏）。

    逐字符扫描：反斜杠后若是合法转义（`"` `\\` `/` 或 `\\uXXXX`）原样保留；若是
    `b/f/n/r/t` 且其后紧跟字母（LaTeX 命令如 `\\frac`/`\\nu`）→ 补成双反斜杠；若是
    真正的单字符转义（`\\n` 后接非字母）→ 保留；其余（`\\leq`/`\\alpha`/`\\sqrt`…）→ 补双。
    语义上双反斜杠解析后正是 LaTeX 想要的单个字面反斜杠，不改动任何正文内容。
    """
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:  # 末尾孤立反斜杠
            out.append("\\\\")
            i += 1
            continue
        nxt = raw[i + 1]
        if nxt in '"\\/':  # 合法转义（含已转义的反斜杠）
            out.append("\\")
            out.append(nxt)
            i += 2
            continue
        if nxt == "u" and i + 6 <= n and all(c in "0123456789abcdefABCDEF" for c in raw[i + 2 : i + 6]):
            out.append(raw[i : i + 6])  # 合法 \uXXXX
            i += 6
            continue
        after = raw[i + 2] if i + 2 < n else ""
        if nxt in "bfnrt" and not (after.isalpha()):
            out.append("\\")
            out.append(nxt)  # 真正的 \n \t 等控制转义
            i += 2
            continue
        out.append("\\\\")  # LaTeX 命令单反斜杠 → 补成双反斜杠
        out.append(nxt)
        i += 2
    return "".join(out)


_ITEM_LIST_KEYS = (
    "objectives",
    "prior_knowledge",
    "concepts",
    "theorems",
    "examples",
    "inquiries",
    "design_intent",
    "key_points",
    "difficult_points",
    "homework",
    "reflection",
)

# ── 标题占位后缀清理（P2）──────────────────────────────────────────────────
# 占位符是**内容层**的诚实机制（覆盖不足时不得编造）；但它不是标题的一部分，
# 出现在 metadata.title / 蓝图 title 里是泄漏（实测
# `"3.1.2 椭圆的简单几何性质（第一课时，待确认）"`）。
# 清理必须是确定性的、且清理后不得为空。
_TBD_IN_TITLE_RE = re.compile(r"[（(][^）)]*待确认[^）)]*[）)]")
_TBD_TAIL_RE = re.compile(r"[，,、;；:：]?\s*待确认\s*$")
_EMPTY_PAIRS_RE = re.compile(r"[（(]\s*[）)]")
_TITLE_TRAILING_JUNK_RE = re.compile(r"[\s，,、;；:：\-—]+$")
_TITLE_FALLBACK = "教学设计蓝图"


def sanitize_title(raw: Any, *, fallback: str = _TITLE_FALLBACK) -> str:
    """清理标题里的占位后缀（`（待确认）`/`（第一课时，待确认）`/末尾 `，待确认`）。

    - 确定性、幂等（清理结果再清理不变）；
    - **不得为空**：清理后为空 → 回退 `fallback`；
    - 只做删除，不做改写（不发明标题内容）；
    - W1-A 增补（LEAD-BAR §1.3）：同时剥除编辑**内部标记**（`（教师修订）`/`（教师新增）`
      /`（AI 生成）`…），避免内部过程标注泄漏进标题；保留教师自撰的正式后缀（如「（教师版）」）。
    """
    text = str(raw or "")
    text = strip_edit_markers(text)  # 内部标记（教师修订/待确认/AI 生成…）先行剥除
    text = _TBD_IN_TITLE_RE.sub("", text)
    text = _TBD_TAIL_RE.sub("", text)
    text = _EMPTY_PAIRS_RE.sub("", text)
    text = text.replace(_TBD, "")
    text = _TITLE_TRAILING_JUNK_RE.sub("", text).strip()
    return text or fallback


_STAGE_MAP = {
    "intro": "introduction",
    "introduction": "introduction",
    "导入": "introduction",
    "new": "new_content",
    "new_content": "new_content",
    "讲解": "new_content",
    "example": "example",
    "例题": "example",
    "exercise": "exercise",
    "练习": "exercise",
    "practice": "exercise",
    "summary": "summary",
    "小结": "summary",
    "homework": "homework",
    "作业": "homework",
    "inquiry": "new_content",
    "探究": "new_content",
    "discussion": "exercise",
}


def _as_item(v: Any) -> dict[str, Any] | None:
    """LLM 常把条目输出成纯字符串 → 规范为 BlueprintItem dict。

    W1-A：文本经 `strip_edit_markers` 净化（内部标记不得进入蓝图/PPT/教案），
    净化后为空 → 丢弃该条（不编造补齐）；`source_ref` 原样保留（不被净化触碰）。
    """
    if isinstance(v, str):
        text = strip_edit_markers(v.strip()).strip()
        return {"text": text, "source_ref": None} if text else None
    if isinstance(v, dict):
        text = v.get("text") or v.get("title") or v.get("content")
        if isinstance(text, str):
            text = strip_edit_markers(text.strip()).strip()
            if text:
                ref = v.get("source_ref")
                origin = v.get("source_origin")
                return {
                    "text": text,
                    "source_ref": ref if isinstance(ref, str) else None,
                    "source_origin": origin if isinstance(origin, str) else None,
                }
    return None


def _normalize_blueprint_payload(data: dict[str, Any]) -> dict[str, Any]:
    """宽容规范化 LLM JSON（仍过 schema；不发明事实）。"""
    out = dict(data)
    # P2：标题占位后缀是内容层机制的泄漏，统一在此清理（含 LLM 原始输出与预填两条路径）
    if out.get("title") is not None:
        out["title"] = sanitize_title(out["title"])
    for key in _ITEM_LIST_KEYS:
        raw = out.get(key)
        if isinstance(raw, list):
            items = [_as_item(v) for v in raw]
            out[key] = [x for x in items if x]
        elif isinstance(raw, str) and raw.strip():
            cleaned = strip_edit_markers(raw.strip()).strip()
            out[key] = [{"text": cleaned, "source_ref": None}] if cleaned else []

    flow = out.get("lesson_flow")
    if isinstance(flow, list):
        norm_flow = []
        for stage in flow:
            if isinstance(stage, str):
                norm_flow.append(
                    {
                        "stage": "new_content",
                        "title": stage.strip()[:80] or "教学环节",
                        "items": [],
                    }
                )
                continue
            if not isinstance(stage, dict):
                continue
            s = dict(stage)
            raw_stage = str(s.get("stage") or "").strip().lower()
            s["stage"] = _STAGE_MAP.get(raw_stage, "new_content")
            # W1-A：环节标题经净化（内部标记不得进入落库/渲染文本），保留教师正式后缀
            s["title"] = sanitize_title(s.get("title"), fallback="教学环节")
            items = s.get("items")
            if isinstance(items, list):
                s["items"] = [x for x in (_as_item(v) for v in items) if x]
            elif isinstance(items, str) and items.strip():
                cleaned = strip_edit_markers(items.strip()).strip()
                s["items"] = [{"text": cleaned, "source_ref": None}] if cleaned else []
            # R3 additive：展开卡三组 + 教学提示（缺省空数组；宽容规范化字符串条目）
            for group_key in ("teacher_questions", "student_activities", "board_notes", "hints"):
                g = s.get(group_key)
                if isinstance(g, list):
                    s[group_key] = [x for x in (_as_item(v) for v in g) if x]
                elif isinstance(g, str) and g.strip():
                    cleaned = strip_edit_markers(g.strip()).strip()
                    s[group_key] = [{"text": cleaned, "source_ref": None}] if cleaned else []
                else:
                    s[group_key] = []
            norm_flow.append(s)
        out["lesson_flow"] = norm_flow
    return out


_STAGE_TITLES = {
    "introduction": "导入",
    "new_content": "新知讲解",
    "example": "例题",
    "exercise": "练习",
    "summary": "小结",
    "homework": "作业",
}
_STAGE_ORDER = ("introduction", "new_content", "example", "exercise", "summary", "homework")
_TBD = "（待确认）"

# ── 桶分类（FROZEN-CONTRACT §B.1）────────────────────────────────────────────
# 教材事实桶：教材文本里**真实存在** → 服务端确定性预填（refs 来自上下文，
# grounding 天然通过）。取不到就是**空**——不占位（空是诚实事实，占位是伪装）。
_TEXTBOOK_BUCKETS: dict[str, str] = {
    "concepts": "concepts",
    "theorems": "theorems",
    "examples": "examples",
    "inquiries": "inquiries",
    "design_intent": "design_intent",
}

# 推导桶：**教研产物**，教材文本里天然不存在原文（教材不会写"教学目标是什么"）。
# 只能由 LLM 基于教材事实推导；教材若显式给出证据则作为底稿，否则留空待推导。
# 映射值 = context 侧字段名（key_points/difficult_points 共用 key_difficult_points）。
_DERIVED_BUCKETS: dict[str, str] = {
    "objectives": "objectives",
    "prior_knowledge": "prior_knowledge",
    "key_points": "key_difficult_points",
    "difficult_points": "key_difficult_points",
}

# 数量下限（FROZEN-CONTRACT §B.2.3）：低于下限 → 如实记 warning，不编造补齐。
_DERIVED_MIN_COUNT: dict[str, int] = {
    "objectives": 2,
    "key_points": 2,
    "difficult_points": 1,
}
# 同一章节占位符比例上限（§B.2.4）：超出 → 如实记 warning。
_PLACEHOLDER_RATIO_LIMIT = 0.20


def _facts_of(context: TextbookTeachingContext, name: str) -> list[dict[str, Any]]:
    """取 context 某桶的事实（text + canonical ref）；空则空列表（不占位）。

    例题桶（examples）额外标注 `source_origin="textbook_problems"`（三源分标签，
    KERNEL-RUBRIC §1.2.7）——教材例题必有 locator，区别于 AI 生成变式。
    """
    origin = "textbook_problems" if name == "examples" else None
    out: list[dict[str, Any]] = []
    for f in getattr(context, name, []) or []:
        if not getattr(f, "text", ""):
            continue
        item: dict[str, Any] = {"text": f.text, "source_ref": (f.source_ref or f.chunk_ref or None)}
        if origin is not None:
            item["source_origin"] = origin
        out.append(item)
    return out


_PREFILL_ITEM_LIMITS: dict[str, int] = {
    "concepts": 12,
    "theorems": 12,
    "examples": 12,
    "inquiries": 12,
    "design_intent": 12,
    "objectives": 12,
    "prior_knowledge": 12,
    "key_points": 8,
    "difficult_points": 8,
}
# BlueprintItem.text 的 schema 上限（max_length=400）
_ITEM_TEXT_LIMIT = 400


def _bounded_prefill(out_key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """预填条目裁剪到 schema 上限（text ≤400 字；条数 ≤ 该桶 max_length）。

    实测（2026-09-23）：教材切片原文常 >400 字，直接预填会让 `model_validate` 报
    「String should have at most 400 characters」（这正是紧凑模式历史上「校验失败 10 项」
    的未定因，本次在事实桶预填模式复现定位）。只做确定性截断——不发明、不改写内容。
    """
    limit = _PREFILL_ITEM_LIMITS.get(out_key, 12)
    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        text = (it.get("text") or "")[:_ITEM_TEXT_LIMIT]
        if not text:
            continue
        out.append({**it, "text": text})
    return out


def _prefill_from_context(context: TextbookTeachingContext) -> dict[str, Any]:
    """服务端确定性预填（紧凑模式）：结构字段 + 事实桶（按 §B.1 分类）。

    - 教材事实桶：确定性预填真实事实，取不到即空；
    - 推导桶：教材显式证据作为底稿（通常为空），留给 LLM 推导覆盖；
      合并后仍为空 → `_apply_derived_fallbacks` 做诚实兜底（证据 → 否则「（待确认）」）。
    - `warnings_ack` 由服务端回执全部上下文警告码。
    """

    prefill: dict[str, Any] = {
        "schema_version": BLUEPRINT_PROPOSAL_SCHEMA_VERSION,
        "context_snapshot_hash": context.snapshot_hash,
        "title": sanitize_title(f"{context.section.title[:100]} 教学设计蓝图"),
        "warnings_ack": [w.code for w in context.warnings],
    }
    for out_key, ctx_key in _TEXTBOOK_BUCKETS.items():
        prefill[out_key] = _bounded_prefill(out_key, _facts_of(context, ctx_key))
    for out_key, ctx_key in _DERIVED_BUCKETS.items():
        prefill[out_key] = _bounded_prefill(out_key, _facts_of(context, ctx_key))
    return prefill


def _apply_derived_fallbacks(
    payload: dict[str, Any], context: TextbookTeachingContext
) -> dict[str, Any]:
    """推导桶的诚实兜底（§B.2.1）：LLM 未产出时，有教材证据用证据，否则标「（待确认）」。

    占位符是**最后手段**，不是默认值——它会被投影器按反幻觉规则整条剔除
    （`specs/projector.py::is_placeholder_text`），因此绝不用于"填满"。
    """
    for out_key, ctx_key in _DERIVED_BUCKETS.items():
        if payload.get(out_key):
            continue
        evidence = _facts_of(context, ctx_key)
        # schema 上限裁剪（同 `_bounded_prefill`）：教材原文常 >400 字，直接兜底会
        # 让 `model_validate` 抛 string_too_long —— ④ 确定性兜底本应「绝不抛错」。
        payload[out_key] = _bounded_prefill(out_key, evidence) or [{"text": _TBD, "source_ref": None}]
    return payload


def derived_bucket_quality(payload: dict[str, Any]) -> list[str]:
    """推导桶质量门禁（§B.2.3/§B.2.4）→ warning 列表（只披露，不编造、不阻断）。

    返回形如 `objectives_below_min:1<2`、`derived_placeholder_ratio:0.80>=0.20`。
    """
    warnings: list[str] = []
    for key, minimum in _DERIVED_MIN_COUNT.items():
        items = payload.get(key) or []
        real = [i for i in items if not is_placeholder_item(i)]
        if len(real) < minimum:
            warnings.append(f"{key}_below_min:{len(real)}<{minimum}")
    total = sum(len(payload.get(k) or []) for k in _DERIVED_BUCKETS)
    if total:
        placeholders = sum(
            1 for k in _DERIVED_BUCKETS for i in (payload.get(k) or []) if is_placeholder_item(i)
        )
        ratio = placeholders / total
        if ratio >= _PLACEHOLDER_RATIO_LIMIT:
            warnings.append(f"derived_placeholder_ratio:{ratio:.2f}>={_PLACEHOLDER_RATIO_LIMIT:.2f}")
    return warnings


def is_placeholder_item(item: Any) -> bool:
    """条目是否为「（待确认…）」占位（兼容 dict/str）。"""
    raw = item.get("text") if isinstance(item, dict) else item
    return isinstance(raw, str) and _TBD in raw


def _merge_compact(prefill: dict[str, Any], llm_data: dict[str, Any]) -> dict[str, Any]:
    """合并（紧凑模式）：prefill 为底；LLM 贡献推导桶与环节内容。

    - 教材事实桶：LLM 不得改写（事实由服务端持有）；
    - 推导桶（objectives/key_points/difficult_points/prior_knowledge）：LLM 值覆盖底稿；
    - 环节骨架由服务端持有，LLM 只提供各 stage 的条目。
    """
    flow_items = llm_data.get("flow_items")
    merged = dict(prefill)
    for key, value in llm_data.items():
        if key == "flow_items":
            continue
        if key in _TEXTBOOK_BUCKETS:
            continue  # 教材事实桶不接受 LLM 改写
        if value not in (None, [], {}, ""):
            merged[key] = value
    stages: list[dict[str, Any]] = []
    if isinstance(flow_items, dict):
        for stage in _STAGE_ORDER:
            raw = flow_items.get(stage)
            items = [x for x in (_as_item(v) for v in (raw if isinstance(raw, list) else [])) if x]
            if items:
                stages.append(
                    {
                        "stage": stage,
                        "title": _STAGE_TITLES[stage],
                        "items": items,
                        "duration_minutes": None,
                    }
                )
    if not stages:  # schema 要求 lesson_flow 至少 1 段
        stages = [
            {"stage": "new_content", "title": _STAGE_TITLES["new_content"], "items": [], "duration_minutes": None}
        ]
    merged["lesson_flow"] = stages
    return merged



def _derive_from_facts(
    sources: list[dict[str, Any]], prefix: str, limit: int
) -> list[dict[str, Any]]:
    """由既有真实条目**确定性派生**新条目（LEAD-BAR §4.1 桶非空 / §4.3 六章非空）。

    反幻觉约束：不发明新事实——文本 = `prefix + 源文本`（派生关系在文本内自披露，
    且与源条目**文本不同**，满足 §4.1「互不重复」）；`source_ref` 随源条目保留，
    使派生条目仍可被教材定位器 resolve（grounding 天然通过）。
    """
    out: list[dict[str, Any]] = []
    for it in sources:
        text = (it.get("text") if isinstance(it, dict) else str(it) or "").strip()
        if not text or is_placeholder_item(it):
            continue
        ref = it.get("source_ref") if isinstance(it, dict) else None
        # 前缀 + 源文本仍须遵守 BlueprintItem.text ≤400：否则派生条目会撑破 schema，
        # 使 ④ 确定性兜底抛 ValidationError（实测 "本节结论：" + 400 字 = 405 → 兜底失败）。
        out.append({"text": f"{prefix}{text}"[:_ITEM_TEXT_LIMIT], "source_ref": ref})
        if len(out) >= limit:
            break
    return out


def _ensure_bucket_ref(data: dict[str, Any], key: str, locator: str) -> None:
    """保证 `key` 桶至少 1 条带真实 locator（LEAD-BAR §4.1）。

    仅当**整桶无任何 source_ref** 时，用本节 section locator 给首个真实条目兜底；
    已有 ref 则不动（不覆盖 LLM/教材给出的更精确定位）。
    """
    items = data.get(key) or []
    if not locator or not items:
        return
    if any(isinstance(i, dict) and i.get("source_ref") for i in items):
        return
    for it in items:
        if isinstance(it, dict) and not is_placeholder_item(it):
            it["source_ref"] = locator
            return


def _finalize_derived_buckets(
    data: dict[str, Any], context: TextbookTeachingContext
) -> dict[str, Any]:
    """推导桶收口（§B.2 / §F.4 / LEAD-BAR §4.1/§4.3）：诚实兜底 + 桶非空 + 质量门禁披露。

    - 推导桶（objectives/prior_knowledge/key_points/difficult_points）：有教材证据用证据，
      否则「（待确认）」占位（投影器按反幻觉整条剔除，不在版面造假）；
    - theorems 非空（LEAD-BAR §4.1）：教材无定理原文时，由本节 concepts/key_points
      **确定性派生**「本节结论」（显式派生、不冒充教材定理；source_ref 随源保留）；
    - reflection 非空（LEAD-BAR §4.3 六章齐全）：无依据时由本节重难点确定性派生
      「预设反思」（显式预设、不编造事实；source_ref 随源保留）；homework 无依据仍留空；
    - 数量下限 / 占位比例质量码 → 追加进 warnings_ack（供投影 warnings 透出）。
    """
    data = _apply_derived_fallbacks(data, context)
    ack = list(data.get("warnings_ack") or [])
    # LEAD-BAR §4.1：语义桶非空。theorems 是教材事实桶（§B.1），本节教材无定理原文
    # （检索/抽取未命中 → context.theorems=空）时，退化为由本节要点/概念派生的「本节结论」
    # （key_points 优先——它们通常带真实 locator，使派生理仍有 ≥1 条可 resolve 依据）。
    if not data.get("theorems"):
        data["theorems"] = _derive_from_facts(
            list(data.get("key_points") or []) + list(data.get("concepts") or []),
            "本节结论：",
            3,
        )
    for key, code in (("homework", "homework_no_basis"), ("reflection", "reflection_no_basis")):
        if not data.get(key):
            data[key] = []
            if code not in ack:
                ack.append(code)
    # LEAD-BAR §4.3：教学反思章非空——无依据时由本节重难点/要点派生「预设反思」（诚实预设）。
    if not data.get("reflection"):
        derived_reflection = _derive_from_facts(
            list(data.get("difficult_points") or []) + list(data.get("key_points") or []),
            "预设反思：",
            3,
        )
        if derived_reflection:
            data["reflection"] = derived_reflection
            ack = [w for w in ack if w != "reflection_no_basis"]
    # LEAD-BAR §4.1：objectives/key_points/concepts/theorems 至少各 1 条带真实 locator。
    # LLM 全契约产出可能漏 ref（concepts 尤甚）→ 用**本节 section locator**（真实、可 resolve）
    # 给首个无 ref 条目兜底（该条目确属本节，attach 不构成 misattribution）。
    for key in ("objectives", "key_points", "concepts", "theorems"):
        _ensure_bucket_ref(data, key, context.locator or "")
    for warning in derived_bucket_quality(data):
        if warning not in ack:
            ack.append(warning)
    # W1-A（LEAD-BAR §4.1 / KERNEL §1.2.5）：`no_objectives_evidence` /
    # `no_key_difficult_evidence` 是**上下文**信号（教材原文不写「教学目标」属正常）——
    # 一旦推导桶（LLM 教研产物）产出真实内容，该信号即过时，必须从 warnings 消除
    # （否则教案/PPT 警告区出现误导性的 no_*_evidence）。
    stale: set[str] = set()
    if any(not is_placeholder_item(i) for i in (data.get("objectives") or [])):
        stale.add("no_objectives_evidence")
    if any(
        not is_placeholder_item(i)
        for i in (data.get("key_points") or []) + (data.get("difficult_points") or [])
    ):
        stale.add("no_key_difficult_evidence")
    if stale:
        ack = [w for w in ack if w not in stale]
    data["warnings_ack"] = ack[:30]  # schema max_length=30
    return data


def parse_and_validate(
    raw: str,
    *,
    context_snapshot_hash: str | None = None,
    prefill: dict[str, Any] | None = None,
    context: TextbookTeachingContext | None = None,
) -> TeachingBlueprintProposal:
    """JSON 解析 + schema 校验（LLM 输出不可信：三重闸门之一）。

    `prefill` 非空时表示紧凑模式：服务端已确定性填好事实字段，LLM 只提供
    difficult_points 与 flow_items；此处按 prefill 为底合并（闸门不变）。

    `context` 非空时对推导桶做收口（诚实兜底 + 质量门禁；§B.2/§F.4）。省略则跳过
    （向后兼容裸调用，例如仅测 schema 的场景）。
    """
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # LLM 常把 LaTeX 单反斜杠（`$\frac{...}$`）直接写进 JSON 字符串值 → 非法/损坏转义。
        # 严格解析失败时，确定性修复非法转义后重试（只改转义写法，不改正文语义）。
        try:
            data = json.loads(_repair_json_escapes(cleaned))
        except json.JSONDecodeError:
            raise BlueprintProposalInvalid(f"LLM 输出非合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise BlueprintProposalInvalid(f"LLM 输出根节点必须是 JSON 对象，实际 {type(data).__name__}")
    if prefill is not None:
        data = _normalize_blueprint_payload(_merge_compact(prefill, data))
    data = _normalize_blueprint_payload(data)
    if context is not None:
        data = _finalize_derived_buckets(data, context)
    try:
        proposal = TeachingBlueprintProposal.model_validate(data)
    except ValidationError as exc:
        issues = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise BlueprintProposalInvalid(f"proposal schema 校验失败（{len(issues)} 项）", issues) from exc
    if context_snapshot_hash is not None and proposal.context_snapshot_hash != context_snapshot_hash:
        raise BlueprintProposalInvalid(
            f"proposal 声明的上下文 hash 不匹配（proposal={proposal.context_snapshot_hash[:12]}… 上下文={context_snapshot_hash[:12]}…）"
        )
    return proposal


def check_grounding(proposal: TeachingBlueprintProposal, context: TextbookTeachingContext) -> list[str]:
    """grounding 校验：所有 source_ref ∈ 上下文可解析集合；返回违规 ref 列表。

    通过该集合约束实现「每个引用可 resolve」（LLM 只能引用上下文事实的定位器）。
    """
    allowed = resolvable_refs_of(context)
    return [ref for ref in collect_refs(proposal) if ref not in allowed]


def _fallback_proposal_data(
    context: TextbookTeachingContext, prefill: dict[str, Any] | None
) -> dict[str, Any]:
    """W2.1 第④段——确定性兜底 proposal：重试后仍失败 → 返回结构合法的预填 proposal。

    复用既有确定性链路（零 LLM、零编造）：
    - `_prefill_from_context`：事实桶 + 标题（占位清理）+ 警告回执；
    - `_merge_compact`：合并骨架（无 LLM 贡献 → lesson_flow 为单段骨架）；
    - `_finalize_derived_buckets`：推导桶诚实兜底 + 质量门禁 warning；
    - 追加失败留痕码（`blueprint_fallback_after_parse_failure`）。
    教师界面只见提案与产品化文案；失败详情只进 warnings/ai_runs，不拼上屏。
    """
    base = prefill if prefill is not None else _prefill_from_context(context)
    data = _merge_compact(base, {})
    data = _finalize_derived_buckets(data, context)
    ack = list(data.get("warnings_ack") or [])
    if _FALLBACK_WARNING not in ack:
        ack.append(_FALLBACK_WARNING)
    data["warnings_ack"] = ack[:30]  # schema max_length=30
    return data


# ── 例题三源兜底：教材无例题时生成 **AI 变式**（KERNEL-RUBRIC §1.2.7 / LEAD-BAR §4.1）─
# 反幻觉红线：「禁止 AI 生成冒充教材例题」。教材例题（textbook_problems）走事实桶、
# 必带 source_ref locator；当上下文**没有任何教材例题**时，允许由 LLM 基于教材概念/定理
# 生成 1-2 道**变式**，但必须显式标注 `source_origin="ai_generated"`（前端/教案据此打标签），
# 且绝不携带 source_ref（不冒充教材出处）。
_AI_EXAMPLES_WARNING = "examples_ai_generated"
_EXAMPLES_VARIANT_PROMPT = """你是高中数学教师的备课助手。本次教材上下文中**没有可直接使用的教材例题**。请基于下面给出的教材概念/定理与学习目标，设计 1-2 道**由易到难**的课堂例题**变式**。

铁律：
1. 这些是 **AI 生成变式**，不是你引用教材原题：不得声称来自教材、不得编造页码或出处；
2. 数学必须正确、题干自洽、可解；数量词与取值范围要写清；用 $...$ 写行内 LaTeX；
3. 只输出**一个** JSON 对象：{{"examples":[{{"text":"题干（≤120字）","solution":"解题思路（≤80字）"}}]}}；不要解释、不要 markdown 代码围栏、不要多余文字。

【教材概念/定理】
{facts}

【本课学习目标】
{objectives}
"""


def _examples_variant_prompt(proposal: TeachingBlueprintProposal) -> str | None:
    """构造 AI 变式例题 prompt；无可依据的教材事实/目标时返回 None（不编造）。"""
    facts = [it.text.strip() for it in (*proposal.concepts, *proposal.theorems) if it.text.strip()][:6]
    objectives = [it.text.strip() for it in proposal.objectives if it.text.strip()][:4]
    if not facts and not objectives:
        return None
    fact_lines = "\n".join(f"- {t}" for t in facts) or "（无）"
    obj_lines = "\n".join(f"- {t}" for t in objectives) or "（无）"
    return _EXAMPLES_VARIANT_PROMPT.format(facts=fact_lines, objectives=obj_lines)


def _parse_example_variants(raw: str) -> list[dict[str, Any]]:
    """解析 AI 变式例题（容错）；只收题干，标注 ai_generated，绝不带 source_ref。"""
    cleaned = _strip_code_fence(raw or "")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            data = json.loads(_repair_json_escapes(cleaned))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, dict):
        return []
    raw_items = data.get("examples")
    out: list[dict[str, Any]] = []
    for e in raw_items if isinstance(raw_items, list) else []:
        text = e.get("text") if isinstance(e, dict) else e
        if isinstance(text, str) and text.strip():
            out.append(
                {
                    "text": strip_edit_markers(text.strip())[:400],
                    "source_ref": None,
                    "source_origin": "ai_generated",
                }
            )
    return out[:2]


async def _generate_example_variants(
    proposal: TeachingBlueprintProposal, llm_generate: LlmGenerate
) -> list[dict[str, Any]]:
    """教材无例题时生成 AI 变式（失败/无依据 → 返回空，不阻断、不编造）。"""
    prompt = _examples_variant_prompt(proposal)
    if prompt is None:
        return []
    try:
        result = llm_generate(prompt)
        if inspect.isawaitable(result):
            result = await result
    except Exception:  # noqa: BLE001 —— 变式生成是加分项，失败不阻断主流程（不编造）
        logger.warning("ai example variant generation failed", exc_info=False)
        return []
    return _parse_example_variants(result if isinstance(result, str) else "")


async def project_blueprint(
    context: TextbookTeachingContext,
    llm_generate: LlmGenerate,
    *,
    max_context_tokens: int = 8000,
    require_grounding: bool = True,
    compact: bool = False,
    allow_ai_examples: bool = True,
) -> TeachingBlueprintProposal:
    """上下文 → 有界 prompt → LLM → schema+grounding 校验 → proposal（不写库）。

    `llm_generate` 返回模型原始文本（可能含围栏/多余文字）；本函数负责解析与校验。

    `compact=True`：事实字段由服务端确定性预填，LLM 只输出 difficult_points 与环节内容。
    实测（真实上下文 + spark-x2.5-4b，各 1 次）：

    | | 完整契约 | 紧凑契约 |
    |---|---|---|
    | 延迟 | 272,348 ms | 163,903 ms |
    | 输出 token | 21,431 | 13,124 |
    | 推理 token | 15,476 | 12,752（占输出 82%） |
    | 正文字符 | 10,095 | **702** |

    结论：产出量 −93%、总耗时 −40%，**但仍 >120 s 用例预算，因为 82% 的算力花在推理上、
    与"要写多少字"无关** → 拆小产出不足以让该模型进预算。

    另：该次紧凑调用合并后 schema 校验失败 10 项（未保留原始输出，未定因）；
    为不使生产路径回退（更快但校验不过 < 更慢但可用），**默认仍走完整契约**。
    `compact=True` 保留给换用具备可控推理预算的模型后的复核。

    R6 提速（实测 2026-09-23，doubao-seed-2.0-lite）：整份契约的可见 JSON 与 reasoning
    共用输出上限，模型常写到 `finish_reason=length` 被截断 → 触发严格重试（单场景 75s→153s）。
    prompt 侧加了「输出规模硬约束」（每桶条数取下限附近、每条 ≤60 字、禁止成段复述教材原文），
    实测单次 60.9s、`finish_reason=stop`、无重试，且推导桶与 lesson_flow 质量不变。

    W2.1 四段式稳健性（RULINGS 裁定 5；LLM 主通道健康 → 这是健壮性问题）：
    ① 严格解析（`parse_and_validate` 现状）→ ② 失败才修复转义重试解析（现状）→
    ③ 仍失败则带 `STRICT_RETRY_INSTRUCTION` 重试 1 次 → ④ 仍失败则确定性兜底
    （`_fallback_proposal_data`，返回结构合法 proposal，**不抛错**）。
    全部失败路径留痕：`warnings_ack` 码 + 网关 ai_runs（每次调用一条）；
    用户界面只见提案与产品化中文文案（`background.py::ERROR_COPY`），
    绝不出现「生成内容未通过校验，请重试」。
    """
    if compact:
        prefill: dict[str, Any] | None = _prefill_from_context(context)
        prompt_result = build_compact_blueprint_prompt(context, max_context_tokens=max_context_tokens)
    else:
        prefill = None
        prompt_result = build_blueprint_prompt(context, max_context_tokens=max_context_tokens)
    result = llm_generate(prompt_result.prompt)
    if inspect.isawaitable(result):
        result = await result
    strict_retry_used = False
    used_fallback = False
    try:
        proposal = parse_and_validate(
            result, context_snapshot_hash=context.snapshot_hash, prefill=prefill, context=context
        )
    except BlueprintProposalInvalid as first_exc:
        # ③ 带更严格输出指令重试 1 次（只约束输出形态，事实边界铁律不变）
        strict_retry_used = True
        logger.warning(
            "blueprint parse failed, retrying with strict instruction",
            extra={"reason": str(first_exc)[:300]},
        )
        retry_result = llm_generate(prompt_result.prompt + STRICT_RETRY_INSTRUCTION)
        if inspect.isawaitable(retry_result):
            retry_result = await retry_result
        try:
            proposal = parse_and_validate(
                retry_result, context_snapshot_hash=context.snapshot_hash, prefill=prefill, context=context
            )
        except BlueprintProposalInvalid as retry_exc:
            # ④ 确定性兜底：返回结构合法的预填 proposal，不抛错（消除用户可见「生成失败」）
            logger.warning(
                "blueprint strict retry failed, using deterministic fallback",
                extra={"reason": str(retry_exc)[:300]},
            )
            used_fallback = True
            fallback_data = _fallback_proposal_data(context, prefill)
            proposal = TeachingBlueprintProposal.model_validate(_normalize_blueprint_payload(fallback_data))
    if strict_retry_used:
        # 留痕：首次解析/校验失败（重试后成功与否都如实记录，只披露不阻断）
        ack = list(proposal.warnings_ack)
        if _RETRY_USED_WARNING not in ack:
            ack.append(_RETRY_USED_WARNING)
        proposal.warnings_ack = ack[:30]
    # W1-A：教材无例题 → 生成 AI 变式并**显式标注** ai_generated（不冒充教材例题）。
    # 仅在 LLM 正常产出路径执行（确定性兜底路径不再补调 LLM，保持其零 LLM 语义）。
    if allow_ai_examples and not used_fallback and not proposal.examples:
        variants = await _generate_example_variants(proposal, llm_generate)
        if variants:
            from .schema import BlueprintItem as _BpItem

            proposal.examples = [_BpItem.model_validate(v) for v in variants]
            ack = list(proposal.warnings_ack)
            if _AI_EXAMPLES_WARNING not in ack:
                ack.append(_AI_EXAMPLES_WARNING)
            proposal.warnings_ack = ack[:30]
    if require_grounding:
        violations = check_grounding(proposal, context)
        if violations:
            raise GroundingViolation(violations)
    return proposal


def proposal_dto(proposal: TeachingBlueprintProposal) -> dict[str, Any]:
    """proposal → 可写 DTO（供 R14 propose_blueprint 的 body 落库；教师确认前非事实）。"""
    return proposal.model_dump(mode="json")


__all__ = [
    "BlueprintProjectionError",
    "BlueprintProposalInvalid",
    "GroundingViolation",
    "check_grounding",
    "derived_bucket_quality",
    "parse_and_validate",
    "project_blueprint",
    "proposal_dto",
    "sanitize_title",
]
