<template>
  <p class="user-text" data-testid="prep-card-user-text">{{ text }}</p>
</template>

<script setup lang="ts">
/** allowlisted `user_text`：纯文本，不渲染 HTML（props 由服务端 schema 校验过）。 */
defineProps<{ text?: string }>()
</script>

<style scoped>
.user-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--ma-text-primary);
  font-size: var(--ma-font-size-body);
}
</style>
