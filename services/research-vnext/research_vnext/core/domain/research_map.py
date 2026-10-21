"""RV09 · ResearchMap —— 2–4 个**可区分**的调研/验证视角，不是一张清单。

为什么不能是 checklist
---------------------
"列 8 个步骤"和"提出 2 个彼此不同意对方的验证视角"在 UI 上长得很像，
在科研上完全不同：

- 清单：步骤之间**没有张力**，做完就"完成"了；
- 地图：视角之间**互相挑战**，每个视角有自己的**停止条件**
  （"到什么程度我就不再往下走了"）和**不确定性**（"我最可能错在哪"）。

因此本模块的硬门是四道：

1. **数量** 2–4。1 个视角不是地图；5 个以上不是地图，是清单。
2. **可区分**：`lens` 两两不同，且 `kind` 至少有 2 个不同取值 ——
   否则"三个视角"就是同一个视角抄了三遍。
3. **有交付物 + 有停止条件**：没有停止条件的视角可以无限延伸，
   那正是"研究永远做不完"的成因。
4. **有依赖**：视角之间必须存在 `requires` 关系（允许单向链，不允许环）。
   零依赖的多个视角仍然是清单。

图由**服务端**从 perspectives 推导（`derive_graph`），模型不自己画图。
这样"边指向不存在的节点"在结构上不可能发生 —— 而不是靠事后校验去拦。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"

MIN_PERSPECTIVES = 2
MAX_PERSPECTIVES = 4

#: 视角的**认识论类型**。至少要有两种不同取值，否则不叫"多视角"。
PERSPECTIVE_KINDS: tuple[str, ...] = (
    "THEORETICAL",  # 解析推导 / 证明
    "COMPUTATIONAL",  # 计算 / 数值实验
    "EMPIRICAL",  # 数据/实证
    "LITERATURE",  # 文献对照
    "GEOMETRIC",  # 几何/可视化
    "ALGEBRAIC",  # 代数/符号
    "CONSTRUCTION",  # 构造反例或构造解
)

DELIVERABLE_KINDS: tuple[str, ...] = (
    "PROOF",
    "MODEL",
    "DATASET",
    "COUNTEREXAMPLE",
    "BOUND",
    "ALGORITHM",
    "REVIEW",
)

#: 顶层键白名单。
TOP_LEVEL_KEYS: tuple[str, ...] = (
    "schemaVersion",
    "perspectives",
    "rationale",
    "insufficiencies",
)

#: 出现这些键 ⇒ 这是一张伪装的清单。
CHECKLIST_TELLS = frozenset(
    {"steps", "checklist", "todos", "todo", "tasks", "procedure", "workflow", "plan_steps"}
)

#: 元素级引用前缀（依赖可以指向问题地图的一段或一条证据）。
REF_PREFIXES = ("evidence:", "problemMap:")

_DEP_RE = re.compile(r"^(p[0-9]+|evidence:.+|problemMap:[A-Za-z]+)$")
#: 指向**本图内**视角的引用。
#:
#: ⚠️ 必须用正则而不是 `str.startswith("p")` —— `"problemMap:successCriteria"`
#: 也以 `p` 开头。用 startswith 会把外部引用误判成悬空视角（实测踩过）。
_PERSPECTIVE_REF_RE = re.compile(r"^p[0-9]+$")


def is_perspective_ref(target: str) -> bool:
    return bool(_PERSPECTIVE_REF_RE.match(target or ""))


class ResearchMapRejected(Exception):
    """结构不成立（可机器处理）。由上层转成 `ProposalRejected`。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Perspective:
    id: str
    lens: str
    kind: str
    question: str
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    stop_conditions: list[dict[str, Any]] = field(default_factory=list)
    uncertainties: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lens": self.lens,
            "kind": self.kind,
            "question": self.question,
            "dependencies": self.dependencies,
            "deliverables": self.deliverables,
            "stopConditions": self.stop_conditions,
            "uncertainties": self.uncertainties,
            "evidenceRefs": self.evidence_refs,
        }


