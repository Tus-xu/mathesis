/**
 * mathx/presets —— 图形库 preset 注册表（SPEC §5.6 首发清单 + L2 截面示范）
 * 每个 preset 两套渲染：
 *   build(ctx)  → JSXGraph 真实构造（主画布 / 编辑态 / 构造态）
 *   miniSvg(p)  → 轻量 SVG 快照（缩略图 / 大纲栏 / 坞内预览，零引擎开销）
 * 参数与 V3FigurePreset 契约一致，供属性面板自动生成滑杆。
 */
// 注意：本文件会被 vite.config 加载链（mock 服务）以相对路径引入，禁止顶层 import 有副作用的运行时（如 jsxgraph）。
// 需 JXG 的地方一律从 'jsxgraph' 直接导入。
import type { V3FigureCategory, V3FigurePreset } from '@/types/teacherV3'
import { drawSolid2d } from './draw/solids'
import { CUBE_EDGES, CUBE_VERTS, pointOnEdge, sectionPolygon, type V3 } from './section'
import { isoProject } from './draw/solids'

export interface BuildCtx {
  board: any
  p: Record<string, number>
  t: Record<string, boolean>
}
export interface FigurePresetDef extends V3FigurePreset {
  boundingbox: [number, number, number, number]
  axis: boolean
  /** 图形实际绘制范围 [xmin, ymin, xmax, ymax]（比 boundingbox 紧凑）：自由画布命中检测/选中框用 */
  extent?: [number, number, number, number]
  build: (ctx: BuildCtx) => void
  miniSvg: (p: Record<string, number>, t?: Record<string, boolean>) => string
}

const NAVY = '#0f4787'
const GOLD = '#c99735'
const INK2 = '#4a5568'
const LINEC = '#8b95a7'

/* ---------- SVG 小工具 ---------- */
const svgWrap = (inner: string) =>
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80" style="width:100%;height:100%">${inner}</svg>`
const f1 = (n: number) => Number(n.toFixed(1))

/* ---------- 立体几何（斜二测 2D 直观图，教材标准画法；被遮挡棱虚线） ---------- */
/** 斜二测：x 右，y 上，z 右上 45° 半缩放 */
function iso(x: number, y: number, z: number, s = 34, ox = 38, oy = 60): [number, number] {
  return [ox + x * s + z * s * 0.5, oy - y * s - z * s * 0.5]
}
const polyPts = (pts: [number, number][]) => pts.map((p) => `${f1(p[0])},${f1(p[1])}`).join(' ')
function cubeEdgesSvg(size = 1, extra = ''): string {
  const v = (x: number, y: number, z: number) => iso(x, y, z)
  const bottom = [v(0, 0, 0), v(size, 0, 0), v(size, size, 0), v(0, size, 0)]
  const top = [v(0, 0, size), v(size, 0, size), v(size, size, size), v(0, size, size)]
  const edge = (a: [number, number], b: [number, number], hidden = false) =>
    `<line x1="${f1(a[0])}" y1="${f1(a[1])}" x2="${f1(b[0])}" y2="${f1(b[1])}" stroke="${hidden ? '#c3cad6' : NAVY}" stroke-width="${hidden ? 1 : 1.6}"/>`
  let out = ''
  for (let i = 0; i < 4; i++) {
    out += edge(bottom[i], bottom[(i + 1) % 4], i === 2 || i === 3)
    out += edge(top[i], top[(i + 1) % 4])
    out += edge(bottom[i], top[i], i === 3)
  }
  return out + extra
}
function sectionOfCube(t1: number, t2: number, t3: number, size = 1): [number, number][] {
  // 三点分别在 AB、CC1、DD1 棱上（演示截面题的常见取点）
  const s = size
  const p1 = pointOnEdge([0, 0, 0], [s, 0, 0], t1)
  const p2 = pointOnEdge([s, s, 0], [s, s, s], t2)
  const p3 = pointOnEdge([0, s, 0], [0, s, s], t3)
  return sectionPolygon(
    CUBE_VERTS.map((v) => [v[0] * s, v[1] * s, v[2] * s] as V3),
    CUBE_EDGES,
    p1, p2, p3,
  ).map((v) => iso(v[0], v[1], v[2]))
}

