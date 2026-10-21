<template>
  <div
    class="v3sc" :class="{ 'v3sc--editable': editable }"
    :style="{ width: pxW, height: pxH }"
    :data-testid="testid"
    @dragover.prevent @drop="onDrop($event)"
  >
    <div v-if="slide.anchor_bar" class="v3sc__anchor" :title="slide.anchor_bar">⚓ {{ slide.anchor_bar }}</div>

    <!-- rich lesson 课堂任务：保留教师投影可见语义，学生互动仍留在学生端播放器。 -->
    <aside v-if="slide.task" class="v3sc__task" aria-label="课堂任务">
      <strong>课堂任务</strong>
      <span>任务目标：{{ slide.task.goal }}</span>
      <span>问题情境：{{ slide.task.context }}</span>
      <span>学生行动：{{ slide.task.student_action }}</span>
      <span>可检查产出：{{ slide.task.expected_output }}</span>
    </aside>

    <div
      v-for="elx in slide.elements" :key="elx.id"
      class="v3sc__el"
      :class="{
        'mx-object': editable && isMathEl(elx),
        'is-selected': editable && selectedId === elx.id,
        'v3sc__el--confirmed': elx.teacher_confirmed,
      }"
      :style="elStyle(elx)"
      @mousedown.stop="editable && onElDown($event, elx)"
      @click.stop="editable && $emit('select-element', elx.id)"
    >
      <!-- 文本（$..$ 内联公式） -->
      <template v-if="elx.type === 'text'">
        <div class="v3sc__text" :style="textStyle(elx)" v-html="renderRich(elx.html)" />
      </template>

      <!-- 公式（结构化 latex，KaTeX 渲染） -->
      <template v-else-if="elx.type === 'formula'">
        <div class="v3sc__formula" :style="{ fontSize: sc(elx.font_size) + 'px' }" v-html="renderLatex(elx.latex, elx.display)" />
      </template>

      <!-- 几何（结构化 preset，编辑画布用 JSXGraph，缩略图用 miniSvg） -->
      <template v-else-if="elx.type === 'geometry'">
        <GeoFigure
          v-if="!thumb && !elx.board_json"
          :preset-id="elx.preset_id" :params="elx.params" :toggles="elx.toggles"
          :height="sc(elx.height)" :interactive="presenting" :boxed="false" :show-badge="editable"
        />
        <div
          v-else-if="elx.board_json"
          class="v3sc__geo-fallback"
          :title="geometryFallbackDetail(elx.board_json)"
        >
          <strong>{{ geometryFallbackTitle(elx.board_json) }}</strong>
          <span>{{ geometryFallbackDetail(elx.board_json) }}</span>
        </div>
        <div v-else class="v3sc__geo-thumb" v-html="miniSvgOf(elx)" />
        <span v-if="elx.recipe_id" class="v3sc__recipe-tag" title="来自构造配方">⚙ 配方</span>
      </template>

      <!-- 函数图像（表达式采样 SVG） -->
      <template v-else-if="elx.type === 'functionPlot'">
        <div class="v3sc__fx" v-html="fxSvg(elx)" />
        <span v-if="elx.live_sliders" class="v3sc__fx-live" :class="{ 'tv3-pulse-dot': presenting }">⟳ 放映可拖参数</span>
      </template>

      <!-- 立体几何 3D 配图（确定性场景，静态化显示：只取图片边界，无工具栏） -->
      <template v-else-if="elx.type === 'figure3d'">
        <MathFigure3D
          v-if="!thumb"
          :figure="elx.scene" :height="sc(elx.height)" bare
        />
        <div v-else class="v3sc__geo-thumb v3sc__figure3d-thumb" title="立体图形" v-html="figure3dMiniSvg(elx.scene)" />
      </template>

      <!-- 动态演示卡 -->
      <template v-else-if="elx.type === 'dynamicDemo'">
        <div class="v3sc__demo">
          <div class="v3sc__demo-icon">▶</div>
          <div class="v3sc__demo-body">
            <div class="v3sc__demo-title">{{ demoName(elx.demo_id) }}</div>
            <div class="v3sc__demo-sub">{{ elx.caption || '课堂动态演示 · 点击进入' }}</div>
          </div>
        </div>
      </template>

      <!-- 图片 / 拍照原图锚定 -->
      <template v-else-if="elx.type === 'image'">
        <img class="v3sc__img" :src="elx.src" :alt="elx.alt || ''" draggable="false">
      </template>
      <template v-else-if="elx.type === 'anchorPhoto'">
        <div class="v3sc__anchor-photo" :data-upgrade="elx.upgrade_state">
          <img class="v3sc__img" :src="elx.src" alt="学生作业原图（锚定）" draggable="false">
          <div class="v3sc__anchor-photo-tag">原图锚定 · 不可删除</div>
          <span v-if="elx.upgrade_state !== 'none'" class="v3sc__upgrade-tag">
            {{ upgradeLabel(elx.upgrade_state) }}
          </span>
        </div>
      </template>

      <!-- 页码 -->
      <template v-else-if="elx.type === 'pageNo'">
        <div class="v3sc__pageno">{{ elx.no }}</div>
      </template>

      <!-- 选中态浮动删除按钮（原图锚定元素除外） -->
      <button
        v-if="editable && selectedId === elx.id && elx.type !== 'anchorPhoto'"
        class="v3sc__del" title="删除元素（Delete）"
        :data-testid="`${testid}-del`"
        @mousedown.stop @click.stop="$emit('delete-element', elx.id)"
      >×</button>
    </div>

    <div v-if="fillRate !== undefined" class="v3sc__fillnote" :title="`本页装填率 ${(fillRate * 100).toFixed(0)}%`">
      <div class="tv3-fillbar"><div class="tv3-fillbar__bar" :class="fillClass" :style="{ width: (fillRate * 100) + '%' }" /></div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * SlideCanvasV3 —— V3 课件画布（1280×720 逻辑坐标 → 任意宽度等比缩放）
 * 元素八类：text / formula / geometry / functionPlot / dynamicDemo / image / anchorPhoto / pageNo
 * 编辑态：点选（mx-object 金色描边）、拖拽移动、键盘删除由父级处理；
 * 放映态：几何可拖点、函数图带参数滑杆（live_sliders）。
 * 数学元素永远结构化渲染（红线 R1：禁止截图化）。
 */
