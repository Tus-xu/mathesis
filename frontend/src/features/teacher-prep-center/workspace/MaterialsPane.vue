<script setup lang="ts">
/**
 * S7 教材与资料（golden4.png · G-golden4）。
 *
 * 三栏：左信息列 280 / 中教材原文阅读器（弹性）/ 右来源与资料 300。
 * - 左列：会话标题 + 元信息；教学目标（真实 blueprint/context objectives）；「教材与资料」
 *   五类树（教材解析/相关图片/思维导图/例题素材/拓展资料，**条目恒在**、数据有则计数，
 *   点击切中栏内容或诚实空态；**只保留本会话章节内容**——混入 chunk 整段剔除）+「我的资料」组
 *   （树下方成组：个人上传标题 + 真实文件行（名+大小）/「暂无上传资料」+ 上传入口）；
 * - 中列：教材原文阅读器 —— 切片 markdown 经 `sliceMarkdown` 渲染管线**结构化真实渲染**：
 *   节号徽章标题 / 定义 callout / 行内公式与公式块（KaTeX+编号）/ 插图（真实 <img>，
 *   失败或退化图元降级占位，**源码永不上屏**，G22）；工具条 = 教材 pill + 页码导航（真实翻页）
 *   + 缩放（真实生效）+ 视图切换（单页/连续）+ 检索；底条 = 页码 + 跳转输入（真实跳转）；
 * - 右列四区块（Golden MUST）：教材来源卡（封面+书名+章节+页码范围+引文块
 *   + 定位原文（实心蓝，翻页+滚动中栏并高亮）+ 加入备课依据（描边，真实写入会话依据））
 *   / 拓展资料（文件行形态）/ 相关图片（真实切片插图缩略+图题）/ 我的资料（真实资源行 + 上传链）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  fetchTextbookView,
  type PrepTextbookView,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import SliceBlocks from '@/features/teacher-prep-center/math/SliceBlocks.vue'
import SliceFigure from '@/features/teacher-prep-center/math/SliceFigure.vue'
import {
  cleanInlineSlice,
  parseSliceBlocks,
  refSectionId,
  scopeBlocksToSection,
  sectionNoOf,
  sectionRef,
  sliceTitleText,
  type SliceBlock,
} from '@/features/teacher-prep-center/math/sliceMarkdown'
import TextbookCover, { textbookDisplayName } from './classroom/TextbookCover.vue'
import { MATERIALS as M } from '@/features/teacher-prep-center/copy/prepCopy'
import { v3Api, type V3ResourceItem } from '@/api/teacherV3'
import { presignUpload } from '@/api/teacherV3Upload'

const route = useRoute()
const store = usePrepSessionStore()

const sessionId = computed(() => String(route.params.sessionId || store.currentSessionId || ''))

/* ── 左列：会话标题 + 元信息 ──────────────────────────────────────── */
const sessionTitle = computed(() => store.currentCourseTitle || '备课记录')
const sessionMeta = computed(() => {
  const meta = store.currentCourseMeta
  const parts: string[] = []
  if (meta.textbook) parts.push(meta.textbook.split('·')[0]?.trim() || meta.textbook)
  if (meta.className) parts.push(meta.className)
  parts.push(`${meta.minutes}分钟`)
  return parts.join(' · ')
})

/* ── 教学目标（蓝图 objectives 优先，回退上下文 objectives）────────── */
const objectives = computed<{ text: string }[]>(() => {
  const bp = store.current?.artifact.blueprint?.body?.objectives
  const fromBp = Array.isArray(bp) ? bp.filter((o) => !!o?.text) : []
  if (fromBp.length) return fromBp.map((o) => ({ text: o.text }))
  const ctx = store.current?.artifact.context?.objectives
  return Array.isArray(ctx) ? ctx.filter((o) => !!o?.text).map((o) => ({ text: o.text as string })) : []
})

/* ── 本会话章节（chunk 作用域过滤的唯一依据）───────────────────────── */
const ctx = computed(() => store.current?.artifact.context ?? null)
const targetSectionId = computed(() => ctx.value?.section?.id || null)
const targetNo = computed(() => {
  const s = ctx.value?.section
  return (
    sectionNoOf(s?.section_path) ||
    sectionNoOf(s?.title) ||
    sectionNoOf(view.value?.section?.section_path) ||
    sectionNoOf(view.value?.section?.title) ||
    null
  )
})

/** evidence 条目可读标题（SliceBlocks 同源渲染：**保留行内数学**，KatexText 上屏，
 *  修复标题层「如图, 设, 是…」符号剥失）：优先切片章节/小节标题，其次首句。 */
