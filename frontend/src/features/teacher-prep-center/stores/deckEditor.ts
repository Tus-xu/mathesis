/**
 * PPT 页 deck 编辑状态源（pinia，R4）。
 *
 * 数据源：
 * - 权威 deck spec 来自会话工作区快照 `artifact.teaching_deck`（8100 投影/patch 唯一事实）；
 * - 手工编辑 = deck patch 通道（提案→应用→revision+1→刷新快照）；
 * - AI 单页修改 / 快捷操作 / 添加页 = deck regen / pages 通道（refs 齐全，真实 LLM）。
 *
 * 保存状态（4.8）：`savedAt` 只来自服务端持久化时间（deck.updated_at / apply 返回 revision），
 * 禁止前端编造时间；保存中/失败状态真实。
 *
 * 手工编辑队列（R5）：patchBusy / 保存失败期间的编辑**入队**（不静默丢弃），flush 时
 * 每条基于最新 store 状态重新推导 operations（绝不重放陈旧 elements 快照）。
 */
import { defineStore } from 'pinia'
import {
  addDeckPage,
  applyDeckPatch,
  createDeckPatch,
  enrichDeck,
  ensureDeckFigures,
  regenDeckPage,
  type DeckArtifact,
  type DeckPatchProposalView,
  type DeckRefs,
  type DeckSlide,
  type MathObject,
  type PatchOp,
  type SlideElement,
} from '@/features/teacher-prep-center/api/deckApi'
import { getExportJob, type ExportJobView } from '@/features/teacher-prep-center/api/exportApi'
import { PrepCenterApiError } from '@/features/teacher-prep-center/api/prepCenterApi'
import { syncSlideFormulas } from '@/features/teacher-prep-center/math/ellipseSync'
import { usePrepSessionStore } from './prepSession'

export type DeckSaveState = 'idle' | 'saving' | 'saved' | 'error'
export type DeckAiTask = 'simplify' | 'practice' | 'add_page' | 'modify_page' | 'derive' | null
/** 页结构类型再导出（对外 getter 以 DeckSlide 为面；测试/组件统一从 store 取类型）。 */
export type { DeckSlide }

/** 封面判定（与 SlideCanvas 一致；用于序号徽标派生）。 */
export function isCoverSlide(s: DeckSlide | null | undefined): boolean {
  return !!s && (s.type === 'title' || s.layout_hint === 'title')
}

/* ── 渲染层文本净化（VISUAL-SPEC-V3 §3-S4.4 / §3-S4.5；确定性纯函数，唯一实现）──
 * 1) `stripChapterNo`：课件页眉/封面只呈现**课题名**，行首章节号（`3.1.2 ` / `3、` / `第3章 `）不上屏；
 * 2) `isTeachingAnnotation`：以教学动作词开头且整句不含数学符号的**教研批注**（`引导学生…`）
 *    不是课件正文，渲染层确定性剔除（不能只依赖后端清理）；
 * 3) `sanitizeDeckText`：内部修订类括注标记（EDIT_MARK_RE 命中的括注）与空括号不上屏。 */

/** 行首章节号；只剥离编号本身，其余文字原样保留（剥离后为空则回退原文，不丢信息）。 */
const CHAPTER_NO_RE =
  /^\s*(?:第\s*[\d一二三四五六七八九十百]+\s*[章节讲单元课时]\s*|\d+(?:[.\-]\d+)+[\s、.·:：]*|\d+\s*[、.·:：]\s*)/

export function stripChapterNo(raw: string | null | undefined): string {
  const t = (raw || '').trim()
  const stripped = t.replace(CHAPTER_NO_RE, '').trim()
  return stripped || t
}

/** 课件页眉课题名（唯一口径）：会话课题名 → deck 标题，均剥离行首章节号。 */
export function deckCourseTitle(sessionTitle: string | null | undefined, deckTitle: string | null | undefined): string {
  return stripChapterNo(sessionTitle) || stripChapterNo(deckTitle)
}

/** 教研批注特征（主编裁定）：教学动作词开头 + 整句不含数学符号。 */
const TEACHING_NOTE_RE = /^\s*(?:引导学生|帮助学生|让学生|教师|通过[^，。；]{0,30}?(?:使学生|让学生|引导学生))/
/** 数学符号/公式痕迹：出现任一即判定为「含数学」，不按批注剔除。 */
const MATH_SYMBOL_RE = /[$\\=＋+\-−×÷≤≥≈√∑∫^_<>]|\d\s*[a-zA-Z]|[a-zA-Z]\s*\^/

export function isTeachingAnnotation(raw: string | null | undefined): boolean {
  const t = (raw || '').trim()
  if (!t) return false
  return TEACHING_NOTE_RE.test(t) && !MATH_SYMBOL_RE.test(t)
}

const EDIT_MARK_RE = /[（(][^）)]*(教师修订|教师新增|教师删除|教师补充|待确认|待补充|待完善|占位|TODO)[^）)]*[）)]/g

