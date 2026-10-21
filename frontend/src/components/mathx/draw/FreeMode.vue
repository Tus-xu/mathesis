<template>
  <div class="mxd-free" data-testid="mxd-free">
    <!-- 工具栏 -->
    <div class="mxd-free__bar">
      <div class="tv3-seg">
        <button
          v-for="t in TOOLS" :key="t.id"
          class="tv3-seg__btn" :class="{ 'is-active': tool === t.id }"
          :title="t.tip" :data-testid="`mxd-free-tool-${t.id}`"
          @click="setTool(t.id)"
        >{{ t.icon }} {{ t.label }}</button>
      </div>
      <div class="mxd-free__colors">
        <button
          v-for="c in COLORS" :key="c"
          class="mxd-free__color" :class="{ 'is-active': color === c }"
          :style="{ background: c }" :title="c"
          @click="color = c"
        />
      </div>
      <div class="tv3-seg">
        <button
          v-for="w in WIDTHS" :key="w.v"
          class="tv3-seg__btn" :class="{ 'is-active': penW === w.v }"
          :title="w.tip" @click="penW = w.v"
        >{{ w.label }}</button>
      </div>
      <div class="tv3-card__spacer" />
      <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!records.length" data-testid="mxd-free-undo" @click="undo">↶ 撤销</button>
      <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!records.length" data-testid="mxd-free-clear" @click="clearAll">清空</button>
    </div>

    <!-- 规整图形（点击放置到画布中心，选择工具可拖动 / 调参 / 删除） -->
    <div class="mxd-free__presets">
      <div class="mxd-free__pgroup">
        <span class="mxd-free__presets-lbl">平面</span>
        <button
          v-for="p in planeChips" :key="p.id"
          class="mxd-free__preset" :title="`${p.name}：${p.desc}`"
          :data-testid="`mxd-free-preset-${p.id}`"
          @click="addPreset(p)"
        >
          <span class="mxd-free__preset-thumb" v-html="p.miniSvg(defParams(p))" />
          <span>{{ p.name }}</span>
        </button>
      </div>
      <div class="mxd-free__pgroup">
        <span class="mxd-free__presets-lbl">立体</span>
        <button
          v-for="p in solidChips" :key="p.id"
          class="mxd-free__preset" :title="`${p.name}：${p.desc}`"
          :data-testid="`mxd-free-preset-${p.id}`"
          @click="addPreset(p)"
        >
          <span class="mxd-free__preset-thumb" v-html="p.miniSvg(defParams(p))" />
          <span>{{ p.name }}</span>
        </button>
      </div>
    </div>

    <!-- 画布 -->
    <div class="mxd-free__stage">
      <div
        ref="host"
        class="mxd-free__board"
        :class="`mxd-free__board--${tool}`"
        data-testid="mxd-free-board"
        @pointerdown="onDown"
        @pointermove="onMove"
        @pointerup="onUp"
        @pointercancel="onUp"
        @dblclick="onDbl"
      />
      <div class="mxd-free__hint">{{ hintOf }}</div>
      <div v-if="records.length" class="mxd-free__count" data-testid="mxd-free-count">{{ records.length }} 条结构化记录</div>
      <div v-if="selectedRec && !showProps" class="mxd-free__selinfo" data-testid="mxd-free-selinfo">
        已选中：{{ recLabel(selectedRec) }} · 拖动移动 · Delete 删除
      </div>

      <!-- 选中对象属性面板（即时调参 / 改样式 / 删除） -->
      <div v-if="selectedRec && showProps" class="mxd-free__props" data-testid="mxd-free-props">
        <div class="mxd-free__props-head">
          <span class="mxd-free__props-name">{{ recLabel(selectedRec) }}</span>
          <div class="tv3-card__spacer" />
          <button class="mxd-free__props-x" type="button" title="收起" @click="showProps = false">▾</button>
        </div>

        <!-- preset 参数滑杆 + 开关 -->
        <template v-if="selectedRec.kind === 'preset'">
          <div v-for="sp in selParams" :key="sp.key" class="mx-slider">
            <span class="mx-slider__label" :title="sp.label">{{ sp.label }}</span>
            <input
              type="range" :min="sp.min" :max="sp.max" :step="sp.step"
              :value="selectedRec.params[sp.key]" :data-testid="`mxd-free-param-${sp.key}`"
              @input="setSelParam(sp.key, Number(($event.target as HTMLInputElement).value))"
            >
            <span class="mx-slider__value">{{ selectedRec.params[sp.key] }}</span>
          </div>
          <label v-for="tg in selToggles" :key="tg.key" class="mxd-free__tg">
            <input
              type="checkbox" style="accent-color: var(--tv3-gold)"
              :checked="(selectedRec.toggles?.[tg.key] ?? tg.def)"
              :data-testid="`mxd-free-toggle-${tg.key}`"
              @change="setSelToggle(tg.key, ($event.target as HTMLInputElement).checked)"
            >{{ tg.label }}
          </label>
        </template>

        <!-- 文字内容编辑 -->
        <template v-if="selectedRec.kind === 'text'">
          <div class="tv3-form-label" style="margin-bottom: 6px">文字内容</div>
          <input
            class="tv3-input" style="font-size: 12.5px" :value="selectedRec.text"
            data-testid="mxd-free-text-edit" @change="setSelText(($event.target as HTMLInputElement).value)"
          >
        </template>

        <!-- 圆：半径滑杆 -->
        <template v-if="selectedRec.kind === 'circle'">
          <div class="mx-slider">
            <span class="mx-slider__label">半径 r</span>
            <input
              type="range" min="0.3" max="6" step="0.05" :value="selectedRec.r"
              data-testid="mxd-free-param-r"
              @input="setSelCircleR(Number(($event.target as HTMLInputElement).value))"
            >
            <span class="mx-slider__value">{{ selectedRec.r.toFixed(2) }}</span>
          </div>
        </template>

        <!-- 直线：端点数值编辑 -->
        <template v-if="selectedRec.kind === 'line'">
          <div class="tv3-form-label" style="margin: 10px 0 6px">端点坐标（可调）</div>
          <div class="mxd-free__props-grid">
            <label>A x<input type="number" step="0.1" class="tv3-input" :value="fnum(selectedRec.a[0])" @change="setSelLinePt('a', 0, ($event.target as HTMLInputElement).value)"></label>
            <label>A y<input type="number" step="0.1" class="tv3-input" :value="fnum(selectedRec.a[1])" @change="setSelLinePt('a', 1, ($event.target as HTMLInputElement).value)"></label>
            <label>B x<input type="number" step="0.1" class="tv3-input" :value="fnum(selectedRec.b[0])" @change="setSelLinePt('b', 0, ($event.target as HTMLInputElement).value)"></label>
            <label>B y<input type="number" step="0.1" class="tv3-input" :value="fnum(selectedRec.b[1])" @change="setSelLinePt('b', 1, ($event.target as HTMLInputElement).value)"></label>
          </div>
        </template>

        <!-- 样式 -->
        <div class="tv3-form-label" style="margin: 10px 0 6px">样式</div>
        <div class="mxd-free__props-row">
          <button
            v-for="c in COLORS" :key="c"
            class="mxd-free__color" :class="{ 'is-active': selectedRec.color === c }"
            :style="{ background: c }" title="颜色" @click="setSelColor(c)"
          />
          <div class="tv3-seg" style="margin-left: 8px">
            <button
              v-for="w in WIDTHS" :key="w.v"
              class="tv3-seg__btn" :class="{ 'is-active': selectedRec.width === w.v }"
              @click="setSelWidth(w.v)"
            >{{ w.label }}</button>
          </div>
        </div>

        <button
          class="tv3-btn tv3-btn--sm" type="button" style="margin-top: 10px; color: var(--tv3-rose); border-color: var(--tv3-rose-border)"
          data-testid="mxd-free-del" @click="removeSelected"
        >🗑 删除此元素（Delete）</button>
      </div>
      <button
        v-else-if="selectedRec" class="mxd-free__props-mini" type="button"
        title="展开属性面板" @click="showProps = true"
      >⚙ 调参</button>

      <!-- 文字标注输入 -->
      <div v-if="textDraft" class="mxd-free__textinput" :style="textStyle">
        <input
          ref="textIn" v-model="textDraft.val" class="tv3-input"
          data-testid="mxd-free-text-input" placeholder="标注文字，回车确认"
          @keydown.enter.prevent="commitText"
          @keydown.esc="textDraft = null"
          @blur="commitText"
        >
      </div>
    </div>

    <!-- 底部 -->
    <div class="mxd-free__foot">
      <span class="mxd-free__tip">画笔松手自动规整；「选择」工具：点选 → 拖动移动 → 面板调参 → Delete 删除；双击文字可改</span>
      <div class="tv3-card__spacer" />
      <button class="tv3-btn tv3-btn--primary" type="button" :disabled="!records.length" data-testid="mxd-free-insert" @click="onInsert">插入课件</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * FreeMode —— 绘图工作台·自由画布模式 v2（SPEC R1 结构化红线）
 * records 是唯一持久层（数学坐标的结构化操作），画布只是它的实时渲染。
 * 交互对齐成熟画布规范（Excalidraw / GeoGebra 调研结论）：
 *   选择工具：点选 → 金色包围框 → 拖动移动（offset）→ Delete 删除 → Esc/空白取消；
 *   规整图形放置后可整体拖动（坐标平移代理）、面板实时调参、改样式；
 *   双击文字进入编辑；插入课件 = SVG 白底快照 + records 配方（可重开）。
 */
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import JXG from 'jsxgraph'
import { FIGURE_PRESETS, type FigurePresetDef } from '../presets'
import type { V3DrawRecord } from '@/types/teacherV3'
import {
  boardSvgToDataUrl, drawRecId, drawRecordBbox, hitDrawRecord, isNearlyCircle, isNearlyLine,
  offsetBoard, simplifyPen, type Pt, type V3DrawInsert,
} from './drawCore'