function evidenceTitle(item: { title?: string | null; snippet?: string | null }): string | null {
  const source = item.snippet || item.title || ''
  const headingMatch = /^[ \t]*#{1,6}[ \t]*(.+?)[ \t]*$/m.exec(source)
  const heading = sliceTitleText(headingMatch?.[1] || '', 24)
  if (heading.length >= 4) return heading
  const body = cleanInlineSlice(source.replace(/^[ \t]*#{1,6}[ \t]*.*$/gm, ' '))
  const sentence = body
    .replace(/图\s*\d+(?:[.\-–]\d+)*/g, '')
    .replace(/^[\s，,、。；;：:]+/, '')
    .split(/[。；！？]/)[0]
    .trim()
  return sliceTitleText(sentence, 20) || null
}

/* ── 教材与资料（G-golden4 五类树：条目恒在，数据有则计数）────────────── */
/** 教材解析取材：概念/定理/前置知识/重难点/目标（仅本会话章节）。 */
const ANALYSIS_KINDS = ['concepts', 'theorems', 'prior_knowledge', 'key_difficult_points', 'objectives']
/** 拓展资料取材：本节探究/思考/设计意图等片段 + 其它章节的前置知识（明确标注，不冒充本节）。 */
const EXTRA_KINDS = ['inquiries', 'design_intent', 'blueprint', 'fact']

const evidence = computed(() => store.current?.evidence ?? [])

interface EvidenceEntry {
  ref: string
  label: string
  tag: string
}

/** 本会话章节归属判定（ref 的 /section/<id> 或取自 chunk 归属）。 */
function inSessionSection(ref: string): boolean {
  const sid = refSectionId(ref)
  return !targetSectionId.value || (!!sid && sid === targetSectionId.value)
}

/** 教材解析条目（真实 evidence，本会话章节内）。 */
const analysisItems = computed<EvidenceEntry[]>(() =>
  evidence.value
    .filter((e) => ANALYSIS_KINDS.includes(String(e.kind)) && inSessionSection(e.ref))
    .map((e): EvidenceEntry | null => {
      const label = evidenceTitle(e)
      return label ? { ref: e.ref, label, tag: '' } : null
    })
    .filter((e): e is EvidenceEntry => !!e),
)

/** 拓展资料：真实教材片段（本节探究/思考/设计意图 + 其它章节前置知识，逐条标注）。 */
const extraItems = computed<EvidenceEntry[]>(() => {
  const out: EvidenceEntry[] = []
  for (const e of evidence.value) {
    const kind = String(e.kind)
    const scoped = inSessionSection(e.ref)
    const isExtraKind = EXTRA_KINDS.includes(kind)
    const isPrior = kind === 'prior_knowledge' && !scoped
    if (!isExtraKind && !isPrior) continue
    const label = evidenceTitle(e)
    if (label) out.push({ ref: e.ref, label, tag: isPrior ? '前置知识' : '本节片段' })
    if (out.length >= 4) break
  }
  return out
})

/* ── 中列：教材原文（真实切片 + 渲染管线）────────────────────────── */
const locator = computed(() => {
  // 权威锚 = 本会话章节级 ref（后端 SourceLocator v1）；其次 evidence 命中本节；
  // 最后才回退 context.locator / 首条 evidence（可能指向其它章节 → 由块级过滤兜底）。
  const built = sectionRef(ctx.value?.source?.id, ctx.value?.section?.id)
  if (built) return built
  const hit = evidence.value.find((e) => inSessionSection(e.ref))
  if (hit?.ref) return hit.ref
  return ctx.value?.locator || evidence.value[0]?.ref || ''
})
const view = ref<PrepTextbookView | null>(null)
const loading = ref(false)
const failed = ref(false)

async function load() {
  const refId = locator.value
  if (!sessionId.value || !refId) {
    view.value = null
    return
  }
  loading.value = true
  failed.value = false
  try {
    view.value = await fetchTextbookView(sessionId.value, refId)
  } catch {
    view.value = null
    failed.value = true
  } finally {
    loading.value = false
  }
}
watch([sessionId, locator], () => void load(), { immediate: true })

/** 书名（全局权威展示名优先，与顶栏/PPT 页眉同口径；`·` → 空格、去年份）。 */
const bookName = computed(() =>
  textbookDisplayName(store.currentCourseMeta.textbook || view.value?.source_title || ''),
)

const pageRange = computed<[number, number] | null>(() => {
  const r = view.value?.page_range
  return r && Number.isFinite(r[0]) && Number.isFinite(r[1]) ? r : null
})
const pageText = computed(() =>
  pageRange.value ? M.pagesLabel(pageRange.value[0], pageRange.value[1]) : '',
)

interface ChunkView {
  id: string
  page: string
  blocks: SliceBlock[]
}

/** 中栏块视图：解析 + **chunk 作用域过滤**（只保留本会话章节内容，混入章节整段剔除）。 */
const chunkViews = computed<ChunkView[]>(() => {
  const chunks = view.value?.chunks ?? []
  const chunkScoped =
    !targetSectionId.value ||
    refSectionId(view.value?.ref) === targetSectionId.value ||
    view.value?.section?.id === targetSectionId.value
  const targetTitle = ctx.value?.section?.title || view.value?.section?.title || null
  return chunks
    .map((c) => ({
      id: c.id,
      page: c.page_range && Number.isFinite(c.page_range[0]) ? `P${c.page_range[0]}` : '',
      blocks: scopeBlocksToSection(parseSliceBlocks(c.text), targetNo.value, { chunkScoped, targetTitle }),
    }))
    .filter((c) => c.blocks.length > 0)
})

/** 切片插图清单（本节作用域内）：含图题/标注的真实插图——无标注的抽取退化图元
 *  （行内图标被当插图抽出）不算「相关图片」，中栏由 SliceFigure 统一降级。 */
const figureItems = computed(() => {
  const out: { src: string | null; caption: string; alt: string; blockKey: string; chunkId: string }[] = []
  for (const c of chunkViews.value) {
    c.blocks.forEach((b, i) => {
      if (b.kind === 'figure' && (b.caption.trim() || b.alt.trim())) {
        out.push({ src: b.src, caption: b.caption, alt: b.alt, blockKey: `${c.id}-b${i}`, chunkId: c.id })
      }
    })
  }
  return out
})

/** 右栏「相关图片」：本节切片里的真实插图（含图题）；点击定位到中栏对应图。
 *  有图题者优先（Golden 缩略+图题形态），其次补位到 3 张。 */
const relatedFigures = computed(() => {
  const captioned = figureItems.value.filter((f) => f.caption.trim())
  const plain = figureItems.value.filter((f) => !f.caption.trim())
  return [...captioned, ...plain].slice(0, 3)
})

/** 例题素材：本节切片真实例题标题（例1…例4）+ evidence examples（若有）。 */
const exampleItems = computed<TreeItem[]>(() => {
  const out: TreeItem[] = []
  for (const c of chunkViews.value) {
    c.blocks.forEach((b, i) => {
      if (b.kind === 'heading' && /^例\s*\d/.test(b.text)) {
        out.push({ label: sliceTitleText(b.text, 26), tag: '', ref: null, blockKey: `${c.id}-b${i}`, chunkId: c.id })
      }
    })
  }
  for (const e of evidence.value) {
    if (String(e.kind) === 'examples' && inSessionSection(e.ref)) {
      const label = evidenceTitle(e)
      if (label) out.push({ label, tag: '', ref: e.ref, blockKey: null, chunkId: null })
    }
  }
  return out
})

/* ── 二栏「教材与资料」树（G-golden4 五类，条目恒在）+ 点击切中栏 ──── */
interface TreeItem {
  label: string
  tag: string
  ref: string | null
  blockKey: string | null
  chunkId: string | null
}
type TreeKey = 'analysis' | 'images' | 'mindmap' | 'examples' | 'extra'

function evidenceToTree(items: EvidenceEntry[]): TreeItem[] {
  return items.map((it) => ({ label: it.label, tag: it.tag, ref: it.ref, blockKey: null, chunkId: null }))
}

const treeCategories = computed<{ key: TreeKey; label: string; items: TreeItem[] }[]>(() => [
  { key: 'analysis', label: M.groupAnalysis, items: evidenceToTree(analysisItems.value) },
  {
    key: 'images',
    label: M.relatedImages,
    items: figureItems.value.map((f) => ({
      label: f.caption || f.alt || M.relatedImages,
      tag: '',
      ref: null,
      blockKey: f.blockKey,
      chunkId: f.chunkId,
    })),
  },
  { key: 'mindmap', label: M.groupMindmap, items: [] }, // 无数据源 → 恒空（诚实空态，条目仍在）
  { key: 'examples', label: M.groupExamples, items: exampleItems.value },
  { key: 'extra', label: M.extraTitle, items: evidenceToTree(extraItems.value) },
])

const activeTree = ref<TreeKey>('analysis')
function selectTree(key: TreeKey) {
  activeTree.value = key
}
const activeCategory = computed(
  () => treeCategories.value.find((c) => c.key === activeTree.value) || treeCategories.value[0],
)
function emptyTextOf(key: TreeKey): string {
  if (key === 'images') return M.imagesEmpty
  if (key === 'mindmap') return M.mindmapEmpty
  if (key === 'examples') return M.examplesEmpty
  if (key === 'extra') return M.extraEmpty
  return M.analysisEmpty
}
function openTreeItem(it: TreeItem) {
  if (it.ref) {
    openRef(it.ref)
    return
  }
  if (it.blockKey || it.chunkId) locateBlock(it.chunkId, it.blockKey)
}

/* ── 阅读器交互：翻页 / 跳转 / 缩放 / 视图 / 检索（全部真实状态变化）── */
const readerEl = ref<HTMLElement | null>(null)

/* 翻页（数字 = 切片页数，真实翻页）：单页=只渲染当前切片；连续=全量滚动 */
const viewMode = ref<'single' | 'continuous'>('single')
const pageIndex = ref(0)
const pageCount = computed(() => chunkViews.value.length)
const pageIndicator = computed(() =>
  pageCount.value ? M.pageOf(pageIndex.value + 1, pageCount.value) : '',
)
const visibleChunks = computed<ChunkView[]>(() => {
  if (viewMode.value !== 'single' || !pageCount.value) return chunkViews.value
  return [chunkViews.value[Math.min(pageIndex.value, pageCount.value - 1)]]
})
watch(pageCount, (n) => {
  if (pageIndex.value >= n) pageIndex.value = Math.max(0, n - 1)
})
function goPage(i: number) {
  if (!pageCount.value) return
  pageIndex.value = Math.min(pageCount.value - 1, Math.max(0, i))
  if (viewMode.value === 'continuous') {
    void nextTick(() => {
      const id = chunkViews.value[pageIndex.value]?.id
      const el = id ? readerEl.value?.querySelector(`[data-chunk="${id}"]`) : null
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }
}
function prevPage() {
  goPage(pageIndex.value - 1)
}
function nextPage() {
  goPage(pageIndex.value + 1)
}
const jumpInput = ref('')
function applyJump() {
  const n = Number.parseInt(jumpInput.value, 10)
  jumpInput.value = ''
  if (Number.isFinite(n)) goPage(n - 1)
}

const zoom = ref(1)
const zoomPct = computed(() => Math.round(zoom.value * 100))
function zoomBy(delta: number) {
  zoom.value = Math.min(1.6, Math.max(0.8, Math.round((zoom.value + delta) * 10) / 10))
}
const isFullscreen = ref(false)
function toggleFullscreen() {
  const el = readerEl.value
  if (!el) return
  if (document.fullscreenElement) void document.exitFullscreen()
  else void el.requestFullscreen?.()
}
function onFullscreenChange() {
  isFullscreen.value = document.fullscreenElement === readerEl.value
}
watch(readerEl, (el, _old, onCleanup) => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  onCleanup(() => document.removeEventListener('fullscreenchange', onFullscreenChange))
})

function blockSearchText(b: SliceBlock): string {
  if (b.kind === 'formula') return b.tex
  if (b.kind === 'figure') return b.caption
  return b.text
}
const query = ref('')
const activeMatch = ref(0)
const matchedIds = computed<string[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return []
  return chunkViews.value
    .filter((c) => c.blocks.some((b) => blockSearchText(b).toLowerCase().includes(q)))
    .map((c) => c.id)
})
const matchCountText = computed(() =>
  matchedIds.value.length ? M.searchMatches(matchedIds.value.length) : M.searchNoMatch,
)
function jumpMatch(delta: number) {
  const ids = matchedIds.value
  if (!ids.length) return
  activeMatch.value = (activeMatch.value + delta + ids.length) % ids.length
  locateBlock(ids[activeMatch.value], null)
}
function nextMatch() {
  activeMatch.value = -1
  jumpMatch(1)
}

/* ── 定位原文（真实滚动 + 高亮；单页模式先翻到目标切片页）───────────── */
function highlightBlock(el: Element | null | undefined) {
  if (!el) return
  readerEl.value?.querySelectorAll('.is-located').forEach((n) => n.classList.remove('is-located'))
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('is-located')
  window.setTimeout(() => el.classList.remove('is-located'), 2600)
}
/** 切回教材原文视图并（按需）翻到目标切片页，再高亮目标块/切片。 */
function locateBlock(chunkId: string | null, blockKey: string | null) {
  activeTree.value = 'analysis'
  const chunk = chunkId
    ? chunkViews.value.find((c) => c.id === chunkId)
    : blockKey
      ? chunkViews.value.find((c) => blockKey.startsWith(`${c.id}-b`))
      : null
  const idx = chunk ? chunkViews.value.indexOf(chunk) : -1
  const reveal = () => {
    const sel = blockKey
      ? `[data-block="${blockKey}"]`
      : chunkId
        ? `[data-chunk="${chunkId}"]`
        : '[data-chunk]'
    highlightBlock(readerEl.value?.querySelector(sel))
  }
  if (idx >= 0 && viewMode.value === 'single') {
    pageIndex.value = idx
    void nextTick(reveal)
    return
  }
  void nextTick(reveal)
}
function locateSource() {
  locateBlock(quote.value?.chunkId || null, quote.value?.blockKey || null)
}
function locateFigure(f: { blockKey: string; chunkId?: string | null }) {
  locateBlock(f.chunkId || null, f.blockKey)
}

/* ── 加入备课依据（真实写入会话依据：结构化 selection 直传 + 快照刷新）── */
const joining = ref(false)
const joined = ref(false)
const joinNotice = ref('')
const canJoin = computed(
  () => !!ctx.value?.source?.id && !!ctx.value?.section?.id && !!sessionId.value,
)
async function joinEvidence() {
  const sourceId = ctx.value?.source?.id
  const sectionId = ctx.value?.section?.id
  if (!sourceId || !sectionId || !sessionId.value) return
  joining.value = true
  joinNotice.value = ''
  try {
    const ok = await store.pickSection(
      sessionId.value,
      { textbook_id: sourceId, section_id: sectionId },
      `把教材原文「${sectionLine.value || '本节'}」作为本课备课依据`,
    )
    joined.value = ok
    joinNotice.value = ok ? M.joinNoticeOk : M.joinFail
  } catch {
    joinNotice.value = M.joinFail
  } finally {
    joining.value = false
  }
}

/* ── 我的资料：真实资源行 + 真实上传（presign 直传 MinIO）─────────── */
const resources = ref<V3ResourceItem[]>([])
/** 会话内真实上传文件（名+大小均取自真实 File，刷新后无服务端清单 → 诚实空态）。 */
const uploads = ref<{ name: string; key: string; size: number }[]>([])
const uploadState = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const leftFileInput = ref<HTMLInputElement | null>(null)
const KIND_LABEL: Record<string, string> = {
  deck: '课件',
  plan: '教案',
  'figure-recipe': '图形配方',
  'photo-bank': '图片库',
}
onMounted(async () => {
  try {
    resources.value = (await v3Api.catalog.resources()).data.items.slice(0, 3)
  } catch {
    resources.value = []
  }
})
/** 文件大小（真实 File.size 派生；无数据不编造）。 */
function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
function pickUploadFile() {
  fileInput.value?.click()
}
function pickLeftUpload() {
  leftFileInput.value?.click()
}
async function onUploadChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadState.value = M.uploading
  try {
    const handle = await presignUpload(file)
    uploads.value = [{ name: file.name, key: handle.key, size: file.size }, ...uploads.value].slice(0, 3)
    uploadState.value = M.uploadDone(file.name)
  } catch {
    uploadState.value = M.uploadFail
  } finally {
    input.value = ''
  }
}

function openRef(ref: string) {
  if (ref) void store.openTextbookViewer(sessionId.value, ref)
}
function focusComposer() {
  window.dispatchEvent(new CustomEvent('pc:focus-composer'))
}

/** 引文块：本节首个正文短句（真实切片原文，KaTeX 渲染）+ 所在块锚（定位用）。 */
const quote = computed<{ text: string; chunkId: string; blockKey: string } | null>(() => {
  for (const c of chunkViews.value) {
    for (let i = 0; i < c.blocks.length; i += 1) {
      const b = c.blocks[i]
      if (b.kind !== 'paragraph' && b.kind !== 'definition' && b.kind !== 'list') continue
      const t = b.text.trim()
      if (t.length <= 12) continue
      const sentence = t.match(/^.{8,80}?[。；]/)
      return {
        text: sentence ? sentence[0] : t.slice(0, 80),
        chunkId: c.id,
        blockKey: `${c.id}-b${i}`,
      }
    }
  }
  return null
})
const sectionLine = computed(() => {
  const s = view.value?.section
  const c = ctx.value?.section
  const title = s?.title || c?.title
  if (!title) return null
  const path = s?.section_path || c?.section_path
  return path ? `${path} ${title}` : title
})
</script>

<template>
  <section class="pc-mat">
    <div class="pc-mat__grid">
      <!-- 左信息列 -->
      <aside class="pc-mat__col pc-mat__col--left">
        <div class="pc-mat__session">
          <h2 class="pc-mat__session-title" :title="sessionTitle">{{ sessionTitle }}</h2>
          <p v-if="sessionMeta" class="pc-mat__session-meta">{{ sessionMeta }}</p>
        </div>

        <section v-if="objectives.length" class="pc-card">
          <h3 class="pc-card__title">{{ M.objectivesTitle }}</h3>
          <ol class="pc-mat__objectives">
            <li v-for="(o, i) in objectives" :key="i"><KatexText :text="o.text" /></li>
          </ol>
        </section>

        <!-- 「教材与资料」树（G-golden4 五类：条目恒在，数据有则计数；点击切中栏） -->
        <section class="pc-card">
          <h3 class="pc-card__title">{{ M.materialsTitle }}</h3>
          <div class="pc-mat__tree">
            <div
              v-for="cat in treeCategories"
              :key="cat.key"
              class="pc-mat__tree-group"
              :class="{ 'is-active': activeTree === cat.key }"
            >
              <button
                type="button"
                class="pc-mat__tree-row"
                :class="{ 'is-active': activeTree === cat.key }"
                :data-tree="cat.key"
                :title="cat.label"
                @click="selectTree(cat.key)"
              >
                <svg class="pc-mat__tree-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M2 4.4A1.4 1.4 0 0 1 3.4 3h3l1.3 1.4h4.9A1.4 1.4 0 0 1 14 5.8v5.8A1.4 1.4 0 0 1 12.6 13H3.4A1.4 1.4 0 0 1 2 11.6V4.4Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
                </svg>
                <span class="pc-mat__tree-label" :title="cat.label">{{ cat.label }}</span>
                <span class="pc-mat__group-count">{{ cat.items.length }}</span>
                <svg
                  class="pc-mat__tree-chevron"
                  :class="{ 'is-open': activeTree === cat.key }"
                  viewBox="0 0 12 12"
                  aria-hidden="true"
                >
                  <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
              <ul v-if="activeTree === cat.key && cat.items.length" class="pc-mat__group-list">
                <li v-for="(it, i) in cat.items" :key="i">
                  <button type="button" class="pc-mat__group-item" @click="openTreeItem(it)">
                    <span class="pc-mat__group-name" :title="it.label"><KatexText :text="it.label" /></span>
                    <span v-if="it.tag" class="pc-mat__tag">{{ it.tag }}</span>
                    <svg class="pc-mat__group-chevron" viewBox="0 0 12 12" aria-hidden="true">
                      <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                </li>
              </ul>
            </div>
          </div>
        </section>

        <!-- 「我的资料」组（G-golden4：树下方成组——个人上传标题 + 文件行含大小） -->
        <section class="pc-card pc-mat__my" data-test="pc-my-materials">
          <h3 class="pc-card__title">{{ M.myMaterials }}</h3>
          <p class="pc-mat__my-sub">{{ M.myUploadsTitle }}</p>
          <ul v-if="uploads.length" class="pc-mat__group-list">
            <li v-for="(u, i) in uploads" :key="`lu${i}`">
              <div class="pc-mat__file">
                <svg class="pc-mat__file-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M4 1.8h5l3 3v9.4H4V1.8Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                  <path d="M9 1.8v3.2h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                </svg>
                <span class="pc-mat__file-name" :title="u.name">{{ u.name }}</span>
                <span class="pc-mat__file-size">{{ formatSize(u.size) }}</span>
              </div>
            </li>
          </ul>
          <p v-else class="pc-mat__hint" data-test="pc-my-empty">{{ M.uploadsEmpty }}</p>
          <button type="button" class="pc-mat__upload" @click="pickLeftUpload">+ {{ M.uploadFile }}</button>
          <input id="pc-my-upload-input" ref="leftFileInput" type="file" class="pc-mat__file-input" @change="onUploadChange" />
        </section>
      </aside>

      <!-- 中列：教材原文阅读器 -->
      <div class="pc-mat__col pc-mat__col--center">
        <div ref="readerEl" class="pc-reader">
          <header class="pc-reader__bar">
            <div class="pc-reader__bar-row">
              <span v-if="bookName" class="pc-reader__book" :title="bookName">{{ bookName }}</span>
              <div class="pc-reader__pager" data-test="pc-pager">
                <button
                  type="button"
                  class="pc-reader__tool"
                  :title="M.prevPage"
                  :disabled="!pageCount || pageIndex <= 0"
                  @click="prevPage"
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </button>
                <span class="pc-reader__pager-text" data-test="pc-page-indicator">{{ pageIndicator }}</span>
                <button
                  type="button"
                  class="pc-reader__tool"
                  :title="M.nextPage"
                  :disabled="!pageCount || pageIndex >= pageCount - 1"
                  @click="nextPage"
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </button>
              </div>
            </div>
            <div class="pc-reader__bar-row pc-reader__bar-row--tools">
              <div class="pc-reader__zoom-group">
                <button type="button" class="pc-reader__tool" :title="M.zoomOut" @click="zoomBy(-0.1)">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 8h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
                </button>
                <span class="pc-reader__zoom" data-test="pc-zoom">{{ zoomPct }}%</span>
                <button type="button" class="pc-reader__tool" :title="M.zoomIn" @click="zoomBy(0.1)">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4v8M4 8h8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
                </button>
              </div>
              <div class="pc-reader__views" role="group" data-test="pc-view-toggle">
                <button
                  type="button"
                  class="pc-reader__view-btn"
                  :class="{ 'is-active': viewMode === 'single' }"
                  :aria-pressed="viewMode === 'single'"
                  @click="viewMode = 'single'"
                >
                  {{ M.viewSingle }}
                </button>
                <button
                  type="button"
                  class="pc-reader__view-btn"
                  :class="{ 'is-active': viewMode === 'continuous' }"
                  :aria-pressed="viewMode === 'continuous'"
                  @click="viewMode = 'continuous'"
                >
                  {{ M.viewContinuous }}
                </button>
              </div>
              <button type="button" class="pc-reader__tool" :title="isFullscreen ? M.exitFullscreen : M.fullscreen" @click="toggleFullscreen">
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M6 2.6H2.6V6M10 2.6h3.4V6M6 13.4H2.6V10M10 13.4h3.4V10" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
              <label class="pc-reader__search">
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4" />
                  <path d="M10.4 10.4 14 14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
                </svg>
                <input v-model="query" type="text" :placeholder="M.searchPlaceholder" @keydown.enter.prevent="nextMatch" />
                <span v-if="query.trim()" class="pc-reader__match">{{ matchCountText }}</span>
              </label>
            </div>
          </header>

          <div class="pc-reader__body">
            <div v-if="loading" class="pc-reader__state">{{ M.readerLoading }}</div>
            <div v-else-if="failed" class="pc-reader__state pc-reader__state--error">
              {{ M.readerFailed }}
              <button type="button" class="pc-reader__retry" @click="load">{{ M.readerRetry }}</button>
            </div>
            <div v-else-if="!view || !chunkViews.length" class="pc-reader__state">{{ M.readerEmpty }}</div>
            <template v-else>
              <!-- 树分类视图（点击二栏条目切换；空类=诚实空态） -->
              <div v-if="activeTree !== 'analysis'" class="pc-mat__cat" data-test="pc-cat-panel">
                <template v-if="activeCategory && activeCategory.items.length">
                  <ul v-if="activeTree === 'images'" class="pc-mat__fig-grid">
                    <li v-for="(f, i) in figureItems" :key="i">
                      <SliceFigure :src="f.src" :caption="f.caption" :alt="f.alt" compact />
                      <button type="button" class="pc-mat__cat-locate" @click="locateBlock(f.chunkId, f.blockKey)">
                        {{ M.locateFigure }}
                      </button>
                    </li>
                  </ul>
                  <ul v-else class="pc-mat__group-list">
                    <li v-for="(it, i) in activeCategory.items" :key="i">
                      <button type="button" class="pc-mat__group-item" @click="openTreeItem(it)">
                        <span class="pc-mat__group-name" :title="it.label"><KatexText :text="it.label" /></span>
                        <span v-if="it.tag" class="pc-mat__tag">{{ it.tag }}</span>
                        <svg class="pc-mat__group-chevron" viewBox="0 0 12 12" aria-hidden="true">
                          <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                        </svg>
                      </button>
                    </li>
                  </ul>
                </template>
                <div v-else class="pc-mat__cat-empty" data-test="pc-cat-empty">
                  <p>{{ emptyTextOf(activeTree) }}</p>
                </div>
                <button type="button" class="pc-mat__cat-back" @click="selectTree('analysis')">{{ M.backToReader }}</button>
              </div>
              <!-- 教材原文（默认）：真实切片渲染，单页/连续由视图切换控制 -->
              <div v-else class="pc-reader__flow" :style="{ '--pc-reader-scale': String(zoom) }" data-test="pc-reader-flow">
                <article
                  v-for="c in visibleChunks"
                  :key="c.id"
                  class="pc-reader__chunk"
                  :class="{ 'is-match': matchedIds.includes(c.id) }"
                  :data-chunk="c.id"
                >
                  <span v-if="c.page" class="pc-reader__chunk-page">{{ c.page }}</span>
                  <SliceBlocks :blocks="c.blocks" :anchor-prefix="c.id" />
                </article>
              </div>
            </template>
          </div>

          <footer class="pc-reader__foot">
            <span class="pc-reader__foot-page" data-test="pc-foot-page">{{ pageIndicator }}</span>
            <input
              v-model="jumpInput"
              class="pc-reader__jump"
              type="number"
              min="1"
              :max="Math.max(1, pageCount)"
              :placeholder="M.jumpPlaceholder"
              @keydown.enter.prevent="applyJump"
            />
            <button type="button" class="pc-reader__foot-btn" data-test="pc-jump" @click="applyJump">{{ M.jumpTo }}</button>
            <span class="pc-reader__foot-spacer"></span>
            <button type="button" class="pc-reader__foot-btn" @click="store.toggleMaterials(true)">{{ M.materials }}</button>
            <button type="button" class="pc-reader__foot-btn is-primary" @click="focusComposer">{{ M.aiSuggest }}</button>
          </footer>
        </div>
      </div>

      <!-- 右列：来源与资料（Golden 四区块） -->
      <aside class="pc-mat__col pc-mat__col--right">
        <!-- 1) 教材来源卡 -->
        <section class="pc-card">
          <h3 class="pc-card__title">{{ M.sourceTitle }}</h3>
          <div class="pc-mat__source">
            <TextbookCover :name="bookName" size="sm" />
            <div class="pc-mat__source-detail">
              <p class="pc-mat__source-book">{{ bookName || '—' }}</p>
              <p v-if="sectionLine" class="pc-mat__source-line">{{ sectionLine }}</p>
              <button v-if="pageText" type="button" class="pc-mat__source-page" @click="locateSource">{{ pageText }}</button>
            </div>
          </div>
          <blockquote v-if="quote" class="pc-mat__quote">
            <span class="pc-mat__quote-mark" aria-hidden="true">“</span>
            <KatexText class="pc-mat__quote-text" :text="quote.text" />
          </blockquote>
          <div class="pc-mat__actions">
            <button type="button" class="pc-mat__locate" :disabled="!chunkViews.length" @click="locateSource">
              {{ M.locate }}
            </button>
            <button
              type="button"
              class="pc-mat__join"
              :disabled="!canJoin || joining"
              @click="joinEvidence"
            >
              {{ joined ? M.joined : M.joinEvidence }}
            </button>
          </div>
          <p v-if="joinNotice" class="pc-mat__notice">{{ joinNotice }}</p>
        </section>

        <!-- 2) 拓展资料（真实教材片段；文件行形态：图标 + 名称 + 元信息 + ›） -->
        <section class="pc-card">
          <h3 class="pc-card__title">{{ M.extraTitle }}</h3>
          <p class="pc-mat__hint">{{ M.extraHint }}</p>
          <ul v-if="extraItems.length" class="pc-mat__group-list">
            <li v-for="(it, i) in extraItems" :key="i">
              <button type="button" class="pc-mat__group-item pc-mat__group-item--file" @click="openRef(it.ref)">
                <svg class="pc-mat__file-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M4 1.8h5l3 3v9.4H4V1.8Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                  <path d="M9 1.8v3.2h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                </svg>
                <span class="pc-mat__group-name" :title="it.label"><KatexText :text="it.label" /></span>
                <span class="pc-mat__tag">{{ it.tag }}</span>
                <svg class="pc-mat__group-chevron" viewBox="0 0 12 12" aria-hidden="true">
                  <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </li>
          </ul>
          <p v-else class="pc-mat__hint">{{ M.extraEmpty }}</p>
        </section>

        <!-- 3) 相关图片（本节切片真实插图 + 图题） -->
        <section class="pc-card">
          <h3 class="pc-card__title">{{ M.relatedImages }}</h3>
          <ul v-if="relatedFigures.length" class="pc-mat__thumbs">
            <li v-for="(f, i) in relatedFigures" :key="i">
              <button type="button" class="pc-mat__thumb" @click="locateFigure(f)">
                <SliceFigure :src="f.src" :caption="f.caption" :alt="f.alt" compact />
              </button>
            </li>
          </ul>
          <p v-else class="pc-mat__hint">{{ M.imagesEmpty }}</p>
        </section>

        <!-- 4) 我的资料（真实资源行 + 上传链） -->
        <section class="pc-card">
          <h3 class="pc-card__title">{{ M.myMaterials }}</h3>
          <ul class="pc-mat__group-list">
            <li v-for="(u, i) in uploads" :key="`u${i}`">
              <div class="pc-mat__file">
                <svg class="pc-mat__file-icon" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M4 1.8h5l3 3v9.4H4V1.8Z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                  <path d="M9 1.8v3.2h3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round" />
                </svg>
                <span class="pc-mat__file-name" :title="u.name">{{ u.name }}</span>
                <span class="pc-mat__file-size">{{ formatSize(u.size) }}</span>
              </div>
            </li>
            <li v-for="r in resources" :key="r.id">
              <div class="pc-mat__file">
                <span class="pc-mat__file-name" :title="r.name">{{ r.name }}</span>
                <span class="pc-mat__tag">{{ KIND_LABEL[r.kind] || r.kind }}</span>
              </div>
            </li>
          </ul>
          <p v-if="!uploads.length && !resources.length" class="pc-mat__hint">{{ M.resourcesEmpty }}</p>
          <button type="button" class="pc-mat__upload" @click="pickUploadFile">+ {{ M.uploadFile }}</button>
          <input ref="fileInput" type="file" class="pc-mat__file-input" @change="onUploadChange" />
          <p v-if="uploadState" class="pc-mat__notice">{{ uploadState }}</p>
        </section>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.pc-mat {
  padding: 20px 24px 160px;
  font-family: var(--pc-font);
}
.pc-mat__grid {
  display: grid;
  /* G23 栏宽配比（R4afix2 返工定型，R4afix3 仅在授权区间内微调二栏）：
   * Golden G-golden4 三栏 15.6% / 62% / 22.4%。中栏教材页画布是主对象，必须拿最大份额；
   * 二栏信息列 clamp 上限 200px（15.6–20% 授权区间内取 20%，1440 实测 ≈194px，
   * 为 12px 标签可读与「我的资料」组同帧让位）；右栏来源列 ≈22.4%（215–250px）不动。
   * 中列 minmax(0,1fr) 吃满剩余 = 无横滚。 */
  grid-template-columns: clamp(150px, 20%, 200px) minmax(0, 1fr) clamp(215px, 22.4%, 250px);
  gap: 16px;
  align-items: start;
}
.pc-mat__col {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  max-height: calc(var(--pc-viewport-h, 100vh) - 236px);
  overflow: auto;
}
.pc-mat__col--center {
  overflow: hidden;
}

