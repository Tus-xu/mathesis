<template>
  <details class="context" data-testid="t23-run-context">
    <summary>{{ copyFor('prep.t23.context.title') }}</summary>
    <dl>
      <div v-if="view.promptRef"><dt>{{ copyFor('prep.t23.context.prompt') }}</dt><dd>{{ view.promptRef.id }} · {{ view.promptRef.version }}</dd></div>
      <div v-if="view.contextManifest"><dt>{{ copyFor('prep.t23.context.manifest') }}</dt><dd>{{ view.contextManifest }}</dd></div>
      <div v-if="view.evidenceRefs.length"><dt>{{ copyFor('prep.t23.context.evidence') }}</dt><dd>{{ view.evidenceRefs.join('、') }}</dd></div>
      <div v-if="view.model"><dt>{{ copyFor('prep.t23.context.model') }}</dt><dd>{{ view.model }}</dd></div>
      <div v-if="view.tools.length"><dt>{{ copyFor('prep.t23.context.tools') }}</dt><dd>{{ view.tools.join('、') }}</dd></div>
      <div v-if="view.cost"><dt>{{ copyFor('prep.t23.context.cost') }}</dt><dd>{{ view.cost.value }} {{ view.cost.unit }}</dd></div>
      <div v-if="view.traceId"><dt>{{ copyFor('prep.t23.context.trace') }}</dt><dd>{{ view.traceId }}</dd></div>
    </dl>
  </details>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
interface Context { prompt_ref?: unknown; context_manifest?: unknown; evidence_refs?: unknown; model?: unknown; tools?: unknown; cost?: unknown; trace_id?: unknown }
const props = defineProps<{ context: Context }>()
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
const view = computed(() => {
  const prompt = props.context.prompt_ref && typeof props.context.prompt_ref === 'object' ? props.context.prompt_ref as Record<string, unknown> : null
  const cost = props.context.cost && typeof props.context.cost === 'object' ? props.context.cost as Record<string, unknown> : null
  return {
    promptRef: prompt && typeof prompt.id === 'string' && typeof prompt.version === 'string' ? { id: prompt.id, version: prompt.version } : null,
    contextManifest: typeof props.context.context_manifest === 'string' ? props.context.context_manifest : '',
    evidenceRefs: strings(props.context.evidence_refs), model: typeof props.context.model === 'string' ? props.context.model : '', tools: strings(props.context.tools),
    cost: cost && typeof cost.unit === 'string' && typeof cost.value === 'number' ? { unit: cost.unit, value: cost.value } : null,
    traceId: typeof props.context.trace_id === 'string' ? props.context.trace_id : '',
  }
})
</script>
<style scoped>
.context { padding: var(--ma-space-2) 0; color: var(--ma-text-body); }.context summary { cursor: pointer; color: var(--ma-action-primary); }.context dl { display: grid; grid-template-columns: auto 1fr; gap: var(--ma-space-1) var(--ma-space-3); }.context dd { margin: 0; }.context dt { color: var(--ma-text-muted); }
</style>
