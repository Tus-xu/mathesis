<script setup lang="ts">
/**
 * 教案左大纲栏（黄金图 211px）：标题 + 六章导航（图标 + 文字，激活项浅蓝胶囊 + 蓝字）+ 底部收起。
 * 点击 = 平滑滚动到对应章节（由父组件承接）；激活态与文档滚动双向联动。
 */
import { PLAN_CHAPTERS, type ChapterKey } from './usePlanEditor'

defineProps<{ active: ChapterKey | null }>()
const emit = defineEmits<{ (e: 'select', key: ChapterKey): void; (e: 'collapse'): void }>()

const CHAPTER_ICON: Record<ChapterKey, string> = {
  objectives:
    'M8 2.2a5.8 5.8 0 1 0 0 11.6A5.8 5.8 0 0 0 8 2.2Zm0 4.1a1.7 1.7 0 1 1 0 3.4 1.7 1.7 0 0 1 0-3.4Z',
  key_points: 'M8 2.4a5.6 5.6 0 1 0 0 11.2A5.6 5.6 0 0 0 8 2.4ZM7.3 5h1.4v4H7.3V5Zm0 5h1.4v1.4H7.3V10Z',
  process:
    'M3.4 3.2h9.2a1 1 0 0 1 1 1v7.6a1 1 0 0 1-1 1H3.4a1 1 0 0 1-1-1V4.2a1 1 0 0 1 1-1Zm1.3 2.6h4.1v1H4.7v-1Zm0 2.6h6.6v1H4.7v-1Zm0 2.6h6.6v1H4.7v-1Z',
  board:
    'M11.2 2.6 13.4 4.8 6.6 11.6 4.2 12.2 4 12.4l.6-2.4 6.6-6.6Zm-1 2-5.3 5.3-.2.9.9-.2 5.3-5.3-.7-.7Z',
  homework:
    'M4.2 2h5.1l2.5 2.5V14H4.2a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1Zm4.6 1.2V5h1.8L8.8 3.2ZM5.4 7h5.2v1H5.4V7Zm0 2.4h5.2v1H5.4v-1Z',
  reflection:
    'M8 1.8a4.2 4.2 0 0 0-2.6 7.5c.4.4.6.7.7 1.2h3.8c.1-.5.3-.8.7-1.2A4.2 4.2 0 0 0 8 1.8ZM5.9 11.8h4.2v1H5.9v-1Zm.7 1.9h2.8v.7H6.6v-.7Z',
}
</script>

<template>
  <nav class="pc-outline" aria-label="教案大纲">
    <h2 class="pc-outline__title">教案大纲</h2>
    <ul class="pc-outline__nav">
      <li v-for="c in PLAN_CHAPTERS" :key="c.key">
        <button
          type="button"
          class="pc-outline__item"
          :class="{ 'is-active': active === c.key }"
          :aria-current="active === c.key ? 'true' : undefined"
          @click="emit('select', c.key)"
        >
          <svg class="pc-outline__icon" viewBox="0 0 16 16" aria-hidden="true">
            <path :d="CHAPTER_ICON[c.key]" fill="currentColor" />
          </svg>
          <span class="pc-outline__label">{{ c.title }}</span>
        </button>
      </li>
    </ul>
    <button type="button" class="pc-outline__collapse" @click="emit('collapse')">
      <svg viewBox="0 0 12 12" aria-hidden="true">
        <path d="M7.5 2.5 4 6l3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </svg>
      收起侧栏
    </button>
  </nav>
</template>

<style scoped>
.pc-outline {
  width: 211px;
  flex: none;
  display: flex;
  flex-direction: column;
  padding: 18px 12px 16px;
  background: #f6fbfe;
  border-right: 1px solid var(--pc-line);
  overflow-y: auto;
  box-sizing: border-box;
}
.pc-outline__title {
  margin: 0 0 14px;
  padding: 0 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-outline__nav {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}
.pc-outline__item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  color: var(--pc-text);
  text-align: left;
  cursor: pointer;
}
.pc-outline__item:hover {
  background: #eef5fb;
}
.pc-outline__item.is-active {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-outline__icon {
  flex: none;
  width: 16px;
  height: 16px;
  color: var(--pc-primary);
  opacity: 0.7;
}
.pc-outline__item.is-active .pc-outline__icon {
  color: var(--pc-primary);
  opacity: 1;
}
.pc-outline__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-outline__collapse {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 14px;
  padding: 8px 8px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-outline__collapse:hover {
  color: var(--pc-primary);
  background: #eef5fb;
}
.pc-outline__collapse svg {
  width: 12px;
  height: 12px;
}
</style>
