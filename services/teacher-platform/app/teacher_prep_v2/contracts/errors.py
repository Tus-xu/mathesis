"""跨端契约错误码（R13）。

契约层错误用独立的 `ContractErrorCode`（数字区间 42200-50399，与 8100 平台
`app/core/errors.py` 的 ErrorCode 区间错开，避免语义串扰）；HTTP 映射由
adapter/入口层完成（contracts 包不依赖 app.core，保持纯数据层）。
"""

from __future__ import annotations

from enum import IntEnum


class ContractErrorCode(IntEnum):
    OK = 0
    VALIDATION = 42201                 # 载荷不符合契约形状
    VERSION_UNSUPPORTED = 42202        # schema_version major 高于当前，fail closed
    STATE_TRANSITION_INVALID = 40901   # 非法状态迁移
    IDEMPOTENCY_CONFLICT = 40902       # 同幂等键不同载荷
    MEMBERSHIP_DENIED = 40310          # 跨班访问 / 非当前成员（8000 ClassMember 事实拒绝）
    AUTHORITY_UNREACHABLE = 50301      # 8000 权威不可达（fail closed）
    MAPPING_MISSING = 40403            # 稳定映射缺失（重试窗口外 / 未绑定）
    LEGACY_ALIAS_UNKNOWN = 42203       # 旧别名载荷无法归一到已知契约
    INTERNAL = 50001


class ContractError(Exception):
    """契约层业务异常。"""

    def __init__(self, code: ContractErrorCode, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code.value} {code.name}] {message}")


class ContractValidationFailed(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.VALIDATION, message)


class ContractVersionUnsupported(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.VERSION_UNSUPPORTED, message)


class ContractStateTransitionInvalid(ContractError):
    def __init__(self, contract_kind: str, current: str, target: str):
        super().__init__(
            ContractErrorCode.STATE_TRANSITION_INVALID,
            f"{contract_kind} 非法状态迁移 {current!r} → {target!r}",
        )


class ContractIdempotencyConflict(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.IDEMPOTENCY_CONFLICT, message)


class ContractMembershipDenied(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.MEMBERSHIP_DENIED, message)


class ContractAuthorityUnreachable(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.AUTHORITY_UNREACHABLE, message)


class ContractMappingMissing(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.MAPPING_MISSING, message)


class ContractLegacyAliasUnknown(ContractError):
    def __init__(self, message: str):
        super().__init__(ContractErrorCode.LEGACY_ALIAS_UNKNOWN, message)
