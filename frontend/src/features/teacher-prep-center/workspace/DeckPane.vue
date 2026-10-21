<script setup lang="ts">
/**
 * PPT 页（黄金图 PPT-GOLDEN 真实实现）：左缩略栏 251px / 中工具条+画布 / 右智能属性 292px。
 *
 * 反空壳红线：
 * - 页数/缩略图/页码 = 真实 deck spec（缩略图由真实 slide 数据微缩派生）；
 * - 工具条：添加页（AI 生成真实内容）、文本/公式（追加真实可编辑元素并落库）、
 *   数学图形（为无图形页创建参数化对象）、图片/备注（无真实能力 → 禁用 + 即将支持）；
 * - 参数/开关/样式 → patch 通道落库（revision+1，刷新还原）；AI 快捷操作/单页修改走真实 LLM；
 * - 保存状态来自服务端持久化时间（deck.updated_at）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  useDeckEditorStore,
  isCoverSlide,
  isTeachingAnnotation,
  sanitizeDeckText,
  stripChapterNo,
  deckCourseTitle,
} from '@/features/teacher-prep-center/stores/deckEditor'
import { stripDeckTitleTail } from '@/features/teacher-prep-center/copy/prepCopy'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import SlideCanvas from '@/features/teacher-prep-center/ppt/SlideCanvas.vue'
import SlideThumb from '@/features/teacher-prep-center/ppt/SlideThumb.vue'
import PropertiesPanel from '@/features/teacher-prep-center/ppt/PropertiesPanel.vue'
import PreviewOverlay from '@/features/teacher-prep-center/ppt/PreviewOverlay.vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import GeneratePanel from '@/features/teacher-prep-center/workspace/GeneratePanel.vue'
import PublishCoursewareDialog from '@/features/teacher-prep-center/publish/PublishCoursewareDialog.vue'
import { useToastStore } from '@/stores/toast'
import type { DeckSlide, SlideElement } from '@/features/teacher-prep-center/api/deckApi'

const store = useDeckEditorStore()
const prep = usePrepSessionStore()
const toast = useToastStore()

/* 发布到班级课堂（不改动导出/保存：发布读当前 deck 快照，编辑状态由 store 自行管理）。 */
const publishOpen = ref(false)
const publishSessionId = computed(() => prep.currentSessionId || '')

/* 工作区快照 → deck 编辑 store（单次翻页/选中态不被覆盖） */
watch(
  () => prep.current?.artifact.teaching_deck ?? null,
  (deck) => {
    if (deck) store.syncFromWorkspace(deck)
  },
  { immediate: true, deep: false },
)
watch(
  () => prep.currentSessionId,
  (id, old) => {
    if (id !== old) store.reset()
  },
)

const deckMissing = computed(() => !store.deckId || store.pageCount === 0)

/* 默认选中（P0-12 前置）：deck 首次载入后优先选中含 math_object 的页（右面板「图形属性」进页即可见），
   否则第一个非封面页。仅在 deckId 变化时执行一次，不覆盖教师手动翻页/编辑后的选中态。 */
const autoSelectedDeckId = ref<string | null>(null)
watch(
  () => [store.deckId, store.pageCount] as const,
  ([deckId, pageCount]) => {
    if (!deckId || !pageCount || autoSelectedDeckId.value === deckId) return
    autoSelectedDeckId.value = deckId
    const withMath = store.slides.findIndex((s) => !!s.math_object)
    if (withMath >= 0) {
      store.selectPage(withMath)
      return
    }
    const firstNonCover = store.slides.findIndex((s) => !isCoverSlide(s))
    if (firstNonCover > 0) store.selectPage(firstNonCover)
  },
  { immediate: true },
)

/* 教材行（P0-11）：唯一权威字段 currentCourseMeta.textbook，`·` 转空格完整展示；
   禁止前端自行 edition+volume 拼接。防御：旧数据若仍含完全重复段（后端 W1.2 去重未覆盖），
   仅剔除重复段（保留首现，零信息损失）。 */
const textbookLine = computed(() => {
  const t = prep.currentCourseMeta.textbook
  if (!t) return ''
  const parts = t.split('·').map((s) => s.trim()).filter(Boolean)
  // 页眉右端空间有限（VISUAL-SPEC-V3 §3-S4.4）：去掉版本年份（`人教A版2019` → `人教A版`），
  // 教材行给足宽度不截半句；其余信息（学段/册次）完整保留。
  return parts
    .filter((p, i) => parts.indexOf(p) === i)
    .map((p) => p.replace(/\d{4}$/, '').trim())
    .filter(Boolean)
    .join(' ')
})

/* 画布页眉课题名（黄金图实证）：呈现**课题名**，章节号不上屏（`3.1.2 椭圆的简单几何性质` → `椭圆的简单几何性质`）。
   优先会话课题名（`session.title`），回退 deck 标题；两者统一走 `deckCourseTitle`（唯一口径）。
   顶栏「章节题」由 PrepCenterShell 独立消费，不受此处影响。 */
const canvasTitle = computed(
  () =>
    stripDeckTitleTail(deckCourseTitle(prep.current?.session.title, store.deckTitle)) ||
    stripDeckTitleTail(stripChapterNo(prep.currentCourseTitle)),
)
/* 缩略图描述：真实文本，剔除 LaTeX 源码段（红线：裸公式不上屏）。
 * 2026-09-23 主编实测缺陷：原实现把 `$...$` 替换成字面量「公式」，正文里出现
 * 「两个定点公式的距离之和等于常数（大于公式）」这类残句 → 改为**整体删除**数学片段
 * 并收掉残留空格/标点前空格（宁可少一个符号，也不要读不通的句子）。 */
