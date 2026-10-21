/**
 * LaTeX 编辑器（CodeMirror 6）扩展装配（FRONTEND-ARCH §7）。
 *
 * 职责：把 CM6 的扩展集中在一处，供 `LatexEditorPane.vue` 挂载。
 * - 行号（`Compartment` 供 `行号` 开关热切换）
 * - `stex` 语法（`@codemirror/legacy-modes/mode/stex`）+ `defaultHighlightStyle`
 * - **自研轻量高亮**：`\命令`（蓝）/ `% 注释`（绿）/ `$数学$`（紫）/ 环境名 —— 不依赖
 *   `@lezer/highlight`（本仓未提升该包），用 `ViewPlugin` + mark 装饰实现，色彩走 `--rw-*`
 * - 当前行高亮、历史（撤销/重做）、括号匹配、自动缩进、行折叠、Tab 缩进、选区绘制
 * - `anchorLineField`：`scrollToLine()` 的行锚点（供 Outline / 编译日志定位）
 * - `searchMatches()`：`@codemirror/search` 本仓不可用，自研纯文本查找（🔍 工具条）
 *
 * 纪律：**禁止裸 textarea**（VISUAL-SPEC §7.4 / 宪法 §19）。
 */
import {
  Compartment,
  EditorState,
  RangeSetBuilder,
  StateEffect,
  StateField,
  type Extension,
} from '@codemirror/state'
import {
  Decoration,
  EditorView,
  ViewPlugin,
  crosshairCursor,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  placeholder,
  rectangularSelection,
  type DecorationSet,
  type ViewUpdate,
} from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  StreamLanguage,
  bracketMatching,
  defaultHighlightStyle,
  foldGutter,
  foldKeymap,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
} from '@codemirror/language'
import { stex } from '@codemirror/legacy-modes/mode/stex'

/* ───────────────────────── 行号开关（Compartment） ───────────────────────── */

const lineNumberCompartment = new Compartment()

/** 构造行号扩展（开=显示 gutter，关=空扩展）。 */
export function lineNumberExtension(on: boolean): Extension {
  return on ? [lineNumbers(), highlightActiveLineGutter()] : []
}

/* ───────────────────────── 行锚点（定位高亮） ───────────────────────── */

/** 设置 / 清除行锚点（1-based；null 清除）。 */
export const setAnchorLine = StateEffect.define<number | null>()

export const anchorLineField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    let next = deco.map(tr.changes)
    for (const effect of tr.effects) {
      if (!effect.is(setAnchorLine)) continue
      if (effect.value == null) {
        next = Decoration.none
        continue
      }
      const lineNo = Math.min(Math.max(1, Math.floor(effect.value)), tr.state.doc.lines)
      const line = tr.state.doc.line(lineNo)
      next = Decoration.set([Decoration.line({ class: 'rw-cm-anchor' }).range(line.from)])
    }
    return next
  },
  provide: (field) => EditorView.decorations.from(field),
})

/* ───────────────────────── 自研 LaTeX 高亮 ───────────────────────── */

const TOK_MACRO = Decoration.mark({ class: 'rw-tok-macro' })
const TOK_COMMENT = Decoration.mark({ class: 'rw-tok-comment' })
const TOK_MATH = Decoration.mark({ class: 'rw-tok-math' })
const TOK_BRACE = Decoration.mark({ class: 'rw-tok-brace' })

