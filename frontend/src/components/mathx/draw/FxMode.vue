<template>
  <div class="mxd-fx" data-testid="mxd-fx">
    <div class="mxd-fx__bar">
      <span class="mxd-fx__y">y =</span>
      <MathField v-model="expr" :font-size="20" testid="mxd-fx-expr" placeholder="如 a\cdot\sin(bx+c)+d" @focus="hot = true" @blur="hot = false" />
      <span v-if="parseErr" class="mxd-fx__err" data-testid="mxd-fx-err">{{ parseErr }}</span>
      <div class="mxd-fx__samples">
        <button v-for="s in SAMPLES" :key="s.latex" class="tv3-btn tv3-btn--sm" type="button" :title="s.tip" @click="expr = s.latex">{{ s.label }}</button>
      </div>
    </div>

    <div v-if="paramKeys.length" class="mxd-fx__params" data-testid="mxd-fx-params">
      <div v-for="k in paramKeys" :key="k" class="mx-slider">
        <span class="mx-slider__label">{{ k }}</span>
        <input
          type="range" min="-5" max="5" step="0.1"
          :value="paramVals[k]"
          :data-testid="`mxd-fx-param-${k}`"
          @input="paramVals[k] = Number(($event.target as HTMLInputElement).value); refreshNow()"
        >
        <span class="mx-slider__value">{{ fmt(paramVals[k]) }}</span>
      </div>
      <button class="tv3-btn tv3-btn--sm" type="button" @click="resetParams">恢复默认</button>
    </div>
    <div v-else class="mxd-fx__params mxd-fx__params--empty">输入含参数（a、b、c、d）的表达式，自动生成调参滑杆</div>

    <div ref="host" class="mxd-fx__board" />

    <div class="mxd-fx__foot">
      <label class="mxd-fx__domain">
        定义域 x ∈
        <input v-model.number="domain[0]" type="number" step="1" data-testid="mxd-fx-domain-min" @change="rebuild">
        ,
        <input v-model.number="domain[1]" type="number" step="1" data-testid="mxd-fx-domain-max" @change="rebuild">
      </label>
      <div class="tv3-card__spacer" />
      <span class="mxd-fx__hint">插入后为结构化函数元素 · 放映态参数可拖</span>
      <button class="tv3-btn tv3-btn--primary" type="button" :disabled="!canInsert" data-testid="mxd-fx-insert" @click="onInsert">插入课件</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * FxMode —— 绘图工作台·函数绘图模式
 * 输入 LaTeX 表达式 → expr.ts 解析 → JSXGraph functiongraph 实时渲染；
 * a-d 参数自动生成滑杆，闭包捕获当前值，滑杆拖动仅 board.update() 不重建。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import JXG from 'jsxgraph'
import MathField from '../MathField.vue'
import { makeFn, parseLatexExpr } from '../expr'
import { boardSvgToDataUrl, type V3DrawInsert } from './drawCore'

const props = withDefaults(defineProps<{ initialExpr?: string }>(), { initialExpr: '' })
const emit = defineEmits<{ (e: 'insert', payload: V3DrawInsert): void; (e: 'changed', expr: string): void }>()

const SAMPLES = [
  { latex: 'a\\cdot\\sin(bx+c)+d', label: 'y=a·sin(bx+c)+d', tip: '正弦函数族（三参数联动）' },
  { latex: 'ax^{2}+bx+c', label: 'y=ax²+bx+c', tip: '二次函数族' },
  { latex: 'a\\cdot e^{x}', label: 'y=a·eˣ', tip: '指数函数' },
  { latex: '\\ln{x}', label: 'y=ln x', tip: '对数函数' },
  { latex: '\\frac{1}{x}', label: 'y=1/x', tip: '反比例函数' },
  { latex: '\\tan{x}', label: 'y=tan x', tip: '正切函数' },
]

const expr = ref(props.initialExpr || 'a\\cdot\\sin(bx+c)+d')
const domain = ref<[number, number]>([-8, 8])
const paramVals = ref<Record<string, number>>({ a: 1, b: 1, c: 0, d: 0 })
const hot = ref(false)

const parsed = computed(() => parseLatexExpr(expr.value))
const parseErr = computed(() => parsed.value.error)
const paramKeys = computed(() => parsed.value.params)
const canInsert = computed(() => !parseErr.value && parsed.value.js.length > 0 && domain.value[1] > domain.value[0])

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1))

/* ---------- JSXGraph ---------- */
const host = ref<HTMLElement | null>(null)
let board: any = null
let evaluator: (x: number) => number = () => NaN

