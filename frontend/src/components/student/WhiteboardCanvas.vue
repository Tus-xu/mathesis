<template>
  <div class="wb-env">
    <div class="wb-toolbar" v-if="!hidden">
      <button
        v-for="c in COLORS" :key="c.name"
        class="wb-color" :class="{ on: tool === 'pen' && color === c.value }"
        :style="{ background: c.value }" :title="c.name" @click="pickPen(c.value)"
      ></button>
      <button class="wb-btn" :class="{ on: tool === 'laser' }" @click="tool = 'laser'" title="激光笔（轨迹自动淡出）">🔦</button>
      <button class="wb-btn" :class="{ on: tool === 'eraser' }" @click="tool = 'eraser'" title="橡皮">🧽</button>
      <button class="wb-btn" :class="{ on: latexOpen }" @click="latexOpen = !latexOpen" title="板书公式（OpenMAIC wb_draw_latex）">∑</button>
      <button class="wb-btn" @click="clearAll" title="清空板书">🗑</button>
      <button class="wb-btn" :class="{ on: penetrate }" @click="penetrate = !penetrate" title="穿透交互：暂时不影响下方 3D 图形操作（关闭后恢复圈画）">✋</button>
      <button class="wb-btn" @click="hidden = true" title="收起板书">✕</button>
    </div>
    <button v-else class="wb-fab" title="打开板书（跟老师一样在幻灯片上圈画）" @click="hidden = false">✏️</button>

    <!-- 公式板书输入（对应 OpenMAIC wb_draw_latex：公式贴到板上，可拖拽/删除） -->
    <div v-if="!hidden && latexOpen" class="wb-latex" @pointerdown.stop>
      <LatexText class="wb-lt-preview" :text="latexDraft || '$\\text{输入公式预览}$'" />
      <textarea v-model="latexDraft" rows="2" class="wb-lt-ta" placeholder="例如：\frac{-b \pm \sqrt{b^2-4ac}}{2a}"></textarea>
      <div class="wb-lt-actions">
        <span class="wb-lt-hint">渲染后拖到板书位置</span>
        <button class="wb-btn" @click="latexOpen = false">取消</button>
        <button class="wb-btn on" :disabled="!latexDraft.trim()" @click="addLatex">板书 ⤴</button>
      </div>
    </div>

    <!-- 已板书公式（可拖拽/删除） -->
    <div
      v-for="f in formulas" :key="f.id"
      class="wb-formula"
      :style="{ left: f.x + 'px', top: f.y + 'px' }"
      @pointerdown="dragFormula(f, $event)"
    >
      <LatexText :text="f.latex" />
      <button class="wf-del" title="删除该公式" @pointerdown.stop @click.stop="removeFormula(f.id)">×</button>
    </div>

    <canvas
      ref="cv" class="wb-canvas" :class="{ active: !hidden, penetrate, laser: tool === 'laser' }"
      @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointerleave="onUp"
    ></canvas>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import LatexText from '@/components/LatexText.vue'

const props = defineProps({ page: { type: Number, default: 0 } })
const COLORS = [
  { name: '墨蓝', value: '#2563eb' },
  { name: '玫红', value: '#dc2626' },
  { name: '墨绿', value: '#059669' },
  { name: '墨黑', value: '#1f2937' },
]

const cv = ref(null)
// 默认收起为 FAB（✏️）：板书是叠加层，收起时下方 3D 交互图形立即可旋转/缩放
const hidden = ref(true)
const tool = ref('pen')
const penetrate = ref(false)
const color = ref(COLORS[0].value)
const strokes = [] // [{points, tool, color, width}]
let cur = null
let raf = null
let laserTimer = null
let laserPoints = []
let ctx = null
let lastPage = props.page

