<template>
  <nav class="rw-rail" aria-label="写作导航">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="rw-rail__item"
      :class="{ 'rw-rail__item--active': item.key === modelValue }"
      :aria-current="item.key === modelValue ? 'page' : undefined"
      @click="emit('update:modelValue', item.key)"
    >
      <component :is="item.icon" class="rw-rail__ico" aria-hidden="true" />
      <span class="rw-rail__label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
/**
 * WRITE 图标轨（VISUAL-SPEC §5.1）：宽 64px，bg `--rw-app-bg-alt`，右边框 1px。
 * 四项纵向排列：`项目`(默认选中，主蓝) / `文献` / `笔记` / `设置`；每项 20px 图标 + 11px 标签。
 *
 * 本轮仅「项目」有真实内容（文件树 + 论文结构）；其余三项**诚实置灰为后续能力**，
 * 点击仅切换选中态，不伪造内容（宪法 §70）。
 */
import type { Component } from 'vue'
import {
  AlbumsOutline,
  DocumentTextOutline,
  BookOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

export type RailKey = 'project' | 'literature' | 'notes' | 'settings'

defineProps<{ modelValue: RailKey }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: RailKey): void }>()

const items: { key: RailKey; label: string; icon: Component }[] = [
  { key: 'project', label: '项目', icon: AlbumsOutline },
  { key: 'literature', label: '文献', icon: DocumentTextOutline },
  { key: 'notes', label: '笔记', icon: BookOutline },
  { key: 'settings', label: '设置', icon: SettingsOutline },
]
</script>

<style scoped>
.rw-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: var(--rw-w-iconrail, 64px);
  flex: 0 0 var(--rw-w-iconrail, 64px);
  padding: 8px 0;
  background: var(--rw-app-bg-alt);
  border-right: var(--rw-border-w) solid var(--rw-border);
}
.rw-rail__item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 52px;
  padding: 8px 0 6px;
  border: 1px solid transparent;
  border-radius: 12px;
  background: transparent;
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  cursor: pointer;
  transition: all .16s ease;
}
.rw-rail__item:hover {
  background: var(--rw-surface);
  border-color: var(--rw-border);
  color: var(--rw-primary);
}
.rw-rail__ico {
  width: 20px;
  height: 20px;
}
.rw-rail__label {
  font-size: var(--rw-fs-xs);
  line-height: 1;
}
.rw-rail__item--active,
.rw-rail__item--active:hover {
  color: var(--rw-primary);
  background: var(--rw-surface);
  border-color: var(--rw-border);
  box-shadow: var(--rw-shadow-card);
}
</style>
