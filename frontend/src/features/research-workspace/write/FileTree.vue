<template>
  <div class="rw-tree" role="tree" aria-label="草稿文件树">
    <!-- 新建文件：内联输入（Enter 提交，Esc 取消） -->
    <div v-if="creating" class="rw-tree__row rw-tree__row--input">
      <DocumentTextOutline class="rw-tree__ico rw-tree__ico--file" aria-hidden="true" />
      <input
        ref="createInput"
        v-model="draftName"
        class="rw-tree__input"
        placeholder="新文件名，如 sections/method.tex"
        aria-label="新文件名"
        @keydown.enter.prevent="submitCreate"
        @keydown.esc.prevent="cancelCreate"
        @blur="cancelCreate"
      />
    </div>

    <div v-if="loading" class="rw-tree__hint">正在加载文件树…</div>
    <div v-else-if="!rows.length && !creating" class="rw-tree__hint">此项目还没有草稿文件。</div>

    <div v-for="row in rows" :key="row.node.path" class="rw-tree__row-wrap">
      <div
        v-if="renamingPath === row.node.path"
        class="rw-tree__row rw-tree__row--input"
        :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
      >
        <component :is="iconFor(row.node)" class="rw-tree__ico" :class="iconClass(row.node)" aria-hidden="true" />
        <input
          ref="renameInput"
          v-model="renameValue"
          class="rw-tree__input"
          :aria-label="`重命名 ${row.node.name}`"
          @keydown.enter.prevent="submitRename"
          @keydown.esc.prevent="cancelRename"
          @blur="cancelRename"
        />
      </div>

      <button
        v-else
        type="button"
        class="rw-tree__row"
        :class="{
          'rw-tree__row--active': row.node.path === activePath,
          'rw-tree__row--dir': row.isDir,
          'rw-tree__row--root': row.isRoot,
        }"
        :style="{ paddingLeft: `${8 + row.depth * 14}px` }"
        :aria-expanded="row.isDir ? expanded.has(row.node.path) : undefined"
        :aria-selected="row.node.path === activePath"
        role="treeitem"
        @click="onRowClick(row)"
      >
        <span class="rw-tree__chev" :class="{ 'rw-tree__chev--open': expanded.has(row.node.path) }">
          <ChevronForwardOutline v-if="row.isDir" aria-hidden="true" />
        </span>
        <component :is="iconFor(row.node)" class="rw-tree__ico" :class="iconClass(row.node)" aria-hidden="true" />
        <span class="rw-tree__name">{{ row.node.name }}</span>

        <span v-if="!row.isDir" class="rw-tree__actions">
          <span
            class="rw-tree__act"
            role="button"
            tabindex="0"
            :aria-label="`重命名 ${row.node.name}`"
            @click.stop="startRename(row.node)"
            @keydown.enter.stop="startRename(row.node)"
          >
            <CreateOutline aria-hidden="true" />
          </span>
          <span
            class="rw-tree__act rw-tree__act--danger"
            role="button"
            tabindex="0"
            :aria-label="`删除 ${row.node.name}`"
            @click.stop="askRemove(row.node)"
            @keydown.enter.stop="askRemove(row.node)"
          >
            <TrashOutline aria-hidden="true" />
          </span>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 草稿文件树（VISUAL-SPEC §5.1）：真实层级、按类型着色、当前文件选中浅蓝底。
 *
 * - 目录 `▸/▾` + 文件夹图标 + 名称；文件按类型着色（.tex 蓝 / .bib 蓝 / 图片 / PDF 红徽标）；
 * - 支持真实多文件（宪法 §17）：新建 / 重命名 / 删除；
 * - **禁止伪造「永远只有 main.tex」**（§7）。
 *
 * 行为向上抛事件，由 `WriteSidebar` 落到 `stores/files`（保持组件无副作用）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import type { Component } from 'vue'