import { computed } from 'vue'
import { renderLatex, renderRich } from '@/components/mathx/latex'
import { FIGURE_PRESETS } from '@/components/mathx/presets'
import { makeFn, parseLatexExpr } from '@/components/mathx/expr'
import GeoFigure from '@/components/mathx/GeoFigure.vue'
import MathFigure3D from '@/components/chat/MathFigure3D.vue'
import type { V3Element, V3Slide } from '@/types/teacherV3'

const props = withDefaults(defineProps<{
  slide: V3Slide
  width?: number
  editable?: boolean
  presenting?: boolean
  thumb?: boolean
  selectedId?: string
  testid?: string
}>(), {
  width: 960,
  editable: false,
  presenting: false,
  thumb: false,
  selectedId: '',
})

const emit = defineEmits<{
  (e: 'select-element', id: string): void
  (e: 'delete-element', id: string): void
  (e: 'move-element', id: string, left: number, top: number): void
  (e: 'drop-latex', payload: { latex: string; left: number; top: number; from?: 'keyboard' | 'butler'; width?: number; height?: number; font_size?: number; teacher_confirmed?: boolean }): void
}>()

const pxW = computed(() => `${props.width}px`)
const pxH = computed(() => `${Math.round((props.width / 1280) * 720)}px`)
const k = computed(() => props.width / 1280)
const sc = (v: number) => Math.round(v * k.value * 10) / 10

