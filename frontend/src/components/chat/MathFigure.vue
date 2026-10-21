<template>
  <!-- F13 可视化讲解图形卡：figure 事件渲染
       契约：{step_no?, caption?, frames:[{data_uri, label}], figure_params?}
       帧渐进揭示（流结束自动播放）+ 手动帧圆点导航/重播（对标 manim-slides 可逆步骤）。
       单帧退化为静态图；加载失败显示兜底 + 调试参数，绝不白屏。 -->
  <div class="math-figure">
    <div v-if="questionFigure || figure.caption || figure.step_no" class="mf-head">
      <span v-if="questionFigure" class="mf-badge mf-badge--question">题目配图</span>
      <span v-else-if="figure.step_no" class="mf-badge">步骤 {{ figure.step_no }}</span>
      <span v-if="!questionFigure && figure.caption" class="mf-caption">{{ figure.caption }}</span>
      <button
        v-if="interactiveAvailable && !showInteractive"
        class="mf-replay"
        style="margin-left:auto;"
        @click="showInteractive = true"
      >🖐 可拖动</button>
      <button
        v-if="interactiveAvailable && showInteractive"
        class="mf-replay"
        style="margin-left:auto;"
        @click="showInteractive = false"
      >看静态帧</button>
      <button
        v-if="!questionFigure && frames.length > 1 && !showInteractive"
        class="mf-replay"
        style="margin-left:auto;"
        @click="replay"
      >▶ 演示</button>
      <span
        v-if="figure.problem_source_sha256"
        class="mf-src-badge"
        title="本图与原题图 SHA-256 绑定（N3 图形合同）"
      >已核对原题图</span>
    </div>

    <MathFigure3D
      v-if="showInteractive && solidScene"
      :figure="solidScene"
      :caption="figure.caption || ''"
      :height="300"
    />
    <FigureConicInteractive
      v-else-if="showInteractive && conicParams"
      :params="conicParams"
    />
    <template v-else>
    <div class="mf-stage">
      <Transition name="mf-fade" mode="out-in">
        <img
          v-if="frames.length && !imgFailed"
          :key="frameIndex"
          :src="frames[frameIndex].data_uri"
          class="mf-img"
          :alt="frames[frameIndex].label || '数学图形'"
          loading="lazy"
          @error="imgFailed = true"
        />
        <div v-else class="mf-fallback">
          <span>{{ imgFailed ? '图形加载失败' : '图形加载中…' }}</span>
          <details v-if="figure.figure_params" class="mf-debug">
            <summary>渲染参数（调试）</summary>
            <pre>{{ prettyParams }}</pre>
          </details>
        </div>
      </Transition>
    </div>
    </template>

    <div v-if="!questionFigure && !showInteractive && frames.length > 1" class="mf-controls">
      <div class="mf-dots" role="tablist">
        <button
          v-for="(f, i) in frames"
          :key="i"
          class="mf-dot"
          :class="{ on: i === frameIndex }"
          :title="f.label"
          :aria-label="f.label"
          @click="jumpTo(i)"
        />
      </div>
      <span class="mf-label">{{ frames[frameIndex]?.label }}</span>
      <button class="mf-replay" title="从头播放" @click="replay">
        <UiIcon name="refresh" :size="12" /> 重播
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import UiIcon from '@/components/common/UiIcon.vue'
import MathFigure3D from '@/components/chat/MathFigure3D.vue'
import FigureConicInteractive from '@/components/chat/FigureConicInteractive.vue'

const props = defineProps({
  figure: { type: Object, required: true },
  // 所属消息是否仍在流式输出：流结束（done）后自动播放帧序列一次
  streaming: { type: Boolean, default: false },
  // 题目配图（随题卡渲染）：只做静态题干配图，不提供讲解步骤徽标 / 动态演示入口
  questionFigure: { type: Boolean, default: false },
})

const frames = computed(() =>
  Array.isArray(props.figure?.frames) ? props.figure.frames.slice(0, 6) : []
)
const frameIndex = ref(0)

