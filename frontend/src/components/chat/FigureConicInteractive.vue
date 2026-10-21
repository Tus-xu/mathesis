<template>
  <!-- N6 可拖动圆锥曲线（无第三方依赖，纯 SVG + 指针事件）：
       拖动动点 M 沿曲线滑动，焦半径实时联动，定义式读数（椭圆 |MF1|+|MF2|=2a、
       双曲线 ||MF1|-|MF2||=2a、抛物线 |MF|=|M到准线|）——数学定义的可视化。
       动点参数（t/u）是唯一状态，屏幕坐标全部由其派生，拖动即改参数。 -->
  <div class="fci">
    <svg
      ref="svgRef"
      class="fci-svg"
      viewBox="0 0 400 300"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
    >
      <rect x="0" y="0" width="400" height="300" fill="#ffffff" />
      <line :x1="sx(xmin)" :y1="sy(0)" :x2="sx(xmax)" :y2="sy(0)" stroke="#222" stroke-width="1.2" />
      <line :x1="sx(0)" :y1="sy(ymin)" :x2="sx(0)" :y2="sy(ymax)" stroke="#222" stroke-width="1.2" />
      <text :x="sx(xmax) - 6" :y="sy(0) - 8" font-size="12" font-style="italic">x</text>
      <text :x="sx(0) + 8" :y="sy(ymax) + 10" font-size="12" font-style="italic">y</text>
      <text v-if="showOrigin" :x="sx(0) - 14" :y="sy(0) + 15" font-size="12">O</text>
      <!-- 准线（抛物线） -->
      <line v-if="curve === 'parabola'" :x1="s.dir.x1" :y1="s.dir.y1" :x2="s.dir.x2" :y2="s.dir.y2"
        stroke="#8a93a0" stroke-width="1" stroke-dasharray="5,4" />
      <text v-if="curve === 'parabola'" :x="s.dir.x2 - 8" :y="s.dir.y2 - 4" font-size="11" fill="#8a93a0">l</text>
      <!-- 渐近线（双曲线） -->
      <line v-for="(a, i) in asymptotes" :key="'as' + i" :x1="a.x1" :y1="a.y1" :x2="a.x2" :y2="a.y2"
        stroke="#8a93a0" stroke-width="1" stroke-dasharray="5,4" />
      <!-- 曲线 -->
      <path :d="curvePath" fill="none" stroke="#1a5fb4" stroke-width="2" />
      <path v-if="curvePath2" :d="curvePath2" fill="none" stroke="#1a5fb4" stroke-width="2" />
      <!-- 焦半径（虚线，实时联动） -->
      <line v-for="(s2, i) in focalRadii" :key="'fr' + i" :x1="s2.x1" :y1="s2.y1" :x2="s2.x2" :y2="s2.y2"
        stroke="#8a93a0" stroke-width="1.2" stroke-dasharray="4,4" />
      <!-- 焦点 -->
      <g v-for="(f, i) in fociScreen" :key="'f' + i">
        <circle :cx="f.x" :cy="f.y" r="3" fill="#222" />
        <text :x="f.x - 11" :y="f.y + 14" font-size="13" font-style="italic" fill="#c01c28">{{ f.name }}</text>
      </g>
      <!-- 动点 M（可拖动） -->
      <circle :cx="MView.sx" :cy="MView.sy" r="6" fill="#c01c28" stroke="#fff" stroke-width="1.5"
        style="cursor: grab" @pointerdown.stop="onDown" />
      <text :x="MView.sx + 9" :y="MView.sy - 8" font-size="13" font-style="italic" fill="#c01c28">{{ gliderLabel }}</text>
      <!-- 定义式读数 -->
      <text x="10" y="18" font-size="13" fill="#1a5fb4">{{ definitionText }}</text>
    </svg>
    <div class="fci-hint">🖐 拖动红点 {{ gliderLabel }} 沿曲线滑动，观察焦半径与定义式读数</div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  // 后端 validate_figure_params 规范化后的 conic params（标注点坐标已解析为数值）
  params: { type: Object, required: true },
})

const W = 400
const H = 300
const svgRef = ref(null)
const dragging = ref(false)

