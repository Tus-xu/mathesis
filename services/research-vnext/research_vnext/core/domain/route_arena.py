"""RV10 · RouteArena —— 2–3 条**实质不同**的建模路线 + 服务端结构化 rubric。

为什么不是"让模型打分选最优"
---------------------------
让模型给每条路线打一个总分、然后宣布"路线 2 最优"，是把**不可审计的偏好**
伪装成**可审计的结论**：分数没有判据、trade-off 被一个数字压平、
研究者失去了"在哪些维度上 A 好、哪些维度上 B 好"的视野。

因此本模块做三件事，并把裁决权留给研究者：

1. **结构完备门**：每条路线必须有模型/方法、假设、变量/参数、数据、
   可识别性、指标、证伪/反例、资源、风险、证据覆盖。
   缺失的路线被标 `REJECTED` 并**保留原因**（不是静默删除）——
   "这条路线因为什么不成立"本身就是有价值的研究信息。
2. **预算/证据门**：资源超出声明预算、或证据引用不可解析 ⇒ 路线级拒绝。
3. **服务端 rubric**：比较矩阵由服务端从路线**结构**推导
   （`STRONG / MIXED / WEAK / UNKNOWN` 四档，每档带机器可读理由），
   **没有总分、没有胜者、没有排名**。模型输出里出现
   winner / totalScore / ranking 之类的裁决词 ⇒ 整轮拒绝。

trade-off 与 unknown 是**一等公民**：它们原样保留在每条路线上，
不被任何比较逻辑吞掉——"A 快但依赖分布假设"这句话比任何分数都更有用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"

MIN_ROUTES = 2
MAX_ROUTES = 3

#: 路线的认识论类型——**复用** RV09 ResearchMap 的词表（同一套认识论，不造第二套）。
ROUTE_KINDS: tuple[str, ...] = (
    "THEORETICAL",
    "COMPUTATIONAL",
    "EMPIRICAL",
    "LITERATURE",
    "GEOMETRIC",
    "ALGEBRAIC",
    "CONSTRUCTION",
)

#: 变量角色（封闭词表）。
VARIABLE_ROLES: tuple[str, ...] = ("INDEPENDENT", "DEPENDENT", "CONTROL", "PARAMETER")

#: 数据/证据的类别（对齐 ProblemMap requiredEvidence 的 kind 词表）。
DATA_KINDS: tuple[str, ...] = (
    "LITERATURE",
    "DATASET",
    "DERIVATION",
    "NUMERICAL",
    "COUNTEREXAMPLE",
    "EXPERT",
    "TEXTBOOK",
)

#: 模型试图裁决"哪条路线赢"的词——出现即整轮拒绝。
VERDICT_TELLS: frozenset[str] = frozenset(
    {
        "winner",
        "recommendation",
        "recommended",
        "recommend",
        "ranking",
        "ranked",
        "best",
        "bestRoute",
        "totalScore",
        "total_score",
        "overallScore",
        "overall_score",
        "finalScore",
        "verdict",
    }
)

#: 模型可提供的顶层键（封闭词表；schemaVersion/comparison/insufficiencies 由服务端写入）。
MODEL_TOP_LEVEL_KEYS: tuple[str, ...] = ("routes", "budget")

RUBRIC_LEVELS: tuple[str, ...] = ("STRONG", "MIXED", "WEAK", "UNKNOWN")

_RUBRIC_DISCLAIMER = (
    "本比较矩阵由服务端从各路线的结构声明推导，只做维度级对照；"
    "它没有总分、不产生胜者——最终选择由研究者做出。"
)

_ARENA_NOTE = "维度取值依据见各格 reason；UNKNOWN 表示服务端无法从结构判定，而不是『差』。"

_TARGET_RE = re.compile(
    r"^(?P<obj>route:(?P<rid>r[0-9]+)|budget)\.(?P<rest>[A-Za-z0-9_.#]+)$"
)
#: 整条路线的 accept / reject 目标（不带字段部分）。
_ROUTE_REF_RE = re.compile(r"^route:(?P<rid>r[0-9]+)$")

#: 编辑不得触碰的字段：来源/身份/状态/裁决由服务端持有（与 ProblemMap 的 origin 保护同源）。
EDIT_FORBIDDEN_FIELDS = frozenset(
    {"origin", "quote", "id", "kind", "status", "rejection", "evidenceRefs", "evidence_refs"}
)


class RouteArenaRejected(Exception):
    """结构不成立（可机器处理）。由上层转成 `ProposalRejected`。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class RouteArenaContent:
    content: dict[str, Any]
    insufficiencies: list[dict[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# 元素规范化
# --------------------------------------------------------------------------


def _as_str(value: Any, limit: int = 2000) -> str:
    return str(value if value is not None else "")[:limit]


def _norm_origin(value: Any, *, default: str = "INFERENCE") -> str:
    origin = str(value or "").upper()
    return origin if origin in ("USER", "SOURCE", "INFERENCE") else default


def _norm_texts(raw: Any, *, field_name: str) -> list[dict[str, str]]:
    """text/origin 元素列表（缺失 origin 一律降级 INFERENCE——与 ResearchMap 同规则）。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise RouteArenaRejected("ROUTE_ARENA_SECTION_NOT_LIST", f"{field_name} 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"text": _as_str(item), "origin": default_origin()})
            continue
        if not isinstance(item, dict):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", f"{field_name} 的元素必须是对象或字符串")
        entry = {"text": _as_str(item.get("text") or item.get("value")), "origin": _norm_origin(item.get("origin"))}
        if entry["origin"] == "USER":
            quote = _as_str(item.get("quote"), 2000).strip()
            if quote:
                entry["quote"] = quote
        out.append(entry)
    return out


def default_origin() -> str:
    return "INFERENCE"


def _norm_variables(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RouteArenaRejected("ROUTE_ARENA_SECTION_NOT_LIST", "variables 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": _as_str(item, 128), "role": "PARAMETER", "text": "", "origin": default_origin()})
            continue
        if not isinstance(item, dict):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", "variables 的元素必须是对象或字符串")
        role = str(item.get("role") or "PARAMETER").upper()
        if role not in VARIABLE_ROLES:
            role = "PARAMETER"
        out.append(
            {
                "name": _as_str(item.get("name") or item.get("text"), 128),
                "role": role,
                "text": _as_str(item.get("description") or item.get("text")),
                "origin": _norm_origin(item.get("origin")),
            }
        )
    return out


def _norm_metrics(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise RouteArenaRejected("ROUTE_ARENA_SECTION_NOT_LIST", "metrics 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"name": _as_str(item, 128), "direction": "", "origin": default_origin()})
            continue
        if not isinstance(item, dict):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", "metrics 的元素必须是对象或字符串")
        direction = str(item.get("direction") or "").upper()
        if direction not in ("MINIMIZE", "MAXIMIZE", "TEST", ""):
            direction = "TEST"
        out.append(
            {
                "name": _as_str(item.get("name") or item.get("text"), 128),
                "direction": direction,
                "origin": _norm_origin(item.get("origin")),
            }
        )
    return out


def _norm_risks(raw: Any) -> list[dict[str, str]]:
    out = _norm_texts(raw, field_name="risks")
    raw_list = raw if isinstance(raw, list) else []
    for i, item in enumerate(out):
        src = raw_list[i] if i < len(raw_list) and isinstance(raw_list[i], dict) else {}
        severity = str(src.get("severity") or "MEDIUM").upper()
        if severity not in ("LOW", "MEDIUM", "HIGH"):
            severity = "MEDIUM"
        item["severity"] = severity
        item["mitigation"] = _as_str(src.get("mitigation"))
    return out


def _norm_data(raw: Any) -> list[dict[str, str]]:
    out = _norm_texts(raw, field_name="data")
    raw_list = raw if isinstance(raw, list) else []
    for i, item in enumerate(out):
        src = raw_list[i] if i < len(raw_list) and isinstance(raw_list[i], dict) else {}
        kind = str(src.get("kind") or "").upper()
        item["kind"] = kind if kind in DATA_KINDS else "DATASET"
    return out


def _norm_identifiability(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        return {"text": _as_str(raw), "status": "UNKNOWN", "origin": default_origin()}
    if not isinstance(raw, dict):
        return {"text": "", "status": "UNKNOWN", "origin": default_origin()}
    status = str(raw.get("status") or "UNKNOWN").upper()
    if status not in ("IDENTIFIABLE", "PARTIALLY_IDENTIFIABLE", "UNKNOWN"):
        status = "UNKNOWN"
    return {
        "text": _as_str(raw.get("text") or raw.get("value")),
        "status": status,
        "origin": _norm_origin(raw.get("origin")),
    }


def _norm_resources(raw: Any) -> dict[str, Any]:
    if isinstance(raw, (int, float)):
        raw = {"computeHours": raw}
    if not isinstance(raw, dict):
        return {"computeHours": None, "dataVolume": "", "notes": "", "origin": default_origin()}
    hours = raw.get("computeHours", raw.get("compute_hours"))
    cost = raw.get("costUsd", raw.get("cost_usd"))
    return {
        "computeHours": float(hours) if isinstance(hours, (int, float)) else None,
        "costUsd": float(cost) if isinstance(cost, (int, float)) else None,
        "dataVolume": _as_str(raw.get("dataVolume") or raw.get("data_volume"), 256),
        "notes": _as_str(raw.get("notes"), 1000),
        "origin": _norm_origin(raw.get("origin")),
    }


def _norm_budget(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"maxComputeHours": None, "maxCostUsd": None, "notes": "", "origin": default_origin()}
    hours = raw.get("maxComputeHours", raw.get("max_compute_hours"))
    cost = raw.get("maxCostUsd", raw.get("max_cost_usd"))
    return {
        "maxComputeHours": float(hours) if isinstance(hours, (int, float)) else None,
        "maxCostUsd": float(cost) if isinstance(cost, (int, float)) else None,
        "notes": _as_str(raw.get("notes"), 1000),
        "origin": _norm_origin(raw.get("origin")),
    }


def _norm_refs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise RouteArenaRejected("ROUTE_ARENA_SECTION_NOT_LIST", "evidenceRefs 必须是数组")
    return [_as_str(x, 64) for x in raw]


# --------------------------------------------------------------------------
# 路线级门
# --------------------------------------------------------------------------


def _check_route_elements(route: dict[str, Any]) -> tuple[str, str] | None:
    """返回 (code, message) 表示该路线不成立；None 表示结构完备。"""
    rid = route["id"]
    if not route["label"].strip():
        return ("ROUTE_NO_LABEL", f"{rid} 缺少 label（这条路线叫什么）")
    if not route["method"].strip():
        return ("ROUTE_NO_METHOD", f"{rid} 没有说明模型/方法——路线之间无从比较")
    if not route["hypotheses"]:
        return ("ROUTE_NO_HYPOTHESIS", f"{rid} 没有可检验的假设")
    if not route["variables"]:
        return ("ROUTE_NO_VARIABLES", f"{rid} 没有声明变量/参数——实验将无从设置")
    if not route["metrics"]:
        return ("ROUTE_NO_METRICS", f"{rid} 没有可度量的指标——成功与失败无法区分")
    if not route["falsification"]:
        return ("ROUTE_NO_FALSIFICATION", f"{rid} 没有证伪判据——不可反驳的假设不能作为路线")
    if not route["identifiability"]["text"].strip():
        return ("ROUTE_NO_IDENTIFIABILITY", f"{rid} 没有可识别性声明——参数是否可从数据恢复未知")
    res = route["resources"]
    if res["computeHours"] is None and not res["notes"].strip() and not res["dataVolume"].strip():
        return ("ROUTE_NO_RESOURCES", f"{rid} 没有资源估计——无法判断预算可行性")
    return None


def _check_budget(route: dict[str, Any], budget: dict[str, Any]) -> tuple[str, str] | None:
    res = route["resources"]
    max_h = budget.get("maxComputeHours")
    if isinstance(max_h, (int, float)) and isinstance(res.get("computeHours"), (int, float)):
        if float(res["computeHours"]) > float(max_h):
            return (
                "ROUTE_BUDGET_EXCEEDED",
                f"{route['id']} 需 {res['computeHours']}h，超出声明预算 {max_h}h",
            )
    max_c = budget.get("maxCostUsd")
    if isinstance(max_c, (int, float)) and isinstance(res.get("costUsd"), (int, float)):
        if float(res["costUsd"]) > float(max_c):
            return (
                "ROUTE_BUDGET_EXCEEDED",
                f"{route['id']} 需 ${res['costUsd']}，超出声明预算 ${max_c}",
            )
    return None


def _label_key(label: str) -> str:
    return re.sub(r"[\s·\-—_/（）()【】]+", "", label).lower()


# --------------------------------------------------------------------------
# rubric（服务端推导；无总分）
# --------------------------------------------------------------------------


def _dim(level: str, reason: str) -> dict[str, str]:
    return {"level": level, "reason": reason}


def compute_rubric(
    routes: list[dict[str, Any]],
    *,
    budget: dict[str, Any] | None = None,
    resolved_ids: set[str] | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    """从路线**结构**推导维度级对照。四档：STRONG / MIXED / WEAK / UNKNOWN。

    `resolved_ids is None` 表示证据尚未经服务端解析（提案期），证据维度只能是
    PENDING；解析之后由 `finalize()` 重算。**绝不输出总分。**
    """
    rubric: dict[str, dict[str, dict[str, str]]] = {}
    budget = budget or {}
    for r in routes:
        rid = str(r.get("id") or "")
        dims: dict[str, dict[str, str]] = {}

        # 证据覆盖
        refs = [x for x in (r.get("evidenceRefs") or []) if x]
        if not refs:
            dims["evidence"] = _dim("UNKNOWN", "NO_EVIDENCE_REFS")
        elif resolved_ids is None:
            dims["evidence"] = _dim("MIXED", "PENDING_RESOLUTION")
        else:
            unresolved = [x for x in refs if x not in resolved_ids]
            if not unresolved:
                dims["evidence"] = _dim("STRONG", "ALL_REFS_RESOLVED")
            elif len(unresolved) == len(refs):
                dims["evidence"] = _dim("WEAK", "NO_REF_RESOLVED")
            else:
                dims["evidence"] = _dim("MIXED", "SOME_REFS_UNRESOLVED")

        # 可证伪性
        if r.get("falsification") and r.get("hypotheses"):
            dims["falsifiability"] = _dim("STRONG", "HAS_FALSIFICATION_AND_HYPOTHESIS")
        elif r.get("falsification") or r.get("hypotheses"):
            dims["falsifiability"] = _dim("MIXED", "PARTIAL_FALSIFIABILITY")
        else:
            dims["falsifiability"] = _dim("WEAK", "NO_FALSIFICATION")

        # 可识别性
        status = (r.get("identifiability") or {}).get("status") or "UNKNOWN"
        dims["identifiability"] = _dim(
            {"IDENTIFIABLE": "STRONG", "PARTIALLY_IDENTIFIABLE": "MIXED", "UNKNOWN": "UNKNOWN"}.get(status, "WEAK"),
            f"IDENTIFIABILITY_{status}",
        )

        # 预算适配
        res = r.get("resources") or {}
        hours = res.get("computeHours")
        max_h = budget.get("maxComputeHours")
        if hours is None:
            dims["resourceFit"] = _dim("UNKNOWN", "COMPUTE_HOURS_UNDECLARED")
        elif max_h is None:
            dims["resourceFit"] = _dim("MIXED", "NO_BUDGET_DECLARED")
        elif float(hours) <= float(max_h):
            dims["resourceFit"] = _dim("STRONG", "WITHIN_BUDGET")
        else:
            dims["resourceFit"] = _dim("WEAK", "OVER_BUDGET")

        # 风险暴露
        risks = r.get("risks") or []
        if not risks:
            dims["riskExposure"] = _dim("UNKNOWN", "NO_RISKS_DECLARED")
        elif any((x.get("severity") or "").upper() == "HIGH" for x in risks):
            dims["riskExposure"] = _dim("MIXED", "HAS_HIGH_SEVERITY_RISK")
        else:
            dims["riskExposure"] = _dim("STRONG", "NO_HIGH_SEVERITY_RISK")

        rubric[rid] = dims
    return rubric


def comparison_block(
    routes: list[dict[str, Any]],
    *,
    budget: dict[str, Any] | None = None,
    resolved_ids: set[str] | None = None,
) -> dict[str, Any]:
    """比较块：rubric + 免责声明 + 显式 `hasTotalScore=false`。"""
    return {
        "rubric": compute_rubric(routes, budget=budget, resolved_ids=resolved_ids),
        "dimensions": ["evidence", "falsifiability", "identifiability", "resourceFit", "riskExposure"],
        "levels": list(RUBRIC_LEVELS),
        "hasTotalScore": False,
        "rubricDisclaimer": _RUBRIC_DISCLAIMER,
        "arenaNote": _ARENA_NOTE,
    }


# --------------------------------------------------------------------------
# 规范化主流程
# --------------------------------------------------------------------------


def normalize(payload: dict) -> RouteArenaContent:
    """把 proposal payload 规范化成 RouteArena 内容并执行全部门。"""
    if not isinstance(payload, dict):
        raise RouteArenaRejected("ROUTE_ARENA_NOT_OBJECT", "route_arena payload 必须是对象")

    tells = sorted(set(payload) & VERDICT_TELLS)
    if tells:
        raise RouteArenaRejected(
            "ROUTE_ARENA_VERDICT_FORBIDDEN",
            f"payload 含裁决词 {tells}：路线比较没有总分、没有胜者——"
            "比较矩阵由服务端推导，选择由研究者做出",
        )

    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise RouteArenaRejected(
            "ROUTE_ARENA_NO_ROUTES",
            "route_arena 必须给出 routes（2–3 条实质不同的建模路线）",
        )
    if not (MIN_ROUTES <= len(raw_routes) <= MAX_ROUTES):
        raise RouteArenaRejected(
            "ROUTE_ARENA_ROUTE_COUNT",
            f"路线数量必须为 {MIN_ROUTES}–{MAX_ROUTES}，实际 {len(raw_routes)}",
        )

    unknown_keys = sorted(set(payload) - set(MODEL_TOP_LEVEL_KEYS))
    if unknown_keys:
        raise RouteArenaRejected(
            "ROUTE_ARENA_UNKNOWN_KEY", f"payload 含未登记字段 {unknown_keys}"
        )

    budget = _norm_budget(payload.get("budget"))

    routes: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    for idx, raw in enumerate(raw_routes):
        if not isinstance(raw, dict):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", "routes 的元素必须是对象")
        route = _normalize_route(raw, index=idx)
        failure = _check_route_elements(route)
        if failure is None:
            failure = _check_budget(route, budget)
        if failure is not None:
            code, message = failure
            route["status"] = "REJECTED"
            route["rejection"] = {"code": code, "message": message}
            rejections.append({"routeId": route["id"], "code": code, "message": message})
        else:
            route["status"] = "CANDIDATE"
        routes.append(route)

    insufficiencies = _collect_insufficiencies(routes, budget)
    _check_arena_gates(routes)

    content: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "budget": budget,
        "routes": routes,
        "comparison": comparison_block(routes, budget=budget, resolved_ids=None),
        "insufficiencies": insufficiencies,
    }
    if rejections:
        content["rejections"] = rejections
    return RouteArenaContent(content=content, insufficiencies=insufficiencies)


def _normalize_route(raw: dict[str, Any], *, index: int) -> dict[str, Any]:
    rid = _as_str(raw.get("id"), 32).strip() or f"r{index + 1}"
    kind = str(raw.get("kind") or "").upper()
    if kind and kind not in ROUTE_KINDS:
        raise RouteArenaRejected(
            "ROUTE_KIND_UNKNOWN", f"{rid} 的 kind={kind!r} 不在 {list(ROUTE_KINDS)}"
        )
    if not kind:
        kind = "THEORETICAL"
    return {
        "id": rid,
        "label": _as_str(raw.get("label") or raw.get("name") or raw.get("title"), 128),
        "kind": kind,
        "method": _as_str(raw.get("method") or raw.get("approach")),
        "hypotheses": _norm_texts(raw.get("hypotheses"), field_name="hypotheses"),
        "variables": _norm_variables(raw.get("variables")),
        "data": _norm_data(raw.get("data")),
        "identifiability": _norm_identifiability(raw.get("identifiability")),
        "metrics": _norm_metrics(raw.get("metrics")),
        "falsification": _norm_texts(raw.get("falsification") or raw.get("counterexamples"), field_name="falsification"),
        "resources": _norm_resources(raw.get("resources")),
        "risks": _norm_risks(raw.get("risks")),
        "evidenceRefs": _norm_refs(raw.get("evidenceRefs") or raw.get("evidence_refs")),
        "tradeOffs": _norm_texts(raw.get("tradeOffs") or raw.get("trade_offs"), field_name="tradeOffs"),
        "unknowns": _norm_texts(raw.get("unknowns"), field_name="unknowns"),
    }


def _check_arena_gates(routes: list[dict[str, Any]]) -> None:
    """对**有效（CANDIDATE）**路线执行可区分性 / kind 多样性 / 最少数量门。"""
    valid = [r for r in routes if r.get("status") == "CANDIDATE"]
    rejected_detail = "；".join(
        f"{r['id']}({(r.get('rejection') or {}).get('code', '?')})"
        for r in routes
        if r.get("status") == "REJECTED"
    )
    suffix = f"（被拒路线：{rejected_detail}）" if rejected_detail else ""
    if len(valid) < MIN_ROUTES:
        raise RouteArenaRejected(
            "ROUTE_ARENA_ROUTE_COUNT",
            f"实质成立的路线不足 {MIN_ROUTES} 条{suffix}——"
            "少于两条真差异路线不构成可比选的路线竞技场",
        )
    if len(valid) > MAX_ROUTES:
        raise RouteArenaRejected("ROUTE_ARENA_ROUTE_COUNT", f"有效路线超过 {MAX_ROUTES} 条{suffix}")

    seen: dict[str, str] = {}
    for r in valid:
        key = _label_key(r["label"])
        if key in seen:
            raise RouteArenaRejected(
                "ROUTE_NOT_DISTINGUISHABLE",
                f"{r['id']} 与 {seen[key]} 的 label 规范化后相同（{r['label']!r}）："
                "请给出真正不同的路线，而不是同一条路线换名字",
            )
        seen[key] = r["id"]

    kinds = {r["kind"] for r in valid}
    if len(kinds) < 2:
        raise RouteArenaRejected(
            "ROUTE_SINGLE_LENS",
            f"全部有效路线的 kind 都是 {sorted(kinds)[0]}：只有一种认识论类型的多条路线是同一条路线的变奏",
        )


def _collect_insufficiencies(routes: list[dict[str, Any]], budget: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in routes:
        if not r.get("data"):
            out.append(
                {"routeId": r["id"], "section": "data", "reason": "DATA_EMPTY", "role": f"{r['label']} 所需数据"}
            )
        res = r.get("resources") or {}
        if res.get("computeHours") is None:
            out.append(
                {
                    "routeId": r["id"],
                    "section": "resources.computeHours",
                    "reason": "COMPUTE_HOURS_UNDECLARED",
                    "role": f"{r['label']} 的资源估计",
                }
            )
    if budget.get("maxComputeHours") is None and budget.get("maxCostUsd") is None and not budget.get("notes"):
        out.append({"routeId": "*", "section": "budget", "reason": "BUDGET_UNDECLARED", "role": "本竞技场的预算基准"})
    return out


# --------------------------------------------------------------------------
# 证据门（DB 依赖；由 proposal handler 调用）
# --------------------------------------------------------------------------


def apply_evidence_gate(content: dict[str, Any], resolved_ids: set[str]) -> list[dict[str, str]]:
    """把"引用了不可解析证据"的路线标为 REJECTED（证据不足被拒）。

    返回新增拒绝列表。**只拒不删**：被拒路线保留在 arena 中展示原因。
    """
    added: list[dict[str, str]] = []
    for r in content.get("routes") or []:
        if r.get("status") == "REJECTED":
            continue
        refs = [x for x in (r.get("evidenceRefs") or []) if x]
        unresolved = [x for x in refs if x not in resolved_ids]
        if unresolved:
            message = f"{r['id']} 引用的证据无法解析或未授权: {unresolved}"
            r["status"] = "REJECTED"
            r["rejection"] = {"code": "ROUTE_EVIDENCE_UNRESOLVED", "message": message}
            added.append({"routeId": r["id"], "code": "ROUTE_EVIDENCE_UNRESOLVED", "message": message})
    if added:
        rejections = list(content.get("rejections") or []) + added
        content["rejections"] = rejections
    return added


def finalize(
    content: dict[str, Any], *, resolved_ids: set[str] | None = None
) -> dict[str, Any]:
    """证据解析之后重算比较矩阵（handler 在 `_ground_origins` 前后调用皆可）。"""
    out = dict(content)
    out["comparison"] = comparison_block(
        list(out.get("routes") or []),
        budget=dict(out.get("budget") or {}),
        resolved_ids=resolved_ids,
    )
    return out


# --------------------------------------------------------------------------
# 逐项编辑（revise 流）
# --------------------------------------------------------------------------


def _find_route(content: dict[str, Any], rid: str) -> dict[str, Any] | None:
    return next((r for r in content.get("routes") or [] if r.get("id") == rid), None)


def _check_edit_target(target: str) -> re.Match[str]:
    if not isinstance(target, str) or not _TARGET_RE.match(target):
        raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"无法解析编辑目标: {target!r}")
    match = _TARGET_RE.match(target)
    rest = match.group("rest") or ""
    # 禁改字段：按 token 检查（`.origin`、`#0.origin` 等任何位置出现都算）
    tokens = [t for t in re.split(r"[.#]", rest) if t]
    if any(t in EDIT_FORBIDDEN_FIELDS for t in tokens):
        raise RouteArenaRejected(
            "ROUTE_ARENA_EDIT_FORBIDDEN_FIELD",
            f"{target}：来源/身份/状态/裁决由服务端持有，不允许通过编辑修改",
        )
    return match


def _apply_edit(route: dict[str, Any] | None, rest: str, *, text: str | None, number: float | None) -> None:
    """把一次编辑落到路线 dict 上。rest 已通过禁改检查。"""
    tokens = [t for t in re.split(r"[.#]", rest) if t]

    def _dig(node: Any, path: list[str]) -> Any:
        if not path:
            return node
        head = path[0]
        m = re.match(r"^([A-Za-z]+)#([0-9]+)$", head)
        if m and isinstance(node, list):
            idx = int(m.group(2))
            if idx >= len(node):
                raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"索引越界: {head}")
            return _dig(node[idx], path[1:])
        if isinstance(node, dict) and head in node:
            return _dig(node[head], path[1:])
        raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {'.'.join(path)}")

    if route is None:
        raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", "目标路线不存在")

    # 顶层标量（label / method）
    if len(tokens) == 1 and tokens[0] in ("label", "method"):
        if text is None:
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", f"{rest} 需要 text")
        route[tokens[0]] = _as_str(text, 128 if tokens[0] == "label" else 2000)
        return
    # identifiability.text
    if len(tokens) == 2 and tokens[0] == "identifiability" and tokens[1] == "text":
        if text is None:
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", f"{rest} 需要 text")
        route["identifiability"]["text"] = _as_str(text)
        return
    # resources 数值/文本
    if tokens and tokens[0] == "resources":
        field = tokens[1] if len(tokens) > 1 else ""
        if field == "computeHours":
            route["resources"]["computeHours"] = float(number) if number is not None else None
            return
        if field in ("dataVolume", "notes"):
            route["resources"][field] = _as_str(text, 1000)
            return
        raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {rest}")

    # 列表元素文本（hypotheses#i.text 等）
    if len(tokens) >= 2 and "#" in tokens[0]:
        head, idx = tokens[0].split("#")
        i = int(idx)
        container = route.get(head)
        if not isinstance(container, list) or i >= len(container):
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {rest}")
        item = container[i]
        field = tokens[1]
        allowed = {
            "hypotheses": {"text"},
            "variables": {"name", "text"},
            "data": {"text"},
            "metrics": {"name", "direction"},
            "falsification": {"text"},
            "risks": {"text", "severity", "mitigation"},
            "tradeOffs": {"text"},
            "unknowns": {"text"},
        }.get(head, set())
        if field not in allowed:
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {rest}")
        if head == "risks" and field == "severity":
            sev = str(text or "MEDIUM").upper()
            item[field] = sev if sev in ("LOW", "MEDIUM", "HIGH") else "MEDIUM"
        elif head == "metrics" and field == "direction":
            direction = str(text or "").upper()
            item[field] = direction if direction in ("MINIMIZE", "MAXIMIZE", "TEST") else "TEST"
        else:
            if text is None:
                raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", f"{rest} 需要 text")
            item[field] = _as_str(text, 128 if field == "name" else 2000)
        return

    raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {rest}")


def apply_corrections(
    content: dict[str, Any],
    *,
    accept: list[str] | None,
    reject: list[str] | None,
    edits: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """逐项 accept / reject / edit（**只改提案草稿**，不触碰正式状态）。

    - `accept`: `["route:r1", ...]`——确认保留该路线（记录到 editSummary）；
    - `reject`: `["route:r3", ...]`——从草稿中移除该路线；
    - `edits`: `[{"target": "route:r1.label", "text": "..."}, ...]`。
    任何改动之后**整体重校验**（数量/可区分/kind 多样性/预算）并重算 rubric——
    与 ResearchMap 同哲学：结构编辑后的半成品比直接拒绝更糟。
    """
    out = {
        "schemaVersion": content.get("schemaVersion", SCHEMA_VERSION),
        "budget": dict(content.get("budget") or {}),
        "routes": [dict(r) for r in content.get("routes") or []],
    }
    # 深拷贝嵌套结构（routes 元素含 list/dict）
    out["routes"] = [
        {**r, "resources": dict(r.get("resources") or {}), "identifiability": dict(r.get("identifiability") or {})}
        for r in out["routes"]
    ]

    accepted: list[str] = []
    for target in accept or []:
        match = _ROUTE_REF_RE.match(target or "")
        if not match:
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"无法解析 accept 目标: {target!r}")
        if _find_route(out, match.group("rid")) is None:
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标路线不存在: {target}")
        accepted.append(target)

    for target in reject or []:
        match = _ROUTE_REF_RE.match(target or "")
        if not match:
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"无法解析 reject 目标: {target!r}")
        route = _find_route(out, match.group("rid"))
        if route is None:
            raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标路线不存在: {target}")
        out["routes"] = [r for r in out["routes"] if r.get("id") != route["id"]]

    edited: list[str] = []
    for edit in edits or []:
        if not isinstance(edit, dict):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", "edits 的元素必须是对象")
        # 两种形状等价受理：
        # - 服务端/测试风格：{"target": "route:r1.label", "text": "..."}；
        # - 前端通用 revise 风格（与 problem_map 一致）：{"ref": ..., "value": {"text": ...}}。
        target_raw = edit.get("target") or edit.get("ref") or ""
        value = edit.get("value")
        text_value = edit.get("text")
        number = edit.get("number")
        if isinstance(value, dict):
            if "text" in value:
                text_value = value.get("text")
            if "number" in value:
                number = value.get("number")
        match = _check_edit_target(str(target_raw))
        if number is not None and not isinstance(number, (int, float)):
            raise RouteArenaRejected("ROUTE_ARENA_ELEMENT_INVALID", "number 必须是数值")
        if match.group("obj") == "budget":
            field = (match.group("rest") or "").split(".", 1)[-1]
            if field == "maxComputeHours":
                out["budget"]["maxComputeHours"] = float(number) if number is not None else None
            elif field == "maxCostUsd":
                out["budget"]["maxCostUsd"] = float(number) if number is not None else None
            elif field == "notes":
                out["budget"]["notes"] = _as_str(text_value, 1000)
            else:
                raise RouteArenaRejected("ROUTE_ARENA_TARGET_UNKNOWN", f"目标不存在: {edit.get('target')}")
        else:
            route = _find_route(out, match.group("rid"))
            _apply_edit(route, match.group("rest") or "", text=text_value, number=number)
        edited.append(str(edit.get("target")))

    # 整体重校验 + 重算 rubric / insufficiencies
    budget = _norm_budget(out["budget"])
    out["budget"] = budget
    for r in out["routes"]:
        if r.get("status") == "REJECTED":
            continue
        failure = _check_route_elements(r) or _check_budget(r, budget)
        if failure is not None:
            code, message = failure
            r["status"] = "REJECTED"
            r["rejection"] = {"code": code, "message": message}
    _check_arena_gates(out["routes"])

    insufficiencies = _collect_insufficiencies(out["routes"], budget)
    out["insufficiencies"] = insufficiencies
    out["comparison"] = comparison_block(out["routes"], budget=budget, resolved_ids=None)
    out["editSummary"] = {
        "accepted": len(accepted),
        "rejected": len(reject or []),
        "edited": len(edited),
    }
    return out


__all__ = [
    "DATA_KINDS",
    "EDIT_FORBIDDEN_FIELDS",
    "MAX_ROUTES",
    "MIN_ROUTES",
    "ROUTE_KINDS",
    "RUBRIC_LEVELS",
    "RouteArenaContent",
    "RouteArenaRejected",
    "SCHEMA_VERSION",
    "VERDICT_TELLS",
    "apply_corrections",
    "apply_evidence_gate",
    "comparison_block",
    "compute_rubric",
    "finalize",
    "normalize",
]
