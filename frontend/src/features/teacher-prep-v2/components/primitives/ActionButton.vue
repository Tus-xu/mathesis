<template>
  <!--
    T22 · 领域内共享原语：动作按钮。
    - 无权限 = **可见但禁用**（不隐藏），附 `aria-disabled` 与说明；
    - 主 CTA 使用平台主色；不使用通用紫色渐变 / 暗色发光。
  -->
  <button
    class="prep-btn"
    :class="[`prep-btn--${variant}`, { 'is-busy': busy }]"
    type="button"
    :disabled="disabled || busy"
    :aria-disabled="disabled || busy"
    :aria-busy="busy"
    :data-testid="testId"
    @click="$emit('click', $event)"
  >
    <slot />
  </button>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost'
    disabled?: boolean
    busy?: boolean
    testId?: string
  }>(),
  { variant: 'secondary', disabled: false, busy: false, testId: undefined },
)

defineEmits<{ click: [ev: MouseEvent] }>()
</script>

<style scoped>
.prep-btn {
  appearance: none;
  border: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  color: var(--ma-text-primary);
  border-radius: var(--ma-radius-btn);
  padding: 0 var(--ma-space-3);
  min-height: 32px;
  font-size: var(--ma-font-size-body);
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease;
}

.prep-btn--primary {
  background: var(--ma-action-primary);
  border-color: var(--ma-action-primary);
  color: var(--ma-color-white-000);
}

.prep-btn--ghost {
  background: transparent;
  border-color: transparent;
  color: var(--ma-text-body);
}

.prep-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.prep-btn:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: 2px;
}
</style>