function stripTex(raw: string): string {
  return raw
    .replace(/\$[^$]*\$/g, '')
    .replace(/\\[a-zA-Z]+(\{[^}]*\})?/g, '')
    .replace(/[${}]/g, '')
    .replace(/\s{2,}/g, ' ')
    .replace(/\s+([，。、；：）)】」])/g, '$1')
    .replace(/([（(【「])\s+/g, '$1')
    // 数学片段被删后可能只剩空括号（「（大于）」「（）」）→ 整对删除，不留残壳
    .replace(/[（(【「]\s*[）)】」]/g, '')
    // 比较关系词后面的数学被删 → 「（大于）」这类悬空比较 → 连括号一起删
    .replace(/[（(]\s*(?:大于等于|小于等于|不小于|不大于|大于|小于|等于|≥|≤)\s*[）)]/g, '')
    // 中文字之间的残留空格（删除数学后产生）→ 收掉，避免「等于常数 的」这类断句
    .replace(/([\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

/* 渲染层净化（LEAD-BAR 铁律 3 · 反内部泄漏）：唯一实现收口在 stores/deckEditor.ts 的
 * `sanitizeDeckText`，缩略条目与画布标题共用同一净化口径（禁止各组件各写一套）。 */

/** 缩略条目「环节名」：净化后的标题（零标记上屏）。 */
const slideName = (slide: DeckSlide): string =>
  stripDeckTitleTail(stripChapterNo(sanitizeDeckText(slide.title))) || '未命名页'
/** 缩略条目「一句描述」：取自蓝图节点摘要/正文，**不得是标题的复制**；
 *  候选逐一净化后若全部等于标题，则诚实省略（宁缺勿复读）。 */
const slideDesc = (slide: DeckSlide): string => {
  const title = slideName(slide)
  const els = slide.elements ?? []
  const candidates: string[] = [
    slide.teaching_goal || '',
    els.find((e) => e.role === 'definition' && e.text)?.text || '',
    els.find((e) => (e.role === 'bullets' || e.role === 'steps') && (e.items ?? []).length)?.items?.[0] || '',
    (slide.content || '').split('\n').map((s) => s.trim()).filter(Boolean)[0] || '',
  ]
  for (const c of candidates) {
    if (isTeachingAnnotation(c)) continue // 教研批注不是课件内容（VISUAL-SPEC-V3 §3-S4.5）
    const t = stripTex(sanitizeDeckText(c))
    if (t && t !== title) return t.length > 26 ? `${t.slice(0, 26)}…` : t
  }
  return ''
}

const zoom = ref(1)

/* ── 工具条 ─────────────────────────────────────────────────────────── */
const COMING_SOON = '即将支持'
function onAddPage() {
  void store.aiAddPageAfterCurrent()
}
const usesElements = (slide: DeckSlide | null): boolean => !!slide && (slide.elements ?? []).length > 0
function onAddText() {
  const s = store.currentSlide
  if (!s) return
  if (usesElements(s)) void store.appendElement(s.id, { role: 'bullets', items: ['新建文本'] })
  else void store.appendContentLine(s.id, '新建文本')
}
function onAddFormula() {
  const s = store.currentSlide
  if (!s) return
  if (usesElements(s)) void store.appendElement(s.id, { role: 'formula', latex: 'x = 1' })
  else void store.appendContentLine(s.id, '$x = 1$')
}
function onAddMathFigure() {
  const s = store.currentSlide
  if (!s) return
  if (s.math_object) {
    store.selectMath(true)
    return
  }
  void addFigures()
}
async function addFigures() {
  const n = await store.addDeckFigures()
  if (n > 0) {
    toast.success(`已按本课主题补充 ${n} 张真实数学图`)
  } else if (store.figuresError) {
    toast.error(`配图失败：${store.figuresError}`)
  } else {
    toast.info('本课暂无可生成的确定性数学图（支持数列 / 集合 / 函数 / 三角等），未伪造占位图')
  }
}

/* ── 底部条（黄金图：左页数 / 右缩放 + 全屏）──────────────────────────
 * 单页 AI 修改由**唯一** composer（共享 ChatComposer，placeholder `告诉 AI 如何修改当前页…`）承担，
   本页不再内联第二个输入条（VISUAL-SPEC-V3 §5.6 单一 composer 铁律）。 */
function zoomOut() {
  zoom.value = Math.max(0.5, Math.round((zoom.value - 0.1) * 10) / 10)
}
function zoomIn() {
  zoom.value = Math.min(1.5, Math.round((zoom.value + 0.1) * 10) / 10)
}

const noteLines = computed(() => (store.currentSlide?.speaker_note || '').split('\n').filter((s) => s.trim()))

/* ── 画布自适应（P0-R3）───────────────────────────────────────────────
 * 幻灯片底座固定 980×551（16:9，内部版式按此设计）。中间栏可用宽/高不足时整体
 * 等比缩放（禁止溢出到右侧「智能属性」面板之下、禁止被裁切），并在中间栏内水平
 * 垂直居中；宽度上限 1120（VISUAL-SPEC-V3 §3-S4.4）。 */
const SLIDE_W = 980
const SLIDE_H = 551
const SLIDE_MAX_W = 1120
const STAGE_PAD = 48 // 中间栏四周留白（左右各 24px，≥20px）
const MIN_FIT = 0.4

/* 编辑轻引导显隐 ref：必须先于下方 measureFit 的 watch 源声明（TDZ），
   逻辑（onMounted/记忆/关闭）见下方「编辑轻引导」小节。 */
const GUIDE_STORAGE_KEY = 'pc-deck-edit-guide-seen-v1'
const editGuideVisible = ref(false)

const stageEl = ref<HTMLElement | null>(null)
const fitScale = ref(0.85)
let stageRO: ResizeObserver | null = null

/** 实测中间栏可用宽/高（扣掉上方引导/错误/提示条与下方备注区）→ 等比缩放比。 */
function measureFit() {
  const el = stageEl.value
  if (!el) return
  const w = el.clientWidth
  const h = el.clientHeight
  if (w <= 0 || h <= 0) return
  const pick = (sel: string) => (el.querySelector(sel) as HTMLElement | null)?.offsetHeight ?? 0
  const enrichH = pick('.pc-deck__enrich')
  const notesH = pick('.pc-deck__notes')
  const guideH = pick('.pc-deck__guide')
  const saveErrH = pick('.pc-deck__save-error')
  const chrome = enrichH + notesH + guideH + saveErrH + (enrichH || notesH || guideH || saveErrH ? 24 : 0)
  const availW = Math.max(320, w - STAGE_PAD)
  const availH = Math.max(220, h - STAGE_PAD - chrome)
  fitScale.value = Math.max(MIN_FIT, Math.min(Math.min(availW, SLIDE_MAX_W) / SLIDE_W, availH / SLIDE_H))
}

onMounted(() => {
  measureFit()
  if (typeof ResizeObserver !== 'undefined') {
    stageRO = new ResizeObserver(measureFit)
    if (stageEl.value) stageRO.observe(stageEl.value)
  }
})
onBeforeUnmount(() => {
  stageRO?.disconnect()
  stageRO = null
})
/* 提示条/备注区出现或消失、翻页 → 重算（中间栏自身尺寸不变，ResizeObserver 覆盖不到）。 */
watch(
  () => [
    store.currentIndex,
    store.deckId,
    store.deckThin,
    store.enrichBusy,
    store.enrichCounts,
    store.saveState,
    store.pendingPatchCount,
    editGuideVisible.value,
    noteLines.value.length,
  ],
  () => void nextTick(measureFit),
)

const fitW = computed(() => Math.round(SLIDE_W * fitScale.value))
const fitH = computed(() => Math.round(SLIDE_H * fitScale.value))
/** 画布缩放 = 自适应比 × 教师缩放；超出部分被中间栏 overflow 裁住，绝不越界到右面板。 */
const canvasScale = computed(() => fitScale.value * zoom.value)

/* ── 逐页增稠（骨架稀薄 → 真实异步作业；进度只呈现服务端真实阶段 + 真实计时/页数）──
 * G13：已用时计时必须跨 tab 切换持续（watch immediate —— 重新挂载时按
 * `enrichStartedAt`（store 持久）重建 interval 并立即刷新读数，绝不冻结于 0）。 */
const enrichElapsed = ref(0)
let enrichTimer: ReturnType<typeof setInterval> | null = null
watch(
  () => store.enrichBusy,
  (busy) => {
    if (enrichTimer) {
      clearInterval(enrichTimer)
      enrichTimer = null
    }
    if (!busy) {
      enrichElapsed.value = 0
      return
    }
    const tick = () => {
      enrichElapsed.value = store.enrichStartedAt ? Math.floor((Date.now() - store.enrichStartedAt) / 1000) : 0
    }
    tick()
    enrichTimer = setInterval(tick, 1000)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (enrichTimer) clearInterval(enrichTimer)
})

const enrichDoneText = computed(() => {
  const c = store.enrichCounts
  if (!c) return ''
  const seconds = c.totalMs > 0 ? `，耗时 ${Math.round(c.totalMs / 1000)} 秒` : ''
  return `课件内容已充实：${c.enriched} / ${c.pages} 页由 AI 补全${seconds}`
})

function onEnrichClick() {
  void store.startEnrich()
}

function onElementChange(payload: { index: number; next: SlideElement }) {
  const s = store.currentSlide
  if (s) void store.updateElement(s.id, payload.index, payload.next)
}

/* 元素块 hover 操作条（删除 / 同 role 邻居间上移下移 → store 落库）。 */
function onElementRemove(index: number) {
  const s = store.currentSlide
  if (s) void store.removeElement(s.id, index)
}
function onElementMove(index: number, dir: -1 | 1) {
  const s = store.currentSlide
  if (s) void store.moveElement(s.id, index, dir)
}

/* ── 保存失败错误条（失败可见 + 可重试）────────────────────────────────
 * saveState==='error' 时醒目上屏 saveError 原因；「重试」重发暂存修改 —— 队列在
 * flush 时基于最新 store 状态重新推导 operations（绝不重放陈旧快照）；暂存修改
 * 未清空前不提供关闭（避免误关后丢失未保存内容），「放弃修改」为两步确认。 */
const discardArmed = ref(false)
let discardTimer: ReturnType<typeof setTimeout> | null = null

function onRetrySave() {
  disarmDiscard()
  void store.retrySave()
}
function disarmDiscard() {
  discardArmed.value = false
  if (discardTimer) {
    clearTimeout(discardTimer)
    discardTimer = null
  }
}
function onDiscardEdits() {
  if (!discardArmed.value) {
    discardArmed.value = true
    if (discardTimer) clearTimeout(discardTimer)
    discardTimer = setTimeout(disarmDiscard, 4000)
    return
  }
  disarmDiscard()
  void store.discardPendingEdits()
}
watch(
  () => store.saveState,
  (state) => {
    if (state !== 'error') disarmDiscard()
  },
)
function onDismissSaveError() {
  store.dismissSaveError()
}

/* ── 编辑轻引导（一次性）：首次进入课件页提示双击编辑；localStorage 记忆已展示。
   editGuideVisible 声明在画布自适应小节（先于 measureFit 的 watch 源，防 TDZ）。 */
onMounted(() => {
  try {
    editGuideVisible.value = !deckMissing.value && localStorage.getItem(GUIDE_STORAGE_KEY) !== '1'
  } catch {
    editGuideVisible.value = !deckMissing.value // 隐私模式等 localStorage 不可用：仍展示一次
  }
})
watch(deckMissing, (missing) => {
  if (!missing && !editGuideVisible.value) {
    try {
      editGuideVisible.value = localStorage.getItem(GUIDE_STORAGE_KEY) !== '1'
    } catch {
      editGuideVisible.value = true
    }
  }
})
function dismissEditGuide() {
  editGuideVisible.value = false
  try {
    localStorage.setItem(GUIDE_STORAGE_KEY, '1')
  } catch {
    // localStorage 不可用时仅本次会话内隐藏（诚实降级，不假装已永久记忆）
  }
}

function onTitleChange(title: string) {
  const s = store.currentSlide
  if (s) void store.updateSlideTitle(s.id, title)
}

/* ── 页操作（缩略栏悬浮条）：页序调整 / 单页重试 / 删除页（两步确认）─────
 * 全部走 deck patch 通道（move_slide / remove_slide 支持 → 页序可调）；
 * 删除/重试影响真实内容 → 两步确认 / AI 状态可见，不做静默覆盖。 */
const confirmDeleteId = ref<string | null>(null)
let confirmDeleteTimer: ReturnType<typeof setTimeout> | null = null

function onPageUp(i: number) {
  void store.movePage(i, -1)
}
function onPageDown(i: number) {
  void store.movePage(i, 1)
}
function onRegenPage(slideId: string) {
  void store.aiRegeneratePage(slideId)
}
function onDeletePage(i: number) {
  const slide = store.slides[i]
  if (!slide) return
  if (confirmDeleteId.value !== slide.id) {
    confirmDeleteId.value = slide.id
    if (confirmDeleteTimer) clearTimeout(confirmDeleteTimer)
    confirmDeleteTimer = setTimeout(() => {
      confirmDeleteId.value = null
    }, 4000)
    return
  }
  confirmDeleteId.value = null
  void store.removePage(i)
}
/** 增稠失败页单页重试（/regen；已完成页原样保留）。 */
function onRetryFailedPage(slideId: string) {
  void store.retryFailedPage(slideId, '按本页教学目标重新生成本页内容')
}

onBeforeUnmount(() => {
  if (confirmDeleteTimer) clearTimeout(confirmDeleteTimer)
})
</script>

<template>
  <section class="pc-deck">
    <!-- 左：缩略图栏（真实微缩派生） -->
    <aside class="pc-deck__strip">
      <p v-if="deckMissing" class="pc-deck__strip-empty">
        {{ store.deckId ? '本会话课件暂无页面。' : '课件尚未生成：确认教学路径后会自动生成。' }}
      </p>
      <div
        v-for="(slide, i) in store.slides"
        :key="slide.id"
        class="pc-deck__item-wrap"
        :class="{ 'is-active': i === store.currentIndex }"
      >
        <button
          type="button"
          class="pc-deck__item"
          :class="{ 'is-active': i === store.currentIndex }"
          @click="store.selectPage(i)"
        >
          <span class="pc-deck__item-no" :class="{ 'is-active': i === store.currentIndex }">{{ i + 1 }}</span>
          <span class="pc-deck__item-card">
            <span class="pc-deck__item-thumb"><SlideThumb :slide="slide" :deck-title="canvasTitle" :ordinal="store.slideOrdinals[i] || 0" /></span>
            <span class="pc-deck__item-meta">
              <span class="pc-deck__item-title">{{ slideName(slide) }}</span>
              <span v-if="slideDesc(slide)" class="pc-deck__item-desc">{{ slideDesc(slide) }}</span>
            </span>
          </span>
        </button>
        <!-- 页操作悬浮条（真实 patch 通道：move_slide / remove_slide / regen） -->
        <span class="pc-deck__item-ops">
          <button
            type="button"
            class="pc-deck__op"
            data-op="up"
            :disabled="i === 0 || store.patchBusy"
            :title="`第 ${i + 1} 页上移`"
            aria-label="上移本页"
            @click="onPageUp(i)"
          >▲</button>
          <button
            type="button"
            class="pc-deck__op"
            data-op="down"
            :disabled="i >= store.pageCount - 1 || store.patchBusy"
            :title="`第 ${i + 1} 页下移`"
            aria-label="下移本页"
            @click="onPageDown(i)"
          >▼</button>
          <button
            type="button"
            class="pc-deck__op"
            data-op="regen"
            :disabled="store.aiBusy || store.patchBusy"
            :title="`重新生成第 ${i + 1} 页（AI 重写本页内容）`"
            aria-label="重新生成本页"
            @click="onRegenPage(slide.id)"
          >↻</button>
          <button
            type="button"
            class="pc-deck__op pc-deck__op--danger"
            data-op="delete"
            :class="{ 'is-armed': confirmDeleteId === slide.id }"
            :disabled="store.pageCount <= 1 || store.patchBusy"
            :title="confirmDeleteId === slide.id ? '再点一次确认删除本页' : `删除第 ${i + 1} 页`"
            aria-label="删除本页"
            @click="onDeletePage(i)"
          >{{ confirmDeleteId === slide.id ? '确认' : '✕' }}</button>
        </span>
      </div>
    </aside>

    <!-- 中：工具条 + 画布 + 底部条 -->
    <div class="pc-deck__center">
      <div class="pc-deck__toolbar">
        <button type="button" class="pc-deck__tool" :disabled="store.aiBusy || deckMissing" title="AI 生成新页（基于当前环节任务）" @click="onAddPage">
          <span class="pc-deck__tool-glyph">
            <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><circle cx="8" cy="8" r="6.3" fill="none" stroke="currentColor" stroke-width="1.3" /><path d="M8 5.2v5.6M5.2 8h5.6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
          </span>
          添加页
          <span v-if="store.aiBusy && store.aiTask === 'add_page'" class="pc-deck__tool-busy">生成中…</span>
        </button>
        <span class="pc-deck__tool-sep" aria-hidden="true"></span>
        <button type="button" class="pc-deck__tool" :disabled="deckMissing || store.patchBusy" title="追加文本（真实落库）" @click="onAddText">
          <span class="pc-deck__tool-glyph pc-deck__tool-glyph--t">T</span> 文本
        </button>
        <button type="button" class="pc-deck__tool" :disabled="deckMissing || store.patchBusy" title="追加公式（真实落库）" @click="onAddFormula">
          <span class="pc-deck__tool-glyph">Σ</span> 公式
        </button>
        <button type="button" class="pc-deck__tool" :disabled="deckMissing || store.patchBusy || store.figuresBusy" title="按本课主题插入真实数学图形（数列/集合/函数/三角等）" @click="onAddMathFigure">
          <span class="pc-deck__tool-glyph">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 1.6l5.2 3v6.8L8 14.4 2.8 11.4V4.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
          </span>
          数学图形
        </button>
        <button type="button" class="pc-deck__tool" :disabled="deckMissing || store.patchBusy || store.figuresBusy" title="为本课补充真实数学配图（最多 3 张，确定性渲染，不是 AI 生图）" @click="addFigures">
          <span class="pc-deck__tool-glyph">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="2.8" width="12" height="10.4" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3" /><circle cx="6" cy="6.6" r="1.2" fill="currentColor" /><path d="M2.8 12l3.4-3.6 2.4 2.4 2.2-2.6 2.4 3.8" fill="none" stroke="currentColor" stroke-width="1.2" /></svg>
          </span>
          {{ store.figuresBusy ? '配图中…' : '图片' }}
        </button>
        <span class="pc-deck__tool-sep" aria-hidden="true"></span>
        <button type="button" class="pc-deck__tool" disabled :title="COMING_SOON">
          <span class="pc-deck__tool-glyph">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 4h10M3 8h10M3 12h7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
          </span>
          备注
        </button>
        <!-- 发布到班级课堂：读当前 deck 快照 → 8100 → 8000，学生端「课堂」同步可见。
             工具条宽度受 980 画布中线约束，按钮保持紧凑（完整语义见 title / OverviewPane 同名出口）。 -->
        <button
          type="button"
          class="pc-deck__tool pc-deck__tool--publish"
          :disabled="deckMissing || store.patchBusy || !publishSessionId"
          title="发布到班级课堂：学生端 AI 主讲逐页讲解（发布当前课件快照，不改动备课内容）"
          aria-label="发布到班级课堂"
          data-testid="pc-deck-publish"
          @click="publishOpen = true"
        >
          <span class="pc-deck__tool-glyph">
            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 9.4V2.6m0 0L5.6 5m2.4-2.4L10.4 5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 9.2v3.4h10V9.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </span>
          发布课堂
        </button>
      </div>

      <div ref="stageEl" class="pc-deck__stage">
        <!-- 保存失败错误条（失败可见 + 可重试；重发基于最新 store 状态重新推导 operations） -->
        <div v-if="store.saveState === 'error'" class="pc-deck__save-error" role="alert">
          <span class="pc-deck__save-error-badge" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="15" height="15"><path d="M8 1.6 15 14H1L8 1.6Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /><path d="M8 6v4.2M8 12.2v.2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
          </span>
          <span class="pc-deck__save-error-text">
            保存失败：{{ store.saveError || '保存暂时未成功，请点击重试' }}
            <template v-if="store.pendingPatchCount > 0">（另有 {{ store.pendingPatchCount }} 处修改已暂存，重试后会一并保存）</template>
          </span>
          <button type="button" class="pc-deck__save-error-btn" :disabled="store.patchBusy" @click="onRetrySave">
            {{ store.patchBusy ? '重试中…' : '重试' }}
          </button>
          <button
            v-if="store.pendingPatchCount > 0"
            type="button"
            class="pc-deck__save-error-btn pc-deck__save-error-btn--ghost"
            :class="{ 'is-armed': discardArmed }"
            :title="discardArmed ? '再点一次确认：未保存的修改将被丢弃' : '放弃这些未保存的修改，恢复为最近保存的内容'"
            @click="onDiscardEdits"
          >{{ discardArmed ? '确认放弃' : '放弃修改' }}</button>
          <button v-else type="button" class="pc-deck__save-error-x" aria-label="关闭" @click="onDismissSaveError">×</button>
        </div>

        <!-- 编辑轻引导（一次性；localStorage 记忆已展示） -->
        <div v-if="editGuideVisible && !deckMissing" class="pc-deck__guide">
          <span class="pc-deck__guide-text">单击选中图形 · 双击编辑文字与公式</span>
          <button type="button" class="pc-deck__guide-btn" @click="dismissEditGuide">知道了</button>
        </div>

        <!-- 逐页增稠入口 / 真实进度 / 结果（骨架稀薄才出现；已充实则隐藏） -->
        <div v-if="store.enrichError" class="pc-deck__enrich pc-deck__enrich--error">
          <span class="pc-deck__enrich-text">课件充实未成功：{{ store.enrichError }}</span>
          <button type="button" class="pc-deck__enrich-btn" @click="onEnrichClick">重试</button>
          <button type="button" class="pc-deck__enrich-x" aria-label="关闭" @click="store.clearEnrichNotice()">×</button>
        </div>
        <!-- G14 单页重试（/regen）：失败页逐页可重试，已完成页原样保留 -->
        <div v-else-if="!store.enrichBusy && store.enrichFailedPages.length" class="pc-deck__enrich pc-deck__enrich--error">
          <span class="pc-deck__enrich-text">{{ store.enrichFailedPages.length }} 页生成失败，可单页重试（不影响已完成页）</span>
          <button
            v-for="f in store.enrichFailedPages"
            :key="`retry-${f.slideId}`"
            type="button"
            class="pc-deck__enrich-btn"
            :disabled="store.aiBusy"
            @click="onRetryFailedPage(f.slideId)"
          >{{ store.aiBusy ? '重试中…' : `重试第 ${f.index + 1 >= 1 ? f.index + 1 : f.slideId} 页` }}</button>
          <button type="button" class="pc-deck__enrich-x" aria-label="关闭" @click="store.enrichFailedPages = []">×</button>
        </div>
        <div v-else-if="store.enrichBusy" class="pc-deck__enrich pc-deck__enrich--busy">
          <span class="pc-deck__enrich-spinner" aria-hidden="true"></span>
          <span class="pc-deck__enrich-text">{{ store.enrichStage }}</span>
          <span class="pc-deck__enrich-meta">已用时 {{ enrichElapsed }} 秒 · 共 {{ store.pageCount }} 页</span>
          <!-- G13：实时 n/N 只读作业 durable `progress` 事件（服务端每页真实完成发一条） -->
          <span v-if="store.enrichProgress" class="pc-deck__enrich-meta">
            已补全 {{ store.enrichProgress.done }} / {{ store.enrichProgress.total }} 页
          </span>
          <span v-else-if="store.enrichCounts" class="pc-deck__enrich-meta">
            已补全 {{ store.enrichCounts.enriched }} / {{ store.enrichCounts.pages }} 页
          </span>
        </div>
        <div v-else-if="store.deckThin" class="pc-deck__enrich">
          <span class="pc-deck__enrich-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="15" height="15"><path d="M8 1.4 9.7 6 14.3 7.7 9.7 9.4 8 14 6.3 9.4 1.7 7.7 6.3 6z" fill="currentColor" /></svg>
          </span>
          <!-- 反空壳：不承诺未实现的「自动」。此处只陈述真实状态 + 真实入口（点击触发异步作业）。 -->
          <span class="pc-deck__enrich-text">当前课件内容较简，可一键充实为完整板书（约 30-60 秒）</span>
          <button type="button" class="pc-deck__enrich-btn" @click="onEnrichClick">充实课件内容</button>
        </div>
        <div v-else-if="store.enrichCounts" class="pc-deck__enrich pc-deck__enrich--done">
          <span class="pc-deck__enrich-text">{{ enrichDoneText }}</span>
          <button type="button" class="pc-deck__enrich-x" aria-label="关闭" @click="store.clearEnrichNotice()">×</button>
        </div>

        <div v-if="deckMissing" class="pc-deck__stage-empty">
          <template v-if="prep.currentLoading">正在加载会话…</template>
          <!-- G11 空态出路：真实下一步按钮（确认路径→同源投影→逐页充实，全链真实作业） -->
          <GeneratePanel
            v-else
            empty-title="课件尚未生成"
            empty-desc="确认教学路径后，会按它生成课件与教案，内容逐页由 AI 充实。"
            gen-label="生成课件"
          />
        </div>
        <template v-else>
          <div class="pc-deck__canvas-fit" :style="{ width: `${fitW}px`, height: `${fitH}px` }">
            <div class="pc-deck__canvas-wrap" :style="{ transform: `scale(${canvasScale})` }">
              <SlideCanvas
                v-if="store.currentSlide"
                :key="store.currentSlide.id"
                :slide="store.currentSlide"
                :deck-title="canvasTitle"
                :textbook-line="textbookLine"
                :index="store.currentIndex"
                :ordinal="store.slideOrdinals[store.currentIndex] || 0"
                :editable="true"
                :math-selected="store.mathSelected"
                :math-override="store.draftMath"
                @select-math="store.selectMath"
                @content-change="(c: string) => store.currentSlide && store.updateContent(store.currentSlide.id, c)"
                @element-change="onElementChange"
                @element-remove="onElementRemove"
                @element-move="(i: number, d: -1 | 1) => onElementMove(i, d)"
                @title-change="onTitleChange"
              />
            </div>
          </div>

          <!-- 备注/练习区（真实 speaker_note；AI 练习产出追加于此） -->
          <div v-if="noteLines.length" class="pc-deck__notes">
            <span class="pc-deck__notes-title">备注 / 随堂练习</span>
            <ul class="pc-deck__notes-list">
              <li v-for="(n, i) in noteLines" :key="`n-${i}`"><KatexText :text="n" /></li>
            </ul>
          </div>
        </template>
      </div>

      <div class="pc-deck__bottombar">
        <span class="pc-deck__pager">
          <button type="button" class="pc-deck__pager-nav" :disabled="store.currentIndex <= 0" aria-label="上一页" @click="store.selectPage(store.currentIndex - 1)">‹</button>
          <span class="pc-deck__pager-text">幻灯片 {{ store.pageCount ? store.currentIndex + 1 : 0 }} / {{ store.pageCount }}</span>
          <button type="button" class="pc-deck__pager-next" :disabled="store.currentIndex >= store.pageCount - 1" aria-label="下一页" @click="store.selectPage(store.currentIndex + 1)">›</button>
        </span>

        <span class="pc-deck__zoom">
          <button type="button" aria-label="缩小" @click="zoomOut">−</button>
          <span class="pc-deck__zoom-val">{{ Math.round(zoom * 100) }}%</span>
          <button type="button" aria-label="放大" @click="zoomIn">＋</button>
          <button type="button" class="pc-deck__zoom-full" title="预览演示" @click="store.openPreview()">
            <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><rect x="2.2" y="3" width="11.6" height="10" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3" /><path d="M8 3v10" stroke="currentColor" stroke-width="1.3" /></svg>
          </button>
        </span>
      </div>
    </div>

    <!-- 右：智能属性面板 -->
    <PropertiesPanel />

    <!-- 全屏预览演示 -->
    <PreviewOverlay v-if="store.previewOpen" />

    <!-- 发布到班级课堂（真实链路：8100 打包 → 8000 preparing → published 才显示成功） -->
    <PublishCoursewareDialog
      :open="publishOpen"
      :session-id="publishSessionId"
      :deck-id="store.deckId || ''"
      :deck-title="store.deckTitle"
      @close="publishOpen = false"
    />
  </section>
</template>

<style scoped>
.pc-deck {
  position: relative;
  display: flex;
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  height: calc(var(--pc-viewport-h, 100vh) - var(--pc-header-h));
  overflow: hidden;
  /* 黄金图 PPT 页硬边距：左 36px / 右 38px 留白；底部 74px 让位底栏（bbox 对齐黄金图） */
  padding: 0 38px 74px 36px;
  font-family: var(--pc-font);
}

/* 左栏（VISUAL-SPEC-V2 §4.1 硬数字 200px：序号 + 微缩瓦片 + 环节名/描述） */
.pc-deck__strip {
  width: 200px;
  flex: none;
  background: var(--pc-panel);
  border-right: 1px solid var(--pc-line);
  overflow-y: auto;
  padding: 10px 8px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pc-deck__strip-empty { font-size: 12px; color: var(--pc-text-faint); line-height: 1.7; }
/* 页容器（缩略卡 + 悬浮页操作条） */
.pc-deck__item-wrap { position: relative; display: flex; }
.pc-deck__item-wrap > .pc-deck__item { flex: 1; min-width: 0; }
/* 页操作悬浮条（默认隐藏，悬停显现；不侵占缩略图视觉） */
.pc-deck__item-ops {
  position: absolute;
  top: 6px;
  right: 8px;
  display: inline-flex;
  gap: 4px;
  opacity: 0;
  transition: opacity 0.12s ease;
  pointer-events: none;
}
.pc-deck__item-wrap:hover .pc-deck__item-ops,
.pc-deck__item-ops:focus-within {
  opacity: 1;
  pointer-events: auto;
}
.pc-deck__op {
  width: 22px;
  height: 22px;
  border: 1px solid var(--pc-line);
  border-radius: 6px;
  background: #fff;
  color: var(--pc-navy);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  font-family: inherit;
}
.pc-deck__op:hover:not(:disabled) { border-color: var(--pc-primary); color: var(--pc-primary); }
.pc-deck__op:disabled { opacity: 0.45; cursor: not-allowed; }
.pc-deck__op--danger.is-armed {
  width: auto;
  padding: 0 6px;
  border-color: var(--pc-danger);
  background: var(--pc-danger);
  color: #fff;
  font-size: 10px;
}
.pc-deck__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
}
.pc-deck__item-no {
  flex: none;
  width: 14px;
  margin-top: 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--pc-text-muted);
  text-align: center;
}
.pc-deck__item-no.is-active { color: var(--pc-primary); }
.pc-deck__item-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 4px 4px 8px;
  border: 1px solid var(--pc-line);
  border-radius: 12px;
  background: #fff;
}
/* 选中态（黄金图实证）：2px 主蓝描边 + 浅蓝底 */
.pc-deck__item.is-active .pc-deck__item-card {
  border: 2px solid var(--pc-primary);
  background: var(--pc-primary-soft);
  padding: 3px 3px 7px;
}
/* 真实微缩瓦片（黄金图 160×90）：16:9 等比微缩真实页面内容（标题 + 图形 + 文本块轮廓），
   选中态 2px 蓝描边由 .is-active 卡片承载。 */
.pc-deck__item-thumb {
  flex: none;
  display: block;
  width: 100%;
  height: auto;
  aspect-ratio: 16 / 9;
  line-height: 0;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.pc-deck__item-meta { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; padding: 0 4px; }
/* P1-07：标题完整显示（最多 2 行换行），禁止 … 截断（对齐黄金图） */
.pc-deck__item-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-navy);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-deck__item.is-active .pc-deck__item-title { color: var(--pc-primary); }
.pc-deck__item-desc {
  font-size: 11px;
  color: var(--pc-text-faint);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 中栏 */
.pc-deck__center { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--pc-canvas); }
.pc-deck__toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  background: #fff;
  border-bottom: 1px solid var(--pc-line);
}
.pc-deck__tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  color: var(--pc-navy);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-deck__tool:hover:not(:disabled) { background: var(--pc-primary-soft); color: var(--pc-primary); }
.pc-deck__tool:disabled { color: var(--pc-text-faint); cursor: not-allowed; }
.pc-deck__tool-glyph { font-size: 14px; color: inherit; display: inline-flex; align-items: center; }
.pc-deck__tool-glyph--t { font-weight: 700; font-family: Georgia, serif; font-size: 15px; }
.pc-deck__tool-busy { font-size: 11px; color: var(--pc-warning); }
.pc-deck__tool-sep { width: 1px; height: 16px; background: var(--pc-line); margin: 0 8px; }
/* 发布入口：右对齐、主蓝色（与编辑工具区分），紧凑标签避免挤压既有工具条 */
.pc-deck__tool--publish {
  margin-left: auto;
  flex: none;
  padding: 0 10px;
  font-size: 12.5px;
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-deck__tool--publish:hover:not(:disabled) { background: var(--pc-primary-soft); }

.pc-deck__stage {
  flex: 1;
  min-height: 0;
  /* 中间栏即画布可用区：幻灯片必须完整可见（P0-R3），缩放态也被裁在栏内，不压右面板 */
  overflow: hidden;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 22px 24px;
  gap: 12px;
}
.pc-deck__stage-empty { padding: 80px 0; font-size: 14px; color: var(--pc-text-muted); }
/* 自适应占位盒：尺寸 = 缩放后的幻灯片（保证 flex 居中口径正确） */
.pc-deck__canvas-fit { flex: none; position: relative; }
/* 980×551 底座按 fitScale×zoom 等比缩放（origin 左上，与占位盒对齐） */
/* 以中间栏中心为基准：fitScale 时正好铺满占位盒；教师缩放时围绕中心放大，
   超出部分由中间栏 overflow 裁住（绝不压到右面板）。 */
.pc-deck__canvas-wrap {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 980px;
  height: 551px;
  margin: -275.5px 0 0 -490px;
  transform-origin: center center;
}

/* 保存失败错误条（画布上方；醒目红底 + 重试/放弃两步确认；未清空暂存前不提供静默关闭） */
.pc-deck__save-error {
  flex: none;
  width: min(100%, 980px);
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
  border: 1px solid #f3c1c1;
  border-radius: 10px;
  background: #fdeeee;
  color: #b42323;
}
.pc-deck__save-error-badge { flex: none; display: inline-flex; color: var(--pc-danger, #d92d20); }
.pc-deck__save-error-text { font-size: 13px; line-height: 1.55; color: #b42323; word-break: break-word; }
.pc-deck__save-error-btn {
  margin-left: auto;
  flex: none;
  height: 28px;
  padding: 0 14px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-danger, #d92d20);
  color: #fff;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-deck__save-error-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.pc-deck__save-error-btn--ghost {
  margin-left: 0;
  background: #fff;
  border: 1px solid #f3c1c1;
  color: #b42323;
}
.pc-deck__save-error-btn--ghost:hover { border-color: var(--pc-danger, #d92d20); }
.pc-deck__save-error-btn--ghost.is-armed {
  background: var(--pc-danger, #d92d20);
  border-color: var(--pc-danger, #d92d20);
  color: #fff;
}
.pc-deck__save-error-x {
  flex: none;
  margin-left: auto;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #b42323;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.pc-deck__save-error-x:hover { background: rgba(180, 35, 35, 0.08); }

/* 编辑轻引导（一次性；单行浅蓝条，知识后即隐藏） */
.pc-deck__guide {
  flex: none;
  width: min(100%, 980px);
  min-height: 34px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 14px;
  border: 1px solid #dbe6fb;
  border-radius: 10px;
  background: #f2f7ff;
  color: var(--pc-text-2);
}
.pc-deck__guide-text { font-size: 12.5px; }
.pc-deck__guide-btn {
  margin-left: auto;
  flex: none;
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: #fff;
  color: var(--pc-navy);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-deck__guide-btn:hover { border-color: var(--pc-primary); color: var(--pc-primary); }

/* 逐页增稠提示条（画布上方，与画布同宽；浅色底，反空壳：入口/进度/结果均来自真实作业） */
.pc-deck__enrich {
  flex: none;
  width: min(100%, 980px);
  min-height: 38px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
  border: 1px solid #dbe6fb;
  border-radius: 10px;
  background: #f2f7ff;
  color: var(--pc-text-2);
}
.pc-deck__enrich--busy { background: #f2f7ff; border-color: #c7dbfa; }
.pc-deck__enrich--done { background: #f0faf3; border-color: #c7ebd3; }
.pc-deck__enrich--error { background: #fef4f4; border-color: #f6cfcf; }
.pc-deck__enrich-icon { flex: none; display: inline-flex; color: var(--pc-primary); }
.pc-deck__enrich-text { font-size: 13px; color: var(--pc-text-2); }
.pc-deck__enrich-meta { font-size: 12px; color: var(--pc-text-muted); white-space: nowrap; }
.pc-deck__enrich-btn {
  margin-left: auto;
  flex: none;
  height: 28px;
  padding: 0 14px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-deck__enrich-btn:hover { background: var(--pc-primary-hover, #1d4fd8); }
.pc-deck__enrich-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.pc-deck__enrich-x {
  flex: none;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--pc-text-muted);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}
.pc-deck__enrich-x:hover { background: rgba(15, 23, 42, 0.06); }
.pc-deck__enrich-spinner {
  flex: none;
  width: 13px;
  height: 13px;
  border: 2px solid var(--pc-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-deck-spin 0.8s linear infinite;
}

/* 备注练习区 */
.pc-deck__notes {
  flex: none;
  width: min(100%, 980px);
  max-height: 110px;
  overflow: auto;
  padding: 10px 16px;
  background: #fff;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
  box-shadow: var(--pc-shadow-card);
}
.pc-deck__notes-title { font-size: 11.5px; font-weight: 700; color: var(--pc-text-muted); }
.pc-deck__notes-list { list-style: none; margin: 4px 0 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
.pc-deck__notes-list li { font-size: 12.5px; color: var(--pc-text-2); line-height: 1.6; }

/* 底部条（黄金图：横跨整宽，页码在缩略栏下方 / AI 条居中 / 缩放在右栏下方） */
.pc-deck__bottombar {
  position: absolute;
  left: 36px;
  right: 38px;
  bottom: 0;
  height: 74px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 18px;
  background: #fff;
}
.pc-deck__pager {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 3px 6px 3px 10px;
  border-radius: var(--pc-radius-full);
  background: #fff;
  border: 1px solid var(--pc-line);
}
.pc-deck__pager-nav {
  border: none;
  background: transparent;
  color: var(--pc-navy);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 4px;
}
.pc-deck__pager-nav:disabled { color: var(--pc-text-faint); cursor: not-allowed; }
.pc-deck__pager-text { font-size: 13px; color: var(--pc-navy); white-space: nowrap; }
.pc-deck__pager-next {
  border: 1px solid var(--pc-line);
  background: #fff;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: var(--pc-navy);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
}
.pc-deck__pager-next:disabled { color: var(--pc-text-faint); cursor: not-allowed; }

@keyframes pc-deck-spin { to { transform: rotate(360deg); } }
.pc-deck__zoom {
  flex: none;
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.pc-deck__zoom button {
  border: none;
  background: transparent;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: var(--pc-navy);
  cursor: pointer;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.pc-deck__zoom-val {
  font-size: 13px;
  color: var(--pc-navy);
  min-width: 42px;
  text-align: center;
}
.pc-deck__zoom { padding: 3px 6px; border-radius: var(--pc-radius-full); background: #fff; border: 1px solid var(--pc-line); }
.pc-deck__zoom-full { border-left: 1px solid var(--pc-line) !important; border-radius: 0 8px 8px 0 !important; }
</style>
