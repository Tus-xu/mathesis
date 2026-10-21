"""RV09 · ProblemMap —— 把"我理解你在问什么"写成**结构**，而不是一段散文。

为什么需要它
------------
对话里的研究问题天然是含糊的：范围没界、成功判据没说、假设没列。
如果只把用户原话原样存下来，后续所有轮次（建模路线、实验、claim）都会
在一个**无法证伪**的地基上叠加。ProblemMap 的职责就是把含糊显式化，
并让每一段的可信来源（用户说的 / 有来源的 / 模型推断的）可被看见。

八段最小结构（任务书要求 1）
--------------------------
`researchQuestion` / `scope` / `nonGoals` / `knowns` / `unknowns` /
`constraints` / `successCriteria` / `falsificationCriteria` /
`assumptions` / `risks` / `requiredEvidence`

两类"拒绝"必须区分（这是本模块最核心的设计决定）
------------------------------------------------
1. **结构不成立** ⇒ `ProposalRejected`。例如 `researchQuestion` 缺失、
   出现未知键、元素不是 `{text, origin}`。这类错误要改 prompt 或改模型。
2. **结构成立但信息不足** ⇒ **不拒绝**，而是把缺口登记进 `insufficiencies`
   并把相应段留空。例如"用户只说了'帮我看看这个数列'，没说成功标准"。
   此时服务端**不许**替用户编一个成功标准 —— 那是本产品最危险的失真。
   正确行为是保留缺口、请用户补充（见 `clarification_request()`）。

`UNKNOWN` 的地位
----------------
`unknowns` 的每个元素恒带 `status="UNKNOWN"`，且**永远不会**被任何下游
逻辑折叠成 `knowns`。把"不知道"改写成"已知"是这里唯一不可逆的错误。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0"

#: 顶层键的**完整**白名单。多一个键即拒（不做"宽容透传"）。
TOP_LEVEL_KEYS: tuple[str, ...] = (
    "schemaVersion",
    "researchQuestion",
    "scope",
    "nonGoals",
    "knowns",
    "unknowns",
    "constraints",
    "successCriteria",
    "falsificationCriteria",
    "assumptions",
    "risks",
    "requiredEvidence",
    # 模型可以自报"这一段我填不出来"，但**不能**自报"我推断出了一条结论"。
    "insufficiencies",
    # 模型若发现用户问题自相矛盾，可自报；同时服务端另有机械检测（见 detect_contradiction）。
    "contradictions",
)

#: 每段的语义角色，供投影/前端与文档共用（单一真源）。
SECTION_ROLES: dict[str, str] = {
    "researchQuestion": "研究问题（唯一）",
    "scope": "在范围内的事情",
    "nonGoals": "明确不做的事情",
    "knowns": "已确知（可来自用户或来源）",
    "unknowns": "未知（恒为 UNKNOWN，不得被下游折叠成 knowns）",
    "constraints": "约束（预算/工具/时限/合规）",
    "successCriteria": "成功判据（可被检验）",
    "falsificationCriteria": "证伪判据（什么结果会推翻本问题）",
    "assumptions": "前提假设（未经验证者 origin 必须为 INFERENCE）",
    "risks": "风险与缓解",
    "requiredEvidence": "需要什么证据才算回答",
}

#: 允许为空的段（结构必须存在，内容可空 + 必须登记缺口）。
OPTIONAL_SECTIONS: frozenset[str] = frozenset(
    {
        "scope",
        "nonGoals",
        "knowns",
        "unknowns",
        "constraints",
        "assumptions",
        "risks",
        "requiredEvidence",
    }
)

#: **不允许**为空的段 —— 它们是这个问题的语义核心。
REQUIRED_SECTIONS: tuple[str, ...] = (
    "researchQuestion",
    "successCriteria",
    "falsificationCriteria",
)

#: `researchQuestion` 的最短长度（字符）。低于此值不是"简短"，是"还没提问"。
MIN_QUESTION_CHARS = 6

RISK_SEVERITIES = ("LOW", "MEDIUM", "HIGH")
EVIDENCE_KINDS = ("LITERATURE", "DATASET", "DERIVATION", "NUMERICAL", "COUNTEREXAMPLE", "EXPERT")

#: 缺口原因码（机器可处理；同时也是"请用户补充"的文案依据）。
INSUFFICIENCY_EMPTY_SECTION = "SECTION_EMPTY"
INSUFFICIENCY_NO_ORIGIN = "NO_ORIGIN_EVIDENCE"
INSUFFICIENCY_SCOPE_UNBOUNDED = "SCOPE_WITHOUT_NON_GOALS"


class ProblemMapRejected(Exception):
    """结构不成立（可机器处理）。由上层转成 `ProposalRejected`。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Contradiction:
    """一处被机械检测到的自相矛盾。"""

    kind: str
    a: str
    b: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "a": self.a, "b": self.b, "detail": self.detail}