/* 左列：会话标题（R4afix3：14px + balance 折行，杜绝孤字换行） */
.pc-mat__session-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.35;
  text-wrap: balance;
}
.pc-mat__session-meta {
  margin: 1px 0 0;
  font-size: 12px;
  color: var(--pc-text-muted);
}

/* 卡片（R4afix3 紧凑排版：为二栏「我的资料」组同帧可见让出纵向空间） */
.pc-card {
  padding: 7px 10px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-card__title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-mat__hint {
  margin: -1px 0 4px;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--pc-text-faint);
}

/* 教学目标 */
.pc-mat__objectives {
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: pc-obj;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pc-mat__objectives li {
  position: relative;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--pc-text-2);
}
.pc-mat__objectives li::before {
  counter-increment: pc-obj;
  content: counter(pc-obj) '.';
  position: absolute;
  left: 2px;
  font-weight: 600;
  color: var(--pc-primary);
}

/* 教材与资料 五类树（G-golden4：教材解析高亮态 = is-active） */
.pc-mat__tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pc-mat__tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-left: 3px solid transparent;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.pc-mat__tree-row:hover {
  background: var(--pc-section-bg);
}
.pc-mat__tree-row.is-active {
  border-left-color: var(--pc-primary);
  background: var(--pc-primary-soft);
}
.pc-mat__tree-icon {
  flex: none;
  width: 14px;
  height: 14px;
  color: var(--pc-primary);
}
/* R4afix3 标签排版：12px + 允许两行折行（不省略号截断）+ title 全名兜底 */
.pc-mat__tree-label {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-text-2);
  line-height: 1.4;
  white-space: normal;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-mat__tree-row.is-active .pc-mat__tree-label {
  color: var(--pc-primary);
}
.pc-mat__tree-chevron {
  flex: none;
  width: 12px;
  height: 12px;
  color: var(--pc-text-faint);
  transition: transform 0.15s ease;
}
.pc-mat__tree-chevron.is-open {
  transform: rotate(90deg);
}
.pc-mat__tree-group.is-active .pc-mat__group-list {
  margin: 0 0 3px 16px;
}
.pc-mat__group-count {
  margin-left: auto;
  font-size: 11px;
  font-weight: 500;
  color: var(--pc-text-faint);
  font-variant-numeric: tabular-nums;
}

