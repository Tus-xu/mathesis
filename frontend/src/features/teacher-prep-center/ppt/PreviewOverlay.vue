<script setup lang="ts">
/**
 * 预览演示（R4 · 真实实现）：全屏遍历当前 deck 的真实渲染页（与画布同一渲染器）。
 *
 * 2026-10-20：新增「分步演示」——推导页的 steps 元素按真实顺序逐步揭示，
 * 支持自动播放 / 暂停 / 重播 / 键盘逐步推进。这是预览层动画，不伪造后端动画字段；
 * 导出 PPTX 仍为静态全显示（UI 明示边界）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import SlideCanvas from './SlideCanvas.vue'
import { useDeckEditorStore, deckCourseTitle } from '@/features/teacher-prep-center/stores/deckEditor'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'

const store = useDeckEditorStore()
const prep = usePrepSessionStore()

const slide = computed(() => store.currentSlide)
/** 与画布同口径的课题名（章节号不上屏）。 */
const courseTitle = computed(() => deckCourseTitle(prep.current?.session.title, store.deckTitle))
const textbookLine = computed(() => {
  const t = prep.currentCourseMeta.textbook
  if (!t) return ''
  const parts = t.split('·').map((s) => s.trim()).filter(Boolean)
  return parts.length > 2 ? parts.slice(0, 2).join(' ') : parts.join(' ')
})

/** 可逐步揭示的 step 总数（只统计 steps 元素里的非空条目）。 */
const totalSteps = computed(() => {
  const els = (slide.value?.elements ?? []) as { role?: string; items?: unknown[] }[]
  return els
    .filter((e) => e.role === 'steps')
    .reduce((n, e) => n + (Array.isArray(e.items) ? e.items.filter((x) => String(x || '').trim()).length : 0), 0)
})

/** null = 整页静态；数字 = 当前已揭示的步骤数。 */
const reveal = ref<number | null>(null)
const playing = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}
function pause() {
  playing.value = false
  stopTimer()
}
function play() {
  if (!totalSteps.value) return
  if (reveal.value === null || reveal.value >= totalSteps.value) reveal.value = 0
  playing.value = true
  stopTimer()
  timer = setInterval(() => {
    if (reveal.value === null) return
    if (reveal.value < totalSteps.value) {
      reveal.value += 1
    } else {
      pause()
    }
  }, 1100)
}
function togglePlay() {
  if (playing.value) pause()
  else play()
}
function restart() {
  reveal.value = 0
  play()
}

function prev() {
  if (reveal.value !== null && reveal.value > 0) {
    reveal.value -= 1
    return
  }
  pause()
  store.selectPage(store.currentIndex - 1)
}
function next() {
  // 有步骤且尚未揭示完 → 逐步推进；否则翻下一页
  if (reveal.value !== null && totalSteps.value && reveal.value < totalSteps.value) {
    reveal.value += 1
    return
  }
  pause()
  store.selectPage(store.currentIndex + 1)
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    pause()
    store.closePreview()
  } else if (e.key === 'ArrowLeft') {
    prev()
  } else if (e.key === 'ArrowRight' || e.key === ' ') {
    e.preventDefault()
    next()
  }
}

watch(
  () => store.currentIndex,
  () => {
    pause()
    reveal.value = store.previewDemo && totalSteps.value ? 0 : null
    if (store.previewDemo && totalSteps.value) play()
  },
)

watch(totalSteps, (n) => {
  if (!n) {
    reveal.value = null
    pause()
  }
})

onMounted(() => {
  window.addEventListener('keydown', onKey)
  if (store.previewDemo && totalSteps.value) {
    reveal.value = 0
    play()
  }
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  stopTimer()
})
</script>

<template>
  <Teleport to="body">
    <div class="pc-preview" role="dialog" aria-label="课件预览演示" @click.self="store.closePreview()">
      <div class="pc-preview__stage">
        <SlideCanvas
          v-if="slide"
          :slide="slide"
          :deck-title="courseTitle"
          :textbook-line="textbookLine"
          :index="store.currentIndex"
          :ordinal="store.slideOrdinals[store.currentIndex] || 0"
          :reveal="reveal"
        />
      </div>
      <button type="button" class="pc-preview__close" title="退出预览 (Esc)" @click="store.closePreview()">×</button>
      <button type="button" class="pc-preview__nav pc-preview__nav--prev" :disabled="store.currentIndex <= 0 && !reveal" title="上一步 / 上一页" @click="prev">‹</button>
      <button type="button" class="pc-preview__nav pc-preview__nav--next" :disabled="store.currentIndex >= store.pageCount - 1 && (!reveal || reveal >= totalSteps)" title="下一步 / 下一页" @click="next">›</button>

      <div v-if="totalSteps" class="pc-preview__demo" data-testid="pc-preview-demo">
        <button type="button" class="pc-preview__demo-btn" @click="togglePlay">
          {{ playing ? '⏸ 暂停' : '▶ 逐步演示' }}
        </button>
        <button type="button" class="pc-preview__demo-btn" @click="restart">↺ 重播</button>
        <span class="pc-preview__demo-text">
          {{ reveal === null ? '静态整页' : `第 ${Math.min(reveal, totalSteps)} / ${totalSteps} 步` }}
          · 空格逐步 · ←→ 翻页
        </span>
      </div>

      <div class="pc-preview__counter">{{ store.currentIndex + 1 }} / {{ store.pageCount }}</div>
    </div>
  </Teleport>
</template>

<style scoped>
.pc-preview {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: #0f172a;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pc-preview__stage {
  transform: scale(min(1, calc((100vw - 120px) / 980)));
  transform-origin: center;
}
.pc-preview__close {
  position: absolute;
  top: 18px;
  right: 24px;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 22px;
  cursor: pointer;
}
.pc-preview__nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 44px;
  height: 64px;
  border: none;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 26px;
  cursor: pointer;
}
.pc-preview__nav:disabled { opacity: 0.3; cursor: not-allowed; }
.pc-preview__nav--prev { left: 26px; }
.pc-preview__nav--next { right: 26px; }
.pc-preview__counter {
  position: absolute;
  bottom: 22px;
  right: 28px;
  color: rgba(255, 255, 255, 0.75);
  font-size: 13px;
  font-family: var(--pc-font);
}
.pc-preview__demo {
  position: absolute;
  left: 28px;
  bottom: 18px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(6px);
}
.pc-preview__demo-btn {
  border: none;
  border-radius: 999px;
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.pc-preview__demo-btn:hover { background: rgba(255, 255, 255, 0.26); }
.pc-preview__demo-text {
  color: rgba(255, 255, 255, 0.72);
  font-size: 11.5px;
  padding-right: 4px;
}
</style>
