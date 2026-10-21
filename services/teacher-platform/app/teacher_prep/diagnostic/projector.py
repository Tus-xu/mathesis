"""R18 学前诊断 projector：已确认蓝图 + 上下文 → LLM → schema+grounding+范围 → proposal。

流程（DECISIONS.md §6：确定性工作流控权，LLM 只生成 proposal）：
1. `build_diagnostic_prompt`：蓝图摘要 + 上下文投影为有界 prompt；
2. `llm_generate(prompt)`：抽象生成器（可同步/异步；测试用确定性 fake）；
3. 解析 JSON → `DiagnosticProposal.model_validate`（schema 校验：必填字段/类型/
   HTML 禁令/枚举/长度/时长 5–10/题量 1–8/ref 形状）；
4. `check_grounding`：所有 source_ref ∈ 上下文可解析定位器集合，否则
   GroundingViolation（禁止发明引用）；
5. `check_diagnostic_scope`：所有 kp / knowledge_points ∈ 允许范围，否则
   ScopeViolation（**不足时不得偷偷扩大知识点范围**）；
6. 全过才返回 proposal；任何失败 → 显式异常，不成为事实（不写库）。

LLM 输出视为不可信输入：JSON + schema + grounding + 范围 四重闸门。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from app.teacher_prep.context.models import TextbookTeachingContext

from ..blueprint.projector import _strip_code_fence
from ..blueprint.schema import resolvable_refs_of
from .prompt import build_diagnostic_prompt
from .schema import (
    DiagnosticProposal,
    check_diagnostic_scope,
    collect_diagnostic_refs,
)

LlmGenerate = Callable[[str], str | Awaitable[str]]


class DiagnosticProjectionError(Exception):
    """投影失败基类（不成为事实）。"""


class DiagnosticProposalInvalid(DiagnosticProjectionError):
    """LLM 输出未通过 schema 校验（JSON/字段/HTML/枚举/形状/时长/题量）。"""

    def __init__(self, reason: str, issues: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.issues = issues or []


class GroundingViolation(DiagnosticProjectionError):
    """proposal 引用了上下文可解析集合之外的定位器（发明引用拒绝）。"""

    def __init__(self, refs: list[str]):
        super().__init__(f"diagnostic proposal 含不可解析/发明的 source_ref：{refs}")
        self.refs = refs


class ScopeViolation(DiagnosticProjectionError):
    """proposal 知识点超出已确认蓝图 + 上下文允许范围（不得偷偷扩大知识点范围）。"""

    def __init__(self, kps: list[str]):
        super().__init__(f"diagnostic proposal 知识点超出允许范围：{kps}")
        self.kps = kps


def parse_and_validate(
    raw: str, *, context_snapshot_hash: str | None = None, blueprint_id: str | None = None
) -> DiagnosticProposal:
    """JSON 解析 + schema 校验（LLM 输出不可信：四重闸门之一）。"""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise DiagnosticProposalInvalid(f"LLM 输出非合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise DiagnosticProposalInvalid(f"LLM 输出根节点必须是 JSON 对象，实际 {type(data).__name__}")
    try:
        proposal = DiagnosticProposal.model_validate(data)
    except ValidationError as exc:
        issues = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise DiagnosticProposalInvalid(f"diagnostic proposal schema 校验失败（{len(issues)} 项）", issues) from exc
    if context_snapshot_hash is not None and proposal.context_snapshot_hash != context_snapshot_hash:
        raise DiagnosticProposalInvalid(
            f"proposal 声明的上下文 hash 不匹配（proposal={proposal.context_snapshot_hash[:12]}… 上下文={context_snapshot_hash[:12]}…）"
        )
    if blueprint_id is not None and proposal.blueprint_id != blueprint_id:
        raise DiagnosticProposalInvalid(
            f"proposal 声明的 blueprint_id 不匹配（proposal={proposal.blueprint_id} 实际={blueprint_id}）"
        )
    return proposal


def check_grounding(proposal: DiagnosticProposal, context: TextbookTeachingContext) -> list[str]:
    """grounding 校验：所有 source_ref ∈ 上下文可解析集合；返回违规 ref 列表。"""
    allowed = resolvable_refs_of(context)
    return [ref for ref in collect_diagnostic_refs(proposal) if ref not in allowed]


async def project_diagnostic(
    *,
    blueprint: dict[str, Any],
    context: TextbookTeachingContext,
    llm_generate: LlmGenerate,
    max_context_tokens: int = 5000,
    require_grounding: bool = True,
    require_scope: bool = True,
) -> DiagnosticProposal:
    """已确认蓝图 + 上下文 → 有界 prompt → LLM → schema+grounding+范围 → proposal。

    `llm_generate` 返回模型原始文本（可能含围栏/多余文字）；本函数负责解析与校验。
    """
    prompt_result = build_diagnostic_prompt(blueprint=blueprint, context=context, max_tokens=max_context_tokens)
    result = llm_generate(prompt_result.prompt)
    if inspect.isawaitable(result):
        result = await result
    proposal = parse_and_validate(
        result,
        context_snapshot_hash=context.snapshot_hash,
        blueprint_id=str(blueprint.get("id") or blueprint.get("blueprint_id") or ""),
    )
    if require_grounding:
        violations = check_grounding(proposal, context)
        if violations:
            raise GroundingViolation(violations)
    if require_scope:
        selection = getattr(context, "selection", None)
        selection_kps = (selection.knowledge_points if selection else None) or []
        violations = check_diagnostic_scope(proposal, blueprint=blueprint, selection_kps=selection_kps)
        if violations:
            raise ScopeViolation(violations)
    return proposal


__all__ = [
    "DiagnosticProjectionError",
    "DiagnosticProposalInvalid",
    "GroundingViolation",
    "ScopeViolation",
    "check_grounding",
    "parse_and_validate",
    "project_diagnostic",
]
