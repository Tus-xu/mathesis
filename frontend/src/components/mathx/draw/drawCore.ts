/**
 * mathx/draw/drawCore —— 绘图工作台纯逻辑层（可单测，不依赖 DOM/JSXGraph）
 * - rdp：Douglas-Peucker 笔迹抽稀（"规整笔迹"：手绘抖动线 → 直线段）
 * - penToSegments / distance2：RDP 依赖
 * - V3DrawInsert：三模式产出的插入载荷（由 SlidesView 落成 V3Element）
 */
import type { V3DrawRecord } from '@/types/teacherV3'

export type Pt = [number, number]

export type V3DrawInsert =
  | { type: 'functionPlot'; expr: string; params: Record<string, { value: number; min: number; max: number; step: number }>; domain: [number, number] }
  | { type: 'formula'; latex: string }
  | { type: 'image'; src: string; records: V3DrawRecord[]; aspect: number }
  | { type: 'geometry'; preset_id: string; params: Record<string, number>; label: string }

function dist(a: Pt, b: Pt): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

/** 点到线段的垂直距离（RDP 的距离度量 + 画布命中检测共用） */
export function distToSeg(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return dist(p, a)
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2))
  return dist(p, [a[0] + t * dx, a[1] + t * dy])
}

/** Douglas-Peucker 抽稀：保留首尾，去掉抖动中间点 */
export function rdp(pts: Pt[], eps: number): Pt[] {
  if (pts.length <= 2) return pts.slice()
  let maxD = -1
  let idx = 0
  const a = pts[0]
  const b = pts[pts.length - 1]
  for (let i = 1; i < pts.length - 1; i++) {
    const d = distToSeg(pts[i], a, b)
    if (d > maxD) { maxD = d; idx = i }
  }
  if (maxD <= eps) return [a, b]
  const left = rdp(pts.slice(0, idx + 1), eps)
  const right = rdp(pts.slice(idx), eps)
  return [...left.slice(0, -1), ...right]
}

/** 手绘笔迹 → 规整折线（默认 eps 容差，数学坐标系单位） */
export function simplifyPen(pts: Pt[], eps = 0.18): Pt[] {
  return rdp(pts, eps)
}

/** 是否近似直线（用于判断手绘"想画一条直线"） */
export function isNearlyLine(pts: Pt[], eps = 0.18): boolean {
  if (pts.length < 3) return true
  return rdp(pts, eps).length === 2
}

/** 手绘是否近似圆（闭合 + 各向半径方差小）→ 规整为圆记录 */
export function isNearlyCircle(pts: Pt[]): { c: Pt; r: number } | null {
  if (pts.length < 12) return null
  const n = pts.length
  const cx = pts.reduce((s, p) => s + p[0], 0) / n
  const cy = pts.reduce((s, p) => s + p[1], 0) / n
  const ds = pts.map((p) => Math.hypot(p[0] - cx, p[1] - cy))
  const min = Math.min(...ds)
  const max = Math.max(...ds)
  if (min < 0.25 || max / min > 1.35) return null
  const [x0, y0] = pts[0]
  const [x1, y1] = pts[pts.length - 1]
  if (Math.hypot(x1 - x0, y1 - y0) > max * 0.5) return null
  return { c: [cx, cy], r: ds.reduce((s, d) => s + d, 0) / n }
}

let _rid = 0
export const drawRecId = (p = 'dr') => `${p}${Date.now().toString(36)}${++_rid}`

/* ---------- 自由画布 v2：整体平移 / 命中检测 / 包围盒 ---------- */

/**
 * 平移代理：拦截 board.create，把各类坐标参数整体平移 (dx, dy)。
 * preset build 内部以固定坐标绘制（点/线/曲线/函数…），
 * "放置后拖动" 通过代理在创建时平移实现，records 仍保持结构化。
 */
export function offsetBoard(board: any, dx: number, dy: number): any {
  if (!dx && !dy) return board
  const isPair = (v: unknown): v is Pt =>
    Array.isArray(v) && v.length === 2 && typeof v[0] === 'number' && typeof v[1] === 'number'
  const shift = (v: unknown) => (isPair(v) ? [v[0] + dx, v[1] + dy] as Pt : v)
  const shiftFlat = (a: unknown[]) =>
    typeof a[0] === 'number' && typeof a[1] === 'number'
      ? [a[0] + dx, a[1] + dy, ...a.slice(2)]
      : a.map(shift)
  return new Proxy(board, {
    get(target: any, prop, receiver) {
      if (prop !== 'create') return Reflect.get(target, prop, receiver)
      return (type: string, args: unknown, attrs?: unknown) => {
        const a = (Array.isArray(args) ? args : [args]) as unknown[]
        let out: unknown[] = a
        switch (type) {
          case 'point': case 'glider': case 'text':
            // [x, y, ...] 扁平数字坐标
            out = shiftFlat(a)
            break
          case 'curve':
            if (typeof a[0] === 'function' && typeof a[1] === 'function') {
              const [fx, fy, ...rest] = a as [(t: number) => number, (t: number) => number, ...unknown[]]
              out = [(t: number) => fx(t) + dx, (t: number) => fy(t) + dy, ...rest]
            } else if (Array.isArray(a[0]) && Array.isArray(a[1])) {
              out = [(a[0] as number[]).map((x) => x + dx), (a[1] as number[]).map((y) => y + dy), ...a.slice(2)]
            }
            break
          case 'functiongraph':
            if (typeof a[0] === 'function') {
              const f = a[0] as (x: number) => number
              const x0 = typeof a[1] === 'number' ? a[1] + dx : a[1]
              const x1 = typeof a[2] === 'number' ? a[2] + dx : a[2]
              out = [(x: number) => f(x - dx) + dy, x0, x1, ...a.slice(3)]
            }
            break
          case 'integral':
            // 积分区间 [a, b] 随函数横向平移
            if (Array.isArray(a[0])) out = [[(a[0] as number[])[0] + dx, (a[0] as number[])[1] + dx], ...a.slice(1)]
            break
          default:
            // segment/line/circle/polygon/ellipse…：坐标数组平移，元素对象引用不动
            out = a.map(shift)
        }
        return (target as any).create(type, out, attrs)
      }
    },
  })
}