export function sanitizeDeckText(raw: string | null | undefined): string {
  return (raw || '')
    .replace(EDIT_MARK_RE, '')
    .replace(/[（(]\s*[）)]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/** 渲染层可直接上屏的正文片段（净化 + 剔除教研批注）；空串表示「不上屏」。 */
export function cleanDeckText(raw: string | null | undefined): string {
  const t = sanitizeDeckText(raw)
  return isTeachingAnnotation(t) ? '' : t
}

/* ── G14 正文→版式同步（确定性投影；无 LLM、无编造）─────────────────────────
 * `regen(mode=content)` 只重写 `slide.content`，而画布按 `slide.elements` 渲染、
 * 导出链按正文渲染 —— 不同步则「教师看不到修改生效」且画布≠导出（R3c 实测缺陷）。
 * content 落库后把正文行**确定性投影**回语义元素（figure/note 保留，正文重排），
 * 保证 画布=导出=落库 三者一致。规则与导出边界 `elements→正文` 展开互逆：
 * 纯公式行 → formula（`$...$`）；≥2 行带编号 → steps（去编号，渲染层重新编号）；
 * 其余 → bullets。 */
const PURE_FORMULA_RE = /^\$([^$]+)\$$/
const NUMBERED_LINE_RE = /^\s*\d+[.、]\s*/

function clipItem(s: string): string {
  return s.length > 200 ? s.slice(0, 200) : s
}

export function bodyElementsFromContent(
  content: string | null | undefined,
  opts: { keep?: SlideElement[]; preferSteps?: boolean } = {},
): SlideElement[] {
  const keep = (opts.keep ?? []).filter((e) => e.role === 'figure' || e.role === 'note')
  const lines = (content || '')
    .split('\n')
    .map((s) => sanitizeDeckText(s))
    .filter((s) => s && !isTeachingAnnotation(s))
    .slice(0, 12)
  const formulas: SlideElement[] = []
  const textLines: string[] = []
  for (const ln of lines) {
    const m = PURE_FORMULA_RE.exec(ln.trim())
    if (m) formulas.push({ role: 'formula', latex: m[1].slice(0, 2000) })
    else textLines.push(ln)
  }
  const body: SlideElement[] = []
  if (textLines.length) {
    const numbered = textLines.filter((l) => NUMBERED_LINE_RE.test(l)).length
    // 推导页即使模型漏写编号，也按「分步」渲染，保证可逐步播放（不把推导降级成普通 bullet）。
    const asSteps = numbered >= 2 || (!!opts.preferSteps && textLines.length >= 2)
    const items = (asSteps ? textLines.map((l) => l.replace(NUMBERED_LINE_RE, '')) : textLines)
      .map(clipItem)
      .filter(Boolean)
    if (items.length) body.push(asSteps ? { role: 'steps', items } : { role: 'bullets', items })
  }
  return [
    ...keep.filter((e) => e.role === 'figure'),
    ...body,
    ...formulas,
    ...keep.filter((e) => e.role === 'note'),
  ]
}
/** 逐页增稠完成后，`enrich_result` durable 事件里的真实计数（禁止前端编造）。 */
export interface DeckEnrichCounts {
  pages: number
  enriched: number
  fallback: number
  densityOk: number
  totalMs: number
}

/** 增稠作业状态 → 教师可读阶段文案（内部英文枚举不上屏）。 */
function enrichStageText(job: Pick<ExportJobView, 'status'>): string {
  switch (job.status) {
    case 'pending':
      return '增稠任务排队中…'
    case 'running':
      return '正在逐页充实课件内容…'
    case 'done':
      return '课件内容充实完成'
    case 'failed':
      return '增稠失败'
    case 'cancelled':
      return '增稠已取消'
    default:
      return '正在处理…'
  }
}

/** 从作业 durable 事件取真实的增稠结果计数（无则 null，绝不伪造）。 */
function readEnrichCounts(job: Pick<ExportJobView, 'events'>): DeckEnrichCounts | null {
  const evs = job.events || []
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i]
    if (e?.type !== 'enrich_result' || !e.detail) continue
    const d = e.detail as Record<string, unknown>
    const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
    return {
      pages: num(d.pages),
      enriched: num(d.enriched_pages),
      fallback: num(d.fallback_pages),
      densityOk: num(d.density_ok_pages),
      totalMs: num(d.total_ms),
    }
  }
  return null
}

/** 实时进度（G13）：`progress` durable 事件的真实 n/N（作业每完成一页发一条）。 */
export interface DeckEnrichProgress {
  done: number
  total: number
}

function readEnrichProgress(job: Pick<ExportJobView, 'events'>): DeckEnrichProgress | null {
  const evs = job.events || []
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i]
    if (e?.type !== 'progress' || !e.detail) continue
    const d = e.detail as Record<string, unknown>
    const done = typeof d.done === 'number' && Number.isFinite(d.done) ? d.done : 0
    const total = typeof d.total === 'number' && Number.isFinite(d.total) ? d.total : 0
    if (total > 0) return { done, total }
  }
  return null
}

/** 单页失败明细（G14）：enrich_result.errors 的真实逐页错误（保留已完成页，只重试失败页）。 */
export interface DeckPageFailure {
  slideId: string
  /** 0 基页下标（由 slide id 反查；查不到为 -1，调用方诚实省略页号）。 */
  index: number
  error: string
}

function readPageFailures(
  job: Pick<ExportJobView, 'events'>,
  slides: DeckSlide[],
): DeckPageFailure[] {
  const evs = job.events || []
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i]
    if (e?.type !== 'enrich_result' || !e.detail) continue
    const errors = (e.detail as Record<string, unknown>).errors
    if (!errors || typeof errors !== 'object') return []
    return Object.entries(errors as Record<string, unknown>)
      .filter(([, v]) => !!v)
      .map(([slideId, v]) => ({
        slideId,
        index: slides.findIndex((s) => s.id === slideId),
        error: typeof v === 'string' ? v : '生成失败',
      }))
  }
  return []
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 增稠失败原因 → 教师可读中文（禁止把后端/代理原始串直接上屏，VISUAL-SPEC §7）。 */
function enrichErrorText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    if (e.kind === 'network') return '网络异常，未能提交增稠任务，请检查网络后重试'
    if (e.kind === 'unauthenticated') return '登录已失效，请重新登录后重试'
    if (e.kind === 'conflict') return '课件已被更新，请刷新页面后重试'
    if (e.kind === 'rate_limited') return '请求过于频繁，请稍后重试'
    const detail = (e.detail || '').trim()
    // 技术兜底串（如 artifact api error: 500）不上屏，统一给产品化文案
    if (detail && !/api error\s*:/i.test(detail)) return detail
    return '课件增稠服务暂时不可用，请稍后重试'
  }
  if (e instanceof Error && e.message && !/api error\s*:/i.test(e.message)) return e.message
  return '课件增稠服务暂时不可用，请稍后重试'
}

/** 手工编辑保存失败原因 → 教师可读中文（错误条直接上屏；技术兜底串不上屏）。 */
function saveErrorText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    if (e.kind === 'network') return '网络异常，本次修改未能保存，请检查网络后重试'
    if (e.kind === 'unauthenticated') return '登录已失效，请重新登录后重试'
    if (e.kind === 'conflict') return '课件刚被其它窗口更新，点击重试会以最新内容重新保存'
    if (e.kind === 'forbidden') return '当前账号没有编辑此课件的权限'
    if (e.kind === 'rate_limited') return '操作过于频繁，请稍等片刻再点击重试'
    const detail = (e.detail || '').trim()
    if (detail && !/api error\s*:/i.test(detail)) return detail
    return '保存暂时未成功，请点击重试'
  }
  if (e instanceof Error && e.message && !/api error\s*:/i.test(e.message)) return e.message
  return '保存暂时未成功，请点击重试'
}

