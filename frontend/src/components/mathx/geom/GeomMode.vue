<template>
  <div class="mg-wrap" data-testid="mg-root">
    <!-- 工具行：骨架 + 工具 + 历史 -->
    <div class="mg-bar">
      <div class="tv3-seg">
        <button
          v-for="t in TOOLS" :key="t.id"
          class="tv3-seg__btn" :class="{ 'is-active': tool === t.id }"
          :title="t.tip" :data-testid="`mg-tool-${t.id}`"
          @click="setTool(t.id)"
        >{{ t.icon }} {{ t.label }}</button>
      </div>
      <div class="tv3-card__spacer" />
      <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mg-undo" :disabled="!hist.undo.length" @click="doUndo">↶ 撤销</button>
      <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mg-redo" :disabled="!hist.redo.length" @click="doRedo">↷ 重做</button>
      <button class="tv3-btn tv3-btn--sm" type="button" data-testid="mg-reset-view" title="复位视角" @click="resetView">⌂ 视角</button>
      <button class="tv3-btn tv3-btn--sm" type="button" :disabled="!doc.objects.length" data-testid="mg-clear" @click="clearAll">清空</button>
    </div>

    <div class="mg-bar2">
      <span class="mg-bar2__lbl">骨架</span>
      <button
        v-for="k in SKELETONS" :key="k.id"
        class="mg-chip" :title="k.tip" :data-testid="`mg-skel-${k.id}`"
        @click="addSkeleton(k.id)"
      >{{ k.label }}</button>
      <span class="mg-bar2__sep" />
      <span class="mg-bar2__hint">{{ hintOf }}</span>
    </div>

    <div class="mg-main">
      <!-- 画布 -->
      <div
        ref="host" class="mg-stage"
        data-testid="mg-stage"
        @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp"
        @wheel.prevent="onWheel"
      >
        <div class="mg-svg" v-html="svgHtml" />
        <div v-if="!doc.objects.length" class="mg-empty">
          点骨架快速起稿，或直接粘贴构造描述
        </div>
      </div>

      <!-- 右栏：构造脚本 + 对象属性 -->
      <aside class="mg-side">
        <div class="mg-side__sec">
          <div class="tv3-form-label" style="margin: 0 0 6px">构造描述（每句一行，确定性执行）</div>
          <textarea
            v-model="script" class="tv3-input mg-script" rows="5"
            data-testid="mg-script"
            placeholder="例如：&#10;平行六面体 ABCD-A1B1C1D1&#10;E为A1D1的中点&#10;F为BC1与B1C的交点&#10;连接DB1、BE、AF&#10;DB1、BE、AF画虚线"
          />
          <button
            class="tv3-btn tv3-btn--primary tv3-btn--sm" style="width: 100%; margin-top: 8px"
            type="button" data-testid="mg-run" @click="runScript"
          >✦ 解析并构造</button>
          <div v-if="lastResult" class="mg-result" data-testid="mg-result">
            <div v-for="(a, i) in lastResult.applied" :key="'a' + i" class="mg-result__ok">✓ {{ a }}</div>
            <div v-for="(u, i) in lastResult.uncovered" :key="'u' + i" class="mg-result__miss">⚠ {{ u }}</div>
          </div>
        </div>

        <div v-if="selPoint" class="mg-side__sec" data-testid="mg-props-point">
          <div class="tv3-form-label" style="margin: 0 0 6px">点 {{ selPoint.label }}</div>
          <label class="mg-field">
            名称
            <input class="tv3-input" style="font-size: 12.5px" :value="selPoint.label" data-testid="mg-point-label"
              @change="renameSel(($event.target as HTMLInputElement).value)">
          </label>
          <template v-if="selPoint.def.kind === 'onEdge'">
            <div class="mx-slider" style="margin-top: 8px">
              <span class="mx-slider__label">沿棱位置</span>
              <input type="range" min="0" max="1" step="0.01" :value="selPoint.def.t" data-testid="mg-point-t"
                @input="setEdgeT(Number(($event.target as HTMLInputElement).value))">
              <span class="mx-slider__value">{{ (selPoint.def.t).toFixed(2) }}</span>
            </div>
            <div class="mg-note">拖动点会保持在棱上；比例随 t 联动</div>
          </template>
          <div v-if="selPoint.def.kind === 'ratio'" class="mg-note">比例 {{ selPoint.def.m }}:{{ selPoint.def.n }}（可在描述里改）</div>
          <button class="mg-del" type="button" data-testid="mg-point-del" @click="deleteSel">🗑 删除（含依赖）</button>
        </div>

        <div v-else-if="selSegId" class="mg-side__sec" data-testid="mg-props-seg">
          <div class="tv3-form-label" style="margin: 0 0 6px">线段 {{ segLabel }}</div>
          <div class="tv3-seg" style="margin-bottom: 8px">
            <button class="tv3-seg__btn" :class="{ 'is-active': segStyle === 'auto' }" @click="setSegStyle('auto')">自动虚实</button>
            <button class="tv3-seg__btn" :class="{ 'is-active': segStyle === 'solid' }" @click="setSegStyle('solid')">实线</button>
            <button class="tv3-seg__btn" :class="{ 'is-active': segStyle === 'dashed' }" @click="setSegStyle('dashed')">虚线</button>
          </div>
          <button class="mg-del" type="button" data-testid="mg-seg-del" @click="deleteSel">🗑 删除线段</button>
        </div>

        <div v-else class="mg-side__sec mg-side__sec--muted">
          <div class="mg-note">
            画在背面的线会自动变虚线；拖动空白处旋转视角，滚轮缩放。<br>
            构造出的点带依赖：改顶点/拖动棱上点，相关线段截面实时重算。
          </div>
        </div>
      </aside>
    </div>

    <!-- 底部 -->
    <div class="mg-foot">
      <span class="mg-foot__tip">{{ status }}</span>
      <div class="tv3-card__spacer" />
      <button class="tv3-btn tv3-btn--primary" type="button" :disabled="!doc.objects.length" data-testid="mg-insert" @click="onInsert">插入课件</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * GeomMode —— 立体几何构造模式（阶段 C，回应 docs/draw-reality 阶段A FUNDAMENTAL FAIL）
 * 模型驱动：doc.objects 是唯一持久层；场景与 SVG 每帧重算。
 * 红线：无网格无坐标轴；标签进 SVG；虚线有语义（凸体自动 + 手动覆盖）；
 *      构造描述确定性解析，未识别句如实披露（不做假装 AI）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  type GeomDoc, type GPoint, type GId, type GeomHistory,
  emptyDoc, newHistory, pushHistory, undo as mUndo, redo as mRedo,
  addPointOnEdge, addMidpoint, addSegment, addIntersect, addSection, addVector,
  removeCascade, renamePoint, pointPos,
} from './model'
import { applyConstruction, skeleton } from './dsl'
import { computeScene, sceneToSvg, layoutOf, type Scene } from './render'
import type { V3DrawInsert } from '../draw/drawCore'