const offsetXY = (r: V3DrawRecord): Pt => r.offset ?? [0, 0]

/** 命中检测：从后往前找第一条命中记录的 id（含 offset；preset 用 extent 判定） */
export function hitDrawRecord(
  records: V3DrawRecord[],
  p: Pt,
  extentOf?: (presetId: string) => [number, number, number, number] | undefined,
): string | null {
  const TOL = 0.38
  for (let i = records.length - 1; i >= 0; i--) {
    const r = records[i]
    const [dx, dy] = offsetXY(r)
    let hit = false
    if (r.kind === 'preset') {
      const ex = extentOf?.(r.preset_id)
      if (ex) hit = p[0] >= ex[0] + dx - TOL && p[0] <= ex[2] + dx + TOL && p[1] >= ex[1] + dy - TOL && p[1] <= ex[3] + dy + TOL
    } else if (r.kind === 'line') {
      hit = distToSeg(p, [r.a[0] + dx, r.a[1] + dy], [r.b[0] + dx, r.b[1] + dy]) < TOL
    } else if (r.kind === 'circle') {
      hit = Math.abs(Math.hypot(p[0] - r.c[0] - dx, p[1] - r.c[1] - dy) - r.r) < TOL
    } else if (r.kind === 'polygon') {
      hit = r.verts.some((v, j) => {
        const w = r.verts[(j + 1) % r.verts.length]
        return distToSeg(p, [v[0] + dx, v[1] + dy], [w[0] + dx, w[1] + dy]) < TOL
      })
    } else if (r.kind === 'pen') {
      hit = r.pts.some((v, j) => {
        const u = j === 0 ? [v[0] + dx, v[1] + dy] : [r.pts[j - 1][0] + dx, r.pts[j - 1][1] + dy]
        return distToSeg(p, u as Pt, [v[0] + dx, v[1] + dy]) < TOL
      })
    } else if (r.kind === 'point') {
      hit = Math.hypot(p[0] - r.pos[0] - dx, p[1] - r.pos[1] - dy) < 0.55
    } else if (r.kind === 'text') {
      hit = Math.abs(p[0] - r.pos[0] - dx) < 1.6 && Math.abs(p[1] - r.pos[1] - dy) < 0.7
    }
    if (hit) return r.id
  }
  return null
}

/** 记录包围盒 [xmin, ymin, xmax, ymax]（含 offset；选中框/命中共用；preset 需外部提供 extent） */
export function drawRecordBbox(
  r: V3DrawRecord,
  presetExtent?: [number, number, number, number],
): [number, number, number, number] | null {
  const [dx, dy] = offsetXY(r)
  if (r.kind === 'preset') {
    return presetExtent ? [presetExtent[0] + dx, presetExtent[1] + dy, presetExtent[2] + dx, presetExtent[3] + dy] : null
  }
  const xs: number[] = []
  const ys: number[] = []
  if (r.kind === 'line') { xs.push(r.a[0], r.b[0]); ys.push(r.a[1], r.b[1]) }
  else if (r.kind === 'circle') { xs.push(r.c[0] - r.r, r.c[0] + r.r); ys.push(r.c[1] - r.r, r.c[1] + r.r) }
  else if (r.kind === 'polygon') { r.verts.forEach((v) => { xs.push(v[0]); ys.push(v[1]) }) }
  else if (r.kind === 'pen') { r.pts.forEach((v) => { xs.push(v[0]); ys.push(v[1]) }) }
  else if (r.kind === 'point') { xs.push(r.pos[0] - 0.4, r.pos[0] + 0.4); ys.push(r.pos[1] - 0.4, r.pos[1] + 0.4) }
  else if (r.kind === 'text') { xs.push(r.pos[0] - 1.5, r.pos[0] + 1.5); ys.push(r.pos[1] - 0.7, r.pos[1] + 0.5) }
  else return null
  return [Math.min(...xs) + dx, Math.min(...ys) + dy, Math.max(...xs) + dx, Math.max(...ys) + dy]
}

/** SVG 根节点 → 白底快照 dataURL（JSXGraph 画布导出，插入课件显示用） */
export function boardSvgToDataUrl(svg: SVGSVGElement): { src: string; aspect: number } {
  const clone = svg.cloneNode(true) as SVGSVGElement
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const w = Number(svg.getAttribute('width')) || svg.clientWidth || 800
  const h = Number(svg.getAttribute('height')) || svg.clientHeight || 500
  clone.setAttribute('viewBox', `0 0 ${w} ${h}`)
  clone.setAttribute('width', String(w))
  clone.setAttribute('height', String(h))
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
  bg.setAttribute('width', String(w))
  bg.setAttribute('height', String(h))
  bg.setAttribute('fill', '#ffffff')
  clone.insertBefore(bg, clone.firstChild)
  return { src: 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(clone.outerHTML), aspect: h / w }
}
