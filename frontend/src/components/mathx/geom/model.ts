/**
 * geom/model —— 立体几何结构化模型（阶段 C 新建，纯逻辑层，不依赖 DOM/Vue）
 *
 * 设计对齐阶段 A 实测失败（docs/draw-reality/report/phaseA-verdict.md）：
 * - F1 无平行六面体/任意底面 → 立体 = 顶点(可命名) + 棱 + 面 的参数化构造，非封闭图元
 * - F2 无棱上比例点/中点 → onEdge/midpoint/ratio 依附定义，拖动参数即重算
 * - F4 无交点构造 → intersect 两线段求交（共面校验，不装懂）
 * - F3 无虚线语义 → solid 棱按凸多面体朝向自动判可见性；用户线段按"内部采样"自动 + 手动覆盖
 * - F5 标签跟随 → label 挂在对象上（渲染层输出 SVG <text>），改名即改对象
 * - 依赖模型：点只依赖更早点（无环），resolve 求值；撤销 = 快照栈
 */

export type V3 = [number, number, number]

export const v3 = {
  add: (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  scale: (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k],
  lerp: (a: V3, b: V3, t: number): V3 => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t],
  dot: (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  len: (a: V3): number => Math.hypot(a[0], a[1], a[2]),
  dist: (a: V3, b: V3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]),
}

/* ---------- 视角（正交轴测，可旋转） ---------- */

export interface GeomCamera { yaw: number; pitch: number; zoom: number }

export const DEFAULT_CAMERA: GeomCamera = { yaw: 0.62, pitch: 0.6, zoom: 60 }

/** 世界系 → 相机系：先绕 z 轴 yaw，再按 pitch 抬高视点；返回 [屏幕x, 屏幕上方向, 深度(越大越远)] */
export function project(v: V3, cam: GeomCamera): [number, number, number] {
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw)
  const x1 = v[0] * cy - v[1] * sy
  const y1 = v[0] * sy + v[1] * cy
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch)
  return [x1, v[2] * cp + y1 * sp, y1 * cp - v[2] * sp]
}

/** 相机方向（从场景指向相机，相机系下） */
function towardCamera(): V3 {
  return [0, -1, 0]
}

/* ---------- 对象模型 ---------- */

export type GId = string

/** 点的定义（依赖只指向更早存在的点） */
export type GPointDef =
  | { kind: 'free'; pos: V3 }
  | { kind: 'sum'; base: GId; vec: V3 }
  | { kind: 'onEdge'; a: GId; b: GId; t: number }
  | { kind: 'midpoint'; a: GId; b: GId }
  | { kind: 'ratio'; a: GId; b: GId; m: number; n: number; from: 'a' | 'b' }
  | { kind: 'intersect'; p: [GId, GId]; q: [GId, GId] }

export interface GPoint { id: GId; label: string; def: GPointDef }
export interface GSegment { id: GId; a: GId; b: GId; style: 'auto' | 'solid' | 'dashed'; color?: string }
export interface GVector { id: GId; a: GId; b: GId; color?: string }
export interface GSection { id: GId; p: [GId, GId, GId] }
export interface GSphere { id: GId; c: GId; r: number }
/** 圆锥：底面圆心 c + 半径 r，轴向世界 z 轴向上高 h */
export interface GCone { id: GId; kind: 'cone'; c: GId; r: number; h: number }
/** 圆柱：底面圆心 c + 半径 r，轴向世界 z 轴向上高 h */
export interface GCylinder { id: GId; kind: 'cylinder'; c: GId; r: number; h: number }
/** 平面板块：三点定平行四边形（第 4 顶点 = p2+p3−p1），做"两平行平面"示意 */
export interface GPlanePlate { id: GId; p: [GId, GId, GId]; plate: true; label?: string }
export interface GSolid {
  id: GId
  kind: 'parallelepiped' | 'cuboid' | 'cube' | 'pyramid4' | 'pyramidN' | 'tetra' | 'prism3'
  /** 顶点指向 doc 中的 GPoint id */
  verts: GId[]
  /** 棱 = 顶点下标对（可见性自动判定的拓扑依据） */
  edges: [number, number][]
  /** 面 = 顶点下标环（外法向由程序按凸体校正） */
  faces: number[][]
}
export type GObj = GPoint | GSegment | GVector | GSection | GSphere | GCone | GCylinder | GPlanePlate | GSolid

export interface GeomDoc {
  objects: GObj[]
  camera: GeomCamera
  seq: number
}

