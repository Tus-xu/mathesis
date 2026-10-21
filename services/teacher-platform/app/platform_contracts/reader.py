"""契约容忍读取器（P00）：版本策略 + unknown 字段观察集 + fail closed。

规则（与 `teacher_prep_v2/contracts/reader.py` 同构，独立命名空间）：
- 同 major：按当前模型读取（extra=allow 保留未知字段为观察项，向前兼容）；
- `major > current`：fail closed（`ContractVersionUnsupported`）；
- `major == 0` / 无 `schema_version`：legacy 载荷，按当前模型尽力读取，
  缺必填字段 → fail（**不得伪造默认值**），并给出 legacy warnings；
- 未知**枚举/状态**：由各契约 validator 拒绝（不得默认成成功）；
- 未知**字段**：进入 `unknown_fields`（观察集），不改变 core 语义。

**不削弱既有测试**：本 reader 只新增能力，不改动 `teacher_prep_v2` 的 reader。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .base import PLATFORM_CONTRACTS_SCHEMA_VERSION
from .catalog import CONTRACT_MODELS, contract_model
from .errors import (
    ContractUnknownKind,
    ContractValidationFailed,
    ContractVersionUnsupported,
)

LEGACY_VERSION = "0.0.0"
_CONTRACT_READ_RESULT = "ContractReadResult"


@dataclass
class ContractReadResult:
    kind: str
    data: Any
    schema_version: str
    legacy: bool
    unknown_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _declared_fields(model_cls: type) -> set[str]:
    return set(model_cls.model_fields.keys())


def _parse(kind: str, payload: dict[str, Any], *, legacy: bool) -> ContractReadResult:
    model_cls = contract_model(kind)
    try:
        obj = model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ContractValidationFailed(f"{kind} 校验失败: {_summarize(exc)}") from exc
    except Exception as exc:  # 契约自定义 Policy/Enum 异常（fail closed）
        from .errors import PlatformContractError

        if isinstance(exc, PlatformContractError):
            raise
        raise ContractValidationFailed(f"{kind} 校验失败: {exc}") from exc

    extra = obj.model_extra or {}
    unknown = sorted(k for k in extra if k not in _declared_fields(model_cls))
    version = getattr(obj, "schema_version", PLATFORM_CONTRACTS_SCHEMA_VERSION)
    result = ContractReadResult(
        kind=kind,
        data=obj,
        schema_version=str(version),
        legacy=legacy,
        unknown_fields=unknown,
    )
    if unknown:
        result.warnings.append(
            f"未知字段保留为观察项（不改变 core 语义）: {unknown}"
        )
    if legacy:
        result.warnings.append(f"legacy 读取（无 schema_version 或 major=0）: {kind}")
    return result


def _summarize(exc: ValidationError) -> str:
    out = []
    for err in exc.errors()[:4]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out.append(f"{loc}: {err.get('msg')}")
    return " | ".join(out)


def read_contract(kind: str, payload: dict[str, Any]) -> ContractReadResult:
    """按当前规则读取平台契约载荷（唯一入口）。"""
    if kind not in CONTRACT_MODELS:
        raise ContractUnknownKind(f"未知平台契约 {kind!r}")
    payload = dict(payload or {})
    raw_version = payload.get("schema_version")
    given_version = str(raw_version or LEGACY_VERSION).strip()
    head = given_version.split(".", 1)[0]
    major = int(head) if head.isdigit() else 0

    if major > int(PLATFORM_CONTRACTS_SCHEMA_VERSION.split(".", 1)[0]):
        raise ContractVersionUnsupported(
            f"{kind} payload schema_version={given_version} 高于当前 "
            f"{PLATFORM_CONTRACTS_SCHEMA_VERSION}，拒绝读取（fail closed）"
        )

    if raw_version is None or major == 0:
        normalized = dict(payload)
        normalized.pop("schema_version", None)
        return _parse(kind, normalized, legacy=True)

    if given_version != PLATFORM_CONTRACTS_SCHEMA_VERSION:
        normalized = dict(payload)
        normalized["schema_version"] = PLATFORM_CONTRACTS_SCHEMA_VERSION
        result = _parse(kind, normalized, legacy=False)
        result.warnings.append(
            f"minor 向前兼容读取: 输入 {given_version} → 按 {PLATFORM_CONTRACTS_SCHEMA_VERSION} 解析"
        )
        return result

    return _parse(kind, payload, legacy=False)


def read_any(payload: dict[str, Any]) -> ContractReadResult:
    """从 payload 推断 kind 并读取。"""
    kind = str(payload.get("kind") or payload.get("contract") or "")
    if not kind:
        raise ContractUnknownKind("payload 缺少 kind/contract，无法推断契约")
    return read_contract(kind, payload)


__all__ = ["read_contract", "read_any", "ContractReadResult", "LEGACY_VERSION"]
