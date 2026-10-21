/**
 * solids —— 立体几何斜二测 2D 绘制（教材标准直观图画法）
 * 局部 3D 约定与 section.ts 一致：x 右、y 深（远）、z 上。
 * 投影：screen = [x + K·y, z + K·y]，K = √2/4（45° 半缩，斜二测）。
 * 被遮挡棱（过最远角的三条棱）画虚线；截面用金色粗线 + 半透明填充。
 * 全部用 board.create 的 2D 元素：快照导出、jsdom 测试、缩放都天然稳定。
 */
import { CUBE_EDGES, CUBE_VERTS, pointOnEdge, sectionPolygon, type V3 } from '../section'

export const ISO_K = Math.SQRT1_2 / 2

/** 3D → 2D 屏幕坐标（未缩放、未居中） */
export function isoProject(x: number, y: number, z: number): [number, number] {
  return [x + ISO_K * y, z + ISO_K * y]
}

export interface SolidOpts {
  color: string
  width: number
  showLabels?: boolean
  /** 图形整体缩放（默认 2.2，使默认参数下宽约 6 单位） */
  scale?: number
}

const HIDDEN_COLOR = '#9aa6b8'

/* ---------- 基础绘制小工具（board 由调用方传入，本模块不依赖 jsxgraph） ---------- */

function seg(board: any, a: [number, number], b: [number, number], o: SolidOpts, hidden = false) {
  board.create('segment', [a, b], {
    strokeColor: hidden ? HIDDEN_COLOR : o.color,
    strokeWidth: hidden ? Math.max(1, o.width - 1) : o.width,
    dash: hidden ? 2 : 0,
    highlight: false,
    fixed: true,
  })
}

function curveArc(board: any, fx: (t: number) => number, fy: (t: number) => number, t0: number, t1: number, o: SolidOpts, hidden = false) {
  board.create('curve', [fx, fy, t0, t1], {
    strokeColor: hidden ? HIDDEN_COLOR : o.color,
    strokeWidth: hidden ? Math.max(1, o.width - 1) : o.width,
    dash: hidden ? 2 : 0,
    highlight: false,
    fixed: true,
  })
}

function dot(board: any, p: [number, number], name: string, o: SolidOpts, gold = false) {
  const c = gold ? '#c99735' : o.color
  board.create('point', p, {
    size: 2.6, name, strokeColor: c, fillColor: c, fixed: true, highlight: false,
    label: { fontSize: 14, offset: [6, 6], strokeColor: c },
  })
}

/* ---------- 各立体图形（3D 局部坐标 → 投影 + 缩放居中） ---------- */

type P = (v: V3) => [number, number]

function projector(cx: number, cy: number, cz: number, scale: number): P {
  return (v) => {
    const x = (v[0] - cx) * scale
    const y = (v[1] - cy) * scale
    const z = (v[2] - cz) * scale
    return isoProject(x, y, z)
  }
}

function boxSolids(board: any, a: number, b: number, c: number, o: SolidOpts, labels: string[]) {
  // a=x 宽, b=y 深, c=z 高；最远角 D=(0,b,0)，过 D 的三条棱虚线
  const V: V3[] = CUBE_VERTS.map(([ix, iy, iz]) => [ix * a, iy * b, iz * c])
  const p = projector(a / 2, b / 2, c / 2, o.scale ?? 2.2)
  const D = 3 // CUBE_VERTS[3] = (0,1,0)
  for (const [ai, bi] of CUBE_EDGES) {
    seg(board, p(V[ai]), p(V[bi]), o, ai === D || bi === D)
  }
  if (o.showLabels) {
    for (let i = 0; i < labels.length; i++) dot(board, p(V[i]), labels[i], o)
  }
}

function drawCube(board: any, prm: Record<string, number>, o: SolidOpts) {
  const s = prm.s ?? 2
  boxSolids(board, s, s, s, o, o.showLabels ? ['A', 'B', 'C', 'D', "A'", "B'", "C'", "D'"] : [])
}

