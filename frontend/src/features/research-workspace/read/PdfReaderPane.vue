<template>
  <section ref="paneEl" class="rw-pdf" :class="{ 'rw-pdf--hand': tool === 'hand' }">
    <DocTabs
      :papers="visibleTabs"
      :active-id="activePaperId"
      @select="emit('select-paper', $event)"
      @close="emit('close-tab', $event)"
      @add="emit('add-paper')"
    />

    <PdfToolbar
      :page="pageNum"
      :page-count="numPages"
      :zoom-percent="zoomPercent"
      :has-doc="hasDoc"
      :tool="tool"
      :search-open="searchOpen"
      :search-query="searchQuery"
      :search-total="matches.length"
      :search-index="matchIndex"
      @prev="prevPage"
      @next="nextPage"
      @goto="goPage"
      @zoom-in="zoomIn"
      @zoom-out="zoomOut"
      @fit-width="fitWidth"
      @fit-page="fitPage"
      @set-tool="tool = $event"
      @copy="copySelection"
      @toggle-search="toggleSearch"
      @update:search-query="onQuery"
      @search-prev="prevMatch"
      @search-next="nextMatch"
      @fullscreen="toggleFullscreen"
    />

    <div ref="scrollEl" class="rw-pdf__scroll">
      <!-- 失败态：诚实人话 + 重试（宪法 §60/§61） -->
      <div v-if="loadError" class="rw-pdf__center">
        <EmptyState title="文献暂时无法显示" :description="loadError" action-text="重新加载" @action="reload" />
      </div>
      <!-- 无文献：下一步引导 -->
      <div v-else-if="!activePaperId" class="rw-pdf__center">
        <EmptyState
          title="选择一篇文献开始阅读"
          description="左侧文献列表中的 PDF 将在此渲染，支持翻页、缩放、选文与全文搜索。"
          :icon="DocumentTextOutline"
        />
      </div>
      <!-- 有文献但无资产（解析失败仍可读 PDF；无资产则明确说明） -->
      <div v-else-if="activePaper && !activePaper.asset_id" class="rw-pdf__center">
        <EmptyState
          title="该文献暂无 PDF 文件"
          description="元数据已保存，但未关联可渲染的 PDF 资产。请重新导入该文献。"
          action-text="重新导入"
          @action="emit('add-paper')"
        />
      </div>

      <div v-else class="rw-pdf__stage">
        <div class="rw-pdf__page" :style="pageStyle">
          <canvas ref="canvasEl" class="rw-pdf__canvas" />
          <!-- 文本层：承载选文与坐标（pdfjs TextLayer） -->
          <div ref="textLayerEl" class="textLayer rw-pdf__text" @mouseup="onMouseUp" />
          <!-- 叠层：定位高亮 / 用户高亮 / 搜索命中（均为真实坐标换算） -->
          <div class="rw-pdf__marks" aria-hidden="true">
            <span
              v-for="m in overlays"
              :key="m.key"
              class="rw-pdf__mark"
              :class="`rw-pdf__mark--${m.kind}`"
              :style="m.style"
            />
          </div>
          <div v-if="rendering" class="rw-pdf__skeleton">
            <RegionSkeleton :rows="6" label="页面渲染中" />
          </div>
        </div>
      </div>
    </div>

    <SelectionToolbar
      v-if="sel.visible"
      :left="sel.left"
      :top="sel.top"
      @judge="doAsk"
      @explain="doExplain"
      @evidence="doEvidence"
      @note="doNote"
      @cite="doCite"
      @highlight="doHighlight"
    />

    <p v-if="toast" class="rw-pdf__toast" role="status">{{ toast }}</p>
  </section>
</template>

