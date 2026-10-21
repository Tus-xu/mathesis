<template>
  <div class="mx-geo" :class="{ 'mx-geo--boxed': boxed }" :data-testid="testid">
    <div class="mx-geo__board-wrap">
      <div ref="boardHost" class="mx-geo__board" :style="{ height: pxHeight }" />
      <div v-if="!preset" class="mx-geo__missing">未知图形预设：{{ presetId }}</div>
      <span v-if="showBadge && preset" class="mx-geo__lv" :class="`mx-geo__lv--l${preset.level}`">
        {{ preset.level === 2 ? 'L2 构造' : 'L1 模板' }}
      </span>
    </div>
    <div v-if="sliders && preset" class="mx-geo__panel">
      <div v-for="sp in preset.params" :key="sp.key" class="mx-slider">
        <span class="mx-slider__label" :title="sp.label">{{ sp.label }}</span>
        <input
          type="range" :min="sp.min" :max="sp.max" :step="sp.step"
          :value="numParam(sp.key, sp.def)"
          :data-testid="`mx-geo-param-${sp.key}`"
          @input="onSlider(sp.key, ($event.target as HTMLInputElement).value)"
        >
        <span class="mx-slider__value">{{ fmt(numParam(sp.key, sp.def)) }}{{ sp.unit || '' }}</span>
      </div>
      <div v-if="preset.toggles?.length" class="mx-geo__toggles">
        <label v-for="tg in preset.toggles" :key="tg.key" class="mx-geo__toggle">
          <input
            type="checkbox" :checked="tBool(tg.key, tg.def)"
            @change="onToggle(tg.key, ($event.target as HTMLInputElement).checked)"
          >{{ tg.label }}
        </label>
      </div>
      <div v-if="editable" class="mx-geo__actions">
        <button class="tv3-btn tv3-btn--sm" type="button" @click="resetParams">恢复默认</button>
        <button v-if="preset.level === 2" class="tv3-btn tv3-btn--sm" type="button" data-testid="mx-geo-save-recipe" @click="$emit('save-recipe')">存为配方</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * GeoFigure —— 图形渲染与编辑组件（SPEC §5.6/§5.7）
 * 三种用法：
 *   编辑态  <GeoFigure preset sliders editable @update:params>   画布 + 属性面板滑杆
 *   放映态  <GeoFigure preset :interactive="true">               画布可拖点，无面板
 *   静态态  <GeoFigure preset :static="true">                    miniSvg 零引擎开销（大纲/卡片）
 * 参数变更 → 重建画布（构造式 L2 的截面等依赖参数整体重算）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import JXG from 'jsxgraph'
import { FIGURE_PRESETS } from './presets'

const props = withDefaults(defineProps<{
  presetId: string
  params?: Record<string, number>
  toggles?: Record<string, boolean>
  sliders?: boolean
  editable?: boolean
  interactive?: boolean
  boxed?: boolean
  height?: number
  showBadge?: boolean
  testid?: string
}>(), {
  params: () => ({}),
  toggles: () => ({}),
  sliders: false,
  editable: false,
  interactive: false,
  boxed: false,
  height: 260,
  showBadge: false,
})

const emit = defineEmits<{
  (e: 'update:params', v: Record<string, number>): void
  (e: 'update:toggles', v: Record<string, boolean>): void
  (e: 'save-recipe'): void
}>()

const boardHost = ref<HTMLElement | null>(null)
let board: any = null
let rebuildTimer: number | undefined

// 容错：生成端可能给裸名（hyperbola/ellipse），回退匹配 conic/ 前缀 preset
const preset = computed(
  () =>
    FIGURE_PRESETS.find((p) => p.id === props.presetId) ||
    FIGURE_PRESETS.find((p) => p.id === `conic/${props.presetId}`),
)
const pxHeight = computed(() => `${props.height}px`)