const props = withDefaults(defineProps<{ initialRecords?: V3DrawRecord[] }>(), { initialRecords: () => [] })
const emit = defineEmits<{ (e: 'insert', payload: V3DrawInsert): void; (e: 'changed', records: V3DrawRecord[]): void }>()

type Tool = 'select' | 'pen' | 'line' | 'circle' | 'polygon' | 'point' | 'text'
const TOOLS: { id: Tool; icon: string; label: string; tip: string }[] = [
  { id: 'select', icon: '↖', label: '选择', tip: '点选元素（金色包围框）→ 拖动移动 → 属性面板调参 → Delete 删除 → Esc 取消' },
  { id: 'pen', icon: '✏️', label: '画笔', tip: '按住拖动书写，松手自动规整：近直线→直线，近圆→圆' },
  { id: 'line', icon: '／', label: '直线', tip: '按下定起点，拖动，松开定终点' },
  { id: 'circle', icon: '○', label: '圆', tip: '按下定圆心，拖动定半径，松开完成' },
  { id: 'polygon', icon: '⬡', label: '多边形', tip: '逐个点击顶点，双击结束闭合' },
  { id: 'point', icon: '·', label: '点', tip: '点击画布放置点' },
  { id: 'text', icon: 'T', label: '文字', tip: '点击位置输入标注；双击已有文字可修改' },
]
const COLORS = ['#4f46e5', '#0891b2', '#dc2646', '#0e9488', '#4a5568']
const WIDTHS = [
  { v: 2, label: '细', tip: '细线' },
  { v: 3, label: '中', tip: '中线' },
  { v: 4.5, label: '粗', tip: '粗线' },
]
const PLANE_CHIP_IDS = [
  'conic/parabola', 'conic/ellipse', 'conic/hyperbola', 'function/quadratic',
  'function/sine', 'function/exp-log', 'plane/triangle-heights', 'plane/circle-line', 'stat/normal',
]
const SOLID_CHIP_IDS = [
  'solid/cube', 'solid/cuboid', 'solid/prism', 'solid/pyramid', 'solid/tetra',
  'solid/cylinder', 'solid/cone', 'solid/frustum', 'solid/sphere', 'solid/cube-section', 'solid/cube-net',
]

