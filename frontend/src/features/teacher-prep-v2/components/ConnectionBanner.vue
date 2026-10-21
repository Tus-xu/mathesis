<template>
  <!--
    T22 · 连接/一致性横幅（loading / reconnecting / stale / partial / forbidden / conflict）。
    状态来自 store（`streamState` + `phase` + `error`），**不来自定时器猜测**。
    403 场景下横幅提示"只读"，但内容区不隐藏（可见但禁用）。
  -->
  <div v-if="visible" class="banner" :class="`banner--${tone}`" role="status" :data-testid="`prep-banner-${tone}`">
    <StatusPill :tone="tone" :label="label" />
    <span v-if="detail" class="banner__detail">{{ detail }}</span>
    <ActionButton v-if="showReload" variant="ghost" test-id="prep-banner-reload" @click="$emit('reload')">
      {{ copyFor('common.retry') }}
    </ActionButton>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ActionButton from './primitives/ActionButton.vue'
import StatusPill from './primitives/StatusPill.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { NormalizedError, WorkspacePhase } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{
  phase: WorkspacePhase
  streamState: string
  error: NormalizedError | null
  gapCount: number
}>()

defineEmits<{ reload: [] }>()

type Tone = 'neutral' | 'running' | 'partial' | 'success' | 'warning' | 'danger'

const tone = computed<Tone>(() => {
  if (props.error?.kind === 'forbidden') return 'danger'
  if (props.error?.kind === 'conflict') return 'warning'
  if (props.error) return 'danger'
  if (props.streamState === 'reconnecting') return 'warning'
  if (props.phase === 'stale' || props.gapCount > 0) return 'partial'
  if (props.phase === 'loading') return 'running'
  return 'neutral'
})

const visible = computed(
  () =>
    props.phase === 'loading' ||
    props.phase === 'reconnecting' ||
    props.phase === 'stale' ||
    props.phase === 'partial' ||
    props.streamState === 'reconnecting' ||
    props.gapCount > 0 ||
    props.error !== null,
)

const label = computed(() => {
  if (props.error) return copyFor(props.error.copyKey)
  if (props.streamState === 'reconnecting') return copyFor('prep.state.reconnecting')
  if (props.phase === 'stale' || props.gapCount > 0) return copyFor('prep.state.stale')
  if (props.phase === 'loading') return copyFor('prep.state.loading')
  return copyFor('prep.state.partial')
})

const detail = computed(() => props.error?.detail ?? '')
const showReload = computed(() => props.error !== null || props.gapCount > 0)
</script>

<style scoped>
.banner {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  padding: var(--ma-space-1) var(--ma-space-4);
  border-bottom: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.banner--danger {
  background: var(--ma-surface-default);
  border-bottom-color: var(--ma-status-danger);
}

.banner--warning {
  background: var(--ma-surface-default);
  border-bottom-color: var(--ma-status-warning);
}

.banner__detail {
  color: var(--ma-text-muted);
}
</style>