/* ===== 公式板书（OpenMAIC wb_draw_latex 语义） ===== */
const latexOpen = ref(false)
const latexDraft = ref('')
const formulas = ref([])
let seq = 0
function addLatex() {
  const t = latexDraft.value.trim()
  if (!t) return
  formulas.value.push({ id: ++seq, latex: '$$' + t + '$$', x: 18 + (formulas.value.length % 4) * 16, y: 14 + (formulas.value.length % 4) * 10 })
  latexDraft.value = ''
  latexOpen.value = false
}
function removeFormula(id) {
  formulas.value = formulas.value.filter((f) => f.id !== id)
}
function dragFormula(f, e) {
  e.preventDefault()
  const r = cv.value.getBoundingClientRect()
  const ox = e.clientX - r.left - f.x
  const oy = e.clientY - r.top - f.y
  const onMove = (ev) => {
    const rr = cv.value.getBoundingClientRect()
    f.x = Math.max(0, Math.min(rr.width - 60, ev.clientX - rr.left - ox))
    f.y = Math.max(0, Math.min(rr.height - 30, ev.clientY - rr.top - oy))
  }
  const onUp = () => {
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
  }
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
}

function sizeCanvas() {
  const el = cv.value
  if (!el) return
  const dpr = window.devicePixelRatio || 1
  const rect = el.getBoundingClientRect()
  el.width = Math.round(rect.width * dpr)
  el.height = Math.round(rect.height * dpr)
  ctx = el.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  redraw()
}

function redraw() {
  if (!ctx) return
  ctx.clearRect(0, 0, cv.value.clientWidth, cv.value.clientHeight)
  for (const s of strokes) {
    if (s.tool === 'eraser') continue
    ctx.strokeStyle = s.color
    ctx.lineWidth = s.width
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    s.points.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)))
    ctx.stroke()
  }
}

function applyEraserSegment(s) {
  if (s.points.length < 2) return
  const [a, b] = s.points.slice(-2)
  ctx.save()
  ctx.globalCompositeOperation = 'destination-out'
  ctx.strokeStyle = 'rgba(0,0,0,1)'
  ctx.lineWidth = s.width
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(a.x, a.y)
  ctx.lineTo(b.x, b.y)
  ctx.stroke()
  ctx.restore()
}

function strokeSegment(s) {
  if (s.points.length < 2) return
  const [a, b] = s.points.slice(-2)
  ctx.strokeStyle = s.color
  ctx.lineWidth = s.width
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  ctx.moveTo(a.x, a.y)
  ctx.lineTo(b.x, b.y)
  ctx.stroke()
}

function toXY(e) {
  const r = cv.value.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function onDown(e) {
  if (hidden.value) return
  cv.value.setPointerCapture(e.pointerId)
  const p = toXY(e)
  cur = { points: [p], tool: tool.value, color: color.value, width: tool.value === 'eraser' ? 26 : 4 }
  strokes.push(cur)
  if (cur.tool === 'laser') { laserPoints = [p]; scheduleLaser() }
}

function onMove(e) {
  if (!cur) return
  const p = toXY(e)
  cur.points.push(p)
  if (cur.tool === 'laser') {
    laserPoints = [...laserPoints.slice(-7), p]
    scheduleLaser()
    return
  }
  if (cur.tool === 'eraser') applyEraserSegment(cur)
  else strokeSegment(cur)
}

function onUp() { cur = null }

function scheduleLaser() {
  if (raf) cancelAnimationFrame(raf)
  if (laserTimer) clearTimeout(laserTimer)
  raf = requestAnimationFrame(() => {
    if (!ctx) return
    redraw()
    if (laserPoints.length > 1) {
      ctx.strokeStyle = '#f43f5e'
      ctx.lineWidth = 6
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.globalAlpha = 0.9
      ctx.beginPath()
      laserPoints.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)))
      ctx.stroke()
      ctx.globalAlpha = 1
    }
    laserTimer = setTimeout(() => { if (ctx && laserPoints.length) redraw() }, 360)
  })
}