/* 「我的资料」组（树下方成组） */
.pc-mat__my-sub {
  margin: -1px 0 3px;
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-text-muted);
}
.pc-mat__file-icon {
  flex: none;
  width: 15px;
  height: 15px;
  color: var(--pc-text-faint);
}
.pc-mat__file-size {
  flex: none;
  font-size: 11px;
  color: var(--pc-text-faint);
  font-variant-numeric: tabular-nums;
}
.pc-mat__group-item--file .pc-mat__file-icon {
  color: var(--pc-primary);
}

/* 中栏分类视图（树点击切换；空类 = 诚实空态） */
.pc-mat__cat {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pc-mat__fig-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}
.pc-mat__fig-grid li {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 8px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
}
.pc-mat__cat-locate {
  padding: 2px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  font-family: inherit;
  font-size: 12px;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-mat__cat-locate:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-mat__cat-empty {
  padding: 40px 0;
  text-align: center;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-mat__cat-back {
  align-self: flex-start;
  padding: 4px 2px;
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--pc-primary);
  cursor: pointer;
}
.pc-mat__group-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pc-mat__group-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 6px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.pc-mat__group-item:hover {
  background: var(--pc-section-bg);
}
/* R4afix3 标签排版：12px + 两行折行（不省略号截断）+ title 全名兜底 */
.pc-mat__group-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--pc-text-2);
  line-height: 1.4;
  white-space: normal;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-mat__group-chevron {
  flex: none;
  width: 12px;
  height: 12px;
  color: var(--pc-text-faint);
}
.pc-mat__tag {
  flex: none;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--pc-section-bg);
  font-size: 10.5px;
  color: var(--pc-text-muted);
}

