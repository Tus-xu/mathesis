"""平台契约错误分类（P00）：全部 fail closed。

与 `app/teacher_prep_v2/contracts/errors.py` 同构；独立命名空间避免跨域耦合。
"""

from __future__ import annotations


class PlatformContractError(Exception):
    """平台契约错误基类。任何未知/不安全语义一律以此族抛出（fail closed）。"""


class ContractValidationFailed(PlatformContractError):
    """载荷未通过契约校验（缺字段/类型错/pattern 不匹配/不变式被违反）。"""


class ContractVersionUnsupported(PlatformContractError):
    """`major > current`，拒绝读取未知未来语义。"""


class ContractUnknownEnumValue(PlatformContractError):
    """枚举/状态取值不在冻结集合内（禁止把 unknown 默认成成功）。"""


class ContractPolicyViolation(PlatformContractError):
    """违反平台策略（教材 page 锚点、trust label 提升、未发布 prompt 等）。"""


class ContractUnknownKind(PlatformContractError):
    """契约名未登记。"""


__all__ = [
    "PlatformContractError",
    "ContractValidationFailed",
    "ContractVersionUnsupported",
    "ContractUnknownEnumValue",
    "ContractPolicyViolation",
    "ContractUnknownKind",
]
