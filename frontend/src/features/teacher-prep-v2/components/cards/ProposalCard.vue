<template>
  <!--
    T22 · typed proposal card（蓝图 / 诊断提案）。
    - 内容来自服务端 typed props（allowlisted），前端不渲染任意 HTML/CSS/JS；
    - 动作来自服务端 `available_actions`（copy_key 走 i18n），前端不发明按钮；
    - 无权限 = 可见但禁用（ConfirmCard 的 disabled 直接来自服务端）。
  -->
  <SurfaceCard
    class="proposal"
    :test-id="`prep-proposal-${proposalKind}`"
    :title="title"
  >
    <StatusPill :tone="tone" :label="statusLabel" />

    <div class="proposal__body">
      <slot />
    </div>

    <ConfirmCard
      :actions="actions"
      :busy="busy"
      @run="(a, t) => $emit('run', a, t)"
    />
  </SurfaceCard>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ConfirmCard from './ConfirmCard.vue'
import StatusPill from '../primitives/StatusPill.vue'
import SurfaceCard from '../primitives/SurfaceCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActionView } from '@/features/teacher-prep-v2/types/workspace'

const props = withDefaults(
  defineProps<{
    proposalKind: 'blueprint' | 'diagnostic'
    title?: string
    status?: string | null
    actions: ActionView[]
    busy?: boolean
  }>(),
  { title: '', status: null, busy: false },
)

defineEmits<{ run: [action: string, target: string | null] }>()

/** 提案状态基调：proposal/draft 为"待确认"，不算完成。 */
const tone = computed(() => {
  const s = (props.status ?? '').toLowerCase()
  if (s === 'confirmed' || s === 'published') return 'success' as const
  if (s === 'failed') return 'danger' as const
  if (s === 'rejected' || s === 'cancelled' || s === 'skipped') return 'neutral' as const
  return 'partial' as const
})

const statusLabel = computed(() => copyFor(`prep.job.${props.status ?? ''}`))
</script>

<style scoped>
.proposal__body {
  margin: var(--ma-space-2) 0;
}
</style>