function numParam(key: string, def: number): number {
  const v = props.params?.[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : def
}
function tBool(key: string, def: boolean): boolean {
  const v = props.toggles?.[key]
  return typeof v === 'boolean' ? v : def
}
const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2))

function buildBoard() {
  if (!boardHost.value || !preset.value) return
  if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null }
  boardHost.value.innerHTML = ''
  const def = preset.value
  const p: Record<string, number> = {}
  for (const sp of def.params) p[sp.key] = numParam(sp.key, sp.def)
  const t: Record<string, boolean> = {}
  for (const tg of def.toggles || []) t[tg.key] = tBool(tg.key, tg.def)
  try {
    board = JXG.JSXGraph.initBoard(boardHost.value, {
      boundingbox: def.boundingbox,
      axis: def.axis,
      showNavigation: props.interactive,
      showCopyright: false,
      pan: { enabled: false },
      zoom: { factorX: 1, factorY: 1, wheel: false },
      registerEvents: props.interactive,
      defaultAxes: def.axis
        ? { x: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 10 } } }, y: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 10 } } } }
        : undefined,
    })
    def.build({ board, p, t })
  } catch {
    boardHost.value.innerHTML = `<div style="display:grid;place-items:center;height:100%;color:#8b95a7;font-size:12px">图形渲染失败</div>`
  }
}

function scheduleRebuild() {
  if (rebuildTimer) window.clearTimeout(rebuildTimer)
  rebuildTimer = window.setTimeout(buildBoard, 30)
}

function onSlider(key: string, raw: string) {
  const v = Number(raw)
  if (!Number.isFinite(v)) return
  emit('update:params', { ...props.params, [key]: v })
}
function onToggle(key: string, v: boolean) {
  emit('update:toggles', { ...props.toggles, [key]: v })
}
function resetParams() {
  const p: Record<string, number> = {}
  const t: Record<string, boolean> = {}
  for (const sp of preset.value?.params || []) p[sp.key] = sp.def
  for (const tg of preset.value?.toggles || []) t[tg.key] = tg.def
  emit('update:params', p)
  emit('update:toggles', t)
}

onMounted(buildBoard)
watch(() => [props.presetId, props.params, props.toggles, props.interactive], scheduleRebuild, { deep: true })
onBeforeUnmount(() => {
  if (rebuildTimer) window.clearTimeout(rebuildTimer)
  if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null }
})
</script>

<style scoped>
.mx-geo { display: flex; flex-direction: column; gap: 0; min-width: 0; }
.mx-geo--boxed { border: 1px solid var(--tv3-line); border-radius: 10px; background: #fff; overflow: hidden; }
.mx-geo__board-wrap { position: relative; min-height: 0; }
.mx-geo__board { width: 100%; border-radius: inherit; overflow: hidden; }
.mx-geo__board :deep(.JXGtext) { font-family: var(--tv3-font-num); }
.mx-geo__missing {
  position: absolute; inset: 0; display: grid; place-items: center;
  color: var(--tv3-ink3); font-size: 12px;
}
.mx-geo__lv {
  position: absolute; top: 6px; right: 6px; z-index: 5;
  font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 999px;
}
.mx-geo__lv--l1 { background: var(--tv3-primary-soft); color: var(--tv3-primary); }
.mx-geo__lv--l2 { background: var(--tv3-gold-soft); color: var(--tv3-gold-deep); }
.mx-geo__panel { padding: 10px 12px 8px; border-top: 1px solid var(--tv3-line2); background: #fbfcfe; }
.mx-geo__toggles { display: flex; gap: 12px; margin: 4px 0 6px; }
.mx-geo__toggle { font-size: 11.5px; color: var(--tv3-ink2); display: inline-flex; gap: 4px; align-items: center; cursor: pointer; }
.mx-geo__toggle input { accent-color: var(--tv3-gold); }
.mx-geo__actions { display: flex; gap: 8px; margin-top: 6px; }
</style>
