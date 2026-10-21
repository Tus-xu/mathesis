<template>
  <div class="assistant-text" data-testid="prep-card-assistant-text">
    <p class="assistant-text__body">{{ text }}</p>
    <p v-if="copyKey" class="assistant-text__hint">{{ copyFor(copyKey) }}</p>
  </div>
</template>

<script setup lang="ts">
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

/** allowlisted `assistant_text`：服务端文本，前端不做富文本解析。 */
defineProps<{ text?: string; copyKey?: string }>()
</script>

<style scoped>
.assistant-text__body {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ma-text-primary);
  font-size: var(--ma-font-size-body);
}

.assistant-text__hint {
  margin: var(--ma-space-1) 0 0;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}
</style>
