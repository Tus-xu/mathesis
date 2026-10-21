"""Typed proposal —— **模型输出的唯一合法形状**。

铁律（ADR-RV-003 不变量 2）：LLM 输出**永远不能**直接改变正式领域状态。
模型只能产出本模块定义的 proposal；落正式状态必须经 PendingAction →
用户 confirm → 复检 → mutation。

因此这里的校验是**硬门**：
- 非法 JSON ⇒ 拒绝（`ProposalSchemaError`）；
- 未知 `kind` ⇒ 拒绝（`additionalProperties` 风格的白名单，不做"宽容透传"）；
- payload 与原语 schema 不符 ⇒ 拒绝。
绝不允许自由 HTML / CSS / 组件名通过。
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = "1.0.0"

ProposalKind = Literal[
    "answer",
    "intake",
    #: RV09 正式形状：八段最小结构（见 `domain/problem_map.py`）。
    "problem_map",
    #: RV09 正式形状：2–4 个可区分视角 + 依赖 + 交付物 + 停止条件（见 `domain/research_map.py`）。
    "research_map",
    #: 历史别名。`map` 曾是"typed nodes + edges"的草图；RV09 起由 `research_map` 取代。
    #: 保留它是为了不让已入库的历史 proposal 失效，新提示词不再引导模型使用它。
    "map",
    #: RV10：2–3 条实质不同的建模路线 + 服务端 rubric（无总分）。
    "route_arena",
    #: RV10：研究蓝图——绑定 ProblemMap/ResearchMap/RouteArena 修订 + 选定路线
    #: + 实验计划/证据要求/验证门/预算截止/停止条件。
    "research_blueprint",
    "blueprint",
    "experiment_plan",
    "claim",
    "paper_patch",
    "review",
]

#: RV09 起新提示词引导使用的 kind（其余为历史兼容）。
CURRENT_KINDS: frozenset[str] = frozenset(
    {"answer", "problem_map", "research_map", "route_arena", "research_blueprint"}
)
#: 仅历史兼容的 kind：仍可解析，但服务端标记 `legacyKind=true` 供审计。
LEGACY_KINDS: frozenset[str] = frozenset({"intake", "map"})

#: 允许出现在 payload 里的原语类型（**只有**这些）。
Primitive = str | int | float | bool | None

#: 输出里一旦出现这些键，说明模型在尝试输出前端代码/样式 —— 直接拒绝。
FORBIDDEN_KEYS = frozenset(
    {"html", "css", "style", "class", "classname", "component", "vue", "jsx", "script", "svg"}
)


class ProposalSchemaError(Exception):
    """模型输出不是合法 proposal。**必须**被上层转成诚实降级，不得吞掉。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProposalKind
    text: str = Field(min_length=0, max_length=8000)
    #: 与 `kind` 对应的结构化内容。**只允许原语/列表/字典**，由 `_check_primitives` 强制。
    payload: dict[str, Any] = Field(default_factory=dict)
    #: 模型自评的证据引用（必须指向真实 evidence id；越界由上层校验）。
    evidence_refs: list[str] = Field(default_factory=list)
    #: 模型认为是否需要人工确认才可落正式状态。
    requires_confirmation: bool = True


def _check_primitives(value: Any, path: str = "payload") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ProposalSchemaError("PROPOSAL_INVALID_PAYLOAD", f"{path} 的键必须是字符串")
            if k.strip().lower() in FORBIDDEN_KEYS:
                raise ProposalSchemaError(
                    "PROPOSAL_FORBIDDEN_KEY",
                    f"{path}.{k} 属于被禁止的输出类别（HTML/CSS/组件名）",
                )
            _check_primitives(v, f"{path}.{k}")
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _check_primitives(v, f"{path}[{i}]")
        return
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    raise ProposalSchemaError(
        "PROPOSAL_INVALID_PAYLOAD",
        f"{path} 含非原语类型 {type(value).__name__}（只允许原语/数组/对象）",
    )


def _strip_code_fence(raw: str) -> str:
    """剥离```围栏。

    这**不是**宽容：模型偶尔会用围栏包 JSON，而"因为多了三引号就整轮失败"
    会让降级率虚高。剥掉围栏后仍然**必须**通过 schema 校验。
    """
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_proposal(raw: str) -> Proposal:
    """把模型原始输出解析成 `Proposal`；任何不合法都抛 `ProposalSchemaError`。"""
    if raw is None or not raw.strip():
        raise ProposalSchemaError("PROPOSAL_EMPTY", "模型返回空内容")

    text = _strip_code_fence(raw)

    # 容错：截取第一个平衡的 JSON 对象（模型可能在前后加一句自然语言）。
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ProposalSchemaError("PROPOSAL_NOT_JSON", "模型输出不含 JSON 对象")
    candidate = text[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ProposalSchemaError("PROPOSAL_NOT_JSON", f"模型输出不是合法 JSON: {e}") from e

    if not isinstance(data, dict):
        raise ProposalSchemaError("PROPOSAL_NOT_OBJECT", "模型输出的 JSON 根必须是对象")

    try:
        proposal = Proposal.model_validate(data)
    except ValidationError as e:
        # 把 pydantic 的错误压缩成一行，既能机器处理也不泄露内部结构
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ())) or "<root>"
        raise ProposalSchemaError(
            "PROPOSAL_SCHEMA_INVALID",
            f"proposal 不符合契约：{loc}: {first.get('msg', 'invalid')}",
        ) from e

    _check_primitives(proposal.payload)
    return proposal


def proposal_digest(proposal: Proposal) -> str:
    """proposal 的规范摘要（用于幂等/审计）。"""
    import hashlib

    blob = json.dumps(
        proposal.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