function clearAll() {
  strokes.length = 0
  formulas.value = []
  if (ctx) ctx.clearRect(0, 0, cv.value.clientWidth, cv.value.clientHeight)
}
function pickPen(v) { color.value = v; tool.value = 'pen' }

watch(() => props.page, (p) => { if (p !== lastPage) { lastPage = p; clearAll() } })

let ro = null
onMounted(() => {
  sizeCanvas()
  ro = new ResizeObserver(sizeCanvas)
  if (cv.value) ro.observe(cv.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  clearTimeout(laserTimer)
  cancelAnimationFrame(raf)
})
</script>

<style scoped>
.wb-env { position: absolute; inset: 0; z-index: 8; pointer-events: none; }
.wb-canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; touch-action: none; }
.wb-canvas.active { pointer-events: auto; cursor: crosshair; }
.wb-canvas.active.penetrate { pointer-events: none; cursor: default; }
.wb-canvas.active.laser { cursor: none; }
.wb-toolbar {
  position: absolute; top: 8px; right: 8px; z-index: 10; display: flex; gap: 6px; align-items: center;
  padding: 5px 8px; background: rgba(255,255,255,.94); border: 1px solid rgba(0,0,0,.08);
  border-radius: 10px; box-shadow: 0 2px 10px rgba(15,23,42,.08); pointer-events: auto;
}
.wb-color { width: 18px; height: 18px; border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px #e2e8f0; cursor: pointer; }
.wb-color.on { box-shadow: 0 0 0 2px #0f172a; }
.wb-btn { border: none; background: transparent; font-size: 14px; cursor: pointer; padding: 2px 5px; border-radius: 6px; }
.wb-btn:hover { background: #f1f5f9; }
.wb-btn.on { background: #dbeafe; }
.wb-btn:disabled { opacity: .4; }
.wb-fab {
  position: absolute; top: 8px; right: 8px; z-index: 10; pointer-events: auto; cursor: pointer;
  font-size: 16px; border: 1px solid rgba(0,0,0,.08); background: rgba(255,255,255,.94);
  border-radius: 10px; padding: 6px 9px; box-shadow: 0 2px 10px rgba(15,23,42,.08);
}
/* 公式板书输入面板 */
.wb-latex {
  position: absolute; left: 8px; bottom: 10px; z-index: 11; width: 320px; pointer-events: auto;
  background: rgba(255,255,255,.97); border: 1px solid rgba(0,0,0,.1); border-radius: 12px;
  padding: 10px 12px; box-shadow: 0 8px 28px rgba(15,23,42,.16);
}
.wb-lt-preview { min-height: 34px; display: flex; align-items: center; justify-content: center; font-size: 15px; padding: 6px; background: #f8fafc; border-radius: 8px; margin-bottom: 8px; overflow-x: auto; }
.wb-lt-ta { width: 100%; font: inherit; font-size: 12.5px; border: 1px solid var(--line, #eef1f5); border-radius: 8px; padding: 7px 9px; resize: vertical; color: #1f2937; }
.wb-lt-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.wb-lt-hint { flex: 1; font-size: 11px; color: var(--ink3, #9aa1ac); }
/* 已板书公式块（可拖拽） */
.wb-formula {
  position: absolute; z-index: 9; pointer-events: auto; cursor: grab;
  background: rgba(255,255,255,.92); border: 1px dashed rgba(59,123,255,.45);
  border-radius: 10px; padding: 6px 22px 6px 12px; font-size: 15px;
  box-shadow: 0 2px 10px rgba(15,23,42,.08); user-select: none;
}
.wb-formula:active { cursor: grabbing; }
.wf-del {
  position: absolute; top: 2px; right: 3px; border: none; background: none; cursor: pointer;
  color: var(--ink3, #9aa1ac); font-size: 13px; line-height: 1; padding: 0 3px;
}
.wf-del:hover { color: #dc2626; }
</style>
