<template>
  <div
    class="rw-selbar"
    role="toolbar"
    aria-label="选区操作"
    :style="{ left: `${left}px`, top: `${top}px` }"
    @mousedown.prevent
  >
    <button type="button" class="rw-selbar__btn rw-selbar__btn--primary" @click="emit('judge')">
      <CheckmarkCircle aria-hidden="true" />
      <span>评判</span>
    </button>
    <button type="button" class="rw-selbar__btn" @click="emit('explain')">
      <ChatbubbleEllipsesOutline aria-hidden="true" />
      <span>解释</span>
    </button>
    <button type="button" class="rw-selbar__btn" @click="emit('evidence')">
      <SearchOutline aria-hidden="true" />
      <span>找证据</span>
    </button>
    <button type="button" class="rw-selbar__btn" @click="emit('note')">
      <CreateOutline aria-hidden="true" />
      <span>笔记</span>
    </button>
    <button type="button" class="rw-selbar__btn" @click="emit('cite')">
      <LinkOutline aria-hidden="true" />
      <span>引用</span>
    </button>
    <button type="button" class="rw-selbar__icon" title="高亮选中文字" aria-label="高亮选中文字" @click="emit('highlight')">
      <BrushOutline aria-hidden="true" />
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 选区浮动工具条（2026-10-21 体验重构）。
 *
 * 只承载“动词”，不在这里出结果：评判/解释/找证据/笔记/引用/高亮。
 * 长结果统一进右侧 Agent 对话线程，并带原文锚点回跳。
 */
import {
  BrushOutline,
  ChatbubbleEllipsesOutline,
  CheckmarkCircle,
  CreateOutline,
  LinkOutline,
  SearchOutline,
} from '@vicons/ionicons5'

withDefaults(defineProps<{ left: number; top: number }>(), { left: 0, top: 0 })

const emit = defineEmits<{
  (e: 'judge'): void
  (e: 'explain'): void
  (e: 'evidence'): void
  (e: 'note'): void
  (e: 'cite'): void
  (e: 'highlight'): void
}>()
</script>

<style scoped>
.rw-selbar {
  position: absolute;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 2px;
  height: 34px;
  padding: 3px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: 999px;
  background: var(--rw-surface);
  box-shadow: 0 10px 28px rgba(15, 23, 42, .16);
  transform: translateX(-50%);
  white-space: nowrap;
}
.rw-selbar__btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 8px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: 10.5px;
  font-weight: 600;
  cursor: pointer;
}
.rw-selbar__btn:hover {
  background: var(--rw-surface-2);
  color: var(--rw-text);
}
.rw-selbar__btn :deep(svg) {
  width: 13px;
  height: 13px;
}
.rw-selbar__btn--primary,
.rw-selbar__btn--primary:hover {
  background: var(--gradient-brand);
  color: var(--rw-surface);
  box-shadow: 0 4px 12px -8px rgba(79, 70, 229, .6);
}
.rw-selbar__icon {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--rw-text-3);
  cursor: pointer;
}
.rw-selbar__icon:hover { background: var(--rw-surface-2); color: var(--rw-primary); }
.rw-selbar__icon :deep(svg) { width: 13px; height: 13px; }
</style>