const tool = ref<Tool>('pen')
const color = ref('#4f46e5')
const penW = ref(3)
const records = ref<V3DrawRecord[]>([...props.initialRecords])
const selectedRecId = ref<string | null>(null)
const showProps = ref(true)
const planeChips = computed(() => PLANE_CHIP_IDS.map((id) => FIGURE_PRESETS.find((p) => p.id === id)).filter(Boolean) as FigurePresetDef[])
const solidChips = computed(() => SOLID_CHIP_IDS.map((id) => FIGURE_PRESETS.find((p) => p.id === id)).filter(Boolean) as FigurePresetDef[])
const selectedRec = computed(() => records.value.find((r) => r.id === selectedRecId.value) ?? null)
const selPresetDef = computed(() => {
  const r = selectedRec.value
  return r?.kind === 'preset' ? FIGURE_PRESETS.find((p) => p.id === r.preset_id) ?? null : null
})
const selParams = computed(() => selPresetDef.value?.params ?? [])
const selToggles = computed(() => selPresetDef.value?.toggles ?? [])

const hintOf = computed(() => TOOLS.find((t) => t.id === tool.value)?.tip || '')

const KIND_LABEL: Record<V3DrawRecord['kind'], string> = {
  pen: '手绘曲线', line: '直线', circle: '圆', polygon: '多边形',
  point: '点', text: '文字', preset: '规整图形', geomdoc: '立体几何',
}
function recLabel(r: V3DrawRecord): string {
  if (r.kind === 'preset') return selPresetIdName(r.preset_id)
  return KIND_LABEL[r.kind]
}
function selPresetIdName(id: string): string {
  return FIGURE_PRESETS.find((p) => p.id === id)?.name || id
}
const extentOf = (presetId: string) => FIGURE_PRESETS.find((p) => p.id === presetId)?.extent

