<template>
  <!--
    增量流式 markdown 渲染器（4.1 核心）
    - 按块切分（空行/标题边界，跳过代码围栏与 $$ 段内部）
    - 已完成块按内容哈希缓存 HTML，key 稳定 → DOM 不动；仅尾块随 token 重渲
    - rAF 节流（≥50ms 一帧）；流式时光标附在尾块
    - 历史消息（streaming=false）一次性走同一管线
  -->
  <div ref="rootRef" class="md-body imd" :class="{ zoomable }">
    <div v-for="b in blocks" :key="b.key" class="imd-block" v-html="b.html"></div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { enhanceCodeBlocks, hashStr, renderBlockCached, renderMarkdown, splitMarkdownBlocks } from '@/utils/markdown'
import { openLightbox } from '@/utils/lightbox'

const props = defineProps({
  text: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  /** 图片尺寸模式：question=题干居中大图 / option=选项行内小图 */
  mode: { type: String, default: 'question' },
  /** 点击图片放大（lightbox） */
  zoomable: { type: Boolean, default: true },
})

const rootRef = ref(null)
const blocks = ref([]) // [{key, html, tail}]

const FRAME_MS = 50
let rafId = 0
let timerId = 0
let lastPaint = 0
let disposed = false

/** 全量重算分块（只在帧回调里执行；已完成块命中缓存，开销≈尾块一次 parse） */
function paint() {
  if (disposed) return
  lastPaint = Date.now()
  const src = props.text || ''
  const parts = splitMarkdownBlocks(src)
  const streamingTail = props.streaming
  blocks.value = parts.map((text, i) => {
    const isTail = i === parts.length - 1
    // key = 位置+内容哈希：同位置内容不变则 DOM 稳定（增量核心）；
    // 必须带下标——文档中两段相同文字会产生同哈希，裸哈希会撞 key
    const key = `${i}_${hashStr(text)}`
    let html
    if (isTail && streamingTail) {
      // 尾块：流式容错渲染，不进缓存（内容每帧都在变）
      html = renderMarkdown(text, { streamingTail: true, imgMode: props.mode })
      html += '<span class="imd-cursor"></span>'
    } else {
      html = renderBlockCached(text, { imgMode: props.mode })
    }
    return { key, html, tail: isTail }
  })
  // DOM 补丁落地后做代码高亮/复制按钮
  // 流式中仅当尾块确实含代码围栏时才跳过最后一个 pre（避免每帧重高亮，也防止误跳完成块）
  const tailHasFence = streamingTail && (parts[parts.length - 1] || '').includes('```')
  nextTick(() => {
    if (disposed) return
    enhanceCodeBlocks(rootRef.value, { skipTail: tailHasFence })
  })
}

function schedule() {
  if (disposed) return
  if (rafId || timerId) return
  const wait = Math.max(0, FRAME_MS - (Date.now() - lastPaint))
  if (wait === 0) {
    rafId = requestAnimationFrame(() => { rafId = 0; paint() })
  } else {
    timerId = setTimeout(() => {
      timerId = 0
      rafId = requestAnimationFrame(() => { rafId = 0; paint() })
    }, wait)
  }
}

watch(() => props.text, schedule)
// streaming 结束：立即终渲（尾块进缓存 + 高亮），不防抖
watch(() => props.streaming, (v) => { if (!v) flushNow() })

function flushNow() {
  if (rafId) cancelAnimationFrame(rafId)
  if (timerId) clearTimeout(timerId)
  rafId = 0
  timerId = 0
  paint()
}

/* 图片加载失败占位 + 点击放大（事件委托；DOMPurify 会剥离内联事件） */
const onImgError = (e) => {
  const img = e.target
  if (!(img instanceof HTMLImageElement) || img.dataset.fallback) return
  img.dataset.fallback = '1'
  const wrap = document.createElement('div')
  wrap.className = 'img-fallback'
  wrap.textContent = `🖼 ${img.alt || '图片'}（图片加载失败）`
  img.replaceWith(wrap)
}
const onImgClick = (e) => {
  const img = e.target
  if (!(img instanceof HTMLImageElement) || !props.zoomable) return
  openLightbox(img.currentSrc || img.src)
}

onMounted(() => {
  paint() // 首帧立即渲染（历史消息无延迟）
  const el = rootRef.value
  if (el) {
    el.addEventListener('error', onImgError, true)
    el.addEventListener('click', onImgClick)
  }
})
onBeforeUnmount(() => {
  disposed = true
  if (rafId) cancelAnimationFrame(rafId)
  if (timerId) clearTimeout(timerId)
  const el = rootRef.value
  if (el) {
    el.removeEventListener('error', onImgError, true)
    el.removeEventListener('click', onImgClick)
  }
})
</script>

<style scoped>
.imd-block :deep(img.md-img-question) {
  max-width: min(100%, 420px);
  height: auto;
  display: block;
  margin: 0.5em auto;
  border-radius: 8px;
  cursor: zoom-in;
}
.imd-block :deep(img.md-img-option) {
  max-height: 36px;
  width: auto;
  vertical-align: middle;
  display: inline-block;
  margin: 0 0.3em;
  border-radius: 4px;
}
.imd-block :deep(.img-fallback) {
  display: inline-block;
  padding: 6px 12px;
  border: 1px dashed #d1d5db;
  border-radius: 6px;
  color: #9ca3af;
  font-size: 12px;
  margin: 0.3em 0;
}
/* 流式光标：附在尾块末尾，闪烁 */
.imd-block :deep(.imd-cursor) {
  display: inline-block;
  width: 8px; height: 1.05em;
  margin-left: 2px; vertical-align: text-bottom;
  background: var(--primary);
  border-radius: 1px;
  animation: imd-blink 0.9s steps(2, start) infinite;
}
@keyframes imd-blink { to { visibility: hidden; } }
@media (max-width: 480px) {
  .imd-block :deep(img.md-img-question) { max-width: 100%; }
}
</style>
