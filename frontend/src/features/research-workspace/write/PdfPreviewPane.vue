<template>
  <section class="rw-preview">
    <!-- 对齐占位：黄金图预览栏顶部与编辑器「文件 Tab 条」同高（y119~155 为空浅底），
         使预览工具条与编辑器格式条落在**同一行**（y156~202）。 -->
    <div class="rw-preview__gutter" aria-hidden="true" />
    <!-- 工具条：‹ [1] / N › ｜ − 100% + ｜ 适宽 适页 ↗（黄金图 `WRITE-GOLDEN` 实测 h≈46） -->
    <div class="rw-preview__bar">
      <button type="button" class="rw-preview__btn rw-preview__btn--lead" :disabled="!docReady" aria-label="适应宽度" title="适应宽度" @click="fitWidth">
        <ResizeOutline aria-hidden="true" />
      </button>
      <button type="button" class="rw-preview__btn" :disabled="!pageCount || pageNum <= 1" aria-label="上一页" @click="go(-1)">
        <ChevronBackOutline aria-hidden="true" />
      </button>
      <span class="rw-preview__page">
        <input
          v-model.number="pageInput"
          class="rw-preview__page-input"
          inputmode="numeric"
          aria-label="页码"
          :disabled="!pageCount"
          @change="commitPageInput"
          @keydown.enter.prevent="commitPageInput"
        />
        <span class="rw-preview__page-total">/ {{ pageCount || '–' }}</span>
      </span>
      <button type="button" class="rw-preview__btn" :disabled="!pageCount || pageNum >= pageCount" aria-label="下一页" @click="go(1)">
        <ChevronForwardOutline aria-hidden="true" />
      </button>

      <span class="rw-preview__sep" aria-hidden="true" />

      <button type="button" class="rw-preview__btn" :disabled="!docReady" aria-label="缩小" @click="zoomBy(-10)">
        <RemoveOutline aria-hidden="true" />
      </button>
      <button type="button" class="rw-preview__zoom" :disabled="!docReady" aria-label="重置缩放" @click="resetZoom">
        {{ zoomPercent }}%
      </button>
      <button type="button" class="rw-preview__btn" :disabled="!docReady" aria-label="放大" @click="zoomBy(10)">
        <AddOutline aria-hidden="true" />
      </button>

      <span class="rw-preview__sep" aria-hidden="true" />

      <button type="button" class="rw-preview__btn rw-preview__btn--soft" :disabled="!docReady" aria-label="适应页面" title="适应页面" @click="fitPage">
        <ContractOutline aria-hidden="true" />
      </button>
      <button type="button" class="rw-preview__btn rw-preview__btn--soft" :disabled="!docReady" aria-label="在新标签打开" title="在新标签打开 PDF" @click="openExternal">
        <OpenOutline aria-hidden="true" />
      </button>
    </div>

    <!-- 阅读区 -->
    <div ref="scrollRef" class="rw-preview__stage">
      <div v-if="loading" class="rw-preview__pad"><RegionSkeleton :rows="6" label="正在渲染 PDF" /></div>

      <div v-else-if="error" class="rw-preview__pad rw-preview__pad--center">
        <EmptyState
          :title="error"
          description="请检查编译日志后重新编译；编译失败时会继续显示上一次成功的产物。"
          :icon="DocumentOutline"
        />
      </div>

      <div v-else-if="!runId" class="rw-preview__pad rw-preview__pad--center">
        <EmptyState
          title="暂无编译产物"
          description="点击顶栏「重新编译」后将在此显示真实 PDF 预览。"
          :icon="DocumentOutline"
        />
      </div>

      <div v-else class="rw-preview__canvas-wrap">
        <canvas ref="canvasRef" class="rw-preview__canvas" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * PDF 预览面板（宽 462，VISUAL-SPEC §5.3 / 宪法 §22/§23）。
 *
 * - pdfjs 真实渲染编译产物（`GET /compile-runs/{run_id}/pdf` → `getDocument({data})`）；
 * - 工具条 `‹ [1] / N › ｜ − 100% + ｜ 适宽 适页 ↗`；
 * - 深色纸张底 `--rw-page-bg`，纸张白底居中；
 * - **编译失败不清空**：`runId` 由父级传入「最近一次成功 run」，失败时保持一致（宪法 §23）；
 * - 区域级 loading（`RegionSkeleton`），**禁止整页 spinner**（§6）。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  AddOutline,
  ChevronBackOutline,
  ChevronForwardOutline,
  ContractOutline,
  DocumentOutline,
  OpenOutline,
  RemoveOutline,
  ResizeOutline,
} from '@vicons/ionicons5'
import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import EmptyState from '../ui/EmptyState.vue'
import RegionSkeleton from '../ui/RegionSkeleton.vue'
import { downloadRunPdf } from '../api/compile'
// 与 READ 侧共用同一「单 canvas 渲染控制器」：真取消在途 RenderTask + 代际作废过期结果。
// （WRITE 预览曾只做 `renderToken` 代际、**未** cancel 上一个 RenderTask，密集触发时仍会撞
//   pdf.js 的 `Cannot use the same canvas during multiple render() operations.`）
import { isRenderCancelled, PdfRenderController } from '../read/usePdfDocument'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