const fillRate = computed(() => props.slide.fill_rate)
const fillClass = computed(() => (fillRate.value === undefined ? '' : fillRate.value > 0.92 ? 'tv3-fillbar__bar--over' : fillRate.value > 0.6 ? 'tv3-fillbar__bar--ok' : 'tv3-fillbar__bar--warn'))

const DEMO_NAMES: Record<string, string> = {
  'conic/ellipse-def': '椭圆定义 · 绳长实验',
  'function/sine': '正弦函数 · 三参数联动',
  'solid/cube-section': '正方体截面 · 三点拖动',
  'plane/circle-line': '直线与圆 · 位置关系',
}

function elStyle(elx: V3Element) {
  return {
    left: sc(elx.left) + 'px',
    top: sc(elx.top) + 'px',
    width: sc(elx.width) + 'px',
    height: sc(elx.height) + 'px',
    zIndex: elx.z,
  }
}
function textStyle(elx: Extract<V3Element, { type: 'text' }>) {
  return {
    fontSize: sc(elx.font_size) + 'px',
    color: elx.color || undefined,
    fontWeight: elx.bold ? 700 : undefined,
  }
}
const isMathEl = (elx: V3Element) => ['formula', 'geometry', 'functionPlot', 'dynamicDemo', 'figure3d'].includes(elx.type)
const demoName = (id: string) => DEMO_NAMES[id] || id