<script setup lang="ts">
/**
 * 中栏 PDF 阅读器（VISUAL-SPEC §4.2 / FRONTEND-ARCH §6），宽 737px。
 *
 * 真实能力：真实 PDF 渲染（pdfjs `fetch→Uint8Array→getDocument({data})`）、
 * 翻页 / 缩放 / 适宽 / 适页 / 页码跳转 / 全文搜索 / 选文 / 复制 / 全屏 / 定位原文。
 * 选区 → locator 采用 `PageViewport.convertToPdfPoint` 归一化到**与后端 `/text` 同一
 * PDF 坐标系**（DPR 只影响 canvas 位图，不影响 CSS 尺寸与坐标换算），并用该 y 反查最近
 * 章节标题得到 `section`。
 *
 * 分区域 loading：页面渲染用 `RegionSkeleton`，**禁止整页 spinner**。
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { DocumentTextOutline } from '@vicons/ionicons5'
import DocTabs from './DocTabs.vue'
import PdfToolbar from './PdfToolbar.vue'
import SelectionToolbar from './SelectionToolbar.vue'
import EmptyState from '../ui/EmptyState.vue'
import RegionSkeleton from '../ui/RegionSkeleton.vue'
import { useRwPapersStore } from '../stores/papers'
import { useRwNotesStore } from '../stores/notes'
import { RESEARCH_API_BASE } from '../api/client'
import {
  fetchPdfBytes,
  isRenderCancelled,
  loadPdfDocument,
  pagePlainText,
  PdfRenderController,
  pdfBboxToLocalRect,
  rectToPdfBbox,
  sectionAt,
  type PDFDocumentProxy,
  type PageViewport,
} from './usePdfDocument'
import type { Locator, PageBlock, Paper } from '../api/types'

const props = defineProps<{
  projectId: string
  papers: Paper[]
  activePaperId: string | null
}>()

const emit = defineEmits<{
  (e: 'select-paper', id: string): void
  (e: 'close-tab', id: string): void
  (e: 'add-paper'): void
  (e: 'ask-ai', locator: Locator): void
  (e: 'explain-ai', locator: Locator): void
  (e: 'evidence-ai', locator: Locator): void
  /**
   * 选区变化 → 通知上层把选区**自动装载**进 Research Context（宪法 §26/§46）。
   * 传 `null` 表示当前选区已失效（切换文献 / 换页）。
   */
  (e: 'selection-change', locator: Locator | null): void
}>()

const papersStore = useRwPapersStore()
const notesStore = useRwNotesStore()

const paneEl = ref<HTMLElement | null>(null)
const scrollEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const textLayerEl = ref<HTMLElement | null>(null)

/**
 * 单 canvas 渲染控制器：串行化同一 canvas 上的 render()，取消过期渲染并以代际
 * 令牌作废过期结果 —— 杜绝 pdf.js「Cannot use the same canvas during multiple
 * render() operations」竞态（用户实测 P0）。全生命周期复用一个实例。
 */
const renderer = new PdfRenderController()

/** pdfjs 文档含 #private 字段 → 必须 shallowRef（严禁深层响应式代理）。 */
const doc = shallowRef<PDFDocumentProxy | null>(null)
const viewport = shallowRef<PageViewport | null>(null)

const numPages = ref(0)
const pageNum = ref(1)
const scale = ref(1)
const naturalW = ref(612)
const naturalH = ref(792)
const fitMode = ref<'free' | 'width' | 'page'>('width')
const rendering = ref(false)
const loadError = ref('')
const tool = ref<'select' | 'hand'>('select')

const blocks = ref<PageBlock[]>([])
const highlightBoxes = ref<[number, number, number, number][]>([])
const locateBox = ref<[number, number, number, number] | null>(null)
const pendingLocator = ref<Locator | null>(null)

interface Overlay {
  key: string
  kind: 'user' | 'locate' | 'search'
  style: Record<string, string>
}
const overlays = ref<Overlay[]>([])

const searchOpen = ref(false)
const searchQuery = ref('')
const matches = ref<{ page: number }[]>([])
const matchIndex = ref(0)
let searchTimer: ReturnType<typeof setTimeout> | null = null

const sel = ref<{ visible: boolean; text: string; bbox: [number, number, number, number]; section?: string; left: number; top: number }>(
  { visible: false, text: '', bbox: [0, 0, 0, 0], left: 0, top: 0 },
)
const toast = ref('')
let toastTimer: ReturnType<typeof setTimeout> | null = null

/** 文档加载代际令牌：快速切换文献时，过期加载不得写入 doc / 状态。 */
let loadToken = 0

