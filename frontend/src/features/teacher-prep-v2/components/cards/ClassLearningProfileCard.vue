<template>
  <section class="profile" data-testid="t23-learning-profile">
    <header><div><span class="profile__eyebrow">{{ copyFor('prep.t23.profile.eyebrow') }}</span><h4>{{ copyFor('prep.t23.profile.title') }}</h4></div><span v-if="view.confidence">{{ levelLabel(view.confidence) }}</span></header>
    <dl><div v-if="view.sampleSize !== null"><dt>{{ copyFor('prep.t23.profile.sample') }}</dt><dd>{{ view.sampleSize }}</dd></div><div v-if="view.coverage !== null"><dt>{{ copyFor('prep.t23.profile.coverage') }}</dt><dd>{{ Math.round(view.coverage * 100) }}%</dd></div><div v-if="view.window"><dt>{{ copyFor('prep.t23.profile.window') }}</dt><dd>{{ view.window }}</dd></div></dl>
    <p v-if="view.difficulty"><strong>{{ copyFor('prep.t23.profile.difficulty') }}</strong>：{{ levelLabel(view.difficulty) }}</p>
    <div v-if="view.misconceptions.length"><h5>{{ copyFor('prep.t23.profile.misconceptions') }}</h5><ul><li v-for="item in view.misconceptions" :key="item">{{ item }}</li></ul></div>
    <p v-if="view.unknown.length" class="muted">{{ copyFor('prep.t23.profile.unknown') }}：{{ view.unknown.join('、') }}</p>
    <p v-if="view.warnings.length" class="warning">{{ view.warnings.join('、') }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
interface Profile { sample_size?: unknown; window?: unknown; coverage?: unknown; confidence?: unknown; misconceptions?: unknown; difficulty?: unknown; unknown?: unknown; warnings?: unknown }
const props = defineProps<{ profile: Profile }>()
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
const view = computed(() => ({
  sampleSize: typeof props.profile.sample_size === 'number' ? props.profile.sample_size : null,
  window: typeof props.profile.window === 'string' ? props.profile.window : null,
  coverage: typeof props.profile.coverage === 'number' ? props.profile.coverage : null,
  confidence: typeof props.profile.confidence === 'string' || typeof props.profile.confidence === 'number' ? String(props.profile.confidence) : null,
  difficulty: typeof props.profile.difficulty === 'string' ? props.profile.difficulty : null,
  misconceptions: strings(props.profile.misconceptions), unknown: strings(props.profile.unknown), warnings: strings(props.profile.warnings),
}))
const levelLabel = (value: string) => ({ low: '较低', medium: '中等', high: '较高', low_confidence: '较低置信度', medium_confidence: '中等置信度', high_confidence: '较高置信度' }[value] ?? '待补充')
</script>
<style scoped>
.profile { padding: var(--ma-space-2) 0 0; border-top: 1px solid var(--ma-surface-border); color: var(--ma-text-body); }.profile header { display: flex; justify-content: space-between; align-items: baseline; }.profile__eyebrow { color: var(--ma-action-primary); font-size: var(--ma-font-size-body); }.profile h4 { margin: var(--ma-space-1) 0 var(--ma-space-2); color: var(--ma-text-primary); }.profile dl { display: grid; grid-template-columns: auto 1fr; gap: var(--ma-space-1) var(--ma-space-3); }.profile dd { margin: 0; }.profile dt, .muted { color: var(--ma-text-muted); }.profile h5 { margin-bottom: var(--ma-space-1); }.profile ul { margin: 0; padding-left: var(--ma-space-4); }.warning { color: var(--ma-status-warning); }
</style>
