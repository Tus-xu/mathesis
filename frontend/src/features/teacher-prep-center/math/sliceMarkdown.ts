/**
 * 教材切片 markdown → 结构化渲染块（R4a「资料页真实化」渲染管线内核）。
 *
 * 切片原文是 markdown 形态的教材正文（`## 3.1.1 …`、`![](images/<hash>.jpg) 图3-1-2`、
 * `$$ … $$`、`\-` 转义、`定义：…`）。红线（G22 原文不泄漏）：**任何上屏文本都必须先经过
 * 本模块清洗**——`![](`/`\-`/`$$`/`\frac` 等源码永不直出；图片解析为真实 `<img>` URL，
 * 加载失败由 SliceFigure 降级占位（也不回退源码）。
 *
 * 三条通道：
 * - `parseSliceBlocks`：切片原文 → 结构化块（标题/定义 callout/公式块/插图/列表/段落）；
 * - `cleanInlineSlice`：行内清洗（保留 $…$ 数学给 KaTeX；文本段剥图片/转义/标记）；
 * - `slicePreviewText`：纯文本预览（数学整段丢弃）——折叠预览/列表标签专用，
 *   保证 `document.body.innerText` 零源码探针（G22）。
 *
 * 另含 chunk 作用域过滤（`scopeBlocksToSection`）：只保留本会话章节（含子节）内容，
 * 混入的其它章节切片被整段剔除（R4a 缺陷 3）。
 */

export type SliceBlock =
  | { kind: 'heading'; level: 1 | 2; no: string | null; text: string }
  | { kind: 'definition'; text: string }
  | { kind: 'formula'; tex: string; tag: string | null }
  | { kind: 'figure'; src: string | null; caption: string; alt: string }
  | { kind: 'list'; text: string }
  | { kind: 'paragraph'; text: string }

/* ── 文本段清洗（非数学段）────────────────────────────────────────── */