/* ---------- JSXGraph 画布 ---------- */
const host = ref<HTMLElement | null>(null)
const textIn = ref<HTMLInputElement | null>(null)
let board: any = null
let rebuildTimer: number | undefined
const elsOf = new Map<string, any[]>()
let selBoxEls: any[] = []
let polyPreviewEls: any[] = []

function toUser(e: PointerEvent | MouseEvent): Pt {
  const rect = host.value!.getBoundingClientRect()
  const [xmin, ymax, xmax, ymin] = board.getBoundingBox()
  return [
    xmin + ((e.clientX - rect.left) / Math.max(1, rect.width)) * (xmax - xmin),
    ymax - ((e.clientY - rect.top) / Math.max(1, rect.height)) * (ymax - ymin),
  ]
}

function buildBoard() {
  if (!host.value) return
  if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null }
  elsOf.clear()
  selBoxEls = []
  polyPreviewEls = []
  host.value.innerHTML = ''
  try {
    board = JXG.JSXGraph.initBoard(host.value, {
      boundingbox: [-8, 6, 8, -6],
      axis: true,
      grid: true,
      keepaspectratio: true,
      showNavigation: false,
      showCopyright: false,
      registerEvents: false,
      pan: { enabled: false },
      zoom: { factorX: 1, factorY: 1, wheel: false },
      defaultAxes: {
        x: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 11 } } },
        y: { strokeColor: '#8b95a7', ticks: { strokeColor: '#c3cad6', label: { fontSize: 11 } } },
      },
    })
    for (const r of records.value) buildRecord(r)
    board.update()
    drawSelBox()
  } catch { /* 渲染失败静默 */ }
}

/** 建一条记录：坐标 + offset 应用（preset 走平移代理），并把产生的 JSXGraph 元素挂到 elsOf */
function buildRecord(r: V3DrawRecord) {
  if (!board) return
  const [dx, dy] = r.offset ?? [0, 0]
  const attr = { strokeColor: r.color, strokeWidth: r.width, highlight: false, fixed: true }
  const before = new Set(Object.keys(board.objects || {}))
  switch (r.kind) {
    case 'pen':
      board.create('curve', [r.pts.map((p) => p[0] + dx), r.pts.map((p) => p[1] + dy)], attr)
      break
    case 'line':
      board.create('segment', [[r.a[0] + dx, r.a[1] + dy], [r.b[0] + dx, r.b[1] + dy]], attr)
      break
    case 'circle':
      board.create('circle', [[r.c[0] + dx, r.c[1] + dy], r.r], attr)
      break
    case 'polygon':
      board.create('polygon', r.verts.map((v) => [v[0] + dx, v[1] + dy]), {
        ...attr, fillColor: 'none', fillOpacity: 0,
        vertices: { visible: false, fixed: true },
        borders: { strokeColor: r.color, strokeWidth: r.width, highlight: false, fixed: true },
      })
      break
    case 'point':
      board.create('point', [r.pos[0] + dx, r.pos[1] + dy], { ...attr, size: 2.5, fillColor: r.color, name: '' })
      break
    case 'text':
      board.create('text', [r.pos[0] + dx, r.pos[1] + dy, r.text], { fontSize: 15, color: r.color, anchorX: 'middle', cssStyle: 'font-weight:600', fixed: true, highlight: false })
      break
    case 'preset': {
      const def = FIGURE_PRESETS.find((p) => p.id === r.preset_id)
      if (def) def.build({ board: offsetBoard(board, dx, dy), p: r.params, t: r.toggles ?? {} })
      break
    }
  }
  const els: any[] = []
  for (const k of Object.keys(board.objects || {})) {
    if (!before.has(k)) els.push(board.objects[k])
  }
  elsOf.set(r.id, els)
}

function removeEls(id: string) {
  for (const el of elsOf.get(id) || []) { try { board?.removeObject(el) } catch { /* ignore */ } }
  elsOf.delete(id)
}

/** 元素级重建：拖动/调参只重建这一条记录，其余不动（流畅不闪烁） */
function rebuildOne(id: string) {
  const r = records.value.find((x) => x.id === id)
  if (!r || !board) return
  removeEls(id)
  buildRecord(r)
  board.update()
  drawSelBox()
}
let oneTimer: number | undefined
function scheduleOne(id: string) {
  if (oneTimer) window.clearTimeout(oneTimer)
  oneTimer = window.setTimeout(() => rebuildOne(id), 16)
}
function scheduleRebuild() {
  if (rebuildTimer) window.clearTimeout(rebuildTimer)
  rebuildTimer = window.setTimeout(buildBoard, 30)
}