const emit = defineEmits<{ (e: 'insert', payload: V3DrawInsert): void }>()

type Tool = 'select' | 'edgePoint' | 'midpoint' | 'connect' | 'intersect' | 'section' | 'vector'
const TOOLS: { id: Tool; icon: string; label: string; tip: string }[] = [
  { id: 'select', icon: '↖', label: '选择', tip: '点选 → 拖动（点沿棱/水平面动）→ 空白处拖动旋转视角 → Delete 删除' },
  { id: 'edgePoint', icon: '·', label: '棱上点', tip: '点击任意棱/线段 → 在点击处建依附点（可拖、可调比例）' },
  { id: 'midpoint', icon: '⌖', label: '中点', tip: '依次点击两个点 → 构造中点' },
  { id: 'connect', icon: '／', label: '连接', tip: '依次点击两个点 → 连线段（自动虚实）' },
  { id: 'intersect', icon: '✕', label: '交点', tip: '依次点击两条线段 → 构造交点（不共面会如实提示）' },
  { id: 'section', icon: '◇', label: '截面', tip: '依次点击三个点 → 作截面' },
  { id: 'vector', icon: '→', label: '向量', tip: '依次点击两个点 → 作向量（带箭头）' },
]
const SKELETONS = [
  { id: 'parallelepiped', label: '平行六面体', tip: '斜平行六面体（可拖顶点变形）' },
  { id: 'cuboid', label: '长方体', tip: '长方体' },
  { id: 'cube', label: '正方体', tip: '正方体' },
  { id: 'pyramid4', label: '四棱锥', tip: '底面四边形 + 顶点 P' },
  { id: 'tetra', label: '三棱锥', tip: '四面体' },
  { id: 'prism3', label: '三棱柱', tip: '直三棱柱' },
  { id: 'corner', label: '墙角模型', tip: 'PA/PB/PC 两两垂直三棱锥 + 外接球' },
  { id: 'burger', label: '汉堡模型', tip: '直三棱柱 + 外接球（O₁O₂ 为两底外接圆圆心）' },
  { id: 'cone', label: '球内接圆锥', tip: '圆锥内接于球：顶点在球顶，底面圆在球面' },
  { id: 'planes', label: '平行平面', tip: '两平行水平板块 α ∥ β' },
]

