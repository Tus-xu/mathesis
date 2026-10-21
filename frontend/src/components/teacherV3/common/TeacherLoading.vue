<template>
  <div class="tloading" role="status" :aria-label="text">
    <div class="tloading__spinner"></div>
    <span class="tloading__text">{{ text }}</span>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ text?: string }>(), { text: '加载中…' })
</script>

<style scoped>
.tloading {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px; padding: 48px 24px;
}
.tloading__spinner {
  width: 32px; height: 32px; border-radius: 50%;
  border: 3px solid var(--teacher-primary-light);
  border-top-color: var(--teacher-primary);
  animation: tloading-spin 0.8s linear infinite;
}
@keyframes tloading-spin { to { transform: rotate(360deg); } }
.tloading__text { font-size: 13px; color: var(--teacher-text-secondary); }
</style>
