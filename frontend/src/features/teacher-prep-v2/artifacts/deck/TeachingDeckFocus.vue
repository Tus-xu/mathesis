<template>
  <section class="deck" data-testid="t24-teaching-deck-focus" aria-labelledby="deck-title">
    <header class="deck__header">
      <div>
        <span class="deck__eyebrow">{{ copyFor('prep.t24.deck.eyebrow') }}</span>
        <h2 id="deck-title">{{ title }}</h2>
      </div>
      <span class="deck__revision">{{ copyFor('prep.t24.revision.current') }} {{ revision }}</span>
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

    <p v-if="editing?.errorKey" class="deck__error" data-testid="t24-deck-error">{{ copyFor(editing.errorKey) }}</p>

    <div class="deck__body">
      <SlideNavigator
        :slides="navigatorSlides"
        :active-index="activeIndex"
        @select="activeIndex = $event"
      />
      <main class="deck__canvas" :aria-label="copyFor('prep.t24.aria.canvas')">
        <article v-if="activeSlide" class="slide" data-testid="t24-slide-canvas">
          <h3>{{ activeSlide.title }}</h3>
          <label class="slide__field">
            {{ copyFor('prep.t24.deck.content') }}
            <textarea v-model="contentDraft" :readonly="readOnly" data-testid="t24-slide-content" />
          </label>
          <label class="slide__notes">
            {{ copyFor('prep.t24.deck.notes') }}
            <textarea v-model="notesDraft" :readonly="readOnly" data-testid="t24-slide-notes" />
          </label>
          <div class="slide__actions">
            <button
              type="button"
              :disabled="readOnly || contentDraft === activeSlide.content"
              data-testid="t24-slide-content-propose"
              @click="proposeContent"
            >
              {{ copyFor('prep.t24.deck.propose_content') }}
            </button>
            <button
              type="button"
              :disabled="readOnly || notesDraft === activeSlide.notes"
              data-testid="t24-slide-propose"
              @click="proposeNotes"
            >
              {{ copyFor('prep.t24.deck.propose') }}
            </button>
          </div>

          <!-- 局部重生成：只针对**当前页**（服务端在 changed_slide_ids 上背书局部性） -->
          <div v-if="!readOnly" class="slide__regen" data-testid="t24-slide-regen">
            <input
              v-model="regenInstruction"
              class="slide__regen-input"
              :placeholder="copyFor('prep.t24.regen.placeholder')"
              data-testid="t24-slide-regen-instruction"
            />
            <button
              type="button"
              :disabled="busy || !regenInstruction.trim()"
              data-testid="t24-slide-regen-submit"
              @click="submitRegen"
            >
              {{ copyFor('prep.t24.regen.submit_page') }}
            </button>
          </div>
        </article>
        <p v-else class="deck__empty">{{ copyFor('prep.t24.deck.empty') }}</p>

        <SlideQaPanel
          :issues="qaIssues"
          :report="editing?.qa ?? null"
          :active-page-no="activeIndex + 1"
          :busy="busy"
          @fix="emit('fix-qa', $event)"
          @retry-page="emit('retry-page', $event)"
          @select-page="activeIndex = $event - 1"
        />
      </main>
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
import { computed, ref, watch } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import SlideNavigator, { type SlideSummary } from './SlideNavigator.vue'
import SlideQaPanel, { type QaIssue } from './SlideQaPanel.vue'
import LineagePanel from '../shared/LineagePanel.vue'
import ConflictResolver from '../shared/ConflictResolver.vue'
import RevisionToolbar from '../shared/RevisionToolbar.vue'
import ExportPanel, { type ExportCapability } from '../shared/ExportPanel.vue'
import type { ArtifactEditingView } from '@/features/teacher-prep-v2/surface/useArtifactEditing'
import type { PatchOperation, RevisionConflict } from '@/features/teacher-prep-v2/types/t24'

export interface DeckSlide extends SlideSummary { content: string; notes: string }

/** 冲突视图：优先 store 的 409 载荷，其次兼容旧的两数字形状。 */
export interface ConflictView extends Partial<RevisionConflict> {
  expectedRevision: number
  currentRevision: number
  reloadedAfterConflict?: boolean
}

const props = withDefaults(defineProps<{
  title: string
  revision: number
  slides: DeckSlide[]
  qaIssues: QaIssue[]
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
  editing?: ArtifactEditingView | null
}>(), { exportCapabilities: () => [], dirty: false, readOnly: false, stale: false, busy: false, conflict: null, editing: null })

