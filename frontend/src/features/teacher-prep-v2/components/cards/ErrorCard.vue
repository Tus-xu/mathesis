<template>
  <!-- allowlisted `error_card`：错误显式可见，带服务端标签；不静默吞掉。 -->
  <SurfaceCard class="error-card" test-id="prep-card-error" :title="copyFor('prep.error.server')">
    <p v-if="label" class="error-card__label">{{ label }}</p>
    <p v-if="message" class="error-card__message">{{ message }}</p>
    <template #footer>
      <ActionButton v-if="retryable" @click="$emit('retry')">{{ copyFor('common.retry') }}</ActionButton>
    </template>
  </SurfaceCard>
</template>

<script setup lang="ts">
import ActionButton from '../primitives/ActionButton.vue'
import SurfaceCard from '../primitives/SurfaceCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

withDefaults(
  defineProps<{
    label?: string | null
    message?: string | null
    retryable?: boolean
  }>(),
  { label: null, message: null, retryable: false },
)

defineEmits<{ retry: [] }>()
</script>

<style scoped>
.error-card__label {
  margin: 0 0 var(--ma-space-1);
  color: var(--ma-status-danger);
  font-size: var(--ma-font-size-body);
}

.error-card__message {
  margin: 0;
  color: var(--ma-text-body);
}
</style>