export const emptyDoc = (): GeomDoc => ({ objects: [], camera: { ...DEFAULT_CAMERA }, seq: 0 })

let _idc = 0
export const gid = (doc: GeomDoc, p = 'g') => `${p}${(++doc.seq).toString(36)}${(++_idc).toString(36)}`

/* ---------- 求值 ---------- */

export function pointPos(doc: GeomDoc, id: GId, seen: Set<GId> = new Set()): V3 {
  const pt = doc.objects.find((o) => o.id === id && o.hasOwnProperty('def')) as GPoint | undefined
  if (!pt) throw new Error(`point missing: ${id}`)
  if (seen.has(id)) throw new Error('dependency cycle')
  seen.add(id)
  const d = pt.def
  let out: V3
  switch (d.kind) {
    case 'free': out = [...d.pos] as V3; break
    case 'sum': out = v3.add(pointPos(doc, d.base, seen), d.vec); break
    case 'onEdge': out = v3.lerp(pointPos(doc, d.a, seen), pointPos(doc, d.b, seen), d.t); break
    case 'midpoint': out = v3.scale(v3.add(pointPos(doc, d.a, seen), pointPos(doc, d.b, seen)), 0.5); break
    case 'ratio': {
      const a = pointPos(doc, d.a, seen), b = pointPos(doc, d.b, seen)
      const k = d.m / (d.m + d.n)
      out = d.from === 'a' ? v3.lerp(a, b, k) : v3.lerp(b, a, k)
      break
    }
    case 'intersect': {
      const r = segIntersect(pointPos(doc, d.p[0], seen), pointPos(doc, d.p[1], seen), pointPos(doc, d.q[0], seen), pointPos(doc, d.q[1], seen))
      if (!r) throw new Error('segments do not intersect')
      out = r.point
      break
    }
  }
  seen.delete(id) // 路径栈语义：允许菱形依赖（B、C 共同引用 A），只挡真环
  return out
}

export interface SegXResult { s: number; t: number; point: V3 }

/** 空间两线段交点：最小二乘解 + 残差/范围校验 */
export function segIntersect(p1: V3, q1: V3, p2: V3, q2: V3): SegXResult | null {
  const d1 = v3.sub(q1, p1), d2 = v3.sub(q2, p2)
  const r = v3.sub(p1, p2)
  const a = v3.dot(d1, d1), b = v3.dot(d1, d2), c = v3.dot(d2, d2)
  const den = a * c - b * b
  if (Math.abs(den) < 1e-9) return null
  const rawS = (b * v3.dot(d2, r) - c * v3.dot(d1, r)) / den
  const rawT = (a * v3.dot(d2, r) - b * v3.dot(d1, r)) / den
  const x1 = v3.add(p1, v3.scale(d1, rawS))
  const x2 = v3.add(p2, v3.scale(d2, rawT))
  if (v3.dist(x1, x2) > 1e-6) return null
  if (rawS < -1e-9 || rawS > 1 + 1e-9 || rawT < -1e-9 || rawT > 1 + 1e-9) return null
  return { s: rawS, t: rawT, point: v3.lerp(x1, x2, 0.5) }
}

/* ---------- 凸体可见性 / 内部判定 ---------- */

interface FaceGeo { pts: V3[]; normal: V3 }

function solidFaces(doc: GeomDoc, s: GSolid): FaceGeo[] {
  const P = s.verts.map((id) => pointPos(doc, id))
  const center = P.reduce((acc, p) => v3.add(acc, p), [0, 0, 0] as V3)
  return s.faces.map((f) => {
    const pts = f.map((i) => P[i])
    const c = pts.reduce((acc, p) => v3.add(acc, p), [0, 0, 0] as V3)
    let n = v3.cross(v3.sub(pts[1], pts[0]), v3.sub(pts[2], pts[1]))
    // 外法向校正（凸体：面心 - 体心 应与法向同向）
    if (v3.dot(n, v3.sub(v3.scale(c, 1 / f.length), v3.scale(center, 1 / P.length))) < 0) n = v3.scale(n, -1)
    const L = v3.len(n) || 1
    return { pts, normal: v3.scale(n, 1 / L) }
  })
}

/** 相机系下判断面是否朝向相机 */
function faceVisible(nWorld: V3, cam: GeomCamera): boolean {
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw)
  const x1 = nWorld[0] * cy - nWorld[1] * sy
  const y1 = nWorld[0] * sy + nWorld[1] * cy
  return v3.dot([x1, y1, nWorld[2]], towardCamera()) < 0
}