/** pdfjs CMap 静态基址（对应 `public/pdfjs-cmaps/`；应用部署在根路径）。 */
const CMAP_BASE_URL = '/'

const props = withDefaults(
  defineProps<{
    projectId: string
    /** 要渲染的 run id（父级传「最近成功 run」；失败时不变，保证不清空） */
    runId?: string | null
  }>(),
  { runId: null },
)

const scrollRef = ref<HTMLElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)

const loading = ref(false)
const error = ref<string | null>(null)
const pageCount = ref(0)
const pageNum = ref(1)
const pageInput = ref(1)
const scale = ref(1)
const zoomPercent = ref(100)
const docReady = ref(false)

// pdfjs 文档/页对象不进入响应式代理（避免私有字段被 Proxy 干扰）
let pdfDoc: import('pdfjs-dist').PDFDocumentProxy | null = null
let pdfBytes: Uint8Array | null = null
// 同一 canvas 的渲染控制器：串行化渲染 + 同步取消在途 RenderTask + 代际作废过期结果。
// 覆盖 WRITE 侧真实触发序列：编译完成后刷新预览 / 连续快速「重新编译」 / 切换文件重渲染 / resize。
const renderer = new PdfRenderController()
// 快速切换 runId 时作废旧加载，避免过期文档后到覆盖最新状态（串台）。
let loadToken = 0

const DPR = () => Math.min(window.devicePixelRatio || 1, 2)