type Point3 = [number, number, number]
function asPoint3(value: unknown): Point3 | null {
  if (!Array.isArray(value) || value.length < 3) return null
  const point = value.slice(0, 3).map(Number)
  return point.every(Number.isFinite) ? point as Point3 : null
}
function escSvg(value: string): string {
  return value.replace(/[&<>\"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch] || ch))
}

/** 3D 缩略图只作结构预览；主画布继续使用 MathFigure3D，PPTX 由后端静态化。 */
function figure3dMiniSvg(scene: Record<string, unknown>): string {
  const points: Point3[] = []
  const edges: [number, number][] = []
  const polygons: number[][] = []
  const curves: { points: Point3[]; color: string; closed: boolean }[] = []
  const planes: { points: Point3[]; color: string }[] = []
  const addPoint = (point: Point3) => { points.push(point); return points.length - 1 }
  const addLoop = (ids: number[]) => ids.forEach((id, index) => edges.push([id, ids[(index + 1) % ids.length]]))
  for (const raw of (Array.isArray(scene?.solids) ? scene.solids : [])) {
    if (!raw || typeof raw !== 'object') continue
    const solid = raw as Record<string, unknown>
    const kind = String(solid.kind || '')
    if (kind === 'pyramid') {
      const base = (Array.isArray(solid.base) ? solid.base : []).map(asPoint3).filter(Boolean) as Point3[]
      const apex = asPoint3(solid.apex)
      if (base.length >= 3 && apex) {
        const ids = base.map(addPoint); const apexId = addPoint(apex)
        polygons.push(ids); addLoop(ids); ids.forEach((id) => edges.push([id, apexId]))
      }
    } else if (kind === 'prism') {
      const bottom = (Array.isArray(solid.bottom) ? solid.bottom : []).map(asPoint3).filter(Boolean) as Point3[]
      const top = (Array.isArray(solid.top) ? solid.top : []).map(asPoint3).filter(Boolean) as Point3[]
      if (bottom.length >= 3 && top.length === bottom.length) {
        const a = bottom.map(addPoint); const b = top.map(addPoint)
        polygons.push(a, b); addLoop(a); addLoop(b); a.forEach((id, index) => edges.push([id, b[index]]))
      }
    } else if (kind === 'polyhedron') {
      const rawVerts = Array.isArray(solid.vertices) ? solid.vertices : []
      const verts = rawVerts
        .map((item) => asPoint3(item && typeof item === 'object' ? (item as Record<string, unknown>).pos : item))
        .filter(Boolean) as Point3[]
      const ids = verts.map(addPoint)
      const name2i = new Map<string, number>()
      rawVerts.forEach((item, index) => {
        if (item && typeof item === 'object' && typeof (item as Record<string, unknown>).name === 'string') {
          name2i.set(String((item as Record<string, unknown>).name), index)
        }
      })
      const edgeIndex = (value: unknown): number | null => {
        if (Number.isInteger(value)) return Number(value)
        const index = name2i.get(String(value))
        return index === undefined ? null : index
      }
      for (const edge of (Array.isArray(solid.edges) ? solid.edges : [])) {
        if (!Array.isArray(edge) || edge.length < 2) continue
        const a = edgeIndex(edge[0]); const b = edgeIndex(edge[1])
        if (a !== null && b !== null && ids[a] !== undefined && ids[b] !== undefined) edges.push([ids[a], ids[b]])
      }
    }
  }
  for (const raw of (Array.isArray(scene?.curves) ? scene.curves : [])) {
    if (!raw || typeof raw !== 'object') continue
    const curve = raw as Record<string, unknown>
    const curvePoints = (Array.isArray(curve.points) ? curve.points : [])
      .map(asPoint3).filter(Boolean) as Point3[]
    if (curvePoints.length < 2) continue
    curves.push({
      points: curvePoints,
      color: typeof curve.color === 'string' ? curve.color : '#dc2626',
      closed: !!curve.closed,
    })
    points.push(...curvePoints)
  }
  for (const raw of (Array.isArray(scene?.planes) ? scene.planes : [])) {
    if (!raw || typeof raw !== 'object') continue
    const plane = raw as Record<string, unknown>
    const planePoints = (Array.isArray(plane.points) ? plane.points : [])
      .map(asPoint3).filter(Boolean) as Point3[]
    if (planePoints.length < 3) continue
    planes.push({
      points: planePoints,
      color: typeof plane.color === 'string' ? plane.color : '#f59e0b',
    })
    points.push(...planePoints)
  }
  const labels = (Array.isArray(scene?.labels) ? scene.labels : [])
    .map((raw) => raw && typeof raw === 'object' ? raw as Record<string, unknown> : null)
    .map((label) => ({ point: asPoint3(label?.pos), text: String(label?.text || '') }))
    .filter((label): label is { point: Point3; text: string } => !!label.point)
  const projected = (point: Point3): [number, number] => [point[0] - point[2] * 0.32, point[1] - point[2] * 0.22]
  const all = [...points, ...labels.map((label) => label.point)]
  if (!all.length) return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 70"><text x="50" y="38" text-anchor="middle" font-size="10">暂无图形</text></svg>'
  const xy = all.map(projected)
  const minX = Math.min(...xy.map((p) => p[0])); const maxX = Math.max(...xy.map((p) => p[0]))
  const minY = Math.min(...xy.map((p) => p[1])); const maxY = Math.max(...xy.map((p) => p[1]))
  const sx = (x: number) => 10 + ((x - minX) / Math.max(maxX - minX, 0.1)) * 80
  const sy = (y: number) => 60 - ((y - minY) / Math.max(maxY - minY, 0.1)) * 48
  const lineSvg = edges.map(([a, b]) => {
    const p = points[a]; const q = points[b]
    return p && q ? `<line x1="${sx(projected(p)[0]).toFixed(1)}" y1="${sy(projected(p)[1]).toFixed(1)}" x2="${sx(projected(q)[0]).toFixed(1)}" y2="${sy(projected(q)[1]).toFixed(1)}" />` : ''
  }).join('')
  const polygonSvg = polygons.map((ids) => `<polygon points="${ids.map((id) => { const p = points[id]; const [x, y] = projected(p); return `${sx(x).toFixed(1)},${sy(y).toFixed(1)}` }).join(' ')}" />`).join('')
  const curveSvg = curves.map((curve) => {
    const curvePoints = curve.closed ? [...curve.points, curve.points[0]] : curve.points
    const coords = curvePoints.map((point) => {
      const [x, y] = projected(point)
      return `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`
    }).join(' ')
    return `<polyline points="${coords}" fill="none" stroke="${escSvg(curve.color)}" stroke-width="1.5" />`
  }).join('')
  const planeSvg = planes.map((plane) => {
    const coords = plane.points.map((point) => {
      const [x, y] = projected(point)
      return `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`
    }).join(' ')
    return `<polygon points="${coords}" fill="${escSvg(plane.color)}" fill-opacity=".22" stroke="${escSvg(plane.color)}" stroke-width="1" />`
  }).join('')
  const labelSvg = labels.map(({ point, text }) => { const [x, y] = projected(point); return `<text x="${sx(x).toFixed(1)}" y="${sy(y).toFixed(1)}">${escSvg(text)}</text>` }).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 70"><g fill="#dbeafe" fill-opacity=".65" stroke="#2563eb" stroke-width="1.2">${polygonSvg}${lineSvg}</g>${planeSvg}${curveSvg}<g fill="#0f172a" font-size="6">${labelSvg}</g></svg>`
}

function miniSvgOf(elx: Extract<V3Element, { type: 'geometry' }>) {
  const def = FIGURE_PRESETS.find((p) => p.id === elx.preset_id)
  if (!def) return ''
  try { return def.miniSvg(elx.params, elx.toggles) } catch { return '' }
}
function geometryFallbackTitle(board: Record<string, unknown>): string {
  const kind = String(board.type || board.kind || 'boardJson')
  return `结构化图形 · ${kind}`
}
function geometryFallbackDetail(board: Record<string, unknown>): string {
  const kind = String(board.type || board.kind || 'boardJson')
  const caption = typeof board.caption === 'string' ? board.caption.trim() : ''
  if (kind === 'ggb' && Array.isArray(board.commands)) {
    return `${board.commands.length} 个作图命令${caption ? ` · ${caption}` : ''}`
  }
  return caption || '保留原始结构，可在绘图工作台继续编辑'
}
const upgradeLabel = (s: string) => (s === 'library' ? '↑ 图形库' : s === 'rebuilt' ? '✓ 已重建' : s === 'demo' ? '▶ 动态演示' : '')

/** 函数图像：采样 SVG（无引擎开销；参数取当前值） */
function fxSvg(elx: Extract<V3Element, { type: 'functionPlot' }>): string {
  const W = 1000
  const H = Math.round((elx.height / elx.width) * W)
  const [x0, x1] = elx.domain
  const ySpan = Math.max(4, (x1 - x0) / 2)
  const xv = (x: number) => ((x - x0) / (x1 - x0)) * W
  const yv = (y: number) => H / 2 - (y / ySpan) * (H / 2)
  const parsed = parseLatexExpr(elx.expr)
  const fn = makeFn(parsed)
  const p: Record<string, number> = {}
  for (const [key, spec] of Object.entries(elx.params || {})) p[key] = spec.value
  let d = ''
  let pen = false
  for (let i = 0; i <= 400; i++) {
    const x = x0 + ((x1 - x0) * i) / 400
    const y = fn(x, p)
    if (!Number.isFinite(y) || Math.abs(y) > ySpan * 1.6) { pen = false; continue }
    d += `${pen ? 'L' : 'M'}${xv(x).toFixed(1)},${yv(y).toFixed(1)} `
    pen = true
  }
  const axisX = `<line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="#c3cad6" stroke-width="1"/>`
  const axisY = `<line x1="${xv(0)}" y1="0" x2="${xv(0)}" y2="${H}" stroke="#c3cad6" stroke-width="1"/>`
  const exprTex = renderLatex(elx.expr.replace(/^y\s*=/, 'y='), false)
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" style="width:100%;height:100%">${axisX}${axisY}` +
    `<path d="${d}" fill="none" stroke="#4f46e5" stroke-width="2.4"/>` +
    `<text x="12" y="22" font-size="26" fill="#16233b" font-family="Cambria Math,Georgia,serif">${exprTex.replace(/<[^>]+>/g, (m) => m.includes('katex') ? '' : m) || elx.expr}</text></svg>`
}

/* ---------- 编辑态拖拽移动 ---------- */
function onElDown(ev: MouseEvent, elx: V3Element) {
  if (!props.editable || ev.button !== 0) return
  emit('select-element', elx.id)
  const startX = ev.clientX
  const startY = ev.clientY
  const originLeft = elx.left
  const originTop = elx.top
  let moved = false
  const onMove = (e2: MouseEvent) => {
    const dx = (e2.clientX - startX) / k.value
    const dy = (e2.clientY - startY) / k.value
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true
    if (moved) {
      elx.left = Math.max(0, Math.min(1280 - elx.width, Math.round(originLeft + dx)))
      elx.top = Math.max(0, Math.min(720 - elx.height, Math.round(originTop + dy)))
    }
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    if (moved) emit('move-element', elx.id, elx.left, elx.top)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

/* 拖入画布 → 生成元素。双通道：
 *  ① application/x-v3-element（管家公式卡等通用元素源，带 width/height/font_size/teacher_confirmed）
 *  ② mx/latex（公式键盘既有通道）
 * 坐标用画布 rect 计算：offsetX 相对命中子元素，拖到公式/图形上方会错位 */
function onDrop(ev: DragEvent) {
  if (!props.editable || !ev.dataTransfer) return
  const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect()
  const left = Math.round((ev.clientX - rect.left) / k.value)
  const top = Math.round((ev.clientY - rect.top) / k.value)
  const raw = ev.dataTransfer.getData('application/x-v3-element')
  if (raw) {
    try {
      const el = JSON.parse(raw) as { latex?: string; width?: number; height?: number; font_size?: number; teacher_confirmed?: boolean }
      if (el.latex) {
        emit('drop-latex', { latex: el.latex, left, top, from: 'butler', width: el.width, height: el.height, font_size: el.font_size, teacher_confirmed: el.teacher_confirmed })
        return
      }
    } catch { /* 非法 JSON 走 latex 通道兜底 */ }
  }
  const latex = ev.dataTransfer.getData('mx/latex')
  if (latex) emit('drop-latex', { latex, left, top, from: 'keyboard' })
}
</script>

<style scoped>
.v3sc {
  position: relative; background: #fff; border-radius: 6px; overflow: hidden;
  box-shadow: 0 2px 10px rgba(79, 70, 229, 0.08); flex-shrink: 0;
}
.v3sc--editable { cursor: default; }
.v3sc__el { position: absolute; }
.v3sc__del {
  position: absolute; right: 4px; top: 4px; z-index: 30;
  width: 20px; height: 20px; border-radius: 50%; border: none;
  background: var(--tv3-rose, #dc2646); color: #fff; cursor: pointer;
  font-size: 14px; line-height: 18px; padding: 0; text-align: center;
  box-shadow: 0 2px 6px rgba(220, 38, 70, 0.35);
}
.v3sc__del:hover { background: #b71e39; }
.v3sc__el.v3sc--editable { cursor: move; }
.v3sc__text { line-height: 1.65; white-space: pre-wrap; }
.v3sc__text :deep(.katex) { font-size: 1.02em; }
.v3sc__formula { line-height: 1.4; }
.v3sc__geo-thumb, .v3sc__fx { width: 100%; height: 100%; }
.v3sc__geo-thumb :deep(svg) { width: 100%; height: 100%; }
.v3sc__geo-fallback {
  width: 100%; height: 100%; box-sizing: border-box;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
  padding: 18px; text-align: center; color: var(--tv3-ink2, #334155);
  background: linear-gradient(135deg, #eff6ff, #f8fafc);
  border: 1px dashed var(--tv3-primary-border, #c7d2fe); border-radius: 8px;
}
.v3sc__geo-fallback strong { color: var(--tv3-primary, #4f46e5); font-size: 14px; }
.v3sc__geo-fallback span { font-size: 12px; color: var(--tv3-ink3, #64748b); }
.v3sc__figure3d-thumb {
  display: grid; place-items: center;
  background: linear-gradient(135deg, #eef2ff, #f0f9ff);
  border: 1px solid var(--tv3-ai-border, #c7d2fe);
  border-radius: 6px;
}
.v3sc__figure3d-chip {
  font-size: 13px; font-weight: 800; letter-spacing: 1px;
  color: var(--tv3-ai, #4f46e5);
  border: 1.5px dashed var(--tv3-ai-border, #c7d2fe);
  border-radius: 999px;
  padding: 3px 12px;
}
.v3sc__recipe-tag {
  position: absolute; left: 4px; top: 4px; z-index: 4;
  font-size: 10px; padding: 1px 6px; border-radius: 999px;
  background: var(--tv3-gold-soft); color: var(--tv3-gold-deep); border: 1px solid var(--tv3-gold-border);
}
.v3sc__fx-live {
  position: absolute; right: 4px; top: 4px; z-index: 4;
  font-size: 10px; padding: 1px 6px; border-radius: 999px;
  background: var(--tv3-ai-soft); color: var(--tv3-ai); border: 1px solid var(--tv3-ai-border);
}
.v3sc__demo {
  width: 100%; height: 100%; border-radius: 10px;
  background: linear-gradient(135deg, #f3eefd, #ece2fb);
  border: 1.5px dashed var(--tv3-ai-border);
  display: flex; align-items: center; gap: 14px; padding: 0 20px;
}
.v3sc__demo-icon {
  width: 44px; height: 44px; border-radius: 50%; flex-shrink: 0;
  background: var(--tv3-ai); color: #fff; display: grid; place-items: center; font-size: 16px;
}
.v3sc__demo-title { font-size: 15px; font-weight: 700; color: var(--tv3-ai-deep); }
.v3sc__demo-sub { font-size: 12px; color: #7c6ba8; margin-top: 2px; }
.v3sc__img { width: 100%; height: 100%; object-fit: contain; border-radius: 4px; }
.v3sc__anchor-photo {
  width: 100%; height: 100%; position: relative;
  border: 2px dashed var(--tv3-gold); border-radius: 8px; padding: 3px; background: #fffdf6;
}
.v3sc__anchor-photo-tag {
  position: absolute; left: 6px; bottom: 6px; z-index: 4;
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  background: rgba(168, 123, 36, 0.92); color: #fff;
}
.v3sc__upgrade-tag {
  position: absolute; right: 6px; top: 6px; z-index: 4;
  font-size: 11px; padding: 1px 8px; border-radius: 999px;
  background: var(--tv3-teal); color: #fff; font-weight: 600;
}
.v3sc__pageno {
  font-family: var(--tv3-font-num); font-size: 13px; color: var(--tv3-ink3);
  display: grid; place-items: center; width: 100%; height: 100%;
}
.v3sc__anchor {
  position: absolute; top: 0; left: 0; right: 0; z-index: 20;
  font-size: 11.5px; color: var(--tv3-primary);
  background: linear-gradient(90deg, var(--tv3-primary-soft), transparent 85%);
  padding: 4px 10px; border-bottom: 1px solid var(--tv3-primary-border);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.v3sc__task {
  position: absolute; right: 28px; bottom: 18px; z-index: 19;
  width: 360px; max-height: 112px; overflow: hidden;
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 12px; border: 1px solid var(--tv3-primary-border, #c7d2fe);
  border-radius: 8px; background: rgba(248, 250, 255, 0.96); color: var(--tv3-ink2, #334155);
  font-size: 12px; line-height: 1.35; box-shadow: 0 2px 8px rgba(30, 58, 95, 0.08);
}
.v3sc__task strong { color: var(--tv3-primary, #4f46e5); font-size: 13px; }
.v3sc__fillnote { position: absolute; right: 8px; bottom: 6px; width: 90px; z-index: 20; }
</style>