function drawCuboid(board: any, prm: Record<string, number>, o: SolidOpts) {
  boxSolids(board, prm.a ?? 3, prm.b ?? 2, prm.c ?? 2, o, o.showLabels ? ['A', 'B', 'C', 'D', "A'", "B'", "C'", "D'"] : [])
}

function drawPyramid(board: any, n: number, prm: Record<string, number>, o: SolidOpts) {
  // 底面正 n 边形（斜二测），顶点 S 在底面中心上方 h；后侧棱/后底边虚线
  const r = prm.r ?? 1.6, h = prm.h ?? 2, sc = o.scale ?? 2.2
  const base: V3[] = []
  for (let i = 0; i < n; i++) {
    const t = -Math.PI / 2 + (i / n) * Math.PI * 2
    base.push([r * Math.cos(t), r * Math.sin(t), 0])
  }
  // 旋转底面让一条边正对观察者（前边水平）
  const rot = Math.PI / 2 + Math.PI / n
  const rotV = (v: V3): V3 => [
    v[0] * Math.cos(rot) - v[1] * Math.sin(rot),
    v[0] * Math.sin(rot) + v[1] * Math.cos(rot),
    v[2],
  ]
  const V = base.map(rotV)
  const S: V3 = [0, 0, h]
  const p = projector(0, 0, h / 2, sc)
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n
    const back = (V[i][1] + V[j][1]) / 2 > 0.01 // 整条底边在后方 → 虚线
    seg(board, p(V[i]), p(V[j]), o, back)
    seg(board, p(V[i]), p(S), o, V[i][1] > 0.01) // 远侧棱虚线
  }
  dot(board, p(S), 'S', o)
  if (o.showLabels) for (let i = 0; i < Math.min(n, 8); i++) dot(board, p(V[i]), String.fromCharCode(65 + i), o)
}

/** 正 n 棱柱：上下正 n 边形底面 + n 条侧棱；远底边与远侧棱虚线 */
function drawPrism(board: any, prm: Record<string, number>, o: SolidOpts) {
  const n = Math.max(3, Math.min(8, Math.round(prm.n ?? 3)))
  const r = prm.r ?? 1.4, h = prm.h ?? 2, sc = o.scale ?? 2.2
  const base: V3[] = []
  for (let i = 0; i < n; i++) {
    const t = -Math.PI / 2 + (i / n) * Math.PI * 2
    base.push([r * Math.cos(t), r * Math.sin(t), 0])
  }
  const rot = Math.PI / 2 + Math.PI / n
  const rotV = (v: V3): V3 => [
    v[0] * Math.cos(rot) - v[1] * Math.sin(rot),
    v[0] * Math.sin(rot) + v[1] * Math.cos(rot),
    v[2],
  ]
  const B = base.map(rotV)
  const T = B.map((v) => [v[0], v[1], h] as V3)
  const p = projector(0, 0, h / 2, sc)
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n
    const back = (B[i][1] + B[j][1]) / 2 > 0.01
    seg(board, p(B[i]), p(B[j]), o, back) // 下底：远边虚线
    seg(board, p(T[i]), p(T[j]), o) // 上底：全实线
    seg(board, p(B[i]), p(T[i]), o, B[i][1] > 0.01) // 侧棱：远侧棱虚线
  }
  if (o.showLabels) {
    for (let i = 0; i < Math.min(n, 6); i++) {
      dot(board, p(B[i]), String.fromCharCode(65 + i), o)
      dot(board, p(T[i]), `${String.fromCharCode(65 + i)}'`, o)
    }
  }
}

/** 正四面体：底面外接半径 a/√3，高 a√(2/3)（六条棱全等的标准画法） */
function drawTetrahedron(board: any, prm: Record<string, number>, o: SolidOpts) {
  const a = prm.a ?? 2.6
  drawPyramid(board, 3, { r: a / Math.sqrt(3), h: a * Math.sqrt(2 / 3) }, o)
}