async function loadDoc(runId: string) {
  const token = ++loadToken
  loading.value = true
  error.value = null
  docReady.value = false
  pageCount.value = 0
  pageNum.value = 1
  pageInput.value = 1
  try {
    const bytes = await downloadRunPdf(props.projectId, runId)
    if (token !== loadToken) return
    pdfBytes = bytes
    // 复用已验证接入：fetch → Uint8Array → getDocument({ data })（禁 url，避 worker 私有字段问题）。
    // ⚠️ CJK 必配 CMap：XeLaTeX(ctex) 产出的中文是 CIDFontType0C（FandolSong，Encoding=/Identity-H，
    //    无 ToUnicode），pdfjs 需要 `Adobe-GB1-UCS2` 等 CMap 才能 translateFont；缺失时**整段中文静默丢失**
    //    （控制台报 “Ensure that the cMapUrl and cMapPacked API parameters are provided.”）。
    //    CMap 由 `public/pdfjs-cmaps/` 静态提供（构建后同路径可访问）。
    const doc = await pdfjsLib.getDocument({
      data: bytes.slice(),
      cMapUrl: `${CMAP_BASE_URL}pdfjs-cmaps/`,
      cMapPacked: true,
    }).promise
    if (token !== loadToken) {
      // 过期加载：销毁刚落地的文档，避免内存泄漏与串台。
      void doc.destroy()
      return
    }
    pdfDoc = doc
    pageCount.value = doc.numPages
    pageNum.value = 1
    pageInput.value = 1
    docReady.value = true
  } catch (e) {
    if (token !== loadToken) return
    const status = (e as { status?: number })?.status
    error.value = status === 404 ? '该次编译没有可用的 PDF' : '编译产物暂时无法加载'
    pdfDoc = null
    pdfBytes = null
  } finally {
    // 过期加载不再接管 UI 状态（由最新一次 loadDoc 负责）。
    if (token !== loadToken) return
    // ⚠️ 关键顺序：`<canvas>` 只在 `v-else`（非 loading/error/empty）分支挂载。
    // 若在 `loading=true` 期间调用 renderPage()，`canvasRef.value` 仍为 null → 直接 return，
    // canvas 停留在默认 300×150 且**永远空白**（曾导致预览区白框）。
    // 因此必须先关闭 loading、等 DOM 更新挂载 canvas，再渲染。
    loading.value = false
    await nextTick()
    if (docReady.value && canvasRef.value) await renderPage()
  }
}

async function renderPage() {
  const doc = pdfDoc
  const canvas = canvasRef.value
  if (!doc || !canvas) return
  // 开启新一代际：`begin()` 会**同步** cancel 上一个在途 RenderTask，故同一 canvas 绝不并发 render。
  const gen = renderer.begin()
  try {
    const page = await doc.getPage(pageNum.value)
    if (!renderer.isValid(gen)) return
    await renderer.renderCanvas(page, canvas, scale.value, DPR(), gen)
  } catch (e) {
    // 取消属正常控制流（快速翻页 / 缩放 / 重新编译 / resize），绝不上报 UI。
    if (!renderer.isValid(gen) || isRenderCancelled(e)) return
    error.value = '编译产物暂时无法加载'
  }
}

function go(delta: number) {
  const next = Math.min(Math.max(1, pageNum.value + delta), pageCount.value || 1)
  if (next === pageNum.value) return
  pageNum.value = next
  pageInput.value = next
  void renderPage()
}

function commitPageInput() {
  const n = Number(pageInput.value)
  if (!Number.isFinite(n)) {
    pageInput.value = pageNum.value
    return
  }
  const next = Math.min(Math.max(1, Math.round(n)), pageCount.value || 1)
  pageNum.value = next
  pageInput.value = next
  void renderPage()
}

function applyZoom(next: number) {
  const clamped = Math.min(300, Math.max(30, Math.round(next)))
  scale.value = clamped / 100
  zoomPercent.value = clamped
  void renderPage()
}
function zoomBy(delta: number) {
  applyZoom(zoomPercent.value + delta)
}
function resetZoom() {
  applyZoom(100)
}

async function fitWidth() {
  if (!pdfDoc || !scrollRef.value) return
  const page = await pdfDoc.getPage(pageNum.value)
  const base = page.getViewport({ scale: 1 }).width
  const avail = scrollRef.value.clientWidth - 32
  applyZoom((avail / base) * 100)
}
async function fitPage() {
  if (!pdfDoc || !scrollRef.value) return
  const page = await pdfDoc.getPage(pageNum.value)
  const vp = page.getViewport({ scale: 1 })
  const availW = scrollRef.value.clientWidth - 32
  const availH = scrollRef.value.clientHeight - 32
  applyZoom(Math.min(availW / vp.width, availH / vp.height) * 100)
}

