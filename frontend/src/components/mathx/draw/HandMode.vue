<template>
  <div class="mxd-hand" data-testid="mxd-hand">
    <!-- 左：手写板 -->
    <div class="mxd-hand__main">
      <div class="mxd-hand__bar">
        <div class="tv3-seg">
          <button
            v-for="w in WIDTHS" :key="w.v"
            class="tv3-seg__btn" :class="{ 'is-active': penW === w.v }"
            :title="w.tip" @click="penW = w.v"
          >{{ w.label }}</button>
        </div>
        <div class="tv3-card__spacer" />
        <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!strokes.length" data-testid="mxd-hand-undo" @click="strokes.pop()">↶ 撤销一笔</button>
        <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!strokes.length" data-testid="mxd-hand-clear" @click="clearAll">清空</button>
        <button
          class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" type="button"
          :disabled="!strokes.length || recognizing"
          data-testid="mxd-hand-recognize"
          @click="recognize"
        >{{ recognizing ? '识别中…' : '✦ 识别为公式' }}</button>
      </div>

      <div
        class="mxd-hand__pad"
        data-testid="mxd-hand-pad"
        @pointerdown="onDown"
        @pointermove="onMove"
        @pointerup="onUp"
        @pointercancel="onUp"
      >
        <svg class="mxd-hand__svg">
          <path v-for="(s, i) in strokes" :key="i" :d="pathD(s)" class="mxd-hand__stroke" :style="{ strokeWidth: penW + 0.5 }" />
          <path v-if="cur.length" :d="pathD(cur)" class="mxd-hand__stroke mxd-hand__stroke--cur" :style="{ strokeWidth: penW + 0.5 }" />
        </svg>
        <div v-if="!strokes.length && !cur.length" class="mxd-hand__empty">
          用鼠标 / 触控笔直接书写公式
          <div class="mxd-hand__empty-sub">支持多笔：写完一个符号提笔再写下一个</div>
        </div>
      </div>

      <div v-if="stage" class="mxd-hand__stage" data-testid="mxd-hand-stage">{{ stage }}</div>
    </div>

    <!-- 右：识别结果审查区 -->
    <div class="mxd-hand__result">
      <template v-if="resultLatex !== null">
        <div class="mxd-hand__rhead">
          <span class="tv3-tag tv3-tag--gold">识别结果</span>
          <!-- B0 诚实化：当前为固定样例池演示，不显示伪造的精确置信度 -->
          <span class="mxd-hand__conf" title="原型为固定样例演示，未接入真实识别服务">识别演示（未接入真实识别服务）</span>
        </div>
        <div class="tv3-form-label">在编辑器中审查修改（红线：识别不定稿）</div>
        <MathField v-model="resultLatex" :font-size="26" testid="mxd-hand-result-field" />
        <div class="mxd-hand__warn">左侧保留原笔迹，可对照核验；修改后再插入</div>
        <div class="mxd-hand__acts">
          <button class="tv3-btn tv3-btn--sm" type="button" @click="recognize">重新识别</button>
          <div class="tv3-card__spacer" />
          <button class="tv3-btn tv3-btn--primary" type="button" data-testid="mxd-hand-insert" @click="onInsert">插入到课件</button>
        </div>
      </template>
      <template v-else>
        <div class="mxd-hand__guide">
          <div class="mxd-hand__guide-icon">✍️</div>
          <div class="mxd-hand__guide-title">手写 → 公式</div>
          <div class="mxd-hand__guide-text">
            在左侧手写公式，点击「识别为公式」。<br>
            识别结果载入编辑器审查修改，原笔迹保留对照，确认后插入课件。
          </div>
          <div class="mxd-hand__guide-tags">
            <span class="tv3-tag">VLM 识别</span>
            <span class="tv3-tag tv3-tag--gold">结果可编辑</span>
            <span class="tv3-tag tv3-tag--primary">原笔迹锚定</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * HandMode —— 绘图工作台·手写公式识别模式（SPEC §5.5 红线）
 * 手写板收集多笔笔迹 → SSE 识别（笔迹分割 → 符号分类 → LaTeX 组装）
 * → 结果载入 MathField 审查修改（不直接定稿）→ 原笔迹保留对照 → 插入。
 */
import { onBeforeUnmount, ref } from 'vue'
import MathField from '../MathField.vue'
import { v3Api } from '@/api/teacherV3'
import { cleanPlaceholder } from '../latex'
import type { V3DrawInsert } from './drawCore'

const emit = defineEmits<{ (e: 'insert', payload: V3DrawInsert): void }>()

const WIDTHS = [
  { v: 2.5, label: '细', tip: '细笔' },
  { v: 4, label: '中', tip: '中笔' },
  { v: 6, label: '粗', tip: '粗笔' },
]

const penW = ref(4)
const strokes = ref<[number, number][][]>([])
const cur = ref<[number, number][]>([])
const stage = ref('')
const recognizing = ref(false)
const resultLatex = ref<string | null>(null)
const confidence = ref(0)
let sseCtrl: { abort: () => void } | null = null

function pathD(s: [number, number][]): string {
  if (!s.length) return ''
  return s.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
}

function ptOf(e: PointerEvent): [number, number] {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  return [e.clientX - rect.left, e.clientY - rect.top]
}