/** 椭圆底面：θ∈(0,π) 后半（y>0）虚线，前半实线 */
function ellipseBase(board: any, r: number, z: number, o: SolidOpts, p: (v: V3) => [number, number], hiddenBack: boolean) {
  const fx = (t: number) => p([r * Math.cos(t), r * Math.sin(t), z])[0]
  const fy = (t: number) => p([r * Math.cos(t), r * Math.sin(t), z])[1]
  curveArc(board, fx, fy, Math.PI, Math.PI * 2, o) // 前半实线
  if (hiddenBack) curveArc(board, fx, fy, 0, Math.PI, o, true)
}

function drawCylinder(board: any, prm: Record<string, number>, o: SolidOpts) {
  const r = prm.r ?? 1, h = prm.h ?? 2, sc = o.scale ?? 2.2
  const p = projector(0, 0, h / 2, sc)
  ellipseBase(board, r, h, o, p, false) // 上底：全实线
  ellipseBase(board, r, 0, o, p, true) // 下底：后虚线
  seg(board, p([-r, 0, 0]), p([-r, 0, h]), o)
  seg(board, p([r, 0, 0]), p([r, 0, h]), o)
  if (o.showLabels) {
    dot(board, p([0, 0, h / 2]), 'O', o)
    seg(board, p([0, 0, 0]), p([0, 0, h]), { ...o, width: 1 }, true)
  }
}

function drawCone(board: any, prm: Record<string, number>, o: SolidOpts) {
  const r = prm.r ?? 1.2, h = prm.h ?? 2.2, sc = o.scale ?? 2.2
  const p = projector(0, 0, h / 2, sc)
  ellipseBase(board, r, 0, o, p, true)
  seg(board, p([-r, 0, 0]), p([0, 0, h]), o)
  seg(board, p([r, 0, 0]), p([0, 0, h]), o)
  dot(board, p([0, 0, h]), 'S', o)
}

function drawFrustum(board: any, prm: Record<string, number>, o: SolidOpts) {
  const r1 = prm.r1 ?? 1.6, r2 = prm.r2 ?? 0.9, h = prm.h ?? 2, sc = o.scale ?? 2.2
  const p = projector(0, 0, h / 2, sc)
  ellipseBase(board, r2, h, o, p, false)
  ellipseBase(board, r1, 0, o, p, true)
  seg(board, p([-r2, 0, h]), p([-r1, 0, 0]), o)
  seg(board, p([r2, 0, h]), p([r1, 0, 0]), o)
}

function drawSphere(board: any, prm: Record<string, number>, o: SolidOpts) {
  const r = prm.r ?? 1.6, sc = o.scale ?? 2.2
  const p = projector(0, 0, 0, sc)
  // 大圆（正投影）+ 赤道圆（斜二测椭圆，后半虚线）+ 一条经线
  const [ox, oy] = p([0, 0, 0])
  board.create('circle', [[ox, oy], r * sc], {
    strokeColor: o.color, strokeWidth: o.width, highlight: false, fixed: true,
  })
  ellipseBase(board, r, 0, o, p, true)
  const rx = (t: number) => p([r * Math.sin(t) * 0.32, 0, r * Math.cos(t)])[0]
  const ry = (t: number) => p([r * Math.sin(t) * 0.32, 0, r * Math.cos(t)])[1]
  curveArc(board, rx, ry, 0, Math.PI * 2, o, true)
  if (o.showLabels) {
    const [nx, ny] = p([0, 0, r])
    board.create('text', [nx, ny + 0.35, 'N'], { fontSize: 14, color: o.color, anchorX: 'middle', fixed: true, highlight: false })
  }
}

