<template>
  <!-- F11 graph block：契约 {engine:'jsxgraph', schema:{...}}（graph_block.py 只约束到 schema 存在）。
       含函数表达式 → inline SVG 简易函数图像（安全解析器，无 eval）；否则结构化 JSON 卡兜底。 -->
  <div class="graph-block">
    <div v-if="plot.ok" class="gb-plot">
      <div class="gb-head">
        <span class="gb-title">📈 {{ GTYPE_ZH[plot.graphType] || plot.graphType || '函数图像' }}</span>
        <span v-for="(e, i) in plot.exprs" :key="i" class="gb-legend" :style="{ color: COLORS[i % COLORS.length] }">
          f(x) = {{ e }}
        </span>
      </div>
      <svg :viewBox="`0 0 ${W} ${H}`" class="gb-svg" role="img">
        <!-- 网格 -->
        <line v-for="g in plot.grid" :key="g.k" :x1="g.x1" :y1="g.y1" :x2="g.x2" :y2="g.y2" class="gb-grid" />
        <!-- 坐标轴 -->
        <line v-if="plot.axisX !== null" :x1="0" :y1="plot.axisX" :x2="W" :y2="plot.axisX" class="gb-axis" />
        <line v-if="plot.axisY !== null" :x1="plot.axisY" :y1="0" :x2="plot.axisY" :y2="H" class="gb-axis" />
        <!-- 曲线 -->
        <path
          v-for="(p, i) in plot.paths"
          :key="i"
          :d="p"
          fill="none"
          :stroke="COLORS[i % COLORS.length]"
          stroke-width="1.8"
          stroke-linejoin="round"
        />
        <!-- 点 -->
        <circle v-for="(pt, i) in plot.pts" :key="'p' + i" :cx="pt.x" :cy="pt.y" r="3.5" class="gb-pt" />
      </svg>
    </div>

    <details v-else class="gb-json">
      <summary>📈 图形数据（{{ plot.graphType || 'graph' }}）</summary>
      <pre>{{ plot.pretty }}</pre>
    </details>

    <div v-if="plot.captions.length" class="gb-steps">
      <div v-for="s in plot.captions" :key="s.step_no" class="gb-step">
        <span class="gb-step-no">{{ s.step_no }}</span>
        <MarkdownView :text="s.caption || ''" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownView from '@/components/MarkdownView.vue'

const props = defineProps({
  graph: { type: Object, required: true },
})

const W = 340
const H = 250
const COLORS = ['#4F6EF7', '#DC2626', '#059669', '#D97706']
const GTYPE_ZH = {
  function_plot: '函数图像',
  geometry: '几何作图',
  conic: '圆锥曲线',
  trig: '三角函数',
  vector: '向量',
  analytic_geo: '解析几何',
  solid_projection: '立体几何',
  statistics: '统计图',
}

/* ===== 安全表达式解析（禁 eval）：递归下降 → AST → 求值函数 ===== */
const FUNCS = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan,
  asin: Math.asin, acos: Math.acos, atan: Math.atan,
  sqrt: Math.sqrt, abs: Math.abs, exp: Math.exp,
  ln: Math.log, log: Math.log10, lg: Math.log10,
}

