<script setup lang="ts">
/**
 * 缩略图 · 真实 16:9 等比微缩（黄金图 PPT-GOLDEN 左缩略栏）。
 *
 * 设计取舍（对齐黄金图）：**不再**用"定尺重排的小字瓦片"（会出现拉长方块 + 文字堆叠 +
 * 页码/课名小字重复），而是把**真实 SlideCanvas** 放进 980×551 底座，
 * 用 CSS `transform: scale()` 等比缩放（`transform-origin: top left`），容器 `overflow: hidden`。
 * 因为与画布 100% 同源，微缩里呈现的图形/公式/定义带与画布逐像素一致。
 *
 * - `mini` 模式隐藏页眉/页脚（黄金图微缩是干净的，不出现"3.1.1 椭圆的标准方程 / 苏教版…"小字堆叠）；
 * - 容器宽度由 ResizeObserver 实测后换算缩放比，缩放与容器解耦（无写死分辨率）；
 * - `pointer-events: none`：微缩纯展示，点击由外层缩略条目承载。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import SlideCanvas from './SlideCanvas.vue'
import type { DeckSlide } from '@/features/teacher-prep-center/api/deckApi'

const props = withDefaults(
  defineProps<{
    slide: DeckSlide
    /** 页眉课题名（与画布同源，由 DeckPane 传入）。 */
    deckTitle?: string
    /** 画布页眉右上教材行（与画布同源）。 */
    textbookLine?: string
    /** 非封面页序（1 基；封面为 0）——与画布序号徽标同源。 */
    ordinal?: number
  }>(),
  { deckTitle: '', textbookLine: '', ordinal: 0 },
)

/** 与 SlideCanvas 底座严格同尺寸（16:9）。 */
const BASE_W = 980
const BASE_H = 551

const box = ref<HTMLElement | null>(null)
const scale = ref(0.18)
let ro: ResizeObserver | null = null

/** 容器实测宽度 → 等比缩放比（宽度为 0 时保持上一次值，避免闪断）。 */
function measure() {
  const w = box.value?.clientWidth ?? 0
  if (w > 0) scale.value = w / BASE_W
}

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(measure)
    if (box.value) ro.observe(box.value)
  }
})
onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
})
</script>

<template>
  <div ref="box" class="pc-thumb" aria-hidden="true">
    <div class="pc-thumb__scaler" :style="{ transform: `scale(${scale})` }">
      <SlideCanvas
        :slide="props.slide"
        :deck-title="props.deckTitle"
        :textbook-line="props.textbookLine"
        :ordinal="props.ordinal"
        mini
      />
    </div>
  </div>
</template>

<style scoped>
.pc-thumb {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #fff;
  border-radius: inherit;
}
/* 980×551 底座按容器宽度等比缩放（origin: 左上角，与容器左/上对齐）。 */
.pc-thumb__scaler {
  width: 980px;
  height: 551px;
  transform-origin: top left;
  pointer-events: none;
  user-select: none;
}
</style>
