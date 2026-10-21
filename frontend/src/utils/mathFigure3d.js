/**
 * MathFigure3D 图形场景归一化（纯函数，供 MathFigure3D.vue 与单测复用）。
 *
 * 契约对齐后端 stage_router._normalize_figure：前端只消费已采样的
 * 折线点集（curves[].points）与显式坐标，不执行任何数学表达式。
 * 非法项直接剔除；场景为空返回 null（组件显示兜底）。
 */

const MAX_COORD = 50
const DEFAULT_COLORS = ['#4f8ef7', '#ef6b5b', '#4cc49c', '#c084fc', '#f59e0b', '#38bdf8', '#fb7185', '#a3e635']
const COLOR_RE = /^#[0-9a-fA-F]{6}$/

// 最小可读尺寸保护：与后端 _MIN_SOLID_SIZE / _MIN_RADIUS 保持一致。
// LLM 偶尔输出半径 0.1、边长 0.05 之类的"挤在原点"坐标，
// 渲染时几何体几乎不可见，本保护把它们拉到最低可读尺寸。
const MIN_SOLID_SIZE = 0.8
const MIN_RADIUS = 0.55
// 单个几何体最长边期望长度（自适应取景目标）。3.0 是经验值，
// 物体占视野约 60% 时观感最好（4.0 会让相机距离过远，物体反而更小）。
const TARGET_SPAN = 3.0
// 触发自适应缩放的最大跨度：只对 span < 2.0 的"挤在原点"坐标做放大。
const SCALE_BELOW = 2.0

