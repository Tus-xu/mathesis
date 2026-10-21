<script setup>
/**
 * 老师课件舞台（学生端「双师课堂 · 老师课堂」）。
 *
 * 一份课件、两种视图：直接复用教师端 `SlideCanvas`（980×551 / 16:9）渲染
 * `session.slides[].deck_slide`，禁止在学生端另写一套 PPT 版面（否则教师看到 A、学生看到 B）。
 *
 * 缩放策略：ResizeObserver 测容器宽高 → `deckStageScale` 等比缩放（只缩不放）：
 * - 外层 frame 高度 = 551 × scale，整页始终完整可见、不裁切、不溢出；
 * - 980×551 设计尺寸的画布以 `transform: scale()` 缩放，不影响内部排版重排；
 * - 只引入备课中心 `--pc-*` token，不复制 `.pc-slide` 版式 CSS。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import SlideCanvas from '@/features/teacher-prep-center/ppt/SlideCanvas.vue'
import { DECK_STAGE_HEIGHT, DECK_STAGE_WIDTH, deckStageScale } from '@/utils/dualClassroom'
import '@/features/teacher-prep-center/tokens/prep.css'

const props = defineProps({
  /** 一页老师课件（后端 session.slides[].deck_slide，只读）。 */
  slide: { type: Object, required: true },
  /** 课件标题（页眉/封面课题名）。 */
  deckTitle: { type: String, default: '' },
  /** 教材行（页眉右上 / 封面副标题）。 */
  textbookLine: { type: String, default: '' },
  /** 本页资产 assetId → data URL（后端 asset_urls，学生端免二次鉴权）。 */
  assetUrls: { type: Object, default: () => ({}) },
  /** 当前页序（1 基，标题序号徽标用）。 */
  ordinal: { type: Number, default: 0 },
})

const hostEl = ref(null)
const boxW = ref(0)
const boxH = ref(0)
let ro = null
let resizeHandler = null

const scale = computed(() => deckStageScale(boxW.value, boxH.value))
const stageW = computed(() => Math.round(DECK_STAGE_WIDTH * scale.value))
const stageH = computed(() => Math.round(DECK_STAGE_HEIGHT * scale.value))
const canvasStyle = computed(() => ({
  width: `${DECK_STAGE_WIDTH}px`,
  height: `${DECK_STAGE_HEIGHT}px`,
  transform: `scale(${scale.value})`,
}))

function measure() {
  const el = hostEl.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const parentRect = el.parentElement?.getBoundingClientRect()
  boxW.value = rect.width || el.clientWidth || parentRect?.width || 0
  // 可用高度以舞台容器（父元素）为准：宿主 height:100% 在「父高不确定」时可能被内容撑开，
  // 直接用自身高度会把 551 当成可用高度，导致窄屏溢出。父容器无高度时再回退自身。
  boxH.value = parentRect?.height || rect.height || el.clientHeight || 0
}

onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(measure)
    if (hostEl.value) ro.observe(hostEl.value)
  } else {
    resizeHandler = measure
    window.addEventListener('resize', resizeHandler)
  }
})

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  ro = null
  if (resizeHandler) {
    window.removeEventListener('resize', resizeHandler)
    resizeHandler = null
  }
})
</script>

<template>
  <div ref="hostEl" class="deck-stage" role="region" aria-label="老师课件">
    <div class="deck-stage__frame" :style="{ width: `${stageW}px`, height: `${stageH}px` }">
      <div class="deck-stage__canvas" :style="canvasStyle">
        <SlideCanvas
          :slide="props.slide"
          :deck-title="props.deckTitle"
          :textbook-line="props.textbookLine"
          :ordinal="props.ordinal"
          :asset-urls="props.assetUrls"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.deck-stage {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.deck-stage__frame {
  position: relative;
  flex: none;
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.1);
}
/* 画布按 980×551 排版后用 transform 缩放：内部不重排，版式与教师端完全一致 */
.deck-stage__canvas {
  position: absolute;
  top: 0;
  left: 0;
  transform-origin: top left;
}
/* 外框已提供投影/圆角，去掉画布自带阴影避免叠加发灰 */
.deck-stage__canvas :deep(.pc-slide) {
  box-shadow: none;
  border-radius: 0;
}
</style>