const hasDoc = computed(() => Boolean(doc.value))
const zoomPercent = computed(() => Math.round(scale.value * 100))
const activePaper = computed(() => props.papers.find((p) => p.id === props.activePaperId) ?? null)
/** Tab 条只显示未被「×」关闭的文献（store 内 `closedIds` 会话态，§70：× 必须真实生效）。 */
const visibleTabs = computed(() => props.papers.filter((p) => !papersStore.closedIds.includes(p.id)))
const pageStyle = computed<Record<string, string>>(() => {
  const vp = viewport.value
  if (!vp) return { width: '1px', height: '1px' }
  return { width: `${Math.floor(vp.width)}px`, height: `${Math.floor(vp.height)}px` }
})

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}
function clampScale(v: number): number {
  return clamp(Number.isFinite(v) ? v : 1, 0.3, 5)
}

function showToast(msg: string) {
  toast.value = msg
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 2200)
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : '文献加载失败，请稍后重试。'
}

/* ───────────────────────── 加载与渲染 ───────────────────────── */

function assetUrl(assetId: string): string {
  return `${RESEARCH_API_BASE}/assets/${encodeURIComponent(assetId)}/content`
}

async function destroyDoc() {
  // 先取消进行中的渲染，避免旧文档的渲染回写已销毁 / 已换页的 canvas。
  renderer.cancel()
  const d = doc.value
  doc.value = null
  viewport.value = null
  if (d) {
    try {
      await d.destroy()
    } catch {
      /* 忽略销毁异常 */
    }
  }
}

async function loadActive() {
  const myToken = ++loadToken
  sel.value.visible = false
  // 换文献/重载后旧选区失效 —— 同步清掉 Agent 上下文里的选区（§26 上下文必须与现场一致）
  emit('selection-change', null)
  loadError.value = ''
  overlays.value = []
  locateBox.value = null
  searchOpen.value = false
  searchQuery.value = ''
  matches.value = []
  await destroyDoc()
  if (myToken !== loadToken) return
  const paper = activePaper.value
  if (!paper || !paper.asset_id) {
    numPages.value = 0
    return
  }
  // 恢复该文献的用户高亮（localStorage 持久化，刷新后仍在；§70/§84）
  highlightBoxes.value = loadHighlights(paper.id)
  rendering.value = true
  try {
    const bytes = await fetchPdfBytes(assetUrl(paper.asset_id))
    if (myToken !== loadToken) return
    const loaded = await loadPdfDocument(bytes)
    // 过期加载：新文献已被接管 → 丢弃刚加载的文档，不写入状态。
    if (myToken !== loadToken) {
      try {
        await loaded.destroy()
      } catch {
        /* 忽略销毁异常 */
      }
      return
    }
    doc.value = loaded
    numPages.value = loaded.numPages
    const lastPage = (paper as Paper & { last_page?: number | null }).last_page ?? 1
    pageNum.value = clamp(lastPage, 1, numPages.value || 1)
    await applyFit()
    if (myToken !== loadToken) return
    await render()
    if (myToken !== loadToken) return
    if (pendingLocator.value) {
      const loc = pendingLocator.value
      pendingLocator.value = null
      await applyLocator(loc)
    }
  } catch (e) {
    if (myToken !== loadToken) return
    loadError.value = errText(e)
    numPages.value = 0
  } finally {
    if (myToken === loadToken) rendering.value = false
  }
}

/**
 * 渲染当前页（**可重入安全**）。
 *
 * 并发调用时：**后者接管、前者作废** —— 每次进入先 `renderer.begin()`（同步取消
 * 上一个 RenderTask 并自增代际号），随后每一步 await 之后都用 `isValid(gen)` 校验；
 * 过期代际一律 return，**不写 canvas、不改 UI 状态**（不闪屏、不丢滚动位置）。
 * 取消异常（`RenderingCancelledException`）被吞掉，绝不变成「文献暂时无法显示」。
 *
 * 该方法被翻页 / 缩放 / 适宽 / 适页 / 定位 / resize 等多处调用，因此竞态防护
 * 必须在**此处**统一完成，而不是逐个调用点打补丁。
 */