import {
  ChevronForwardOutline,
  CreateOutline,
  DocumentOutline,
  DocumentTextOutline,
  FolderOutline,
  ImageOutline,
  LibraryOutline,
  TrashOutline,
} from '@vicons/ionicons5'
import type { FileKind, FileNode } from '../api/types'

const props = withDefaults(
  defineProps<{
    nodes: FileNode[]
    activePath?: string | null
    loading?: boolean
    /** 项目根节点显示名（提供后树顶部渲染一行项目根，与黄金图一致）。 */
    rootLabel?: string
  }>(),
  { activePath: null, loading: false, rootLabel: '' },
)

const emit = defineEmits<{
  (e: 'open', path: string): void
  (e: 'create', path: string, kind: FileKind): void
  (e: 'rename', from: string, to: string): void
  (e: 'remove', path: string): void
}>()

interface Row {
  node: FileNode
  depth: number
  isDir: boolean
  /** 是否为项目根（合成的展示行，非磁盘文件；不可重命名/删除）。 */
  isRoot?: boolean
}

/** 合成根节点路径（空串即项目根，不会与真实文件路径冲突）。 */
const ROOT_PATH = ''

const expanded = ref<Set<string>>(new Set())

/** 默认展开全部目录（含合成项目根），保证多文件可见；用户可折叠。 */
function defaultExpanded(nodes: FileNode[], depth = 0, acc = new Set<string>()): Set<string> {
  for (const n of nodes) {
    if (n.type === 'dir') {
      acc.add(n.path)
      if (n.children) defaultExpanded(n.children, depth + 1, acc)
    }
  }
  return acc
}

watch(
  () => [props.nodes, props.rootLabel] as const,
  ([nodes]) => {
    const acc = expanded.value
    if (props.rootLabel) acc.add(ROOT_PATH)
    for (const p of defaultExpanded(nodes)) acc.add(p)
    expanded.value = new Set(acc)
  },
  { immediate: true, deep: false },
)

const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const walk = (nodes: FileNode[], depth: number) => {
    for (const n of nodes) {
      const isDir = n.type === 'dir'
      out.push({ node: n, depth, isDir })
      if (isDir && expanded.value.has(n.path) && n.children) walk(n.children, depth + 1)
    }
  }
  if (props.rootLabel) {
    const root: FileNode = {
      name: props.rootLabel,
      path: ROOT_PATH,
      type: 'dir',
      kind: 'dir',
      children: props.nodes ?? [],
    }
    out.push({ node: root, depth: 0, isDir: true, isRoot: true })
    if (expanded.value.has(ROOT_PATH)) walk(props.nodes ?? [], 1)
  } else {
    walk(props.nodes ?? [], 0)
  }
  return out
})

function onRowClick(row: Row) {
  if (row.isDir) {
    const next = new Set(expanded.value)
    if (next.has(row.node.path)) next.delete(row.node.path)
    else next.add(row.node.path)
    expanded.value = next
    return
  }
  emit('open', row.node.path)
}

/* ── 图标 / 着色 ── */
function iconFor(node: FileNode): Component {
  if (node.type === 'dir') return FolderOutline
  switch (node.kind) {
    case 'image':
      return ImageOutline
    case 'bib':
      return LibraryOutline
    case 'pdf':
      return DocumentOutline
    case 'tex':
    case 'sty':
    case 'cls':
      return DocumentTextOutline
    default:
      return DocumentOutline
  }
}
function iconClass(node: FileNode): string {
  if (node.type === 'dir') return 'rw-tree__ico--dir'
  if (node.kind === 'pdf') return 'rw-tree__ico--pdf'
  if (node.kind === 'image') return 'rw-tree__ico--img'
  return 'rw-tree__ico--file'
}

/* ── 新建 ── */
const creating = ref(false)
const draftName = ref('')
const createInput = ref<HTMLInputElement | null>(null)