function onDown(e: PointerEvent) {
  if (e.button !== 0) return
  cur.value = [ptOf(e)]
  ;(e.currentTarget as HTMLElement)?.setPointerCapture?.(e.pointerId)
}

function onMove(e: PointerEvent) {
  if (!cur.value.length) return
  const p = ptOf(e)
  const last = cur.value[cur.value.length - 1]
  if (Math.hypot(p[0] - last[0], p[1] - last[1]) > 1.2) cur.value.push(p)
}

function onUp() {
  if (cur.value.length > 1) strokes.value.push(cur.value.slice())
  cur.value = []
  resultLatex.value = null
}

function clearAll() {
  strokes.value = []
  resultLatex.value = null
  stage.value = ''
}

async function recognize() {
  if (!strokes.value.length || recognizing.value) return
  recognizing.value = true
  stage.value = '提交笔迹…'
  resultLatex.value = null
  try {
    sseCtrl = v3Api.draw.handRecognize({ strokes: strokes.value.length }, (event, data) => {
      if (event === 'meta') stage.value = `已提交 ${data.strokes} 笔笔迹（${data.note || ''}）`
      else if (event === 'recognizing') stage.value = `${data.stage}…`
      else if (event === 'result') { resultLatex.value = cleanPlaceholder(String(data.latex || '')); confidence.value = Number(data.confidence) || 0 }
      else if (event === 'error') {
        /* 后端诚实失败（识别 sidecar 未部署）——如实呈现，绝不返回猜测结果 */
        stage.value = data?.message || '识别失败，请稍后重试或改用公式键盘'
      }
      else if (event === 'done') {
        stage.value = data?.finish_reason && data.finish_reason !== 'stop'
          ? data?.finish_reason === 'dependency_missing'
            ? '识别服务未就绪（后端如实返回）——可改用语音公式或公式键盘'
            : `识别未完成（${data.finish_reason}）`
          : '识别完成，请在右侧审查'
      }
    })
  } catch (e: any) {
    stage.value = e?.message || '识别失败，请检查后端服务'
  } finally {
    recognizing.value = false
  }
}

function onInsert() {
  const latex = cleanPlaceholder(resultLatex.value || '')
  if (!latex) return
  emit('insert', { type: 'formula', latex })
}

onBeforeUnmount(() => sseCtrl?.abort())
</script>

<style scoped>
.mxd-hand { display: flex; height: 100%; min-height: 0; gap: 0; }
.mxd-hand__main { flex: 1.6; display: flex; flex-direction: column; min-width: 0; }
.mxd-hand__bar {
  display: flex; align-items: center; gap: 10px; padding: 8px 14px;
  border-bottom: 1px solid var(--tv3-line2);
}
.mxd-hand__pad {
  position: relative; flex: 1; min-height: 0; margin: 12px 14px 6px;
  border: 1.5px dashed var(--tv3-line); border-radius: 12px;
  background:
    linear-gradient(#eef2f8 1px, transparent 1px) 0 0 / 100% 34px,
    linear-gradient(90deg, #eef2f8 1px, transparent 1px) 0 0 / 34px 100%,
    #fff;
  touch-action: none; cursor: crosshair; overflow: hidden;
}
.mxd-hand__svg { width: 100%; height: 100%; display: block; }
.mxd-hand__stroke { fill: none; stroke: #16233b; stroke-linecap: round; stroke-linejoin: round; }
.mxd-hand__stroke--cur { stroke: var(--tv3-primary); }
.mxd-hand__empty {
  position: absolute; inset: 0; display: grid; place-items: center;
  color: var(--tv3-ink3); font-size: 14px; text-align: center; pointer-events: none;
}
.mxd-hand__empty-sub { font-size: 12px; margin-top: 6px; }
.mxd-hand__stage {
  margin: 0 14px 10px; padding: 6px 12px; border-radius: 8px;
  background: var(--tv3-ai-soft); border: 1px solid var(--tv3-ai-border);
  font-size: 12.5px; color: var(--tv3-ink2);
}
.mxd-hand__result {
  width: 340px; border-left: 1px solid var(--tv3-line2); padding: 16px;
  display: flex; flex-direction: column; gap: 10px; background: #fbfcfe;
}
.mxd-hand__rhead { display: flex; align-items: center; gap: 10px; }
.mxd-hand__conf { font-size: 12px; color: var(--tv3-gold-deep); font-family: var(--tv3-font-num); }
.mxd-hand__warn {
  font-size: 11.5px; color: var(--tv3-ink3); line-height: 1.6;
  padding: 8px 10px; background: var(--tv3-gold-soft); border-radius: 8px;
}
.mxd-hand__acts { display: flex; align-items: center; gap: 8px; margin-top: auto; }
.mxd-hand__guide { margin: auto; text-align: center; padding: 0 10px; }
.mxd-hand__guide-icon { font-size: 38px; margin-bottom: 10px; }
.mxd-hand__guide-title { font-size: 16px; font-weight: 700; color: var(--tv3-ink); margin-bottom: 8px; }
.mxd-hand__guide-text { font-size: 12.5px; color: var(--tv3-ink3); line-height: 1.8; margin-bottom: 14px; }
.mxd-hand__guide-tags { display: flex; gap: 6px; justify-content: center; flex-wrap: wrap; }
</style>