@dataclass
class ProblemMapContent:
    """规范化后的 ProblemMap 内容（可直接落 `ProblemMapRow.content`）。"""

    content: dict[str, Any]
    insufficiencies: list[dict[str, str]] = field(default_factory=list)
    contradictions: list[dict[str, str]] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.insufficiencies and not self.contradictions


# --------------------------------------------------------------------------
# 机械检测（**纯函数**，不依赖模型自述）
# --------------------------------------------------------------------------

#: 否定标记。用于把"同一谓词的肯定式与否定式"归到同一个 key 上。
_NEGATION_MARKERS = ("不", "无", "非", "未", "没有", "不能", "无法", "不是", "не", "no ", "not ", "never")
_CLAUSE_SPLIT = re.compile(r"[。；;！？!?\n]+")
#: 疑问/研究动词标记 —— 命中其一即认为"这是一个可研究的问题"。
#:
#: ⚠️ 这张表**刻意很宽**。RV09 的真实 E2E 实测踩过一次反向事故：
#: 只收疑问词（如何/是否/…）时，模型把信息充足的问题整理成
#: "为单校区 3 个快递点设计取件排队方案"这类**陈述式**研究问题，
#: 被判成 `PROBLEM_MAP_QUESTION_INSUFFICIENT` —— 于是系统对一个
#: **完全没问题**的问题反过来要求用户补充。假阳性在这里比假阴性更有害：
#: 它会让用户觉得系统听不懂人话。
_INTERROGATIVES = (
    # 疑问
    "如何", "怎么", "怎样", "是否", "能否", "为什么", "为何", "什么", "多少",
    "哪个", "哪", "吗", "?", "？",
    # 研究动词 / 目标
    "求", "证明", "验证", "探究", "分析", "比较", "设计", "优化", "最小", "最大",
    "最优", "推导", "建模", "估计", "判断", "确定", "存在", "成立", "唯一",
    "收敛", "发散", "边界", "条件", "误差", "精度", "效率", "方案", "模型",
)

#: 陈述式研究问题的**长度兜底**。
#:
#: 有些研究问题的表述里一个标记词都没有（例如纯描述性命题）。
#: 中文里 20 字以上的完整陈述几乎必然是"在问一件事"，而
#: "帮我看看这个数列。"（9 字）之类仍然会被拦。这是一个**有意的取舍**：
#: 宁可放过一句含糊的长句（用户下一轮会被澄清卡问住），
#: 也不要把一句合法的长陈述判成"你还没提问"。
DECLARATIVE_MIN_CHARS = 20


def _normalize_clause(clause: str) -> str:
    text = re.sub(r"[\s，,、（）()【】\[\]\"'“”‘’]+", "", clause).strip()
    for neg in _NEGATION_MARKERS:
        text = text.replace(neg, "")
    return text


