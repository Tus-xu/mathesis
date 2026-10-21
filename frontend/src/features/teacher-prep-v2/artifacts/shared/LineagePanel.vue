<template>
  <details class="lineage" data-testid="t24-lineage-panel">
    <summary>{{ copyFor('prep.t24.lineage.title') }}</summary>
    <dl>
      <div v-if="lineage.blueprintRevision != null"><dt>{{ copyFor('prep.t24.lineage.blueprint') }}</dt><dd>{{ lineage.blueprintRevision }}</dd></div>
      <div v-if="lineage.specRevision != null"><dt>{{ copyFor('prep.t24.lineage.spec_revision') }}</dt><dd data-testid="t24-lineage-spec-revision">{{ lineage.specRevision }}</dd></div>
      <div v-if="lineage.artifactRevision != null"><dt>{{ copyFor('prep.t24.lineage.artifact') }}</dt><dd>{{ lineage.artifactRevision }}</dd></div>
      <div v-if="lineage.sourceLocator"><dt>{{ copyFor('prep.t24.lineage.source') }}</dt><dd>{{ lineage.sourceLocator }}</dd></div>
      <div v-if="lineage.createdByRunId"><dt>{{ copyFor('prep.t24.lineage.created_by_run') }}</dt><dd data-testid="t24-lineage-created-by-run">{{ lineage.createdByRunId }}</dd></div>
      <div v-else-if="lineage.runId"><dt>{{ copyFor('prep.t24.lineage.run') }}</dt><dd>{{ lineage.runId }}</dd></div>
      <div v-if="lineage.promptRef"><dt>{{ copyFor('prep.t24.lineage.prompt_ref') }}</dt><dd data-testid="t24-lineage-prompt-ref">{{ lineage.promptRef }}</dd></div>
      <div v-if="lineage.sources?.length"><dt>{{ copyFor('prep.t24.lineage.sources') }}</dt><dd><code data-testid="t24-lineage-sources">{{ lineage.sources.join(' · ') }}</code></dd></div>
      <div v-if="lineage.qaStatus"><dt>{{ copyFor('prep.t24.lineage.qa') }}</dt><dd data-testid="t24-lineage-qa">{{ lineage.qaStatus }}</dd></div>
      <div v-if="lineage.contentHash || lineage.hash"><dt>{{ copyFor('prep.t24.lineage.hash') }}</dt><dd><code data-testid="t24-lineage-hash">{{ lineage.contentHash || lineage.hash }}</code></dd></div>
    </dl>
    <p v-if="!hasDetail" class="lineage__empty">{{ copyFor('prep.t24.lineage.empty') }}</p>
  </details>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

/**
 * 血缘面板（INV-8）：source lineage / spec revision / content hash /
 * created_by_run / PromptRef / QA status 全部来自服务端，缺失即不显示（不填占位）。
 */
export interface LineageData {
  blueprintRevision?: number | null
  artifactRevision?: number | null
  sourceLocator?: string | null
  runId?: string | null
  hash?: string | null
  /** T24 追加：spec 的 revision（与 artifact revision 分开表述）。 */
  specRevision?: number | null
  /** 内容摘要（spec_sha256 / artifact content_sha256）。 */
  contentHash?: string | null
  createdByRunId?: string | null
  promptRef?: string | null
  qaStatus?: string | null
  /** 教材/证据引用（`lineage.evidence_refs` + `source_refs`，服务端原样）。 */
  sources?: string[]
}

const props = defineProps<{ lineage: LineageData }>()
const hasDetail = computed(() =>
  Object.values(props.lineage).some((value) => value !== null && value !== undefined && value !== ''),
)
</script>
<style scoped>
.lineage { margin-top: var(--ma-space-3); color: var(--ma-text-body); }
.lineage summary { cursor: pointer; color: var(--ma-action-primary); }
.lineage dl { display: grid; grid-template-columns: auto 1fr; gap: var(--ma-space-1) var(--ma-space-3); margin: var(--ma-space-2) 0 0; }
.lineage dt { color: var(--ma-text-muted); }
.lineage dd { margin: 0; overflow-wrap: anywhere; }
.lineage code { font-family: var(--ma-font-family-mono); }
.lineage__empty { color: var(--ma-text-muted); }
</style>
