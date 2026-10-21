"""P00 共享平台契约（Shared Platform Contracts）命名空间。

唯一职责：冻结**跨端/跨服务**真正需要的最小契约，作为 Teacher / Research / Student
adapter 三者的共同上游。P00 是 shared contracts / ADR 的唯一 owner（M00
`DIRTY-OWNERSHIP.md` §5），其他任务本轮不得并行修改本命名空间。

设计不变量（与 P00 任务书、DECISIONS.md、总手册 §6.2/§12 一致）：
1. 本包只定义**形状与语义承诺**；不含任何业务 ORM、不触达任何域表。
2. 契约版本采用 `<major>.<minor>.<patch>`；`major > current` → fail closed。
3. 未知**字段**在同 major 内保留为观察项（向前兼容），
   未知**枚举/状态**一律拒绝（不得把 unknown 默认成成功）。
4. 教材证据只允许 `source → section → chunk`；禁止 `page / page_refs / pNN`。
5. 用户上传/检索文本始终 `UNTRUSTED_DATA`，不得提升为系统指令。
6. 生产不得使用 `release_status != RELEASED` 的 prompt；
   `AIRunRecord.environment == PRODUCTION` 时强制校验。

复用既有方式：与 `app/teacher_prep_v2/contracts` 同一套（Pydantic → JSON Schema → TS），
不引入第二套 Agent/workflow 框架或第二套 validator。
"""

from __future__ import annotations

from .access import AccessContext, ResourceScope
from .ai_run import AIRunRecord
from .artifact import ArtifactRef
from .base import (
    PLATFORM_CONTRACTS_BUNDLE_VERSION,
    PLATFORM_CONTRACTS_SCHEMA_VERSION,
    UuidStr,
)
from .catalog import CONTRACT_NAMES, CONTRACT_MODELS, contract_model
from .command import CommandEnvelope
from .component_catalog import ComponentCatalogContract
from .context_manifest import ContextManifest
from .design_tokens import PlatformDesignTokenContract
from .event_envelope import DomainEventEnvelope, SSECursor
from .evidence import EvidenceRef
from .frontend_state import FrontendStateAndCopyContract
from .job import JOB_CONTRACT_STATES, JobContract
from .memory import MemoryRecord
from .prompt_ref import PromptRef
from .provider import ProviderContract
from .reader import ContractReadResult, read_contract, read_any
from .tool_policy import TOOL_POLICY_LEVELS, ToolPolicy
from .workspace_layout import WorkspaceLayoutContract

__all__ = [
    # 版本
    "PLATFORM_CONTRACTS_SCHEMA_VERSION",
    "PLATFORM_CONTRACTS_BUNDLE_VERSION",
    # 目录
    "CONTRACT_NAMES",
    "CONTRACT_MODELS",
    "contract_model",
    # 读取
    "read_contract",
    "read_any",
    "ContractReadResult",
    # 1-2
    "AccessContext",
    "ResourceScope",
    "CommandEnvelope",
    # 3
    "DomainEventEnvelope",
    "SSECursor",
    # 4
    "JobContract",
    "JOB_CONTRACT_STATES",
    # 5
    "ArtifactRef",
    # 6
    "EvidenceRef",
    # 7
    "ProviderContract",
    # 8
    "AIRunRecord",
    # 9
    "ToolPolicy",
    "TOOL_POLICY_LEVELS",
    # 10
    "PromptRef",
    # 11
    "MemoryRecord",
    # 12
    "ContextManifest",
    # 13-16
    "PlatformDesignTokenContract",
    "WorkspaceLayoutContract",
    "ComponentCatalogContract",
    "FrontendStateAndCopyContract",
    # 便捷
    "bundle_schema",
    "UuidStr",
]


def bundle_schema() -> dict:
    """16 契约打包 JSON Schema（$defs 含全部子模型，根为 Bundle）。"""
    from .bundle import PlatformContractsBundle

    return PlatformContractsBundle.model_json_schema()
