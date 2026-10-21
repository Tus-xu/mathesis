"""RV06 · Federation 编排：scope 冻结 → 逐 provider 取数 → 复核 → 去重 rerank。

三条不可协商的行为：

1. **部分失败不伪装成成功**：只要有 provider 失败，响应就是 `PARTIAL`，
   `degraded_providers` 非空，错误码原样进 `warnings`。全部失败才是 `FAILED`。
2. **scope 由编排层兜底**：provider 声称的归属要再经 `check_hit_scope()` 复核。
   "相信 provider 自己会过滤" 在跨租户场景下不是可接受的假设。
3. **无 locator 即丢弃**：宁可少一条结果，也不产出一条看起来能回源、
   实际点不开的引用。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .catalog import DEFAULT_CATALOG
from .contracts import (
    FederatedQuery,
    FederatedResult,
    ProviderHit,
    snippet_hash,
)
from .dedupe import dedupe_and_rank
from .errors import ScopeViolation
from .providers.base import FederatedProvider, ProviderError
from .sanitize import sanitize_untrusted
from .scope import ResolvedScope, check_hit_scope
from .types import (
    EvidenceType,
    ProviderStatus,
    ResponseStatus,
    WarnCode,
)


@dataclass(frozen=True)
class FederatedResponse:
    """统一检索响应。`status` 是唯一可信的完成度判据。"""

    status: ResponseStatus
    results: tuple[FederatedResult, ...]
    provider_status: Mapping[str, ProviderStatus]
    degraded_providers: tuple[str, ...]
    warnings: tuple[str, ...]
    trace_id: str
    query_hash: str
    latency_ms: int
    collapsed: int
    conflicts_kept: int
    scope: ResolvedScope
    provider_errors: Mapping[str, dict[str, Any]] = MappingProxyType({})

    @property
    def complete(self) -> bool:
        """只有 `OK` 才是"全部 provider 都成功"。"""
        return self.status is ResponseStatus.OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "complete": self.complete,
            "trace_id": self.trace_id,
            "query_hash": self.query_hash,
            "latency_ms": self.latency_ms,
            "collapsed": self.collapsed,
            "conflicts_kept": self.conflicts_kept,
            "provider_status": {k: v.value for k, v in self.provider_status.items()},
            "degraded_providers": list(self.degraded_providers),
            "warnings": list(self.warnings),
            # to_untrusted_block() 返回只读映射（防调用方改写 trust_level）；
            # 对外 JSON 投影需要可变 dict，这里显式复制一份。
            "results": [dict(r.to_untrusted_block()) for r in self.results],
        }


TraceRecorder = Callable[[FederatedQuery, FederatedResponse], None]


def _result_of_hit(hit: ProviderHit, sanitized: str, patterns: tuple[str, ...]) -> FederatedResult:
    warnings = tuple(dict.fromkeys((*hit.warnings, *((WarnCode.INJECTION_PATTERN_DETECTED.value,) if patterns else ()))))
    citation_eligible = hit.citation_eligible
    if hit.evidence_type is EvidenceType.FOUNDATIONAL_MATH:
        # 兜底：教材永不声明"可作为文献引用"（provider 已设 False，这里再钉一次）
        citation_eligible = False
    return FederatedResult(
        result_id=f"{hit.provider}:{hit.locator.discriminator}:{snippet_hash(sanitized)[:12]}",
        evidence_type=hit.evidence_type,
        provider=hit.provider,
        provider_version=hit.provider_version,
        locator=hit.locator,
        snippet=sanitized,
        snippet_hash=snippet_hash(sanitized),
        rights=hit.rights,
        provenance=hit.provenance,
        retrieved_at=hit.retrieved_at,
        score_components=hit.score_components,
        warnings=warnings,
        labels=hit.labels,
        sanitized=bool(patterns),
        citation_eligible=citation_eligible,
        record_kind=hit.record_kind,
    )


def _status_for(attempted: int, failed: int) -> ResponseStatus:
    if attempted == 0:
        return ResponseStatus.FAILED
    if failed == 0:
        return ResponseStatus.OK
    if failed >= attempted:
        return ResponseStatus.FAILED
    return ResponseStatus.PARTIAL


def federated_search(
    query: FederatedQuery,
    *,
    scope: ResolvedScope,
    providers: Mapping[str, FederatedProvider] | None = None,
    trace_recorder: TraceRecorder | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FederatedResponse:
    """执行一次统一检索。"""
    if query.tenant_id != scope.tenant_id:
        raise ScopeViolation(
            f"query.tenant_id={query.tenant_id} 与已冻结 scope.tenant_id={scope.tenant_id} 不一致"
        )

    registry: Mapping[str, FederatedProvider] = providers or {}
    started = clock()
    deadline = started + (query.deadline_ms / 1000.0)

    warnings: list[str] = []
    provider_status: dict[str, ProviderStatus] = {}
    provider_errors: dict[str, dict[str, Any]] = {}
    degraded: list[str] = []
    hits: list[ProviderHit] = []
    attempted = 0
    failed = 0
    calls = 0

    def _mark_degraded(name: str, status: ProviderStatus, code: WarnCode | str, error: dict[str, Any] | None = None) -> None:
        nonlocal failed
        provider_status[name] = status
        degraded.append(name)
        failed += 1
        value = code.value if isinstance(code, WarnCode) else str(code)
        if value not in warnings:
            warnings.append(value)
        if error is not None:
            provider_errors[name] = error

    for name in query.provider_allowlist:
        spec = DEFAULT_CATALOG.get(name)
        if spec is None:
            if WarnCode.UNKNOWN_PROVIDER.value not in warnings:
                warnings.append(WarnCode.UNKNOWN_PROVIDER.value)
            provider_status[name] = ProviderStatus.UNAVAILABLE
            continue
        if not spec.enabled:
            if WarnCode.PROVIDER_DISABLED.value not in warnings:
                warnings.append(WarnCode.PROVIDER_DISABLED.value)
            provider_status[name] = ProviderStatus.DISABLED
            continue

        # enabled ⇒ 计入"应当被尝试"的分母（预算/死线跳过也算它没成功）
        attempted += 1

        if calls >= query.budget.max_provider_calls:
            _mark_degraded(name, ProviderStatus.UNAVAILABLE, WarnCode.BUDGET_EXCEEDED)
            continue
        if clock() >= deadline:
            _mark_degraded(name, ProviderStatus.UNAVAILABLE, WarnCode.PROVIDER_TIMEOUT)
            continue

        provider = registry.get(name)
        if provider is None:
            _mark_degraded(
                name,
                ProviderStatus.UNAVAILABLE,
                WarnCode.PROVIDER_ERROR,
                {"code": WarnCode.PROVIDER_ERROR.value, "message": "provider 未注册实现", "retryable": False},
            )
            continue

        calls += 1
        try:
            produced = list(provider.search(query, scope))
        except ProviderError as exc:
            status = ProviderStatus.DEGRADED if exc.retryable else ProviderStatus.UNAVAILABLE
            _mark_degraded(name, status, exc.warn_code, exc.as_dict())
            continue
        except Exception as exc:  # noqa: BLE001 — 未预期的 provider 缺陷也要如实上报，不吞
            _mark_degraded(
                name,
                ProviderStatus.UNAVAILABLE,
                WarnCode.PROVIDER_ERROR,
                {"code": WarnCode.PROVIDER_ERROR.value, "message": str(exc), "retryable": False},
            )
            continue

        provider_status[name] = ProviderStatus.OK
        hits.extend(produced)

    collected: list[FederatedResult] = []
    for hit in hits:
        # provider 只能产出**它自己声明的那个维度**的结果。缺这一条时，
        # 一个 foundational_math provider 可以返回 LITERATURE 结果并被照单全收 ——
        # 那等于绕过了"教材不得伪装成文献"的整条防线。
        spec = DEFAULT_CATALOG.get(hit.provider)
        if spec is None or spec.evidence_type is not hit.evidence_type:
            if WarnCode.DIMENSION_MISMATCH.value not in warnings:
                warnings.append(WarnCode.DIMENSION_MISMATCH.value)
            continue
        violation = check_hit_scope(hit, scope)
        if violation is not None:
            if WarnCode.SCOPE_VIOLATION.value not in warnings:
                warnings.append(WarnCode.SCOPE_VIOLATION.value)
            continue
        if hit.locator is None:
            if WarnCode.MISSING_LOCATOR.value not in warnings:
                warnings.append(WarnCode.MISSING_LOCATOR.value)
            continue
        sanitized = sanitize_untrusted(hit.snippet)
        if sanitized.sanitized and WarnCode.INJECTION_PATTERN_DETECTED.value not in warnings:
            warnings.append(WarnCode.INJECTION_PATTERN_DETECTED.value)
        collected.append(_result_of_hit(hit, sanitized.text, sanitized.patterns))

    outcome = dedupe_and_rank(collected, limit=query.budget.max_results)
    latency_ms = int((clock() - started) * 1000)
    status = _status_for(attempted, failed)

    response = FederatedResponse(
        status=status,
        results=outcome.results,
        provider_status=MappingProxyType(dict(provider_status)),
        degraded_providers=tuple(degraded),
        warnings=tuple(warnings),
        trace_id=query.trace_id,
        query_hash=query.query_hash,
        latency_ms=max(0, latency_ms),
        collapsed=outcome.collapsed,
        conflicts_kept=outcome.conflicts_kept,
        scope=scope,
        provider_errors=MappingProxyType(dict(provider_errors)),
    )

    if trace_recorder is not None:
        trace_recorder(query, response)
    return response


__all__ = ["FederatedResponse", "TraceRecorder", "federated_search"]
