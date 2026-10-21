/**
 * P01 · 平台内置组件目录条目。
 *
 * 组成原则（ADR-PLAT-10 + P00 `ComponentCatalogContract`）：
 * - 这里**只登记平台原语**（domain = 'platform'），不含 Teacher/Research 业务组件；
 * - 域组件（如 DiagnosticCard / ModelRouteArena）由**域 manifest** 在 P01 之后注册
 *   （见 `DOMAIN-REGISTRATION-BOUNDARY.md`）；
 * - 每个条目都必须有 fallback + accessibility，且 renderer 不得声明任意标记。
 *
 * 与 P00 fixture `component_catalog_platform.json` 的关系：
 * fixture 是**契约样例**（含 teacher 域示例条目，用于证明契约可跨域）；
 * 本文件是**实装目录**（P01 只装平台原语）。
 */
import type { CatalogEntry } from './index'

/** 便于书写的简写构造器（保持条目形状显式，不做魔法默认值）。 */
function platformPrimitive(
  component_type: string,
  component_name: string,
  opts: {
    role: string
    labelKey: string
    props?: Record<string, unknown>
    risk?: 'LOW' | 'MEDIUM' | 'HIGH'
    aiGeneratable?: boolean
    fallback?: 'TEXT_SUMMARY' | 'PLACEHOLDER' | 'HIDE'
  },
): CatalogEntry {
  return {
    component_type,
    version: '1.0.0',
    domain: 'platform',
    props_schema: {
      type: 'object',
      properties: opts.props ?? {},
      additionalProperties: true,
    },
    event_schema: { type: 'object' },
    risk: opts.risk ?? 'LOW',
    renderer: { kind: 'PLATFORM_PRIMITIVE', component_name },
    accessibility: { role: opts.role, label_i18n_key: opts.labelKey, keyboard_operable: true },
    fallback: { strategy: opts.fallback ?? 'TEXT_SUMMARY' },
    registered_by_manifest: 'platform-catalog@1.0.0',
    ai_generatable: opts.aiGeneratable ?? true,
  }
}

/**
 * 平台目录条目（P01 实装）。
 *
 * 覆盖 P01 指令点名的 8 类原语：
 * ConversationTimeline / ProposalCard / ConfirmCard / EvidenceRail /
 * SourceLocatorChip / ArtifactCanvas / JobStatusProgress / ErrorEmptyState
 */
export const PLATFORM_CATALOG_ENTRIES: CatalogEntry[] = [
  platformPrimitive('ConversationTimeline', 'ConversationTimeline', {
    role: 'log',
    labelKey: 'platform.conversation.timeline',
    props: { messages: { type: 'array' }, streaming: { type: 'boolean' } },
  }),
  platformPrimitive('ProposalCard', 'ProposalCard', {
    role: 'article',
    labelKey: 'platform.proposal.card',
    risk: 'MEDIUM',
    props: { proposal_id: { type: 'string' }, summary: { type: 'string' } },
  }),
  platformPrimitive('ConfirmCard', 'ConfirmCard', {
    role: 'group',
    labelKey: 'platform.confirm.card',
    risk: 'MEDIUM',
    props: { confirm_label_i18n_key: { type: 'string' }, risk: { type: 'string' } },
  }),
  platformPrimitive('EvidenceRail', 'EvidenceRail', {
    role: 'complementary',
    labelKey: 'platform.evidence.rail',
    props: { evidence_refs: { type: 'array' } },
  }),
  platformPrimitive('SourceLocatorChip', 'SourceLocatorChip', {
    role: 'note',
    labelKey: 'platform.source.locator',
    props: { source_id: { type: 'string' }, section_id: { type: 'string' }, chunk_id: { type: 'string' } },
  }),
  platformPrimitive('ArtifactCanvas', 'ArtifactCanvas', {
    role: 'region',
    labelKey: 'platform.artifact.canvas',
    props: { artifact_ref: { type: 'object' }, revision: { type: 'integer' } },
  }),
  platformPrimitive('JobStatusProgress', 'JobStatusProgress', {
    role: 'status',
    labelKey: 'platform.job.status',
    props: { status: { type: 'string' }, progress: { type: 'number' } },
  }),
  platformPrimitive('ErrorEmptyState', 'ErrorEmptyState', {
    role: 'alert',
    labelKey: 'platform.error.empty',
    props: { code: { type: 'string' }, message_i18n_key: { type: 'string' } },
  }),
]
