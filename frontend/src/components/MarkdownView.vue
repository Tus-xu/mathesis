<template>
  <!-- DOMPurify 消毒后渲染；streaming 期间 KaTeX 容错，结束后由父组件把 streaming 置 false 触发严格重渲染 -->
  <div ref="rootRef" class="md-body" v-html="html" :class="{ zoomable }"></div>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { enhanceCodeBlocks, renderMarkdown } from '@/utils/markdown'
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

const html = computed(() =>
  renderMarkdown(props.text || '', { streamingTail: props.streaming, imgMode: props.mode })
)

// 渲染后增强：代码高亮 + 复制按钮（v-html 更新后需等 DOM 落地）
watch(html, () => nextTick(() => enhanceCodeBlocks(rootRef.value)))

// 图片加载失败占位 + 点击放大（事件委托；DOMPurify 会剥离内联事件）
const onImgError = (e) => {
  const img = e.target
  if (!(img instanceof HTMLImageElement) || img.dataset.fallback) return
  img.dataset.fallback = '1'
  const wrap = document.createElement('div')
  wrap.className = 'img-fallback'
  wrap.textContent = `🖼 ${img.alt || '图片'}（图片加载失败或已过期）`
  img.replaceWith(wrap)
}
const onImgClick = (e) => {
  const img = e.target
  if (!(img instanceof HTMLImageElement) || !props.zoomable) return
  openLightbox(img.currentSrc || img.src)
}

onMounted(() => {
  const el = rootRef.value // 容器级事件委托（修正：不得用 document.querySelector 全局选首个 .md-body）
  if (el) {
    el.addEventListener('error', onImgError, true)
    el.addEventListener('click', onImgClick)
  }
  enhanceCodeBlocks(el)
})
onBeforeUnmount(() => {
  const el = rootRef.value
  if (el) {
    el.removeEventListener('error', onImgError, true)
    el.removeEventListener('click', onImgClick)
  }
})
</script>

<style scoped>
.md-body :deep(img.md-img-question) {
  max-width: min(100%, 420px);
  height: auto;
  display: block;
  margin: 0.5em auto;
  border-radius: 8px;
  cursor: zoom-in;
}
.md-body :deep(img.md-img-option) {
  max-height: 36px;
  width: auto;
  vertical-align: middle;
  display: inline-block;
  margin: 0 0.3em;
  border-radius: 4px;
}
.md-body :deep(.img-fallback) {
  display: inline-block;
  padding: 6px 12px;
  border: 1px dashed #d1d5db;
  border-radius: 6px;
  color: #9ca3af;
  font-size: 12px;
  margin: 0.3em 0;
}
@media (max-width: 480px) {
  .md-body :deep(img.md-img-question) { max-width: 100%; }
}
</style>
