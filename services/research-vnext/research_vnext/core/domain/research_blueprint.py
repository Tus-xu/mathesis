"""RV10 · ResearchBlueprint —— 被选定路线的**承诺书**（proposal，不是结论）。

Blueprint 在链路里的位置
------------------------
ProblemMap（问题是什么）→ ResearchMap（从哪些视角逼近）→ **RouteArena**（有哪些
路线、各自的结构与 trade-off）→ **ResearchBlueprint**（选定一条，绑定上游修订，
声明实验计划 / 证据要求 / 验证门 / 预算与截止 / 停止条件）→ RV11 实验执行。

三条硬规则
----------
1. **绑定由服务端解析**：模型只说"我选 routeId=r1"；服务端去查**最新已确认**的
   ProblemMap / ResearchMap / RouteArena 修订并把 `{revision, objectId, contentHash}`
   写进 binds。模型连 revision 号都不许提供——自报的修订号是幻觉修订号，
   拒绝比宽容便宜得多。
2. **只绑定 CANDIDATE 路线**：被拒路线（证据不足/超预算/结构缺失）不能被选定。
3. **Blueprint 是 proposal，不是结论**：payload 里出现 status=PROVEN、conclusion
   之类的结论词 ⇒ 拒绝。真假裁决属 RV12 的 claim 域，蓝图只承诺"将如何验证"。

为什么确认前不创建实验 job：蓝图被确认只意味着**研究者承诺了这个计划**；
实验执行是 RV11 消费 `research.blueprint_ready` 事件之后的事。事件只携带
ID / 版本 / 哈希——大上下文留在库里，事件流不做第二份真相。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

SCHEMA_VERSION = "1.0.0"

#: 模型可提供的顶层键（封闭词表）。`binds` / `route` / `modelSpec` / `lineage`
#: 由服务端写入；`schemaVersion` / `insufficiencies` 同样不由模型提供。
MODEL_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "objective",
    "routeId",
    "experimentPlan",
    "evidenceRequirements",
    "verificationGates",
    "budget",
    "deadline",
    "stopConditions",
    "assumptions",
    "risks",
)

#: 结论性词——blueprint 是承诺书，出现即拒。
CONCLUSION_TELLS: frozenset[str] = frozenset(
    {"status", "proven", "verdict", "conclusion", "result", "claimStatus", "verified"}
)

_DATA_KINDS = ("LITERATURE", "DATASET", "DERIVATION", "NUMERICAL", "COUNTEREXAMPLE", "EXPERT", "TEXTBOOK")
_SEVERITIES = ("LOW", "MEDIUM", "HIGH")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class BlueprintRejected(Exception):
    """结构不成立（可机器处理）。由上层转成 `ProposalRejected`。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class BlueprintContent:
    content: dict[str, Any]
    insufficiencies: list[dict[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# 元素规范化
# --------------------------------------------------------------------------


def _as_str(value: Any, limit: int = 2000) -> str:
    return str(value if value is not None else "")[:limit]


def _norm_origin(value: Any) -> str:
    origin = str(value or "").upper()
    return origin if origin in ("USER", "SOURCE", "INFERENCE") else "INFERENCE"


def _norm_texts(raw: Any, *, field_name: str, allow_empty: bool = False) -> list[dict[str, str]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise BlueprintRejected("BLUEPRINT_SECTION_NOT_LIST", f"{field_name} 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"text": _as_str(item), "origin": "INFERENCE"})
            continue
        if not isinstance(item, dict):
            raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", f"{field_name} 的元素必须是对象或字符串")
        entry = {"text": _as_str(item.get("text") or item.get("value")), "origin": _norm_origin(item.get("origin"))}
        if entry["origin"] == "USER":
            quote = _as_str(item.get("quote"), 2000).strip()
            if quote:
                entry["quote"] = quote
        out.append(entry)
    if not out and not allow_empty:
        raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", f"{field_name} 不能是空数组——请留空整个键或给出内容")
    return out


def _norm_evidence_requirements(raw: Any) -> list[dict[str, str]]:
    if raw is None or not isinstance(raw, list):
        raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", "evidenceRequirements 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"text": _as_str(item), "kind": "DATASET", "evidenceRef": "", "origin": "INFERENCE"})
            continue
        if not isinstance(item, dict):
            raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", "evidenceRequirements 的元素必须是对象或字符串")
        kind = str(item.get("kind") or "DATASET").upper()
        if kind not in _DATA_KINDS:
            kind = "DATASET"
        out.append(
            {
                "text": _as_str(item.get("text") or item.get("value")),
                "kind": kind,
                "evidenceRef": _as_str(item.get("evidenceRef") or item.get("evidence_ref"), 64),
                "origin": _norm_origin(item.get("origin")),
            }
        )
    return out


def _norm_gates(raw: Any) -> list[dict[str, str]]:
    if raw is None or not isinstance(raw, list):
        raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", "verificationGates 必须是数组")
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"text": _as_str(item), "criterion": "", "origin": "INFERENCE"})
            continue
        if not isinstance(item, dict):
            raise BlueprintRejected("BLUEPRINT_ELEMENT_INVALID", "verificationGates 的元素必须是对象或字符串")
        out.append(
            {
                "text": _as_str(item.get("text") or item.get("value")),
                "criterion": _as_str(item.get("criterion"), 128),
                "origin": _norm_origin(item.get("origin")),
            }
        )
    return out


