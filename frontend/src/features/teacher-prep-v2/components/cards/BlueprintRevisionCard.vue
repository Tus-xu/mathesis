<template>
  <section class="revision" data-testid="t23-blueprint-revision">
    <header><div><span class="revision__eyebrow">{{ copyFor('prep.t23.revision.eyebrow') }}</span><h4>{{ targetLabel(revision.target) }}</h4></div><span v-if="typeof revision.confidence === 'number'">{{ Math.round(revision.confidence * 100) }}%</span></header>
    <p>{{ text(revision.reason) }}</p>
    <div class="revision__diff"><div><strong>{{ copyFor('prep.diff.before') }}</strong>{{ text(revision.before) }}</div><div><strong>{{ copyFor('prep.diff.after') }}</strong>{{ text(revision.after) }}</div></div>
    <ul v-if="limitations.length"><li v-for="item in limitations" :key="item">{{ item }}</li></ul>
    <footer><span>{{ copyFor('prep.t23.evidence.links') }} {{ evidenceLinks.join('、') || copyFor('prep.t23.evidence.none') }}</span></footer>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
interface Revision { target?: unknown; before?: unknown; after?: unknown; reason?: unknown; confidence?: unknown; limitations?: unknown; evidence_links?: unknown; expected_revision?: unknown }
const props = defineProps<{ revision: Revision }>()
defineEmits<{ (event: 'conflict', payload: { status: 409; recoverable: true }): void }>()
const text = (value: unknown) => typeof value === 'string' ? value : ''
const targetLabel = (value: unknown) => ({ lesson_flow: '教学环节', objectives: '教学目标', assessment: '评价方式' }[text(value)] ?? (text(value) || '教学方案'))
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
const limitations = computed(() => strings(props.revision.limitations))
const evidenceLinks = computed(() => strings(props.revision.evidence_links))
</script>
<style scoped>
.revision { padding: var(--ma-space-2) 0 0; border-top: 1px solid var(--ma-surface-border); color: var(--ma-text-body); }.revision header, .revision footer { display: flex; justify-content: space-between; align-items: baseline; gap: var(--ma-space-2); }.revision__eyebrow { color: var(--ma-action-primary); font-size: var(--ma-font-size-body); }.revision h4 { margin: var(--ma-space-1) 0 0; color: var(--ma-text-primary); }.revision__diff { display: grid; gap: var(--ma-space-2); }.revision__diff div { display: flex; gap: var(--ma-space-2); line-height: var(--ma-typography-body); }.revision__diff strong { min-width: 42px; color: var(--ma-text-muted); }.revision footer { color: var(--ma-text-muted); font-size: var(--ma-font-size-body); }.revision button { border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-body); cursor: pointer; }
</style>