const doc = ref<GeomDoc>(emptyDoc())
const hist = ref<GeomHistory>(newHistory())
const tool = ref<Tool>('select')
const script = ref('')
const lastResult = ref<{ applied: string[]; uncovered: string[] } | null>(null)
const status = ref('就绪')
const host = ref<HTMLElement | null>(null)

let scene: Scene = { pts: [], segs: [], sections: [], vectors: [], spheres: [], shapes: [], plates: [], bbox: [-100, -80, 100, 80] }
let layout = { w: 800, h: 520, ox: 46, oy: 46 }
const svgHtml = ref('')

const selId = ref<string | null>(null)
const selPoint = computed(() => doc.value.objects.find((o) => o.id === selId.value && (o as GPoint).def !== undefined) as GPoint | null)
const selSegId = computed(() => {
  const o = doc.value.objects.find((x) => x.id === selId.value) as { style?: string } | undefined
  return o && o.style !== undefined ? selId.value : null
})
const selSeg = computed(() => doc.value.objects.find((o) => o.id === selId.value) as { a: GId; b: GId; style: 'auto' | 'solid' | 'dashed' } | null)
const segStyle = computed(() => selSeg.value?.style || 'auto')
const segLabel = computed(() => {
  if (!selSeg.value) return ''
  const la = labelOf(selSeg.value.a), lb = labelOf(selSeg.value.b)
  return la + lb
})
function labelOf(id: GId): string {
  const p = doc.value.objects.find((o) => o.id === id) as GPoint | undefined
  return p?.label || '?'
}

const hintOf = computed(() => TOOLS.find((t) => t.id === tool.value)?.tip || '')

function rerender() {
  scene = computeScene(doc.value)
  layout = layoutOf(scene)
  const r = sceneToSvg(scene, { selectedId: selId.value })
  svgHtml.value = r.svg
}

/* ---------- 骨架 / 构造 ---------- */
function addSkeleton(kind: string) {
  pushHistory(doc.value, hist.value)
  const label = skeleton(kind, doc.value)
  selId.value = null
  status.value = `已建 ${label}——顶点可直接拖动变形，棱上可加依附点`
  rerender()
}

function runScript() {
  if (!script.value.trim()) return
  pushHistory(doc.value, hist.value)
  const r = applyConstruction(doc.value, script.value)
  lastResult.value = r
  status.value = r.uncovered.length
    ? `已执行 ${r.applied.length} 步，${r.uncovered.length} 句未识别（见右侧披露）`
    : `已执行 ${r.applied.length} 步构造`
  selId.value = null
  rerender()
}

