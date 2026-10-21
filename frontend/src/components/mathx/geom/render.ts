/**
 * geom/render —— 场景投影 + SVG 渲染（纯函数）
 * 与阶段 A 失败对齐：
 * - 标签是 SVG <text>（不再用 HTML 覆盖层）→ 导出/插入后标注不丢
 * - 无坐标网格/坐标轴（立体几何不需要）
 * - solid 棱凸体自动虚实；用户线段 auto 规则 + 手动覆盖
 */
import {
  type GeomDoc, type GSolid, type GPoint, type GSegment, type GVector, type GSection,
  type GPlanePlate,
  type V3, pointPos, solidEdgeVisibility, solidVertVisibility, autoDashed, project, v3,
} from './model'

export interface ScenePt { id: string; label: string; x: number; y: number; visible: boolean; onSolid: boolean }
export interface SceneSeg { id: string; x1: number; y1: number; x2: number; y2: number; dashed: boolean; kind: 'solidEdge' | 'user' | 'solidEdgeHidden'; color: string }
export interface SceneSection { id: string; pts: [number, number][] }
export interface SceneVector { id: string; x1: number; y1: number; x2: number; y2: number }
export interface SceneSphere { id: string; cx: number; cy: number; r: number; ryEquator: number }
export interface SceneShape { id: string; kind: 'cone' | 'cylinder'; cx: number; cyB: number; cyT: number; rx: number; ry: number }
export interface ScenePlate { id: string; pts: [number, number][]; label?: string }
export interface Scene {
  pts: ScenePt[]
  segs: SceneSeg[]
  sections: SceneSection[]
  vectors: SceneVector[]
  spheres: SceneSphere[]
  shapes: SceneShape[]
  plates: ScenePlate[]
  bbox: [number, number, number, number]
}

const INK = '#123a6d'
const HIDDEN = '#9aa6b8'
const GOLD = '#c99735'