async function render(): Promise<void> {
  const d = doc.value
  const c = canvasEl.value
  if (!d || !c) return
  // 抢占：作废任何进行中的渲染，拿到本次的代际号。
  const gen = renderer.begin()
  rendering.value = true
  try {
    const pg = await d.getPage(clamp(pageNum.value, 1, numPages.value || 1))
    if (!renderer.isValid(gen)) return
    naturalW.value = pg.getViewport({ scale: 1 }).width
    naturalH.value = pg.getViewport({ scale: 1 }).height
    const dpr = window.devicePixelRatio || 1
    const r = await renderer.renderCanvas(pg, c, scale.value, dpr, gen)
    // 取消 / 过期 → 不写 viewport、不建文本层、不刷叠层。
    if (!r || !renderer.isValid(gen)) return
    viewport.value = r.viewport
    const layer = textLayerEl.value
    if (layer) {
      const ok = await renderer.renderText(pg, layer, r.viewport, scale.value, gen)
      if (!ok || !renderer.isValid(gen)) return
    }
    await loadBlocks()
    if (!renderer.isValid(gen)) return
    refreshOverlays()
  } catch (e) {
    // 取消是正常控制流；过期渲染的错误同样不得污染 UI。
    if (!renderer.isValid(gen) || isRenderCancelled(e)) return
    loadError.value = errText(e)
  } finally {
    // 仅最新代际负责收尾 skeleton；过期代际不得改动（避免误关后发渲染的 loading）。
    if (renderer.isValid(gen)) rendering.value = false
  }
}

async function loadBlocks() {
  const { projectId, activePaperId } = props
  if (!projectId || !activePaperId) {
    blocks.value = []
    return
  }
  const text = await papersStore.loadText(projectId, activePaperId, pageNum.value)
  blocks.value = text?.blocks ?? []
}

function reload() {
  void loadActive()
}

/* ───────────────────────── 缩放 / 适配 ───────────────────────── */

async function applyFit() {
  const s = scrollEl.value
  if (!s || !naturalW.value) return
  if (fitMode.value === 'width') {
    scale.value = clampScale((s.clientWidth - 2) / naturalW.value)
  } else if (fitMode.value === 'page') {
    scale.value = clampScale(
      Math.min((s.clientWidth - 2) / naturalW.value, (s.clientHeight - 2) / naturalH.value),
    )
  }
}

async function fitWidth() {
  fitMode.value = 'width'
  await applyFit()
  await render()
}

async function fitPage() {
  fitMode.value = 'page'
  await applyFit()
  await render()
}

async function zoomIn() {
  fitMode.value = 'free'
  scale.value = clampScale(scale.value * 1.2)
  await render()
}

async function zoomOut() {
  fitMode.value = 'free'
  scale.value = clampScale(scale.value / 1.2)
  await render()
}

async function goPage(n: number) {
  if (!doc.value) return
  const target = clamp(Math.round(n) || 1, 1, numPages.value || 1)
  if (target !== pageNum.value) {
    pageNum.value = target
    await render()
    void persistReadState()
  }
}

function prevPage() {
  void goPage(pageNum.value - 1)
}
function nextPage() {
  void goPage(pageNum.value + 1)
}

async function persistReadState() {
  const { projectId, activePaperId } = props
  if (!projectId || !activePaperId) return
  await papersStore.setReadState(projectId, activePaperId, 'reading', pageNum.value)
}

/* ───────────────────────── 搜索（真实计数） ───────────────────────── */

function toggleSearch() {
  searchOpen.value = !searchOpen.value
  if (!searchOpen.value) {
    searchQuery.value = ''
    matches.value = []
    matchIndex.value = 0
    refreshOverlays()
  }
}

function onQuery(q: string) {
  searchQuery.value = q
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => void runSearch(q), 260)
}

async function runSearch(q: string) {
  const needle = q.trim().toLowerCase()
  const d = doc.value
  if (!d || !needle) {
    matches.value = []
    matchIndex.value = 0
    refreshOverlays()
    return
  }
  const found: { page: number }[] = []
  for (let p = 1; p <= numPages.value; p += 1) {
    const pg = await d.getPage(p)
    const txt = (await pagePlainText(pg)).toLowerCase()
    let i = txt.indexOf(needle)
    while (i !== -1) {
      found.push({ page: p })
      i = txt.indexOf(needle, i + needle.length)
    }
  }
  matches.value = found
  matchIndex.value = 0
  if (found.length) await goPage(found[0].page)
  else refreshOverlays()
}