/* ---------- 交互 ---------- */
let drag: { mode: 'orbit'; x: number; y: number } | { mode: 'pt'; id: GId } | null = null
let pending: GId[] = []          // 两步/三步工具的待选点
let pendingSegs: string[] = []   // 交点工具待选线段（scene seg id）

function toScene(e: PointerEvent | MouseEvent | WheelEvent): [number, number] {
  // SVG 在舞台内居中缩放显示，命中换算必须用 svg 自身的 rect
  const svgEl = host.value?.querySelector("svg")
  const rect = (svgEl ?? host.value)!.getBoundingClientRect()
  const sx = (e.clientX - rect.left) * (layout.w / Math.max(1, rect.width))
  const sy = (e.clientY - rect.top) * (layout.h / Math.max(1, rect.height))
  return [sx - layout.ox, sy - layout.oy]
}

function setTool(t: Tool) {
  tool.value = t
  pending = []
  pendingSegs = []
}

function hitPoint(p: [number, number]): string | null {
  let best: { id: string; d: number } | null = null
  for (const pt of scene.pts) {
    const d = Math.hypot(pt.x - p[0], pt.y - p[1])
    if (d < 13 && (!best || d < best.d)) best = { id: pt.id, d }
  }
  return best?.id || null
}

function hitSeg(p: [number, number]): { id: string; a: GId; b: GId; t: number } | null {
  let best: { id: string; a: GId; b: GId; d: number; t: number } | null = null
  for (const s of scene.segs) {
    const dx = s.x2 - s.x1, dy = s.y2 - s.y1
    const L2 = dx * dx + dy * dy
    const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - s.x1) * dx + (p[1] - s.y1) * dy) / L2))
    const d = Math.hypot(s.x1 + t * dx - p[0], s.y1 + t * dy - p[1])
    if (d < 9 && (!best || d < best.d)) {
      const ends = segEnds(s.id)
      if (ends) best = { id: s.id, a: ends[0], b: ends[1], d, t }
    }
  }
  return best ? { id: best.id, a: best.a, b: best.b, t: best.t } : null
}