/* ---------- 选中框（金色虚线包围框，非 records 一部分） ---------- */
function drawSelBox() {
  for (const el of selBoxEls) { try { board?.removeObject(el) } catch { /* ignore */ } }
  selBoxEls = []
  const r = selectedRec.value
  if (!r || !board) return
  const bb = drawRecordBbox(r, r.kind === 'preset' ? extentOf(r.preset_id) : undefined)
  if (!bb) return
  const [x0, y0, x1, y1] = bb
  const pad = 0.22
  const corners: Pt[] = [
    [x0 - pad, y1 + pad], [x1 + pad, y1 + pad], [x1 + pad, y0 - pad], [x0 - pad, y0 - pad],
  ]
  selBoxEls.push(board.create('polygon', corners, {
    strokeColor: '#0891b2', strokeWidth: 1.6, dash: 2,
    fillColor: 'rgba(6,182,212,0.05)', fillOpacity: 1,
    vertices: { visible: false }, highlight: false, fixed: true,
  }))
}

function select(id: string | null) {
  selectedRecId.value = id
  if (id) showProps.value = true
  drawSelBox()
}

function removeRecord(id: string) {
  removeEls(id)
  records.value = records.value.filter((r) => r.id !== id)
  if (selectedRecId.value === id) select(null)
}
function removeSelected() {
  if (!selectedRecId.value) return
  removeRecord(selectedRecId.value)
}

/* ---------- 记录变更（结构化红线：全部进 records） ---------- */
watch(records, () => emit('changed', records.value), { deep: true })

function pushRecord(r: V3DrawRecord) {
  records.value.push(r)
  if (board) { buildRecord(r); board.update() }
}

function undo() {
  const last = records.value[records.value.length - 1]
  if (!last) return
  removeEls(last.id)
  records.value.pop()
  if (selectedRecId.value === last.id) select(null)
}

function clearAll() {
  for (const r of records.value) removeEls(r.id)
  records.value = []
  select(null)
}

function addPreset(p: FigurePresetDef) {
  const r: V3DrawRecord = { id: drawRecId(), kind: 'preset', preset_id: p.id, params: defParams(p), color: color.value, width: penW.value }
  pushRecord(r)
  select(r.id)
}

function defParams(p: FigurePresetDef): Record<string, number> {
  const o: Record<string, number> = {}
  for (const sp of p.params) o[sp.key] = sp.def
  return o
}

/* ---------- 选中对象属性编辑（即时重渲染） ---------- */
function setSelParam(key: string, v: number) {
  const r = selectedRec.value
  if (r?.kind !== 'preset') return
  r.params = { ...r.params, [key]: v }
  rebuildOne(r.id)
}
function setSelToggle(key: string, v: boolean) {
  const r = selectedRec.value
  if (r?.kind !== 'preset') return
  r.toggles = { ...(r.toggles || {}), [key]: v }
  rebuildOne(r.id)
}
function setSelText(v: string) {
  const r = selectedRec.value
  if (r?.kind !== 'text') return
  r.text = v
  rebuildOne(r.id)
}
function setSelCircleR(v: number) {
  const r = selectedRec.value
  if (r?.kind !== 'circle') return
  r.r = Math.max(0.1, v)
  rebuildOne(r.id)
}
function setSelLinePt(pt: 'a' | 'b', axis: 0 | 1, raw: string) {
  const r = selectedRec.value
  if (r?.kind !== 'line') return
  const v = Number(raw)
  if (!Number.isFinite(v)) return
  const next: [number, number] = [r[pt][0], r[pt][1]]
  next[axis] = v
  r[pt] = next
  rebuildOne(r.id)
}
const fnum = (v: number) => Math.round(v * 100) / 100
function setSelColor(c: string) {
  const r = selectedRec.value
  if (!r) return
  r.color = c
  rebuildOne(r.id)
}
function setSelWidth(w: number) {
  const r = selectedRec.value
  if (!r) return
  r.width = w
  rebuildOne(r.id)
}

/* ---------- 绘制交互 ---------- */
let drawing:
  | { mode: 'pen'; pts: Pt[] }
  | { mode: 'line'; a: Pt; b: Pt }
  | { mode: 'circle'; c: Pt; r: number }
  | null = null
let drag: { id: string; startPt: Pt; startOff: Pt } | null = null
let tmpEl: any = null
const polyPts = ref<Pt[]>([])
const textDraft = ref<{ pos: Pt; val: string; editId?: string } | null>(null)
const textPosPx = ref({ x: 0, y: 0 })

const textStyle = computed(() => ({ left: textPosPx.value.x + 'px', top: textPosPx.value.y + 'px' }))

