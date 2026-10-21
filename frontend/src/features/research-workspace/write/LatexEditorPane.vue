<template>
  <section class="rw-editor">
    <!-- 文件 Tab 条 -->
    <div class="rw-editor__tabs">
      <div class="rw-editor__tablist" role="tablist" aria-label="已打开文件">
        <div
          v-for="path in openDocs"
          :key="path"
          class="rw-editor__tab"
          :class="{ 'rw-editor__tab--active': path === activePath }"
          role="tab"
          :aria-selected="path === activePath"
          tabindex="0"
          @click="emit('select', path)"
          @keydown.enter.prevent="emit('select', path)"
        >
          <span class="rw-editor__tab-ico" aria-hidden="true"><DocumentTextOutline /></span>
          <span class="rw-editor__tab-name">{{ baseName(path) }}</span>
          <button
            type="button"
            class="rw-editor__tab-close"
            :aria-label="`关闭 ${path}`"
            @click.stop="emit('close', path)"
          >
            <CloseOutline aria-hidden="true" />
          </button>
        </div>
        <button type="button" class="rw-editor__tab-add" aria-label="新建文件" title="新建文件" @click="emit('new-file')">
          <AddOutline aria-hidden="true" />
        </button>
      </div>
      <SaveStateIndicator v-if="activePath" :state="saveState" />
    </div>

    <!-- 格式工具条 -->
    <FormatToolbar
      :line-numbers="lineNumbers"
      @update:line-numbers="onToggleLineNumbers"
      @undo="onUndo"
      @redo="onRedo"
      @wrap="onWrap"
      @insert="onInsert"
      @search="toggleSearch"
      @outdent="onOutdent"
    />

    <!-- 查找条 -->
    <div v-if="searching" class="rw-editor__search">
      <SearchOutline class="rw-editor__search-ico" aria-hidden="true" />
      <input
        ref="searchInput"
        v-model="query"
        class="rw-editor__search-input"
        placeholder="查找…"
        aria-label="在此文件中查找"
        @input="runSearch"
        @keydown.enter.prevent="nextHit"
        @keydown.esc.prevent="closeSearch"
      />
      <!-- 0 命中时显式给「无结果」，不显示误导性的 1/0（§70） -->
      <span v-if="query && !hits.length" class="rw-editor__search-count">无结果</span>
      <span v-else class="rw-editor__search-count">{{ hitIndex + 1 }}/{{ hits.length }}</span>
      <button type="button" class="rw-editor__search-btn" aria-label="下一个" @click="nextHit">
        <ChevronDownOutline aria-hidden="true" />
      </button>
      <button type="button" class="rw-editor__search-btn" aria-label="关闭查找" @click="closeSearch">
        <CloseOutline aria-hidden="true" />
      </button>
    </div>

    <!-- 编辑器主体 -->
    <div v-show="activePath" ref="host" class="rw-editor__host" />

    <div v-if="!activePath" class="rw-editor__empty">
      <EmptyState
        title="选择文件开始编辑"
        description="左侧文件树中选择 .tex 文件；编辑器支持行号、语法高亮、自动保存与论文结构定位。"
        :icon="CodeSlashOutline"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * LaTeX 编辑器面板（宽 590，VISUAL-SPEC §5.2）。
 *
 * - **CodeMirror 6**（禁止裸 textarea，§7.4）：行号 + stex 语法高亮 + 当前行高亮 + 撤销/重做；
 * - 文件 Tab 条：已打开文件 + 关闭 + `＋`；右侧 `SaveStateIndicator` 显式展示保存四态；
 * - 自动保存：`update` → `update:doc` → 800ms debounce → `PUT /files/{path}`（宪法 §51）；
 * - 自研查找（🔍，本仓无 `@codemirror/search`）；
 * - `defineExpose({ scrollToLine, openFile })` 供 Outline / 编译日志跨文件定位。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorSelection, EditorState, type Extension } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { undo, redo } from '@codemirror/commands'