function prevMatch() {
  if (!matches.value.length) return
  matchIndex.value = (matchIndex.value - 1 + matches.value.length) % matches.value.length
  void goPage(matches.value[matchIndex.value].page)
}
function nextMatch() {
  if (!matches.value.length) return
  matchIndex.value = (matchIndex.value + 1) % matches.value.length
  void goPage(matches.value[matchIndex.value].page)
}

/* ───────────────────────── 叠层 ───────────────────────── */

function refreshOverlays() {
  const vp = viewport.value
  const list: Overlay[] = []
  if (vp) {
    highlightBoxes.value.forEach((b, i) => {
      const r = pdfBboxToLocalRect(b, vp)
      list.push({
        key: `h${i}`,
        kind: 'user',
        style: { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` },
      })
    })
    if (locateBox.value) {
      const r = pdfBboxToLocalRect(locateBox.value, vp)
      list.push({
        key: 'loc',
        kind: 'locate',
        style: { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` },
      })
    }
    const layer = textLayerEl.value
    if (searchQuery.value.trim() && layer) {
      const needle = searchQuery.value.trim().toLowerCase()
      const spans = Array.from(layer.querySelectorAll('span')) as HTMLElement[]
      let idx = 0
      for (const sp of spans) {
        const t = (sp.textContent ?? '').toLowerCase()
        if (t.includes(needle)) {
          list.push({
            key: `s${idx}`,
            kind: 'search',
            style: {
              left: `${sp.offsetLeft}px`,
              top: `${sp.offsetTop}px`,
              width: `${sp.offsetWidth}px`,
              height: `${sp.offsetHeight}px`,
            },
          })
          idx += 1
        }
      }
    }
  }
  overlays.value = list
}

function scrollToBbox(bbox: [number, number, number, number]) {
  const vp = viewport.value
  const s = scrollEl.value
  if (!vp || !s) return
  const r = pdfBboxToLocalRect(bbox, vp)
  const target = r.top + r.height / 2 - s.clientHeight / 2
  s.scrollTo({ top: Math.max(0, target), behavior: 'smooth' })
}

/* ───────────────────────── 选文 → locator ───────────────────────── */

function hideSel() {
  sel.value = { ...sel.value, visible: false }
}

function onMouseUp() {
  const s = window.getSelection()
  if (!s || s.isCollapsed || s.rangeCount === 0) {
    hideSel()
    return
  }
  const text = s.toString().replace(/\s+/g, ' ').trim()
  if (!text) {
    hideSel()
    return
  }
  const c = canvasEl.value
  const pane = paneEl.value
  const vp = viewport.value
  if (!c || !pane || !vp) return
  const range = s.getRangeAt(0)
  const rect = range.getBoundingClientRect()
  const cr = c.getBoundingClientRect()
  if (rect.bottom < cr.top || rect.top > cr.bottom || rect.right < cr.left || rect.left > cr.right) {
    hideSel()
    return
  }
  const bbox = rectToPdfBbox(rect, cr, vp)
  const section = sectionAt((bbox[1] + bbox[3]) / 2, blocks.value)
  const pr = pane.getBoundingClientRect()
  const left = clamp(rect.left + rect.width / 2 - pr.left, 120, Math.max(120, pane.clientWidth - 120))
  const top = Math.max(8, rect.top - pr.top - 42)
  sel.value = { visible: true, text: text.slice(0, 500), bbox, section, left, top }
  // 宪法 §26/§46：选区必须**自动装载**进 Research Context —— 这些不是用户手选的 Filter。
  // 有了这一步，用户「在 PDF 里选中一段数学陈述 → 直接点 Agent 的『推导校验』」就能拿到真实结论，
  // 无需先点浮动工具条的「问 AI」（那一步只是把同一份 locator 立刻送去执行）。
  if (props.activePaperId) {
    emit('selection-change', {
      paper_id: props.activePaperId,
      page: pageNum.value,
      bbox,
      selected_text: sel.value.text,
      section,
    })
  }
}

/** 由当前选区构造 locator（证据 / 笔记 / Agent 共用，宪法 §14）。 */
function currentLocator(): Locator | null {
  if (!props.activePaperId || !sel.value.visible) return null
  return {
    paper_id: props.activePaperId,
    page: pageNum.value,
    bbox: sel.value.bbox,
    selected_text: sel.value.text,
    section: sel.value.section,
  }
}

/* ───────────────────────── 用户高亮（localStorage 持久化，§70/§84） ───────────────────────── */

/** 高亮存 localStorage（key 按 paper 隔离）：刷新/重进后仍在 —— 不做内存摆设。 */
function highlightKey(paperId: string): string {
  return `rw.highlights.v1.${paperId}`
}

function loadHighlights(paperId: string): [number, number, number, number][] {
  try {
    const raw = localStorage.getItem(highlightKey(paperId))
    const arr = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(arr) ? (arr as [number, number, number, number][]) : []
  } catch {
    return []
  }
}

function saveHighlights(paperId: string, boxes: [number, number, number, number][]) {
  try {
    localStorage.setItem(highlightKey(paperId), JSON.stringify(boxes))
  } catch {
    /* 存储失败时高亮仅在当前会话内可见，不打断阅读 */
  }
}

function doHighlight() {
  if (sel.value.visible && props.activePaperId) {
    highlightBoxes.value = [...highlightBoxes.value, sel.value.bbox]
    saveHighlights(props.activePaperId, highlightBoxes.value)
    refreshOverlays()
  }
  hideSel()
}

async function doNote() {
  const loc = currentLocator()
  if (!loc || !props.projectId) return
  const created = await notesStore.create(props.projectId, {
    title: loc.selected_text.slice(0, 40) || '阅读笔记',
    body: loc.selected_text,
    paper_id: loc.paper_id,
    page: loc.page,
    section: loc.section ?? null,
    selected_text: loc.selected_text,
    xref_kind: 'paper',
  })
  showToast(created ? '已添加笔记' : notesStore.error?.message || '笔记保存失败，请重试。')
  hideSel()
}

async function doCite() {
  const loc = currentLocator()
  const paper = activePaper.value
  if (!loc) return
  const cite = `${paper?.authors?.[0] ?? paper?.title ?? '文献'} (${paper?.year ?? 'n.d.'}) · p.${loc.page}：「${loc.selected_text}」`
  try {
    await navigator.clipboard.writeText(cite)
    showToast('引用已复制到剪贴板')
  } catch {
    showToast('复制失败，请手动选择文字复制。')
  }
  hideSel()
}

function doAsk() {
  const loc = currentLocator()
  if (loc) emit('ask-ai', loc)
  hideSel()
}

function doExplain() {
  const loc = currentLocator()
  if (loc) emit('explain-ai', loc)
  hideSel()
}

function doEvidence() {
  const loc = currentLocator()
  if (loc) emit('evidence-ai', loc)
  hideSel()
}

async function copySelection() {
  const s = window.getSelection()
  const text = s && !s.isCollapsed ? s.toString() : sel.value.text
  if (!text) {
    showToast('未选中任何文字')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    showToast('已复制选中文字')
  } catch {
    showToast('复制失败，请手动选择文字复制。')
  }
}

/* ───────────────────────── 定位原文（对外） ───────────────────────── */

async function applyLocator(loc: Locator) {
  const target = loc.page && loc.page > 0 ? loc.page : 1
  if (target !== pageNum.value) await goPage(target)
  else await render()
  let box: [number, number, number, number] | null = null
  const b = loc.bbox
  if (b && b.length === 4 && b.some((n) => Number.isFinite(n) && n !== 0)) box = b
  if (!box && loc.selected_text) {
    const needle = loc.selected_text.slice(0, 20)
    const blk = blocks.value.find((x) => x.text.includes(needle) || needle.includes(x.text.slice(0, 10)))
    if (blk) box = blk.bbox
  }
  if (box) {
    locateBox.value = box
    refreshOverlays()
    scrollToBbox(box)
    const boxRef = box
    window.setTimeout(() => {
      if (locateBox.value === boxRef) {
        locateBox.value = null
        refreshOverlays()
      }
    }, 1900)
  }
}

/**
 * 切到 locator 指定 paper 并渲染该 page，滚动至 bbox 并淡出高亮。
 * 最近笔记 / 证据「定位原文」均走此入口。
 */
async function locate(loc: Locator | null) {
  if (!loc || !loc.paper_id) return
  if (loc.paper_id !== props.activePaperId) {
    pendingLocator.value = loc
    emit('select-paper', loc.paper_id)
    return
  }
  await applyLocator(loc)
}

defineExpose({ locate })

/* ───────────────────────── 全屏 / 生命周期 ───────────────────────── */

function toggleFullscreen() {
  const el = paneEl.value
  if (!el) return
  if (document.fullscreenElement) void document.exitFullscreen()
  else void el.requestFullscreen?.()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    hideSel()
    return
  }
  if (!hasDoc.value) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
  if (e.key === 'ArrowDown' || e.key === 'PageDown') {
    nextPage()
  } else if (e.key === 'ArrowUp' || e.key === 'PageUp') {
    prevPage()
  }
}