/* ---------- 绕轴旋转（Rodrigues）：展开图的几何基础 ---------- */
function rotateAround(p: V3, a: V3, b: V3, ang: number): V3 {
  const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2]
  const len = Math.hypot(ux, uy, uz) || 1
  const nx = ux / len, ny = uy / len, nz = uz / len
  const vx = p[0] - a[0], vy = p[1] - a[1], vz = p[2] - a[2]
  const c = Math.cos(ang), s = Math.sin(ang)
  const dot = nx * vx + ny * vy + nz * vz
  const cx = ny * vz - nz * vy, cy = nz * vx - nx * vz, cz = nx * vy - ny * vx
  return [
    a[0] + vx * c + cx * s + nx * dot * (1 - c),
    a[1] + vy * c + cy * s + ny * dot * (1 - c),
    a[2] + vz * c + cz * s + nz * dot * (1 - c),
  ]
}

/** 正方体展开图（十字 1-4-1 型）：t=0 收起为正方体直观图，t=1 完全展开摊平；六面同色填充便于折叠前后对照 */
function drawCubeNet(board: any, prm: Record<string, number>, o: SolidOpts) {
  const s = prm.s ?? 1.6
  const t = Math.max(0, Math.min(1, prm.t ?? 1))
  // s·sc ≈ 2.4 常数：t=1 完全展开时总宽约 12 单位，恰好铺满画布
  const sc = (o.scale ?? 2.4) / s
  const p = projector(0, s / 2, s / 2, sc)
  const q = (v: V3) => p(v)
  const θ = (t * Math.PI) / 2

  const face = (pts: V3[]) => {
    board.create('polygon', pts.map(q), {
      fillColor: 'rgba(15, 71, 135, 0.10)', fillOpacity: 0.8,
      borders: { strokeColor: o.color, strokeWidth: o.width, highlight: false, fixed: true },
      vertices: { visible: false }, highlight: false, fixed: true,
    })
  }

  const bottom: V3[] = [[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0]]
  // 四个侧面：贴底面一条边竖直放置，绕该边向外旋转 θ（展开时摊平到 z=0 平面）
  const sideDefs: { a: V3; b: V3; sign: 1 | -1; axis: 'x' | 'y' }[] = [
    { a: [0, 0, 0], b: [s, 0, 0], sign: 1, axis: 'x' },
    { a: [s, 0, 0], b: [s, s, 0], sign: 1, axis: 'y' },
    { a: [s, s, 0], b: [0, s, 0], sign: -1, axis: 'x' },
    { a: [0, s, 0], b: [0, 0, 0], sign: -1, axis: 'y' },
  ]
  const sides: V3[][] = sideDefs.map(({ a, b, sign, axis }) => {
    const raw: V3[] = [a, b, [b[0], b[1], s], [a[0], a[1], s]]
    const axisEnd: V3 = axis === 'x' ? [a[0] + 1, a[1], a[2]] : [a[0], a[1] + 1, a[2]]
    return raw.map((v) => rotateAround(v, a, axisEnd, sign * θ))
  })
  // 顶面：先随侧面 3（绕 x=0 边）一起旋转，再绕其展开后的上边继续向外转 θ
  const e3 = sideDefs[3].a
  const axis3: V3 = [e3[0], e3[1] + 1, e3[2]]
  const topRaw: V3[] = [[0, 0, s], [s, 0, s], [s, s, s], [0, s, s]]
  const step1 = topRaw.map((v) => rotateAround(v, e3, axis3, -θ))
  const hingeA = rotateAround([0, 0, s], e3, axis3, -θ)
  const hingeB = rotateAround([0, s, s], e3, axis3, -θ)
  const top = step1.map((v) => rotateAround(v, hingeA, hingeB, -θ))

  face(bottom)
  sides.forEach(face)
  face(top)
  if (o.showLabels) {
    const c0 = q([s / 2, s / 2, 0])
    board.create('text', [c0[0], c0[1], '下底面'], { fontSize: 13, color: o.color, anchorX: 'middle', anchorY: 'middle', fixed: true, highlight: false })
    const ct = top.map((v) => [v[0] / 4, v[1] / 4, v[2] / 4] as V3).reduce((acc, v) => [acc[0] + v[0], acc[1] + v[1], acc[2] + v[2]] as V3)
    const ctq = q(ct)
    board.create('text', [ctq[0], ctq[1], '上底面'], { fontSize: 13, color: o.color, anchorX: 'middle', anchorY: 'middle', fixed: true, highlight: false })
  }
}

