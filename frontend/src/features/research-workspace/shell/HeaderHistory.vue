<template>
  <div ref="rootRef" class="rw-hist">
    <button
      type="button"
      class="rw-hist__btn"
      :aria-expanded="open"
      aria-haspopup="dialog"
      aria-label="历史"
      @click="toggle"
    >
      <TimeOutline class="rw-hist__ico" aria-hidden="true" />
      <span class="rw-hist__label">历史</span>
    </button>

    <div v-if="open" class="rw-hist__panel" role="dialog" aria-label="历史">
      <header class="rw-hist__head">
        <span class="rw-hist__title">历史</span>
        <span class="rw-hist__sub">最近打开的文献</span>
      </header>

      <p v-if="loading && !items.length" class="rw-hist__hint">正在加载历史…</p>
      <ul v-else-if="items.length" class="rw-hist__list">
        <li v-for="it in items" :key="it.id">
          <button type="button" class="rw-hist__item" @click="pick(it)">
            <span class="rw-hist__badge" aria-hidden="true">PDF</span>
            <span class="rw-hist__name" :title="it.title">{{ it.title }}</span>
            <span class="rw-hist__meta">
              <span v-if="it.page">{{ it.page }} 页</span>
              <span class="rw-hist__time">{{ when(it.updated_at) }}</span>
            </span>
          </button>
        </li>
      </ul>
      <p v-else class="rw-hist__hint">{{ emptyText }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 顶栏「历史」（VISUAL-SPEC §1.1；宪法 §50 —— 历史必须能回到真实位置）。
 *
 * 数据：`useProjectHistory`（真实 `recent_papers`，见该文件注释）。
 * 点击条目 → 向 Shell 冒泡 `open`，由 Shell 复用既有「定位原文」链路
 * （切到该文献 + 跳到该页 + 写入 recent），**不伪造历史条目**。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { TimeOutline } from '@vicons/ionicons5'
import { useProjectHistory, type HeaderHistoryItem } from './useProjectHistory'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{
  (e: 'open', payload: { paperId: string; page: number | null; title: string }): void
}>()

const rootRef = ref<HTMLElement | null>(null)
const open = ref(false)
const { items, loading, failed, load } = useProjectHistory()

const emptyText = computed(() =>
  failed.value ? '历史暂时无法加载，请稍后重试。' : '暂无历史记录。打开一篇文献后会出现在这里。',
)

function toggle() {
  open.value = !open.value
  if (open.value) void load(props.projectId)
}

function pick(it: HeaderHistoryItem) {
  open.value = false
  emit('open', { paperId: it.id, page: it.page, title: it.title })
}

function onDocClick(e: MouseEvent) {
  if (open.value && rootRef.value && !rootRef.value.contains(e.target as Node)) open.value = false
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('mousedown', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
})

watch(
  () => props.projectId,
  () => {
    items.value = []
    if (open.value) void load(props.projectId)
  },
)

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
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (sameDay(d, y)) return `昨天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<style scoped>
.rw-hist {
  position: relative;
  flex: 0 0 auto;
}
.rw-hist__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 6px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  color: var(--rw-text);
  cursor: pointer;
}
.rw-hist__btn:hover {
  background: var(--rw-surface-2);
}
.rw-hist__ico {
  width: 18px;
  height: 18px;
}
.rw-hist__label {
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
}
.rw-hist__panel {
  position: absolute;
  top: 40px;
  right: 0;
  width: 320px;
  max-height: 384px;
  overflow-y: auto;
  padding: 8px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-md);
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-pop);
  z-index: 30;
}
.rw-hist__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 2px 6px 8px;
}
.rw-hist__title {
  font-size: var(--rw-fs-h3);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-hist__sub {
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-hist__list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.rw-hist__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-hist__item:hover {
  background: var(--rw-primary-soft);
}
.rw-hist__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex: 0 0 auto;
  border-radius: var(--rw-radius-sm);
  background: var(--rw-pdf-badge);
  color: var(--rw-surface);
  font-size: 9px;
  font-weight: var(--rw-fw-bold);
}
.rw-hist__name {
  flex: 1 1 auto;
  min-width: 0;
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-hist__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
.rw-hist__hint {
  margin: 0;
  padding: 12px 8px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}
</style>