/** 单行扫描：注释 > 数学 > 命令 > 花括号（重叠由 `Decoration.set(…, true)` 排序）。 */
function decorateLine(text: string, lineStart: number, out: Array<{ from: number; to: number; deco: Decoration }>) {
  // 1) 未转义的 `%` → 注释（行尾）
  let commentAt = -1
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '%' && (i === 0 || text[i - 1] !== '\\')) {
      commentAt = i
      break
    }
  }
  const codeEnd = commentAt === -1 ? text.length : commentAt
  const code = text.slice(0, codeEnd)

  // 2) 数学段 $...$ / $$...$$（在非注释区）
  const mathSpans: Array<[number, number]> = []
  for (let i = 0; i < code.length; i++) {
    if (code[i] !== '$' || (i > 0 && code[i - 1] === '\\')) continue
    const double = code[i + 1] === '$'
    const close = double ? code.indexOf('$$', i + 2) : code.indexOf('$', i + 1)
    const end = close === -1 ? codeEnd : close + (double ? 2 : 1)
    mathSpans.push([i, end])
    i = end - 1
  }

  // 3) `\命令`
  const macroRe = /\\[a-zA-Z@]+/g
  let m: RegExpExecArray | null
  while ((m = macroRe.exec(code)) !== null) {
    out.push({ from: lineStart + m.index, to: lineStart + m.index + m[0].length, deco: TOK_MACRO })
  }

  // 4) 花括号（结构性；仅命令后的分组，避免噪声）
  const braceRe = /[{}]/g
  while ((m = braceRe.exec(code)) !== null) {
    out.push({ from: lineStart + m.index, to: lineStart + m.index + 1, deco: TOK_BRACE })
  }

  // 5) 数学段
  for (const [a, b] of mathSpans) {
    out.push({ from: lineStart + a, to: lineStart + Math.min(b, codeEnd), deco: TOK_MATH })
  }

  // 6) 注释
  if (commentAt !== -1) {
    out.push({ from: lineStart + commentAt, to: lineStart + text.length, deco: TOK_COMMENT })
  }
}

const latexHighlight = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet
    constructor(view: EditorView) {
      this.decorations = this.build(view)
    }
    update(u: ViewUpdate) {
      if (u.docChanged || u.viewportChanged) this.decorations = this.build(u.view)
    }
    build(view: EditorView): DecorationSet {
      const builder = new RangeSetBuilder<Decoration>()
      const ranges: Array<{ from: number; to: number; deco: Decoration }> = []
      for (const { from, to } of view.visibleRanges) {
        let pos = from
        while (pos <= to) {
          const line = view.state.doc.lineAt(pos)
          decorateLine(line.text, line.from, ranges)
          if (line.to + 1 > to) break
          pos = line.to + 1
        }
      }
      // 排序后统一构建（mark 装饰允许重叠，但必须按 from 非降序压入）
      ranges.sort((a, b) => a.from - b.from || a.to - b.to)
      let lastFrom = -1
      for (const r of ranges) {
        if (r.from < lastFrom) continue
        builder.add(r.from, r.to, r.deco)
        lastFrom = r.from
      }
      return builder.finish()
    }
  },
  { decorations: (v) => v.decorations },
)

/* ───────────────────────── 主题 ───────────────────────── */

/** 编辑器主题：仅用 `--rw-*`；`&` 指代编辑器根。 */
const latexTheme: Extension = EditorView.theme({
  '&': {
    height: '100%',
    color: 'var(--rw-text)',
    backgroundColor: 'var(--rw-surface)',
    fontFamily: 'var(--rw-font-mono)',
    fontSize: 'var(--rw-fs-body)',
  },
  '.cm-scroller': {
    fontFamily: 'var(--rw-font-mono)',
    // 黄金图实测行距 18.4px / 字号 13px → line-height 1.42
    lineHeight: '1.42',
  },
  '.cm-content': { padding: '2px 0', caretColor: 'var(--rw-primary)' },
  '.cm-gutters': {
    // 黄金图行号槽底 ≈ #F3F6F9（= `--rw-inset` 同族），非纯白
    backgroundColor: 'var(--rw-inset)',
    color: 'var(--rw-text-3)',
    border: 'none',
    borderRight: '1px solid var(--rw-border)',
    minWidth: '34px',
  },
  '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 0', textAlign: 'right' },
  '.cm-activeLine': { backgroundColor: 'var(--rw-inset)' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--rw-inset)', color: 'var(--rw-text-2)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': {
    backgroundColor: 'var(--rw-primary-soft)',
  },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--rw-primary)' },
  '.cm-foldPlaceholder': {
    backgroundColor: 'var(--rw-inset)',
    border: 'none',
    color: 'var(--rw-text-2)',
  },
  '.rw-tok-macro': { color: 'var(--rw-code-macro)' },
  '.rw-tok-comment': { color: 'var(--rw-code-comment)' },
  '.rw-tok-math': { color: 'var(--rw-code-math)' },
  '.rw-tok-brace': { color: 'var(--rw-code-brace)' },
  '.rw-cm-anchor': {
    backgroundColor: 'var(--rw-primary-soft)',
    boxShadow: 'inset 2px 0 0 var(--rw-primary)',
  },
})