let resizeObs: ResizeObserver | null = null

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  const s = scrollEl.value
  if (s && typeof ResizeObserver !== 'undefined') {
    resizeObs = new ResizeObserver(() => {
      if (fitMode.value !== 'free' && hasDoc.value) void fitCurrent()
    })
    resizeObs.observe(s)
  }
})

async function fitCurrent() {
  await applyFit()
  await render()
}

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  resizeObs?.disconnect()
  if (searchTimer) clearTimeout(searchTimer)
  if (toastTimer) clearTimeout(toastTimer)
  // 先作废所有进行中的渲染（含文本层），再销毁文档，避免卸载后回写已脱离的 DOM。
  renderer.dispose()
  void destroyDoc()
})

watch(
  () => [props.activePaperId, props.projectId],
  () => void loadActive(),
  { immediate: true },
)
</script>

<style>
/* 文本层样式（pdfjs TextLayer 依赖；作用域限定在 .rw-pdf__text 之下） */
.rw-pdf__text {
  position: absolute;
  inset: 0;
  overflow: clip;
  line-height: 1;
  opacity: 1;
  transform-origin: 0 0;
  text-align: initial;
  -webkit-text-size-adjust: none;
  text-size-adjust: none;
  forced-color-adjust: none;
  caret-color: var(--rw-text);
  z-index: 2;
}
.rw-pdf__text :is(span, br) {
  color: transparent;
  position: absolute;
  white-space: pre;
  cursor: text;
  transform-origin: 0% 0%;
}
.rw-pdf__text ::selection {
  background: color-mix(in srgb, var(--rw-primary) 30%, transparent);
}
</style>