const p = props.params
const curve = p.curve || 'ellipse'
const axis = p.axis || 'x'
const cx = p.cx || 0
const cy = p.cy || 0
const gliderLabel = (p.points && p.points[0] && p.points[0].label) || 'M'
const firstPt = p.points && p.points[0] ? p.points[0] : null

// 动点唯一状态：椭圆/双曲线用参数角 t（度），抛物线用横向参数 u
const t = ref(firstPt && firstPt._t != null ? firstPt._t : 55)
const u = ref(firstPt && firstPt._u != null ? firstPt._u : 2 * (p.p || 1))
const branch = ref(firstPt && firstPt.branch === -1 ? -1 : 1)

function worldOfGlider() {
  const rad = (t.value * Math.PI) / 180
  if (curve === 'ellipse') {
    if (axis === 'x') return { x: cx + p.a * Math.cos(rad), y: cy + p.b * Math.sin(rad) }
    return { x: cx + p.b * Math.cos(rad), y: cy + p.a * Math.sin(rad) }
  }
  if (curve === 'hyperbola') {
    const uu = p.a * Math.cosh(rad)
    const vv = p.b * Math.sinh(rad) * branch.value
    if (axis === 'x') return { x: cx + uu, y: cy + vv }
    return { x: cx + vv, y: cy + uu }
  }
  const sgn = p.opening === 'down' || p.opening === 'left' ? -1 : 1
  const horiz = p.opening === 'up' || p.opening === 'down'
  const v = (sgn * u.value * u.value) / (4 * (p.p || 1))
  if (horiz) return { x: cx + u.value, y: cy + v }
  return { x: cx + v, y: cy + u.value }
}

// ===== 等比投影（与后端 _conic_svg 同思路，含动点与焦点在内的视野） =====
const proj = computed(() => {
  const xs = [cx]
  const ys = [cy]
  const mw = worldOfGlider()
  xs.push(mw.x)
  ys.push(mw.y)
  if (curve === 'ellipse') {
    const rx = axis === 'x' ? p.a : p.b
    const ry = axis === 'x' ? p.b : p.a
    xs.push(cx - rx, cx + rx)
    ys.push(cy - ry, cy + ry)
  } else if (curve === 'hyperbola') {
    const rx = Math.max(p.a, p.focal) * 1.15
    const ry = p.b * 2
    if (axis === 'x') { xs.push(cx - rx, cx + rx); ys.push(cy - ry, cy + ry) }
    else { xs.push(cx - ry, cx + ry); ys.push(cy - rx, cy + rx) }
  } else {
    const us = 4.2 * (p.p || 1)
    const vs = 4.5 * (p.p || 1)
    const horiz = p.opening === 'up' || p.opening === 'down'
    xs.push(cx - (horiz ? us : vs), cx + (horiz ? us : vs))
    ys.push(cy - (horiz ? vs : us), cy + (horiz ? vs : us))
  }
  let x0 = Math.min(...xs)
  let x1 = Math.max(...xs)
  let y0 = Math.min(...ys)
  let y1 = Math.max(...ys)
  x0 -= 0.18 * (x1 - x0) || 1
  x1 += 0.18 * (x1 - x0) || 1
  y0 -= 0.18 * (y1 - y0) || 1
  y1 += 0.18 * (y1 - y0) || 1
  const ml = 36; const mr = 18; const mt = 18; const mb = 24
  const plotW = W - ml - mr
  const plotH = H - mt - mb
  const scale = Math.min(plotW / Math.max(x1 - x0, 1e-6), plotH / Math.max(y1 - y0, 1e-6))
  const viewW = plotW / scale
  const viewH = plotH / scale
  const left = (x0 + x1) / 2 - viewW / 2
  const bottom = (y0 + y1) / 2 - viewH / 2
  return {
    scale, left, bottom, viewW, viewH,
    sx: (x) => ml + (x - left) * scale,
    sy: (y) => mt + (bottom + viewH - y) * scale,
    toWorld: (px, py) => ({
      x: left + (px - ml) / scale,
      y: bottom + viewH - (py - mt) / scale,
    }),
  }
})

function sx(x) { return proj.value.sx(x) }
function sy(y) { return proj.value.sy(y) }
const xmin = computed(() => proj.value.left)
const xmax = computed(() => proj.value.left + proj.value.viewW)
const ymin = computed(() => proj.value.bottom)
const ymax = computed(() => proj.value.bottom + proj.value.viewH)
const showOrigin = computed(() => xmin.value <= 0 <= xmax.value && ymin.value <= 0 <= ymax.value)