function num(v) {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

/** 把 cylinder/cone 的轴向"立起来"为 Y 轴（向上）。与后端 _orient_vertical 一致。
 *
 * LLM 经常把圆柱/圆锥的 height 写在 Z 方向（base.y == top.y），
 * 导致"躺"在地面上观感极差。判定：Y 分量 < 水平分量时把轴旋转为 +Y。
 */
function orientVertical(base, top) {
  if (!base || !top) return [base, top]
  const ax = [top[0] - base[0], top[1] - base[1], top[2] - base[2]]
  const h = Math.hypot(ax[0], ax[1], ax[2])
  if (h < 1e-6) return [base, top]
  const absY = Math.abs(ax[1])
  const absXZ = Math.hypot(ax[0], ax[2])
  if (absY >= absXZ) return [base, top] // 已经是上下方向，不动
  const newTop = [
    Math.round(base[0] * 10000) / 10000,
    Math.round((base[1] + h) * 10000) / 10000,
    Math.round(base[2] * 10000) / 10000,
  ]
  return [base, newTop]
}

/** 点集整体缩放到目标跨度（保持相对位置）。
 *  与后端 _scale_to_target 一致：只对 span < SCALE_BELOW 的紧凑坐标放大。 */
function scaleToTarget(points3, center) {
  if (!points3 || !points3.length) return points3
  if (!center) {
    center = [0, 0, 0]
    for (const p of points3) for (let i = 0; i < 3; i++) center[i] += p[i]
    for (let i = 0; i < 3; i++) center[i] /= points3.length
  }
  let span = 0
  for (const p of points3) for (let i = 0; i < 3; i++) span = Math.max(span, Math.abs(p[i] - center[i]))
  if (span < 1e-6 || span >= SCALE_BELOW) return points3
  const k = TARGET_SPAN / span
  return points3.map((p) => [0, 1, 2].map((i) => Math.round((center[i] + (p[i] - center[i]) * k) * 10000) / 10000))
}

/** 坐标三元组：3 个 ≤50 的有限数字，否则 null */
export function toPt(v) {
  if (!Array.isArray(v) || v.length < 3) return null
  const out = []
  for (const c of v.slice(0, 3)) {
    const n = num(c)
    if (n === null || Math.abs(n) > MAX_COORD) return null
    out.push(Math.round(n * 10000) / 10000)
  }
  return out
}

function color(v, fallback) {
  return typeof v === 'string' && COLOR_RE.test(v) ? v : fallback
}

function opacityOf(v, def) {
  const n = num(v)
  return n === null ? def : Math.min(Math.max(n, 0.08), 0.8)
}

export function normalizeScene(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const scene = {}

  const caption = String(raw.caption ?? '').trim().slice(0, 40)
  if (caption) scene.caption = caption
  scene.grid = raw.grid !== false
  scene.axes = raw.axes !== false

  const camera = raw.camera
  if (camera && typeof camera === 'object') {
    const pos = toPt(camera.pos)
    const target = toPt(camera.target)
    if (pos || target) scene.camera = { ...(pos ? { pos } : {}), ...(target ? { target } : {}) }
  }

  const solids = []
  ;(Array.isArray(raw.solids) ? raw.solids : []).slice(0, 8).forEach((s, i) => {
    if (!s || typeof s !== 'object') return
    const kind = String(s.kind || 'box')
    const known = kind === 'box' || kind === 'polyhedron' || kind === 'prism' || kind === 'pyramid' || kind === 'cylinder' || kind === 'cone' || kind === 'sphere'
    if (!known) return
    const item = { kind, color: color(s.color, DEFAULT_COLORS[i % DEFAULT_COLORS.length]), opacity: opacityOf(s.opacity, 0.35) }
    let ok = true
    if (kind === 'box') {
      const center = toPt(s.center)
      const size = toPt(s.size || s.dimensions)
      if (s.center != null && !center) ok = false
      else if (size && size.every((v) => v > 0 && v <= MAX_COORD)) {
        // 最小可读尺寸保护：过小的几何体放大到 MIN_SOLID_SIZE
        item.center = center || [0, 0, 0]
        item.size = size.map((v) => Math.max(Math.round(v * 10000) / 10000, MIN_SOLID_SIZE))
      } else ok = false
    } else if (kind === 'polyhedron') {
      // 顶点：LLM 经常给空 name 或重复 name（如 A,A,A,A,B,B,B,B），导致后续 edges
      // 引用 name 时几乎全部被过滤掉。统一重命名为 v0..vN，并把原 name 存为
      // _label（用于显示标签），保证 edges 一定能匹配上。
      const rawVerts = (Array.isArray(s.vertices) ? s.vertices : []).slice(0, 30)
        .map((pt) => (pt && typeof pt === 'object' && toPt(pt.pos) ? { name: String(pt.name ?? '').slice(0, 8), pos: toPt(pt.pos) } : null))
        .filter(Boolean)
      if (rawVerts.length) {
        const scaled = scaleToTarget(rawVerts.map((v) => v.pos))
        const finalVerts = scaled ? rawVerts.map((v, i) => ({ name: `v${i}`, pos: scaled[i] })) : rawVerts.map((v, i) => ({ name: `v${i}`, pos: v.pos }))
        item.vertices = finalVerts
        const names = new Set(finalVerts.map((v) => v.name))
        // 原 name → 下标 映射（处理 LLM 用的 A/B/C 等字母）。
        // 同时支持整数下标引用（LLM 可能写 [0, 4] 这种）。
        const origToIndex = new Map(rawVerts.map((v, i) => [v.name, i]))
        const edges = (Array.isArray(s.edges) ? s.edges : []).slice(0, 60)
          .map((e) => {
            if (!Array.isArray(e) || e.length !== 2) return null
            let ai = origToIndex.get(String(e[0]))
            let bi = origToIndex.get(String(e[1]))
            if (ai == null && /^\d+$/.test(String(e[0]))) ai = parseInt(String(e[0]), 10)
            if (bi == null && /^\d+$/.test(String(e[1]))) bi = parseInt(String(e[1]), 10)
            if (ai == null || bi == null) return null
            if (ai < 0 || ai >= finalVerts.length || bi < 0 || bi >= finalVerts.length) return null
            if (ai === bi) return null
            return [`v${ai}`, `v${bi}`]
          })
          .filter((e) => e && names.has(e[0]) && names.has(e[1]))
        if (edges.length) item.edges = edges
        // 顶点 labels（用于画 A/B/C 等角标）：保留原 name（去重后）
        const seenLabels = new Set()
        const labels = []
        finalVerts.forEach((v, i) => {
          const orig = rawVerts[i] && rawVerts[i].name
          if (orig && !seenLabels.has(orig) && !/^v\d+$/.test(orig)) {
            seenLabels.add(orig)
            labels.push({ pos: v.pos, text: orig })
          }
        })
        if (labels.length) item.labels = labels
      } else ok = false
    } else if (kind === 'prism') {
      const bottom = (Array.isArray(s.bottom) ? s.bottom : []).slice(0, 10).map(toPt)
      const top = (Array.isArray(s.top) ? s.top : []).slice(0, 10).map(toPt)
      if (bottom.length >= 3 && bottom.length === top.length && bottom.every(Boolean) && top.every(Boolean)) {
        // 上下底面 + 顶面整体一起算 span，再统一缩放（避免中心错位）
        const all = [...bottom, ...top]
        const sc = scaleToTarget(all)
        if (sc) {
          item.bottom = sc.slice(0, bottom.length)
          item.top = sc.slice(bottom.length)
        } else {
          item.bottom = bottom
          item.top = top
        }
      } else ok = false
    } else if (kind === 'pyramid') {
      const base = (Array.isArray(s.base) ? s.base : []).slice(0, 8).map(toPt)
      const apex = toPt(s.apex)
      if (base.length >= 3 && base.every(Boolean) && apex) {
        // 底面+顶点一起缩放，保持锥形
        const all = [...base, apex]
        const sc = scaleToTarget(all)
        if (sc) {
          item.base = sc.slice(0, base.length)
          item.apex = sc[sc.length - 1]
        } else {
          item.base = base
          item.apex = apex
        }
      } else ok = false
    } else if (kind === 'cylinder' || kind === 'cone') {
      const baseRaw = toPt(s.base)
      const topRaw = toPt(s.top || s.apex)
      const r = num(s.radius)
      if (baseRaw) {
        // 最小半径保护
        const r2 = r && r > 0 && r <= MAX_COORD ? Math.max(Math.round(r * 10000) / 10000, MIN_RADIUS) : 1
        if (topRaw) {
          const ax = [topRaw[0] - baseRaw[0], topRaw[1] - baseRaw[1], topRaw[2] - baseRaw[2]]
          const h = Math.hypot(ax[0], ax[1], ax[2])
          if (h < MIN_SOLID_SIZE * 1.2) {
            // 高度过短：方向向上拉伸（保持 base 不动）
            const dir = ax[1] >= 0 ? [0, 1, 0] : [0, -1, 0]
            item.base = baseRaw
            item.top = [
              Math.round((baseRaw[0] + dir[0] * Math.max(MIN_SOLID_SIZE * 2, r2 * 2)) * 10000) / 10000,
              Math.round((baseRaw[1] + dir[1] * Math.max(MIN_SOLID_SIZE * 2, r2 * 2)) * 10000) / 10000,
              Math.round((baseRaw[2] + dir[2] * Math.max(MIN_SOLID_SIZE * 2, r2 * 2)) * 10000) / 10000,
            ]
          } else {
            // 高度合理：检测轴向是否"太平"，是则旋转为 Y（关键：避免"躺"地上）
            const [b2, t2] = orientVertical(baseRaw, topRaw)
            item.base = b2
            item.top = t2
          }
        } else {
          // cylinder/cone 必须有 top，否则按 +y 方向补一个
          item.base = baseRaw
          item.top = [
            baseRaw[0],
            Math.round((baseRaw[1] + Math.max(MIN_SOLID_SIZE * 2, r2 * 2)) * 10000) / 10000,
            baseRaw[2],
          ]
        }
        item.radius = r2
      } else ok = false
    } else if (kind === 'sphere') {
      const center = toPt(s.center)
      const r = num(s.radius)
      if (s.center != null && !center) ok = false
      else {
        // 最小半径保护
        const r2 = r && r > 0 && r <= MAX_COORD ? Math.max(Math.round(r * 10000) / 10000, MIN_RADIUS) : 1
        item.center = center || [0, 0, 0]
        item.radius = r2
      }
    }
    const labels = (Array.isArray(s.labels) ? s.labels : []).slice(0, 12)
      .map((lb) => (lb && typeof lb === 'object' && toPt(lb.pos) ? { pos: toPt(lb.pos), text: String(lb.text ?? '').slice(0, 8) } : null))
      .filter(Boolean)
    if (labels.length) item.labels = labels
    if (ok) solids.push(item)
  })
  if (solids.length) scene.solids = solids

  const curves = []
  ;(Array.isArray(raw.curves) ? raw.curves : []).slice(0, 4).forEach((c) => {
    if (!c || typeof c !== 'object') return
    const pts = (Array.isArray(c.points) ? c.points : []).slice(0, 200).map(toPt).filter(Boolean)
    if (pts.length < 2) return
    curves.push({ kind: 'polyline', points: pts, closed: !!c.closed, color: color(c.color, '#ef4444') })
  })
  if (curves.length) scene.curves = curves

  const planes = []
  ;(Array.isArray(raw.planes) ? raw.planes : []).slice(0, 3).forEach((p) => {
    if (!p || typeof p !== 'object') return
    const pts = (Array.isArray(p.points) ? p.points : []).slice(0, 3).map(toPt)
    if (pts.length === 3 && pts.every(Boolean)) {
      planes.push({ points: pts, color: color(p.color, '#f59e0b'), opacity: opacityOf(p.opacity, 0.22) })
    }
  })
  if (planes.length) scene.planes = planes

  const segments = []
  ;(Array.isArray(raw.segments) ? raw.segments : []).slice(0, 10).forEach((s) => {
    if (!s || typeof s !== 'object') return
    const a = toPt(s.a)
    const b = toPt(s.b)
    if (a && b) {
      segments.push({ a, b, dashed: !!s.dashed, color: color(s.color, '#64748b'), label: String(s.label ?? '').trim().slice(0, 8) || null })
    }
  })
  if (segments.length) scene.segments = segments

  const hasContent = scene.solids?.length || scene.curves?.length || scene.planes?.length || scene.segments?.length
  return hasContent ? scene : null
}