function openExternal() {
  if (!pdfBytes) return
  const blob = new Blob([pdfBytes.slice()], { type: 'application/pdf' })
  const url = URL.createObjectURL(blob)
  window.open(url, '_blank', 'noopener')
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** 释放当前文档与在途渲染（切换 runId / 卸载时调用），避免在途 render 撞上已销毁文档。 */
function destroyDoc() {
  renderer.cancel()
  const doc = pdfDoc
  pdfDoc = null
  pdfBytes = null
  docReady.value = false
  if (doc) void doc.destroy()
}

watch(
  () => props.runId,
  (runId) => {
    if (runId) void loadDoc(runId)
    else destroyDoc()
  },
  { immediate: true },
)

/** 兜底：canvas 因分支切换而重新挂载时补渲染（防止再次出现空白画布）。 */
watch(canvasRef, (el) => {
  if (el && docReady.value && !loading.value) void renderPage()
})

onBeforeUnmount(() => {
  loadToken++
  renderer.dispose()
  void pdfDoc?.destroy()
  pdfDoc = null
})
</script>

<style scoped>
.rw-preview {
  display: flex;
  flex-direction: column;
  /* 必须撑满 `.rw-pane--preview`（flex 列容器的主轴）：否则本组件按内容收缩，
     预览舞台只剩工具条 + 一张小纸，下方留出大片 `--rw-page-bg` 空白。 */
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--rw-page-bg);
}

/* 顶部对齐占位（37px = 编辑器文件 Tab 条高，黄金图预览栏该带为纯 `--rw-page-bg` 空白） */
.rw-preview__gutter {
  height: 37px;
  flex: 0 0 auto;
  background: var(--rw-page-bg);
}

/* 工具条（黄金图 `WRITE-GOLDEN` 实测：白底条带 y156~202，h≈46，内容带 y170~187）
 * 分组：`[页宽] ｜ ‹ [页] / N › ｜ − [N%] + ｜ 适页 ↗`
 * 左内边距 30px：黄金图首个图标内容起于 x=940，预览栏原点 x=896（中间为编辑器右缘/把手留白）。 */
.rw-preview__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 46px;
  padding: 0 12px 0 30px;
  flex: 0 0 auto;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-preview__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-md);
  background: transparent;
  color: var(--rw-text);
  cursor: pointer;
}
.rw-preview__btn:hover:not(:disabled) {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
.rw-preview__btn:disabled {
  opacity: .32;
  cursor: not-allowed;
}
.rw-preview__btn :deep(svg) {
  width: 18px;
  height: 18px;
}
/* 首尾图标常态浅底（黄金图实测：页宽 / 适页 / 外开 三枚带浅底） */
.rw-preview__btn--lead,
.rw-preview__btn--soft {
  background: var(--rw-inset);
}
.rw-preview__page {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.rw-preview__page-input {
  width: 46px;
  height: 32px;
  padding: 0 4px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-medium);
  text-align: center;
  outline: none;
}
.rw-preview__page-input:focus {
  border-color: var(--rw-primary);
}
.rw-preview__page-total {
  font-size: var(--rw-fs-h2);
  color: var(--rw-text);
  white-space: nowrap;
}
.rw-preview__sep {
  width: 1px;
  height: 20px;
  margin: 0 10px;
  flex: 0 0 auto;
  background: var(--rw-border);
}
/* 缩放值：黄金图中 `100%` 为带描边圆角盒（与页码输入同族） */
.rw-preview__zoom {
  min-width: 62px;
  height: 32px;
  padding: 0 8px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-medium);
  cursor: pointer;
}
.rw-preview__zoom:hover:not(:disabled) {
  border-color: var(--rw-primary);
  color: var(--rw-primary);
}
.rw-preview__zoom:disabled {
  opacity: .32;
  cursor: not-allowed;
}

/* 阅读区 */
.rw-preview__stage {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 12px 0;
}
.rw-preview__pad {
  width: 100%;
}
.rw-preview__pad--center {
  display: flex;
  align-items: center;
  justify-content: center;
}
.rw-preview__canvas-wrap {
  align-self: flex-start;
  margin: 0 auto;
  background: var(--rw-surface);
  box-shadow: var(--rw-shadow-card);
}
.rw-preview__canvas {
  display: block;
}
</style>