// ===== 世界坐标 → 屏幕坐标的派生视图 =====
const MView = computed(() => {
  const w = worldOfGlider()
  return { w, sx: sx(w.x), sy: sy(w.y) }
})

function worldDist(ax, ay, bx, by) {
  // 输入均为世界坐标
  return Math.hypot(ax - bx, ay - by)
}

const fociWorld = computed(() => {
  if (curve === 'parabola') {
    const sgn = p.opening === 'up' || p.opening === 'right' ? 1 : -1
    if (p.opening === 'up' || p.opening === 'down') return [{ x: cx, y: cy + sgn * p.p, name: 'F' }]
    return [{ x: cx + sgn * p.p, y: cy, name: 'F' }]
  }
  if (axis === 'x') {
    return [
      { x: cx - p.focal, y: cy, name: 'F₁' },
      { x: cx + p.focal, y: cy, name: 'F₂' },
    ]
  }
  return [
    { x: cx, y: cy - p.focal, name: 'F₁' },
    { x: cx, y: cy + p.focal, name: 'F₂' },
  ]
})
const fociScreen = computed(() => fociWorld.value.map((f) => ({ ...f, x: sx(f.x), y: sy(f.y) })))

const focalRadii = computed(() => {
  const m = MView.value
  return fociScreen.value.map((f) => ({ x1: f.x, y1: f.y, x2: m.sx, y2: m.sy }))
})

const curvePath = computed(() => {
  const pts = []
  if (curve === 'ellipse') {
    for (let i = 0; i <= 180; i++) {
      const r = (2 * Math.PI * i) / 180
      if (axis === 'x') pts.push([cx + p.a * Math.cos(r), cy + p.b * Math.sin(r)])
      else pts.push([cx + p.b * Math.cos(r), cy + p.a * Math.sin(r)])
    }
  } else if (curve === 'hyperbola') {
    const tMax = 1.7
    for (let i = 0; i <= 90; i++) {
      const tt = -tMax + (2 * tMax * i) / 90
      const uu = p.a * Math.cosh(tt)
      const vv = p.b * Math.sinh(tt) * branch.value
      if (axis === 'x') pts.push([cx + uu, cy + vv])
      else pts.push([cx + vv, cy + uu])
    }
  } else {
    const sgn = p.opening === 'down' || p.opening === 'left' ? -1 : 1
    const U = 4.2 * (p.p || 1)
    for (let i = 0; i <= 120; i++) {
      const uu = -U + (2 * U * i) / 120
      const vv = (sgn * uu * uu) / (4 * (p.p || 1))
      if (p.opening === 'up' || p.opening === 'down') pts.push([cx + uu, cy + vv])
      else pts.push([cx + vv, cy + uu])
    }
  }
  return pts.map((q, i) => `${i ? 'L' : 'M'}${sx(q[0]).toFixed(1)},${sy(q[1]).toFixed(1)}`).join('')
})

const curvePath2 = computed(() => {
  if (curve !== 'hyperbola') return ''
  const tMax = 1.7
  const pts = []
  for (let i = 0; i <= 90; i++) {
    const tt = -tMax + (2 * tMax * i) / 90
    const uu = p.a * Math.cosh(tt)
    const vv = p.b * Math.sinh(tt) * -branch.value
    if (axis === 'x') pts.push([cx + uu, cy + vv])
    else pts.push([cx + vv, cy + uu])
  }
  return pts.map((q, i) => `${i ? 'L' : 'M'}${sx(q[0]).toFixed(1)},${sy(q[1]).toFixed(1)}`).join('')
})

const asymptotes = computed(() => {
  if (curve !== 'hyperbola') return []
  const ratio = p.b / p.a
  const { left, bottom, viewW, viewH } = proj.value
  const out = []
  for (const s of [1, -1]) {
    if (axis === 'x') {
      out.push({
        x1: sx(left), y1: sy(cy + s * ratio * (left - cx)),
        x2: sx(left + viewW), y2: sy(cy + s * ratio * (left + viewW - cx)),
      })
    } else {
      out.push({
        x1: sx(cx + s * ratio * (bottom - cy)), y1: sy(bottom),
        x2: sx(cx + s * ratio * (bottom + viewH - cy)), y2: sy(bottom + viewH),
      })
    }
  }
  return out
})