/** solid 每条棱是否可见（凸体：至少一个邻接面朝向相机） */
export function solidEdgeVisibility(doc: GeomDoc, s: GSolid): boolean[] {
  const faces = solidFaces(doc, s)
  const fvis = faces.map((f) => faceVisible(f.normal, doc.camera))
  return s.edges.map(([a, b]) => {
    let vis = false
    for (let i = 0; i < s.faces.length; i++) {
      const f = s.faces[i]
      for (let j = 0; j < f.length; j++) {
        const u = f[j], w = f[(j + 1) % f.length]
        if ((u === a && w === b) || (u === b && w === a)) { vis = vis || fvis[i] }
      }
    }
    return vis
  })
}

/** solid 顶点是否可见（任一邻接面可见） */
export function solidVertVisibility(doc: GeomDoc, s: GSolid): boolean[] {
  const faces = solidFaces(doc, s)
  const fvis = faces.map((f) => faceVisible(f.normal, doc.camera))
  return s.verts.map((_, vi) => fvis.some((fv, fi) => s.faces[fi].includes(vi) && fv))
}

/** 点是否在凸体内部（严格内部） */
export function insideSolid(doc: GeomDoc, s: GSolid, p: V3): boolean {
  const faces = solidFaces(doc, s)
  return faces.every((f) => v3.dot(f.normal, v3.sub(p, f.pts[0])) < -1e-9)
}

/** 用户线段的自动虚实：中段深入体内部或两端都在被遮挡顶点上 → 虚线 */
export function autoDashed(doc: GeomDoc, aId: GId, bId: GId): boolean {
  const solids = doc.objects.filter((o) => (o as GSolid).edges) as GSolid[]
  if (!solids.length) return false
  const a = pointPos(doc, aId), b = pointPos(doc, bId)
  const mid = v3.lerp(a, b, 0.5)
  for (const s of solids) {
    if (insideSolid(doc, s, mid)) return true
    const hv = solidVertVisibility(doc, s)
    const ia = s.verts.indexOf(aId), ib = s.verts.indexOf(bId)
    if (ia >= 0 && ib >= 0 && !hv[ia] && !hv[ib]) return true
  }
  return false
}

/* ---------- 立体构造（顶点带标签入库，棱/面拓扑标准） ---------- */

const SUB = ['₀', '₁', '₂', '₃', '₄', '₅', '₆', '₇', '₈', '₉']
const sub1 = (n: number) => String(n).split('').map((c) => SUB[Number(c)]).join('')

function addSolidPoint(doc: GeomDoc, label: string, def: GPointDef): GPoint {
  const pt: GPoint = { id: gid(doc, 'v'), label, def }
  doc.objects.push(pt)
  return pt
}

function makeSolid(doc: GeomDoc, kind: GSolid['kind'], ids: GId[], edges: [number, number][], faces: number[][]): GSolid {
  const s: GSolid = { id: gid(doc, 's'), kind, verts: ids, edges, faces }
  doc.objects.push(s)
  return s
}

const BOX_EDGES: [number, number][] = [[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]]
const BOX_FACES: number[][] = [[0,1,2,3],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]

/** 平行六面体：A 为锚点（自由，可拖动整块变形），其余 7 顶点 = sum(A/B/…, 常向量) */
export function buildParallelepiped(doc: GeomDoc, opt?: { A?: V3; u?: V3; v?: V3; w?: V3; labels?: string[] }): GSolid {
  const A = opt?.A ?? [-2.2, 0, 0]
  const u = opt?.u ?? [4.4, 0, 0]
  const v = opt?.v ?? [0, 1.7, 0]
  const w = opt?.w ?? [0.8, 0, 2.6]
  const labels = opt?.labels || ['A', 'B', 'C', 'D', `A${sub1(1)}`, `B${sub1(1)}`, `C${sub1(1)}`, `D${sub1(1)}`]
  const a = addSolidPoint(doc, labels[0], { kind: 'free', pos: [...A] as V3 })
  const b = addSolidPoint(doc, labels[1], { kind: 'sum', base: a.id, vec: u })
  const c = addSolidPoint(doc, labels[2], { kind: 'sum', base: b.id, vec: v })
  const d = addSolidPoint(doc, labels[3], { kind: 'sum', base: a.id, vec: v })
  const a1 = addSolidPoint(doc, labels[4], { kind: 'sum', base: a.id, vec: w })
  const b1 = addSolidPoint(doc, labels[5], { kind: 'sum', base: a1.id, vec: u })
  const c1 = addSolidPoint(doc, labels[6], { kind: 'sum', base: b1.id, vec: v })
  const d1 = addSolidPoint(doc, labels[7], { kind: 'sum', base: a1.id, vec: v })
  return makeSolid(doc, 'parallelepiped', [a.id, b.id, c.id, d.id, a1.id, b1.id, c1.id, d1.id], BOX_EDGES, BOX_FACES)
}