function onDown(e: PointerEvent) {
  if (e.button !== 0) return
  try { (host.value as any)?.setPointerCapture?.(e.pointerId) } catch { /* 合成事件无活动指针，忽略 */ }
  const p = toScene(e)
  if (tool.value === 'select') {
    const pid = hitPoint(p)
    if (pid) {
      selId.value = pid
      pushHistory(doc.value, hist.value)
      drag = { mode: 'pt', id: pid }
      return
    }
    const sg = hitSeg(p)
    if (sg && sg.id.includes(':') === false) {
      // 用户线段可选（solid 棱带冒号不可选整体）
      selId.value = sg.id
      rerender()
      return
    }
    selId.value = null
    pushHistory(doc.value, hist.value)
    drag = { mode: 'orbit', x: e.clientX, y: e.clientY }
    rerender()
    return
  }
  if (tool.value === 'edgePoint') {
    const sg = hitSeg(p)
    if (!sg) { status.value = '请点击棱或线段'; return }
    pushHistory(doc.value, hist.value)
    const pt = addPointOnEdge(doc.value, sg.a, sg.b, sg.t)
    selId.value = pt.id
    status.value = `已建棱上点 ${pt.label}（${labelOf(sg.a)}${labelOf(sg.b)} 上，可拖）`
    rerender()
    return
  }
  if (tool.value === 'connect' || tool.value === 'midpoint' || tool.value === 'vector' || tool.value === 'section') {
    const pid = hitPoint(p)
    if (!pid) { status.value = '请点击一个点'; return }
    pending.push(pid)
    const need = tool.value === 'section' ? 3 : 2
    if (pending.length < need) {
      status.value = `已选 ${pending.map(labelOf).join('、')}，再选 ${need - pending.length} 个点`
      return
    }
    pushHistory(doc.value, hist.value)
    try {
      if (tool.value === 'connect') {
        const seg = addSegment(doc.value, pending[0], pending[1])
        status.value = `已连接 ${labelOf(pending[0])}${labelOf(pending[1])}（可在右侧设虚实）`
        selId.value = seg.id
      } else if (tool.value === 'midpoint') {
        const mp = addMidpoint(doc.value, pending[0], pending[1])
        selId.value = mp.id
        status.value = `已建中点 ${mp.label}`
      } else if (tool.value === 'vector') {
        addVector(doc.value, pending[0], pending[1])
        status.value = `已作向量 ${labelOf(pending[0])}${labelOf(pending[1])}`
      } else {
        const r = addSection(doc.value, [pending[0], pending[1], pending[2]])
        if ('error' in r) { hist.value.undo.pop(); status.value = r.error }
        else status.value = '已作截面（金色）'
      }
    } catch (err) {
      hist.value.undo.pop()
      status.value = err instanceof Error ? err.message : '构造失败'
    }
    pending = []
    rerender()
    return
  }
  if (tool.value === 'intersect') {
    const sg = hitSeg(p)
    if (!sg) { status.value = '请点击一条线段'; return }
    if (!pendingSegs.includes(sg.id)) pendingSegs.push(sg.id)
    if (pendingSegs.length < 2) { status.value = '再选另一条线段'; return }
    const A = segEnds(pendingSegs[0]), B = segEnds(pendingSegs[1])
    if (!A || !B) { status.value = '线段解析失败'; pendingSegs = []; return }
    pushHistory(doc.value, hist.value)
    try {
      const pt = addIntersect(doc.value, [A[0], A[1]], [B[0], B[1]])
      selId.value = pt.id
      status.value = `已建交点 ${pt.label}`
    } catch (err) {
      hist.value.undo.pop()
      status.value = err instanceof Error ? err.message : '无法构造交点'
    }
    pendingSegs = []
    rerender()
    return
  }
}

/** scene seg id → [aId, bId] */
function segEnds(sceneId: string): [GId, GId] | null {
  if (sceneId.includes(':')) {
    const [sid, ei] = sceneId.split(':')
    const sol = doc.value.objects.find((o) => o.id === sid) as { verts?: GId[]; edges?: [number, number][] } | undefined
    if (!sol?.edges || !sol.verts) return null
    const e = sol.edges[Number(ei)]
    return e ? [sol.verts[e[0]], sol.verts[e[1]]] : null
  }
  const seg = doc.value.objects.find((o) => o.id === sceneId) as { a: GId; b: GId } | undefined
  return seg ? [seg.a, seg.b] : null
}

function onMove(e: PointerEvent) {
  if (!drag) return
  if (drag.mode === 'orbit') {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y
    drag.x = e.clientX; drag.y = e.clientY
    doc.value.camera.yaw += dx * 0.011
    doc.value.camera.pitch = Math.max(0.12, Math.min(1.45, doc.value.camera.pitch + dy * 0.008))
    rerender()
    return
  }
  if (drag.mode !== 'pt') return
  const dragPt = drag
  const pt = doc.value.objects.find((o) => o.id === dragPt.id && (o as GPoint).def !== undefined) as GPoint | undefined
  if (!pt) return
  const p = toScene(e)
  const def = pt.def
  if (def.kind === 'onEdge') {
    const sg = scene.segs.find((s) => {
      const ends = segEnds(s.id)
      return ends && ((ends[0] === def.a && ends[1] === def.b) || (ends[0] === def.b && ends[1] === def.a))
    })
    if (sg) {
      const dx = sg.x2 - sg.x1, dy = sg.y2 - sg.y1
      const L2 = dx * dx + dy * dy
      const t = L2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - sg.x1) * dx + (p[1] - sg.y1) * dy) / L2))
      def.t = t
    }
  } else if (def.kind === 'free') {
    // 水平面内拖动（z 不变）：屏幕位移 → 世界 Δx/Δy
    const dxy = screenToPlaneDelta(e)
    def.pos = [def.pos[0] + dxy[0], def.pos[1] + dxy[1], def.pos[2]]
  } else if (def.kind === 'sum') {
    // 拖动骨架派生顶点 = 改第三棱/基向量 → 整块立体变形
    const dxy = screenToPlaneDelta(e)
    def.vec = [def.vec[0] + dxy[0], def.vec[1] + dxy[1], def.vec[2]]
  }
  rerender()
}

