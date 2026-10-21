"""RV06 · 去重与 rerank：保留来源维度，不做无损合并。

原则（任务书 §必须完成 6）：

1. 去重键含 `evidence_type` —— **跨维度绝不合并**。教材 chunk 与文献即使
   标识巧合相近，也永远是两条不同的证据。
2. 同 locator 但 `rights` 或 `provenance` 不同 → **不合并**，两份都留下，
   并在每条上互记 `DUPLICATE_LOCATOR_DISTINCT_SOURCE`。
   把 PUBLIC 与 RESTRICTED 合成一条，等于把权利差异抹平 —— 那是数据事故。
3. rerank 权重是模块常量，签名里**没有** query 参数：
   评分不得依赖查询字面量，否则评测分数可以用特例刷出来。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from .contracts import FederatedResult
from .types import WarnCode

#: rerank 的加权系数。常量，不随查询变化。
DEFAULT_WEIGHTS: MappingProxyType = MappingProxyType(
    {
        "lexical": 1.0,
        "vector": 1.0,
        "recency": 0.2,
        "rights_penalty": 1.0,
    }
)


@dataclass(frozen=True)
class DedupeOutcome:
    results: tuple[FederatedResult, ...]
    collapsed: int
    conflicts_kept: int

    @property
    def input_count(self) -> int:
        return len(self.results) + self.collapsed


def dedupe_key(result: FederatedResult) -> tuple[str, str]:
    """去重键：`(evidence_type, locator.discriminator)`。"""
    return (result.evidence_type.value, result.locator.discriminator)


def _source_identity(result: FederatedResult) -> tuple[str, str, str]:
    """来源身份：rights + provenance 指纹。只有完全一致才允许折叠。"""
    return (
        result.rights.rights_status,
        result.rights.rights_evidence_ref,
        result.provenance.source_hash,
    )


def _with_warning(result: FederatedResult, code: WarnCode) -> FederatedResult:
    if code.value in result.warnings:
        return result
    return replace(result, warnings=(*result.warnings, code.value))


def dedupe_results(results: Sequence[FederatedResult]) -> DedupeOutcome:
    """按维度隔离去重。返回保留的结果 + 折叠数 + 冲突保留数。"""
    groups: dict[tuple[str, str], list[FederatedResult]] = {}
    order: list[tuple[str, str]] = []
    for r in results:
        key = dedupe_key(r)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    kept: list[FederatedResult] = []
    collapsed = 0
    conflicts = 0
    for key in order:
        bucket = groups[key]
        by_identity: dict[tuple[str, str, str], FederatedResult] = {}
        for r in bucket:
            ident = _source_identity(r)
            if ident not in by_identity:
                by_identity[ident] = r
            else:
                # 完全同源同权利 → 真重复，折叠
                collapsed += 1

        distinct = list(by_identity.values())
        if len(distinct) > 1:
            # 同 locator 多来源（rights / provenance 不同）：全部保留并互记告警
            conflicts += 1
            distinct = [_with_warning(r, WarnCode.DUPLICATE_LOCATOR_DISTINCT_SOURCE) for r in distinct]
        kept.extend(distinct)

    return DedupeOutcome(results=tuple(kept), collapsed=collapsed, conflicts_kept=conflicts)


def rerank(
    results: Sequence[FederatedResult],
    *,
    weights: MappingProxyType | dict[str, float] | None = None,
) -> tuple[FederatedResult, ...]:
    """按分项加权和稳定重排。

    **不接收 query**：评分只读 `score_components`，因此评测分数无法被
    "针对某个查询的特例" 抬高。
    """
    w: dict[str, float] = dict(weights or DEFAULT_WEIGHTS)

    def weighted(r: FederatedResult) -> float:
        sc = r.score_components
        return (
            w["lexical"] * sc.lexical
            + w["vector"] * sc.vector
            + w["recency"] * sc.recency
            - w["rights_penalty"] * sc.rights_penalty
        )

    decorated: list[tuple[float, int, FederatedResult]] = [
        (weighted(r), idx, r) for idx, r in enumerate(results)
    ]
    # 同分时按输入序（idx）稳定，不依赖字典/集合顺序
    decorated.sort(key=lambda item: (-item[0], item[1]))
    return tuple(r for _, _, r in decorated)


def balance_dimensions(results: Sequence[FederatedResult]) -> tuple[FederatedResult, ...]:
    """按 `evidence_type` 轮转交织，保证多维度都可见。

    为什么需要它：各 provider 的候选数量天然不均。实测中 Teacher 一次给出 300 条
    direct-chunk 候选，而本项目资产与文献各只有 1 条；纯按分数排序时 top-k 被教材
    占满 —— 结果**看着像单库检索**，用户完全看不到另外两个维度。
    轮转输出序列形如 [A1,B1,C1,A2,B2,…]，因此在任何 `limit >= 维度数` 时
    每个维度都至少出现一次。

    维度内部仍保持 rerank 的分数次序（输入已是降序），组间顺序取各维度最高分的名次。
    """
    groups: dict[str, list[FederatedResult]] = {}
    order: list[str] = []
    for r in results:
        key = r.evidence_type.value
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    total = len(results)
    out: list[FederatedResult] = []
    depth = 0
    while len(out) < total:
        progressed = False
        for key in order:
            bucket = groups[key]
            if depth < len(bucket):
                out.append(bucket[depth])
                progressed = True
                if len(out) >= total:
                    break
        if not progressed:
            break
        depth += 1
    return tuple(out)


def dedupe_and_rank(
    results: Sequence[FederatedResult],
    *,
    limit: int | None = None,
    weights: MappingProxyType | dict[str, float] | None = None,
    balance_by_dimension: bool = True,
) -> DedupeOutcome:
    """去重 → rerank → 维度交织 → 截断（编排层的一次调用入口）。"""
    outcome = dedupe_results(results)
    ranked = rerank(outcome.results, weights=weights)
    if balance_by_dimension:
        ranked = balance_dimensions(ranked)
    if limit is not None:
        ranked = ranked[:limit]
    return DedupeOutcome(
        results=ranked, collapsed=outcome.collapsed, conflicts_kept=outcome.conflicts_kept
    )


def rerank_weights() -> dict[str, Any]:
    return dict(DEFAULT_WEIGHTS)


__all__ = [
    "DEFAULT_WEIGHTS",
    "DedupeOutcome",
    "dedupe_key",
    "dedupe_results",
    "rerank",
    "balance_dimensions",
    "dedupe_and_rank",
    "rerank_weights",
]
