<template>
  <div v-if="view" class="locator" data-testid="t23-direct-chunk-locator">
    <span class="locator__label">{{ copyFor('prep.t23.evidence.source') }}</span>
    <span v-if="view.sectionPath.length">{{ view.sectionPath.join(' / ') }}</span>
    <details>
      <summary>{{ copyFor('prep.t23.evidence.details') }}</summary>
      <span>{{ view.sourceId }} · {{ view.chunkId }}<template v-if="view.chunkOrdinal !== null"> · #{{ view.chunkOrdinal }}</template></span>
      <code>{{ view.contentHash }}</code>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

interface DirectChunkLocator {
  sourceId: string
  sectionPath: string[]
  chunkId: string
  chunkOrdinal: number | null
  contentHash: string
}

const props = defineProps<{ locator: Record<string, unknown> }>()
const view = computed<DirectChunkLocator | null>(() => {
  const sourceId = typeof props.locator.source_id === 'string' ? props.locator.source_id : ''
  const chunkId = typeof props.locator.chunk_id === 'string' ? props.locator.chunk_id : ''
  const contentHash = typeof props.locator.content_hash === 'string' ? props.locator.content_hash : ''
  if (!sourceId || !chunkId || !contentHash) return null
  return {
    sourceId,
    sectionPath: Array.isArray(props.locator.section_path)
      ? props.locator.section_path.filter((part): part is string => typeof part === 'string')
      : [],
    chunkId,
    chunkOrdinal: typeof props.locator.chunk_ordinal === 'number' ? props.locator.chunk_ordinal : null,
    contentHash,
  }
})
</script>

<style scoped>
.locator { display: flex; flex-wrap: wrap; align-items: center; gap: var(--ma-space-1) var(--ma-space-2); color: var(--ma-text-body); font-size: var(--ma-font-size-body); }
.locator__label { color: var(--ma-text-primary); font-weight: 600; }
.locator code { color: var(--ma-text-muted); font-family: var(--ma-font-family-mono); }
</style>
