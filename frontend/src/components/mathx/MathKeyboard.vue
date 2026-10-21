<template>
  <div class="mx-kbd" :data-testid="testid">
    <div class="mx-kbd__tabs">
      <button
        v-for="g in orderedGroups" :key="g.id"
        class="mx-kbd__tab" :class="{ 'is-active': active === g.id, 'mx-kbd__tab--smart': smartFor && g.smartFor?.includes(smartFor) }"
        type="button" @click="active = g.id"
      >{{ g.label }}<span v-if="smartFor && g.smartFor?.includes(smartFor)" class="mx-kbd__smart-dot" /></button>
      <span class="mx-kbd__tabs-gap" />
      <button
        class="mx-kbd__tab mx-kbd__tab--canvas" :class="{ 'is-active': active === 'canvas' }"
        type="button" title="不会 LaTeX？切到手写画板，写完识别成公式再插入" data-testid="mx-kbd-canvas-tab"
        @click="active = 'canvas'"
      >✍ 画布</button>
    </div>

    <!-- 画布模式：手写 → 识别 → 审查（可改）→ 插入/拖拽（复用绘图工作台手写识别链路） -->
    <div v-if="active === 'canvas'" class="mx-kbd__canvas" data-testid="mx-kbd-canvas">
      <div
        class="mx-kbd__pad" data-testid="mx-kbd-pad"
        @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
      >
        <svg class="mx-kbd__pad-svg">
          <path v-for="(s, i) in strokes" :key="i" :d="pathD(s)" class="mx-kbd__stroke" :style="{ strokeWidth: penW + 0.5 }" />
          <path v-if="cur.length" :d="pathD(cur)" class="mx-kbd__stroke mx-kbd__stroke--cur" :style="{ strokeWidth: penW + 0.5 }" />
        </svg>
        <span v-if="!strokes.length && !cur.length" class="mx-kbd__pad-empty">在这里手写公式</span>
      </div>
      <div class="mx-kbd__canvas-side">
        <div class="tv3-seg">
          <button v-for="w in WIDTHS" :key="w.v" class="tv3-seg__btn" :class="{ 'is-active': penW === w.v }" type="button" @click="penW = w.v">{{ w.label }}</button>
        </div>
        <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!strokes.length" data-testid="mx-kbd-undo" @click="strokes.pop()">↶ 撤销</button>
        <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!strokes.length" @click="clearStrokes">清空</button>
        <button
          class="tv3-btn tv3-btn--sm tv3-btn--ghost-ai" type="button"
          :disabled="!strokes.length || recognizing" data-testid="mx-kbd-recognize"
          @click="recognize"
        >{{ recognizing ? '识别中…' : '✦ 识别' }}</button>
      </div>
      <div v-if="resultLatex !== null" class="mx-kbd__result" data-testid="mx-kbd-result">
        <span class="mx-kbd__result-chip" draggable="true" data-testid="mx-kbd-result-chip"
          title="识别演示（固定样例，未接入真实识别服务） · 也可拖入公式框"
          @dragstart="onResultDrag"
          v-html="renderLatex(resultLatex)"
        />
        <span class="mx-kbd__result-conf" title="原型为固定样例演示，未接入真实识别服务">识别演示</span>
        <span class="mx-kbd__result-tip">识别结果可改（点框编辑）：</span>
        <MathField v-model="resultLatex" :font-size="17" testid="mx-kbd-result-field" />
        <button class="tv3-btn tv3-btn--sm tv3-btn--gold" type="button" data-testid="mx-kbd-insert" @click="insertResult">插入</button>
      </div>
      <span v-else-if="canvasStage" class="mx-kbd__stage" data-testid="mx-kbd-stage">{{ canvasStage }}</span>
    </div>

    <div v-else class="mx-kbd__keys">
      <div
        v-for="k in currentKeys" :key="k.latex"
        class="mx-kbd__key" :class="{ 'mx-kbd__key--rec': smartFor && k.rec }"
        draggable="true" :data-testid="`mx-kbd-key-${k.latex}`"
        :title="k.tip || k.label"
        @click="$emit('insert', k)"
        @dragstart="onDragStart($event, k)"
      >
        <span v-html="k.html || k.label" />
      </div>
    </div>
    <div class="mx-kbd__hint">{{ active === 'canvas' ? '手写 → 识别 → 审查修改后插入；原笔迹保留对照，识别不直接定稿' : '点按插入 · 拖到公式上包裹结构 · Backspace 逐层删除' }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * MathKeyboard —— 公式键盘 + 公式画布（SPEC §5.5：分组 + 章节智能排序 + 最近使用前置）
 * 键盘模式：拖拽入位（key draggable，dataTransfer 携带 mx/latex，由 MathField 宿主处理 drop 包裹）。
 * 画布模式（V3.2）：不会 LaTeX 的老师手写公式 → 识别 → MathField 审查修改（识别不定稿）
 *   → 插入或拖拽进输入框；复用绘图工作台 handRecognize 链路（笔迹→LaTeX）。
 */
import { onBeforeUnmount, computed, ref } from 'vue'
import { renderLatex, cleanPlaceholder } from './latex'
import MathField from './MathField.vue'
import { v3Api } from '@/api/teacherV3'

export interface KbdKey { latex: string; label: string; html?: string; tip?: string; rec?: boolean }
export interface KbdGroup { id: string; label: string; keys: KbdKey[]; smartFor?: string[] }

const props = withDefaults(defineProps<{
  smartFor?: 'conic' | 'solid' | 'function' | 'stat' | 'plane'
  testid?: string
}>(), {})

const emit = defineEmits<{ (e: 'insert', k: KbdKey): void }>()

const GROUPS: KbdGroup[] = [
  {
    id: 'structure', label: '结构', smartFor: [],
    keys: [
      { latex: '\\sqrt{#0}', label: '√', tip: '根号（拖到数字上变 √3）' },
      { latex: '\\sqrt[#1]{#0}', label: 'ⁿ√', tip: 'n 次根' },
      { latex: '\\frac{#0}{#1}', label: 'a/b', tip: '分式（除法横线）' },
      { latex: '#0^{#1}', label: 'x²', tip: '上标' },
      { latex: '#0_{#1}', label: 'x₂', tip: '下标' },
      { latex: '\\left(#0\\right)', label: '( )', tip: '括号' },
      { latex: '\\left|#0\\right|', label: '| |', tip: '绝对值' },
      { latex: '#0^{\\circ}', label: '°', tip: '角度' },
      { latex: '\\sum_{#0}^{#1}', label: 'Σ', tip: '求和' },
      { latex: '\\int_{#0}^{#1}', label: '∫', tip: '积分' },
      { latex: '\\lim_{#0}', label: 'lim', tip: '极限' },
      { latex: '\\mathrm{e}^{#0}', label: 'eˣ', tip: '指数' },
      { latex: '\\log_{#0}#1', label: 'log', tip: '对数' },
      { latex: '\\vec{#0}', label: 'a⃗', tip: '向量' },
    ],
  },
  {
    id: 'ops', label: '运算',
    keys: [
      { latex: '\\pm', label: '±' }, { latex: '\\times', label: '×' }, { latex: '\\div', label: '÷' },
      { latex: '\\neq', label: '≠' }, { latex: '\\leq', label: '≤' }, { latex: '\\geq', label: '≥' },
      { latex: '\\approx', label: '≈' }, { latex: '\\infty', label: '∞' }, { latex: '\\cdots', label: '⋯' },
      { latex: '\\because', label: '∵' }, { latex: '\\therefore', label: '∴' },
    ],
  },
  {
    id: 'greek', label: '希腊',
    keys: [
      { latex: '\\alpha', label: 'α' }, { latex: '\\beta', label: 'β' }, { latex: '\\gamma', label: 'γ' },
      { latex: '\\theta', label: 'θ' }, { latex: '\\lambda', label: 'λ' }, { latex: '\\mu', label: 'μ' },
      { latex: '\\rho', label: 'ρ' }, { latex: '\\varphi', label: 'φ' }, { latex: '\\omega', label: 'ω' },
    ],
  },
  {
    id: 'set', label: '集合',
    keys: [
      { latex: '\\in', label: '∈' }, { latex: '\\notin', label: '∉' }, { latex: '\\subset', label: '⊂' },
      { latex: '\\subseteq', label: '⊆' }, { latex: '\\cup', label: '∪' }, { latex: '\\cap', label: '∩' },
      { latex: '\\varnothing', label: '∅' }, { latex: '\\mathbb{N}', label: 'ℕ' }, { latex: '\\mathbb{Z}', label: 'ℤ' },
      { latex: '\\mathbb{R}', label: 'ℝ' },
    ],
  },
  {
    id: 'reason', label: '推理', smartFor: ['conic', 'plane', 'solid'],
    keys: [
      { latex: '\\Rightarrow', label: '⇒' }, { latex: '\\Leftrightarrow', label: '⇔' },
      { latex: '\\perp', label: '⊥', tip: '垂直', rec: true }, { latex: '\\parallel', label: '∥', tip: '平行', rec: true },
      { latex: '\\angle', label: '∠', rec: true }, { latex: '\\triangle', label: '△', rec: true },
      { latex: '\\sim', label: '∽', tip: '相似' }, { latex: '\\cong', label: '≌', tip: '全等' },
    ],
  },
]

// 章节智能键：讲圆锥曲线时把 e=c/a、焦点弦直接可用
const SMART_KEYS: Record<string, KbdKey[]> = {
  conic: [
    { latex: 'e=\\frac{c}{a}', label: 'e=c/a', html: renderLatex('e=\\frac{c}{a}'), tip: '离心率', rec: true },
    { latex: 'c^2=a^2-b^2', label: 'c²=a²−b²', html: renderLatex('c^2=a^2-b^2'), tip: '焦点关系', rec: true },
  ],
  solid: [
    { latex: 'V=\\frac{1}{3}S h', label: 'V=⅓Sh', html: renderLatex('V=\\frac{1}{3}Sh'), tip: '锥体体积', rec: true },
    { latex: 'S=\\pi r^2', label: 'S=πr²', html: renderLatex('S=\\pi r^2'), tip: '圆面积', rec: true },
  ],
  function: [
    { latex: "f'(x)", label: "f'(x)", html: renderLatex("f'(x)"), tip: '导数', rec: true },
    { latex: '\\frac{#0}{#1}', label: 'a/b', html: renderLatex('\\frac{a}{b}'), tip: '差商', rec: true },
  ],
}

const active = ref('structure')
const orderedGroups = computed(() => {
  if (!props.smartFor) return GROUPS
  return [...GROUPS].sort((a, b) => {
    const rank = (g: KbdGroup) => (g.smartFor?.includes(props.smartFor as string) ? 0 : 1)
    return rank(a) - rank(b)
  })
})
const currentKeys = computed<KbdKey[]>(() => {
  const g = GROUPS.find((x) => x.id === active.value)
  const base = g ? [...g.keys] : []
  const smart = props.smartFor ? SMART_KEYS[props.smartFor] || [] : []
  return active.value === 'structure' || (g?.smartFor?.includes(props.smartFor ?? '')) ? [...smart, ...base] : base
})

function onDragStart(ev: DragEvent, k: KbdKey) {
  ev.dataTransfer?.setData('mx/latex', k.latex)
  ev.dataTransfer?.setData('text/plain', k.label)
}

/* ============ 画布模式：手写 → 识别 → 审查 → 插入/拖拽 ============ */
const WIDTHS = [
  { v: 2.5, label: '细' },
  { v: 4, label: '中' },
  { v: 6, label: '粗' },
]
const penW = ref(4)
const strokes = ref<[number, number][][]>([])
const cur = ref<[number, number][]>([])
const recognizing = ref(false)
const resultLatex = ref<string | null>(null)
const confidence = ref(0)
const canvasStage = ref('')
let handCtrl: { abort: () => void } | null = null

function pathD(s: [number, number][]): string {
  if (!s.length) return ''
  return s.map((p, i) => `${i === 0 ? 'M' : 'L'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')
}
function padPoint(e: PointerEvent): [number, number] {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  return [e.clientX - rect.left, e.clientY - rect.top]
}
function onDown(e: PointerEvent) {
  if (e.button !== 0) return
  cur.value = [padPoint(e)]
  ;(e.currentTarget as HTMLElement)?.setPointerCapture?.(e.pointerId)
}
function onMove(e: PointerEvent) {
  if (!cur.value.length) return
  const p = padPoint(e)
  const last = cur.value[cur.value.length - 1]
  if (Math.hypot(p[0] - last[0], p[1] - last[1]) > 1.2) cur.value.push(p)
}
function onUp() {
  if (cur.value.length > 1) strokes.value.push(cur.value.slice())
  cur.value = []
  resultLatex.value = null
}
function clearStrokes() {
  strokes.value = []
  resultLatex.value = null
  canvasStage.value = ''
}
async function recognize() {
  if (!strokes.value.length || recognizing.value) return
  recognizing.value = true
  canvasStage.value = '提交笔迹…'
  resultLatex.value = null
  try {
    handCtrl = v3Api.draw.handRecognize({ strokes: strokes.value.length }, (event, data) => {
      if (event === 'meta') canvasStage.value = `已提交 ${data.strokes} 笔笔迹`
      else if (event === 'recognizing') canvasStage.value = `${data.stage}…`
      else if (event === 'result') { resultLatex.value = cleanPlaceholder(String(data.latex || '')); confidence.value = Number(data.confidence) || 0 }
      else if (event === 'error') {
        /* 后端诚实失败（识别 sidecar 未部署）——如实呈现，绝不返回猜测结果 */
        canvasStage.value = data?.message || '识别失败，请改用公式键盘点按输入'
      }
      else if (event === 'done') {
        canvasStage.value = data?.finish_reason && data.finish_reason !== 'stop'
          ? data?.finish_reason === 'dependency_missing' ? '识别服务未就绪——请改用公式键盘' : `识别未完成（${data.finish_reason}）`
          : ''
      }
    })
  } catch (e: any) { canvasStage.value = e?.message || '识别失败，请检查后端服务' } finally { recognizing.value = false }
}
function insertResult() {
  const latex = cleanPlaceholder(resultLatex.value || '')
  if (!latex) return
  emit('insert', { latex, label: '手写公式', tip: '手写识别结果' })
  resultLatex.value = null
  strokes.value = []
  canvasStage.value = ''
}
function onResultDrag(ev: DragEvent) {
  const latex = cleanPlaceholder(resultLatex.value || '')
  ev.dataTransfer?.setData('mx/latex', latex)
  ev.dataTransfer?.setData('text/plain', '手写公式')
}
onBeforeUnmount(() => handCtrl?.abort())
</script>

<style scoped>
.mx-kbd__tab--smart { color: var(--tv3-gold-deep); }
.mx-kbd__smart-dot { display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: var(--tv3-gold); margin-left: 4px; }
.mx-kbd__key--rec { border-color: var(--tv3-gold-border); background: var(--tv3-gold-soft); }
.mx-kbd__hint { padding: 0 10px 9px; font-size: 10.5px; color: var(--tv3-ink3); }
.mx-kbd__key :deep(.katex) { font-size: 0.95em; }
/* 画布模式 */
.mx-kbd__tabs { display: flex; align-items: center; }
.mx-kbd__tabs-gap { flex: 1; }
.mx-kbd__tab--canvas { color: var(--tv3-gold-deep); font-weight: 700; }
.mx-kbd__canvas { display: flex; align-items: stretch; gap: 10px; padding: 8px 10px 4px; }
.mx-kbd__pad {
  position: relative; flex: 1; min-width: 0; height: 96px;
  border: 1.5px dashed var(--tv3-line); border-radius: 10px; touch-action: none; cursor: crosshair;
  background:
    linear-gradient(#eef2f8 1px, transparent 1px) 0 0 / 100% 24px,
    linear-gradient(90deg, #eef2f8 1px, transparent 1px) 0 0 / 24px 100%,
    #fff;
}
.mx-kbd__pad-svg { width: 100%; height: 100%; display: block; }
.mx-kbd__stroke { fill: none; stroke: #16233b; stroke-linecap: round; stroke-linejoin: round; }
.mx-kbd__stroke--cur { stroke: var(--tv3-primary); }
.mx-kbd__pad-empty {
  position: absolute; inset: 0; display: grid; place-items: center;
  color: var(--tv3-ink4); font-size: 12px; pointer-events: none;
}
.mx-kbd__canvas-side { display: flex; flex-direction: column; gap: 6px; justify-content: center; flex-shrink: 0; }
.mx-kbd__result { display: flex; align-items: center; gap: 8px; padding: 6px 10px; flex-wrap: wrap; }
.mx-kbd__result-chip {
  display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 8px; cursor: grab;
  background: var(--tv3-gold-soft); border: 1px solid var(--tv3-gold-border); font-size: 15px;
}
.mx-kbd__result-conf { font-size: 11px; color: var(--tv3-gold-deep); font-family: var(--tv3-font-num); }
.mx-kbd__result-tip { font-size: 11px; color: var(--tv3-ink3); }
.mx-kbd__stage { padding: 6px 10px; font-size: 11.5px; color: var(--tv3-ink3); }
</style>