function refreshNow() {
  bindEvaluator()
  board?.update()
}

function onInsert() {
  if (!canInsert.value) return
  const params: Record<string, { value: number; min: number; max: number; step: number }> = {}
  for (const k of paramKeys.value) params[k] = { value: paramVals.value[k] ?? 1, min: -5, max: 5, step: 0.1 }
  emit('insert', { type: 'functionPlot', expr: expr.value, params, domain: [domain.value[0], domain.value[1]] })
}

function buildBoard() {
  if (!host.value) return
  if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null }
  host.value.innerHTML = ''
  const [x0, x1] = domain.value
  try {
    board = JXG.JSXGraph.initBoard(host.value, {
      boundingbox: [x0, Math.max(6, (x1 - x0) / 2.4), x1, -Math.max(6, (x1 - x0) / 2.4)],
      axis: true,
      showNavigation: false,
      showCopyright: false,
      pan: { enabled: false },
      zoom: { factorX: 1, factorY: 1, wheel: false },
      defaultAxes: {
        x: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 11 } } },
        y: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 11 } } },
      },
    })
    bindEvaluator()
    board.create('functiongraph', [(x: number) => evaluator(x), x0, x1], {
      strokeColor: '#4f46e5', strokeWidth: 3, highlight: false,
    })
    board.update()
  } catch { /* 渲染失败静默 */ }
}

function bindEvaluator() {
  const p = { ...paramVals.value }
  const fn = makeFn(parsed.value)
  evaluator = (x: number) => fn(x, p)
}

function rebuild() {
  if (!Number.isFinite(domain.value[0])) domain.value[0] = -8
  if (!Number.isFinite(domain.value[1])) domain.value[1] = 8
  buildBoard()
}

function resetParams() {
  const o: Record<string, number> = {}
  for (const k of paramKeys.value) o[k] = k === 'c' || k === 'd' ? 0 : 1
  paramVals.value = o
  refreshNow()
}

function thumb(): string {
  const svg = host.value?.querySelector('svg')
  return svg ? boardSvgToDataUrl(svg as unknown as SVGSVGElement).src : ''
}

defineExpose({
  loadExpr: (e: string) => { expr.value = e || 'a\\cdot\\sin(bx+c)+d' },
  describe: () => ({ kind: 'fx' as const, expr: expr.value, thumb: thumb() }),
})

watch(() => props.initialExpr, (e) => { if (e) expr.value = e })
watch(paramKeys, (keys) => {
  const o = { ...paramVals.value }
  for (const k of keys) if (!(k in o)) o[k] = k === 'c' || k === 'd' ? 0 : 1
  paramVals.value = o
  refreshNow()
})
watch(expr, () => {
  if (!hot.value) refreshNow()
  emit('changed', expr.value)
})
watch(hot, (h) => { if (!h) refreshNow() })

onMounted(() => nextTick(buildBoard))
onBeforeUnmount(() => { if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null } })
</script>

<style scoped>
.mxd-fx { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.mxd-fx__bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  border-bottom: 1px solid var(--tv3-line2); flex-wrap: wrap;
}
.mxd-fx__y { font-family: var(--tv3-font-num); font-size: 17px; font-weight: 700; color: var(--tv3-ink); }
.mxd-fx__err { font-size: 12px; color: var(--tv3-rose); }
.mxd-fx__samples { display: flex; gap: 6px; margin-left: 10px; flex-wrap: wrap; }
.mxd-fx__params {
  display: flex; align-items: center; gap: 14px; padding: 8px 14px;
  border-bottom: 1px solid var(--tv3-line2); flex-wrap: wrap; background: #fbfcfe;
}
.mxd-fx__params--empty { font-size: 12px; color: var(--tv3-ink3); }
.mxd-fx__params .mx-slider { margin: 0; }
.mxd-fx__board { flex: 1; min-height: 0; }
.mxd-fx__board :deep(.JXGtext) { font-family: var(--tv3-font-num); }
.mxd-fx__foot {
  display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  border-top: 1px solid var(--tv3-line2);
}
.mxd-fx__domain { font-size: 13px; color: var(--tv3-ink2); display: inline-flex; align-items: center; gap: 6px; }
.mxd-fx__domain input {
  width: 62px; padding: 4px 6px; border: 1px solid var(--tv3-line); border-radius: 6px;
  font-family: var(--tv3-font-num); font-size: 13px;
}
.mxd-fx__hint { font-size: 11.5px; color: var(--tv3-ink3); }
</style>
