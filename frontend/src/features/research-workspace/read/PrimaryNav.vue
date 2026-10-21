<template>
  <nav class="rw-nav" aria-label="一级导航">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="rw-nav__item"
      :class="{ 'rw-nav__item--active': item.key === modelValue }"
      :aria-current="item.key === modelValue ? 'page' : undefined"
      @click="emit('update:modelValue', item.key)"
    >
      <component :is="item.icon" class="rw-nav__ico" aria-hidden="true" />
      <span class="rw-nav__label">{{ item.label }}</span>
    </button>
  </nav>
</template>

<script setup lang="ts">
/**
 * 一级导航（VISUAL-SPEC §4.1-3）：4 项，每项 h 40px、radius 6、图标 18px + 标签 14/500。
 * 选中：bg `--rw-primary-soft`、文字与图标 `--rw-primary`；未选中：文字 `--rw-text-2`。
 * READ 默认选中「文献」。
 */
import type { Component } from 'vue'
import {
  DocumentTextOutline,
  FolderOpenOutline,
  LibraryOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

defineProps<{ modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', key: string): void }>()

interface NavItem {
  key: string
  label: string
  icon: Component
}

const items: NavItem[] = [
  { key: 'literature', label: '文献', icon: LibraryOutline },
  { key: 'notes', label: '笔记', icon: DocumentTextOutline },
  { key: 'project', label: '项目', icon: FolderOpenOutline },
  { key: 'settings', label: '设置', icon: SettingsOutline },
]
</script>

<style scoped>
.rw-nav {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.rw-nav__item {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 40px;
  padding: 0 8px;
  border: none;
  border-radius: 12px;
  background: transparent;
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h3);
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}
.rw-nav__item:hover {
  background: var(--rw-surface-2);
}
.rw-nav__item--active,
.rw-nav__item--active:hover {
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
  font-weight: 800;
}
/* 与学生端卡片同源的图标胶囊：线性图标 + 浅底 + 圆角 */
.rw-nav__ico {
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  padding: 6px;
  border: 1px solid var(--rw-border);
  border-radius: 9px;
  background: var(--rw-surface);
  color: var(--rw-text-2);
  transition: all .16s ease;
}
.rw-nav__item:hover .rw-nav__ico { border-color: var(--rw-primary-border, var(--rw-border-strong)); color: var(--rw-primary); }
.rw-nav__item--active .rw-nav__ico {
  border-color: transparent;
  background: var(--rw-surface);
  color: var(--rw-primary);
  box-shadow: var(--rw-shadow-card);
}
.rw-nav__label {
  min-width: 0;
  white-space: nowrap;
}
</style>