/* ── 教材原文阅读器 ─────────────────────────────────────────────── */
.pc-reader {
  display: flex;
  flex-direction: column;
  height: calc(var(--pc-viewport-h, 100vh) - 236px);
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-panel);
  overflow: hidden;
}
.pc-reader:fullscreen {
  height: 100vh;
  border-radius: 0;
}
.pc-reader__bar {
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--pc-line);
}
.pc-reader__bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.pc-reader__bar-row--tools {
  flex-wrap: wrap;
}
.pc-reader__book {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-bg);
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-reader__pager {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pc-reader__pager-text {
  min-width: 88px;
  text-align: center;
  font-size: 12px;
  color: var(--pc-text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.pc-reader__zoom-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pc-reader__views {
  display: inline-flex;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  overflow: hidden;
}
.pc-reader__view-btn {
  height: 26px;
  padding: 0 9px;
  border: none;
  background: var(--pc-panel);
  font-family: inherit;
  font-size: 11.5px;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-reader__view-btn + .pc-reader__view-btn {
  border-left: 1px solid var(--pc-line);
}
.pc-reader__view-btn.is-active {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-reader__search {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 120px;
  min-width: 0;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-bg);
}
.pc-reader__search svg {
  flex: none;
  width: 13px;
  height: 13px;
  color: var(--pc-text-faint);
}
.pc-reader__search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-text);
}
.pc-reader__match {
  font-size: 11px;
  color: var(--pc-text-faint);
  white-space: nowrap;
}
.pc-reader__tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-reader__tool:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-reader__tool svg {
  width: 14px;
  height: 14px;
}
.pc-reader__zoom {
  min-width: 38px;
  text-align: center;
  font-size: 12px;
  color: var(--pc-text-muted);
  font-variant-numeric: tabular-nums;
}
.pc-reader__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 20px 26px 28px;
  font-size: 14px;
}
/* 缩放真实生效：切片流整体按比例缩放（−/100%/+ 全部可见变化） */
.pc-reader__flow {
  zoom: var(--pc-reader-scale, 1);
}
.pc-reader__state {
  padding: 40px 0;
  text-align: center;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-reader__state--error {
  color: var(--pc-danger);
}
.pc-reader__retry {
  margin-left: 8px;
  padding: 2px 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-reader__chunk {
  position: relative;
  padding: 4px 8px;
  border-radius: var(--pc-radius-btn);
}
.pc-reader__chunk + .pc-reader__chunk {
  margin-top: 10px;
}
.pc-reader__chunk.is-match {
  background: var(--pc-primary-soft);
  outline: 1px solid var(--pc-primary);
}
/* 定位原文 / 相关图片 → 滚动到块并高亮（真实可见状态变化） */
.pc-reader__chunk.is-located,
.pc-reader__chunk :deep(.is-located) {
  background: var(--pc-primary-soft);
  outline: 2px solid var(--pc-primary);
  border-radius: var(--pc-radius-btn);
}
.pc-reader__chunk-page {
  position: absolute;
  top: 4px;
  right: 8px;
  font-size: 11px;
  color: var(--pc-text-faint);
}
.pc-reader__foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  border-top: 1px solid var(--pc-line);
}
.pc-reader__foot-page {
  font-size: 12px;
  color: var(--pc-text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.pc-reader__jump {
  width: 52px;
  height: 28px;
  padding: 0 6px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-bg);
  font-family: inherit;
  font-size: 12px;
  color: var(--pc-text);
  outline: none;
}
.pc-reader__jump:focus {
  border-color: var(--pc-primary);
}
.pc-reader__foot-spacer {
  flex: 1;
}
.pc-reader__foot-btn {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-reader__foot-btn:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-reader__foot-btn.is-primary {
  border-color: transparent;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-weight: 600;
}

/* ── 右列：教材来源 ─────────────────────────────────────────────── */
.pc-mat__source {
  display: flex;
  gap: 12px;
}
.pc-mat__source-detail {
  min-width: 0;
  flex: 1;
}
.pc-mat__source-book {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.5;
}
.pc-mat__source-line {
  margin: 0 0 2px;
  font-size: 12px;
  color: var(--pc-text-muted);
  line-height: 1.5;
}
.pc-mat__source-page {
  display: block;
  margin: 2px 0 0;
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}
.pc-mat__quote {
  position: relative;
  margin: 12px 0 0;
  padding: 8px 10px 8px 28px;
  background: var(--pc-primary-softer);
  border-radius: 6px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--pc-text-2);
}
.pc-mat__quote-text {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-mat__quote-mark {
  position: absolute;
  left: 8px;
  top: 0;
  font-size: 22px;
  line-height: 1;
  color: var(--pc-primary);
  opacity: 0.7;
  font-family: Georgia, serif;
}
.pc-mat__actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.pc-mat__locate {
  flex: 1;
  height: 32px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-mat__locate:hover:not(:disabled) {
  background: var(--pc-primary-hover);
}
.pc-mat__locate:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pc-mat__join {
  flex: 1;
  height: 32px;
  border: 1px solid var(--pc-primary);
  border-radius: var(--pc-radius-btn);
  background: transparent;
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-mat__join:hover:not(:disabled) {
  background: var(--pc-primary-softer);
}
.pc-mat__join:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.pc-mat__notice {
  margin: 8px 0 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--pc-text-muted);
}

/* 相关图片缩略 */
.pc-mat__thumbs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-mat__thumb {
  display: block;
  width: 100%;
  padding: 6px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-bg);
  cursor: pointer;
}
.pc-mat__thumb:hover {
  border-color: var(--pc-primary);
}
.pc-mat__thumb :deep(.pc-slice-fig) {
  margin: 0;
}

/* 我的资料 */
.pc-mat__file {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
}
.pc-mat__file-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--pc-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-mat__upload {
  margin-top: 4px;
  padding: 2px;
  border: none;
  background: transparent;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--pc-primary);
  cursor: pointer;
}
.pc-mat__file-input {
  display: none;
}
</style>