/* ───────────────────────── 扩展装配 ───────────────────────── */

export interface LatexEditorOptions {
  editable?: boolean
  /** 初始是否显示行号 */
  lineNumbers?: boolean
  placeholderText?: string
  /** 文档变化（自动保存触发源） */
  onChange?: (doc: string) => void
  /** 光标行变化（用于上下文 chips / Agent 上下文 `line`） */
  onCursorLine?: (line: number) => void
  /**
   * 选区变化（宪法 §26/§46「选中即装载」）。
   *
   * 为什么必须有它：READ 侧 `PdfReaderPane` 会发 `selection-change` 并由 Shell 装进
   * `agent.context.selection`，而 WRITE 侧此前**从不发**选区事件 —— 导致在编辑器里
   * 选中公式/段落再点「公式检查 / 翻译」时，后端 `_selected_text()` 读到空串，
   * 恒返回 needs_conditions（「当前没有可校验的公式」）——即"表面调了能力、
   * 实际拿不到选区"的假链路。
   */
  onSelection?: (info: { text: string; line: number }) => void
}

/** 组装完整扩展集。 */
export function buildLatexExtensions(opts: LatexEditorOptions = {}): Extension[] {
  const editable = opts.editable !== false
  const extensions: Extension[] = [
    lineNumberCompartment.of(lineNumberExtension(opts.lineNumbers !== false)),
    history(),
    drawSelection(),
    dropCursor(),
    EditorState.allowMultipleSelections.of(true),
    indentOnInput(),
    indentUnit.of('  '),
    bracketMatching(),
    foldGutter(),
    rectangularSelection(),
    crosshairCursor(),
    keymap.of([...defaultKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
    StreamLanguage.define(stex),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    latexHighlight,
    highlightActiveLine(),
    EditorView.lineWrapping,
    anchorLineField,
    latexTheme,
    EditorState.readOnly.of(!editable),
    EditorView.editable.of(editable),
  ]
  if (opts.placeholderText) extensions.push(placeholder(opts.placeholderText))
  extensions.push(
    EditorView.updateListener.of((u) => {
      if (u.docChanged && opts.onChange) opts.onChange(u.state.doc.toString())
      if (u.selectionSet || u.docChanged) {
        const sel = u.state.selection.main
        const head = sel.head
        opts.onCursorLine?.(u.state.doc.lineAt(head).number)
        // 选区装载（宪法 §26/§46）：光标折叠 = 没有选区，如实上报空串，
        // 避免「上一篇/上一次的选区」被当成当前选区（与 READ 的 null 语义一致）。
        opts.onSelection?.({
          text: sel.from === sel.to ? '' : u.state.sliceDoc(sel.from, sel.to),
          line: u.state.doc.lineAt(sel.from).number,
        })
      }
    }),
  )
  return extensions
}

/**
 * 生成行号开关的重配置效应（热切换，不动文档）。
 *
 * `Compartment.reconfigure()` 返回 `StateEffect<unknown>`（而非 `Extension`），
 * 必须原样作为 `dispatch({ effects })` 使用，故此处显式标注为 `StateEffect<unknown>`。
 */
export function reconfigureLineNumbers(on: boolean): StateEffect<unknown> {
  return lineNumberCompartment.reconfigure(lineNumberExtension(on))
}

/* ───────────────────────── 查找（🔍，替代不可用的 @codemirror/search） ───────────────────────── */

export interface SearchHit {
  from: number
  to: number
}

/** 纯文本查找：返回全部命中（不区分大小写可选）。 */
export function searchMatches(text: string, query: string, caseSensitive = false): SearchHit[] {
  const hits: SearchHit[] = []
  const q = caseSensitive ? query : query.toLowerCase()
  if (!q) return hits
  const hay = caseSensitive ? text : text.toLowerCase()
  let idx = hay.indexOf(q)
  while (idx !== -1) {
    hits.push({ from: idx, to: idx + q.length })
    idx = hay.indexOf(q, idx + q.length)
  }
  return hits
}
