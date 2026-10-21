<template>
  <div class="rw-outline" role="tree" aria-label="论文结构">
    <div v-if="loading" class="rw-outline__hint">正在解析论文结构…</div>
    <div v-else-if="!visible.length" class="rw-outline__hint">
      尚未解析到结构。创建 <code>main.tex</code> 并使用
      <code>\section{}</code> / <code>\subsection{}</code> 后，这里会实时生成论文结构。
    </div>

    <template v-for="row in visible" :key="`${row.item.line}-${row.item.title}`">
      <div
        class="rw-outline__row"
        :class="{ 'rw-outline__row--sub': row.item.level > 1 }"
        :style="{ paddingLeft: `${4 + (row.item.level - 1) * 14}px` }"
        role="treeitem"
        :aria-level="row.item.level"
        tabindex="0"
        @click="emit('navigate', row.item.file, row.item.line)"
        @keydown.enter.prevent="emit('navigate', row.item.file, row.item.line)"
      >
        <span
          v-if="row.hasChildren"
          class="rw-outline__chev"
          :class="{ 'rw-outline__chev--open': !collapsed.has(row.item.line) }"
          role="button"
          tabindex="0"
          :aria-label="collapsed.has(row.item.line) ? '展开' : '折叠'"
          @click.stop="toggle(row.item.line)"
          @keydown.enter.stop="toggle(row.item.line)"
        >
          <ChevronForwardOutline aria-hidden="true" />
        </span>
        <span v-else-if="row.item.level > 1" class="rw-outline__dot" aria-hidden="true">·</span>

        <component
          :is="iconFor(row.item)"
          v-if="!row.hasChildren && row.item.level <= 1"
          class="rw-outline__ico"
          aria-hidden="true"
        />
        <span class="rw-outline__title">{{ row.label }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 论文结构 Outline（VISUAL-SPEC §5.1 / 宪法 §18）。
 *
 * - 由 `GET /projects/{id}/outline` 的 `items[{title,level,line,kind}]` **实时**生成；
 * - 层级缩进；点击 → `emit('navigate', file, line)` 由 Editor 定位（跨文件先 open）；
 * - `\subsection` 自动编号（`N.M`）—— 后端 `title` 不含编号，编号在此按序补出；
 * - 无 `main.tex` 时后端 404 → 本组件渲染**诚实空态**并给出下一步（不伪造结构）。
 */
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import {
  BookOutline,
  ChevronForwardOutline,
  DocumentTextOutline,
  ListOutline,
} from '@vicons/ionicons5'
import type { OutlineItem } from '../api/types'

const props = withDefaults(
  defineProps<{
    items: OutlineItem[]
    loading?: boolean
  }>(),
  { loading: false },
)

const emit = defineEmits<{ (e: 'navigate', file: string, line: number): void }>()

const collapsed = ref<Set<number>>(new Set())

/** 有子项的一系列行号。 */
const parentLines = computed(() => {
  const items = props.items ?? []
  const set = new Set<number>()
  for (let i = 0; i < items.length; i++) {
    const next = items[i + 1]
    if (next && next.level > items[i].level) set.add(items[i].line)
  }
  return set
})

interface Row {
  item: OutlineItem
  label: string
  hasChildren: boolean
}

/** 带编号的行（section → `N`，subsection → `N.M`；摘要/参考文献/附录不编号）。 */
const numbered = computed<Row[]>(() => {
  const items = props.items ?? []
  let sec = 0
  let sub = 0
  return items.map((item) => {
    const t = item.title || ''
    let label = t
    if (item.kind === 'section') {
      sec += 1
      sub = 0
      label = `${sec} ${t}`
    } else if (item.kind === 'subsection' || item.level > 1) {
      if (sec === 0) sec = 1
      sub += 1
      label = `${sec}.${sub} ${t}`
    }
    return { item, label, hasChildren: parentLines.value.has(item.line) }
  })
})

/** 折叠过滤：被折叠祖先隐藏的行不渲染。 */
const visible = computed<Row[]>(() => {
  const items = props.items ?? []
  const out: Row[] = []
  for (let i = 0; i < numbered.value.length; i++) {
    const row = numbered.value[i]
    // 向上找最近的一个更高级祖先；若它被折叠，则本行隐藏。
    let hidden = false
    for (let j = i - 1; j >= 0; j--) {
      const anc = items[j]
      if (anc.level < row.item.level) {
        hidden = collapsed.value.has(anc.line)
        break
      }
    }
    if (!hidden) out.push(row)
  }
  return out
})

function toggle(line: number) {
  const next = new Set(collapsed.value)
  if (next.has(line)) next.delete(line)
  else next.add(line)
  collapsed.value = next
}

function iconFor(item: OutlineItem): Component {
  if (item.kind === 'bibliography') return BookOutline
  if (item.kind === 'abstract') return ListOutline
  return DocumentTextOutline
}
</script>

<style scoped>
.rw-outline {
  padding: 4px 6px 10px;
  overflow: auto;
}
.rw-outline__hint {
  padding: 10px 8px;
  font-size: var(--rw-fs-sm);
  line-height: var(--rw-lh-body);
  color: var(--rw-text-3);
}
.rw-outline__hint code {
  padding: 0 3px;
  border-radius: var(--rw-radius-sm);
  background: var(--rw-inset);
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-2);
}
.rw-outline__row {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding-right: 6px;
  border-radius: var(--rw-radius);
  color: var(--rw-text);
  font-size: var(--rw-fs-body);
  cursor: pointer;
}
.rw-outline__row:hover {
  background: var(--rw-surface-2);
}
.rw-outline__row:focus-visible {
  outline: 2px solid var(--rw-primary);
  outline-offset: -2px;
}
.rw-outline__chev {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
  color: var(--rw-text-3);
  transition: transform .12s ease;
}
.rw-outline__chev--open {
  transform: rotate(90deg);
}
.rw-outline__chev :deep(svg) {
  width: 13px;
  height: 13px;
}
.rw-outline__dot {
  display: inline-flex;
  justify-content: center;
  width: 14px;
  flex: 0 0 14px;
  color: var(--rw-text-3);
  font-size: var(--rw-fs-sm);
}
.rw-outline__ico {
  width: 15px;
  height: 15px;
  flex: 0 0 15px;
  color: var(--rw-text-2);
}
.rw-outline__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-outline__row--sub .rw-outline__title {
  color: var(--rw-text-2);
}
</style>