def detect_contradiction(text: str) -> list[Contradiction]:
    """检测**机械可判**的自相矛盾。

    只做一种判定：把输入按标点切成子句，去掉否定词后若两条子句的"谓词骨架"
    相同、而**否定极性不同**，则判为矛盾。

    例：`这个数列收敛。这个数列不收敛。` ⇒ 骨架都是 `这个数列收敛`，
    极性一正一负 ⇒ `SELF_NEGATION`。

    刻意**不**做语义级矛盾推断：那需要模型，而模型判"用户自相矛盾"本身
    就可能误判。因此本函数只抓机械可判的一类，其余交给用户确认。
    """
    out: list[Contradiction] = []
    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(text or "") if c.strip()]
    seen: dict[str, tuple[bool, str]] = {}
    for clause in clauses:
        negative = any(neg in clause for neg in _NEGATION_MARKERS)
        key = _normalize_clause(clause)
        if len(key) < 4:
            continue
        prev = seen.get(key)
        if prev is None:
            seen[key] = (negative, clause)
            continue
        prev_negative, prev_clause = prev
        if prev_negative != negative:
            out.append(
                Contradiction(
                    kind="SELF_NEGATION",
                    a=prev_clause,
                    b=clause,
                    detail="同一谓词既被肯定又被否定",
                )
            )
    return out


def is_question_like(text: str) -> bool:
    """一段文字是否**看起来**是一个可研究的问题（而非一句寒暄/半个词）。

    命中疑问/研究动词标记，**或**长度达到 `DECLARATIVE_MIN_CHARS`
    （陈述式研究问题的兜底）。两者都说明"它在问一件事"。
    """
    stripped = (text or "").strip()
    if len(stripped) < MIN_QUESTION_CHARS:
        return False
    if any(marker in stripped for marker in _INTERROGATIVES):
        return True
    return len(stripped) >= DECLARATIVE_MIN_CHARS


def _overlap_pairs(a: list[dict], b: list[dict], *, kind: str) -> list[Contradiction]:
    """两个段之间逐字重复的元素 —— 说明作者自己都没分清它们。"""
    out: list[Contradiction] = []
    b_texts = {(e.get("text") or "").strip() for e in b if (e.get("text") or "").strip()}
    for item in a:
        t = (item.get("text") or "").strip()
        if t and t in b_texts:
            out.append(Contradiction(kind=kind, a=t, b=t, detail="同一句话同时出现在两个互斥段"))
    return out


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------


