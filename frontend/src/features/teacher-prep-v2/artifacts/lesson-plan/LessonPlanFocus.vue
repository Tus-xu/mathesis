<template>
  <section class="lesson" data-testid="t24-lesson-plan-focus" aria-labelledby="lesson-title">
    <header class="lesson__header">
      <div>
        <span class="lesson__eyebrow">{{ copyFor('prep.t24.lesson.eyebrow') }}</span>
        <h2 id="lesson-title">{{ title }}</h2>
      </div>
      <span class="lesson__revision">{{ copyFor('prep.t24.revision.current') }} {{ revision }}</span>
    </header>

    <RevisionToolbar
      :revision="revision"
      :dirty="dirty"
      :read-only="readOnly"
      :stale="stale"
      :busy="busy"
      :revisions="editing?.revisions ?? []"
      :has-pending-proposal="editing?.hasPendingProposal ?? false"
      :pending-patch-id="editing?.pendingPatchId ?? null"
      :blocked-by-conflict="Boolean(editing?.conflict)"
      @propose="emit('propose')"
      @discard="emit('discard')"
      @reload="emit('reload')"
      @apply="emit('apply')"
      @reject="emit('reject')"
      @revert="emit('revert', $event)"
    />

    <p v-if="editing?.errorKey" class="lesson__error" data-testid="t24-lesson-error">
      {{ copyFor(editing.errorKey) }}<span v-if="editing.errorDetail"> · {{ editing.errorDetail }}</span>
    </p>

    <p v-if="!blocks.length" class="lesson__empty" data-testid="t24-lesson-empty">{{ copyFor('prep.t24.lesson.empty') }}</p>
    <div v-else class="lesson__blocks">
      <LessonPlanBlockEditor
        v-for="block in blocks"
        :key="block.id"
        v-bind="block"
        :block-id="block.id"
        :selected="selectedId === block.id"
        :read-only="readOnly"
        @select="selectedId = block.id"
        @propose="emit('patch', $event)"
        @propose-operations="onProposeOperations"
      />
    </div>

    <!-- 局部重生成：只针对**选中块**（INV-2 由服务端在 changed_block_ids 上背书） -->
    <div v-if="selectedId && !readOnly" class="lesson__regen" data-testid="t24-lesson-regen">
      <label :for="`t24-regen-${selectedId}`">{{ copyFormat('prep.t24.regen.target_prefix', selectedId) }}</label>
      <input
        :id="`t24-regen-${selectedId}`"
        v-model="regenInstruction"
        class="lesson__regen-input"
        :placeholder="copyFor('prep.t24.regen.placeholder')"
        data-testid="t24-lesson-regen-instruction"
      />
      <button
        type="button"
        :disabled="busy || !regenInstruction.trim()"
        data-testid="t24-lesson-regen-submit"
        @click="submitRegen"
      >
        {{ copyFor('prep.t24.regen.submit') }}
      </button>
    </div>

    <LineagePanel :lineage="editing?.lineage ?? lineage" />
    <ExportPanel
      :capabilities="editing?.exportCapabilities ?? exportCapabilities"
      @request="emit('export', $event)"
      @cancel="emit('cancel-export', $event)"
      @refresh-download="emit('refresh-download', $event)"
    />

    <ConflictResolver
      v-if="conflictView"
      :expected-revision="conflictView.expectedRevision"
      :current-revision="conflictView.currentRevision"
      :fields="conflictView.fields"
      :operations="conflictView.operations"
      :summary-zh="conflictView.summaryZh"
      :reloaded="conflictView.reloadedAfterConflict"
      :busy="busy"
      @reload="emit('conflict-reload')"
      @reapply="emit('conflict-reapply')"
      @discard="emit('conflict-discard')"
      @compare="emit('compare-conflict')"
    />
  </section>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'
import RevisionToolbar from '../shared/RevisionToolbar.vue'
import ConflictResolver from '../shared/ConflictResolver.vue'
import LineagePanel from '../shared/LineagePanel.vue'
import ExportPanel, { type ExportCapability } from '../shared/ExportPanel.vue'
import LessonPlanBlockEditor from './LessonPlanBlockEditor.vue'
import type { ArtifactEditingView } from '@/features/teacher-prep-v2/surface/useArtifactEditing'
import type { PatchOperation, RevisionConflict } from '@/features/teacher-prep-v2/types/t24'

