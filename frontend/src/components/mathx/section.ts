/**
 * mathx/section —— 多面体截面算法（SPEC §5.7 L2 的原型实现，纯函数可单测）
 * 路线：取棱上三点确定平面 → 平面与全部棱求交 → 交点凸包排序成截面多边形。
 * 教学语义 = GeoGebra 官方验证过的"取点 → 建面 → 交线"构造路线。
 */

export type V3 = [number, number, number]
export interface Plane { n: V3; d: number }

/** 单位正方体顶点（边长 s 参数化时按比例缩放），底面 z=0 顶面 z=1 */
export const CUBE_VERTS: V3[] = [
  [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
  [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
]

/** 正方体 12 条棱（顶点索引对） */
export const CUBE_EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 0],
  [4, 5], [5, 6], [6, 7], [7, 4],
  [0, 4], [1, 5], [2, 6], [3, 7],
]

export function planeFrom3Points(p1: V3, p2: V3, p3: V3): Plane {
  const u: V3 = [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]]
  const v: V3 = [p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]]
  const n: V3 = [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ]
  const d = -(n[0] * p1[0] + n[1] * p1[1] + n[2] * p1[2])
  return { n, d }
}

/** 线段与平面交点（严格内部，t∈(eps,1-eps)；共面/平行返回 null） */
export function intersectSegmentPlane(a: V3, b: V3, plane: Plane): V3 | null {
  const dir: V3 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
  const denom = plane.n[0] * dir[0] + plane.n[1] * dir[1] + plane.n[2] * dir[2]
  if (Math.abs(denom) < 1e-9) return null
  const t = -(plane.n[0] * a[0] + plane.n[1] * a[1] + plane.n[2] * a[2] + plane.d) / denom
  if (t <= 1e-6 || t >= 1 - 1e-6) return null
  return [a[0] + dir[0] * t, a[1] + dir[1] * t, a[2] + dir[2] * t]
}

/** 交点集合按质心 + 平面内正交基做极角排序（凸包顺序） */
function sortByAngleOnPlane(pts: V3[], n: V3): V3[] {
  const c: V3 = [0, 0, 0]
  for (const p of pts) { c[0] += p[0] / pts.length; c[1] += p[1] / pts.length; c[2] += p[2] / pts.length }
  const len = Math.hypot(n[0], n[1], n[2]) || 1
  const nn: V3 = [n[0] / len, n[1] / len, n[2] / len]
  // 平面内正交基 u,v：取与 n 最不平行的坐标轴叉乘
  const helper: V3 = Math.abs(nn[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]
  const u: V3 = [
    nn[1] * helper[2] - nn[2] * helper[1],
    nn[2] * helper[0] - nn[0] * helper[2],
    nn[0] * helper[1] - nn[1] * helper[0],
  ]
  const ul = Math.hypot(u[0], u[1], u[2]) || 1
  u[0] /= ul; u[1] /= ul; u[2] /= ul
  const v: V3 = [
    nn[1] * u[2] - nn[2] * u[1],
    nn[2] * u[0] - nn[0] * u[2],
    nn[0] * u[1] - nn[1] * u[0],
  ]
  return [...pts].sort((p, q) => {
    const pa = Math.atan2(
      (p[0] - c[0]) * v[0] + (p[1] - c[1]) * v[1] + (p[2] - c[2]) * v[2],
      (p[0] - c[0]) * u[0] + (p[1] - c[1]) * u[1] + (p[2] - c[2]) * u[2],
    )
    const qa = Math.atan2(
      (q[0] - c[0]) * v[0] + (q[1] - c[1]) * v[1] + (q[2] - c[2]) * v[2],
      (q[0] - c[0]) * u[0] + (q[1] - c[1]) * u[1] + (q[2] - c[2]) * u[2],
    )
    return pa - qa
  })
}

/**
 * 正方体被过三点的平面所截的截面多边形（SPEC §5.7 L2）。
 * verts/edges 可换成长方体/棱锥等（顶点表驱动）。
 * 返回凸包顺序的交点数组；三点共线或无交点返回 []。
 */
export function sectionPolygon(verts: V3[], edges: [number, number][], p1: V3, p2: V3, p3: V3): V3[] {
  const plane = planeFrom3Points(p1, p2, p3)
  if (Math.hypot(plane.n[0], plane.n[1], plane.n[2]) < 1e-9) return []
  const raw: V3[] = []
  for (const [ai, bi] of edges) {
    const hit = intersectSegmentPlane(verts[ai], verts[bi], plane)
    if (hit) raw.push(hit)
  }
  // 去重（顶点恰好落在棱交点时两条棱给同一点）
  const dedup: V3[] = []
  for (const p of raw) {
    if (!dedup.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) < 1e-6)) dedup.push(p)
  }
  if (dedup.length < 3) return []
  return sortByAngleOnPlane(dedup, plane.n)
}

/** 三点是否共线（构造校验门的一部分：三点共线无法定面） */
export function collinear(p1: V3, p2: V3, p3: V3): boolean {
  const u: V3 = [p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]]
  const v: V3 = [p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]]
  const n: V3 = [
    u[1] * v[2] - u[2] * v[1],
    u[2] * v[0] - u[0] * v[2],
    u[0] * v[1] - u[1] * v[0],
  ]
  return Math.hypot(n[0], n[1], n[2]) < 1e-9
}

/** 棱上取点：edge 端点 A→B 的 t 比例处 */
export function pointOnEdge(a: V3, b: V3, t: number): V3 {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}