function parseExpression(src) {
  const s = String(src).replace(/\s+/g, '').toLowerCase().replace(/×/g, '*').replace(/÷/g, '/')
  let i = 0
  const peek = () => s[i]
  function parseExpr() {
    let node = parseTerm()
    while (peek() === '+' || peek() === '-') {
      const op = s[i++]
      node = { op, l: node, r: parseTerm() }
    }
    return node
  }
  function parseTerm() {
    let node = parsePow()
    for (;;) {
      const c = peek()
      if (c === '*' || c === '/') {
        i++
        node = { op: c, l: node, r: parsePow() }
      } else if (c !== undefined && /[0-9a-z(π]/.test(c)) {
        node = { op: '*', l: node, r: parsePow() } // 隐式乘法：2x、2(x+1)、x sin(x)
      } else break
    }
    return node
  }
  function parsePow() {
    const base = parseUnary()
    if (peek() === '^') {
      i++
      return { op: '^', l: base, r: parsePow() } // 右结合
    }
    if (s.startsWith('**', i)) {
      i += 2
      return { op: '^', l: base, r: parsePow() }
    }
    return base
  }
  function parseUnary() {
    if (peek() === '-') { i++; return { op: 'neg', r: parseUnary() } }
    if (peek() === '+') { i++; return parseUnary() }
    return parsePrimary()
  }
  function parsePrimary() {
    const c = peek()
    if (c === '(') {
      i++
      const n = parseExpr()
      if (peek() === ')') i++
      return n
    }
    if (c !== undefined && /[0-9.]/.test(c)) {
      const m = /^[0-9]*\.?[0-9]+/.exec(s.slice(i))
      if (!m) throw new Error('数字语法错误')
      i += m[0].length
      return { num: parseFloat(m[0]) }
    }
    if (c === 'π') { i++; return { num: Math.PI } }
    if (c !== undefined && /[a-z]/.test(c)) {
      const m = /^[a-z]+/.exec(s.slice(i))[0]
      i += m.length
      if (m === 'x') return { v: 'x' }
      if (m === 'pi') return { num: Math.PI }
      if (m === 'e') return { num: Math.E }
      if (FUNCS[m] && peek() === '(') {
        i++
        const arg = parseExpr()
        if (peek() === ')') i++
        return { op: 'fn', fn: m, arg }
      }
      throw new Error(`未知标识符 ${m}`)
    }
    throw new Error(`语法错误 @${i}`)
  }
  const tree = parseExpr()
  if (i < s.length) throw new Error('表达式存在多余字符')
  const ev = (n, x) => {
    if (n.num !== undefined) return n.num
    if (n.v) return x
    if (n.op === 'neg') return -ev(n.r, x)
    if (n.op === 'fn') return FUNCS[n.fn](ev(n.arg, x))
    const l = ev(n.l, x)
    const r = ev(n.r, x)
    if (n.op === '+') return l + r
    if (n.op === '-') return l - r
    if (n.op === '*') return l * r
    if (n.op === '/') return r === 0 ? NaN : l / r
    if (n.op === '^') return Math.pow(l, r)
    return NaN
  }
  return (x) => ev(tree, x)
}

/* ===== graph 载荷归一化 + SVG 采样 ===== */
const plot = computed(() => {
  const fail = { ok: false, exprs: [], paths: [], pts: [], grid: [], captions: [], graphType: '', pretty: '' }
  try {
    const g = props.graph || {}
    // 兼容两种形态：{engine, schema} 或历史信封 block {type:'graph', engine, schema}
    const schema = (g.schema && typeof g.schema === 'object' ? g.schema : g) || {}
    const graphType = schema.graph_type || schema.graphType || g.graph_type || ''

    // 收集函数表达式：schema.elements[].kind=functiongraph 的 parents[0]，
    // 以及顶层 expression / type=function 形态
    const exprSrc = []
    const pointSrc = []
    const elements = Array.isArray(schema.elements) ? schema.elements : []
    for (const el of elements) {
      if (!el || typeof el !== 'object') continue
      if (el.kind === 'functiongraph' && typeof el.parents?.[0] === 'string') exprSrc.push(el.parents[0])
      if (el.kind === 'point' && Array.isArray(el.parents) && el.parents.length >= 2) {
        const [px, py] = el.parents
        if (Number.isFinite(+px) && Number.isFinite(+py)) pointSrc.push([+px, +py])
      }
    }
    for (const key of ['expression', 'expr', 'fn']) {
      if (typeof schema[key] === 'string') exprSrc.push(schema[key])
      if (typeof g[key] === 'string') exprSrc.push(g[key])
    }
    const captions = (Array.isArray(schema.steps) ? schema.steps : [])
      .filter((s) => s && typeof s === 'object')
      .slice(0, 12)

    const pretty = JSON.stringify(g, null, 2)
    if (!exprSrc.length && !pointSrc.length) {
      return { ...fail, graphType, pretty, captions }
    }

    // 视窗：schema.board.boundingbox = [xmin, ymax, xmax, ymin]
    const bb = Array.isArray(schema.board?.boundingbox) ? schema.board.boundingbox : [-5, 5, 5, -5]
    const xmin = Number.isFinite(+bb[0]) ? +bb[0] : -5
    const ymax = Number.isFinite(+bb[1]) ? +bb[1] : 5
    const xmax = Number.isFinite(+bb[2]) ? +bb[2] : 5
    const ymin = Number.isFinite(+bb[3]) ? +bb[3] : -5
    if (xmax - xmin <= 0 || ymax - ymin <= 0) return { ...fail, graphType, pretty, captions }

    const toPx = (x, y) => [((x - xmin) / (xmax - xmin)) * W, H - ((y - ymin) / (ymax - ymin)) * H]

    // 网格：整数刻度
    const grid = []
    for (let gx = Math.ceil(xmin); gx <= Math.floor(xmax); gx++) {
      const [px] = toPx(gx, 0)
      grid.push({ k: `x${gx}`, x1: px, y1: 0, x2: px, y2: H })
    }
    for (let gy = Math.ceil(ymin); gy <= Math.floor(ymax); gy++) {
      const [, py] = toPx(0, gy)
      grid.push({ k: `y${gy}`, x1: 0, y1: py, x2: W, y2: py })
    }
    const axisX = ymin < 0 && ymax > 0 ? toPx(0, 0)[1] : null
    const axisY = xmin < 0 && xmax > 0 ? toPx(0, 0)[0] : null

    // 曲线采样：不连续/非有限处断笔
    const paths = []
    const exprs = []
    for (const src of exprSrc.slice(0, 4)) {
      let fn
      try { fn = parseExpression(src) } catch { continue }
      exprs.push(src)
      const N = 240
      let d = ''
      let pen = false
      let lastPy = 0
      for (let i = 0; i <= N; i++) {
        const x = xmin + ((xmax - xmin) * i) / N
        const y = fn(x)
        if (!Number.isFinite(y) || Math.abs(y) > 1e5) { pen = false; continue }
        const [px, py] = toPx(x, y)
        // 纵向跳变过大视为渐近线断点
        if (pen && Math.abs(py - lastPy) > H) pen = false
        d += `${pen ? 'L' : 'M'}${px.toFixed(1)},${py.toFixed(1)} `
        lastPy = py
        pen = true
      }
      if (d) paths.push(d.trim())
    }
    const pts = pointSrc.slice(0, 20).map(([x, y]) => {
      const [px, py] = toPx(x, y)
      return { x: px, y: py }
    })
    if (!paths.length && !pts.length) return { ...fail, graphType, pretty, captions }
    return { ok: true, exprs, paths, pts, grid, captions, graphType, pretty, axisX, axisY }
  } catch {
    let pretty = ''
    try { pretty = JSON.stringify(props.graph, null, 2) } catch { pretty = String(props.graph) }
    return { ...fail, pretty }
  }
})
</script>

<style scoped>
.graph-block { margin-top: 8px; }
.gb-plot {
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-white); padding: 10px 12px;
}
.gb-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.gb-title { font-weight: 700; font-size: 12px; color: var(--text-secondary); }
.gb-legend { font-size: 12px; font-family: Georgia, serif; font-style: italic; }
.gb-svg { width: 100%; max-width: 420px; display: block; }
.gb-grid { stroke: #eef2f7; stroke-width: 1; }
.gb-axis { stroke: #cbd5e1; stroke-width: 1.2; }
.gb-pt { fill: #dc2626; stroke: #fff; stroke-width: 1; }
.gb-json {
  border: 1px dashed var(--border); border-radius: var(--radius-md);
  padding: 8px 12px; font-size: 12px; color: var(--text-secondary);
  background: rgba(241, 245, 249, 0.5);
}
.gb-json summary { cursor: pointer; font-weight: 600; }
.gb-json pre { margin-top: 8px; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: 11px; }
.gb-steps { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.gb-step { display: flex; gap: 8px; align-items: baseline; font-size: 12px; }
.gb-step-no {
  flex-shrink: 0; width: 18px; height: 18px; border-radius: 50%; background: var(--primary);
  color: #fff; font-size: 11px; display: inline-flex; align-items: center; justify-content: center;
}
</style>