def _as_str(value: Any, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


def _norm_element(
    raw: Any, *, section: str, default_origin: str = "INFERENCE"
) -> dict[str, Any]:
    """把一条元素规范成 `{text, origin, ...}`。

    与 `proposal_handlers._norm_originated` 同口径：**缺失/非法 origin 一律降级
    INFERENCE**，绝不升级成 USER/SOURCE。`quote` 只在声称 USER 时保留，
    因为它是服务端裁决 USER 的逐字锚定物（见 `evidence/origin_grounding.py`）。
    """
    if isinstance(raw, str):
        return {"text": raw[:4000], "origin": default_origin}
    if not isinstance(raw, dict):
        raise ProblemMapRejected(
            "PROBLEM_MAP_ELEMENT_INVALID", f"{section} 的元素必须是对象或字符串"
        )
    origin = str(raw.get("origin", "")).upper()
    if origin not in ("USER", "SOURCE", "INFERENCE"):
        origin = default_origin
    item: dict[str, Any] = {"text": _as_str(raw.get("text") or raw.get("value") or raw.get("statement")), "origin": origin}
    if origin == "USER":
        quote = _as_str(raw.get("quote"), 2000).strip()
        if quote:
            item["quote"] = quote
    return item


def _norm_section(payload: dict, key: str) -> list[dict[str, Any]]:
    raw = payload.get(key)
    if raw is None:
        return []
    if isinstance(raw, dict):
        # 单元素容错：模型常把"只有一个元素"的段写成对象而不是数组。
        raw = [raw]
    if not isinstance(raw, list):
        raise ProblemMapRejected("PROBLEM_MAP_SECTION_NOT_LIST", f"{key} 必须是数组")
    return [_norm_element(x, section=key) for x in raw]


def _norm_risks(payload: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in payload.get("risks") or []:
        if isinstance(raw, str):
            out.append({"text": raw[:4000], "origin": "INFERENCE", "severity": "MEDIUM", "mitigation": ""})
            continue
        if not isinstance(raw, dict):
            raise ProblemMapRejected("PROBLEM_MAP_ELEMENT_INVALID", "risks 的元素必须是对象或字符串")
        base = _norm_element(raw, section="risks")
        severity = str(raw.get("severity", "MEDIUM")).upper()
        if severity not in RISK_SEVERITIES:
            # 未知级别不猜测，落到最保守的 MEDIUM 并保留原文供审计
            severity = "MEDIUM"
        base["severity"] = severity
        base["mitigation"] = _as_str(raw.get("mitigation") or raw.get("mitigation_text"), 1000)
        out.append(base)
    return out


def _norm_required_evidence(payload: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in payload.get("requiredEvidence") or []:
        if isinstance(raw, str):
            out.append({"text": raw[:4000], "origin": "INFERENCE", "kind": "LITERATURE", "evidenceRef": ""})
            continue
        if not isinstance(raw, dict):
            raise ProblemMapRejected(
                "PROBLEM_MAP_ELEMENT_INVALID", "requiredEvidence 的元素必须是对象或字符串"
            )
        base = _norm_element(raw, section="requiredEvidence")
        kind = str(raw.get("kind", "LITERATURE")).upper()
        if kind not in EVIDENCE_KINDS:
            kind = "LITERATURE"
        base["kind"] = kind
        base["evidenceRef"] = _as_str(raw.get("evidenceRef") or raw.get("evidence_ref"), 64)
        out.append(base)
    return out


def _norm_unknowns(payload: dict) -> list[dict[str, Any]]:
    out = _norm_section(payload, "unknowns")
    for item in out:
        # 硬钉死：unknowns 恒为 UNKNOWN。模型写 KNOWN 也不改判。
        item["status"] = "UNKNOWN"
    return out


def normalize(payload: dict) -> ProblemMapContent:
    """把 proposal payload 规范化成 ProblemMap 内容。

    只做**结构与词表**校验；"信息够不够"由 `assess()` 判定，两者刻意分开 ——
    混在一起会让"信息不足"被误报成"格式错误"。
    """
    if not isinstance(payload, dict):
        raise ProblemMapRejected("PROBLEM_MAP_NOT_OBJECT", "problem_map payload 必须是对象")

    unknown_keys = sorted(set(payload) - set(TOP_LEVEL_KEYS))
    if unknown_keys:
        raise ProblemMapRejected(
            "PROBLEM_MAP_UNKNOWN_KEY",
            f"payload 含未登记字段 {unknown_keys}；ProblemMap 的段是封闭词表",
        )

    question_raw = payload.get("researchQuestion")
    if isinstance(question_raw, list):
        question_raw = question_raw[0] if question_raw else None
    if question_raw is None:
        raise ProblemMapRejected("PROBLEM_MAP_NO_QUESTION", "problem_map 缺少 researchQuestion")
    question = _norm_element(question_raw, section="researchQuestion", default_origin="USER")

    contradictions_raw = payload.get("contradictions") or []
    if not isinstance(contradictions_raw, list):
        raise ProblemMapRejected("PROBLEM_MAP_ELEMENT_INVALID", "contradictions 必须是数组")

    content: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "researchQuestion": [question],
        "scope": _norm_section(payload, "scope"),
        "nonGoals": _norm_section(payload, "nonGoals"),
        "knowns": _norm_section(payload, "knowns"),
        "unknowns": _norm_unknowns(payload),
        "constraints": _norm_section(payload, "constraints"),
        "successCriteria": _norm_section(payload, "successCriteria"),
        "falsificationCriteria": _norm_section(payload, "falsificationCriteria"),
        "assumptions": _norm_section(payload, "assumptions"),
        "risks": _norm_risks(payload),
        "requiredEvidence": _norm_required_evidence(payload),
    }

    out = ProblemMapContent(content=content)
    out.contradictions = _collect_contradictions(content, payload, contradictions_raw)
    out.insufficiencies = _collect_insufficiencies(content)
    content["insufficiencies"] = out.insufficiencies
    content["contradictions"] = out.contradictions
    return out


def _collect_contradictions(
    content: dict[str, Any], payload: dict, model_declared: list
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    question_text = content["researchQuestion"][0]["text"]
    for c in detect_contradiction(question_text):
        out.append(c.as_dict())

    for c in _overlap_pairs(content["scope"], content["nonGoals"], kind="SCOPE_NONGOAL_OVERLAP"):
        out.append(c.as_dict())
    for c in _overlap_pairs(content["knowns"], content["unknowns"], kind="KNOWN_UNKNOWN_OVERLAP"):
        out.append(c.as_dict())

    # 模型自报的矛盾也照收（它见到的语义比机械检测多），但不据此判真假 ——
    # 只作为"需要用户确认"的输入。
    for raw in model_declared:
        if isinstance(raw, dict) and (raw.get("a") or raw.get("b")):
            out.append(
                {
                    "kind": str(raw.get("kind") or "MODEL_DECLARED"),
                    "a": _as_str(raw.get("a"), 500),
                    "b": _as_str(raw.get("b"), 500),
                    "detail": _as_str(raw.get("detail") or raw.get("reason"), 500),
                }
            )
    return out


def _collect_insufficiencies(content: dict[str, Any]) -> list[dict[str, str]]:
    """登记缺口。**不**修复、**不**补齐 —— 只如实记录哪一段是空的。"""
    out: list[dict[str, str]] = []
    for section in TOP_LEVEL_KEYS:
        if section in ("schemaVersion", "insufficiencies", "contradictions"):
            continue
        items = content.get(section) or []
        if not items:
            out.append(
                {
                    "section": section,
                    "reason": INSUFFICIENCY_EMPTY_SECTION,
                    "role": SECTION_ROLES.get(section, section),
                }
            )

    # 没有 non-goals 的范围等于没有边界。只在实际写了 scope 时才报这条，
    # 避免与 SECTION_EMPTY 重复登记同一个缺口。
    if content["scope"] and not content["nonGoals"]:
        out.append(
            {
                "section": "nonGoals",
                "reason": INSUFFICIENCY_SCOPE_UNBOUNDED,
                "role": SECTION_ROLES["nonGoals"],
            }
        )

    # 假设里凡未标 INFERENCE 的都不算数：未经验证的假设冒充 USER/SOURCE
    # 会让"这是用户说的"变成一句假话。
    for item in content["assumptions"]:
        if item.get("origin") not in ("USER", "SOURCE", "INFERENCE"):
            out.append(
                {
                    "section": "assumptions",
                    "reason": INSUFFICIENCY_NO_ORIGIN,
                    "role": SECTION_ROLES["assumptions"],
                }
            )
    return out


def validate(content: dict[str, Any]) -> None:
    """语义硬门：**语义核心为空即拒**（区别于 `assess()` 的信息不足）。"""
    question = (content.get("researchQuestion") or [{}])[0].get("text", "")
    if not is_question_like(question):
        raise ProblemMapRejected(
            "PROBLEM_MAP_QUESTION_INSUFFICIENT",
            f"researchQuestion 不像一个可研究的问题（长度 {len(question.strip())}，"
            f"缺少疑问/求解标记）",
        )
    for section in REQUIRED_SECTIONS:
        if section == "researchQuestion":
            continue
        if not (content.get(section) or []):
            raise ProblemMapRejected(
                "PROBLEM_MAP_MISSING_CORE_SECTION",
                f"{section} 是语义核心，不允许为空（信息不足时应先请用户补充）",
            )


def clarification_request(content: dict[str, Any]) -> str:
    """把缺口写成**给用户看的**补充请求（确定性文案，不经模型）。"""
    gaps = content.get("insufficiencies") or []
    if not gaps:
        return ""
    lines = ["已按你的描述整理出问题地图，但有几处我不打算替你猜："]
    for gap in gaps:
        lines.append(f"- 缺少「{gap.get('role') or gap.get('section')}」")
    lines.append("")
    lines.append("请补充上述内容；在你确认之前，地图里这些段落保持为空。")
    return "\n".join(lines)


def apply_corrections(
    content: dict[str, Any], *, accept: list[str] | None, reject: list[str] | None,
    edits: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """逐项 accept / reject / edit（前端 living map 的写入口）。

    只允许对**元素**做操作，不允许整段替换 —— 整段替换会绕过逐项确认，
    等价于"一次性接受一个未审阅的提案"。

    元素寻址格式：`<section>#<index>`，例如 `constraints#1`。
    `edit` 的 `value` 只允许改 `text`（以及 risks 的 `severity`/`mitigation`），
    **不允许**改 `origin` —— 来源由服务端裁决，不由编辑者声明。
    """
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in content.items()}
    accepted_count = 0
    rejected_sections = list(reject or [])

    for ref in accept or []:
        section, _idx = _parse_ref(ref)
        if section not in out:
            raise ProblemMapRejected("PROBLEM_MAP_REF_UNKNOWN", f"未知段: {section!r}")
        accepted_count += 1

    for ref in rejected_sections:
        section, idx = _parse_ref(ref)
        items = out.get(section)
        if not isinstance(items, list) or idx >= len(items):
            raise ProblemMapRejected("PROBLEM_MAP_REF_UNKNOWN", f"元素不存在: {ref!r}")
        out[section] = items[:idx] + items[idx + 1 :]

    for edit in edits or []:
        ref = str(edit.get("ref") or "")
        section, idx = _parse_ref(ref)
        items = out.get(section)
        if not isinstance(items, list) or idx >= len(items):
            raise ProblemMapRejected("PROBLEM_MAP_REF_UNKNOWN", f"元素不存在: {ref!r}")
        value = edit.get("value")
        if not isinstance(value, dict):
            raise ProblemMapRejected("PROBLEM_MAP_EDIT_INVALID", f"{ref} 的 value 必须是对象")
        forbidden = sorted(set(value) & {"origin", "quote"})
        if forbidden:
            raise ProblemMapRejected(
                "PROBLEM_MAP_EDIT_FORBIDDEN_FIELD",
                f"{ref} 不允许改 {forbidden}：来源由服务端裁决，不由编辑者声明",
            )
        item = dict(items[idx])
        if "text" in value:
            item["text"] = _as_str(value["text"])
        for extra in ("severity", "mitigation", "kind", "evidenceRef"):
            if extra in value:
                item[extra] = value[extra]
        out[section] = items[:idx] + [item] + items[idx + 1 :]

    out["editSummary"] = {"accepted": accepted_count, "rejected": len(rejected_sections), "edited": len(edits or [])}
    out["insufficiencies"] = _collect_insufficiencies(out)
    return out


_REF_RE = re.compile(r"^([A-Za-z]+)#(\d+)$")


def _parse_ref(ref: str) -> tuple[str, int]:
    m = _REF_RE.match((ref or "").strip())
    if not m:
        raise ProblemMapRejected(
            "PROBLEM_MAP_REF_INVALID", f"元素引用必须是 `<section>#<index>`，收到 {ref!r}"
        )
    section, idx = m.group(1), int(m.group(2))
    if section not in TOP_LEVEL_KEYS:
        raise ProblemMapRejected("PROBLEM_MAP_REF_UNKNOWN", f"未知段: {section!r}")
    return section, idx


__all__ = [
    "DECLARATIVE_MIN_CHARS",
    "EVIDENCE_KINDS",
    "INSUFFICIENCY_EMPTY_SECTION",
    "INSUFFICIENCY_NO_ORIGIN",
    "INSUFFICIENCY_SCOPE_UNBOUNDED",
    "MIN_QUESTION_CHARS",
    "OPTIONAL_SECTIONS",
    "ProblemMapContent",
    "ProblemMapRejected",
    "REQUIRED_SECTIONS",
    "RISK_SEVERITIES",
    "SCHEMA_VERSION",
    "SECTION_ROLES",
    "TOP_LEVEL_KEYS",
    "apply_corrections",
    "clarification_request",
    "detect_contradiction",
    "is_question_like",
    "normalize",
    "validate",
]
