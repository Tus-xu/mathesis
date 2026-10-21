"""R20 结构化 diff 引擎（确定性纯函数）。

对已确认蓝图（base）与修订提案内容（proposal）在 8 个维度上做位置化逐项比较，
输出可解释的 `StructuredDiff`：

  目标 objectives / 重点难点 key_difficult_points / 时间分配 time_allocation /
  活动 activities / 例题 examples / 分层支持 stratified_support /
  诊断响应 diagnostic_response / 证据 evidence

铁律：
- 纯函数：同 (base, proposal, profile) → 同 diff（确定性验收锚点）；
- op 语义：keep=无变化 / change=内容变化 / add=新增 / remove=移除；
- 证据绑定：proposal.evidence 中 `applies_to` 命中该维度的 finding id
  被确定性挂到对应 diff 条目（Profile 更新 → 可解释 diff 的锚点）；
- 教材引用零 `page_refs/pNN`：diff 只搬运 source_ref（SourceLocator）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DIFF_SCHEMA_VERSION = "1.0.0"

# 8 个维度（顺序固定 = 前端 ProposalDiff card 渲染顺序）
DIFF_DIMENSIONS: tuple[str, ...] = (
    "objectives",
    "key_difficult_points",
    "time_allocation",
    "activities",
    "examples",
    "stratified_support",
    "diagnostic_response",
    "evidence",
)

DiffOp = Literal["keep", "change", "add", "remove"]


class DiffEntry(BaseModel):
    """一条结构化 diff 条目（可解释、可渲染）。"""

    model_config = ConfigDict(extra="forbid")

    dimension: str
    op: DiffOp  # type: ignore[valid-type]
    path: str = Field(min_length=1, max_length=200)  # 如 objectives[0].text
    before: Any = None
    after: Any = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class DiffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    keep: int = 0
    change: int = 0
    add: int = 0
    remove: int = 0


class StructuredDiff(BaseModel):
    """base 已确认蓝图 → proposal 修订内容的确定性 diff。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = DIFF_SCHEMA_VERSION
    base: dict[str, Any]  # {"blueprint_id", "revision"}
    profile: dict[str, Any]  # {"profile_id", "revision", "computed_at", "expires_at", "stale", "coverage_ratio", "warnings"}
    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: list[DiffEntry] = Field(default_factory=list)
    summary: DiffSummary = Field(default_factory=DiffSummary)
    warnings: list[str] = Field(default_factory=list, max_length=30)


# ── 维度 → base/proposal 源字段映射 ──────────────────────────────────────────


def _base_items(base: dict[str, Any], dimension: str) -> list[Any]:
    bp = (base.get("body") or base) if isinstance(base.get("body"), dict) else base
    if dimension == "objectives":
        return list(bp.get("objectives") or [])
    if dimension == "key_difficult_points":
        return list((bp.get("key_points") or []) + (bp.get("difficult_points") or []))
    if dimension == "activities":
        return list(bp.get("inquiries") or [])
    if dimension == "examples":
        return list(bp.get("examples") or [])
    return []  # stratified_support / diagnostic_response / evidence / time_allocation 单独处理


def _proposal_items(proposal: dict[str, Any], dimension: str) -> list[Any]:
    bp = proposal.get("blueprint") or {}
    if dimension == "objectives":
        return list(bp.get("objectives") or [])
    if dimension == "key_difficult_points":
        return list((bp.get("key_points") or []) + (bp.get("difficult_points") or []))
    if dimension == "activities":
        return list(bp.get("inquiries") or [])
    if dimension == "examples":
        return list(bp.get("examples") or [])
    return list(proposal.get(dimension) or [])