function onUp() {
  drag = null
}

/** 屏幕位移 → 水平面（z=const）世界位移（逆轴测投影） */
function screenToPlaneDelta(e: PointerEvent): [number, number] {
  const cam = doc.value.camera
  const sy = Math.sin(cam.yaw), cy = Math.cos(cam.yaw)
  const sp = Math.sin(cam.pitch)
  const svgEl = host.value?.querySelector("svg")
  const rect = (svgEl ?? host.value)!.getBoundingClientRect()
  const k = layout.w / Math.max(1, rect.width)
  const dsx = (e.movementX * k) / cam.zoom
  const dsup = -(e.movementY * k) / cam.zoom
  const dyW = -dsup / (sp || 0.3)
  const dxW = (dsx + dyW * sy) / (cy || 0.3)
  return [dxW, dyW]
}

function onWheel(e: WheelEvent) {
  const cam = doc.value.camera
  cam.zoom = Math.max(24, Math.min(160, cam.zoom * (e.deltaY > 0 ? 0.92 : 1.08)))
  rerender()
}

/* ---------- 属性编辑 ---------- */
function renameSel(v: string) {
  if (!selPoint.value) return
  pushHistory(doc.value, hist.value)
  renamePoint(doc.value, selPoint.value.id, v)
  rerender()
}
function setEdgeT(t: number) {
  const pt = selPoint.value
  if (!pt || pt.def.kind !== 'onEdge') return
  pt.def.t = Math.max(0, Math.min(1, t))
  rerender()
}
function setSegStyle(s: 'auto' | 'solid' | 'dashed') {
  if (!selSeg.value) return
  pushHistory(doc.value, hist.value)
  selSeg.value.style = s
  rerender()
}
function deleteSel() {
  if (!selId.value) return
  pushHistory(doc.value, hist.value)
  const dead = removeCascade(doc.value, selId.value)
  status.value = `已删除 ${dead.length} 个对象（含依赖）`
  selId.value = null
  rerender()
}
function doUndo() { mUndo(doc.value, hist.value); selId.value = null; rerender() }
function doRedo() { mRedo(doc.value, hist.value); selId.value = null; rerender() }
function resetView() {
  pushHistory(doc.value, hist.value)
  doc.value.camera = { yaw: 0.62, pitch: 0.6, zoom: 60 }
  rerender()
}
function clearAll() {
  pushHistory(doc.value, hist.value)
  doc.value.objects = []
  lastResult.value = null
  selId.value = null
  status.value = '已清空'
  rerender()
}

/* ---------- 键盘 ---------- */
function onKey(ev: KeyboardEvent) {
  const tag = (ev.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || (ev.target as HTMLElement)?.isContentEditable) return
  if ((ev.key === 'Delete' || ev.key === 'Backspace') && selId.value) { ev.preventDefault(); deleteSel() }
  else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); ev.shiftKey ? doRedo() : doUndo() }
  else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'y') { ev.preventDefault(); doRedo() }
}

/* ---------- 插入 / 重开 / 存库 ---------- */
function docRecord() {
  return { id: 'geomdoc', kind: 'geomdoc', color: '#000', width: 0, doc: JSON.parse(JSON.stringify({ objects: doc.value.objects, camera: doc.value.camera, seq: doc.value.seq })) }
}