export function buildCuboid(doc: GeomDoc, a = 4.2, b = 1.7, c = 2.6, cube = false): GSolid {
  void cube
  return buildParallelepiped(doc, { u: [a, 0, 0], v: [0, b, 0], w: [0, 0, c] })
}

export function buildPyramid4(doc: GeomDoc, opt?: { base?: V3[]; apex?: V3; labels?: string[]; apexLabel?: string }): GSolid {
  const base = opt?.base || [[-2, 0, 0], [2, 0, 0], [2, 1.6, 0], [-2, 1.6, 0]]
  const cx = base.reduce((s, p) => s + p[0], 0) / 4
  const cy = base.reduce((s, p) => s + p[1], 0) / 4
  const apex = opt?.apex || [cx - 0.3, cy / 2, 2.7]
  const labels = opt?.labels || ['A', 'B', 'C', 'D']
  const pts = [
    addSolidPoint(doc, labels[0], { kind: 'free', pos: [...base[0]] as V3 }),
    addSolidPoint(doc, labels[1], { kind: 'free', pos: [...base[1]] as V3 }),
    addSolidPoint(doc, labels[2], { kind: 'free', pos: [...base[2]] as V3 }),
    addSolidPoint(doc, labels[3], { kind: 'free', pos: [...base[3]] as V3 }),
    addSolidPoint(doc, opt?.apexLabel || 'P', { kind: 'free', pos: [...apex] as V3 }),
  ]
  return makeSolid(doc, 'pyramid4', pts.map((p) => p.id),
    [[0,1],[1,2],[2,3],[3,0],[0,4],[1,4],[2,4],[3,4]],
    [[0,1,2,3],[0,1,4],[1,2,4],[2,3,4],[3,0,4]])
}

export function buildTetra(doc: GeomDoc, labels?: string[]): GSolid {
  const a = 3.4
  const base: V3[] = [[-a / 2, -a * 0.29, 0], [a / 2, -a * 0.29, 0], [0, a * 0.58 - a * 0.29, 0]]
  const apex: V3 = [0, 0, a * 0.82]
  const ids = base.map((p, i) => addSolidPoint(doc, labels?.[i] || ['A', 'B', 'C'][i], { kind: 'free', pos: p }).id)
  ids.push(addSolidPoint(doc, labels?.[3] || 'P', { kind: 'free', pos: apex }).id)
  return makeSolid(doc, 'tetra', ids,
    [[0,1],[1,2],[2,0],[0,3],[1,3],[2,3]],
    [[0,1,2],[0,1,3],[1,2,3],[2,0,3]])
}

export function buildPrism3(doc: GeomDoc, opt?: { base?: V3[]; h?: number; labels?: string[] }): GSolid {
  const base = opt?.base || [[-1.6, 0.4, 0], [1.6, 0.4, 0], [-1.6, -0.9, 0]]
  const h = opt?.h ?? 2.8
  const labels = opt?.labels || ['A', 'B', 'C', `A${sub1(1)}`, `B${sub1(1)}`, `C${sub1(1)}`]
  const lows = base.map((p, i) => addSolidPoint(doc, labels[i], { kind: 'free', pos: [...p] as V3 }))
  const ups = lows.map((p, i) => addSolidPoint(doc, labels[3 + i], { kind: 'sum', base: p.id, vec: [0, 0, h] }))
  return makeSolid(doc, 'prism3', [...lows.map((p) => p.id), ...ups.map((p) => p.id)],
    [[0,1],[1,2],[2,0],[3,4],[4,5],[5,3],[0,3],[1,4],[2,5]],
    [[0,1,2],[3,4,5],[0,1,4,3],[1,2,5,4],[2,0,3,5]])
}

/* ---------- 球与经典外接球模型（教材通用图元） ---------- */

export function addSphere(doc: GeomDoc, centerId: GId, r: number): GSphere {
  const s: GSphere = { id: gid(doc, 'o'), c: centerId, r }
  doc.objects.push(s)
  return s
}