def _item_eq(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return (a.get("text"), a.get("source_ref")) == (b.get("text"), b.get("source_ref"))
    return a == b


def _evidence_refs_for(proposal: dict[str, Any], dimension: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for finding in proposal.get("evidence") or []:
        applies = finding.get("applies_to") or []
        fid = finding.get("finding_id")
        if fid and dimension in applies and fid not in seen:
            seen.add(fid)
            refs.append(fid)
    return refs


def _diff_list(
    base_items: list[Any],
    proposal_items: list[Any],
    *,
    dimension: str,
    evidence_refs: list[str],
) -> list[DiffEntry]:
    entries: list[DiffEntry] = []
    for i, b in enumerate(base_items):
        path = f"{dimension}[{i}]"
        if i < len(proposal_items):
            p = proposal_items[i]
            if _item_eq(b, p):
                entries.append(DiffEntry(dimension=dimension, op="keep", path=path, before=b, after=p))
            else:
                entries.append(
                    DiffEntry(dimension=dimension, op="change", path=path, before=b, after=p, evidence_refs=evidence_refs)
                )
        else:
            entries.append(DiffEntry(dimension=dimension, op="remove", path=path, before=b))
    for j in range(len(base_items), len(proposal_items)):
        entries.append(
            DiffEntry(
                dimension=dimension,
                op="add",
                path=f"{dimension}[{j}]",
                after=proposal_items[j],
                evidence_refs=evidence_refs,
            )
        )
    return entries


def _diff_time_allocation(base: dict[str, Any], proposal: dict[str, Any], evidence_refs: list[str]) -> list[DiffEntry]:
    """时间分配：lesson_flow 各阶段 duration_minutes 逐阶段位置化比较。"""
    base_bp = base.get("body") if isinstance(base.get("body"), dict) else base
    prop_bp = proposal.get("blueprint") or {}
    base_flow = list((base_bp or {}).get("lesson_flow") or [])
    prop_flow = list(prop_bp.get("lesson_flow") or [])
    entries: list[DiffEntry] = []
    for i, b in enumerate(base_flow):
        path = f"lesson_flow[{i}].duration_minutes"
        b_dur, b_title = b.get("duration_minutes"), b.get("title")
        if i < len(prop_flow):
            p_dur, p_title = prop_flow[i].get("duration_minutes"), prop_flow[i].get("title")
            if b_dur == p_dur and b_title == p_title:
                entries.append(DiffEntry(dimension="time_allocation", op="keep", path=path, before=b_dur, after=p_dur))
            else:
                entries.append(
                    DiffEntry(
                        dimension="time_allocation",
                        op="change",
                        path=path,
                        before={"title": b_title, "duration_minutes": b_dur},
                        after={"title": p_title, "duration_minutes": p_dur},
                        evidence_refs=evidence_refs,
                    )
                )
        else:
            entries.append(
                DiffEntry(
                    dimension="time_allocation",
                    op="remove",
                    path=path,
                    before={"title": b_title, "duration_minutes": b_dur},
                )
            )
    for j in range(len(base_flow), len(prop_flow)):
        p = prop_flow[j]
        entries.append(
            DiffEntry(
                dimension="time_allocation",
                op="add",
                path=f"lesson_flow[{j}].duration_minutes",
                after={"title": p.get("title"), "duration_minutes": p.get("duration_minutes")},
                evidence_refs=evidence_refs,
            )
        )
    return entries


def _diff_evidence(proposal: dict[str, Any]) -> list[DiffEntry]:
    """证据维度：profile finding 全部为新增（base 无证据维度）。"""
    entries: list[DiffEntry] = []
    for j, finding in enumerate(proposal.get("evidence") or []):
        entries.append(
            DiffEntry(
                dimension="evidence",
                op="add",
                path=f"evidence[{j}]",
                after=finding,
                evidence_refs=[finding["finding_id"]] if finding.get("finding_id") else [],
            )
        )
    return entries


def build_revision_diff(
    *,
    base: dict[str, Any],
    proposal: dict[str, Any],
    profile_ref: dict[str, Any],
    context_snapshot_hash: str,
    warnings: list[str] | None = None,
) -> StructuredDiff:
    """确定性 diff：同 (base, proposal, profile_ref, context hash) → 同输出。"""
    entries: list[DiffEntry] = []

    list_dims = (
        "objectives",
        "key_difficult_points",
        "activities",
        "examples",
        "stratified_support",
        "diagnostic_response",
    )
    for dimension in list_dims:
        base_items = _base_items(base, dimension)
        proposal_items = _proposal_items(proposal, dimension)
        if dimension in ("stratified_support", "diagnostic_response") and not base_items:
            # 全新维度：旧蓝图为空 → 全部 add（不做 remove/keep）
            entries.extend(
                DiffEntry(
                    dimension=dimension,
                    op="add",
                    path=f"{dimension}[{j}]",
                    after=item,
                    evidence_refs=_evidence_refs_for(proposal, dimension),
                )
                for j, item in enumerate(proposal_items)
            )
        else:
            entries.extend(
                _diff_list(
                    base_items,
                    proposal_items,
                    dimension=dimension,
                    evidence_refs=_evidence_refs_for(proposal, dimension),
                )
            )

    entries.extend(_diff_time_allocation(base, proposal, _evidence_refs_for(proposal, "time_allocation")))
    entries.extend(_diff_evidence(proposal))

    summary = DiffSummary(
        total=len(entries),
        keep=sum(1 for e in entries if e.op == "keep"),
        change=sum(1 for e in entries if e.op == "change"),
        add=sum(1 for e in entries if e.op == "add"),
        remove=sum(1 for e in entries if e.op == "remove"),
    )
    return StructuredDiff(
        base=base,
        profile=profile_ref,
        context_snapshot_hash=context_snapshot_hash,
        entries=entries,
        summary=summary,
        warnings=list(warnings or []),
    )


__all__ = [
    "DIFF_SCHEMA_VERSION",
    "DIFF_DIMENSIONS",
    "StructuredDiff",
    "DiffEntry",
    "build_revision_diff",
]