function startCreate() {
  creating.value = true
  draftName.value = ''
  void nextTick(() => createInput.value?.focus())
}
function cancelCreate() {
  creating.value = false
  draftName.value = ''
}
function submitCreate() {
  const name = draftName.value.trim()
  if (name) emit('create', name, kindFromPath(name))
  cancelCreate()
}
function kindFromPath(path: string): FileKind {
  const ext = path.includes('.') ? path.split('.').pop()!.toLowerCase() : ''
  const map: Record<string, FileKind> = {
    tex: 'tex',
    bib: 'bib',
    sty: 'sty',
    cls: 'cls',
    png: 'image',
    jpg: 'image',
    jpeg: 'image',
    pdf: 'pdf',
  }
  return map[ext] ?? 'data'
}
defineExpose({ startCreate })

/* ── 重命名 ── */
const renamingPath = ref<string | null>(null)
const renameValue = ref('')
const renameInput = ref<HTMLInputElement | null>(null)

function startRename(node: FileNode) {
  renamingPath.value = node.path
  renameValue.value = node.path
  void nextTick(() => renameInput.value?.focus())
}
function cancelRename() {
  renamingPath.value = null
  renameValue.value = ''
}
function submitRename() {
  const from = renamingPath.value
  const to = renameValue.value.trim()
  if (from && to && to !== from) emit('rename', from, to)
  cancelRename()
}

/* ── 删除（二次确认，禁止静默） ── */
function askRemove(node: FileNode) {
  if (window.confirm(`删除文件「${node.path}」？此操作不可撤销。`)) emit('remove', node.path)
}
</script>

<style scoped>
.rw-tree {
  padding: 4px 6px 8px;
  overflow: auto;
}
.rw-tree__hint {
  padding: 10px 8px;
  font-size: var(--rw-fs-sm);
  color: var(--rw-text-3);
}
.rw-tree__row {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  height: 26px;
  padding: 0 6px;
  border: none;
  border-radius: var(--rw-radius);
  background: transparent;
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  text-align: left;
  cursor: pointer;
}
.rw-tree__row:hover {
  background: var(--rw-surface-2);
}
.rw-tree__row--active,
.rw-tree__row--active:hover {
  background: var(--rw-primary-soft);
}
/* 合成项目根行（黄金图树顶的「有限元误差分析」） */
.rw-tree__row--root .rw-tree__name {
  font-weight: var(--rw-fw-medium);
  color: var(--rw-text);
}
.rw-tree__row--input {
  cursor: text;
}
.rw-tree__chev {
  display: inline-flex;
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
  align-items: center;
  justify-content: center;
  color: var(--rw-text-3);
  transition: transform .12s ease;
}
.rw-tree__chev--open {
  transform: rotate(90deg);
}
.rw-tree__chev :deep(svg) {
  width: 12px;
  height: 12px;
}
.rw-tree__ico {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}
.rw-tree__ico--dir { color: var(--rw-primary); }
.rw-tree__ico--file { color: var(--rw-primary); }
.rw-tree__ico--img { color: var(--rw-primary); }
.rw-tree__ico--pdf { color: var(--rw-pdf-badge); }
.rw-tree__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rw-tree__actions {
  display: none;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
}
.rw-tree__row:hover .rw-tree__actions {
  display: inline-flex;
}
.rw-tree__act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--rw-radius-sm);
  color: var(--rw-text-2);
}
.rw-tree__act:hover {
  background: var(--rw-surface);
  color: var(--rw-primary);
}
.rw-tree__act--danger:hover {
  color: var(--rw-danger);
}
.rw-tree__act :deep(svg) {
  width: 13px;
  height: 13px;
}
.rw-tree__input {
  flex: 1 1 auto;
  min-width: 0;
  height: 22px;
  padding: 0 6px;
  border: var(--rw-border-w) solid var(--rw-primary);
  border-radius: var(--rw-radius-sm);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  outline: none;
}
</style>
