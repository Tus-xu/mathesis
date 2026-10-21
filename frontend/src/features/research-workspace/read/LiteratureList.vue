<template>
  <section class="rw-lit" aria-label="文献列表">
    <header class="rw-lit__head">
      <button type="button" class="rw-lit__fold" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
        <ChevronDownOutline class="rw-lit__chev" :class="{ 'rw-lit__chev--folded': collapsed }" aria-hidden="true" />
        <span class="rw-lit__title">文献列表</span>
      </button>
      <div class="rw-lit__actions">
        <button type="button" class="rw-lit__icon" aria-label="导入 PDF" @click="pickFile">
          <AddOutline aria-hidden="true" />
        </button>
        <button
          type="button"
          class="rw-lit__icon"
          :aria-label="searchOpen ? '关闭搜索' : '搜索文献'"
          :aria-pressed="searchOpen"
          @click="toggleSearch"
        >
          <SearchOutline aria-hidden="true" />
        </button>
      </div>
    </header>

    <!-- 折叠：真实隐藏列表体（此前只转箭头不折叠，属假交互，§70） -->
    <template v-if="!collapsed">
      <div v-if="searchOpen" class="rw-lit__search">
        <input v-model="keyword" type="search" class="rw-lit__input" placeholder="按标题筛选文献…" />
      </div>

      <div v-if="loading" class="rw-lit__body">
        <RegionSkeleton :rows="3" label="文献加载中" />
      </div>
      <ul v-else-if="visible.length" class="rw-lit__body rw-lit__body--list">
        <li v-for="p in visible" :key="p.id">
          <div class="rw-lit__item" :class="{ 'rw-lit__item--active': p.id === activeId }">
            <button type="button" class="rw-lit__open" @click="emit('select', p.id)">
              <span class="rw-lit__badge" aria-hidden="true">PDF</span>
              <span class="rw-lit__text">
                <span class="rw-lit__name" :title="p.title">{{ p.title }}</span>
                <span class="rw-lit__sub">{{ subtitle(p) }}</span>
              </span>
            </button>
            <button
              type="button"
              class="rw-lit__del"
              :aria-label="`删除文献 ${p.title}`"
              @click.stop="emit('remove', p.id)"
            >
              <TrashOutline aria-hidden="true" />
            </button>
          </div>
        </li>
      </ul>
      <div v-else class="rw-lit__body">
        <p class="rw-lit__empty">
          {{ papers.length ? '没有匹配的文献。' : (error || '还没有文献，点击右上「＋」导入 PDF。') }}
        </p>
      </div>
    </template>

    <input ref="fileRef" type="file" accept="application/pdf,.pdf" class="rw-lit__file" @change="onChosen" />
  </section>
</template>

<script setup lang="ts">
/**
 * 文献列表（VISUAL-SPEC §4.1-4）。
 *
 * 条目：PDF 徽标（26×26 红底白字）+ 标题 13/500（当前打开 `--rw-primary`）+ `作者 · 年份` 12/400。
 * 当前打开行 bg `--rw-primary-soft`。真实能力：导入 / 打开 / 切换 / 删除。
 * 无真实文献时给「导入 PDF」的下一步，不伪造条目。
 */
import { computed, ref } from 'vue'
import { AddOutline, ChevronDownOutline, SearchOutline, TrashOutline } from '@vicons/ionicons5'
import RegionSkeleton from '../ui/RegionSkeleton.vue'
import type { Paper } from '../api/types'

const props = withDefaults(
  defineProps<{
    papers: Paper[]
    activeId?: string | null
    loading?: boolean
    error?: string | null
  }>(),
  { activeId: null, loading: false, error: null },
)

const emit = defineEmits<{
  (e: 'select', id: string): void
  (e: 'remove', id: string): void
  (e: 'import-file', file: File): void
}>()

const collapsed = ref(false)
const searchOpen = ref(false)
const keyword = ref('')
const fileRef = ref<HTMLInputElement | null>(null)

const visible = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return props.papers
  return props.papers.filter((p) => p.title.toLowerCase().includes(kw))
})

function subtitle(p: Paper): string {
  const author = p.authors && p.authors.length ? `${p.authors[0]}${p.authors.length > 1 ? ' 等' : ''}` : '未知作者'
  const year = p.year ?? '—'
  return `${author} · ${year}`
}

function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (!searchOpen.value) keyword.value = ''
}

function pickFile() {
  fileRef.value?.click()
}

function onChosen(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) emit('import-file', file)
  input.value = ''
}
</script>

<style scoped>
.rw-lit {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.rw-lit__head {
  display: flex;
  align-items: center;
  height: 30px;
  padding: 0 2px;
}
.rw-lit__fold {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-lit__chev {
  width: 13px;
  height: 13px;
  color: var(--rw-text-2);
  transition: transform .12s ease;
  flex: 0 0 auto;
}
.rw-lit__chev--folded {
  transform: rotate(-90deg);
}
.rw-lit__title {
  font-size: var(--rw-fs-body);
  font-weight: var(--rw-fw-semibold);
  color: var(--rw-text);
}
.rw-lit__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
}
.rw-lit__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-2);
  cursor: pointer;
  font-size: 15px;
}
.rw-lit__icon:hover {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
/* §4.1-4 分区头 `＋` / `🔍` 图标 15px（按钮本体 24×24） */
.rw-lit__icon :deep(svg) {
  width: 15px;
  height: 15px;
}
.rw-lit__search {
  padding: 4px 2px 6px;
}
.rw-lit__input {
  width: 100%;
  height: 28px;
  padding: 0 8px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-sm);
  outline: none;
}
.rw-lit__input:focus {
  border-color: var(--rw-primary);
}
.rw-lit__body {
  min-height: 0;
}
.rw-lit__body--list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.rw-lit__item {
  display: flex;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 12px;
  transition: background-color .15s ease, border-color .15s ease;
}
.rw-lit__item--active {
  background: var(--rw-primary-soft);
  border-color: var(--rw-primary-border, #c7d2fe);
}
.rw-lit__item:hover {
  background: var(--rw-surface-2);
}
.rw-lit__item--active:hover {
  background: var(--rw-primary-soft);
}
.rw-lit__open {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
  height: 48px;
  padding: 7px 8px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.rw-lit__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border-radius: 9px;
  background: var(--rw-danger-soft);
  color: var(--rw-danger);
  border: 1px solid #fecaca;
  font-size: 8.5px;
  font-weight: 800;
  letter-spacing: .2px;
}
.rw-lit__text {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.rw-lit__name {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--rw-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-lit__item--active .rw-lit__name {
  color: var(--rw-primary);
}
.rw-lit__sub {
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rw-lit__del {
  display: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-right: 4px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-2);
  cursor: pointer;
  font-size: 15px;
}
.rw-lit__item:hover .rw-lit__del {
  display: inline-flex;
}
/* §4.1-4 删除图标 14px（按钮本体 24×24） */
.rw-lit__del :deep(svg) {
  width: 14px;
  height: 14px;
}
.rw-lit__del:hover {
  color: var(--rw-danger);
  background: var(--rw-danger-soft);
}
.rw-lit__empty {
  margin: 4px 2px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}
.rw-lit__file {
  display: none;
}
</style>
