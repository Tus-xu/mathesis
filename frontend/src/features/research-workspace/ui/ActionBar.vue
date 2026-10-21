<template>
  <div class="rw-actions" role="group">
    <button
      v-for="a in actions"
      :key="a.key"
      type="button"
      class="rw-actions__btn"
      :class="`rw-actions__btn--${a.variant ?? 'outline'}`"
      :disabled="disabled"
      @click="emit('action', a.key)"
    >
      <component :is="a.icon" v-if="a.icon" class="rw-actions__icon" aria-hidden="true" />
      <span>{{ a.label }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 动作行（VISUAL-SPEC §4.4「动作行」/ 宪法 §32 → Agent 可产生 Action）。
 *
 * 统一渲染 `ResearchResult.actions[]` 映射而来的按钮组：主蓝实心 = 高优先动作
 * （如 `加入笔记` / `定位到第 N 行`），描边 = 次动作（如 `查看证据`）。
 * 所有会**修改论文**的动作（`apply_patch`）必须再经用户确认，不在此静默写入。
 */
import type { Component } from 'vue'

export interface AgentActionResult {
  key: string
  label: string
  variant?: 'primary' | 'outline'
  icon?: Component
}

withDefaults(
  defineProps<{
    actions: AgentActionResult[]
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ (e: 'action', key: string): void }>()
</script>

<style scoped>
.rw-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.rw-actions__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 14px;
  border-radius: var(--rw-radius);
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
  white-space: nowrap;
}
.rw-actions__btn:disabled {
  opacity: 0.55;
  cursor: default;
}
.rw-actions__icon {
  display: inline-flex;
  width: 16px;   /* §4.4 动作行按钮前图标 16px（原仅 font-size，@vicons <svg> 不受控） */
  height: 16px;
  font-size: 16px;
}
.rw-actions__btn--primary {
  border: var(--rw-border-w) solid var(--rw-primary);
  background: var(--rw-primary);
  color: var(--rw-surface);
}
.rw-actions__btn--primary:hover:not(:disabled) {
  background: var(--rw-primary-hover);
  border-color: var(--rw-primary-hover);
}
.rw-actions__btn--outline {
  border: var(--rw-border-w) solid var(--rw-border-strong);
  background: var(--rw-surface);
  color: var(--rw-text);
}
.rw-actions__btn--outline:hover:not(:disabled) {
  border-color: var(--rw-primary);
  color: var(--rw-primary);
}

/* ── WRITE 314px 紧凑态：动作按钮折行 + 收紧内距（VISUAL-SPEC §1.5）── */
@container rw-agent (max-width: 400px) {
  .rw-actions {
    gap: 6px;
  }
  .rw-actions__btn {
    height: 30px;
    padding: 0 10px;
    font-size: var(--rw-fs-sm);
  }
}
</style>
