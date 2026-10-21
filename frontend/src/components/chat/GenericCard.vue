<template>
  <!-- 未知 card 类型兜底：通用 JSON 折叠卡，绝不崩 -->
  <details class="generic-card">
    <summary>🧩 卡片：{{ cardType }}</summary>
    <pre>{{ pretty }}</pre>
  </details>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  card: { type: Object, required: true },
})

const cardType = computed(() => props.card.type || props.card.card_type || '未知类型')
const pretty = computed(() => {
  try {
    return JSON.stringify(props.card, null, 2)
  } catch {
    return String(props.card)
  }
})
</script>

<style scoped>
.generic-card {
  margin-top: 8px; border: 1px dashed var(--border); border-radius: var(--radius-md);
  padding: 8px 12px; font-size: 12px; color: var(--text-secondary); background: rgba(241, 245, 249, 0.5);
}
.generic-card summary { cursor: pointer; font-weight: 600; }
.generic-card pre {
  margin-top: 8px; max-height: 240px; overflow: auto; white-space: pre-wrap;
  word-break: break-all; font-size: 11px; color: var(--text-muted);
}
</style>