function setTool(t: Tool) {
  tool.value = t
  polyPts.value = []
  textDraft.value = null
  dropPolyPreview()
  if (drawing) { dropTmp(); drawing = null }
}

function dropTmp() {
  if (tmpEl) { try { board.removeObject(tmpEl) } catch { /* ignore */ } tmpEl = null }
}

function dropPolyPreview() {
  for (const el of polyPreviewEls) { try { board?.removeObject(el, true) } catch { /* ignore */ } }
  polyPreviewEls = []
}

function updatePolyPreview() {
  dropPolyPreview()
  if (!polyPts.value.length) return
  polyPreviewEls.push(board.create('curve', [
    [...polyPts.value.map((p) => p[0]), polyPts.value[0][0]],
    [...polyPts.value.map((p) => p[1]), polyPts.value[0][1]],
  ], { strokeColor: color.value, strokeWidth: penW.value, dash: 2, highlight: false, fixed: true }))
  for (const p of polyPts.value) {
    polyPreviewEls.push(board.create('point', p, { size: 2, name: '', fillColor: '#0891b2', strokeColor: '#0891b2', fixed: true, highlight: false }))
  }
  board.update()
}

function onDown(e: PointerEvent) {
  if (e.button !== 0 || !board) return
  const p = toUser(e)
  if (tool.value === 'select') {
    const id = hitDrawRecord(records.value, p, extentOf)
    select(id)
    if (id) {
      const r = records.value.find((x) => x.id === id)
      if (r) {
        drag = { id, startPt: p, startOff: r.offset ?? [0, 0] }
        ;(host.value as any)?.setPointerCapture?.(e.pointerId)
      }
    }
    return
  }
  if (tool.value === 'pen') {
    drawing = { mode: 'pen', pts: [p] }
    tmpEl = board.create('curve', [[p[0]], [p[1]]], { strokeColor: color.value, strokeWidth: penW.value, highlight: false, fixed: true })
    ;(host.value as any)?.setPointerCapture?.(e.pointerId)
  } else if (tool.value === 'line') {
    drawing = { mode: 'line', a: p, b: p }
    tmpEl = board.create('segment', [p, p], { strokeColor: color.value, strokeWidth: penW.value, highlight: false, fixed: true })
    ;(host.value as any)?.setPointerCapture?.(e.pointerId)
  } else if (tool.value === 'circle') {
    drawing = { mode: 'circle', c: p, r: 0 }
    tmpEl = board.create('circle', [p, 0.001], { strokeColor: color.value, strokeWidth: penW.value, highlight: false, fixed: true })
    ;(host.value as any)?.setPointerCapture?.(e.pointerId)
  } else if (tool.value === 'polygon') {
    const last = polyPts.value[polyPts.value.length - 1]
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) > 0.2) polyPts.value.push(p)
    updatePolyPreview()
  } else if (tool.value === 'point') {
    pushRecord({ id: drawRecId(), kind: 'point', pos: p, color: color.value, width: penW.value })
  } else if (tool.value === 'text') {
    openTextDraft(p, e)
  }
}

function openTextDraft(pos: Pt, e: PointerEvent | MouseEvent, editId?: string, val = '') {
  const rect = host.value!.getBoundingClientRect()
  textPosPx.value = { x: e.clientX - rect.left, y: e.clientY - rect.top - 18 }
  textDraft.value = { pos, val, editId }
  nextTick(() => textIn.value?.focus())
}

function onMove(e: PointerEvent) {
  if (drag && board) {
    const p = toUser(e)
    const r = records.value.find((x) => x.id === drag!.id)
    if (r) {
      r.offset = [drag!.startOff[0] + (p[0] - drag!.startPt[0]), drag!.startOff[1] + (p[1] - drag!.startPt[1])]
      scheduleOne(r.id)
    }
    return
  }
  if (!drawing || !board) return
  const p = toUser(e)
  if (drawing.mode === 'pen') {
    drawing.pts.push(p)
    tmpEl.dataX = drawing.pts.map((q) => q[0])
    tmpEl.dataY = drawing.pts.map((q) => q[1])
    board.update()
  } else if (drawing.mode === 'line') {
    drawing.b = p
    tmpEl.point2.setPosition(JXG.COORDS_BY_USER, p[0], p[1])
    board.update()
  } else if (drawing.mode === 'circle') {
    drawing.r = Math.hypot(p[0] - drawing.c[0], p[1] - drawing.c[1])
    tmpEl.setRadius(Math.max(0.001, drawing.r))
    board.update()
  }
}