/* ── 提交前预校验（与 ppt_v2 spec.py §A.3 同口径；SlideElement.vue:141-143 曾对
 *    latex>2000 静默 return —— 超限必须行内红字可见，禁止静默丢弃教师输入）── */
export const DECK_ELEMENT_LIMITS = {
  /** 正文 ≤400 字（spec.py SlideElement.text max_length）。 */
  text: 400,
  /** 显示式 LaTeX ≤2000（spec.py SlideElement.latex max_length）。 */
  latex: 2000,
  /** 卡片标题 ≤60 字（spec.py SlideElement.title max_length）。 */
  title: 60,
  /** 列表 ≤10 项（spec.py SlideElement.items max_length）。 */
  items: 10,
  /** 列表每项 ≤200 字（spec.py _each_item_bounded）。 */
  item: 200,
} as const

/** 超限提示文案（行内红字直接展示字数 / 上限；空串表示未超限）。 */
export function elementLimitMessage(el: {
  text?: string | null
  latex?: string | null
  title?: string | null
  items?: string[] | null
}): string {
  const L = DECK_ELEMENT_LIMITS
  if (el.text && el.text.length > L.text) return `正文 ${el.text.length}/${L.text} 字，超出上限，请精简后再提交`
  if (el.latex && el.latex.length > L.latex) return `公式 ${el.latex.length}/${L.latex} 字，超出上限，请拆分后再提交`
  if (el.title && el.title.length > L.title) return `标题 ${el.title.length}/${L.title} 字，超出上限，请精简后再提交`
  if ((el.items?.length ?? 0) > L.items) return `列表 ${el.items!.length}/${L.items} 项，超出上限`
  const overIdx = (el.items ?? []).findIndex((it) => it.length > L.item)
  if (overIdx >= 0) return `第 ${overIdx + 1} 条 ${el.items![overIdx].length}/${L.item} 字，超出上限，请精简后再提交`
  return ''
}

/**
 * flush 时在**最新**元素数组中重定位元素槽位：优先原下标（未被移动时直接命中），
 * 否则按快照内容扫描（排队期间若发生插入/删除，按内容找到该元素，绝不按陈旧下标错写）。
 */
function relocateIndex(arr: SlideElement[], index: number, base: SlideElement | null): number {
  if (index >= 0 && index < arr.length) {
    const cur = arr[index]
    if (!base || cur === base || JSON.stringify(cur) === JSON.stringify(base)) return index
  }
  if (base) {
    const snap = JSON.stringify(base)
    const found = arr.findIndex((el) => JSON.stringify(el) === snap)
    if (found >= 0) return found
  }
  return -1
}

interface DeckEditorState {
  deck: DeckArtifact | null
  deckId: string | null
  revision: number
  /** 服务端最近持久化时间（真实 revision updated_at）。 */
  savedAt: string | null
  saveState: DeckSaveState
  saveError: string | null
  /** 当前页下标（0 基）。 */
  currentIndex: number
  /** 画布内数学图形是否被选中（属性面板 图形属性 上下文）。 */
  mathSelected: boolean
  /** 进行中的 patch（乐观 UI 期间刷新快照可能晚到）。 */
  patchBusy: boolean
  /** 待重发 patch 队列（patchBusy / 错误态期间的编辑入队；flush 时基于最新状态重新推导）。 */
  patchQueue: QueuedPatch[]
  /** 队列排水中（同一时刻只允许一个 flush 循环）。 */
  flushing: boolean
  aiTask: DeckAiTask
  aiBusy: boolean
  aiStageLabel: string
  aiError: string | null
  /** 预览演示全屏浮层。 */
  previewOpen: boolean
  /** 预览是否以「逐步演示」模式打开（推导动画；null/false=整页静态）。 */
  previewDemo: boolean
  /** 为本课补数学图（确定性资产）。 */
  figuresBusy: boolean
  figuresError: string | null
  /** 图形属性编辑草稿（属性面板即时驱动画布重渲；落库后清空）。 */
  draftMath: MathObject | null
  /* ── 逐页增稠（骨架 → 板书内容；真实异步作业）──────────────────────── */
  enrichBusy: boolean
  enrichJobId: string | null
  enrichStage: string
  /** 作业提交的本地时刻（用于「已用时」真实计时；非进度百分比）。 */
  enrichStartedAt: number | null
  enrichError: string | null
  /** 完成后读到的真实计数（enrich_result durable 事件）。 */
  enrichCounts: DeckEnrichCounts | null
  /** 实时进度（G13）：progress durable 事件的真实 n/N；作业未开始/无事件为 null。 */
  enrichProgress: DeckEnrichProgress | null
  /** 单页失败明细（G14）：enrich_result.errors 真实逐页错误，仅这些页可单页重试。 */
  enrichFailedPages: DeckPageFailure[]
}

function errText(e: unknown): string {
  if (e instanceof PrepCenterApiError) return e.detail || e.message
  if (e && typeof e === 'object' && 'detail' in e && typeof (e as { detail?: unknown }).detail === 'string') {
    return (e as { detail: string }).detail
  }
  if (e instanceof Error) return e.message
  return String(e)
}

/** 待重发 patch 队列条目：flush 时调用 derive 基于最新 store 状态重新推导 operations，
 *  绝不重放入队时捕获的陈旧 elements/content 快照（否则会静默回退排队期间的其它变更）。 */
interface QueuedPatch {
  summary: string
  derive: () => PatchOp[] | null
}

