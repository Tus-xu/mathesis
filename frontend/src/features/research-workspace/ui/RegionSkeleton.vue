<template>
  <div class="rw-skel" role="status" aria-busy="true" aria-live="polite">
    <span class="rw-skel__sr">{{ label }}</span>
    <span
      v-for="i in rowCount"
      :key="i"
      class="rw-skel__row"
      :style="{ width: rowWidths[(i - 1) % rowWidths.length] }"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * 区域级骨架（VISUAL-SPEC §6：**禁止整页 spinner**，各区域独立 loading）。
 *
 * 只表达「本区域正在加载」，尺寸随容器自适应；本组件本身不发起任何请求。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    /** 骨架行数 */
    rows?: number
    /** 无障碍文案（默认「内容加载中」） */
    label?: string
  }>(),
  {
    rows: 4,
    label: '内容加载中',
  },
)

const rowCount = computed(() => Math.max(1, Math.min(12, Math.floor(props.rows))))
const rowWidths = ['100%', '86%', '94%', '72%', '80%']
</script>

<style scoped>
.rw-skel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}
.rw-skel__sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.rw-skel__row {
  display: block;
  height: 12px;
  border-radius: var(--rw-radius-sm);
  background: linear-gradient(90deg, var(--rw-inset) 0%, var(--rw-surface-2) 50%, var(--rw-inset) 100%);
  background-size: 200% 100%;
  animation: rw-skel-shimmer 1.2s ease-in-out infinite;
}
@keyframes rw-skel-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@media (prefers-reduced-motion: reduce) {
  .rw-skel__row { animation: none; }
}
</style>