function onUp() {
  if (drag) {
    drag = null
    if (oneTimer) { window.clearTimeout(oneTimer); oneTimer = undefined }
    if (selectedRecId.value) rebuildOne(selectedRecId.value)
    return
  }
  if (!drawing) return
  const d = drawing
  drawing = null
  dropTmp()
  if (d.mode === 'pen') {
    const pts = d.pts
    if (pts.length < 2 || Math.hypot(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) < 0.08) return
    if (isNearlyLine(pts)) {
      pushRecord({ id: drawRecId(), kind: 'line', a: pts[0], b: pts[pts.length - 1], color: color.value, width: penW.value })
    } else {
      const circ = isNearlyCircle(pts)
      if (circ) pushRecord({ id: drawRecId(), kind: 'circle', c: circ.c, r: circ.r, color: color.value, width: penW.value })
      else pushRecord({ id: drawRecId(), kind: 'pen', pts: simplifyPen(pts), color: color.value, width: penW.value })
    }
  } else if (d.mode === 'line') {
    if (Math.hypot(d.b[0] - d.a[0], d.b[1] - d.a[1]) > 0.15) pushRecord({ id: drawRecId(), kind: 'line', a: d.a, b: d.b, color: color.value, width: penW.value })
  } else if (d.mode === 'circle') {
    if (d.r > 0.15) pushRecord({ id: drawRecId(), kind: 'circle', c: d.c, r: d.r, color: color.value, width: penW.value })
  }
}

function onDbl(e: MouseEvent) {
  if (tool.value === 'polygon') {
    if (polyPts.value.length < 3) { polyPts.value = []; dropPolyPreview(); return }
    pushRecord({ id: drawRecId(), kind: 'polygon', verts: polyPts.value.slice(), color: color.value, width: penW.value })
    polyPts.value = []
    dropPolyPreview()
    return
  }
  if (tool.value === 'select' && board) {
    const p = toUser(e)
    const id = hitDrawRecord(records.value, p, extentOf)
    const r = id ? records.value.find((x) => x.id === id) : null
    if (r?.kind === 'text') {
      select(r.id)
      openTextDraft(r.pos, e, r.id, r.text)
    }
  }
}

function commitText() {
  const t = textDraft.value
  textDraft.value = null
  if (!t) return
  const val = t.val.trim()
  if (t.editId) {
    const r = records.value.find((x) => x.id === t.editId)
    if (r && r.kind === 'text') {
      if (val) { r.text = val; rebuildOne(r.id) } else removeRecord(r.id)
    }
    return
  }
  if (!val) return
  pushRecord({ id: drawRecId(), kind: 'text', pos: t.pos, text: val, color: color.value, width: penW.value })
}

/* ---------- 键盘：Delete 删除 / Esc 取消（Excalidraw 规范） ---------- */
function onKey(ev: KeyboardEvent) {
  const tag = (ev.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (ev.target as HTMLElement)?.isContentEditable) return
  if (ev.key === 'Delete' || ev.key === 'Backspace') {
    if (selectedRecId.value) { ev.preventDefault(); removeSelected() }
  } else if (ev.key === 'Escape') {
    if (textDraft.value) { textDraft.value = null; return }
    if (selectedRecId.value) { select(null); ev.preventDefault() }
  }
}

/* ---------- 插入与导出 ---------- */
function onInsert() {
  if (!records.value.length || !host.value) return
  const svg = host.value.querySelector('svg') as SVGSVGElement | null
  if (!svg) return
  const snap = boardSvgToDataUrl(svg)
  emit('insert', { type: 'image', src: snap.src, records: JSON.parse(JSON.stringify(records.value)), aspect: snap.aspect })
}

function describe(): { kind: 'free'; thumb: string; records: V3DrawRecord[] } | null {
  if (!records.value.length) return null
  const svg = host.value?.querySelector('svg') as SVGSVGElement | null
  return {
    kind: 'free',
    thumb: svg ? boardSvgToDataUrl(svg).src : '',
    records: JSON.parse(JSON.stringify(records.value)),
  }
}

watch(() => props.initialRecords, (rs) => {
  if (rs && rs.length) records.value = JSON.parse(JSON.stringify(rs))
}, { immediate: true })

onMounted(() => {
  window.addEventListener('keydown', onKey)
  nextTick(buildBoard)
})
onActivated(() => nextTick(buildBoard))
onDeactivated(() => select(null))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (rebuildTimer) window.clearTimeout(rebuildTimer)
  if (oneTimer) window.clearTimeout(oneTimer)
  if (board) { try { JXG.JSXGraph.freeBoard(board) } catch { /* ignore */ } board = null }
})

function loadRecords(rs: V3DrawRecord[]) {
  records.value = JSON.parse(JSON.stringify(rs || []))
  select(null)
  scheduleRebuild()
}
defineExpose({ loadRecords, describe })
</script>