def _norm_risks(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    out = _norm_texts(raw, field_name="risks", allow_empty=True)
    raw_list = raw if isinstance(raw, list) else []
    for i, item in enumerate(out):
        src = raw_list[i] if i < len(raw_list) and isinstance(raw_list[i], dict) else {}
        severity = str(src.get("severity") or "MEDIUM").upper()
        item["severity"] = severity if severity in _SEVERITIES else "MEDIUM"
        item["mitigation"] = _as_str(src.get("mitigation"))
    return out


def _norm_experiment_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BlueprintRejected("BLUEPRINT_NO_EXPERIMENT_PLAN", "blueprint 必须包含 experimentPlan（RV11 将按它创建作业）")
    metrics = raw.get("metrics")
    if isinstance(metrics, str):
        metrics = [metrics]
    if not isinstance(metrics, list) or not [m for m in metrics if _as_str(m, 128).strip()]:
        raise BlueprintRejected(
            "BLUEPRINT_PLAN_NO_METRIC",
            "experimentPlan 必须声明至少一个度量指标——没有指标的实验无法判定成败",
        )
    stages = [s for s in (raw.get("stages") or []) if isinstance(s, dict) or isinstance(s, str)]
    return {
        "objective": _as_str(raw.get("objective")),
        "datasetRef": _as_str(raw.get("datasetRef") or raw.get("dataset_ref"), 512),
        "codeRef": _as_str(raw.get("codeRef") or raw.get("code_ref"), 512),
        "metrics": [_as_str(m, 128) for m in metrics],
        "stages": [
            {"text": _as_str(s.get("text") if isinstance(s, dict) else s), "origin": _norm_origin(s.get("origin") if isinstance(s, dict) else None)}
            for s in stages
        ],
        "environment": raw.get("environment") if isinstance(raw.get("environment"), dict) else {},
        "notes": _as_str(raw.get("notes"), 2000),
    }


def _norm_budget(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"computeHours": None, "costUsd": None, "notes": "", "origin": "INFERENCE"}
    hours = raw.get("computeHours", raw.get("compute_hours"))
    cost = raw.get("costUsd", raw.get("cost_usd"))
    return {
        "computeHours": float(hours) if isinstance(hours, (int, float)) else None,
        "costUsd": float(cost) if isinstance(cost, (int, float)) else None,
        "notes": _as_str(raw.get("notes"), 1000),
        "origin": _norm_origin(raw.get("origin")),
    }


def _norm_deadline(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        raw = {"at": raw}
    if not isinstance(raw, dict):
        raise BlueprintRejected("BLUEPRINT_DEADLINE_INVALID", "deadline 必须是对象或 ISO 日期字符串")
    at = _as_str(raw.get("at"), 64).strip()
    if at and not (_DATE_RE.match(at) or _ISO_DT_RE.match(at)):
        raise BlueprintRejected(
            "BLUEPRINT_DEADLINE_INVALID",
            f"deadline.at={at!r} 不是可解析的 ISO 日期（YYYY-MM-DD）——模糊的截止日期无法成为停止条件",
        )
    return {
        "at": at,
        "notes": _as_str(raw.get("notes"), 1000),
        "origin": _norm_origin(raw.get("origin")),
    }


# --------------------------------------------------------------------------
# 纯结构校验
# --------------------------------------------------------------------------


def normalize(payload: dict) -> BlueprintContent:
    """把 proposal payload 规范化成 Blueprint 内容（绑定之前的纯结构部分）。"""
    if not isinstance(payload, dict):
        raise BlueprintRejected("BLUEPRINT_NOT_OBJECT", "research_blueprint payload 必须是对象")

    tells = sorted(set(payload) & CONCLUSION_TELLS)
    if tells:
        raise BlueprintRejected(
            "BLUEPRINT_CONCLUSION_TELL",
            f"payload 含结论性字段 {tells}：blueprint 是『将如何验证』的承诺书，"
            "不是结论——真假裁决属于后续的 claim 域",
        )

    unknown_keys = sorted(set(payload) - set(MODEL_TOP_LEVEL_KEYS))
    if unknown_keys:
        raise BlueprintRejected(
            "BLUEPRINT_UNKNOWN_KEY",
            f"payload 含未登记字段 {unknown_keys}（绑定/修订号由服务端解析，不由模型提供）",
        )

    objective = _as_str(payload.get("objective")).strip()
    if not objective:
        raise BlueprintRejected("BLUEPRINT_NO_OBJECTIVE", "blueprint 缺少 objective")
    route_id = _as_str(payload.get("routeId") or payload.get("route_id"), 32).strip()
    if not route_id:
        raise BlueprintRejected("BLUEPRINT_NO_ROUTE", "blueprint 必须从路线竞技场中选择一条 routeId")

    plan = _norm_experiment_plan(payload.get("experimentPlan"))

    evidence_requirements = _norm_evidence_requirements(payload.get("evidenceRequirements"))
    if not evidence_requirements:
        raise BlueprintRejected(
            "BLUEPRINT_NO_EVIDENCE_REQUIREMENT",
            "blueprint 必须声明至少一条证据要求——它与验证门一起构成 RV11 的验收契约",
        )
    gates = _norm_gates(payload.get("verificationGates"))
    if not gates:
        raise BlueprintRejected(
            "BLUEPRINT_NO_VERIFICATION_GATE",
            "blueprint 必须声明至少一条验证门——没有验证门的计划无法被证伪",
        )
    stop_conditions = _norm_texts(payload.get("stopConditions"), field_name="stopConditions", allow_empty=True)
    if not stop_conditions:
        raise BlueprintRejected(
            "BLUEPRINT_NO_STOP_CONDITION",
            "blueprint 必须声明停止条件——没有停止条件的研究永远做不完",
        )

    budget = _norm_budget(payload.get("budget"))
    if budget["computeHours"] is None and budget["costUsd"] is None and not budget["notes"].strip():
        raise BlueprintRejected(
            "BLUEPRINT_NO_BUDGET",
            "blueprint 必须声明预算（computeHours / costUsd / 说明至少其一）——预算是 RV11 的硬约束",
        )
    deadline = _norm_deadline(payload.get("deadline"))

    insufficiencies: list[dict[str, str]] = []
    if deadline["at"] == "" and not deadline["notes"].strip():
        insufficiencies.append(
            {"section": "deadline", "reason": "DEADLINE_UNSET", "role": "截止时间（未知也请说明为什么未知）"}
        )

    content: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "objective": objective,
        "routeId": route_id,
        "experimentPlan": plan,
        "evidenceRequirements": evidence_requirements,
        "verificationGates": gates,
        "budget": budget,
        "deadline": deadline,
        "stopConditions": stop_conditions,
        "assumptions": _norm_texts(payload.get("assumptions"), field_name="assumptions", allow_empty=True),
        "risks": _norm_risks(payload.get("risks")),
        "insufficiencies": insufficiencies,
    }
    return BlueprintContent(content=content, insufficiencies=insufficiencies)


# --------------------------------------------------------------------------
# 绑定（DB 依赖；由 proposal handler 调用）
# --------------------------------------------------------------------------


def _latest_row(db: Any, table: Any) -> Any | None:
    return (
        db.query(table)
        .filter(table.session_id.isnot(None))  # 占位；真实过滤由 handler 传 session_id 的查询完成
        .order_by(table.revision.desc())
        .first()
    )


def bind_context(db: Any, *, content: dict[str, Any], session_id: Any) -> BlueprintContent:
    """解析最新已确认的 ProblemMap / ResearchMap / RouteArena，写入 binds + route + modelSpec。

    **服务端是绑定的唯一作者**：模型提供的 routeId 只是"选择"，
    revision / objectId / contentHash 全部由服务端从库里读取。
    """
    from ..db import models as M

    def latest(table: Any) -> Any | None:
        return (
            db.query(table)
            .filter(table.session_id == session_id)
            .order_by(table.revision.desc())
            .first()
        )

    pm = latest(M.ProblemMapRow)
    if pm is None:
        raise BlueprintRejected(
            "BLUEPRINT_REQUIRES_PROBLEM_MAP",
            "本会话还没有已确认的问题地图：blueprint 必须绑定一个已确认的 ProblemMap 修订",
        )
    rm = latest(M.ResearchMapRow)
    if rm is None:
        raise BlueprintRejected(
            "BLUEPRINT_REQUIRES_RESEARCH_MAP",
            "本会话还没有已确认的研究地图：blueprint 必须绑定一个已确认的 ResearchMap 修订",
        )
    arena_row = latest(M.RouteArenaRow)
    if arena_row is None:
        raise BlueprintRejected(
            "BLUEPRINT_REQUIRES_ROUTE_ARENA",
            "本会话还没有已确认的路线竞技场：请先确认 route_arena，再从中选定路线",
        )

    arena_content = dict(arena_row.content or {})
    routes = [r for r in arena_content.get("routes") or [] if isinstance(r, dict)]
    route = next((r for r in routes if r.get("id") == content["routeId"]), None)
    if route is None:
        raise BlueprintRejected(
            "BLUEPRINT_ROUTE_UNKNOWN",
            f"routeId={content['routeId']!r} 不在已确认竞技场（revision={arena_row.revision}）中",
        )
    if route.get("status") == "REJECTED":
        rejection = route.get("rejection") or {}
        raise BlueprintRejected(
            "BLUEPRINT_ROUTE_REJECTED",
            f"routeId={content['routeId']!r} 在竞技场中已被拒"
            f"（{rejection.get('code', '?')}：{rejection.get('message', '')}）——被拒路线不能进入 blueprint",
        )

    out = dict(content)
    binds = {
        "problemMap": {"revision": int(pm.revision), "objectId": str(pm.id), "contentHash": pm.content_hash or ""},
        "researchMap": {"revision": int(rm.revision), "objectId": str(rm.id), "contentHash": rm.content_hash or ""},
        "routeArena": {
            "revision": int(arena_row.revision),
            "objectId": str(arena_row.id),
            "contentHash": arena_row.content_hash or "",
            "routeId": content["routeId"],
        },
    }
    out["binds"] = binds
    out["route"] = _snapshot_route(route)

    # 证据解析：evidenceCoverage 只收**可解析**的引用；缺口如实进 gaps。
    resolved = _resolvable_ids(db, session_id)
    refs = [x for x in (route.get("evidenceRefs") or []) if x]
    coverage = [x for x in refs if x in resolved]
    gaps = [
        *(u.get("text", "") for u in (route.get("unknowns") or []) if u.get("text")),
        *(f"证据不可解析: {x}" for x in refs if x not in resolved),
    ]
    out["modelSpec"] = {
        "label": route.get("label", ""),
        "kind": route.get("kind", ""),
        "method": route.get("method", ""),
        "variables": list(route.get("variables") or []),
        "hypotheses": list(route.get("hypotheses") or []),
        "data": list(route.get("data") or []),
        "metrics": list(route.get("metrics") or []),
        "falsification": list(route.get("falsification") or []),
        "evidenceCoverage": coverage,
        "gaps": gaps,
    }
    out["lineage"] = [
        {"kind": "problem_map", "revision": binds["problemMap"]["revision"], "contentHash": binds["problemMap"]["contentHash"]},
        {"kind": "research_map", "revision": binds["researchMap"]["revision"], "contentHash": binds["researchMap"]["contentHash"]},
        {
            "kind": "route_arena",
            "revision": binds["routeArena"]["revision"],
            "contentHash": binds["routeArena"]["contentHash"],
            "routeId": content["routeId"],
        },
    ]
    if content["routeId"] not in coverage and refs:
        out.setdefault("insufficiencies", []).append(
            {"section": "route.evidenceRefs", "reason": "SELECTED_ROUTE_EVIDENCE_UNRESOLVED", "role": "选定路线的证据覆盖"}
        )
    return BlueprintContent(content=out, insufficiencies=list(out.get("insufficiencies") or []))


def _snapshot_route(route: dict[str, Any]) -> dict[str, Any]:
    """选定路线的深拷贝（剥离服务端运行时字段 status/rejection —— 由 binds 承载事实）。"""
    import copy

    snap = copy.deepcopy(route)
    snap.pop("status", None)
    snap.pop("rejection", None)
    return snap


def _resolvable_ids(db: Any, session_id: Any) -> set[str]:
    try:
        from ..evidence.resolvable import resolvable_evidence_ids

        return resolvable_evidence_ids(db, session_id=session_id)
    except Exception:  # noqa: BLE001 - 证据层不可用时如实返回空集（coverage 为空，gaps 记缺口）
        return set()


__all__ = [
    "CONCLUSION_TELLS",
    "BlueprintContent",
    "BlueprintRejected",
    "MODEL_TOP_LEVEL_KEYS",
    "SCHEMA_VERSION",
    "bind_context",
    "normalize",
]