function drawCubeSection(board: any, prm: Record<string, number>, o: SolidOpts) {
  const s = 2
  const t1 = prm.t1 ?? 0.5, t2 = prm.t2 ?? 0.55, t3 = prm.t3 ?? 0.45
  const sc = o.scale ?? 2.2
  const V: V3[] = CUBE_VERTS.map(([ix, iy, iz]) => [ix * s, iy * s, iz * s])
  const p = projector(s / 2, s / 2, s / 2, sc)
  const D = 3
  const bodyOpts: SolidOpts = { ...o, width: Math.max(1.6, o.width - 0.6), showLabels: o.showLabels }
  for (const [ai, bi] of CUBE_EDGES) {
    seg(board, p(V[ai]), p(V[bi]), bodyOpts, ai === D || bi === D)
  }
  if (o.showLabels) {
    for (let i = 0; i < 8; i++) dot(board, p(V[i]), ['A', 'B', 'C', 'D', "A'", "B'", "C'", "D'"][i], bodyOpts)
  }
  // 取点：M 在 AB 上、N 在 CC₁ 上、P 在 DD₁ 上（教材截面题常见取法）
  const M = pointOnEdge(V[0], V[1], t1)
  const N = pointOnEdge(V[2], V[6], t2)
  const P = pointOnEdge(V[3], V[7], t3)
  const sec = sectionPolygon(V, CUBE_EDGES, M, N, P)
  if (sec.length >= 3) {
    const pts = sec.map((v) => p(v))
    board.create('polygon', pts, {
      fillColor: '#c99735', fillOpacity: 0.22,
      borders: { strokeColor: '#c99735', strokeWidth: o.width + 1, highlight: false, fixed: true },
      vertices: { visible: false },
      highlight: false, fixed: true,
    })
  }
  dot(board, p(M), 'M', o, true)
  dot(board, p(N), 'N', o, true)
  dot(board, p(P), 'P', o, true)
}

/** 统一入口：按 presetId 在 board 上绘制斜二测立体图形 */
export function drawSolid2d(board: any, presetId: string, prm: Record<string, number>, o: SolidOpts): void {
  switch (presetId) {
    case 'solid/cube': drawCube(board, prm, o); break
    case 'solid/cuboid': drawCuboid(board, prm, o); break
    case 'solid/pyramid': drawPyramid(board, Math.round(prm.n ?? 3), prm, o); break
    case 'solid/pyramid4': drawPyramid(board, 4, prm, o); break // 旧数据兼容：n=4 已被参数化预设覆盖
    case 'solid/prism': drawPrism(board, prm, o); break
    case 'solid/tetra': drawTetrahedron(board, prm, o); break
    case 'solid/cylinder': drawCylinder(board, prm, o); break
    case 'solid/cone': drawCone(board, prm, o); break
    case 'solid/frustum': drawFrustum(board, prm, o); break
    case 'solid/sphere': drawSphere(board, prm, o); break
    case 'solid/cube-net': drawCubeNet(board, prm, o); break
    case 'solid/cube-section': drawCubeSection(board, prm, o); break
  }
}

/** 平移记录预设是否为立体图形（FreeMode 分流用） */
export function isSolidPreset(presetId: string): boolean {
  return presetId.startsWith('solid/')
}
