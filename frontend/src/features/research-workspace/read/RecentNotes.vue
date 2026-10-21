<template>
  <section class="rw-notes" aria-label="最近笔记">
    <header class="rw-notes__head">
      <DocumentTextOutline class="rw-notes__ico" aria-hidden="true" />
      <span class="rw-notes__title">最近笔记</span>
      <button type="button" class="rw-notes__more" @click="emit('more')">
        {{ expanded ? '收起' : '更多 ›' }}
      </button>
    </header>

    <div v-if="loading" class="rw-notes__body">
      <RegionSkeleton :rows="2" label="笔记加载中" />
    </div>
    <ul v-else-if="notes.length" class="rw-notes__body rw-notes__body--list">
      <li v-for="n in notes" :key="n.id">
        <button type="button" class="rw-notes__item" :title="n.title" @click="emit('open', n)">
          <DocumentTextOutline class="rw-notes__rowico" aria-hidden="true" />
          <span class="rw-notes__label">{{ n.title || '未命名笔记' }}</span>
          <span class="rw-notes__time">{{ when(n.updated_at) }}</span>
        </button>
      </li>
    </ul>
    <p v-else class="rw-notes__empty">还没有笔记。阅读时选中文字即可添加，点击条目可回到原文位置。</p>
  </section>
</template>

<script setup lang="ts">
/**
 * 最近笔记（VISUAL-SPEC §4.1-5）：分区头 13/600 + `更多 ›`；条目 h30，
 * 📄 图标 + 标题 12/400 + 右对齐时间 11/400 `--rw-text-3`。
 * 点击条目 → 回到 paper/page/selection（宪法 §11）。
 */
import { DocumentTextOutline } from '@vicons/ionicons5'
import RegionSkeleton from '../ui/RegionSkeleton.vue'
import type { Note } from '../api/types'

withDefaults(
  defineProps<{ notes: Note[]; loading?: boolean; expanded?: boolean }>(),
  { loading: false, expanded: false },
)

const emit = defineEmits<{
  (e: 'open', note: Note): void
  (e: 'more'): void
}>()

/** 相对时间：今天 HH:mm / 昨天 HH:mm / M月D日。 */
function when(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  if (sameDay(d, now)) return `今天 ${hm}`
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameDay(d, yesterday)) return `昨天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<style scoped>
.rw-notes {
  display: flex;
  flex-direction: column;
  min-height: 0;
  margin-top: 10px;
}
.rw-notes__head {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 2px;
}
.rw-notes__ico {
  width: 14px;
  height: 14px;
  color: var(--rw-text-2);
  flex: 0 0 auto;
}
.rw-notes__title {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-notes__more {
  flex: 0 0 auto;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--rw-text-2);
  font-size: var(--rw-fs-sm);
  cursor: pointer;
}
.rw-notes__more:hover {
  color: var(--rw-primary);
}
.rw-notes__body {
  min-height: 0;
}
.rw-notes__body--list {
  list-style: none;
  margin: 2px 0 0;
  padding: 0;
}
.rw-notes__item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 30px;
  padding: 0 4px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-notes__item:hover {
  background: var(--rw-surface-2);
}
.rw-notes__rowico {
  width: 14px;
  height: 14px;
  color: var(--rw-primary);
  flex: 0 0 auto;
}
.rw-notes__label {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-notes__time {
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-notes__empty {
  margin: 4px 2px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}
</style>