function onInsert() {
  if (!doc.value.objects.length) return
  const r = sceneToSvg(computeScene(doc.value), { padding: 40 })
  const payload: V3DrawInsert = {
    type: 'image',
    src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(r.svg),
    records: [docRecord()] as any,
    aspect: r.h / r.w,
  }
  emit('insert', payload)
}

function describe() {
  if (!doc.value.objects.length) return null
  const r = sceneToSvg(computeScene(doc.value), { padding: 40 })
  return {
    kind: 'free' as const,
    thumb: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(r.svg),
    records: [docRecord()] as any,
  }
}

function loadRecords(rs: any[]) {
  const first = Array.isArray(rs) ? rs[0] : null
  if (first && first.kind === 'geomdoc' && first.doc) {
    const d = JSON.parse(JSON.stringify(first.doc))
    doc.value.objects = d.objects || []
    doc.value.camera = d.camera || { yaw: 0.62, pitch: 0.6, zoom: 60 }
    doc.value.seq = d.seq || doc.value.seq
  } else {
    doc.value.objects = []
  }
  hist.value = newHistory()
  selId.value = null
  nextTick(rerender)
}

defineExpose({ describe, loadRecords })

onMounted(() => {
  window.addEventListener('keydown', onKey)
  nextTick(rerender)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.mg-wrap { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.mg-bar { display: flex; align-items: center; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--tv3-line2); flex-wrap: wrap; }
.mg-bar2 { display: flex; align-items: center; gap: 6px; padding: 7px 14px; border-bottom: 1px solid var(--tv3-line2); background: #fbfcfe; flex-wrap: wrap; }
.mg-bar2__lbl { font-size: 12px; color: var(--tv3-ink3); font-weight: 600; }
.mg-bar2__sep { width: 1px; height: 16px; background: var(--tv3-line2); margin: 0 6px; }
.mg-bar2__hint { font-size: 11.5px; color: var(--tv3-ink3); }
.mg-chip {
  padding: 3px 10px; border: 1px solid var(--tv3-line); border-radius: 8px; background: #fff;
  font-size: 12px; cursor: pointer; color: var(--tv3-ink2);
}
.mg-chip:hover { border-color: var(--tv3-gold); background: var(--tv3-gold-soft); }
.mg-main { flex: 1; min-height: 0; display: flex; }
.mg-stage { position: relative; flex: 1; min-width: 0; background: #fff; cursor: default; touch-action: none; overflow: hidden; }
.mg-svg { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
.mg-svg :deep(svg) { max-width: 96%; max-height: 96%; }
.mg-empty {
  position: absolute; inset: 0; display: grid; place-items: center;
  font-size: 13px; color: var(--tv3-ink3); pointer-events: none;
}
.mg-side { width: 288px; border-left: 1px solid var(--tv3-line2); background: #fbfcfe; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.mg-side__sec { background: #fff; border: 1px solid var(--tv3-line); border-radius: 12px; padding: 12px; }
.mg-side__sec--muted { background: transparent; border-style: dashed; }
.mg-script { font-size: 12.5px; line-height: 1.7; resize: vertical; font-family: inherit; }
.mg-result { margin-top: 8px; font-size: 11.8px; line-height: 1.8; }
.mg-result__ok { color: #0e7a4a; }
.mg-result__miss { color: #b3591e; }
.mg-field { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--tv3-ink3); font-weight: 600; }
.mg-field .tv3-input { flex: 1; }
.mg-note { font-size: 11.5px; color: var(--tv3-ink3); line-height: 1.8; margin-top: 6px; }
.mg-del {
  margin-top: 10px; width: 100%; font-size: 12px; padding: 5px 0; border-radius: 8px;
  border: 1px solid var(--tv3-rose-border, #f3c1c1); color: var(--tv3-rose, #dc2646); background: #fff; cursor: pointer;
}
.mg-foot { display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-top: 1px solid var(--tv3-line2); }
.mg-foot__tip { font-size: 11.5px; color: var(--tv3-ink3); }
</style>