import { AddOutline, ChevronDownOutline, CloseOutline, CodeSlashOutline, DocumentTextOutline, SearchOutline } from '@vicons/ionicons5'
import EmptyState from '../ui/EmptyState.vue'
import SaveStateIndicator from './SaveStateIndicator.vue'
import FormatToolbar, { type InsertKind, type WrapKind } from './FormatToolbar.vue'
import {
  buildLatexExtensions,
  reconfigureLineNumbers,
  searchMatches,
  setAnchorLine,
} from './latexEditorSetup'
import type { SaveState } from '../stores/files'

const props = withDefaults(
  defineProps<{
    activePath?: string | null
    openDocs?: string[]
    /** 当前文件已保存基线串（外部同步到编辑器） */
    doc?: string
    saveState?: SaveState
    editable?: boolean
  }>(),
  {
    activePath: null,
    openDocs: () => [],
    doc: '',
    saveState: 'saved',
    editable: true,
  },
)

const emit = defineEmits<{
  (e: 'select', path: string): void
  (e: 'close', path: string): void
  (e: 'new-file'): void
  (e: 'update:doc', path: string, doc: string): void
  (e: 'cursor-line', line: number): void
  /** 选区变化（宪法 §26/§46）：Shell 据此装载 `agent.context.selection`，是「选中即校验/翻译」的前提 */
  (e: 'selection-change', payload: { text: string; line: number }): void
}>()

const host = ref<HTMLElement | null>(null)
const lineNumbers = ref(true)
const searching = ref(false)
const query = ref('')
const hits = ref<number[]>([])
const hitIndex = ref(0)
const searchInput = ref<HTMLInputElement | null>(null)

let view: EditorView | null = null
/** 抑制回环：外部写入触发 dispatch 时不回传 `update:doc`。 */
let syncing = false
let activePathRef: string | null = props.activePath

const baseName = (p: string): string => (p.includes('/') ? p.slice(p.lastIndexOf('/') + 1) : p)

const extensionsFor = (): Extension[] =>
  buildLatexExtensions({
    editable: props.editable,
    lineNumbers: lineNumbers.value,
    placeholderText: '在此撰写 LaTeX…（例如 \\section{引言}）',
    onChange: (docText) => {
      if (syncing || !activePathRef) return
      emit('update:doc', activePathRef, docText)
    },
    onCursorLine: (line) => emit('cursor-line', line),
    onSelection: (info) => emit('selection-change', info),
  })

function setDoc(next: string) {
  if (!view || next === view.state.doc.toString()) return
  syncing = true
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: next } })
  syncing = false
}

function createEditor() {
  if (!host.value) return
  view = new EditorView({
    state: EditorState.create({ doc: props.doc, extensions: extensionsFor() }),
    parent: host.value,
  })
}

onMounted(createEditor)

watch(
  () => props.doc,
  (next) => {
    if (!activePathRef) return
    setDoc(next ?? '')
  },
)

watch(
  () => props.activePath,
  (next) => {
    if (next === activePathRef) return
    activePathRef = next
    setDoc(props.doc ?? '')
    if (view) view.dispatch({ effects: setAnchorLine.of(null) })
  },
)

onBeforeUnmount(() => {
  view?.destroy()
  view = null
})

/* ── 行号开关 ── */
function onToggleLineNumbers(v: boolean) {
  lineNumbers.value = v
  view?.dispatch({ effects: reconfigureLineNumbers(v) })
}

/* ── 撤销 / 重做 ── */
function onUndo() {
  if (view) undo(view)
}
function onRedo() {
  if (view) redo(view)
}

/* ── 排版包裹 ── */
function replaceSelection(text: string) {
  if (!view) return
  const { from, to } = view.state.selection.main
  const selected = view.state.sliceDoc(from, to)
  const insert = text.replace('%s', selected)
  const anchor = from + text.indexOf('%s')
  view.dispatch({
    changes: { from, to, insert },
    selection: EditorSelection.single(anchor, anchor + selected.length),
    scrollIntoView: true,
  })
  view.focus()
}