@dataclass
class ResearchMapContent:
    content: dict[str, Any]
    insufficiencies: list[dict[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------


def _as_str(value: Any, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


def _norm_list(raw: Any, *, field_name: str, default_origin: str = "INFERENCE") -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ResearchMapRejected("RESEARCH_MAP_SECTION_NOT_LIST", f"{field_name} 必须是数组")
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"text": item[:4000], "origin": default_origin})
            continue
        if not isinstance(item, dict):
            raise ResearchMapRejected(
                "RESEARCH_MAP_ELEMENT_INVALID", f"{field_name} 的元素必须是对象或字符串"
            )
        origin = str(item.get("origin", "")).upper()
        if origin not in ("USER", "SOURCE", "INFERENCE"):
            origin = default_origin
        # **去掉下游不需要的键**：只留白名单，避免任意键透传到前端。
        entry: dict[str, Any] = {"text": _as_str(item.get("text") or item.get("value")), "origin": origin}
        if origin == "USER":
            quote = _as_str(item.get("quote"), 2000).strip()
            if quote:
                entry["quote"] = quote
        out.append(entry)
    return out


def _norm_dependencies(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ResearchMapRejected("RESEARCH_MAP_SECTION_NOT_LIST", "dependencies 必须是数组")
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append({"on": item.strip(), "text": "", "origin": "INFERENCE"})
            continue
        if not isinstance(item, dict):
            raise ResearchMapRejected(
                "RESEARCH_MAP_ELEMENT_INVALID", "dependencies 的元素必须是对象或字符串"
            )
        target = _as_str(item.get("on") or item.get("depends_on") or item.get("ref"), 256).strip()
        origin = str(item.get("origin", "")).upper()
        if origin not in ("USER", "SOURCE", "INFERENCE"):
            origin = "INFERENCE"
        out.append({"on": target, "text": _as_str(item.get("text"), 1000), "origin": origin})
    return out


def _norm_deliverables(raw: Any) -> list[dict[str, Any]]:
    out = _norm_list(raw, field_name="deliverables")
    raw_list = raw if isinstance(raw, list) else ([raw] if isinstance(raw, str) else [])
    for idx, item in enumerate(out):
        kind = "MODEL"
        if idx < len(raw_list) and isinstance(raw_list[idx], dict):
            candidate = str(raw_list[idx].get("kind", "")).upper()
            if candidate in DELIVERABLE_KINDS:
                kind = candidate
        item["kind"] = kind
    return out


def normalize(payload: dict) -> ResearchMapContent:
    """把 proposal payload 规范化成 ResearchMap 内容并执行全部硬门。"""
    if not isinstance(payload, dict):
        raise ResearchMapRejected("RESEARCH_MAP_NOT_OBJECT", "research_map payload 必须是对象")

    tells = sorted(set(payload) & CHECKLIST_TELLS)
    if tells:
        raise ResearchMapRejected(
            "RESEARCH_MAP_IS_CHECKLIST",
            f"payload 含 {tells}：这是一张步骤清单，不是研究地图。"
            "多视角地图要给出 perspectives（各自的 lens/交付物/停止条件/不确定性）",
        )

    raw_perspectives = payload.get("perspectives")
    if not isinstance(raw_perspectives, list) or not raw_perspectives:
        # 优先报"没有视角"而不是"有未知键"：模型给 `{nodes, edges}` 时，
        # 它真正的问题是**用错了形状**，而不是多写了两个键。
        extra = sorted(set(payload) - set(TOP_LEVEL_KEYS))
        suffix = f"（payload 还含未登记字段 {extra}）" if extra else ""
        raise ResearchMapRejected(
            "RESEARCH_MAP_NO_PERSPECTIVES",
            f"research_map 必须给出 perspectives（2–4 个可区分的调研/验证视角）{suffix}",
        )

    unknown_keys = sorted(set(payload) - set(TOP_LEVEL_KEYS))
    if unknown_keys:
        raise ResearchMapRejected(
            "RESEARCH_MAP_UNKNOWN_KEY", f"payload 含未登记字段 {unknown_keys}"
        )
    if not (MIN_PERSPECTIVES <= len(raw_perspectives) <= MAX_PERSPECTIVES):
        raise ResearchMapRejected(
            "RESEARCH_MAP_PERSPECTIVE_COUNT",
            f"视角数量必须为 {MIN_PERSPECTIVES}–{MAX_PERSPECTIVES}，实际 {len(raw_perspectives)}："
            "1 个视角不构成地图，5 个以上是清单",
        )

    perspectives = [
        _norm_perspective(raw, index=i) for i, raw in enumerate(raw_perspectives)
    ]

    _check_distinguishable(perspectives)
    _check_dependencies(perspectives)

    content: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "rationale": _as_str(payload.get("rationale"), 2000),
        "perspectives": [p.as_dict() for p in perspectives],
    }
    content.update(derive_graph(perspectives))

    insufficiencies: list[dict[str, str]] = []
    for p in perspectives:
        if not p.uncertainties:
            insufficiencies.append(
                {
                    "section": f"perspectives[{p.id}].uncertainties",
                    "reason": "UNCERTAINTIES_EMPTY",
                    "role": f"{p.lens} 的不确定性",
                }
            )
    content["insufficiencies"] = insufficiencies
    return ResearchMapContent(content=content, insufficiencies=insufficiencies)


def _norm_perspective(raw: Any, *, index: int) -> Perspective:
    if not isinstance(raw, dict):
        raise ResearchMapRejected("RESEARCH_MAP_ELEMENT_INVALID", "perspectives 的元素必须是对象")
    pid = _as_str(raw.get("id"), 32).strip() or f"p{index + 1}"
    lens = _as_str(raw.get("lens") or raw.get("name") or raw.get("title"), 128).strip()
    if not lens:
        raise ResearchMapRejected(
            "RESEARCH_MAP_PERSPECTIVE_NO_LENS", f"{pid} 缺少 lens（这个视角的立场/取径）"
        )
    kind = str(raw.get("kind", "")).upper()
    if kind not in PERSPECTIVE_KINDS:
        raise ResearchMapRejected(
            "RESEARCH_MAP_PERSPECTIVE_KIND_UNKNOWN",
            f"{pid} 的 kind={kind!r} 不在 {list(PERSPECTIVE_KINDS)}",
        )

    deliverables = _norm_deliverables(raw.get("deliverables"))
    stop = _norm_list(raw.get("stopConditions") or raw.get("stop_conditions"), field_name="stopConditions")
    if not deliverables:
        raise ResearchMapRejected(
            "RESEARCH_MAP_PERSPECTIVE_NO_DELIVERABLE",
            f"{pid} 没有交付物：没有交付物的视角无法判断是否推进",
        )
    if not stop:
        raise ResearchMapRejected(
            "RESEARCH_MAP_PERSPECTIVE_NO_STOP_CONDITION",
            f"{pid} 没有停止条件：没有停止条件的视角可以无限延伸，那正是清单的形态",
        )

    refs_raw = raw.get("evidenceRefs") or raw.get("evidence_refs") or []
    if isinstance(refs_raw, str):
        refs_raw = [refs_raw]
    if not isinstance(refs_raw, list):
        raise ResearchMapRejected(
            "RESEARCH_MAP_SECTION_NOT_LIST", "evidenceRefs 必须是数组"
        )

    return Perspective(
        id=pid,
        lens=lens,
        kind=kind,
        question=_as_str(raw.get("question"), 2000),
        dependencies=_norm_dependencies(raw.get("dependencies")),
        deliverables=deliverables,
        stop_conditions=stop,
        uncertainties=_norm_list(raw.get("uncertainties"), field_name="uncertainties"),
        evidence_refs=[_as_str(x, 64) for x in refs_raw],
    )


def _check_distinguishable(perspectives: list[Perspective]) -> None:
    """lens 两两不同，且 kind 至少两种 —— 否则是同一视角抄多遍。"""
    seen: dict[str, str] = {}
    for p in perspectives:
        key = re.sub(r"[\s·\-—_/（）()【】]+", "", p.lens).lower()
        if key in seen:
            raise ResearchMapRejected(
                "RESEARCH_MAP_PERSPECTIVES_NOT_DISTINGUISHABLE",
                f"{p.id} 与 {seen[key]} 的 lens 规范化后相同（{p.lens!r}）："
                "请给出真正不同的取径，而不是同一个视角换名字",
            )
        seen[key] = p.id

    kinds = {p.kind for p in perspectives}
    if len(kinds) < 2:
        raise ResearchMapRejected(
            "RESEARCH_MAP_SINGLE_LENS",
            f"全部视角的 kind 都是 {sorted(kinds)[0]}：只有一种认识论类型的多个视角仍是清单",
        )

    # 停止条件全同 ⇒ 一眼可见的清单特征（复制粘贴的"做完为止"）。
    stop_keys = {
        tuple(sorted((s.get("text") or "").strip() for s in p.stop_conditions))
        for p in perspectives
    }
    if len(perspectives) >= 3 and len(stop_keys) == 1:
        raise ResearchMapRejected(
            "RESEARCH_MAP_CHECKLIST_SHAPED",
            "所有视角的停止条件完全相同：这是复制粘贴的清单，不是各自独立的验证视角",
        )


def _check_dependencies(perspectives: list[Perspective]) -> None:
    """依赖必须指向存在的目标，且依赖图无环。"""
    ids = {p.id for p in perspectives}
    graph: dict[str, list[str]] = {p.id: [] for p in perspectives}
    for p in perspectives:
        for dep in p.dependencies:
            target = dep.get("on") or ""
            if not _DEP_RE.match(target):
                raise ResearchMapRejected(
                    "RESEARCH_MAP_DEPENDENCY_INVALID",
                    f"{p.id} 的依赖 {target!r} 非法：必须是 pN、evidence:<id> 或 problemMap:<section>",
                )
            if is_perspective_ref(target) and target not in ids:
                raise ResearchMapRejected(
                    "RESEARCH_MAP_DANGLING_DEPENDENCY",
                    f"{p.id} 依赖 {target!r}，但地图里没有这个视角",
                )
            if not is_perspective_ref(target):
                # 外部引用（证据 / 问题地图段）不参与环检测
                continue
            if target == p.id:
                raise ResearchMapRejected(
                    "RESEARCH_MAP_SELF_DEPENDENCY", f"{p.id} 依赖自己"
                )
            graph[p.id].append(target)

    if not any(graph.values()):
        raise ResearchMapRejected(
            "RESEARCH_MAP_NO_DEPENDENCY",
            "全部视角互不依赖：互不相干的多个视角仍然是清单。"
            "至少要让一个视角声明它需要另一个视角的产出",
        )

    _assert_acyclic(graph)


def _assert_acyclic(graph: dict[str, list[str]]) -> None:
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for nxt in graph.get(node, []):
            if color.get(nxt) == GREY:
                cycle = " -> ".join(stack + [nxt])
                raise ResearchMapRejected(
                    "RESEARCH_MAP_DEPENDENCY_CYCLE", f"依赖成环：{cycle}"
                )
            if color.get(nxt) == WHITE:
                visit(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            visit(node, [])


# --------------------------------------------------------------------------
# 图由服务端推导（模型不画图）
# --------------------------------------------------------------------------


def derive_graph(perspectives: list[Perspective]) -> dict[str, list[dict[str, str]]]:
    """从 perspectives 推导 `nodes` / `edges`。

    **为什么由服务端推导**：让模型自己画图就必然要处理"边指向不存在的节点"。
    从结构化的 perspectives 推导，悬空边在结构上不可能出现，
    前端画布也不用再实现一套校验。

    节点类型复用既有的 `MAP_NODE_TYPES` 词表（method / claim / risk），
    因此前端按节点类型分色即可，不需要新增契约。
    """
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []

    for p in perspectives:
        nodes.append({"id": p.id, "type": "method", "label": p.lens, "origin": "INFERENCE"})
        for i, d in enumerate(p.deliverables, start=1):
            did = f"{p.id}.d{i}"
            nodes.append(
                {"id": did, "type": "claim", "label": d.get("text", ""), "origin": d.get("origin", "INFERENCE")}
            )
            # 交付物 derived_from 视角：方向与语义一致（产出由视角推出）。
            edges.append({"from": did, "to": p.id, "type": "derived_from"})
        for i, u in enumerate(p.uncertainties, start=1):
            uid = f"{p.id}.u{i}"
            nodes.append(
                {"id": uid, "type": "risk", "label": u.get("text", ""), "origin": u.get("origin", "INFERENCE")}
            )
            edges.append({"from": uid, "to": p.id, "type": "challenges"})
        for dep in p.dependencies:
            target = dep.get("on") or ""
            if is_perspective_ref(target):
                edges.append({"from": p.id, "to": target, "type": "requires"})

    return {"nodes": nodes, "edges": edges}


def refresh_graph(content: dict[str, Any]) -> dict[str, Any]:
    """在 `origin` 被服务端裁决之后**重建** nodes/edges。

    顺序很重要：`normalize()` 先推导一次图（为了让结构错误尽早暴露），
    但那时元素的 `origin` 还是模型自述值。若不复建，
    被降级为 `INFERENCE` 的交付物仍会在图节点里显示"来源"徽标 ——
    也就是说 `ground_element_origin` 的努力会被一层推导绕过去。
    """
    raw = content.get("perspectives") or []
    perspectives = [
        Perspective(
            id=_as_str(p.get("id"), 32),
            lens=_as_str(p.get("lens"), 128),
            kind=_as_str(p.get("kind"), 32),
            question=_as_str(p.get("question"), 2000),
            dependencies=list(p.get("dependencies") or []),
            deliverables=list(p.get("deliverables") or []),
            stop_conditions=list(p.get("stopConditions") or []),
            uncertainties=list(p.get("uncertainties") or []),
            evidence_refs=list(p.get("evidenceRefs") or []),
        )
        for p in raw
        if isinstance(p, dict)
    ]
    out = dict(content)
    out.update(derive_graph(perspectives))
    return out


def dependency_order(perspectives: list[Perspective]) -> list[str]:
    """拓扑序（稳定）：先做被依赖的视角。前端据此排布，而不是随机顺序。"""
    graph = {
        p.id: [d["on"] for d in p.dependencies if is_perspective_ref(d.get("on") or "")]
        for p in perspectives
    }
    visited: set[str] = set()
    order: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        order.append(node)

    for p in perspectives:
        visit(p.id)
    return order


__all__ = [
    "CHECKLIST_TELLS",
    "DELIVERABLE_KINDS",
    "MAX_PERSPECTIVES",
    "MIN_PERSPECTIVES",
    "PERSPECTIVE_KINDS",
    "Perspective",
    "ResearchMapContent",
    "ResearchMapRejected",
    "SCHEMA_VERSION",
    "TOP_LEVEL_KEYS",
    "dependency_order",
    "derive_graph",
    "is_perspective_ref",
    "normalize",
    "refresh_graph",
]