export const useDeckEditorStore = defineStore('prepCenterDeckEditor', {
  state: (): DeckEditorState => ({
    deck: null,
    deckId: null,
    revision: 0,
    savedAt: null,
    saveState: 'idle',
    saveError: null,
    currentIndex: 0,
    mathSelected: false,
    patchBusy: false,
    patchQueue: [],
    flushing: false,
    aiTask: null,
    aiBusy: false,
    aiStageLabel: '',
    aiError: null,
    previewOpen: false,
    previewDemo: false,
    figuresBusy: false,
    figuresError: null,
    draftMath: null,
    enrichBusy: false,
    enrichJobId: null,
    enrichStage: '',
    enrichStartedAt: null,
    enrichError: null,
    enrichCounts: null,
    enrichProgress: null,
    enrichFailedPages: [],
  }),

  getters: {
    slides(state): DeckSlide[] {
      return Array.isArray(state.deck?.slides) ? (state.deck?.slides as DeckSlide[]) : []
    },
    pageCount(): number {
      return this.slides.length
    },
    currentSlide(state): DeckSlide | null {
      return this.slides[state.currentIndex] ?? null
    },
    deckTitle(state): string {
      return state.deck?.metadata?.title || state.deck?.title || '课件'
    },
    /** 顶栏「已保存 HH:MM」（真实持久化时间；无效则空）。 */
    savedClockText(state): string | null {
      if (!state.savedAt) return null
      const d = new Date(state.savedAt)
      if (Number.isNaN(d.getTime())) return null
      const hh = String(d.getHours()).padStart(2, '0')
      const mm = String(d.getMinutes()).padStart(2, '0')
      return `${hh}:${mm}`
    },
    /** 各页「非封面序号」（1 基；封面为 0）——序号徽标派生（契约 §A.4）。 */
    slideOrdinals(state): number[] {
      const slides = Array.isArray(state.deck?.slides) ? (state.deck?.slides as DeckSlide[]) : []
      let n = 0
      return slides.map((s) => (isCoverSlide(s) ? 0 : ++n))
    },
    /** 非封面页的元素密度统计（用于「骨架稀薄」判定；真实 spec 派生）。 */
    deckElementStats(state): { nonCover: number; totalElements: number; avgPerPage: number } {
      const slides = Array.isArray(state.deck?.slides) ? (state.deck?.slides as DeckSlide[]) : []
      const nonCover = slides.filter((s) => !isCoverSlide(s))
      const totalElements = nonCover.reduce((n, s) => n + ((s.elements ?? []).length || 0), 0)
      return {
        nonCover: nonCover.length,
        totalElements,
        avgPerPage: nonCover.length ? totalElements / nonCover.length : 0,
      }
    },
    /** 课件骨架是否稀薄（页均元素 < 3 → 提供真实增稠入口；已增稠则隐藏）。 */
    deckThin(): boolean {
      const st = this.deckElementStats
      return !!this.deckId && st.nonCover > 0 && st.avgPerPage < 3
    },
    /** 待重发的暂存修改数（错误条「N 处修改已暂存」真实计数）。 */
    pendingPatchCount(state): number {
      return state.patchQueue.length
    },
  },

  actions: {
    /** 从权威工作区快照同步 deck（DeckPane watch；编辑临界时延迟采用）。 */
    syncFromWorkspace(deck: DeckArtifact | null) {
      if (!deck || !deck.deck_id) {
        this.deck = null
        this.deckId = null
        return
      }
      if (this.patchBusy || this.aiBusy) return // 编辑/AI 临界：稍后由本 store 主动刷新
      const changed = deck.deck_id !== this.deckId || deck.revision !== this.revision
      this.deck = deck
      this.deckId = String(deck.deck_id)
      this.revision = Number(deck.revision ?? 0)
      if (deck.updated_at) this.savedAt = deck.updated_at
      if (this.currentIndex >= this.pageCount) this.currentIndex = Math.max(0, this.pageCount - 1)
      // 错误态且尚有暂存修改时不降级状态（错误条必须保持可见，等待用户重试/放弃）
      if (changed && !(this.saveState === 'error' && this.patchQueue.length > 0)) {
        this.saveState = this.saveState === 'saving' ? 'saving' : 'saved'
      }
    },

    session(): ReturnType<typeof usePrepSessionStore> {
      return usePrepSessionStore()
    },

    /** 拉权威快照刷新 deck（patch/apply 后唯一收口通道；savedAt 来自服务端）。 */
    async refreshFromServer() {
      const prep = this.session()
      const sid = prep.currentSessionId
      if (!sid) return
      await prep.refreshCurrent(sid)
      const deck = prep.current?.artifact.teaching_deck ?? null
      if (deck && deck.deck_id === this.deckId) {
        this.deck = deck
        this.revision = Number(deck.revision ?? 0)
        if (deck.updated_at) this.savedAt = String(deck.updated_at)
      }
    },

    /** AI refs：真实版本锚点（approved blueprint / context hash 来自投影，spec_revision=当前版本）。 */
    buildRefs(): DeckRefs {
      const deck = this.deck
      const projection = deck?.projection
      const blueprintId = String(projection?.approved_blueprint_id || '')
      if (!blueprintId) throw new Error('课件还没有关联课堂设计，暂时无法发起 AI 修改')
      return {
        approved_blueprint_id: blueprintId,
        blueprint_revision: Number(projection?.approved_blueprint_revision ?? 0),
        spec_revision: this.revision,
        prompt_ref: { prompt_id: 'pc-deck-regen-v1' },
        context_manifest_id: String(projection?.context_snapshot_hash || projection?.run_hash || 'ctx'),
      }
    },

    /** patch 通道：提案→应用→刷新。成功 revision+1 且 savedAt=服务端时间。 */
    async patch(operations: PatchOp[], summary: string): Promise<boolean> {
      if (!this.deckId || this.patchBusy) return false
      this.patchBusy = true
      this.saveState = 'saving'
      this.saveError = null
      try {
        const proposal = await createDeckPatch(this.deckId, {
          expected_revision: this.revision,
          summary,
          operations,
        })
        await applyDeckPatch(this.deckId, proposal.patch_id, this.revision)
        await this.refreshFromServer()
        this.saveState = 'saved'
        // patchBusy 期间入队的编辑：本轮保存完成后自动续发（微任务时 patchBusy 已复位）
        if (this.patchQueue.length) {
          queueMicrotask(() => void this.flushQueue())
        }
        return true
      } catch (e) {
        this.saveState = 'error'
        this.saveError = saveErrorText(e)
        await this.refreshFromServer() // 冲突/失败 → 以服务端权威状态收口
        return false
      } finally {
        this.patchBusy = false
      }
    },

    /**
     * 编辑受理入口（所有手工编辑动作的统一通道）：
     * - 空闲 → 立即推导 operations 发送；
     * - patchBusy / 保存失败错误态 → **入队**（不静默丢弃教师编辑），由本轮 patch
     *   完成后的续发或教师点击「重试」时 flush；
     * - flush 永远调用 derive 基于最新 store 状态重新推导（绝不重放陈旧快照）。
     */
    async submitPatch(summary: string, derive: () => PatchOp[] | null): Promise<boolean> {
      if (!this.deckId) return false
      if (this.patchBusy || this.saveState === 'error') {
        this.patchQueue.push({ summary, derive })
        return true
      }
      const ops = derive()
      if (!ops || ops.length === 0) return false
      const ok = await this.patch(ops, summary)
      if (!ok) {
        // 失败必须可见：显式置错误态（真实 patch 通道已置；此处幂等兜底），
        // 失败原因已由 patch 写入 saveError 时保留原文。
        this.saveState = 'error'
        if (!this.saveError) this.saveError = '保存暂时未成功，请点击重试'
        // 失败的操作入队保留：错误条可见，重试时基于最新状态重新推导再发
        this.patchQueue.push({ summary, derive })
        return false
      }
      return ok
    },

    /**
     * 队列排空：逐条 derive → patch（严格串行；一条失败即停，队首保留待重试）。
     * derive 在每条发送前执行 —— 前一条落库刷新后，后一条基于最新状态推导。
     */
    async flushQueue(): Promise<boolean> {
      if (this.flushing || this.patchBusy) return false
      if (this.patchQueue.length === 0) return true
      this.flushing = true
      try {
        while (this.patchQueue.length > 0) {
          const entry = this.patchQueue[0]
          const ops = entry.derive()
          if (!ops || ops.length === 0) {
            this.patchQueue.shift() // 条件已失效（如目标元素已被删除）→ 诚实跳过
            continue
          }
          const ok = await this.patch(ops, entry.summary)
          if (!ok) break // 失败：队首保留，错误条可见，等待重试
          this.patchQueue.shift()
        }
      } finally {
        this.flushing = false
      }
      // 队列排空（含失败保留队首的情况——那条路径 length>0 不会走到这里）→ 全部暂存修改已重发成功，收敛为已保存
      if (this.patchQueue.length === 0) this.saveState = 'saved'
      return this.patchQueue.length === 0
    },

    /** 错误条「重试」：重发暂存修改（每条基于最新状态重新推导）。 */
    async retrySave(): Promise<boolean> {
      if (this.flushing || this.patchBusy) return false
      return this.flushQueue()
    },

    /** 错误条「放弃修改」：丢弃暂存修改，恢复为服务端最近保存的内容（显式两步确认后触发）。 */
    async discardPendingEdits(): Promise<boolean> {
      this.patchQueue = []
      this.saveError = null
      this.saveState = 'saved'
      try {
        await this.refreshFromServer()
      } catch {
        // 刷新失败不阻断收口：本地已对齐最近一次已知的服务端状态
      }
      return true
    },

    /** 错误条「×」（仅在无暂存修改时出现）：清除错误提示并对齐服务端权威状态。 */
    dismissSaveError() {
      if (this.patchQueue.length > 0) return
      this.saveError = null
      this.saveState = 'saved'
      void this.refreshFromServer().catch(() => undefined)
    },

    /**
     * math_object（参数/开关/样式）整体落库：数据→图形→公式同源（G17）。
     * 同一 patch 内把可识别的椭圆规范式公式（标准方程 / c²=a²−b² / c=√(a²−b²)）
     * 代入当前 a/b/c —— 改参数 → 公式/焦点/标注同步，且 画布=导出=落库 一致。
     * flush 推导：公式同步永远作用于最新 elements/content（排队期间不错写）。
     */
    async updateMathObject(slideId: string, mathObject: MathObject | null): Promise<boolean> {
      return this.submitPatch(`调整图形参数（${slideId}）`, () => {
        const ops: PatchOp[] = [{ op: 'set_slide', target: slideId, field: 'math_object', value: mathObject }]
        const slide = this.slides.find((s) => s.id === slideId)
        if (slide && mathObject) {
          const synced = syncSlideFormulas(slide, mathObject)
          if (synced.elements) ops.push({ op: 'set_slide', target: slideId, field: 'elements', value: synced.elements })
          if (typeof synced.content === 'string') {
            ops.push({ op: 'set_slide', target: slideId, field: 'content', value: synced.content })
          }
        }
        return ops
      })
    },

    /** 工具条：文本/公式 → 追加真实可编辑元素（content 行）并落库。 */
    async appendContentLine(slideId: string, line: string): Promise<boolean> {
      return this.submitPatch(`追加元素（${slideId}）`, () => {
        const slide = this.slides.find((s) => s.id === slideId)
        if (!slide) return null
        const base = slide.content ?? ''
        const next = base ? `${base}\n${line}` : line
        return [{ op: 'set_slide', target: slideId, field: 'content', value: next }]
      })
    },

    /**
     * 语义元素整体落库（元素路径；FROZEN-CONTRACT §A）。
     * 注意：仅在调用方已基于**最新** slides 组装数组时使用（如 G14 正文投影）；
     * 排队重发时按提交时的数组原样重试。逐项编辑请走 updateElement / removeElement / moveElement。
     */
    async updateElements(slideId: string, elements: SlideElement[]): Promise<boolean> {
      return this.submitPatch(`编辑版式元素（${slideId}）`, () => [
        { op: 'set_slide', target: slideId, field: 'elements', value: elements },
      ])
    },

    /** 追加一个语义元素（工具条「文本 / 公式」走此通道，真实落库）。 */
    async appendElement(slideId: string, element: SlideElement): Promise<boolean> {
      const slide = this.slides.find((s) => s.id === slideId)
      if (!slide) return false
      return this.submitPatch(`追加元素（${slideId}）`, () => {
        const cur = this.slides.find((s) => s.id === slideId)
        if (!cur) return null
        return [{ op: 'set_slide', target: slideId, field: 'elements', value: [...(cur.elements ?? []), element] }]
      })
    },

    /** 更新单个语义元素（双击在线编辑落库；flush 时按最新数组重定位，不写错槽位）。 */
    async updateElement(slideId: string, index: number, next: SlideElement): Promise<boolean> {
      const slide = this.slides.find((s) => s.id === slideId)
      if (!slide) return false
      const els = slide.elements ?? []
      if (index < 0 || index >= els.length) return false
      const base = els[index]
      return this.submitPatch(`编辑版式元素（${slideId}）`, () => {
        const cur = this.slides.find((s) => s.id === slideId)
        const arr = [...(cur?.elements ?? [])]
        const i = relocateIndex(arr, index, base)
        if (i < 0) return null
        arr[i] = next
        return [{ op: 'set_slide', target: slideId, field: 'elements', value: arr }]
      })
    },

    /** 删除单个版式元素（元素块 hover「✕」；flush 时重定位，绝不按陈旧下标错删）。 */
    async removeElement(slideId: string, index: number): Promise<boolean> {
      const slide = this.slides.find((s) => s.id === slideId)
      if (!slide) return false
      const els = slide.elements ?? []
      if (index < 0 || index >= els.length) return false
      const base = els[index]
      return this.submitPatch(`删除元素（${slideId}）`, () => {
        const cur = this.slides.find((s) => s.id === slideId)
        const arr = [...(cur?.elements ?? [])]
        const i = relocateIndex(arr, index, base)
        if (i < 0) return null
        arr.splice(i, 1)
        return [{ op: 'set_slide', target: slideId, field: 'elements', value: arr }]
      })
    },

    /**
     * 同 role 邻居间上移/下移（dir=-1 上移 / +1 下移）。
     * 跨 role 拒绝：渲染层按 role 槽位固定 CSS，跨 role 换位槽位语义错位且视觉无反馈。
     */
    async moveElement(slideId: string, index: number, dir: -1 | 1): Promise<boolean> {
      const slide = this.slides.find((s) => s.id === slideId)
      if (!slide) return false
      const els = slide.elements ?? []
      if (index < 0 || index >= els.length) return false
      const base = els[index]
      return this.submitPatch(`调整元素顺序（${slideId}）`, () => {
        const cur = this.slides.find((s) => s.id === slideId)
        const arr = [...(cur?.elements ?? [])]
        const i = relocateIndex(arr, index, base)
        if (i < 0) return null
        const j = i + dir
        if (j < 0 || j >= arr.length || arr[j].role !== base.role) return null
        const moved = arr[i]
        arr[i] = arr[j]
        arr[j] = moved
        return [{ op: 'set_slide', target: slideId, field: 'elements', value: arr }]
      })
    },

    /** 在线编辑内容行（blur/Enter 提交）。 */
    async updateContent(slideId: string, content: string): Promise<boolean> {
      return this.submitPatch(`编辑正文（${slideId}）`, () => [
        { op: 'set_slide', target: slideId, field: 'content', value: content },
      ])
    },

    /** 在线编辑页标题（blur/Enter 提交；空标题拒绝——title 为 spec 必填）。 */
    async updateSlideTitle(slideId: string, title: string): Promise<boolean> {
      const next = (title || '').trim()
      if (!next) return false
      return this.submitPatch(`编辑页标题（${slideId}）`, () =>
        next ? [{ op: 'set_slide', target: slideId, field: 'title', value: next }] : null,
      )
    },

    /** 页操作：删除页。 */
    async removePage(index: number): Promise<boolean> {
      const slide = this.slides[index]
      if (!slide) return false
      const targetId = slide.id
      const ok = await this.submitPatch(`删除第 ${index + 1} 页`, () => {
        // flush 时按 slide id 重查（排队期间页序/页数可能已变，不按陈旧下标删页）
        const cur = this.slides.find((s) => s.id === targetId)
        return cur ? [{ op: 'remove_slide', target: targetId }] : null
      })
      if (ok && this.currentIndex >= this.pageCount) this.currentIndex = Math.max(0, this.pageCount - 1)
      return ok
    },

    /** 页操作：排序（dir=-1 上移 / +1 下移）。 */
    async movePage(index: number, dir: -1 | 1): Promise<boolean> {
      const target = this.slides[index]
      const to = index + dir
      if (!target || to < 0 || to >= this.pageCount) return false
      const targetId = target.id
      const ok = await this.submitPatch(
        `调整页序（第 ${index + 1} 页${dir < 0 ? '上移' : '下移'}）`,
        () => {
          const idx = this.slides.findIndex((s) => s.id === targetId)
          const dest = idx + dir
          if (idx < 0 || dest < 0 || dest >= this.pageCount) return null
          return [{ op: 'move_slide', target: targetId, value: { index: dest } }]
        },
      )
      if (ok) this.currentIndex = to
      return ok
    },

    /**
     * AI 任务共用：发起到 apply 的全链（真实 LLM，1-3 分钟；受理/运行/完成状态可见）。
     * proposal 返回后自动 apply（教师指令即意图）；失败诚实报错。
     * `opts.bodySyncSlideId`：content 模式重写正文后同步版式元素（G14 三者一致）。
     */
    async runAiTask(
      task: Exclude<DeckAiTask, null>,
      fn: () => Promise<DeckPatchProposalView>,
      opts: { bodySyncSlideId?: string } = {},
    ): Promise<boolean> {
      if (this.aiBusy || !this.deckId) return false
      this.aiBusy = true
      this.aiTask = task
      this.aiError = null
      this.aiStageLabel = 'AI 已受理，正在生成…'
      try {
        const proposal = await fn()
        this.aiStageLabel = 'AI 生成完成，正在应用修改…'
        await applyDeckPatch(this.deckId, proposal.patch_id, this.revision)
        await this.refreshFromServer()
        // G14：正文重写必须对教师可见 —— 元素同步失败视为未完成（正文已落库，不丢失）
        if (opts.bodySyncSlideId) {
          this.aiStageLabel = '正在同步版式…'
          const synced = await this.syncBodyElementsFromContent(opts.bodySyncSlideId)
          if (!synced) {
            this.aiError = '正文已更新，但版式同步失败，请刷新页面查看最新内容'
            return false
          }
        }
        this.saveState = 'saved'
        this.aiStageLabel = ''
        return true
      } catch (e) {
        this.aiError = errText(e)
        await this.refreshFromServer().catch(() => undefined)
        return false
      } finally {
        this.aiBusy = false
        this.aiTask = null
        this.aiStageLabel = ''
      }
    },

    /**
     * G14：content 重写落库后，把正文确定性投影回 `slide.elements` 并落库（revision+1）。
     * 画布按 elements 渲染、导出按正文渲染 —— 两者同源才能 画布=导出=落库。
     */
    async syncBodyElementsFromContent(slideId: string): Promise<boolean> {
      const slide = this.slides.find((s) => s.id === slideId)
      if (!slide) return false
      const next = bodyElementsFromContent(slide.content || '', {
        keep: slide.elements ?? [],
        preferSteps: slide.type === 'derivation' || slide.type === 'worked-example',
      })
      return this.updateElements(slideId, next)
    },

    /** AI 快捷操作：让本页更简洁（真实 regen，只改本页 content）。 */
    async aiSimplifyPage(slideId: string, instruction = '让本页更简洁：精简文字，突出重点，保留数学公式与关键步骤'): Promise<boolean> {
      return this.runAiTask(
        'simplify',
        () =>
          regenDeckPage(this.deckId as string, {
            expected_revision: this.revision,
            target: { kind: 'slide', slide_id: slideId },
            instruction,
            refs: this.buildRefs(),
            mode: 'content',
          }),
        { bodySyncSlideId: slideId },
      )
    },

    /**
     * AI 快捷操作：补全本页推导步骤（真实 regen，只写本页正文/元素）。
     *
     * 注意：这里**不承诺动画**。旧实现把指令硬编码为“推导椭圆”，导致数列课件被
     * 写成椭圆内容；现在只给通用指令，课件标题/本页主题由后端注入并做主题护栏。
     */
    async aiCompleteDerivation(slideId: string): Promise<boolean> {
      return this.runAiTask(
        'derive',
        () =>
          regenDeckPage(this.deckId as string, {
            expected_revision: this.revision,
            target: { kind: 'slide', slide_id: slideId },
            instruction:
              '基于本页标题、正文与所属教学阶段，补全分步推导（每步一行，含关键公式）；只使用本页所属数学主题，不得引入其他章节内容',
            refs: this.buildRefs(),
            mode: 'content',
          }),
        { bodySyncSlideId: slideId },
      )
    },

    /**
     * AI 快捷操作：生成推导动画（真实两段式）。
     *
     * 1) 后端 mode=derivation 产出与本课主题一致的编号推导链（主题护栏拒绝串题）；
     * 2) 前端把编号链投影成 steps，打开全屏预览并逐步揭示——不是“换个文案的假动画”。
     *    导出 PPTX 仍为静态全显示（能力边界，UI 明确标注）。
     */
    async aiDeriveAnimation(slideId: string): Promise<boolean> {
      const target = this.slides.find((s) => s.id === slideId)
      if (!target) return false
      if (!['derivation', 'worked-example'].includes(String(target.type || ''))) {
        this.aiError = `「生成推导动画」只适用于推导页或例题页；当前页是「${target.title || '本页'}」。`
        return false
      }
      const ok = await this.runAiTask(
        'derive',
        () =>
          regenDeckPage(this.deckId as string, {
            expected_revision: this.revision,
            target: { kind: 'slide', slide_id: slideId },
            instruction:
              '生成可直接投影的分步推导：每步一行、按 1. 2. 3. 编号，含关键公式；只使用本课主题',
            refs: this.buildRefs(),
            mode: 'derivation',
          }),
        { bodySyncSlideId: slideId },
      )
      if (!ok) return false
      const idx = this.slides.findIndex((s) => s.id === slideId)
      const generated = idx >= 0 ? this.slides[idx] : null
      const hasSteps = (generated?.elements || []).some(
        (e) => e.role === 'steps' && (e.items || []).some((x) => String(x || '').trim()),
      )
      if (!hasSteps) {
        this.aiError = 'AI 已更新本页，但没有形成可播放的分步结构；请重试，或手动把本页改成推导步骤。'
        return false
      }
      if (idx >= 0) this.currentIndex = idx
      this.previewDemo = true
      this.previewOpen = true
      return true
    },

    /** AI 快捷操作：为本课补齐确定性数学配图（最多 3 张，真实 MATH_RENDER 资产）。 */
    async addDeckFigures(): Promise<number> {
      if (!this.deckId || this.figuresBusy) return 0
      this.figuresBusy = true
      this.figuresError = null
      try {
        const r = await ensureDeckFigures(this.deckId, {
          expected_revision: this.revision,
          refs: this.buildRefs(),
          max_figures: 3,
        })
        await this.refreshFromServer()
        if (r?.revision) this.revision = r.revision
        return Number(r?.figures_added || 0)
      } catch (e) {
        this.figuresError = errText(e)
        return 0
      } finally {
        this.figuresBusy = false
      }
    },
    async aiPracticeForPage(slideId: string): Promise<boolean> {
      return this.runAiTask('practice', () =>
        regenDeckPage(this.deckId as string, {
          expected_revision: this.revision,
          target: { kind: 'slide', slide_id: slideId },
          instruction: '基于本页知识点生成随堂练习',
          refs: this.buildRefs(),
          mode: 'note_practice',
        }),
      )
    },

    /** 底部 AI 条：单页定向修改（target=当前页，断言只改该页）。 */
    async aiModifyCurrentPage(message: string): Promise<boolean> {
      const slide = this.currentSlide
      if (!slide) return false
      return this.runAiTask(
        'modify_page',
        () =>
          regenDeckPage(this.deckId as string, {
            expected_revision: this.revision,
            target: { kind: 'slide', slide_id: slide.id },
            instruction: message,
            refs: this.buildRefs(),
            mode: 'content',
          }),
        { bodySyncSlideId: slide.id },
      )
    },

    /**
     * 单页重试按钮（/regen）：按本页教学目标重新生成本页内容（教师显式触发，AI 不静默覆盖）。
     * 粒度=当前页；成功后正文→版式确定性同步（G14），失败诚实报错且保留原页。
     */
    async aiRegeneratePage(slideId: string): Promise<boolean> {
      return this.runAiTask(
        'modify_page',
        () =>
          regenDeckPage(this.deckId as string, {
            expected_revision: this.revision,
            target: { kind: 'slide', slide_id: slideId },
            instruction: '按本页教学目标重新生成本页内容（重写正文，保留图形与关键公式）',
            refs: this.buildRefs(),
            mode: 'content',
          }),
        { bodySyncSlideId: slideId },
      )
    },

    /** AI 添加页：基于当前页教学环节生成真实内容（禁止空白假页）。 */
    async aiAddPageAfterCurrent(): Promise<boolean> {
      const slide = this.currentSlide
      if (!slide) return false
      const ok = await this.runAiTask('add_page', () =>
        addDeckPage(this.deckId as string, {
          expected_revision: this.revision,
          after_slide_id: slide.id,
          instruction: '围绕当前页的教学任务补充一页内容',
          refs: this.buildRefs(),
        }),
      )
      if (ok) this.currentIndex += 1
      return ok
    },

    /* ── 逐页增稠（骨架 → 板书内容；真实异步作业 + 真实进度）───────────
     * 链路：POST /decks/{id}/enrich {expected_revision} → 轮询 GET /exports/{job_id}
     * （与导出作业同通道）→ done 后拉权威快照（新 revision）→ 画布切回第 1 页。
     * 进度只呈现服务端真实阶段 + 本地真实计时/真实页数；**无假进度条**。 */
    async startEnrich(): Promise<boolean> {
      if (!this.deckId || this.enrichBusy || this.patchBusy || this.aiBusy) return false
      this.enrichBusy = true
      this.enrichError = null
      this.enrichCounts = null
      this.enrichProgress = null
      this.enrichFailedPages = []
      this.enrichStartedAt = Date.now()
      this.enrichStage = '正在提交增稠任务…'
      try {
        const start = await enrichDeck(this.deckId, this.revision)
        this.enrichJobId = start.job_id
        const deadline = Date.now() + 300_000
        for (;;) {
          const job = await getExportJob(start.job_id)
          this.enrichStage = enrichStageText(job)
          const counts = readEnrichCounts(job)
          if (counts) this.enrichCounts = counts
          // G13：实时 n/N 只读 progress durable 事件（服务端每页真实完成即发一条）
          const progress = readEnrichProgress(job)
          if (progress) this.enrichProgress = progress
          if (job.status === 'done') {
            // G14：真实逐页失败明细（已完成页保留，只对失败页提供单页重试）
            this.enrichFailedPages = readPageFailures(job, this.slides)
            break
          }
          if (job.status === 'failed' || job.status === 'cancelled') {
            this.enrichFailedPages = readPageFailures(job, this.slides)
            throw new PrepCenterApiError({
              kind: 'error',
              status: null,
              code: null,
              detail: job.error || (job.status === 'cancelled' ? '增稠已取消' : '课件增稠失败，请重试'),
            })
          }
          if (Date.now() >= deadline) {
            throw new PrepCenterApiError({ kind: 'error', status: null, code: null, detail: '增稠耗时过长，已停止等待；可稍后刷新查看结果' })
          }
          await sleep(1500)
        }
        await this.refreshFromServer()
        this.saveState = 'saved'
        this.currentIndex = 0
        return true
      } catch (e) {
        this.enrichError = enrichErrorText(e)
        return false
      } finally {
        this.enrichBusy = false
        this.enrichJobId = null
        this.enrichStage = ''
      }
    },

    clearEnrichError() {
      this.enrichError = null
    },

    /** 关闭增稠结果/错误提示条。 */
    clearEnrichNotice() {
      this.enrichError = null
      this.enrichCounts = null
    },

    /**
     * G14 单页重试：只重试失败的那一页（ppt_v2 /regen），已完成页原样保留。
     * 成功后从失败清单移除该页并刷新权威快照；失败则保留清单继续可重试。
     */
    async retryFailedPage(slideId: string, instruction: string): Promise<boolean> {
      if (!this.deckId || this.aiBusy || this.patchBusy) return false
      this.aiBusy = true
      this.aiTask = 'modify_page'
      this.aiStageLabel = '正在重试该页…'
      this.aiError = null
      try {
        const proposal = await regenDeckPage(this.deckId as string, {
          expected_revision: this.revision,
          target: { kind: 'slide', slide_id: slideId },
          instruction,
          refs: this.buildRefs(),
          mode: 'content',
        })
        await applyDeckPatch(this.deckId, proposal.patch_id, this.revision)
        this.enrichFailedPages = this.enrichFailedPages.filter((f) => f.slideId !== slideId)
        await this.refreshFromServer()
        // G14：content 重写 → 版式元素同步（与 runAiTask 同口径）
        const synced = await this.syncBodyElementsFromContent(slideId)
        if (!synced) {
          this.aiError = '正文已更新，但版式同步失败，请刷新页面查看最新内容'
          return false
        }
        this.saveState = 'saved'
        return true
      } catch (e) {
        this.aiError = errText(e)
        return false
      } finally {
        this.aiBusy = false
        this.aiTask = null
        this.aiStageLabel = ''
      }
    },

    /* ── 本地视图状态 ─────────────────────────────────────────────── */
    selectPage(index: number) {
      if (index >= 0 && index < this.pageCount) {
        this.currentIndex = index
        this.mathSelected = false
      }
    },
    selectMath(selected: boolean) {
      this.mathSelected = selected
      if (!selected) this.draftMath = null
    },
    setDraftMath(m: MathObject | null) {
      this.draftMath = m
    },
    openPreview(demo = false) {
      this.previewDemo = demo
      this.previewOpen = true
    },
    closePreview() {
      this.previewOpen = false
      this.previewDemo = false
    },
    clearAiError() {
      this.aiError = null
    },
    reset() {
      this.deck = null
      this.deckId = null
      this.revision = 0
      this.savedAt = null
      this.saveState = 'idle'
      this.saveError = null
      this.currentIndex = 0
      this.mathSelected = false
      this.previewOpen = false
      this.previewDemo = false
      this.figuresBusy = false
      this.figuresError = null
      this.aiError = null
      this.aiBusy = false
      this.aiTask = null
      this.aiStageLabel = ''
      this.draftMath = null
      this.patchQueue = []
      this.flushing = false
      this.enrichBusy = false
      this.enrichJobId = null
      this.enrichStage = ''
      this.enrichStartedAt = null
      this.enrichError = null
      this.enrichCounts = null
      this.enrichProgress = null
      this.enrichFailedPages = []
    },
  },
})