function insertBlock(text: string, cursorOffset = text.length) {
  if (!view) return
  const { from } = view.state.selection.main
  view.dispatch({
    changes: { from, insert: text },
    selection: EditorSelection.cursor(from + cursorOffset),
    scrollIntoView: true,
  })
  view.focus()
}

function onWrap(kind: WrapKind) {
  switch (kind) {
    case 'bold':
      replaceSelection('\\textbf{%s}')
      break
    case 'italic':
      replaceSelection('\\textit{%s}')
      break
    case 'brace':
      replaceSelection('{%s}')
      break
    case 'math':
      replaceSelection('$%s$')
      break
    case 'env':
      insertBlock('\\begin{theorem}\n  \n\\end{theorem}', 20)
      break
    default:
      break
  }
}

function onInsert(kind: InsertKind) {
  switch (kind) {
    case 'link':
      replaceSelection('\\href{https://}{%s}')
      break
    case 'image':
      replaceSelection('\\includegraphics[width=0.8\\textwidth]{figures/%s}')
      break
    case 'table':
      insertBlock(
        '\\begin{table}[htbp]\n  \\centering\n  \\begin{tabular}{ccc}\n    \\hline\n     &  &  \\\\\n    \\hline\n  \\end{tabular}\n  \\caption{}\n  \\label{tab:}\n\\end{table}\n',
      )
      break
    case 'section':
      insertBlock('\\section{}\n', 9)
      break
    default:
      break
  }
}

/** 取消缩进：删除选中行行首最多 2 个空格。 */
function onOutdent() {
  if (!view) return
  const sel = view.state.selection.main
  const startLine = view.state.doc.lineAt(sel.from)
  const endLine = view.state.doc.lineAt(sel.to)
  const changes: { from: number; to: number; insert: string }[] = []
  for (let n = startLine.number; n <= endLine.number; n++) {
    const line = view.state.doc.line(n)
    const m = /^ {1,2}/.exec(line.text)
    if (m) changes.push({ from: line.from, to: line.from + m[0].length, insert: '' })
  }
  if (changes.length) view.dispatch({ changes })
  view.focus()
}

/** 在当前光标处插入片段（供 Agent「插入引用」等外部动作使用）。 */
function insertSnippet(text: string): void {
  if (!view || !text) return
  const { from, to } = view.state.selection.main
  view.dispatch({
    changes: { from, to, insert: text },
    selection: EditorSelection.cursor(from + text.length),
    scrollIntoView: true,
  })
  view.focus()
}

/* ── 查找 ── */
function toggleSearch() {
  searching.value = !searching.value
  if (searching.value) {
    queueMicrotask(() => searchInput.value?.focus())
    runSearch()
  } else {
    hits.value = []
    query.value = ''
  }
}
function closeSearch() {
  searching.value = false
  query.value = ''
  hits.value = []
  view?.focus()
}
function runSearch() {
  if (!view) return
  const text = view.state.doc.toString()
  const found = searchMatches(text, query.value)
  hits.value = found.map((h) => h.from)
  hitIndex.value = 0
  focusHit()
}
function focusHit() {
  if (!view || !hits.value.length) return
  const from = hits.value[hitIndex.value]
  const to = from + query.value.length
  view.dispatch({
    selection: EditorSelection.single(from, to),
    effects: EditorView.scrollIntoView(from, { y: 'center' }),
  })
}
function nextHit() {
  if (!hits.value.length) return
  hitIndex.value = (hitIndex.value + 1) % hits.value.length
  focusHit()
}

/* ── 对外暴露 ── */
/** 滚动到指定行（1-based）并高亮行锚点。 */
function scrollToLine(line: number) {
  if (!view || !Number.isFinite(line)) return
  const target = Math.min(Math.max(1, Math.floor(line)), view.state.doc.lines)
  const info = view.state.doc.line(target)
  view.dispatch({
    selection: EditorSelection.single(info.from, info.to),
    effects: [setAnchorLine.of(target), EditorView.scrollIntoView(info.from, { y: 'center' })],
  })
  view.focus()
}

