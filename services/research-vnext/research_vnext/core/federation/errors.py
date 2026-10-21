"""RV06 · Knowledge Federation 契约异常。

分层：
- `FederationError` —— 运行时基类；
- `FederationContractError` —— **契约违规**（构造期即拒绝，不靠评审兜底）；
- 维度相关异常是重点：`DimensionMismatch` / `DimensionLeak`。
"""

from __future__ import annotations


class FederationError(Exception):
    """运行时基类。"""


class FederationContractError(FederationError, ValueError):
    """契约违规：构造期拒绝（同时是 `ValueError`，便于调用方按值错误捕获）。"""


class DimensionMismatch(FederationContractError):
    """`evidence_type` 与其 locator 类型不一致。

    典型误用：把 Teacher 教材 chunk 标成 `LITERATURE` 以借用文献字段
    （DOI / 期刊 / 页码）——这正是「把教材伪装成学术文献」。
    """


class DimensionLeak(FederationContractError):
    """locator 出现了不属于该维度的字段。

    典型误用：教材 locator 里塞 `doi` / `page_range` / `pnn`；
    资产 locator 里泄露 `storage_key` / MinIO endpoint。
    """


class MissingLocator(FederationContractError):
    """缺少定位锚点 —— 不得产出「看起来可回源」的假结果。"""


class QueryInvalid(FederationContractError):
    """FederatedQuery 缺少必填项（actor/scope/session/purpose/allowlist/budget/deadline/trace）。"""


class ProviderSpecInvalid(FederationContractError):
    """ProviderSpec 不完整（如 rights 缺 `rights_evidence_ref`）。"""


class ScopeViolation(FederationError):
    """命中越出已冻结的 ResolvedScope。"""


__all__ = [
    "FederationError",
    "FederationContractError",
    "DimensionMismatch",
    "DimensionLeak",
    "MissingLocator",
    "QueryInvalid",
    "ProviderSpecInvalid",
    "ScopeViolation",
]
