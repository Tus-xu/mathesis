<template>
  <section class="proposal" data-testid="t23-diagnostic-proposal">
    <header class="proposal__head">
      <div><span class="proposal__eyebrow">{{ copyFor('prep.t23.diagnostic.eyebrow') }}</span><h4>{{ text(proposal.title) }}</h4></div>
      <span class="proposal__status" data-testid="t23-diagnostic-pending">{{ copyFor('prep.t23.diagnostic.pending') }}</span>
    </header>
    <p v-if="text(proposal.rationale)" class="proposal__rationale">{{ text(proposal.rationale) }}</p>
    <ol class="proposal__questions"><li v-for="q in questions" :key="q.prompt">{{ q.prompt }}</li></ol>
    <footer class="proposal__footer">
      <span>{{ copyFor('prep.t23.revision_prefix') }} {{ revision }} {{ copyFor('prep.t23.revision_suffix') }}</span>
      <div class="proposal__actions">
        <button v-for="action in actions" :key="action" type="button" :data-testid="`t23-diagnostic-${action}`" @click="emitReview(action)">{{ copyFor(`prep.t23.action.${action}`) }}</button>
      </div>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

interface DiagnosticProposal { diagnostic_id?: unknown; status?: unknown; revision?: unknown; title?: unknown; rationale?: unknown; questions?: unknown; available_actions?: unknown }
const props = defineProps<{ proposal: DiagnosticProposal }>()
const emit = defineEmits<{ (event: 'review', payload: { action: string; target: string; expected_revision: number }): void }>()
const revision = computed(() => typeof props.proposal.revision === 'number' ? props.proposal.revision : 0)
const questions = computed(() => Array.isArray(props.proposal.questions) ? props.proposal.questions.filter((q): q is { prompt: string } => Boolean(q && typeof q === 'object' && typeof (q as { prompt?: unknown }).prompt === 'string')) : [])
const actions = computed(() => Array.isArray(props.proposal.available_actions) ? props.proposal.available_actions.filter((action): action is string => ['confirm', 'edit', 'reject', 'skip'].includes(String(action))).map(String) : [])
const text = (value: unknown) => typeof value === 'string' ? value : ''
function emitReview(action: string) {
  emit('review', { action, target: typeof props.proposal.diagnostic_id === 'string' ? props.proposal.diagnostic_id : '', expected_revision: revision.value })
}
</script>

<style scoped>
.proposal { padding: var(--ma-space-2) 0 0; border-top: 1px solid var(--ma-surface-border); color: var(--ma-text-body); }
.proposal__head, .proposal__footer { display: flex; align-items: baseline; justify-content: space-between; gap: var(--ma-space-2); }.proposal__eyebrow { color: var(--ma-action-primary); font-size: var(--ma-font-size-body); }.proposal h4 { margin: var(--ma-space-1) 0 0; color: var(--ma-text-primary); }.proposal__status { color: var(--ma-status-warning); }.proposal__rationale { line-height: var(--ma-typography-body); }.proposal__questions { padding-left: var(--ma-space-4); line-height: var(--ma-typography-body); }.proposal__footer { margin-top: var(--ma-space-3); color: var(--ma-text-muted); font-size: var(--ma-font-size-body); }.proposal__actions { display: flex; gap: var(--ma-space-1); flex-wrap: wrap; }.proposal__actions button { padding: var(--ma-space-1) var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-body); cursor: pointer; }
</style>