/** 定位到文件 + 行（跨文件由父级先 `open`，此处仅同步并滚动）。 */
function openFile(path: string, line?: number) {
  setDoc(props.doc ?? '')
  if (line) scrollToLine(line)
}

defineExpose({ scrollToLine, openFile, focus: () => view?.focus(), insertSnippet })

// 保留 `Extension` 类型导出，供外部按需引用（构建期类型收敛）。
export type { Extension }
</script>

<style scoped>
.rw-editor {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--rw-surface);
}

/* 文件 Tab 条
 * 黄金图 `WRITE-GOLDEN` 实测：条带 y119~155（h37），底 = `--rw-page-bg`（与中栏上方留白同色）；
 * 激活 Tab 白底 + 浅蓝描边 + 上圆角 8（与下方白色格式条连成一体）；非激活 Tab 浅蓝底（#E3EFFD ≈ `--rw-primary-soft`）；
 * `＋` 为同色 35×35 圆角方钮。Tab 左侧 9px 内边距（黄金图 Tab 左边框 x=315，编辑器栏原点 x=306）。
 */
.rw-editor__tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 37px;
  padding: 0 10px 0 9px;
  flex: 0 0 auto;
  background: var(--rw-page-bg);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-editor__tablist {
  display: flex;
  align-items: stretch;
  gap: 4px;
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  overflow-x: auto;
}
.rw-editor__tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 100%;
  padding: 0 10px;
  border: var(--rw-border-w) solid transparent;
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  background: var(--rw-primary-soft);
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h3);
  white-space: nowrap;
  cursor: pointer;
}
.rw-editor__tab--active {
  background: var(--rw-surface);
  border-color: color-mix(in srgb, var(--rw-primary) 32%, var(--rw-surface));
  color: var(--rw-primary);
  font-weight: var(--rw-fw-semibold);
}
/* .tex 文件类型徽标：16×16 深色圆角方 + 白色文档字形（黄金图 x328~343 / y128~148） */
.rw-editor__tab-ico {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  border-radius: 4px;
  background: var(--rw-text);
  color: var(--rw-surface);
}
.rw-editor__tab-ico :deep(svg) {
  width: 11px;
  height: 11px;
}
.rw-editor__tab-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.rw-editor__tab-close:hover {
  background: var(--rw-surface);
}
.rw-editor__tab-close :deep(svg) {
  width: 13px;
  height: 13px;
}
.rw-editor__tab-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 35px;
  height: 35px;
  flex: 0 0 35px;
  border: none;
  border-radius: var(--rw-radius-md);
  background: var(--rw-primary-soft);
  color: var(--rw-text-2);
  cursor: pointer;
}
.rw-editor__tab-add:hover {
  background: var(--rw-surface);
  color: var(--rw-primary);
}
.rw-editor__tab-add :deep(svg) {
  width: 18px;
  height: 18px;
}

/* 查找条 */
.rw-editor__search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 8px;
  flex: 0 0 auto;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-editor__search-ico {
  width: 14px;
  height: 14px;
  color: var(--rw-text-3);
}
.rw-editor__search-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 22px;
  padding: 0 8px;
  border: var(--rw-border-w) solid var(--rw-border);
  border-radius: var(--rw-radius-sm);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-sm);
  outline: none;
}
.rw-editor__search-input:focus {
  border-color: var(--rw-primary);
}
.rw-editor__search-count {
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
  min-width: 34px;
  text-align: right;
}
.rw-editor__search-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-2);
  cursor: pointer;
}
.rw-editor__search-btn:hover {
  background: var(--rw-surface-2);
}
.rw-editor__search-btn :deep(svg) {
  width: 14px;
  height: 14px;
}

.rw-editor__host {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.rw-editor__host :deep(.cm-editor) {
  height: 100%;
  outline: none;
}
.rw-editor__host :deep(.cm-scroller) {
  overflow: auto;
}
.rw-editor__empty {
  flex: 1 1 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}
</style>