const directrix = computed(() => {
  if (curve !== 'parabola') return null
  const sgn = p.opening === 'up' || p.opening === 'right' ? 1 : -1
  const horiz = p.opening === 'up' || p.opening === 'down'
  if (horiz) {
    const yd = cy - sgn * p.p
    return { x1: sx(xmin.value), y1: sy(yd), x2: sx(xmax.value), y2: sy(yd) }
  }
  const xd = cx - sgn * p.p
  return { x1: sx(xd), y1: sy(ymin.value), x2: sx(xd), y2: sy(ymax.value) }
})
const s = computed(() => ({ dir: directrix.value || { x1: 0, y1: 0, x2: 0, y2: 0 } }))

const definitionText = computed(() => {
  const w = MView.value.w
  const fs = fociWorld.value
  if (curve === 'ellipse') {
    const d1 = worldDist(w.x, w.y, fs[0].x, fs[0].y)
    const d2 = worldDist(w.x, w.y, fs[1].x, fs[1].y)
    return `|M${fs[0].name}|+|M${fs[1].name}| = ${(d1 + d2).toFixed(2)} = 2a = ${(2 * p.a).toFixed(2)}`
  }
  if (curve === 'hyperbola') {
    const d1 = worldDist(w.x, w.y, fs[0].x, fs[0].y)
    const d2 = worldDist(w.x, w.y, fs[1].x, fs[1].y)
    return `||M${fs[0].name}|-|M${fs[1].name}|| = ${Math.abs(d1 - d2).toFixed(2)} = 2a = ${(2 * p.a).toFixed(2)}`
  }
  const sgn = p.opening === 'up' || p.opening === 'right' ? 1 : -1
  const horiz = p.opening === 'up' || p.opening === 'down'
  const df = horiz
    ? worldDist(w.x, w.y, cx, cy + sgn * p.p)
    : worldDist(w.x, w.y, cx + sgn * p.p, cy)
  const dl = horiz
    ? Math.abs(w.y - (cy - sgn * p.p))
    : Math.abs(w.x - (cx - sgn * p.p))
  return `|M${fs[0].name}| = ${df.toFixed(2)}　|M到准线l| = ${dl.toFixed(2)}（相等）`
})

// ===== 拖动：指针位置 → 世界坐标 → 投影回曲线参数（点恒在曲线上） =====
function onDown(ev) {
  dragging.value = true
  svgRef.value?.setPointerCapture?.(ev.pointerId)
  onMove(ev)
}
function onUp() { dragging.value = false }
function onMove(ev) {
  if (!dragging.value) return
  const svg = svgRef.value
  if (!svg) return
  const rect = svg.getBoundingClientRect()
  const px = ((ev.clientX - rect.left) / rect.width) * W
  const py = ((ev.clientY - rect.top) / rect.height) * H
  const w = proj.value.toWorld(px, py)
  if (curve === 'ellipse') {
    if (axis === 'x') t.value = (Math.atan2((w.y - cy) / p.b, (w.x - cx) / p.a) * 180) / Math.PI
    else t.value = (Math.atan2((w.y - cy) / p.a, (w.x - cx) / p.b) * 180) / Math.PI
  } else if (curve === 'hyperbola') {
    const ux = axis === 'x' ? w.x - cx : w.y - cy
    const arg = Math.max(1, Math.abs(ux) / p.a)
    t.value = (Math.acosh(arg) * 180) / Math.PI
    branch.value = ux >= 0 ? 1 : -1
  } else {
    const horiz = p.opening === 'up' || p.opening === 'down'
    u.value = horiz ? w.x - cx : w.y - cy
  }
}
</script>

<style scoped>
.fci { border: 1px solid #e3e7ee; border-radius: 10px; background: #fff; overflow: hidden; }
.fci-svg { display: block; width: 100%; height: auto; touch-action: none; }
.fci-hint { padding: 6px 10px; font-size: 12px; color: #5b6572; background: #f8fafc; border-top: 1px solid #eef1f5; }
</style>