/* ===== N6 可拖动交互（three.js 立体旋转 / 圆锥曲线拖动动点） ===== */
const solidScene = computed(() => props.figure?.solid || null)
const conicParams = computed(() => {
  const fp = props.figure?.figure_params
  return fp?.type === 'conic' ? fp.params : null
})
const interactiveAvailable = computed(() => !!solidScene.value || !!conicParams.value)
const showInteractive = ref(false)
const imgFailed = ref(false)
let autoTimer = null
let playedOnce = false

const prettyParams = computed(() => {
  try {
    return JSON.stringify(props.figure?.figure_params || {}, null, 2)
  } catch {
    return '{}'
  }
})

function clearAuto() {
  if (autoTimer) {
    clearTimeout(autoTimer)
    autoTimer = null
  }
}

function stepForward() {
  if (frameIndex.value < frames.value.length - 1) {
    frameIndex.value += 1
    autoTimer = setTimeout(stepForward, 1600)
  } else {
    autoTimer = null
  }
}

function startAuto() {
  clearAuto()
  autoTimer = setTimeout(stepForward, 1600) // 当前帧停留 1.6s 再推下一帧
}

function jumpTo(i) {
  clearAuto()
  frameIndex.value = Math.max(0, Math.min(i, frames.value.length - 1))
}

function replay() {
  frameIndex.value = 0
  imgFailed.value = false
  startAuto()
}

// 流式结束后自动播放一次；流式期间保持展示当前帧（跟随讲解节奏）。
// 题目配图保持静态，不自动播放（避免把“题目”演成“讲解”）。
watch(
  () => props.streaming,
  (streaming) => {
    if (!props.questionFigure && !streaming && frames.value.length > 1 && !playedOnce) {
      playedOnce = true
      startAuto()
    }
  },
  { immediate: true }
)

watch(frames, () => {
  imgFailed.value = false
})

onBeforeUnmount(clearAuto)
</script>

<style scoped>
.math-figure {
  margin-top: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg-white);
  padding: 10px 12px;
}
.mf-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.mf-badge {
  font-size: 11px;
  color: var(--primary);
  background: #eef1ff;
  border-radius: var(--radius-full);
  padding: 2px 8px;
  font-weight: 600;
}
.mf-badge--question {
  color: #0e7490;
  background: #ecfeff;
}
.mf-caption {
  font-size: 13px;
  color: var(--text-secondary);
}
.mf-stage {
  min-height: 40px;
}
.mf-img {
  display: block;
  max-width: 100%;
  width: auto;
  height: auto;
  border-radius: var(--radius-sm);
  background: #fff;
}
.mf-fallback {
  font-size: 12px;
  color: var(--text-muted);
}
.mf-debug {
  margin-top: 6px;
}
.mf-debug summary {
  cursor: pointer;
  font-size: 11px;
}
.mf-debug pre {
  max-height: 160px;
  overflow: auto;
  font-size: 10px;
  white-space: pre-wrap;
  word-break: break-all;
}
.mf-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.mf-dots {
  display: flex;
  gap: 5px;
}
.mf-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: none;
  cursor: pointer;
  padding: 0;
  background: #d8dce6;
  transition: all 0.15s;
}
.mf-dot.on {
  background: var(--primary);
  transform: scale(1.25);
}
.mf-label {
  font-size: 11px;
  color: var(--text-muted);
  flex: 1;
}
.mf-src-badge {
  margin-left: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e8f6ec;
  color: #1c7a43;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.mf-replay {
  border: 1px solid var(--border);
  background: var(--bg-white);
  color: var(--text-secondary);
  font-size: 11px;
  border-radius: var(--radius-full);
  padding: 2px 8px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font);
}
.mf-replay:hover {
  color: var(--primary);
  border-color: var(--primary-border);
}
/* 帧切换淡入（reveal.js fragments 式渐进揭示，零依赖） */
.mf-fade-enter-active {
  transition: opacity 0.45s ease;
}
.mf-fade-leave-active {
  transition: opacity 0.15s ease;
}
.mf-fade-enter-from,
.mf-fade-leave-to {
  opacity: 0;
}
</style>
