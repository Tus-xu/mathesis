<template>
  <div v-if="papers.length" class="rw-dtabs" role="tablist" aria-label="已打开的文献">
    <div class="rw-dtabs__scroll">
      <button
        v-for="p in papers"
        :key="p.id"
        type="button"
        role="tab"
        class="rw-dtabs__tab"
        :class="{ 'rw-dtabs__tab--active': p.id === activeId }"
        :aria-selected="p.id === activeId"
        :title="p.title"
        @click="emit('select', p.id)"
      >
        <span class="rw-dtabs__badge" aria-hidden="true">PDF</span>
        <span class="rw-dtabs__name">{{ p.title }}</span>
        <span
          class="rw-dtabs__close"
          role="button"
          aria-label="关闭标签"
          @click.stop="emit('close', p.id)"
        >
          <CloseOutline aria-hidden="true" />
        </span>
      </button>
    </div>
    <button type="button" class="rw-dtabs__add" aria-label="导入新文献" @click="emit('add')">
      <AddOutline aria-hidden="true" />
    </button>
  </div>
</template>

<script setup lang="ts">
/**
 * 中栏文档 Tab 条（VISUAL-SPEC §4.2）：h 36px，多个文献 Tab + `＋`。
 * 激活页浅蓝底 + 主蓝标题；标题过长省略（禁止断行，用户硬规则）。
 */
import { AddOutline, CloseOutline } from '@vicons/ionicons5'
import type { Paper } from '../api/types'

withDefaults(
  defineProps<{ papers: Paper[]; activeId?: string | null }>(),
  { activeId: null },
)

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'close', id: string): void
  (e: 'add'): void
}>()
</script>

<style scoped>
/*
 * 文档 Tab 条（黄金图 `feC_bands.py read` 实测）：
 *   条带 y126~164（h≈39，底边即 PDF 工具条白底起点），内容带 y136~155（徽标）/ y139~151（标题）。
 *   条带底 = `--rw-page-bg`（与工作区条同底色，白底工具条在其下），故不设自己的背景。
 * 内边距 14px：黄金图 Tab 左边框 x=344，中栏原点 x=330。
 */
.rw-dtabs {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  height: 39px;
  padding: 0 14px;
  flex: 0 0 auto;
}
.rw-dtabs__scroll {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  min-width: 0;
  height: 100%;
  overflow-x: auto;
  scrollbar-width: none;
}
.rw-dtabs__scroll::-webkit-scrollbar {
  display: none;
}
/* 单个 Tab：黄金图 344~611（267×39），上圆角 8，激活态白底 + 浅蓝描边（与下方白工具条连成一体） */
.rw-dtabs__tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 300px;
  height: 39px;
  padding: 0 12px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  background: var(--rw-primary-soft);
  cursor: pointer;
  font-family: var(--rw-font-sans);
}
.rw-dtabs__tab--active {
  background: var(--rw-surface);
  border-color: color-mix(in srgb, var(--rw-primary) 32%, var(--rw-surface));
}
/* PDF 徽标：黄金图 x357~374 / y136~155 → 18×20，圆角 4，白字 ≈8px */
.rw-dtabs__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 20px;
  flex: 0 0 auto;
  border-radius: 4px;
  background: var(--rw-pdf-badge);
  color: var(--rw-surface);
  font-size: 8px;
  font-weight: var(--rw-fw-bold);
  letter-spacing: -.2px;
}
.rw-dtabs__name {
  min-width: 0;
  font-size: var(--rw-fs-h3);
  color: var(--rw-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-dtabs__tab--active .rw-dtabs__name {
  color: var(--rw-primary);
  font-weight: var(--rw-fw-medium);
}
.rw-dtabs__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-sm);
  color: var(--rw-text-3);
  font-size: 14px;
}
.rw-dtabs__tab--active .rw-dtabs__close {
  color: var(--rw-primary);
}
.rw-dtabs__close:hover {
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
}
.rw-dtabs__close :deep(svg) {
  width: 14px;
  height: 14px;
}
/* `＋` 导入：黄金图 616~653 / 133~168 → 约 36×35 的浅底圆角方钮 */
.rw-dtabs__add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 35px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-md);
  background: var(--rw-inset);
  color: var(--rw-text-2);
  cursor: pointer;
  font-size: 18px;
}
.rw-dtabs__add:hover {
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
}
.rw-dtabs__add :deep(svg) {
  width: 18px;
  height: 18px;
}
</style>
