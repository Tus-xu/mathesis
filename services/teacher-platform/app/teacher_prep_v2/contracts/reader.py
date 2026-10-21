"""跨端契约容忍读取器（R13）：新旧版本双读 + 旧 consumer 兼容 + fail closed。

规则（与 BREAKING-CHANGE-RULES.md §2/§3 同文）：
- 同 major：直接按当前模型读取（extra=ignore 容忍 minor 新增字段，向前兼容）；
- major > current：fail closed（ContractVersionUnsupported，拒绝未知未来语义）；
- major == 0 / 无 schema_version：旧式/别名载荷，走 legacy 归一：
  - kind 别名 `TaskContract` → 依 payload.kind（PRE_CLASS_DIAGNOSTIC/CLASSROOM）归一，
    否则默认 PreClassDiagnosticTask；`Submission` → StudentSubmission；
  - 缺失字段由模型默认/unknown_flags 如实表示，不伪造数据；
  - 无法归一到已知契约 → ContractLegacyAliasUnknown。
- 双读保证：同一契约 id，新形状与旧形状读出同一稳定对象（id 不重建）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .base import PREP_CONTRACTS_SCHEMA_VERSION, _ContractModel
from .catalog import CONTRACT_MODELS, contract_model
from .errors import (
    ContractLegacyAliasUnknown,
    ContractValidationFailed,
    ContractVersionUnsupported,
)
from .models import ClassroomTask, PreClassDiagnosticTask, StudentSubmission

# 旧名兼容别名（DECISIONS §6：只能作为内部兼容别名，不得形成第二套事实）
ALIAS_KINDS = {
    "TaskContract": ("PreClassDiagnosticTask", "ClassroomTask"),
    "Submission": ("StudentSubmission",),
}
LEGACY_VERSION = "0.0.0"


@dataclass
class ContractReadResult:
    kind: str
    data: _ContractModel
    schema_version: str
    legacy_alias: bool
    warnings: list[str] = field(default_factory=list)


def _resolve_kind(kind: str, payload: dict[str, Any]) -> str:
    if kind in CONTRACT_MODELS:
        return kind
    for alias, targets in ALIAS_KINDS.items():
        if kind == alias:
            if len(targets) == 1:
                return targets[0]
            inner = str(payload.get("kind", "")).upper()
            for t in targets:
                expected = CONTRACT_MODELS[t].model_fields.get("kind")
                default = getattr(expected, "default", None)
                if isinstance(default, str) and default.upper() == inner:
                    return t
            return targets[0]  # 无法判定 → 默认（容忍读取，warnings 留痕）
    raise ContractLegacyAliasUnknown(f"无法归一的旧别名/未知契约: {kind!r}")


def _parse(kind: str, payload: dict[str, Any]) -> ContractReadResult:
    model_cls: type[_ContractModel] = contract_model(kind)
    try:
        obj = model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ContractValidationFailed(f"{kind} 校验失败: {exc.errors()[:3]}") from exc
    version = getattr(obj, "schema_version", PREP_CONTRACTS_SCHEMA_VERSION)
    return ContractReadResult(kind=kind, data=obj, schema_version=version, legacy_alias=False)


def _normalize_legacy(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """把旧式扁平字段平整化为 v1 结构（不新增/不伪造业务身份字段）。

    - Submission 旧形状：task_id/assignment_id 扁平 → task_ref/assignment_ref；
    - 业务身份字段（tenant_id/class_id/owner_id/window...）若旧载荷真缺失，
      宁可 fail closed（ContractValidationFailed 附 missing 清单），也不编造 UUID。
    """
    out = dict(payload)
    out.setdefault("schema_version", "1.0.0")
    if kind == "StudentSubmission":
        task_id = out.pop("task_id", None)
        if task_id is not None and "task_ref" not in out:
            out["task_ref"] = {
                "task_id": task_id,
                "task_kind": str(out.pop("task_kind", "PRE_CLASS_DIAGNOSTIC")).upper(),
            }
        assignment_id = out.pop("assignment_id", None)
        if assignment_id is not None and "assignment_ref" not in out:
            out["assignment_ref"] = {"task_assignment_id": assignment_id}
    return out


def read_contract(kind: str, payload: dict[str, Any]) -> ContractReadResult:
    """按当前规则读取跨端契约载荷（新旧双读统一入口）。"""
    payload = dict(payload or {})
    given_version = str(payload.get("schema_version") or LEGACY_VERSION).strip()
    major = int(given_version.split(".", 1)[0]) if given_version.split(".", 1)[0].isdigit() else 0

    if major > int(PREP_CONTRACTS_SCHEMA_VERSION.split(".", 1)[0]):
        raise ContractVersionUnsupported(
            f"{kind} payload schema_version={given_version} 高于当前 {PREP_CONTRACTS_SCHEMA_VERSION}，"
            "拒绝读取（fail closed）"
        )

    if kind in ALIAS_KINDS or given_version == LEGACY_VERSION or major == 0:
        real_kind = _resolve_kind(kind, payload)
        normalized = _normalize_legacy(real_kind, payload)
        result = _parse(real_kind, normalized)
        result.legacy_alias = True
        result.warnings.append(
            f"legacy 别名读取: {kind!r} → {real_kind!r}（缺失字段以 unknown/默认值如实表达）"
        )
        return result

    # 同 major 正常读取；minor 不同（更高 minor）也容忍（向后兼容读者）：
    # 用当前契约版本校验形状（const 由 reader 代答），不因 minor 递增爆破旧线程。
    if given_version != PREP_CONTRACTS_SCHEMA_VERSION:
        payload["schema_version"] = PREP_CONTRACTS_SCHEMA_VERSION
        result = _parse(kind, payload)
        result.warnings.append(f"minor 向前兼容读取: 输入 {given_version} → 按 {PREP_CONTRACTS_SCHEMA_VERSION} 解析")
        return result
    return _parse(kind, payload)


def read_any(payload: dict[str, Any]) -> ContractReadResult:
    """从 payload 推断 kind 并读取（适配层对账/列出用）。"""
    kind = str(payload.get("kind") or payload.get("contract") or "")
    if not kind:
        raise ContractLegacyAliasUnknown("payload 缺少 kind/contract，无法推断契约")
    return read_contract(kind, payload)


def dual_read_identical(kind: str, payload: dict[str, Any], legacy_payload: dict[str, Any]) -> bool:
    """新旧版本双读一致性：两个形状读出同一契约 id。"""
    new = read_contract(kind, payload)
    old = read_contract(kind, legacy_payload)
    return new.data.id == old.data.id


__all__ = [
    "ALIAS_KINDS",
    "read_contract",
    "read_any",
    "dual_read_identical",
    "ContractReadResult",
    "PreClassDiagnosticTask",
    "ClassroomTask",
    "StudentSubmission",
]
