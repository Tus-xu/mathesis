<template>
  <!--
    T22 · 领域内共享原语：状态标签。
    无障碍（任务书第 10 条）：**不仅靠颜色表意** —— 始终同时给出文字与形状标记，
    颜色只是强化。`role="status"` 让读屏在状态变化时播报。
  -->
  <span class="prep-pill" :class="`prep-pill--${tone}`" role="status" :data-testid="testId">
    <span class="prep-pill__mark" aria-hidden="true">{{ mark }}</span>
    <span class="prep-pill__text">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    tone: 'neutral' | 'running' | 'partial' | 'success' | 'warning' | 'danger' | 'ai'
    label: string
    testId?: string
  }>(),
  { testId: undefined },
)

/** 形状标记：色盲/单色环境下仍可区分（不依赖颜色）。 */
const MARK: Record<typeof props.tone, string> = {
  neutral: '·',
  running: '◌',
  partial: '◐',
  success: '✓',
  warning: '!',
  danger: '×',
  ai: '✦',
}

const mark = computed(() => MARK[props.tone])
</script>

<style scoped>
.prep-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--ma-space-1);
  padding: 0 var(--ma-space-2);
  border-radius: var(--ma-radius-tag);
  font-size: var(--ma-font-size-body);
  line-height: 1.8;
  border: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
}

.prep-pill__mark {
  font-family: var(--ma-font-family-mono);
}

.prep-pill--running {
  background: var(--ma-action-primary-soft);
  color: var(--ma-action-primary);
  border-color: var(--ma-action-primary);
}

.prep-pill--partial {
  background: var(--ma-surface-subtle);
  color: var(--ma-text-primary);
  border-color: var(--ma-surface-border-strong);
}

.prep-pill--success {
  background: var(--ma-surface-default);
  color: var(--ma-status-success);
  border-color: var(--ma-status-success);
}

.prep-pill--warning {
  background: var(--ma-surface-default);
  color: var(--ma-status-warning);
  border-color: var(--ma-status-warning);
}

.prep-pill--danger {
  background: var(--ma-surface-default);
  color: var(--ma-status-danger);
  border-color: var(--ma-status-danger);
}

.prep-pill--ai {
  background: var(--ma-surface-default);
  color: var(--ma-status-ai);
  border-color: var(--ma-status-ai);
}
</style>