<style scoped>
.mxd-free { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.mxd-free__bar {
  display: flex; align-items: center; gap: 12px; padding: 8px 14px;
  border-bottom: 1px solid var(--tv3-line2); flex-wrap: wrap;
}
.mxd-free__colors { display: flex; gap: 5px; }
.mxd-free__color {
  width: 20px; height: 20px; border-radius: 50%; border: 2px solid #fff;
  box-shadow: 0 0 0 1px var(--tv3-line); cursor: pointer; padding: 0;
}
.mxd-free__color.is-active { box-shadow: 0 0 0 2.5px var(--tv3-gold); }
.mxd-free__presets {
  display: flex; align-items: center; gap: 8px; padding: 7px 14px;
  border-bottom: 1px solid var(--tv3-line2); background: #fbfcfe; flex-wrap: wrap;
}
.mxd-free__pgroup { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.mxd-free__presets-lbl { font-size: 12px; color: var(--tv3-ink3); font-weight: 600; }
.mxd-free__preset {
  display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px 3px 4px;
  border: 1px solid var(--tv3-line); border-radius: 8px; background: #fff;
  font-size: 12px; cursor: pointer; color: var(--tv3-ink2);
}
.mxd-free__preset:hover { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.mxd-free__preset-thumb { width: 34px; height: 23px; display: inline-block; }
.mxd-free__stage { position: relative; flex: 1; min-height: 0; }
.mxd-free__board { width: 100%; height: 100%; touch-action: none; }
.mxd-free__board--pen, .mxd-free__board--line, .mxd-free__board--circle, .mxd-free__board--polygon, .mxd-free__board--point, .mxd-free__board--text { cursor: crosshair; }
.mxd-free__board--select { cursor: default; }
.mxd-free__board :deep(.JXGtext) { font-family: var(--tv3-font-num); }
.mxd-free__hint {
  position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
  font-size: 11.5px; color: var(--tv3-ink3); background: rgba(255, 255, 255, 0.88);
  padding: 3px 12px; border-radius: 999px; pointer-events: none; white-space: nowrap;
  border: 1px solid var(--tv3-line); max-width: 92%; overflow: hidden; text-overflow: ellipsis;
}
.mxd-free__count {
  position: absolute; top: 8px; right: 10px; font-size: 11px; color: var(--tv3-gold-deep);
  background: var(--tv3-gold-soft); padding: 3px 10px; border-radius: 999px; pointer-events: none;
}
.mxd-free__selinfo {
  position: absolute; top: 36px; right: 10px; font-size: 11px; color: var(--tv3-ink2);
  background: #fff; padding: 3px 10px; border-radius: 999px; pointer-events: none;
  border: 1px dashed var(--tv3-gold);
}
.mxd-free__props {
  position: absolute; top: 36px; right: 10px; width: 248px; max-height: calc(100% - 60px);
  overflow-y: auto; background: #fff; border: 1px solid var(--tv3-line);
  border-radius: 12px; padding: 12px 14px; box-shadow: var(--tv3-shadow-md); z-index: 9;
}
.mxd-free__props-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.mxd-free__props-name { font-size: 13px; font-weight: 700; color: var(--tv3-ink); }
.mxd-free__props-x {
  border: none; background: none; cursor: pointer; color: var(--tv3-ink3);
  font-size: 13px; padding: 2px 4px; line-height: 1;
}
.mxd-free__props-x:hover { color: var(--tv3-ink); }
.mxd-free__props-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.mxd-free__props-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.mxd-free__props-grid label {
  display: flex; flex-direction: column; gap: 3px; font-size: 11px;
  color: var(--tv3-ink3); font-weight: 600;
}
.mxd-free__props-grid input { font-size: 12px; padding: 3px 6px; }
.mxd-free__tg {
  display: inline-flex; align-items: center; gap: 5px; font-size: 12px;
  color: var(--tv3-ink2); cursor: pointer; margin-top: 5px; margin-right: 12px;
}
.mxd-free__props-mini {
  position: absolute; top: 36px; right: 10px; font-size: 11.5px; cursor: pointer;
  background: #fff; border: 1px solid var(--tv3-line); border-radius: 999px;
  padding: 3px 12px; color: var(--tv3-ink2); z-index: 9;
}
.mxd-free__props-mini:hover { border-color: var(--tv3-gold); color: var(--tv3-gold-deep); }
.mxd-free__textinput { position: absolute; z-index: 10; }
.mxd-free__textinput input { width: 180px; font-size: 13px; }
.mxd-free__foot {
  display: flex; align-items: center; gap: 10px; padding: 9px 14px;
  border-top: 1px solid var(--tv3-line2);
}
.mxd-free__tip { font-size: 11.5px; color: var(--tv3-ink3); }
</style>
