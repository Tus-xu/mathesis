"""契约目录（P00）：名称 ↔ 模型 ↔ 版本 ↔ 顺序 的机器可读注册表。

唯一权威：本目录 + Pydantic 模型。任何新平台契约必须先在此登记再做 schema/TS 生成。
顺序与 P00 任务书"必须冻结的契约"1..16 完全一致（稳定顺序，避免 schema 漂移）。
"""

from __future__ import annotations

from typing import Any

from .access import AccessContext
from .ai_run import AIRunRecord
from .artifact import ArtifactRef
from .base import PLATFORM_CONTRACTS_SCHEMA_VERSION
from .command import CommandEnvelope
from .component_catalog import ComponentCatalogContract
from .context_manifest import ContextManifest
from .design_tokens import PlatformDesignTokenContract
from .event_envelope import DomainEventEnvelope, SSECursor
from .evidence import EvidenceRef
from .frontend_state import FrontendStateAndCopyContract
from .job import JobContract
from .memory import MemoryRecord
from .prompt_ref import PromptRef
from .provider import ProviderContract
from .tool_policy import ToolPolicy
from .workspace_layout import WorkspaceLayoutContract

# 任务书顺序（1..16）；SSECursor 作为 event envelope 的伴随契约单独登记
CONTRACT_NAMES: tuple[str, ...] = (
    "AccessContext",
    "CommandEnvelope",
    "DomainEventEnvelope",
    "JobContract",
    "ArtifactRef",
    "EvidenceRef",
    "ProviderContract",
    "AIRunRecord",
    "ToolPolicy",
    "PromptRef",
    "MemoryRecord",
    "ContextManifest",
    "PlatformDesignTokenContract",
    "WorkspaceLayoutContract",
    "ComponentCatalogContract",
    "FrontendStateAndCopyContract",
)

# 伴随契约（不占 16 项编号，但可被 read_contract 读取）
COMPANION_CONTRACTS: tuple[str, ...] = ("SSECursor",)

CONTRACT_MODELS: dict[str, type[Any]] = {
    "AccessContext": AccessContext,
    "CommandEnvelope": CommandEnvelope,
    "DomainEventEnvelope": DomainEventEnvelope,
    "JobContract": JobContract,
    "ArtifactRef": ArtifactRef,
    "EvidenceRef": EvidenceRef,
    "ProviderContract": ProviderContract,
    "AIRunRecord": AIRunRecord,
    "ToolPolicy": ToolPolicy,
    "PromptRef": PromptRef,
    "MemoryRecord": MemoryRecord,
    "ContextManifest": ContextManifest,
    "PlatformDesignTokenContract": PlatformDesignTokenContract,
    "WorkspaceLayoutContract": WorkspaceLayoutContract,
    "ComponentCatalogContract": ComponentCatalogContract,
    "FrontendStateAndCopyContract": FrontendStateAndCopyContract,
    "SSECursor": SSECursor,
}

REGISTRY_ORDER: tuple[str, ...] = CONTRACT_NAMES + COMPANION_CONTRACTS


def contract_model(kind: str) -> type[Any]:
    from .errors import ContractUnknownKind

    if kind not in CONTRACT_MODELS:
        raise ContractUnknownKind(
            f"未知平台契约 {kind!r}；允许: {', '.join(REGISTRY_ORDER)}"
        )
    return CONTRACT_MODELS[kind]


def contract_names_with_versions() -> dict[str, str]:
    return {name: PLATFORM_CONTRACTS_SCHEMA_VERSION for name in REGISTRY_ORDER}


__all__ = [
    "CONTRACT_NAMES",
    "COMPANION_CONTRACTS",
    "CONTRACT_MODELS",
    "REGISTRY_ORDER",
    "contract_model",
    "contract_names_with_versions",
]