/** 单个非数学文本段 → 干净文本：剥图片/链接/反斜杠转义/标记，压缩空白。 */
function cleanTextSegment(seg: string): string {
  return seg
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ') // markdown 图片（→ 图块另行渲染）
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接 → 只留标签文字
    .replace(/\\-/g, '-')
    .replace(/\\([_%&#{}])/g, '$1')
    .replace(/\\(?:,|;|!|>|quad|qquad|enspace|hspace\{[^}]*\}|newline|smallskip)\s*/g, ' ')
    .replace(/\\[a-zA-Z]+\s*/g, ' ') // 其余 \command（含 \frac）不得出现在文本段
    .replace(/\$\$/g, ' ')
    .replace(/[*_`]/g, '')
    .replace(/(^|\n)[ \t]*#{1,6}[ \t]*/g, '$1') // 行首标题标记（`## 标题`/`##标题`/独行 `###` 均剥掉）
    .replace(/(^|\s)#{1,6}\s+/g, '$1') // 行内残留 markdown 标题标记
    .replace(/[{}]/g, ' ')
    .replace(/\$/g, ' ') // 文本段里的孤立 $（未闭合数学残留）一律清掉
    .replace(/[ \t]+/g, ' ')
}

/** 文本段扫描的潜在数学开界符判定（按位置 startsWith，单字符正则覆盖不到 `\( ` `\[`）。 */
function isMathOpenAt(s: string, j: number): boolean {
  return s[j] === '$' || s.startsWith('\\(', j) || s.startsWith('\\[', j)
}

/**
 * 行内清洗（供 KaTeXText 渲染前使用）：数学段原样保留给 KaTeX，文本段清洗。
 * 未闭合的数学定界符按文本段清洗（`\frac` 等随之剥掉）——绝不裸源码上屏。
 */
export function cleanInlineSlice(src: string | null | undefined): string {
  const s = String(src ?? '')
  let out = ''
  let i = 0
  while (i < s.length) {
    if (s.startsWith('$$', i)) {
      const end = s.indexOf('$$', i + 2)
      if (end > i) {
        out += s.slice(i, end + 2)
        i = end + 2
        continue
      }
    }
    if (s[i] === '$' && s[i - 1] !== '\\' && s[i + 1] !== '$') {
      const end = s.indexOf('$', i + 1)
      if (end > i) {
        out += s.slice(i, end + 1)
        i = end + 1
        continue
      }
    }
    if (s.startsWith('\\(', i)) {
      const end = s.indexOf('\\)', i + 2)
      if (end > i) {
        out += s.slice(i, end + 2)
        i = end + 2
        continue
      }
    }
    if (s.startsWith('\\[', i)) {
      const end = s.indexOf('\\]', i + 2)
      if (end > i) {
        out += s.slice(i, end + 2)
        i = end + 2
        continue
      }
    }
    let j = i + 1
    while (j < s.length && !isMathOpenAt(s, j)) j += 1
    out += cleanTextSegment(s.slice(i, j))
    i = j
  }
  return out.replace(/\s+\n/g, '\n').trim()
}

/**
 * 纯文本预览（折叠卡/列表标签/图题候选）：数学整段丢弃 + 文本段清洗。
 * 输出保证不含 `![](`、`\-`、`$$`、`\frac`（G22 探针口径）。
 */
export function slicePreviewText(src: string | null | undefined): string {
  const cleaned = cleanInlineSlice(src)
  return cleaned
    .replace(/\$\$[\s\S]*?\$\$/g, ' ')
    .replace(/\$[^$]*\$/g, ' ')
    .replace(/\\\([\s\S]*?\\\)/g, ' ')
    .replace(/\\\[[\s\S]*?\\\]/g, ' ')
    .replace(/\$\$/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * 树/列表标题（R4afix S7）：SliceBlocks 同源行内清洗（**保留 $…$ / \\(…\\) 行内数学**，
 * 由 KatexText 渲染，修复「如图, 设, 是…」标题层符号剥失）+ 数学感知截断——计数只算
 * 普通字符，截断永不切进数学段（避免未闭合 `$` 被当文本清洗后符号二次丢失）。
 * 显示公式块（$$…$$ / \\[…\\]）不上一行标题（直接吃掉，防 `$$` 源码残留）。
 */
export function sliceTitleText(src: string | null | undefined, maxPlain = 24): string {
  const s = cleanInlineSlice(src)
  let out = ''
  let plain = 0
  let i = 0
  while (i < s.length) {
    // 显示公式块：整块吃掉（标题只承载行内数学）
    if (s.startsWith('$$', i)) {
      const e = s.indexOf('$$', i + 2)
      if (e > i) {
        out += ' '
        i = e + 2
        continue
      }
    }
    if (s.startsWith('\\[', i)) {
      const e = s.indexOf('\\]', i + 2)
      if (e > i) {
        out += ' '
        i = e + 2
        continue
      }
    }
    // 行内数学：原样保留给 KaTeX（永不截断进段内）
    let end = -1
    if (s[i] === '$' && s[i - 1] !== '\\' && s[i + 1] !== '$') {
      const e = s.indexOf('$', i + 1)
      if (e > i) end = e + 1
    } else if (s.startsWith('\\(', i)) {
      const e = s.indexOf('\\)', i + 2)
      if (e > i) end = e + 2
    }
    if (end > i) {
      out += s.slice(i, end)
      i = end
      continue
    }
    if (plain >= maxPlain) return `${out.trimEnd()}…`
    out += s[i]
    plain += 1
    i += 1
  }
  return out.trim()
}

/* ── 插图 URL 解析 ───────────────────────────────────────────────── */

export const TEXTBOOK_IMAGE_BASE = '/api/teacher-v3/textbook-v2/slice-images'

/**
 * 切片内相对图路径（`images/<hash>.jpg`）→ 后端教材图资源 URL；
 * 绝对 URL 原样返回；无法解析 → null（调用方直接走降级占位）。
 */
export function sliceImageUrl(raw: string | null | undefined): string | null {
  const src = String(raw ?? '').trim()
  if (!src) return null
  if (/^https?:\/\//i.test(src)) return src
  const name = src.split(/[\\/]/).pop() || ''
  if (!/^[A-Za-z0-9_\-]{4,128}\.(?:jpg|jpeg|png|webp)$/i.test(name)) return null
  return `${TEXTBOOK_IMAGE_BASE}/${encodeURIComponent(name)}`
}

/* ── 解析：切片原文 → 块 ─────────────────────────────────────────── */

const IMAGE_LINE_RE = /^!\[([^\]]*)\]\(([^)]+)\)\s*(.*)$/
const HEADING_RE = /^#{1,6}\s*(.+)$/
const NO_RE = /^(\d+(?:\.\d+)+)\s*(.*)$/
const CAPTION_LINE_RE = /^(?:图|表)\s*[\d]+(?:[-.–—][\d]+)*\s*$|^[（(]\s*\d+\s*[)）]\s*(?:图\s*[\d]+(?:[-.–—][\d]+)*)?\s*$/

/** 从公式块源码提取编号（`\tag{2.3-1}` / 末行 `(2.3-1)`），并把编号从 tex 中剥离。 */
function splitFormulaTag(raw: string): { tex: string; tag: string | null } {
  let tag: string | null = null
  let tex = raw
  const tagMatch = /\\tag\{([^}]*)\}/.exec(tex)
  if (tagMatch) {
    tag = tagMatch[1].trim()
    tex = tex.replace(/\\tag\{[^}]*\}/, ' ')
  } else {
    const lines = tex.split('\n')
    for (let k = lines.length - 1; k >= 0; k -= 1) {
      const m = /^[（(]\s*(\d+(?:[.\-–—]\d+)+)\s*[)）]\s*$/.exec(lines[k].trim())
      if (m) {
        tag = m[1]
        lines.splice(k, 1)
        tex = lines.join('\n')
        break
      }
      if (lines[k].trim()) break
    }
  }
  return { tex: tex.trim(), tag }
}

export function parseSliceBlocks(text: string | null | undefined): SliceBlock[] {
  const lines = String(text ?? '').split(/\r?\n/)
  const out: SliceBlock[] = []
  let para: string[] = []

  const flushPara = () => {
    if (!para.length) return
    const joined = para.join(' ').trim()
    para = []
    if (joined) out.push({ kind: 'paragraph', text: cleanInlineSlice(joined) })
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) {
      flushPara()
      i += 1
      continue
    }

    // 多行/单行公式块（$$ … $$ / \[ … \]）
    const ddOpen = line.startsWith('$$')
    const brOpen = line.startsWith('\\[')
    if (ddOpen || brOpen) {
      flushPara()
      const closeTok = ddOpen ? '$$' : '\\]'
      const afterOpen = line.slice(2)
      const buf: string[] = []
      let done = afterOpen.includes(closeTok)
      if (done) {
        const idx = afterOpen.indexOf(closeTok)
        buf.push(afterOpen.slice(0, idx))
        i += 1
      } else {
        buf.push(afterOpen)
        i += 1
        while (i < lines.length) {
          const l = lines[i]
          const has = l.includes(closeTok)
          buf.push(has ? l.slice(0, l.indexOf(closeTok)) : l)
          i += 1
          if (has) break
        }
      }
      const { tex, tag } = splitFormulaTag(buf.join('\n'))
      if (tex) out.push({ kind: 'formula', tex, tag })
      continue
    }

    // 插图（`![](images/x.jpg) 图3-1-2`）→ 图块；源码永不上屏
    const img = IMAGE_LINE_RE.exec(line)
    if (img) {
      flushPara()
      let caption = (img[3] || '').trim()
      if (!caption && img[1]) caption = img[1].trim()
      if (!caption && i + 1 < lines.length && CAPTION_LINE_RE.test(lines[i + 1].trim())) {
        caption = lines[i + 1].trim()
        i += 1
      }
      out.push({
        kind: 'figure',
        src: img[2].trim(),
        caption: slicePreviewText(caption),
        alt: slicePreviewText(img[1] || caption),
      })
      i += 1
      continue
    }

    // 标题（markdown # / 无 # 的编号标题）
    const hm = HEADING_RE.exec(line)
    const body = (hm ? hm[1] : line).trim()
    const no = NO_RE.exec(body)
    if (hm || no) {
      flushPara()
      if (no) {
        // 编号标题 = 章节单元（节号徽章形态）；无编号小节（思考/探究）为普通小节标题
        out.push({
          kind: 'heading',
          level: 1,
          no: no[1],
          text: cleanInlineSlice(no[2] || ''),
        })
      } else {
        out.push({ kind: 'heading', level: 2, no: null, text: cleanInlineSlice(body) })
      }
      i += 1
      continue
    }

    // 定义 callout
    const def = /^定义\s*[：:]?\s*(.*)$/.exec(body)
    if (def) {
      flushPara()
      const rest = cleanInlineSlice(def[1] || '')
      out.push({ kind: 'definition', text: rest || cleanInlineSlice(body) })
      i += 1
      continue
    }

    // 列表项（`- / \- / 1.` 起行）
    const li = /^(?:\\-\s+|[-*•]\s+|\d+[.、]\s+)/.exec(body)
    if (li) {
      flushPara()
      out.push({ kind: 'list', text: cleanInlineSlice(body.slice(li[0].length)) })
      i += 1
      continue
    }

    para.push(body)
    i += 1
  }
  flushPara()
  return out
}

/* ── chunk 作用域（只展示本会话章节内容）──────────────────────────── */

/** 标题/章节路径里的节号（如 `3.1.1`）；无编号 → null。 */
export function sectionNoOf(text: string | null | undefined): string | null {
  const m = NO_RE.exec(String(text ?? '').trim())
  return m ? m[1] : null
}

/** SourceLocator ref 的 section id（`…/section/<uuid>/…`）；无 → null。 */
export function refSectionId(ref: string | null | undefined): string | null {
  const m = /\/section\/([0-9a-fA-F-]{36})/.exec(String(ref ?? ''))
  return m ? m[1] : null
}

/** 构造章节级 canonical ref（与后端 SourceLocator v1 一致）；缺字段 → null。 */
export function sectionRef(sourceId: string | null | undefined, sectionId: string | null | undefined): string | null {
  const uuidRe = /^[0-9a-fA-F-]{36}$/
  if (!sourceId || !sectionId || !uuidRe.test(sourceId) || !uuidRe.test(sectionId)) return null
  return `textbook_v2/locator/v1/source/${sourceId}/section/${sectionId}`
}

/** 教学活动型小节标题（思考/探究/例…）与提问式标题（「…呢？」）→ 不是新章节边界。 */
const PEDAGOGICAL_HEADING_RE =
  /^(?:思考|探究|观察|观察与思考|练习|习题|例\s*\d*|例题|定义|定理|引理|推论|证明|分析|注|说明|想一想|做一做|问题\s*\d*|实验|交流|讨论|小结|回顾|复习题|巩固|拓展|延伸|阅读|探究与发现|阅读与思考|信息技术应用)\s*[:：]?\s*\d*\s*$/

function isPedagogicalHeading(text: string): boolean {
  const t = (text || '').trim()
  if (!t) return true
  if (/[?？]([^?？]*)$/.test(t)) return true // 提问式小节（「如何建立直线 l 的方程呢？」）
  return PEDAGOGICAL_HEADING_RE.test(t)
}

/**
 * 块级作用域过滤：只保留 targetNo（含子节）内容。
 * - 块里有编号标题但都不属于本节 → 整块（chunk）剔除；
 * - 有本节编号标题 → 保留本节段落；其它编号章节段落、无编号**主题型**小节（如「圆的方程」）
 *   均为章节边界，整段剔除（教学活动型小节「思考/探究/…」与提问式标题跟随本节保留）；
 * - 无编号标题的续段 chunk → 仅当 chunkScoped（取自本会话章节视图）时保留；
 *   主题型标题的 chunk 另需与本节标题吻合（防大杂烩章节的外节 chunk 混入）。
 */
export function scopeBlocksToSection(
  blocks: SliceBlock[],
  targetNo: string | null | undefined,
  opts: { chunkScoped?: boolean; targetTitle?: string | null } = {},
): SliceBlock[] {
  const target = (targetNo || '').trim()
  const chunkScoped = opts.chunkScoped !== false
  const targetTitle = String(opts.targetTitle ?? '').trim()
  const related = (no: string) =>
    no === target || no.startsWith(`${target}.`) || (!!target && target.startsWith(`${no}.`))
  const titleMatch = (heading: string) => {
    const h = heading.trim()
    if (!h || !targetTitle) return false
    return h === targetTitle || h.includes(targetTitle) || targetTitle.includes(h)
  }
  const numbered = blocks.filter((b): b is Extract<SliceBlock, { kind: 'heading' }> => b.kind === 'heading' && !!b.no)
  const headingInScope = (b: Extract<SliceBlock, { kind: 'heading' }>) =>
    (!!b.no && !!target && related(b.no)) || titleMatch(b.text)
  if (!numbered.length) {
    if (!chunkScoped) return []
    const firstHeading = blocks.find((b) => b.kind === 'heading')
    if (firstHeading?.kind === 'heading') {
      if (!isPedagogicalHeading(firstHeading.text) && !titleMatch(firstHeading.text)) return []
    }
    return blocks
  }
  if (!target && !targetTitle) return chunkScoped ? blocks : []
  if (!numbered.some(headingInScope)) return []
  const out: SliceBlock[] = []
  let inScope = false
  for (const b of blocks) {
    if (b.kind === 'heading' && b.no) {
      inScope = headingInScope(b)
      if (inScope) out.push(b)
      continue
    }
    if (b.kind === 'heading' && inScope && !isPedagogicalHeading(b.text) && !titleMatch(b.text)) {
      inScope = false // 无编号主题型小节 = 章节边界（混入内容起点）
      continue
    }
    if (inScope) out.push(b)
  }
  return out
}

/** 提取插图（右栏「相关图片」缩略用；含图题）。 */
export function collectFigures(blocks: SliceBlock[]): { src: string | null; caption: string; alt: string }[] {
  return blocks
    .filter((b): b is Extract<SliceBlock, { kind: 'figure' }> => b.kind === 'figure')
    .map((b) => ({ src: b.src, caption: b.caption, alt: b.alt }))
}

/* ── markdown 渲染预处理（2026-10-11 会话流结构化渲染）────────────────────── */

// 教材 chunk 以 HTML <table> 承载表格（DB 实测 28/421 chunk）：转 markdown 管道表，
// 单元格以 " | " 分隔、行以换行收尾——gfm 表格可直接渲染，行列不再拼成数字流。
const HTML_TD_RE = /<\/t[dh]\s*>/gi
const HTML_TABLE_EDGE_RE = /<\/?table[^>]*>|<\/?(?:thead|tbody|tfoot|caption)\s*>/gi
const HTML_TRAILING_PIPE_RE = /\s*\|\s*\n/g

/** 块级标签 → 换行（段落结构保留；tr 归这组，td/th 归管道分隔）。 */
const HTML_BLOCK_RE = /<br\s*\/?>|<\/?t[rh]\s*>|<\/?(?:p|div|li|blockquote|h[1-6])\s*>/gi
const HTML_LEFTOVER_RE = /<\/?(?:table|thead|tbody|tfoot|caption|span|b|i|u|em|strong|sub|sup|ol|ul|li)\b[^>]*>/gi

/**
 * 助手长文本进 markdown 渲染器前的结构修复（不清洗、不压缩——交给渲染器）：
 * 1. HTML 表格 → markdown 管道表（与后端 strip_html_tags 同规则，兜住存量快照）；
 * 2. `![](images/<hash>.jpg)` → 教材图资源 URL（空 alt 也保留——图真实可渲染后不再删）。
 * 标签剥除用**白名单**（与后端 text_safety 同哲学）：数学文本里的 `a<b>c` 不得误伤。
 * 数学 $...$ 原样透传给 marked-katex-extension。
 */
export function forMarkdown(src: string | null | undefined): string {
  let s = String(src ?? '')
  if (!s) return ''
  s = s.replace(HTML_TD_RE, ' | ')
  s = s.replace(HTML_BLOCK_RE, '\n')
  s = s.replace(HTML_TABLE_EDGE_RE, '\n')
  s = s.replace(HTML_TRAILING_PIPE_RE, '\n')
  s = s.replace(HTML_LEFTOVER_RE, '')
  s = s.replace(/!\[([^\]]*)\]\(([^)]*)\)/g, (m: string, alt: string, url: string) => {
    const resolved = sliceImageUrl(url)
    return resolved ? `![${alt}](${resolved})` : alt || ''
  })
  return s
}

/** 文本是否带 markdown 结构（标题/表格/图片/管道行）——决定气泡走 markdown 还是纯文本渲染。 */
export function hasMarkdownStructure(src: string | null | undefined): boolean {
  const s = String(src ?? '')
  return /^#{1,6}\s/m.test(s) || /<table[\s>]/i.test(s) || /!\[[^\]]*\]\(/.test(s) || /^\|.+\|\s*$/m.test(s)
}
