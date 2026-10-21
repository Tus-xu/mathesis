<template>
  <!--
    T22 · 错误态（401 / 403 / 409 / 网络 / 服务端）。
    - 403 = **可见但禁用**，不隐藏内容（任务书第 7 条的"无权限"语义）；
    - 409 = 提示已在别处更新并给出"重新载入"（走服务端权威，不本地合并）；
    - 文案一律来自 copy catalog，业务细节由服务端 `detail` 提供。
  -->
  <SurfaceCard
    v-if="error"
    class="error-state"
    :test-id="`prep-error-${error.kind}`"
    :title="copyFor(error.copyKey)"
  >
    <p v-if="error.detail" class="error-state__detail">{{ error.detail }}</p>
    <template #footer>
      <ActionButton
        v-if="error.kind === 'conflict' || error.kind === 'error' || error.kind === 'network'"
        variant="primary"
        @click="$emit('retry')"
      >
        {{ copyFor('common.retry') }}
      </ActionButton>
      <ActionButton v-if="error.kind === 'unauthenticated'" variant="primary" @click="$emit('signin')">
        {{ copyFor('prep.error.unauthenticated') }}
      </ActionButton>
    </template>
  </SurfaceCard>
</template>

<script setup lang="ts">
import ActionButton from './primitives/ActionButton.vue'
import SurfaceCard from './primitives/SurfaceCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { NormalizedError } from '@/features/teacher-prep-v2/types/workspace'

defineProps<{ error: NormalizedError | null }>()
defineEmits<{ retry: []; signin: [] }>()
</script>

<style scoped>
.error-state__detail {
  margin: 0;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}
</style>