/** 三角形外接圆圆心（xz 平面内，y 为深度轴） */
function circumcenter2d(p: V3[]): [number, number, number] {
  const [a, b, c] = p
  const d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
  const ux = ((a[0] ** 2 + a[1] ** 2) * (b[1] - c[1]) + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1]) + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])) / d
  const uy = ((a[0] ** 2 + a[1] ** 2) * (c[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0]) + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])) / d
  return [ux, uy, a[2]]
}

/** 墙角模型：PA、PB、PC 两两垂直的三棱锥 + 外接球（R=√(a²+b²+c²)/2） */
export function buildCornerModel(doc: GeomDoc, opt?: { a?: number; b?: number; c?: number; labels?: string[] }): { tetra: GSolid; sphere: GSphere } {
  const a = opt?.a ?? 2.5, b = opt?.b ?? 2.3, c = opt?.c ?? 2.7
  const labels = opt?.labels || ['P', 'A', 'B', 'C']
  const p = addSolidPoint(doc, labels[0], { kind: 'free', pos: [0, 0, 0] })
  const va = addSolidPoint(doc, labels[1], { kind: 'sum', base: p.id, vec: [a, 0, 0] })
  const vb = addSolidPoint(doc, labels[2], { kind: 'sum', base: p.id, vec: [0, b, 0] })
  const vc = addSolidPoint(doc, labels[3], { kind: 'sum', base: p.id, vec: [0, 0, c] })
  const tetra = makeSolid(doc, 'tetra', [p.id, va.id, vb.id, vc.id],
    [[0,1],[0,2],[0,3],[1,2],[1,3],[2,3]],
    [[0,1,2],[0,1,3],[0,2,3],[1,2,3]])
  const o = addSolidPoint(doc, 'O', { kind: 'sum', base: p.id, vec: [a / 2, b / 2, c / 2] })
  const sphere = addSphere(doc, o.id, Math.sqrt(a * a + b * b + c * c) / 2)
  return { tetra, sphere }
}

/** 汉堡模型：直三棱柱 + 上下底面外接圆圆心 O₁/O₂ + 球心 O + 外接球 */
export function buildBurgerModel(doc: GeomDoc, opt?: { base?: V3[]; h?: number; labels?: string[] }): { prism: GSolid; sphere: GSphere } {
  const base = opt?.base || [[-1.7, 0.5, 0], [1.7, 0.5, 0], [0, -1.1, 0]]
  const h = opt?.h ?? 2.6
  const labels = opt?.labels || ['A', 'B', 'C', `A${sub1(1)}`, `B${sub1(1)}`, `C${sub1(1)}`]
  const lows = base.map((p, i) => addSolidPoint(doc, labels[i], { kind: 'free', pos: [...p] as V3 }))
  const ups = lows.map((p, i) => addSolidPoint(doc, labels[3 + i], { kind: 'sum', base: p.id, vec: [0, 0, h] }))
  const prism = makeSolid(doc, 'prism3', [...lows.map((p) => p.id), ...ups.map((p) => p.id)],
    [[0,1],[1,2],[2,0],[3,4],[4,5],[5,3],[0,3],[1,4],[2,5]],
    [[0,1,2],[3,4,5],[0,1,4,3],[1,2,5,4],[2,0,3,5]])
  const cc = circumcenter2d(base)
  const o1 = addSolidPoint(doc, `O${sub1(1)}`, { kind: 'sum', base: lows[0].id, vec: v3.sub(cc, base[0]) })
  const o2 = addSolidPoint(doc, `O${sub1(2)}`, { kind: 'sum', base: o1.id, vec: [0, 0, h] })
  const o = addSolidPoint(doc, 'O', { kind: 'sum', base: o1.id, vec: [0, 0, h / 2] })
  const r = Math.hypot(v3.dist(cc, base[0]), h / 2)
  const sphere = addSphere(doc, o.id, r)
  return { prism, sphere }
}

/* ---------- 圆锥 / 圆柱 / 平行平面板块 ---------- */

export function addCone(doc: GeomDoc, opt?: { c?: V3; r?: number; h?: number; centerLabel?: string; apexLabel?: string }): { cone: GCone; apex: GPoint; center: GPoint } {
  const c = opt?.c ?? [0, 0, 0]
  const r = opt?.r ?? 1.7, h = opt?.h ?? 3
  const center = addSolidPoint(doc, opt?.centerLabel ?? '', { kind: 'free', pos: [...c] as V3 })
  const apex = addSolidPoint(doc, opt?.apexLabel ?? 'P', { kind: 'sum', base: center.id, vec: [0, 0, h] })
  const cone: GCone = { id: gid(doc, 'n'), kind: 'cone', c: center.id, r, h }
  doc.objects.push(cone)
  return { cone, apex, center }
}

