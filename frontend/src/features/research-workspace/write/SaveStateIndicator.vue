<template>
  <span class="rw-savestate" :class="`rw-savestate--${state}`" role="status" aria-live="polite">
    <span class="rw-savestate__dot" aria-hidden="true" />
    <span class="rw-savestate__text">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
/**
 * 保存态指示（VISUAL-SPEC §5.2 / 宪法 §20/§51）：`未保存 / 保存中 / 已保存 / 冲突`。
 * 四态**必须显式可见**，不得只靠颜色暗示。
 */
import { computed } from 'vue'
import type { SaveState } from '../stores/files'

const props = withDefaults(defineProps<{ state?: SaveState }>(), { state: 'idle' })

const label = computed(() => {
  switch (props.state) {
    case 'unsaved':
      return '未保存'
    case 'saving':
      return '保存中…'
    case 'saved':
      return '已保存'
    case 'conflict':
      return '冲突：已刷新最新版本'
    default:
      return '已保存'
  }
})
</script>

<style scoped>
.rw-savestate {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--rw-fs-sm);
  white-space: nowrap;
}
.rw-savestate__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}
.rw-savestate--saved { color: var(--rw-success); }
.rw-savestate--saving { color: var(--rw-primary); }
.rw-savestate--unsaved { color: var(--rw-warning); }
.rw-savestate--conflict { color: var(--rw-danger); }
.rw-savestate--idle { color: var(--rw-text-3); }
</style>