export function computeScene(doc: GeomDoc): Scene {
  const cam = doc.camera
  const toScreen = (p: V3): [number, number] => {
    const [x, up] = project(p, cam)
    return [x * cam.zoom, -up * cam.zoom]
  }
  const pts: ScenePt[] = []
  const segs: SceneSeg[] = []
  const sections: SceneSection[] = []
  const vectors: SceneVector[] = []
  const spheres: SceneSphere[] = []
  const shapes: SceneShape[] = []
  const plates: ScenePlate[] = []

  const solids = doc.objects.filter((o) => (o as GSolid).edges !== undefined) as GSolid[]

  for (const o of doc.objects) {
    if ('r' in o && 'c' in o && !('verts' in o) && !('h' in o)) {
      const c = toScreen(pointPos(doc, o.c))
      spheres.push({ id: o.id, cx: c[0], cy: c[1], r: o.r * cam.zoom, ryEquator: o.r * cam.zoom * Math.abs(Math.sin(cam.pitch)) })
    }
    if ('h' in o && 'c' in o && ('kind' in o)) {
      const cb = toScreen(pointPos(doc, o.c))
      const ct = toScreen(v3.add(pointPos(doc, o.c), [0, 0, o.h] as V3))
      shapes.push({
        id: o.id, kind: (o as { kind: 'cone' | 'cylinder' }).kind,
        cx: cb[0], cyB: cb[1], cyT: ct[1],
        rx: o.r * cam.zoom, ry: o.r * cam.zoom * Math.abs(Math.sin(cam.pitch)),
      })
    }
    if ((o as GPlanePlate).plate === true && (o as GPlanePlate).p !== undefined) {
      const pl = o as GPlanePlate
      const pts = pl.p.map((id) => toScreen(pointPos(doc, id)))
      // 平行四边形第 4 顶点 = p2 + p3 − p1
      const fourth: [number, number] = [pts[1][0] + pts[2][0] - pts[0][0], pts[1][1] + pts[2][1] - pts[0][1]]
      plates.push({ id: pl.id, pts: [pts[0], pts[1], fourth, pts[2]], label: pl.label })
    }
  }

  for (const s of solids) {
    const ev = solidEdgeVisibility(doc, s)
    s.edges.forEach(([a, b], i) => {
      const pa = toScreen(pointPos(doc, s.verts[a]))
      const pb = toScreen(pointPos(doc, s.verts[b]))
      segs.push({
        id: `${s.id}:${i}`, x1: pa[0], y1: pa[1], x2: pb[0], y2: pb[1],
        dashed: !ev[i], kind: ev[i] ? 'solidEdge' : 'solidEdgeHidden', color: ev[i] ? INK : HIDDEN,
      })
    })
  }
  for (const o of doc.objects) {
    if ('def' in o) continue                       // GPoint（后面统一处理）
    if ('verts' in o && 'edges' in o) continue     // GSolid（棱已在上面的循环处理）
    if ('p' in o) {                                // GSection：三点平面截当前 solid
      const tri = o.p.map((id) => pointPos(doc, id))
      const poly = planePolygon(doc, tri)
      if (poly) sections.push({ id: o.id, pts: poly.map(toScreen) })
      continue
    }
    if ('style' in o) {                            // GSegment
      const pa = toScreen(pointPos(doc, o.a))
      const pb = toScreen(pointPos(doc, o.b))
      const dashed = o.style === 'dashed' || (o.style === 'auto' && autoDashed(doc, o.a, o.b))
      segs.push({ id: o.id, x1: pa[0], y1: pa[1], x2: pb[0], y2: pb[1], dashed, kind: 'user', color: o.color || GOLD })
      continue
    }
    if ('a' in o && 'b' in o) {                    // GVector
      const pa = toScreen(pointPos(doc, o.a))
      const pb = toScreen(pointPos(doc, o.b))
      vectors.push({ id: o.id, x1: pa[0], y1: pa[1], x2: pb[0], y2: pb[1] })
    }
  }
  for (const o of doc.objects) {
    const pt = o as GPoint
    if (pt.def === undefined) continue
    const s2 = toScreen(pointPos(doc, pt.id))
    let visible = true
    let onSolid = false
    for (const sol of solids) {
      const vi = sol.verts.indexOf(pt.id)
      if (vi >= 0) { onSolid = true; visible = solidVertVisibility(doc, sol)[vi] }
    }
    pts.push({ id: pt.id, label: pt.label, x: s2[0], y: s2[1], visible, onSolid })
  }

  const xs: number[] = [], ys: number[] = []
  for (const s of segs) { xs.push(s.x1, s.x2); ys.push(s.y1, s.y2) }
  for (const p of pts) { xs.push(p.x); ys.push(p.y) }
  for (const s of spheres) { xs.push(s.cx - s.r, s.cx + s.r); ys.push(s.cy - s.r, s.cy + s.r) }
  for (const s of shapes) { xs.push(s.cx - s.rx, s.cx + s.rx); ys.push(s.cyB - s.ry, Math.min(s.cyB, s.cyT)) }
  for (const p of plates) for (const q of p.pts) { xs.push(q[0]); ys.push(q[1]) }
  const bbox: [number, number, number, number] = xs.length
    ? [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
    : [-100, -80, 100, 80]
  return { pts, segs, sections, vectors, spheres, shapes, plates, bbox }
}

/** 由三点平面截当前 solid 得多边形（渲染层每帧重算；模型 addSection 已做校验） */
function planePolygon(doc: GeomDoc, tri: V3[]): V3[] | null {
  // 局部实现避免循环依赖 model.sectionOf（其带错误语义），这里按边求交
  const solids = doc.objects.filter((o) => (o as GSolid).edges !== undefined) as GSolid[]
  if (!solids.length) return null
  const s = solids[0]
  const n = v3.cross(v3.sub(tri[1], tri[0]), v3.sub(tri[2], tri[0]))
  if (v3.len(n) < 1e-9) return null
  const norm = v3.scale(n, 1 / v3.len(n))
  const P = s.verts.map((id) => pointPos(doc, id))
  const side = P.map((p) => v3.dot(norm, v3.sub(p, tri[0])))
  const xs: V3[] = []
  for (const [a, b] of s.edges) {
    const sa = side[a], sb = side[b]
    if ((sa > 1e-9 && sb < -1e-9) || (sa < -1e-9 && sb > 1e-9)) xs.push(v3.lerp(P[a], P[b], sa / (sa - sb)))
    else if (Math.abs(sa) <= 1e-9) xs.push(P[a])
  }
  const uniq: V3[] = []
  for (const x of xs) if (!uniq.some((u) => v3.dist(u, x) < 1e-6)) uniq.push(x)
  if (uniq.length < 3) return null
  const c = uniq.reduce((acc, p) => v3.add(acc, p), [0, 0, 0] as V3)
  const center = v3.scale(c, 1 / uniq.length)
  const e1v = v3.sub(uniq[0], center)
  const e1 = v3.scale(e1v, 1 / (v3.len(e1v) || 1))
  const e2 = v3.cross(norm, e1)
  uniq.sort((u, w) =>
    Math.atan2(v3.dot(v3.sub(u, center), e2), v3.dot(v3.sub(u, center), e1)) -
    Math.atan2(v3.dot(v3.sub(w, center), e2), v3.dot(v3.sub(w, center), e1)))
  return uniq
}

export interface RenderOpts { selectedId?: string | null; padding?: number }

export interface SvgLayout { w: number; h: number; ox: number; oy: number }

/** 与 sceneToSvg 相同的布局换算（组件把指针像素 → 场景坐标时复用） */
export function layoutOf(scene: Scene, pad = 46): SvgLayout {
  const [x0, y0, x1, y1] = scene.bbox
  const w = Math.max(220, Math.ceil(x1 - x0 + pad * 2))
  const h = Math.max(180, Math.ceil(y1 - y0 + pad * 2))
  return { w, h, ox: pad - x0, oy: pad - y0 }
}

/** 场景 → SVG 字符串（无网格；标签内嵌；selected 金色高亮） */
export function sceneToSvg(scene: Scene, opts: RenderOpts = {}): { svg: string; w: number; h: number } {
  const pad = opts.padding ?? 46
  const { w, h, ox, oy } = layoutOf(scene, pad)
  const out: string[] = []
  const P = (x: number, y: number) => `${(x + ox).toFixed(1)},${(y + oy).toFixed(1)}`

  // 球最先画（垫底）：轮廓大圆实线 + 赤道椭圆（后半虚、前半实）
  for (const s of scene.spheres) {
    const sel = opts.selectedId === s.id
    const col = sel ? GOLD : INK
    const cx = s.cx + ox, cy = s.cy + oy
    out.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${s.r.toFixed(1)}" fill="none" stroke="${col}" stroke-width="${sel ? 2.6 : 2}"/>`)
    const rx = s.r, ry = Math.max(2, s.ryEquator)
    out.push(`<path d="M ${(cx - rx).toFixed(1)} ${cy.toFixed(1)} A ${rx.toFixed(1)} ${ry.toFixed(1)} 0 0 1 ${(cx + rx).toFixed(1)} ${cy.toFixed(1)}" fill="none" stroke="${HIDDEN}" stroke-width="1.6" stroke-dasharray="6 4"/>`)
    out.push(`<path d="M ${(cx - rx).toFixed(1)} ${cy.toFixed(1)} A ${rx.toFixed(1)} ${ry.toFixed(1)} 0 0 0 ${(cx + rx).toFixed(1)} ${cy.toFixed(1)}" fill="none" stroke="${HIDDEN}" stroke-width="1.8"/>`)
  }
  // 平行平面板块（淡填充 + 边框 + 角标）
  for (const p of scene.plates) {
    const ps = p.pts.map(([x, y]) => `${(x + ox).toFixed(1)},${(y + oy).toFixed(1)}`).join(' ')
    out.push(`<polygon points="${ps}" fill="rgba(15,71,135,0.07)" stroke="${INK}" stroke-width="1.8" stroke-linejoin="round"/>`)
    if (p.label) {
      const [lx, ly] = p.pts[3]
      out.push(`<text x="${(lx + ox + 14).toFixed(1)}" y="${(ly + oy - 10).toFixed(1)}" font-size="15" font-weight="700" fill="${GOLD}" font-family="'PingFang SC','Microsoft YaHei',sans-serif">${escapeXml(p.label)}</text>`)
    }
  }
  // 圆锥 / 圆柱：底面椭圆（后虚前实）+ 轮廓线（圆锥两条母线 / 圆柱两条竖直轮廓 + 顶面实椭圆）
  for (const s of scene.shapes) {
    const rx = s.rx, ry = Math.max(2, s.ry)
    const bxc = s.cx + ox, byc = s.cyB + oy
    out.push(`<path d="M ${(bxc - rx).toFixed(1)} ${byc.toFixed(1)} A ${rx.toFixed(1)} ${ry.toFixed(1)} 0 0 1 ${(bxc + rx).toFixed(1)} ${byc.toFixed(1)}" fill="none" stroke="${HIDDEN}" stroke-width="1.6" stroke-dasharray="6 4"/>`)
    out.push(`<path d="M ${(bxc - rx).toFixed(1)} ${byc.toFixed(1)} A ${rx.toFixed(1)} ${ry.toFixed(1)} 0 0 0 ${(bxc + rx).toFixed(1)} ${byc.toFixed(1)}" fill="none" stroke="${INK}" stroke-width="2"/>`)
    if (s.kind === 'cone') {
      out.push(`<line x1="${(bxc - rx).toFixed(1)}" y1="${byc.toFixed(1)}" x2="${(s.cx + ox).toFixed(1)}" y2="${(s.cyT + oy).toFixed(1)}" stroke="${INK}" stroke-width="2"/>`)
      out.push(`<line x1="${(bxc + rx).toFixed(1)}" y1="${byc.toFixed(1)}" x2="${(s.cx + ox).toFixed(1)}" y2="${(s.cyT + oy).toFixed(1)}" stroke="${INK}" stroke-width="2"/>`)
    } else {
      const txc = s.cx + ox, tyc = s.cyT + oy
      out.push(`<line x1="${(bxc - rx).toFixed(1)}" y1="${byc.toFixed(1)}" x2="${(txc - rx).toFixed(1)}" y2="${tyc.toFixed(1)}" stroke="${INK}" stroke-width="2"/>`)
      out.push(`<line x1="${(bxc + rx).toFixed(1)}" y1="${byc.toFixed(1)}" x2="${(txc + rx).toFixed(1)}" y2="${tyc.toFixed(1)}" stroke="${INK}" stroke-width="2"/>`)
      out.push(`<ellipse cx="${txc.toFixed(1)}" cy="${tyc.toFixed(1)}" rx="${rx.toFixed(1)}" ry="${ry.toFixed(1)}" fill="none" stroke="${INK}" stroke-width="2"/>`)
    }
  }

  for (const sec of scene.sections) {
    out.push(`<polygon points="${sec.pts.map(([x, y]) => P(x, y)).join(' ')}" fill="${GOLD}" fill-opacity="0.16" stroke="${GOLD}" stroke-width="2.4" stroke-linejoin="round"/>`)
  }
  // 被遮挡的棱先画（垫底），再画实线
  const order = [...scene.segs].sort((a, b) => (a.kind === 'solidEdgeHidden' ? 0 : 1) - (b.kind === 'solidEdgeHidden' ? 0 : 1))
  for (const s of order) {
    const sel = opts.selectedId === s.id || (s.kind === 'user' && opts.selectedId === s.id)
    const dash = s.dashed ? ' stroke-dasharray="7 5"' : ''
    const color = sel ? GOLD : s.color
    const width = s.kind === 'user' ? 2.4 : 2
    out.push(`<line x1="${(s.x1 + ox).toFixed(1)}" y1="${(s.y1 + oy).toFixed(1)}" x2="${(s.x2 + ox).toFixed(1)}" y2="${(s.y2 + oy).toFixed(1)}" stroke="${color}" stroke-width="${sel ? width + 0.8 : width}"${dash} stroke-linecap="round"/>`)
  }
  for (const v of scene.vectors) {
    const dx = v.x2 - v.x1, dy = v.y2 - v.y1
    const L = Math.hypot(dx, dy) || 1
    const ux = dx / L, uy = dy / L
    const hx = v.x2 - ux * 10, hy = v.y2 - uy * 10
    out.push(`<line x1="${(v.x1 + ox).toFixed(1)}" y1="${(v.y1 + oy).toFixed(1)}" x2="${(hx + ox).toFixed(1)}" y2="${(hy + oy).toFixed(1)}" stroke="${INK}" stroke-width="2.2"/>`)
    out.push(`<polygon points="${P(v.x2, v.y2)} ${(hx - uy * 4.4 + ox).toFixed(1)},${(hy + ux * 4.4 + oy).toFixed(1)} ${(hx + uy * 4.4 + ox).toFixed(1)},${(hy - ux * 4.4 + oy).toFixed(1)}" fill="${INK}"/>`)
  }
  for (const p of scene.pts) {
    const sel = opts.selectedId === p.id
    const c = sel ? GOLD : INK
    out.push(`<circle cx="${(p.x + ox).toFixed(1)}" cy="${(p.y + oy).toFixed(1)}" r="${sel ? 5 : 3.4}" fill="#fff" stroke="${c}" stroke-width="${sel ? 3 : 2.2}"/>`)
    if (p.label) out.push(`<text x="${(p.x + ox + 9).toFixed(1)}" y="${(p.y + oy - 8).toFixed(1)}" font-size="15" font-weight="600" fill="${c}" text-anchor="start" font-family="'PingFang SC','Microsoft YaHei',sans-serif">${escapeXml(p.label)}</text>`)
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><rect x="0" y="0" width="${w}" height="${h}" fill="#ffffff"/>${out.join('')}</svg>`
  return { svg, w, h }
}

function escapeXml(s: string): string {
  return s.replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c] as string))
}
