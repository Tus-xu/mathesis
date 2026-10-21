<template>
  <span class="rw-pill" :class="`rw-pill--${tone}`">
    <component :is="icon" v-if="icon" class="rw-pill__icon" aria-hidden="true" />
    <span class="rw-pill__text">{{ text }}</span>
  </span>
</template>

<script setup lang="ts">
/**
 * 状态药丸（VISUAL-SPEC §4.4「结果卡内容结构 · 状态行」/ 宪法 §35/§59）。
 *
 * 统一 Result Design System 的状态原子：h24 / radius12 / 语义 soft 底 + 语义字色。
 * 只接受语义 tone（success | warning | danger | info），**不接受字面色值**，
 * 颜色一律来自 `--rw-*` token（VISUAL-SPEC §7.9）。
 */
import type { Component } from 'vue'

export type StatusTone = 'success' | 'warning' | 'danger' | 'info'

withDefaults(
  defineProps<{
    /** 语义色调 */
    tone?: StatusTone
    /** 文案（中文状态词，如「基本成立」） */
    text: string
    /** 可选前置小图标（如 ✓ / !） */
    icon?: Component
  }>(),
  {
    tone: 'info',
    icon: undefined,
  },
)
</script>

<style scoped>
.rw-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  /* 黄金图实测状态药丸 ≈28px 高、字 13px（原 24px/12px 偏小） */
  height: 28px;
  padding: 0 11px;
  border-radius: 14px;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  line-height: 1;
  white-space: nowrap;
}
.rw-pill__icon {
  display: inline-flex;
  width: 16px;
  height: 16px;
  font-size: 16px;
}
.rw-pill--success {
  background: var(--rw-success-soft);
  color: var(--rw-success);
}
.rw-pill--warning {
  background: var(--rw-warning-soft);
  color: var(--rw-warning);
}
.rw-pill--danger {
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
}
.rw-pill--info {
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
}
</style>