const emit = defineEmits<{
  (e: 'patch', payload: { slideId: string; target: 'content' | 'notes'; before: string; after: string }): void
  (e: 'propose-operations', operations: PatchOperation[], slideId: string): void
  (e: 'propose'): void
  (e: 'fix-qa', issue: QaIssue): void
  (e: 'reload'): void
  (e: 'discard'): void
  (e: 'apply'): void
  (e: 'reject'): void
  (e: 'revert', revision: number): void
  (e: 'regen', payload: { kind: 'slide'; targetId: string; instruction: string }): void
  (e: 'retry-page', pageNo: number): void
  (e: 'compare-conflict'): void
  (e: 'conflict-reload'): void
  (e: 'conflict-reapply'): void
  (e: 'conflict-discard'): void
  (e: 'export', format: string): void
  (e: 'cancel-export', format: string): void
  (e: 'refresh-download', format: string): void
}>()

const activeIndex = ref(0)
const contentDraft = ref('')
const notesDraft = ref('')
const regenInstruction = ref('')

const activeSlide = computed(() => props.slides[activeIndex.value] ?? null)
watch(activeSlide, (slide) => {
  contentDraft.value = slide?.content ?? ''
  notesDraft.value = slide?.notes ?? ''
}, { immediate: true })

const conflictView = computed<ConflictView | null>(() => props.editing?.conflict ?? props.conflict)

/** 逐页 QA 标记：服务端报告优先（page_no = index + 1），否则用 deck 视图的 slide.qa_status。 */
const navigatorSlides = computed<SlideSummary[]>(() =>
  props.slides.map((slide, index) => {
    const fromReport = props.editing?.qaByPage?.[index + 1]
    const status = fromReport ?? slide.qaStatus ?? null
    return {
      id: slide.id,
      title: slide.title,
      qaStatus: status === 'pass' || status === 'fail' || status === 'unknown' ? status : null,
    }
  }),
)

function proposeContent() {
  if (!activeSlide.value) return
  const slideId = activeSlide.value.id
  emit('patch', { slideId, target: 'content', before: activeSlide.value.content, after: contentDraft.value })
  emit('propose-operations', [{
    op: 'set_slide',
    target: slideId,
    field: 'content',
    value: contentDraft.value,
    before: activeSlide.value.content,
  }], slideId)
}

function proposeNotes() {
  if (!activeSlide.value) return
  const slideId = activeSlide.value.id
  emit('patch', { slideId, target: 'notes', before: activeSlide.value.notes, after: notesDraft.value })
  emit('propose-operations', [{
    op: 'set_slide',
    target: slideId,
    field: 'speaker_note',
    value: notesDraft.value,
    before: activeSlide.value.notes,
  }], slideId)
}

function submitRegen() {
  if (!activeSlide.value || !regenInstruction.value.trim()) return
  emit('regen', { kind: 'slide', targetId: activeSlide.value.id, instruction: regenInstruction.value.trim() })
  regenInstruction.value = ''
}
</script>
<style scoped>
.deck { padding: var(--ma-space-4); }
.deck__header { display: flex; justify-content: space-between; align-items: baseline; }
.deck__eyebrow { color: var(--ma-action-primary); }
.deck h2 { margin: var(--ma-space-1) 0; }
.deck__revision { color: var(--ma-text-muted); }
.deck__error { color: var(--ma-status-danger); line-height: var(--ma-typography-body); }
.deck__body { min-height: 520px; display: grid; grid-template-columns: 210px minmax(0, 1fr); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); overflow: hidden; }
.deck__canvas { min-width: 0; background: var(--ma-surface-page); }
.slide { max-width: 760px; min-height: 300px; margin: var(--ma-space-5) auto; padding: var(--ma-space-5); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-default); }
.slide h3 { margin: 0 0 var(--ma-space-3); font-size: var(--ma-font-size-display); }
.slide__field, .slide__notes { display: flex; flex-direction: column; gap: var(--ma-space-1); }
.slide__field textarea, .slide__notes textarea { min-height: 96px; padding: var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-md); background: var(--ma-surface-subtle); color: var(--ma-text-body); font: inherit; }
.slide__notes { margin-top: var(--ma-space-4); color: var(--ma-text-muted); }
.slide__notes textarea { min-height: 64px; }
.slide__actions { display: flex; gap: var(--ma-space-2); margin-top: var(--ma-space-2); flex-wrap: wrap; }
.slide__regen { display: flex; gap: var(--ma-space-2); margin-top: var(--ma-space-3); padding-top: var(--ma-space-3); border-top: 1px solid var(--ma-surface-border); }
.slide__regen-input { flex: 1 1 auto; height: 30px; padding: 0 var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-primary); font: inherit; }
.slide button { margin-top: var(--ma-space-2); min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-action-primary); color: var(--ma-color-white-000); cursor: pointer; }
.slide__regen button { margin-top: 0; background: var(--ma-surface-default); color: var(--ma-action-primary); }
.slide button:disabled { cursor: not-allowed; opacity: .55; }
.deck__empty { padding: var(--ma-space-4); color: var(--ma-text-muted); }
@media (max-width: 800px) { .deck__body { grid-template-columns: 1fr; } .navigator { border-right: 0; border-bottom: 1px solid var(--ma-surface-border); } }
</style>