/** 底面圆周上的点（参数角，度；0° = +x 方向），作为圆心的 sum 依赖点 */
export function addConeRimPoint(doc: GeomDoc, coneC: GId, r: number, deg: number, label: string): GPoint {
  const t = (deg * Math.PI) / 180
  const rim: GPoint = { id: gid(doc, 'p'), label, def: { kind: 'sum', base: coneC, vec: [r * Math.cos(t), r * Math.sin(t), 0] } }
  doc.objects.push(rim)
  return rim
}

export function addCylinder(doc: GeomDoc, opt?: { c?: V3; r?: number; h?: number }): { cyl: GCylinder; center: GPoint } {
  const c = opt?.c ?? [0, 0, 0]
  const r = opt?.r ?? 1.5, h = opt?.h ?? 3
  const center = addSolidPoint(doc, 'O', { kind: 'free', pos: [...c] as V3 })
  const cyl: GCylinder = { id: gid(doc, 'y'), kind: 'cylinder', c: center.id, r, h }
  doc.objects.push(cyl)
  return { cyl, center }
}

export function addPlanePlate(doc: GeomDoc, p: [V3, V3, V3], label?: string): GPlanePlate {
  const ids = p.map((v) => addSolidPoint(doc, '', { kind: 'free', pos: [...v] as V3 }).id) as [GId, GId, GId]
  const plate: GPlanePlate = { id: gid(doc, 'f'), p: ids, plate: true, label }
  doc.objects.push(plate)
  return plate
}

/** 两平行平面（水平板块 α、β）+ 夹在中间的两条线段端点 A,B,C,D（A,C 在 β，B,D 在 α） */
export function buildParallelPlanes(doc: GeomDoc, opt?: { gap?: number }): { alpha: GPlanePlate; beta: GPlanePlate } {
  const gap = opt?.gap ?? 2.8
  const alpha = addPlanePlate(doc, [[-3.2, 1.5, gap], [1.2, 1.5, gap], [-1.4, -1.1, gap]], 'α')
  const beta = addPlanePlate(doc, [[-3.2, 1.5, 0], [1.2, 1.5, 0], [-1.4, -1.1, 0]], 'β')
  const endpts: [string, V3][] = [
    ['A', [-2.3, 0.6, 0]], ['B', [0.7, -0.4, gap]],
    ['C', [0.3, 1.0, 0]], ['D', [-1.7, -0.5, gap]],
  ]
  for (const [label, pos] of endpts) addSolidPoint(doc, label, { kind: 'free', pos })
  return { alpha, beta }
}

/** 球内接圆锥：顶点在球顶、底面圆在球面上（z0 选取底面高度），底面圆周附 M、N 两点 */
export function buildConeInSphere(doc: GeomDoc, opt?: { R?: number; z0?: number }): { sphere: GSphere; cone: GCone; apex: GPoint } {
  const R = opt?.R ?? 3
  const z0 = opt?.z0 ?? -R * 0.45
  const o = addSolidPoint(doc, 'O', { kind: 'free', pos: [0, 0, 0] })
  const sphere = addSphere(doc, o.id, R)
  const r0 = Math.sqrt(Math.max(0.1, R * R - z0 * z0))
  const { cone, apex } = addCone(doc, { c: [0, 0, z0], r: r0, h: R - z0, centerLabel: '' })
  // 顶点改挂球面：apex = O + (0,0,R)
  apex.def = { kind: 'sum', base: o.id, vec: [0, 0, R] }
  apex.label = 'P'
  addConeRimPoint(doc, cone.c, r0, 176, 'A')
  addConeRimPoint(doc, cone.c, r0, 4, 'B')
  addConeRimPoint(doc, cone.c, r0, 212, 'M')
  addConeRimPoint(doc, cone.c, r0, 332, 'N')
  return { sphere, cone, apex }
}

/* ---------- 点构造 ---------- */

export function addFreePoint(doc: GeomDoc, pos: V3, label?: string): GPoint {
  const pt: GPoint = { id: gid(doc, 'p'), label: label || nextFreeLabel(doc), def: { kind: 'free', pos: [...pos] as V3 } }
  doc.objects.push(pt)
  return pt
}

