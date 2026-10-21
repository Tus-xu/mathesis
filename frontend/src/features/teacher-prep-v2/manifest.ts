/**
 * T22 · teacher-prep-v2 域 manifest —— 通过 **P01 的三条通道**接入平台。
 *
 * 通道 1（布局）：只通过 `WorkspaceLayout` 的 slot（见 `components/PrepWorkspace.vue`）。
 * 通道 2（首页）：`registerWorkspaceHome({ workspace: 'teacher', ... })`。
 * 通道 3（组件）：`registerComponent(catalog, entry)`，**由域注册**，平台不内置 Teacher 组件。
 *
 * 领域组件（DiagnosticCard / ProfileCard / BlueprintDiff / ProposalCard …）留在 Teacher feature，
 * 通过**版本化** domain manifest 注册；不把 Teacher 业务 props 加进平台共享组件（任务书第 5a 条）。
 */
import {
  createCatalog,
  registerComponent,
  validateCatalog,
  type Catalog,
  type CatalogEntry,
} from '@/platform/component-catalog'
import { registerWorkspaceHome } from '@/platform/shell/workspaceManifest'
import type { ComponentType } from '@/features/teacher-prep-v2/types/workspace'

export const TEACHER_MANIFEST_ID = 'teacher-prep-v2@1.0.0'

/**
 * 后端 allowlist（snake_case，8100 `catalog.COMPONENT_CATALOG`）→ 平台 catalog（PascalCase）。
 * 后端类型是唯一真相；本表只做命名桥接，不新增业务语义。
 */
export const SNAKE_TO_CATALOG: Record<ComponentType, string> = {
  user_text: 'UserText',
  assistant_text: 'AssistantText',
  context_card: 'ContextCard',
  blueprint_card: 'BlueprintCard',
  evidence_rail: 'EvidenceRail',
  job_status_card: 'JobStatusCard',
  selection_card: 'SelectionCard',
  basic_mode_card: 'BasicModeCard',
  diagnostic_card: 'DiagnosticCard',
  error_card: 'ErrorCard',
  direct_chunk_locator: 'DirectChunkLocator',
  diagnostic_proposal: 'DiagnosticProposalCard',
  class_learning_profile: 'ClassLearningProfileCard',
  blueprint_revision: 'BlueprintRevisionCard',
  run_context_disclosure: 'RunContextDisclosure',
}

/** 平台共享原语（由 Teacher 复用，非 Teacher 业务组件）。 */
const PLATFORM_SHARED = ['ProposalCard', 'ConfirmCard', 'EvidenceRail', 'SourceLocatorChip', 'ArtifactCanvas', 'JobStatus', 'ErrorState'] as const

function entry(
  component_type: string,
  component_name: string,
  role: string,
  risk: 'LOW' | 'MEDIUM' | 'HIGH' = 'LOW',
  kind: 'PLATFORM_PRIMITIVE' | 'REGISTERED_DOMAIN_COMPONENT' | 'TEXT_FALLBACK' = 'REGISTERED_DOMAIN_COMPONENT',
): CatalogEntry {
  return {
    component_type,
    version: '1.0.0',
    domain: 'teacher',
    risk,
    renderer: {
      kind,
      component_name,
      arbitrary_html: false,
      arbitrary_css: false,
      arbitrary_js: false,
    },
    fallback: { strategy: 'TEXT_SUMMARY', message_i18n_key: 'prep.card.unknown' },
    accessibility: { role },
    props_schema: { type: 'object', additionalProperties: true },
    event_schema: { type: 'object', additionalProperties: true },
  } as unknown as CatalogEntry
}

/** Teacher 领域组件目录（版本化）。 */
export const TEACHER_CATALOG_ENTRIES: CatalogEntry[] = [
  entry('UserText', 'UserTextCard', 'article'),
  entry('AssistantText', 'AssistantTextCard', 'article'),
  entry('ContextCard', 'StructuredCard', 'region'),
  entry('BlueprintCard', 'StructuredCard', 'region'),
  entry('DiagnosticCard', 'StructuredCard', 'region'),
  entry('SelectionCard', 'StructuredCard', 'region'),
  entry('JobStatusCard', 'StructuredCard', 'status'),
  entry('EvidenceRail', 'EvidenceRail', 'complementary'),
  entry('BasicModeCard', 'BasicModeCard', 'form', 'MEDIUM'),
  entry('ErrorCard', 'ErrorCard', 'alert', 'MEDIUM'),
  entry('ProposalCard', 'ProposalCard', 'region'),
  entry('ConfirmCard', 'ConfirmCard', 'group'),
  entry('SourceLocatorChip', 'SourceLocatorChip', 'note'),
  entry('ArtifactCanvas', 'ArtifactCanvas', 'region'),
  entry('JobStatus', 'JobStatus', 'status'),
  entry('ErrorState', 'ErrorState', 'alert'),
  entry('DirectChunkLocator', 'DirectChunkLocator', 'note'),
  entry('DiagnosticProposalCard', 'DiagnosticProposalCard', 'region', 'MEDIUM'),
  entry('ClassLearningProfileCard', 'ClassLearningProfileCard', 'region'),
  entry('BlueprintRevisionCard', 'BlueprintRevisionCard', 'region', 'MEDIUM'),
  entry('RunContextDisclosure', 'RunContextDisclosure', 'note'),
]

/** 建立并注册 Teacher 组件目录；返回目录与校验结果（可审计）。 */
export function createTeacherCatalog(): { catalog: Catalog; errors: string[] } {
  const catalog = createCatalog('teacher-prep-v2-catalog', '1.0.0')
  const errors: string[] = []
  for (const e of TEACHER_CATALOG_ENTRIES) {
    if (!registerComponent(catalog, e)) errors.push(`注册被拒：${String(e.component_type)}`)
  }
  const check = validateCatalog([...catalog.entries.values()])
  errors.push(...check.errors)
  return { catalog, errors }
}

/**
 * 注册 Teacher workspace 首页 + 组件目录。
 * 冲突策略沿用平台：后注册者**不覆盖**（返回 false，可追溯）。
 */
export function registerTeacherPrepV2(): {
  homeRegistered: boolean
  catalog: Catalog
  errors: string[]
} {
  const homeRegistered = registerWorkspaceHome({
    workspace: 'teacher',
    path: '/teacher-v3/today',
    registeredBy: TEACHER_MANIFEST_ID,
  })
  const { catalog, errors } = createTeacherCatalog()
  return { homeRegistered, catalog, errors }
}

export { PLATFORM_SHARED }
