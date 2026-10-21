<template>
  <!--
    T22.3-C · 聚焦式单状态页（UX-CONTRACT E）。
    铁律：同一时刻**只有这里**是主错误呈现；不得同时渲染三栏骨架或「已就绪」文案。
  -->
  <div class="focused" role="alert" data-testid="prep-primary-error">
    <div class="focused__inner">
      <div class="focused__icon" aria-hidden="true">
        <n-icon :size="26"><component :is="icon" /></n-icon>
      </div>
      <h1 class="focused__title" data-testid="prep-focused-title">{{ title }}</h1>
      <p class="focused__desc">{{ description }}</p>

      <div class="focused__actions">
        <n-button
          v-for="a in actions"
          :key="a.label"
          :type="a.primary ? 'primary' : 'default'"
          :data-testid="a.testId"
          @click="a.run"
        >
          {{ a.label }}
        </n-button>
      </div>

      <p v-if="requestId" class="focused__meta">request_id: {{ requestId }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  CloudOfflineOutline,
  LockClosedOutline,
  RefreshOutline,
  SearchOutline,
} from '@vicons/ionicons5'
import { NButton, NIcon } from 'naive-ui'

import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { UiProjection } from '@/features/teacher-prep-v2/projection/types'

/** 聚焦式错误种类：与 `UiProjection['focusedError']` 同源，避免出现第二套枚举。 */
export type FocusedError = NonNullable<UiProjection['focusedError']>

const props = defineProps<{
  kind: FocusedError
  detail?: string | null
  requestId?: string | null
}>()

const emit = defineEmits<{
  (e: 'signin'): void
  (e: 'back-to-list'): void
  (e: 'retry'): void
  (e: 'view-revision'): void
}>()

const icon = computed(() => {
  switch (props.kind) {
    case 'unauthenticated':
      return LockClosedOutline
    case 'forbidden':
      return LockClosedOutline
    case 'not_found':
    case 'gone':
      return SearchOutline
    case 'conflict':
      return RefreshOutline
    default:
      return CloudOfflineOutline
  }
})

const title = computed(() => focusedTitle(props.kind))
const description = computed(() => focusedDescription(props.kind, props.detail ?? null))

/** 403 的关键保证：**绝不**出现「已就绪」类措辞。 */
function focusedTitle(kind: FocusedError): string {
  switch (kind) {
    case 'unauthenticated':
      return copyFor('prep.focused.unauthenticated.title')
    case 'forbidden':
      return copyFor('prep.focused.forbidden.title')
    case 'not_found':
      return copyFor('prep.focused.not_found.title')
    case 'gone':
      return copyFor('prep.focused.gone.title')
    case 'conflict':
      return copyFor('prep.focused.conflict.title')
    default:
      return copyFor('prep.focused.fallback.title')
  }
}

function focusedDescription(kind: FocusedError, detail: string | null): string {
  const tail = detail ? `（${detail}）` : ''
  switch (kind) {
    case 'unauthenticated':
      return copyFor('prep.focused.unauthenticated.desc')
    case 'forbidden':
      return `${copyFor('prep.focused.forbidden.desc')}${tail}`
    case 'not_found':
      return copyFor('prep.focused.not_found.desc')
    case 'gone':
      return copyFor('prep.focused.gone.desc')
    case 'conflict':
      return copyFor('prep.focused.conflict.desc')
    default:
      return `${copyFor('prep.focused.fallback.desc')}${tail}`
  }
}

const actions = computed(() => {
  switch (props.kind) {
    case 'unauthenticated':
      return [
        {
          label: copyFor('prep.focused.unauthenticated.action'),
          primary: true,
          testId: 'prep-focused-signin',
          run: () => emit('signin'),
        },
      ]
    case 'forbidden':
      return [
        {
          label: copyFor('prep.focused.forbidden.action'),
          primary: true,
          testId: 'prep-focused-back',
          run: () => emit('back-to-list'),
        },
      ]
    case 'not_found':
    case 'gone':
      return [
        {
          label: copyFor('prep.focused.back_prep'),
          primary: true,
          testId: 'prep-focused-back',
          run: () => emit('back-to-list'),
        },
      ]
    case 'conflict':
      return [
        {
          label: copyFor('prep.focused.conflict.refresh'),
          primary: true,
          testId: 'prep-focused-refresh',
          run: () => emit('view-revision'),
        },
        {
          label: copyFor('prep.focused.retry'),
          primary: false,
          testId: 'prep-focused-retry',
          run: () => emit('retry'),
        },
      ]
    default:
      return [
        {
          label: copyFor('prep.focused.retry'),
          primary: true,
          testId: 'prep-focused-retry',
          run: () => emit('retry'),
        },
      ]
  }
})
</script>

<style scoped>
.focused {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: var(--ma-space-6);
  background: var(--ma-surface-page);
}

.focused__inner {
  max-width: 520px;
  text-align: center;
  padding: var(--ma-space-6);
  background: var(--ma-surface-default);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-lg);
  box-shadow: var(--ma-elevation-card);
}

.focused__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  margin-bottom: var(--ma-space-3);
  border-radius: var(--ma-radius-full);
  background: var(--ma-action-primary-soft);
  color: var(--ma-action-primary);
}

.focused__title {
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-title);
  color: var(--ma-text-primary);
}

.focused__desc {
  margin: 0 0 var(--ma-space-4);
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-body);
}

.focused__actions {
  display: flex;
  gap: var(--ma-space-2);
  justify-content: center;
  flex-wrap: wrap;
}

.focused__meta {
  margin: var(--ma-space-4) 0 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  font-family: var(--ma-font-family-mono);
}

@media (prefers-reduced-motion: no-preference) {
  .focused__inner {
    animation: focused-in 200ms ease-out;
  }
}

@keyframes focused-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