<style scoped>
.rw-pdf {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--rw-page-bg);
}
.rw-pdf__scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  display: flex;
  justify-content: center;
}
.rw-pdf__center {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1 1 auto;
  width: 100%;
}
.rw-pdf__stage {
  padding: 0;
  margin: 0 auto;
}
.rw-pdf__page {
  position: relative;
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-card);
}
.rw-pdf__canvas {
  display: block;
}
.rw-pdf__marks {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 3;
}
.rw-pdf__mark {
  position: absolute;
  border-radius: 2px;
}
.rw-pdf__mark--search {
  background: color-mix(in srgb, var(--rw-warning) 32%, transparent);
}
.rw-pdf__mark--user {
  background: color-mix(in srgb, var(--rw-primary) 26%, transparent);
  border-bottom: 1px solid var(--rw-primary);
}
.rw-pdf__mark--locate {
  background: color-mix(in srgb, var(--rw-primary) 34%, transparent);
  box-shadow: 0 0 0 1px var(--rw-primary);
  animation: rw-locate-fade 1.9s ease forwards;
}
@keyframes rw-locate-fade {
  0% { opacity: 0; }
  12% { opacity: 1; }
  70% { opacity: 1; }
  100% { opacity: 0; }
}
.rw-pdf__skeleton {
  position: absolute;
  inset: 0;
  background: var(--rw-surface);
}
.rw-pdf__toast {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  margin: 0;
  padding: 7px 14px;
  border-radius: var(--rw-radius-md);
  background: var(--rw-text);
  color: var(--rw-surface);
  font-size: var(--rw-fs-sm);
  z-index: 30;
}
</style>