export function addPointOnEdge(doc: GeomDoc, a: GId, b: GId, t: number, label?: string): GPoint {
  const pt: GPoint = { id: gid(doc, 'p'), label: label || nextFreeLabel(doc), def: { kind: 'onEdge', a, b, t: Math.max(0, Math.min(1, t)) } }
  doc.objects.push(pt)
  return pt
}

export function addMidpoint(doc: GeomDoc, a: GId, b: GId, label?: string): GPoint {
  const pt: GPoint = { id: gid(doc, 'p'), label: label || nextFreeLabel(doc), def: { kind: 'midpoint', a, b } }
  doc.objects.push(pt)
  return pt
}

export function addRatioPoint(doc: GeomDoc, a: GId, b: GId, m: number, n: number, from: 'a' | 'b' = 'a', label?: string): GPoint {
  const pt: GPoint = { id: gid(doc, 'p'), label: label || nextFreeLabel(doc), def: { kind: 'ratio', a, b, m, n, from } }
  doc.objects.push(pt)
  return pt
}

/** 两线段交点：不共面/不相交时如实抛错（调用方转成"未识别"披露） */
export function addIntersect(doc: GeomDoc, p: [GId, GId], q: [GId, GId], label?: string): GPoint {
  const r = segIntersect(pointPos(doc, p[0]), pointPos(doc, p[1]), pointPos(doc, q[0]), pointPos(doc, q[1]))
  if (!r) throw new Error('两线段不共面不相交，无法构造交点')
  const pt: GPoint = { id: gid(doc, 'p'), label: label || nextFreeLabel(doc), def: { kind: 'intersect', p, q } }
  doc.objects.push(pt)
  return pt
}

const LETTERS = 'EFGHIJKLMN'
function nextFreeLabel(doc: GeomDoc): string {
  const used = new Set(doc.objects.filter((o) => (o as GPoint).label !== undefined).map((o) => (o as GPoint).label))
  for (const c of LETTERS) if (!used.has(c)) return c
  for (const c of LETTERS) for (const d of LETTERS) if (!used.has(c + d)) return c + d
  return `X${doc.seq}`
}

/* ---------- 线段 / 向量 / 截面 ---------- */

export function addSegment(doc: GeomDoc, a: GId, b: GId, style: GSegment['style'] = 'auto'): GSegment {
  const seg: GSegment = { id: gid(doc, 'l'), a, b, style }
  doc.objects.push(seg)
  return seg
}

export function addVector(doc: GeomDoc, a: GId, b: GId): GVector {
  const vec: GVector = { id: gid(doc, 'w'), a, b }
  doc.objects.push(vec)
  return vec
}

/** 过三点作凸体截面：返回按环绕顺序的截面多边形顶点 */
export function sectionOf(doc: GeomDoc, p1: V3, p2: V3, p3: V3): { pts: V3[]; solid: GSolid } | { error: string } {
  const s = doc.objects.find((o) => (o as GSolid).edges) as GSolid | undefined
  if (!s) return { error: '画布上没有立体' }
  const n = v3.cross(v3.sub(p2, p1), v3.sub(p3, p1))
  if (v3.len(n) < 1e-9) return { error: '三点共线，无法确定平面' }
  const norm = v3.scale(n, 1 / v3.len(n))
  const P = s.verts.map((id) => pointPos(doc, id))
  const side = P.map((p) => v3.dot(norm, v3.sub(p, p1)))
  const xs: V3[] = []
  for (const [a, b] of s.edges) {
    const sa = side[a], sb = side[b]
    if (Math.abs(sa) < 1e-9) xs.push(P[a])
    if (Math.abs(sb) < 1e-9 && Math.abs(sa) >= 1e-9) xs.push(P[b])
    if ((sa > 1e-9 && sb < -1e-9) || (sa < -1e-9 && sb > 1e-9)) {
      const t = sa / (sa - sb)
      xs.push(v3.lerp(P[a], P[b], t))
    }
  }
  // 去重 + 按绕平面法向的极角排序
  const uniq: V3[] = []
  for (const x of xs) if (!uniq.some((u) => v3.dist(u, x) < 1e-6)) uniq.push(x)
  if (uniq.length < 3) return { error: '平面与立体没有截得多边形（三点确定的平面未穿过立体）' }
  const c = uniq.reduce((acc, p) => v3.add(acc, p), [0, 0, 0] as V3)
  const center = v3.scale(c, 1 / uniq.length)
  const e1 = v3.scale(v3.sub(uniq[0], center), 1 / v3.len(v3.sub(uniq[0], center)))
  const e2 = v3.cross(norm, e1)
  uniq.sort((u, w) => Math.atan2(v3.dot(v3.sub(u, center), e2), v3.dot(v3.sub(u, center), e1)) - Math.atan2(v3.dot(v3.sub(w, center), e2), v3.dot(v3.sub(w, center), e1)))
  return { pts: uniq, solid: s }
}