const defs: FigurePresetDef[] = [
  /* ============ solid 立体几何（斜二测 2D） ============ */
  {
    id: 'solid/cube', category: 'solid', name: '正方体', desc: '可调边长，虚线标注被遮挡棱，顶点标注 ABCD-A₁B₁C₁D₁', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-3.3, -3.3, 3.3, 3.3],
    params: [{ key: 's', label: '边长', min: 1, max: 3, step: 0.1, def: 2 }],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: true }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/cube', p, { color: NAVY, width: 2.4, showLabels: t.showLabels !== false })
    },
    miniSvg: (p) => cubeEdgesSvg(p.s ? Math.min(1.6, p.s / 1.4) : 1.1),
  },
  {
    id: 'solid/cuboid', category: 'solid', name: '长方体', desc: '可调长宽高，虚线标注被遮挡棱', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-4.2, -3.1, 4.2, 3.1],
    params: [
      { key: 'a', label: '长', min: 1.5, max: 4, step: 0.1, def: 3 },
      { key: 'b', label: '宽', min: 1, max: 3, step: 0.1, def: 2 },
      { key: 'c', label: '高', min: 1, max: 3, step: 0.1, def: 2 },
    ],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: false }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/cuboid', p, { color: NAVY, width: 2.4, showLabels: t.showLabels !== false })
    },
    miniSvg: () => svgWrap(
      `<line x1="18" y1="62" x2="82" y2="62" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="18" y1="62" x2="36" y2="70" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="82" y1="62" x2="100" y2="70" stroke="#c3cad6" stroke-width="1"/>` +
      `<line x1="36" y1="70" x2="100" y2="70" stroke="#c3cad6" stroke-width="1"/>` +
      `<line x1="18" y1="62" x2="18" y2="18" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="82" y1="62" x2="82" y2="18" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="36" y1="70" x2="36" y2="26" stroke="#c3cad6" stroke-width="1"/>` +
      `<line x1="100" y1="70" x2="100" y2="26" stroke="#c3cad6" stroke-width="1"/>` +
      `<line x1="18" y1="18" x2="82" y2="18" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="18" y1="18" x2="36" y2="26" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="82" y1="18" x2="100" y2="26" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="36" y1="26" x2="100" y2="26" stroke="${NAVY}" stroke-width="1.6"/>`,
    ),
  },
  {
    id: 'solid/cube-section', category: 'solid', name: '正方体截面（L2）', desc: '拖动三个取点位置，截面实时重算（多面体截面算法），金色高亮', level: 2,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-3.3, -3.3, 3.3, 3.3],
    params: [
      { key: 't1', label: 'M 在 AB 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.5 },
      { key: 't2', label: 'N 在 CC₁ 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.55 },
      { key: 't3', label: 'P 在 DD₁ 点位', min: 0.15, max: 0.85, step: 0.01, def: 0.45 },
    ],
    toggles: [{ key: 'showVerts', label: '显示取点与顶点', def: true }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/cube-section', p, { color: NAVY, width: 2.2, showLabels: t.showVerts !== false })
    },
    miniSvg: (p) => {
      const sec = sectionOfCube(p.t1 ?? 0.5, p.t2 ?? 0.55, p.t3 ?? 0.45, 1.1)
      const extra = sec.length >= 3
        ? `<polygon points="${polyPts(sec)}" fill="rgba(201,151,53,0.3)" stroke="${GOLD}" stroke-width="1.6"/>`
        : ''
      return cubeEdgesSvg(1.1, extra)
    },
  },
  {
    id: 'solid/cube-net', category: 'solid', name: '正方体展开图', desc: '拖动展开度滑杆：0=收起为直观图，1=完全展开（十字 1-4-1 型），六面同色便于折叠前后对照', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-6.4, -2.8, 6.4, 2.8],
    params: [
      { key: 's', label: '边长', min: 1, max: 2.4, step: 0.1, def: 1.6 },
      { key: 't', label: '展开度', min: 0, max: 1, step: 0.02, def: 1 },
    ],
    toggles: [{ key: 'showLabels', label: '标注上下底面', def: false }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/cube-net', p, { color: NAVY, width: 2.2, showLabels: t.showLabels === true })
    },
    miniSvg: (p) => {
      const t = p.t ?? 1
      const sq = (x: number, y: number, s2: number, extra = '') =>
        `<rect x="${f1(x)}" y="${f1(y)}" width="${s2}" height="${s2}" fill="rgba(15,71,135,0.10)" stroke="${NAVY}" stroke-width="1.4"/>${extra}`
      if (t < 0.35) {
        // 收起态：斜二测正方体简图
        return svgWrap(cubeEdgesSvg(1.1))
      }
      // 展开态（十字 1-4-1）：中间底面 + 四向侧面 + 左侧顶面
      const s2 = 26, cx = 47, cy = 27
      let out = sq(cx, cy, s2)
      out += sq(cx, cy - s2, s2) + sq(cx + s2, cy, s2) + sq(cx, cy + s2, s2) + sq(cx - s2, cy, s2)
      out += sq(cx - 2 * s2, cy - s2, s2)
      return svgWrap(out)
    },
  },
  {
    id: 'solid/pyramid', category: 'solid', name: '正 n 棱锥', desc: '底面边数 n 可调（3-8），可调底面半径与高，远侧棱虚线', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-4.9, -3.6, 4.9, 3.6],
    params: [
      { key: 'n', label: '底面边数 n', min: 3, max: 8, step: 1, def: 3 },
      { key: 'r', label: '底面半径', min: 0.8, max: 2, step: 0.1, def: 1.4 },
      { key: 'h', label: '高', min: 1, max: 3, step: 0.1, def: 2 },
    ],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: true }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/pyramid', p, { color: NAVY, width: 2.4, showLabels: t.showLabels !== false })
    },
    miniSvg: (p) => {
      const h = 1 + (p.h ?? 2) * 0.32
      const b = [[30, 62], [92, 62], [66, 46]] as [number, number][]
      const apex: [number, number] = [58, 62 - 42 * (h / 2)]
      let out = `<polygon points="${polyPts(b)}" fill="rgba(15,71,135,0.05)" stroke="${NAVY}" stroke-width="1.6"/>`
      b.forEach((q) => { out += `<line x1="${q[0]}" y1="${q[1]}" x2="${apex[0]}" y2="${apex[1]}" stroke="${NAVY}" stroke-width="1.6"/>` })
      out += `<circle cx="${apex[0]}" cy="${apex[1]}" r="3" fill="${GOLD}"/><text x="${apex[0] + 6}" y="${apex[1] - 4}" font-size="10" fill="${INK2}" font-style="italic">P</text>`
      return svgWrap(out)
    },
  },
  {
    id: 'solid/prism', category: 'solid', name: '正 n 棱柱', desc: '底面边数 n 可调（3-8），可调底面半径与高，远侧棱虚线', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-4.3, -3.4, 4.3, 3.4],
    params: [
      { key: 'n', label: '底面边数 n', min: 3, max: 8, step: 1, def: 3 },
      { key: 'r', label: '底面半径', min: 0.8, max: 2, step: 0.1, def: 1.4 },
      { key: 'h', label: '高', min: 1, max: 3, step: 0.1, def: 2 },
    ],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: true }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/prism', p, { color: NAVY, width: 2.4, showLabels: t.showLabels !== false })
    },
    miniSvg: () => {
      const b = [[28, 64], [92, 64], [62, 47]] as [number, number][]
      const t2 = b.map(([x, y]) => [x, y - 36] as [number, number])
      const ln = (a: [number, number], c: [number, number], hidden = false) =>
        `<line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" stroke="${hidden ? '#c3cad6' : NAVY}" stroke-width="${hidden ? 1 : 1.6}"/>`
      let out = ln(b[0], b[1]) + ln(b[1], b[2], true) + ln(b[2], b[0], true)
      out += ln(t2[0], t2[1]) + ln(t2[1], t2[2]) + ln(t2[2], t2[0])
      out += ln(b[0], t2[0]) + ln(b[1], t2[1]) + ln(b[2], t2[2], true)
      return svgWrap(out)
    },
  },
  {
    id: 'solid/tetra', category: 'solid', name: '正四面体', desc: '六条棱全等的正四面体，可调棱长', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-4.6, -3.6, 4.6, 3.6],
    params: [{ key: 'a', label: '棱长', min: 1.5, max: 3.5, step: 0.1, def: 2.6 }],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: true }],
    build({ board, p, t }) {
      drawSolid2d(board, 'solid/tetra', p, { color: NAVY, width: 2.4, showLabels: t.showLabels !== false })
    },
    miniSvg: () => {
      const b = [[30, 63], [92, 63], [63, 45]] as [number, number][]
      const apex: [number, number] = [58, 13]
      const ln = (a: [number, number], c: [number, number], hidden = false) =>
        `<line x1="${a[0]}" y1="${a[1]}" x2="${c[0]}" y2="${c[1]}" stroke="${hidden ? '#c3cad6' : NAVY}" stroke-width="${hidden ? 1 : 1.6}"/>`
      let out = ln(b[0], b[1]) + ln(b[1], b[2]) + ln(b[2], b[0], true)
      out += ln(b[0], apex) + ln(b[1], apex) + ln(b[2], apex, true)
      out += `<circle cx="${apex[0]}" cy="${apex[1]}" r="3" fill="${GOLD}"/>`
      return svgWrap(out)
    },
  },
  {
    id: 'solid/cylinder', category: 'solid', name: '圆柱', desc: '可调底面半径与高，下底后半椭圆虚线', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-3.1, -3.1, 3.1, 3.1],
    params: [{ key: 'r', label: '半径', min: 0.5, max: 2, step: 0.1, def: 1 }, { key: 'h', label: '高', min: 1, max: 3, step: 0.1, def: 2 }],
    build({ board, p }) {
      drawSolid2d(board, 'solid/cylinder', p, { color: NAVY, width: 2.2 })
    },
    miniSvg: () => svgWrap(
      `<ellipse cx="60" cy="24" rx="30" ry="9" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="30" y1="24" x2="30" y2="60" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="90" y1="24" x2="90" y2="60" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<path d="M30 60 A30 9 0 0 0 90 60" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<path d="M30 60 A30 9 0 0 1 90 60" fill="none" stroke="#c3cad6" stroke-width="1"/>`,
    ),
  },
  {
    id: 'solid/cone', category: 'solid', name: '圆锥', desc: '可调底面半径与高，下底后半椭圆虚线', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-3.7, -3.5, 3.7, 3.5],
    params: [{ key: 'r', label: '半径', min: 0.5, max: 2, step: 0.1, def: 1.2 }, { key: 'h', label: '高', min: 1, max: 3, step: 0.1, def: 2.2 }],
    build({ board, p }) {
      drawSolid2d(board, 'solid/cone', p, { color: NAVY, width: 2.2 })
    },
    miniSvg: () => svgWrap(
      `<ellipse cx="60" cy="62" rx="30" ry="9" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="60" y1="14" x2="30" y2="62" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="60" y1="14" x2="90" y2="62" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<circle cx="60" cy="14" r="2.5" fill="${GOLD}"/>`,
    ),
  },
  {
    id: 'solid/frustum', category: 'solid', name: '圆台', desc: '可调上下底半径与高', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-4.9, -3.6, 4.9, 3.6],
    params: [
      { key: 'r1', label: '下底半径', min: 0.8, max: 2, step: 0.1, def: 1.6 },
      { key: 'r2', label: '上底半径', min: 0.4, max: 1.5, step: 0.1, def: 0.9 },
      { key: 'h', label: '高', min: 1, max: 3, step: 0.1, def: 2 },
    ],
    build({ board, p }) {
      drawSolid2d(board, 'solid/frustum', p, { color: NAVY, width: 2.2 })
    },
    miniSvg: () => svgWrap(
      `<ellipse cx="60" cy="58" rx="34" ry="10" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<path d="M26 58 A34 10 0 0 1 94 58" fill="none" stroke="#c3cad6" stroke-width="1"/>` +
      `<line x1="33" y1="58" x2="41" y2="20" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="87" y1="58" x2="79" y2="20" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<ellipse cx="60" cy="20" rx="19" ry="6" fill="none" stroke="${NAVY}" stroke-width="1.6"/>`,
    ),
  },
  {
    id: 'solid/sphere', category: 'solid', name: '球', desc: '可调半径，赤道与经线示意', level: 1,
    boundingbox: [-7, 5.6, 7, -5.2], axis: false, extent: [-3.7, -3.7, 3.7, 3.7],
    params: [{ key: 'r', label: '半径', min: 0.8, max: 2.4, step: 0.1, def: 1.6 }],
    build({ board, p }) {
      drawSolid2d(board, 'solid/sphere', p, { color: NAVY, width: 2.2 })
    },
    miniSvg: () => svgWrap(
      `<circle cx="60" cy="40" r="26" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<ellipse cx="60" cy="40" rx="26" ry="8" fill="none" stroke="${GOLD}" stroke-width="1" opacity="0.7"/>` +
      `<ellipse cx="60" cy="40" rx="8" ry="26" fill="none" stroke="${GOLD}" stroke-width="1" opacity="0.7"/>`,
    ),
  },

  /* ============ conic 圆锥曲线 ============ */
  {
    id: 'conic/ellipse', category: 'conic', name: '椭圆（焦点+定义）', desc: '半长轴 a 与半短轴 b 可调，焦点与 |MF₁|+|MF₂| 联动', level: 1,
    boundingbox: [-6, 4.4, 6, -4], axis: true, extent: [-4.5, -3.2, 4.5, 3.2],
    params: [{ key: 'a', label: '半长轴 a', min: 1.5, max: 4, step: 0.1, def: 3 }, { key: 'b', label: '半短轴 b', min: 1, max: 3, step: 0.1, def: 2 }],
    toggles: [{ key: 'showFoci', label: '显示焦点', def: true }, { key: 'showSum', label: '动点与线和', def: false }],
    build({ board, p, t }) {
      const c = Math.sqrt(Math.max(0.01, p.a * p.a - p.b * p.b))
      const F1 = board.create('point', [-c, 0], { size: 2.5, name: 'F₁', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      const F2 = board.create('point', [c, 0], { size: 2.5, name: 'F₂', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      board.create('ellipse', [F1, F2, p.a], { strokeColor: NAVY, strokeWidth: 2 })
      if (t.showFoci !== false) {
        board.create('segment', [F1, F2], { strokeColor: LINEC, strokeWidth: 1, dash: 2 })
      }
      if (t.showSum) {
        const M = board.create('glider', [p.a * Math.cos(0.9), p.b * Math.sin(0.9), board.create('ellipse', [F1, F2, p.a], { visible: false })], { name: 'M', size: 3, strokeColor: NAVY })
        board.create('segment', [F1, M], { strokeColor: GOLD, dash: 3, strokeWidth: 1.4 })
        board.create('segment', [F2, M], { strokeColor: GOLD, dash: 3, strokeWidth: 1.4 })
      }
    },
    miniSvg: (p, t) => {
      const a = p.a ?? 3, b = p.b ?? 2, c = Math.sqrt(Math.max(0.01, a * a - b * b))
      const cx = 60, cy = 40, k = 10
      const d = (t?.showFoci !== false) ? `<circle cx="${f1(cx - c * k)}" cy="${cy}" r="2.5" fill="${GOLD}"/><circle cx="${f1(cx + c * k)}" cy="${cy}" r="2.5" fill="${GOLD}"/><line x1="${f1(cx - c * k)}" y1="${cy}" x2="${f1(cx + c * k)}" y2="${cy}" stroke="${LINEC}" stroke-width="0.8" stroke-dasharray="3 2"/>` : ''
      return svgWrap(`<ellipse cx="${cx}" cy="${cy}" rx="${f1(a * k)}" ry="${f1(b * k)}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>${d}`)
    },
  },
  {
    id: 'conic/ellipse-coordinate', category: 'conic', name: '椭圆（坐标系标准图）', desc: '教学标准配图：坐标轴 + 焦点 + 顶点标注', level: 1,
    boundingbox: [-6, 4.4, 6, -4], axis: true, extent: [-4.5, -3, 4.5, 3],
    params: [{ key: 'a', label: '半长轴 a', min: 2, max: 4, step: 0.1, def: 3 }, { key: 'b', label: '半短轴 b', min: 1, max: 2.6, step: 0.1, def: 1.8 }],
    toggles: [{ key: 'showLabels', label: '顶点标注', def: true }],
    build({ board, p }) {
      const c = Math.sqrt(Math.max(0.01, p.a * p.a - p.b * p.b))
      const F1 = board.create('point', [-c, 0], { size: 2, name: 'F₁(-c,0)', strokeColor: GOLD, fillColor: GOLD, fixed: true, label: { fontSize: 12 } })
      const F2 = board.create('point', [c, 0], { size: 2, name: 'F₂(c,0)', strokeColor: GOLD, fillColor: GOLD, fixed: true, label: { fontSize: 12 } })
      board.create('ellipse', [F1, F2, p.a], { strokeColor: NAVY, strokeWidth: 2.2 })
      board.create('point', [-p.a, 0], { size: 1.5, name: 'A₁', fixed: true })
      board.create('point', [p.a, 0], { size: 1.5, name: 'A₂', fixed: true })
      board.create('point', [0, p.b], { size: 1.5, name: 'B₁', fixed: true })
      board.create('point', [0, -p.b], { size: 1.5, name: 'B₂', fixed: true })
    },
    miniSvg: (p) => {
      const a = p.a ?? 3, b = p.b ?? 1.8
      return svgWrap(
        `<line x1="10" y1="40" x2="110" y2="40" stroke="${LINEC}" stroke-width="0.9"/>` +
        `<line x1="60" y1="6" x2="60" y2="74" stroke="${LINEC}" stroke-width="0.9"/>` +
        `<ellipse cx="60" cy="40" rx="${f1(a * 10)}" ry="${f1(b * 10)}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<circle cx="${f1(60 - Math.sqrt(a * a - b * b) * 10)}" cy="40" r="2.2" fill="${GOLD}"/>` +
        `<circle cx="${f1(60 + Math.sqrt(a * a - b * b) * 10)}" cy="40" r="2.2" fill="${GOLD}"/>`,
      )
    },
  },
  {
    id: 'conic/hyperbola', category: 'conic', name: '双曲线', desc: '半实轴 a 可调，含渐近线', level: 1,
    boundingbox: [-6, 4.4, 6, -4], axis: true, extent: [-5, -4, 5, 4],
    params: [{ key: 'a', label: '半实轴 a', min: 0.8, max: 2.4, step: 0.1, def: 1.4 }, { key: 'b', label: '半虚轴 b', min: 0.8, max: 2.4, step: 0.1, def: 1.4 }],
    toggles: [{ key: 'asymptote', label: '渐近线', def: true }],
    build({ board, p, t }) {
      const c = Math.sqrt(p.a * p.a + p.b * p.b)
      const F1 = board.create('point', [-c, 0], { size: 2, name: 'F₁', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      const F2 = board.create('point', [c, 0], { size: 2, name: 'F₂', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      board.create('hyperbola', [F1, F2, p.a], { strokeColor: NAVY, strokeWidth: 2 })
      if (t.asymptote !== false) {
        board.create('functiongraph', [(x: number) => (p.b / p.a) * x, -6, 6], { strokeColor: LINEC, dash: 2, strokeWidth: 1 })
        board.create('functiongraph', [(x: number) => -(p.b / p.a) * x, -6, 6], { strokeColor: LINEC, dash: 2, strokeWidth: 1 })
      }
    },
    miniSvg: (p, t) => {
      const a = p.a ?? 1.4, b = p.b ?? 1.4, k = 14
      const path = (sgn: number) => {
        const pts: string[] = []
        for (let x = a; x <= 4.2; x += 0.1) {
          const y = sgn * b * Math.sqrt((x * x) / (a * a) - 1)
          pts.push(`${f1(60 + x * k)},${f1(40 - y * k)}`)
        }
        return `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.7"/>`
      }
      const asym = t?.asymptote !== false ? `<line x1="14" y1="68" x2="106" y2="12" stroke="${LINEC}" stroke-dasharray="3 2" stroke-width="0.8"/><line x1="14" y1="12" x2="106" y2="68" stroke="${LINEC}" stroke-dasharray="3 2" stroke-width="0.8"/>` : ''
      return svgWrap(asym + path(1) + path(-1))
    },
  },
  {
    id: 'conic/parabola', category: 'conic', name: '抛物线', desc: '焦点 + 准线 + 标准方程', level: 1,
    boundingbox: [-6, 4.4, 6, -4], axis: true, extent: [-1.5, -4, 4, 4],
    params: [{ key: 'p', label: '焦参数 p', min: 0.4, max: 2, step: 0.1, def: 1 }],
    build({ board, p }) {
      const F = board.create('point', [p.p / 2, 0], { size: 2.5, name: 'F', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      const dir = board.create('line', [[-p.p / 2, 0], [-p.p / 2, 1]], { strokeColor: LINEC, dash: 2, strokeWidth: 1 })
      board.create('parabola', [F, dir], { strokeColor: NAVY, strokeWidth: 2 })
    },
    miniSvg: (p) => {
      const pp = p.p ?? 1
      const pts: string[] = []
      for (let y = -3; y <= 3; y += 0.15) pts.push(`${f1(56 + y * y / (2 * pp) * 9)},${f1(40 - y * 9)}`)
      return svgWrap(
        `<line x1="12" y1="40" x2="110" y2="40" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<line x1="${f1(56 - pp / 2 * 9)}" y1="8" x2="${f1(56 - pp / 2 * 9)}" y2="72" stroke="${LINEC}" stroke-dasharray="3 2" stroke-width="0.9"/>` +
        `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<circle cx="${f1(56 + pp / 2 * 9)}" cy="40" r="2.4" fill="${GOLD}"/>`,
      )
    },
  },

  /* ============ function 函数 ============ */
  {
    id: 'function/quadratic', category: 'function', name: '二次函数（顶点式）', desc: 'y = a(x-h)² + k，参数实时联动', level: 1,
    boundingbox: [-7, 5.5, 7, -4.5], axis: true, extent: [-6, -4.5, 6, 5],
    params: [
      { key: 'a', label: '开口 a', min: -3, max: 3, step: 0.1, def: 1 },
      { key: 'h', label: '对称轴 h', min: -3, max: 3, step: 0.1, def: 0 },
      { key: 'k', label: '顶点 k', min: -3, max: 3, step: 0.1, def: -1 },
    ],
    toggles: [{ key: 'showVertex', label: '顶点标注', def: true }],
    build({ board, p, t }) {
      board.create('functiongraph', [(x: number) => p.a * (x - p.h) * (x - p.h) + p.k, -7, 7], { strokeColor: NAVY, strokeWidth: 2.2 })
      if (t.showVertex !== false) {
        board.create('point', [p.h, p.k], { size: 3, name: `(${f1(p.h)},${f1(p.k)})`, strokeColor: GOLD, fillColor: GOLD, fixed: true })
      }
    },
    miniSvg: (p) => {
      const pts: string[] = []
      for (let x = -4; x <= 4; x += 0.12) pts.push(`${f1(60 + x * 11)},${f1(40 - (p.a ?? 1) * (x - (p.h ?? 0)) ** 2 * 4 - (p.k ?? -1) * 3)}`)
      return svgWrap(
        `<line x1="10" y1="40" x2="110" y2="40" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<line x1="60" y1="6" x2="60" y2="74" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<circle cx="${f1(60 + (p.h ?? 0) * 11)}" cy="${f1(40 - (p.k ?? -1) * 3)}" r="2.4" fill="${GOLD}"/>`,
      )
    },
  },
  {
    id: 'function/sine', category: 'function', name: '正弦函数族', desc: 'y = a·sin(bx + c)：课堂引入动图（放映态可拖滑杆）', level: 1,
    boundingbox: [-7, 4.5, 7, -4.5], axis: true, extent: [-6.5, -3.2, 6.5, 3.2],
    params: [
      { key: 'a', label: '振幅 a', min: 0.5, max: 3, step: 0.1, def: 1.5 },
      { key: 'b', label: '角频率 b', min: 0.5, max: 3, step: 0.1, def: 1 },
      { key: 'c', label: '初相 c', min: -3.14, max: 3.14, step: 0.05, def: 0 },
    ],
    toggles: [{ key: 'showCycle', label: '标出一个周期', def: false }],
    build({ board, p, t }) {
      board.create('functiongraph', [(x: number) => p.a * Math.sin(p.b * x + p.c), -7, 7], { strokeColor: NAVY, strokeWidth: 2.2 })
      if (t.showCycle) {
        const w = 2 * Math.PI / p.b
        board.create('segment', [[-p.c / p.b, 0], [-p.c / p.b + w, 0]], { strokeColor: GOLD, strokeWidth: 3, highlight: false })
      }
    },
    miniSvg: (p) => {
      const pts: string[] = []
      for (let x = -6.5; x <= 6.5; x += 0.1) pts.push(`${f1(60 + x * 8)},${f1(40 - (p.a ?? 1.5) * Math.sin((p.b ?? 1) * x + (p.c ?? 0)) * 11)}`)
      return svgWrap(
        `<line x1="10" y1="40" x2="110" y2="40" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>`,
      )
    },
  },
  {
    id: 'function/exp-log', category: 'function', name: '指数与对数', desc: 'y = eˣ 与 y = ln x 对照图', level: 1,
    boundingbox: [-5, 5.5, 7, -4.5], axis: true, extent: [-5.5, -4, 6.5, 5],
    params: [{ key: 'k', label: '缩放 k', min: 0.5, max: 2, step: 0.1, def: 1 }],
    build({ board, p }) {
      board.create('functiongraph', [(x: number) => p.k * Math.exp(x), -5.2, 2], { strokeColor: NAVY, strokeWidth: 2 })
      board.create('functiongraph', [(x: number) => p.k * Math.log(x), 0.05, 7], { strokeColor: GOLD, strokeWidth: 2 })
    },
    miniSvg: () => {
      const pts: string[] = [], pts2: string[] = []
      for (let x = -4; x <= 1.8; x += 0.12) pts.push(`${f1(60 + x * 10)},${f1(70 - Math.exp(x) * 9)}`)
      for (let x = 0.05; x <= 5.8; x += 0.12) pts2.push(`${f1(60 + x * 10)},${f1(70 - Math.log(x) * 9)}`)
      return svgWrap(
        `<line x1="8" y1="70" x2="112" y2="70" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<line x1="60" y1="6" x2="60" y2="76" stroke="${LINEC}" stroke-width="0.8"/>` +
        `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.7"/>` +
        `<polyline points="${pts2.join(' ')}" fill="none" stroke="${GOLD}" stroke-width="1.7"/>`,
      )
    },
  },

  /* ============ plane 平面几何 ============ */
  {
    id: 'plane/triangle-heights', category: 'plane', name: '三角形（高/中线）', desc: '三顶点可拖构造，高线自动跟随', level: 2,
    boundingbox: [-1, 6, 7, -1], axis: false, extent: [0.5, 0.5, 6.5, 5],
    params: [],
    toggles: [{ key: 'showHeight', label: '高线', def: true }, { key: 'showIncircle', label: '内切圆', def: false }],
    build({ board, t }) {
      const A = board.create('point', [1, 1], { name: 'A', size: 3, strokeColor: NAVY })
      const B = board.create('point', [6, 1], { name: 'B', size: 3, strokeColor: NAVY })
      const C = board.create('point', [3.2, 4.4], { name: 'C', size: 3, strokeColor: NAVY })
      board.create('polygon', [A, B, C], { fillColor: 'rgba(15,71,135,0.05)', fillOpacity: 0.5, borders: { strokeColor: NAVY, strokeWidth: 1.8 } })
      if (t.showHeight !== false) {
        const bc = board.create('line', [B, C], { visible: false })
        board.create('perpendicular', [A, bc], { strokeColor: GOLD, dash: 2, strokeWidth: 1.3, point: { visible: false } })
      }
      if (t.showIncircle) board.create('incircle', [A, B, C], { strokeColor: GOLD, fillColor: 'rgba(201,151,53,0.08)' })
    },
    miniSvg: () => svgWrap(
      `<polygon points="18,66 104,66 58,16" fill="rgba(15,71,135,0.05)" stroke="${NAVY}" stroke-width="1.7"/>` +
      `<line x1="58" y1="16" x2="58" y2="66" stroke="${GOLD}" stroke-width="1.2" stroke-dasharray="4 3"/>` +
      `<rect x="58" y="60" width="6" height="6" fill="none" stroke="${GOLD}" stroke-width="0.8"/>`,
    ),
  },
  {
    id: 'plane/circle-line', category: 'plane', name: '直线与圆', desc: '相交/相切/相离三种位置演示，直线可拖', level: 1,
    boundingbox: [-6, 5, 6, -5], axis: false, extent: [-5.5, -4.5, 5.5, 4.5],
    params: [{ key: 'r', label: '半径 r', min: 1, max: 3.5, step: 0.1, def: 2 }],
    toggles: [{ key: 'showIntersect', label: '标出交点', def: true }],
    build({ board, p, t }) {
      const O = board.create('point', [0, 0], { name: 'O', size: 2, strokeColor: NAVY, fixed: true })
      const circ = board.create('circle', [O, p.r], { strokeColor: NAVY, strokeWidth: 2 })
      const L1 = board.create('point', [-5, 1.2], { name: '', size: 1.5, strokeColor: GOLD })
      const L2 = board.create('point', [5, 2.2], { name: '', size: 1.5, strokeColor: GOLD })
      const line = board.create('line', [L1, L2], { strokeColor: GOLD, strokeWidth: 1.8 })
      if (t.showIntersect !== false) {
        try {
          board.create('intersection', [circ, line, 0], { name: 'A', size: 2, strokeColor: '#dc2646' })
          board.create('intersection', [circ, line, 1], { name: 'B', size: 2, strokeColor: '#dc2646' })
        } catch { /* 相离时无交点 */ }
      }
    },
    miniSvg: (p) => svgWrap(
      `<circle cx="56" cy="42" r="${f1((p.r ?? 2) * 13)}" fill="rgba(15,71,135,0.04)" stroke="${NAVY}" stroke-width="1.8"/>` +
      `<line x1="8" y1="30" x2="112" y2="20" stroke="${GOLD}" stroke-width="1.6"/>` +
      `<circle cx="44" cy="27.5" r="2.2" fill="#dc2646"/><circle cx="70" cy="24" r="2.2" fill="#dc2646"/>`,
    ),
  },

  /* ============ stat 统计 ============ */
  {
    id: 'stat/bar', category: 'stat', name: '柱状图', desc: '成绩分布等离散数据', level: 1,
    boundingbox: [-0.5, 5.5, 6.5, -0.5], axis: false, extent: [-0.5, -0.5, 6.5, 5.5],
    params: [{ key: 'n', label: '组数', min: 3, max: 8, step: 1, def: 5 }],
    build({ board, p }) {
      const data = Array.from({ length: Math.round(p.n) }, (_, i) => 2 + Math.sin(i * 1.3) * 1.5 + (i % 3) * 0.7)
      data.forEach((h, i) => {
        const x = i * (6 / Math.round(p.n))
        const w = (6 / Math.round(p.n)) * 0.62
        const pts = [[x, 0], [x + w, 0], [x + w, h], [x, h]].map((q) => board.create('point', q, { visible: false, fixed: true }))
        board.create('polygon', pts, { fillColor: 'rgba(15,71,135,0.16)', fillOpacity: 0.6, borders: { strokeColor: NAVY, strokeWidth: 1 }, vertices: { visible: false } })
      })
    },
    miniSvg: (p) => {
      const n = Math.round(p.n ?? 5)
      let out = ''
      const heights = [38, 26, 44, 32, 50, 30, 40, 36].slice(0, n)
      heights.forEach((h, i) => {
        const w = 96 / n - 3
        out += `<rect x="${f1(12 + (i * 96) / n)}" y="${f1(68 - h)}" width="${f1(w)}" height="${h}" fill="rgba(15,71,135,0.2)" stroke="${NAVY}" stroke-width="1"/>`
      })
      return svgWrap(out + `<line x1="10" y1="68" x2="110" y2="68" stroke="${LINEC}" stroke-width="1"/>`)
    },
  },
  {
    id: 'stat/normal', category: 'stat', name: '正态曲线', desc: 'μ、σ 可调，阴影区概率演示', level: 1,
    boundingbox: [-6, 2.2, 6, -0.6], axis: true, extent: [-6, -0.6, 6, 1.3],
    params: [
      { key: 'mu', label: '均值 μ', min: -2, max: 2, step: 0.1, def: 0 },
      { key: 'sigma', label: '标准差 σ', min: 0.4, max: 2, step: 0.1, def: 1 },
    ],
    build({ board, p }) {
      const f = (x: number) => Math.exp(-((x - p.mu) ** 2) / (2 * p.sigma * p.sigma)) / (p.sigma * Math.sqrt(2 * Math.PI))
      board.create('functiongraph', [f, -6, 6], { strokeColor: NAVY, strokeWidth: 2.2 })
      board.create('integral', [[p.mu - p.sigma, p.mu + p.sigma], board.create('functiongraph', [f, -6, 6], { visible: false })], { fillColor: 'rgba(201,151,53,0.18)', curveLeft: { visible: false }, curveRight: { visible: false } })
    },
    miniSvg: (p) => {
      const f = (x: number) => Math.exp(-((x - (p.mu ?? 0)) ** 2) / (2 * (p.sigma ?? 1) ** 2))
      const pts: string[] = []
      for (let x = -3; x <= 3; x += 0.08) pts.push(`${f1(60 + x * 16)},${f1(70 - f(x) * 52)}`)
      return svgWrap(
        `<line x1="8" y1="70" x2="112" y2="70" stroke="${LINEC}" stroke-width="0.9"/>` +
        `<polyline points="${pts.join(' ')}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<line x1="${f1(60 - (p.sigma ?? 1) * 16)}" y1="70" x2="${f1(60 - (p.sigma ?? 1) * 16)}" y2="8" stroke="${GOLD}" stroke-width="0.8" stroke-dasharray="3 2"/>` +
        `<line x1="${f1(60 + (p.sigma ?? 1) * 16)}" y1="70" x2="${f1(60 + (p.sigma ?? 1) * 16)}" y2="8" stroke="${GOLD}" stroke-width="0.8" stroke-dasharray="3 2"/>`,
      )
    },
  },
  /* ==================== B7 构图导演首批（确定性构造，配 steps 依赖解释） ==================== */
  {
    id: 'solid/pyramid-section', category: 'solid', name: '正四棱锥截面（L2）', desc: '三条棱上取点，截面实时求交重算（斜二测投影，虚实线）', level: 2,
    boundingbox: [-5.2, 5.4, 5.2, -4.4], axis: false, extent: [-3.3, -3.3, 3.3, 3.3],
    params: [
      { key: 't1', label: 'M 在 SB 点位', min: 0.12, max: 0.88, step: 0.01, def: 0.5 },
      { key: 't2', label: 'N 在 SC 点位', min: 0.12, max: 0.88, step: 0.01, def: 0.5 },
      { key: 't3', label: 'P 在 SD 点位', min: 0.12, max: 0.88, step: 0.01, def: 0.5 },
    ],
    toggles: [{ key: 'showVerts', label: '显示取点与顶点', def: true }],
    build({ board, p, t }) {
      const H = 2.2
      const verts: V3[] = [
        [-1.6, -1.6, 0], [1.6, -1.6, 0], [1.6, 1.6, 0], [-1.6, 1.6, 0], [0, 0, H],
      ]
      const edges: [number, number][] = [[0, 1], [1, 2], [2, 3], [3, 0], [4, 0], [4, 1], [4, 2], [4, 3]]
      const P = (i: number) => isoProject(verts[i][0], verts[i][1], verts[i][2])
      const names = ['A', 'B', 'C', 'D', 'S']
      const hiddenEdges = new Set([2, 3, 7])
      if (t.showVerts !== false) {
        verts.forEach((v, i) => { const pp = P(i); board.create('point', [pp[0], pp[1]], { size: 1.6, name: names[i], strokeColor: NAVY, fillColor: '#fff', fixed: true, fontSize: 12 }) })
      }
      edges.forEach(([ai, bi]) => {
        const A = P(ai), B = P(bi)
        const hidden = hiddenEdges.has(ai) && hiddenEdges.has(bi)
        board.create('segment', [A, B], { strokeColor: hidden ? LINEC : NAVY, strokeWidth: 1.8, dash: hidden ? 2 : 0 })
      })
      const lerp = (i: number, t: number): V3 => [
        verts[4][0] + (verts[i][0] - verts[4][0]) * t,
        verts[4][1] + (verts[i][1] - verts[4][1]) * t,
        verts[4][2] + (verts[i][2] - verts[4][2]) * t,
      ]
      const M3 = lerp(1, p.t1), N3 = lerp(2, p.t2), P3 = lerp(3, p.t3)
      const sec3 = sectionPolygon(verts, edges, M3, N3, P3)
      if (t.showVerts !== false) {
        for (const [q, nm] of [[M3, 'M'], [N3, 'N'], [P3, 'P']] as [V3, string][]) {
          const pp = isoProject(q[0], q[1], q[2])
          board.create('point', [pp[0], pp[1]], { size: 2.2, name: nm, strokeColor: GOLD, fillColor: GOLD, fixed: true, fontSize: 12 })
        }
      }
      if (sec3.length >= 3) {
        const poly = sec3.map((q) => isoProject(q[0], q[1], q[2]))
        board.create('polygon', poly, { fillColor: 'rgba(201,151,53,0.30)', borders: { strokeColor: GOLD, strokeWidth: 2.2, fixed: true }, vertices: { visible: false }, flexible: false })
      }
    },
    miniSvg: (p) => svgWrap(
      `<polygon points="24,64 88,64 104,80 40,80" fill="none" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="24" y1="64" x2="64" y2="12" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="88" y1="64" x2="64" y2="12" stroke="${NAVY}" stroke-width="1.6"/>` +
      `<line x1="104" y1="80" x2="64" y2="12" stroke="${LINEC}" stroke-width="1.2" stroke-dasharray="3 2"/>` +
      `<line x1="40" y1="80" x2="64" y2="12" stroke="${LINEC}" stroke-width="1.2" stroke-dasharray="3 2"/>` +
      `<polygon points="52,46 76,52 70,66 48,58" fill="rgba(201,151,53,0.35)" stroke="${GOLD}" stroke-width="1.5"/>`
    ),
  },
  {
    id: 'solid/sphere-section', category: 'solid', name: '球的截面（L2）', desc: '截面到球心距离 d 实时可调，r′=√(r²−d²)', level: 2,
    boundingbox: [-5.4, 5.4, 5.4, -5.4], axis: false, extent: [-4.2, -4.2, 4.2, 4.2],
    params: [
      { key: 'r', label: '球半径 r', min: 1.2, max: 3, step: 0.05, def: 2.4 },
      { key: 'd', label: '球心到截面距离 d', min: 0, max: 3, step: 0.05, def: 1.2 },
    ],
    toggles: [{ key: 'equator', label: '赤道参考（虚线）', def: true }],
    build({ board, p, t }) {
      const O = board.create('point', [0, 0], { size: 1.5, name: 'O', strokeColor: NAVY, fillColor: NAVY, fixed: true })
      board.create('circle', [O, p.r], { strokeColor: NAVY, strokeWidth: 2.2 })
      if (t.equator !== false) {
        board.create('circle', [O, p.r], { strokeColor: LINEC, strokeWidth: 1, dash: 2 })
      }
      const rr = Math.sqrt(Math.max(0, p.r * p.r - p.d * p.d))
      const O2 = board.create('point', [0, p.d], { size: 1.5, name: "O'", strokeColor: GOLD, fillColor: GOLD, fixed: true })
      if (rr > 0.02) {
        board.create('circle', [O2, rr], { strokeColor: GOLD, strokeWidth: 2.2 })
        board.create('segment', [[0, 0], [0, p.d]], { strokeColor: LINEC, strokeWidth: 1.2, dash: 2 })
        board.create('segment', [[0, p.d], [rr, p.d]], { strokeColor: GOLD, strokeWidth: 1.2 })
        board.create('point', [rr, p.d], { size: 1.5, name: 'r′', strokeColor: GOLD, fillColor: '#fff', fixed: true, fontSize: 11 })
      } else {
        board.create('point', [0, p.d], { size: 3, name: '切点', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      }
    },
    miniSvg: (p) => {
      const r = ((p.r ?? 2.4) / 3) * 52
      const d = ((p.d ?? 1.2) / 3) * 52
      const rr = (Math.sqrt(Math.max(0, (p.r ?? 2.4) ** 2 - (p.d ?? 1.2) ** 2)) / 3) * 52
      const cy = 60 - d
      return svgWrap(
        `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<circle cx="60" cy="60" r="${r}" fill="none" stroke="${LINEC}" stroke-dasharray="3 2" stroke-width="0.8"/>` +
        `<circle cx="60" cy="${cy}" r="${Math.max(1, rr)}" fill="rgba(201,151,53,0.18)" stroke="${GOLD}" stroke-width="1.6"/>` +
        `<line x1="60" y1="60" x2="60" y2="${cy}" stroke="${LINEC}" stroke-width="1"/>`
      )
    },
  },
  {
    id: 'plane/line-plane', category: 'plane', name: '直线与平面位置关系（L2）', desc: '相交（夹角可调）/平行/在面内 三态切换，含射影与角标', level: 2,
    boundingbox: [-5.6, 5, 5.6, -4.6], axis: false, extent: [-4.4, -3.4, 4.4, 3.4],
    params: [
      { key: 'mode', label: '位置（0相交 1平行 2在面内）', min: 0, max: 2, step: 1, def: 0 },
      { key: 'theta', label: '线面夹角 θ（度）', min: 10, max: 90, step: 5, def: 40 },
    ],
    toggles: [{ key: 'showProj', label: '显示射影 l′', def: true }],
    build({ board, p, t }) {
      const A = [-3.6, -1.2], B = [2.8, 0.4], C = [3.8, -0.9], D = [-2.6, -2.5]
      const poly = [A, B, C, D].map((q) => board.create('point', [q[0], q[1]], { visible: false, fixed: true }))
      board.create('polygon', poly, { fillColor: 'rgba(15,71,135,0.08)', borders: { strokeColor: NAVY, strokeWidth: 1.6, fixed: true }, vertices: { visible: false }, flexible: false })
      board.create('point', [1.6, -0.55], { size: 1.4, name: 'α', strokeColor: NAVY, fillColor: NAVY, fixed: true, fontSize: 14 })
      const mode = Math.round(p.mode)
      if (mode === 0) {
        const th = (p.theta * Math.PI) / 180
        const foot = [0.2, -0.35]
        const top = [foot[0] + 2.4 * Math.cos(th), foot[1] + 2.4 * Math.sin(th) + 0.4]
        board.create('segment', [[top[0] - 3 * Math.cos(th), top[1] - 3 * Math.sin(th)], top], { strokeColor: NAVY, strokeWidth: 2.2 })
        board.create('point', [top[0], top[1]], { size: 1.4, name: 'l', strokeColor: NAVY, fillColor: NAVY, fixed: true, fontSize: 13 })
        board.create('point', foot, { size: 1.6, name: 'A', strokeColor: GOLD, fillColor: GOLD, fixed: true, fontSize: 12 })
        if (t.showProj !== false) {
          board.create('segment', [foot, [foot[0] + 3, foot[1] - 0.35]], { strokeColor: GOLD, strokeWidth: 1.6, dash: 2 })
          board.create('point', [foot[0] + 3, foot[1] - 0.35], { size: 1.2, name: "l′", strokeColor: GOLD, fillColor: '#fff', fixed: true, fontSize: 12 })
        }
      } else if (mode === 1) {
        board.create('segment', [[-2.6, 1.8], [3, 1.8]], { strokeColor: NAVY, strokeWidth: 2.2 })
        board.create('point', [3, 1.8], { size: 1.4, name: 'l', strokeColor: NAVY, fillColor: NAVY, fixed: true, fontSize: 13 })
        board.create('segment', [[-2.6, -1.4], [3, -1.4]], { strokeColor: LINEC, strokeWidth: 1, dash: 2 })
      } else {
        board.create('segment', [[-2.4, -0.9], [3.2, -1.7]], { strokeColor: NAVY, strokeWidth: 2.2 })
        board.create('point', [3.2, -1.7], { size: 1.4, name: 'l', strokeColor: NAVY, fillColor: NAVY, fixed: true, fontSize: 13 })
      }
    },
    miniSvg: (p) => {
      const mode = Math.round(p.mode ?? 0)
      const line = mode === 1
        ? `<line x1="12" y1="26" x2="108" y2="26" stroke="${NAVY}" stroke-width="1.8"/>`
        : mode === 2
          ? `<line x1="14" y1="62" x2="106" y2="70" stroke="${NAVY}" stroke-width="1.8"/>`
          : `<line x1="52" y1="10" x2="30" y2="70" stroke="${NAVY}" stroke-width="1.8"/>`
      return svgWrap(
        `<polygon points="10,56 92,66 112,50 30,40" fill="rgba(15,71,135,0.07)" stroke="${NAVY}" stroke-width="1.3"/>` + line
      )
    },
  },
  {
    id: 'conic/ellipse-tangent', category: 'conic', name: '椭圆切线与焦半径（L2）', desc: '切点参数角可调，切线 + 双焦半径（光学性质演示）', level: 2,
    boundingbox: [-6, 4.6, 6, -4.2], axis: true, extent: [-4.6, -3.4, 4.6, 3.4],
    params: [
      { key: 'a', label: '半长轴 a', min: 2, max: 3.6, step: 0.1, def: 3 },
      { key: 'b', label: '半短轴 b', min: 1.4, max: 2.6, step: 0.1, def: 2 },
      { key: 't', label: '切点离心角', min: 0, max: 6.28, step: 0.05, def: 0.9 },
    ],
    toggles: [{ key: 'focal', label: '焦半径 F₁T/F₂T', def: true }],
    build({ board, p, t }) {
      const c = Math.sqrt(Math.max(0.01, p.a * p.a - p.b * p.b))
      const F1 = board.create('point', [-c, 0], { size: 2, name: 'F₁', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      const F2 = board.create('point', [c, 0], { size: 2, name: 'F₂', strokeColor: GOLD, fillColor: GOLD, fixed: true })
      board.create('curve', [(u: number) => p.a * Math.cos(u), (u: number) => p.b * Math.sin(u), 0, 2 * Math.PI], { strokeColor: NAVY, strokeWidth: 2.2 })
      const x0 = p.a * Math.cos(p.t), y0 = p.b * Math.sin(p.t)
      board.create('point', [x0, y0], { size: 2.4, name: 'T', strokeColor: GOLD, fillColor: GOLD })
      const tx1 = x0 + p.a * Math.sin(p.t), ty1 = y0 - p.b * Math.cos(p.t)
      board.create('line', [[x0, y0], [tx1, ty1]], { strokeColor: '#0e9488', strokeWidth: 1.8 })
      if (t.focal !== false) {
        board.create('segment', [F1, [x0, y0]], { strokeColor: GOLD, strokeWidth: 1.6 })
        board.create('segment', [[x0, y0], F2], { strokeColor: GOLD, strokeWidth: 1.6, dash: 2 })
      }
    },
    miniSvg: (p) => {
      const a = ((p.a ?? 3) / 3.6) * 52
      const b = ((p.b ?? 2) / 3.6) * 52
      const tx = 60 + a * Math.cos(p.t ?? 0.9)
      const ty = 40 - b * Math.sin(p.t ?? 0.9)
      return svgWrap(
        `<ellipse cx="60" cy="40" rx="${a}" ry="${b}" fill="none" stroke="${NAVY}" stroke-width="1.8"/>` +
        `<circle cx="${tx}" cy="${ty}" r="2.4" fill="${GOLD}"/>` +
        `<line x1="${tx - 34}" y1="${ty + 18}" x2="${tx + 34}" y2="${ty - 18}" stroke="#0e9488" stroke-width="1.4"/>` +
        `<circle cx="${60 - a * 0.75}" cy="40" r="2" fill="${GOLD}"/><circle cx="${60 + a * 0.75}" cy="40" r="2" fill="${GOLD}"/>`
      )
    },
  },
  {
    id: 'function/intersection', category: 'function', name: '函数交点与参数（L2）', desc: 'y=kx 与 y=x²/2 的交点随 k 实时重算并标注', level: 2,
    boundingbox: [-5.4, 5.2, 5.4, -3.4], axis: true, extent: [-4.4, -2.4, 4.4, 4.2],
    params: [{ key: 'k', label: '直线斜率 k', min: -2, max: 2, step: 0.05, def: 1 }],
    toggles: [{ key: 'guide', label: '交点虚线引导', def: true }],
    build({ board, p, t }) {
      board.create('functiongraph', [(x: number) => (x * x) / 2, -4.4, 4.4], { strokeColor: NAVY, strokeWidth: 2.2 })
      board.create('functiongraph', [(x: number) => p.k * x, -4.4, 4.4], { strokeColor: '#0e9488', strokeWidth: 1.8 })
      for (const x of [0, 2 * p.k]) {
        board.create('point', [x, (x * x) / 2], { size: 2.4, name: 'A(' + x.toFixed(2) + ',' + ((x * x) / 2).toFixed(2) + ')', strokeColor: GOLD, fillColor: GOLD, fixed: true, fontSize: 12 })
        if (t.guide !== false) {
          board.create('segment', [[x, (x * x) / 2], [x, 0]], { strokeColor: LINEC, strokeWidth: 1, dash: 2 })
        }
      }
    },
    miniSvg: (p) => {
      const xs = 2 * (p.k ?? 1)
      const px = 60 + xs * 10
      return svgWrap(
        `<path d="M12,80 Q60,-40 108,80" fill="none" stroke="${NAVY}" stroke-width="1.7"/>` +
        `<line x1="14" y1="60" x2="106" y2="60" stroke="#0e9488" stroke-width="1.5"/>` +
        `<circle cx="${Math.min(108, Math.max(12, px))}" cy="40" r="2.6" fill="${GOLD}"/>`
      )
    },
  },
]

/* ---------- 注册表查询 ---------- */
export const FIGURE_PRESETS: FigurePresetDef[] = defs
export const PRESET_MAP: Record<string, FigurePresetDef> = Object.fromEntries(defs.map((d) => [d.id, d]))
export function presetOf(id: string): FigurePresetDef | undefined { return PRESET_MAP[id] }
export function presetsByCategory(cat: V3FigureCategory): FigurePresetDef[] { return defs.filter((d) => d.category === cat) }
export function defaultParams(id: string): Record<string, number> {
  const def = PRESET_MAP[id]
  return def ? Object.fromEntries(def.params.map((q) => [q.key, q.def])) : {}
}
export function defaultToggles(id: string): Record<string, boolean> {
  const def = PRESET_MAP[id]
  return def?.toggles ? Object.fromEntries(def.toggles.map((q) => [q.key, q.def])) : {}
}
