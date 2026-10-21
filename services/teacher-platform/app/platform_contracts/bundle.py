"""打包容器（P00）：仅用于一次性生成 JSON Schema / TS。

与 `teacher_prep_v2/contracts/catalog.py::PreClassContractsBundle` 同构，
把 16 项平台契约压进一个 JSON Schema，使 TS 一次性产出全部接口与根引用。
**不承载业务语义**，不得在运行时被实例化用于业务传递。
"""

from __future__ import annotations

from .access import AccessContext
from .ai_run import AIRunRecord
from .artifact import ArtifactRef
from .base import PLATFORM_CONTRACTS_BUNDLE_VERSION, VersionedModel
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


class PlatformContractsBundle(VersionedModel):
    """16 项平台契约的打包容器（仅用于 JSON Schema / TS 生成）。"""

    schema_version: str = PLATFORM_CONTRACTS_BUNDLE_VERSION
    access_context: AccessContext | None = None
    command_envelope: CommandEnvelope | None = None
    domain_event_envelope: DomainEventEnvelope | None = None
    sse_cursor: SSECursor | None = None
    job_contract: JobContract | None = None
    artifact_ref: ArtifactRef | None = None
    evidence_ref: EvidenceRef | None = None
    provider_contract: ProviderContract | None = None
    ai_run_record: AIRunRecord | None = None
    tool_policy: ToolPolicy | None = None
    prompt_ref: PromptRef | None = None
    memory_record: MemoryRecord | None = None
    context_manifest: ContextManifest | None = None
    platform_design_token_contract: PlatformDesignTokenContract | None = None
    workspace_layout_contract: WorkspaceLayoutContract | None = None
    component_catalog_contract: ComponentCatalogContract | None = None
    frontend_state_and_copy_contract: FrontendStateAndCopyContract | None = None


__all__ = ["PlatformContractsBundle"]