export function addSection(doc: GeomDoc, ids: [GId, GId, GId]): GSection | { error: string } {
  const r = sectionOf(doc, pointPos(doc, ids[0]), pointPos(doc, ids[1]), pointPos(doc, ids[2]))
  if ('error' in r) return r
  const sec: GSection = { id: gid(doc, 'x'), p: ids }
  doc.objects.push(sec)
  return sec
}

/* ---------- 撤销 / 重做（快照栈） ---------- */

export interface GeomHistory { undo: string[]; redo: string[] }

export const newHistory = (): GeomHistory => ({ undo: [], redo: [] })

export function pushHistory(doc: GeomDoc, h: GeomHistory) {
  h.undo.push(JSON.stringify({ objects: doc.objects, camera: doc.camera, seq: doc.seq }))
  if (h.undo.length > 60) h.undo.shift()
  h.redo.length = 0
}

export function undo(doc: GeomDoc, h: GeomHistory): boolean {
  const snap = h.undo.pop()
  if (!snap) return false
  h.redo.push(JSON.stringify({ objects: doc.objects, camera: doc.camera, seq: doc.seq }))
  restore(doc, snap)
  return true
}

export function redo(doc: GeomDoc, h: GeomHistory): boolean {
  const snap = h.redo.pop()
  if (!snap) return false
  h.undo.push(JSON.stringify({ objects: doc.objects, camera: doc.camera, seq: doc.seq }))
  restore(doc, snap)
  return true
}

function restore(doc: GeomDoc, snap: string) {
  const d = JSON.parse(snap) as { objects: GObj[]; camera: GeomCamera; seq: number }
  doc.objects = d.objects
  doc.camera = d.camera
  doc.seq = d.seq
}

/* ---------- 删除（级联依赖） ---------- */

/** 删除对象及其一切依赖者；返回被删的 id 列表 */
export function removeCascade(doc: GeomDoc, id: GId): GId[] {
  const dead = new Set<GId>([id])
  let grew = true
  const depsOf = (o: GObj): GId[] => {
    if ((o as GPoint).def) {
      const d = (o as GPoint).def
      if (d.kind === 'sum') return [d.base]
      if (d.kind === 'onEdge') return [d.a, d.b]
      if (d.kind === 'midpoint') return [d.a, d.b]
      if (d.kind === 'ratio') return [d.a, d.b]
      if (d.kind === 'intersect') return [...d.p, ...d.q]
      return []
    }
    if ('r' in o && !('h' in o) && !('verts' in o) && 'c' in o) return [(o as { c: GId }).c]
    if ('h' in o && 'c' in o && 'kind' in o) return [(o as { c: GId }).c]
    if ((o as GPlanePlate).plate === true && (o as GPlanePlate).p !== undefined) return [...(o as GPlanePlate).p]
    if ((o as GSegment).a) return [(o as GSegment).a, (o as GSegment).b]
    if ((o as GVector).a !== undefined && (o as GVector).b !== undefined) return [(o as GVector).a, (o as GVector).b]
    if ((o as GSection).p) return [...(o as GSection).p]
    if ((o as GSolid).verts) return [...(o as GSolid).verts]
    return []
  }
  while (grew) {
    grew = false
    for (const o of doc.objects) {
      if (dead.has(o.id)) continue
      if (depsOf(o).some((d) => dead.has(d))) { dead.add(o.id); grew = true }
    }
  }
  doc.objects = doc.objects.filter((o) => !dead.has(o.id))
  return [...dead]
}

/* ---------- 标签查找 / 改名 ---------- */

export function findPointByLabel(doc: GeomDoc, label: string): GPoint | null {
  const norm = normLabel(label)
  const pts = doc.objects.filter((o) => (o as GPoint).def !== undefined) as GPoint[]
  return pts.find((p) => normLabel(p.label) === norm) || null
}

/** A1/A' → A₁ / A′ 统一比较 */
export function normLabel(l: string): string {
  return l.replace(/1/g, '₁').replace(/'/g, '′').trim()
}

export function renamePoint(doc: GeomDoc, id: GId, label: string) {
  const pt = doc.objects.find((o) => o.id === id) as GPoint | undefined
  if (pt && label.trim()) pt.label = normLabel(label.trim())
}