export interface LessonBlock { id: string; title: string; kind?: string; content: string; sourceLocator?: string | null }

/** 冲突视图：优先来自 store 的 409 载荷，其次兼容旧的 `{expectedRevision, currentRevision}`。 */
export interface ConflictView extends Partial<RevisionConflict> {
  expectedRevision: number
  currentRevision: number
  reloadedAfterConflict?: boolean
}

const props = withDefaults(defineProps<{
  title: string
  revision: number
  blocks: LessonBlock[]
  exportCapabilities?: ExportCapability[]
  dirty?: boolean
  readOnly?: boolean
  stale?: boolean
  busy?: boolean
  lineage: {
    blueprintRevision?: number | null
    artifactRevision?: number | null
    sourceLocator?: string | null
    runId?: string | null
    hash?: string | null
    specRevision?: number | null
    contentHash?: string | null
    createdByRunId?: string | null
    promptRef?: string | null
    qaStatus?: string | null
  }
  conflict?: ConflictView | null
  /** T24 接线后的服务端事实/动作视图；为 null 时退回旧行为（兼容隔离挂载）。 */
  editing?: ArtifactEditingView | null
}>(), { exportCapabilities: () => [], dirty: false, readOnly: false, stale: false, busy: false, conflict: null, editing: null })

const emit = defineEmits<{
  (e: 'propose'): void
  (e: 'patch', payload: { blockId: string; before: string; after: string }): void
  (e: 'propose-operations', operations: PatchOperation[], blockId: string): void
  (e: 'discard'): void
  (e: 'reload'): void
  (e: 'apply'): void
  (e: 'reject'): void
  (e: 'revert', revision: number): void
  (e: 'regen', payload: { kind: 'block'; targetId: string; instruction: string }): void
  (e: 'compare-conflict'): void
  (e: 'conflict-reload'): void
  (e: 'conflict-reapply'): void
  (e: 'conflict-discard'): void
  (e: 'export', format: string): void
  (e: 'cancel-export', format: string): void
  (e: 'refresh-download', format: string): void
}>()

const selectedId = ref<string | null>(null)
const regenInstruction = ref('')

const conflictView = computed<ConflictView | null>(() => props.editing?.conflict ?? props.conflict)

function onProposeOperations(payload: { operations: PatchOperation[]; blockId: string }) {
  selectedId.value = payload.blockId
  emit('propose-operations', payload.operations, payload.blockId)
}

function submitRegen() {
  if (!selectedId.value || !regenInstruction.value.trim()) return
  emit('regen', { kind: 'block', targetId: selectedId.value, instruction: regenInstruction.value.trim() })
  regenInstruction.value = ''
}
</script>
<style scoped>
.lesson { max-width: 860px; margin: 0 auto; padding: var(--ma-space-4); }
.lesson__header { display: flex; align-items: baseline; justify-content: space-between; gap: var(--ma-space-3); }
.lesson__eyebrow { color: var(--ma-action-primary); font-size: var(--ma-font-size-body); }
.lesson h2 { margin: var(--ma-space-1) 0 0; color: var(--ma-text-primary); }
.lesson__revision { color: var(--ma-text-muted); }
.lesson__blocks { display: flex; flex-direction: column; gap: var(--ma-space-3); margin-top: var(--ma-space-3); }
.lesson__empty { color: var(--ma-text-muted); line-height: var(--ma-typography-body); }
.lesson__error { color: var(--ma-status-danger); line-height: var(--ma-typography-body); }
.lesson__regen { display: flex; align-items: center; gap: var(--ma-space-2); margin-top: var(--ma-space-3); padding: var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-subtle); flex-wrap: wrap; }
.lesson__regen label { color: var(--ma-text-muted); }
.lesson__regen-input { flex: 1 1 240px; height: 30px; padding: 0 var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-primary); font: inherit; }
.lesson__regen button { min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-action-primary); cursor: pointer; }
.lesson__regen button:disabled { cursor: not-allowed; opacity: .55; }
</style>
