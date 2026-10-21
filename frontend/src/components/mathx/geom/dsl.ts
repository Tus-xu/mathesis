/**
 * geom/dsl —— 构造脚本解析（确定性，无 AI 伪装）
 * 目标句式 = 真实试卷语言（阶段 A 的题15/16/19 全部可解析）：
 *   平行六面体 ABCD-A₁B₁C₁D₁ ｜ 正方体/长方体 ABCD-A₁B₁C₁D₁
 *   四棱锥 P-ABCD（底面…）｜ 三棱锥 P-ABC / 四面体 ABCD ｜ 直三棱柱 ABC-A₁B₁C₁
 *   E为A₁D₁的中点 ｜ G在BD上且BG=2GD ｜ N为CC₁上靠近C的三等分点 ｜ E∈A₁D₁，A₁E:ED₁=1:2
 *   F为BC₁与B₁C的交点 ｜ 连接DB₁、BE、AF ｜ DB₁、BE、AF画虚线 ｜ 过A、M、N作截面
 * 未识别的句子进入 uncovered 并如实披露（对齐 C1.1 要求编译器的诚实红线）。
 */
import {
  type GeomDoc, type GId, type GPoint,
  findPointByLabel, normLabel, buildParallelepiped, buildPyramid4, buildTetra, buildPrism3,
  buildCornerModel, buildBurgerModel, buildConeInSphere, addCylinder, buildParallelPlanes,
  addMidpoint, addPointOnEdge, addRatioPoint, addIntersect, addSegment, addSection, addVector,
} from './model'

export interface DslResult {
  applied: string[]      // 已执行的构造描述
  uncovered: string[]    // 无法识别/无法执行的句子 + 原因
}

/** "A1B1C1D1" / "A₁B₁C₁D₁" / "A'B'C'D'" → 规范下标形式 */
function canon(s: string): string {
  return s.trim().replace(/1/g, '₁').replace(/'/g, '′')
}

/** "ABCD-A1B1C1D1" → base labels + top labels */
function parseTwinLabels(s: string): { base: string[]; top: string[] } | null {
  const m = s.replace(/\s/g, '').match(/^([A-Z]{3,4})[-—－]([A-Z0-9₁1′']+)$/)
  if (!m) return null
  const base = m[1].split('')
  const topRaw = m[2].replace(/[₁1′']/g, '')
  if (topRaw.length !== base.length) return null
  return { base, top: base.map((c, i) => `${topRaw[i] === c ? c : topRaw[i]}₁`) }
}

const CN_NUM: Record<string, number> = { 三: 3, 四: 4, 五: 5, 六: 6 }

/** L = 已知标签 → id 的查找；找不到时把句子记为未满足 */
type Lookup = (label: string) => GId | null

function segsOfLabels(lookup: Lookup, tokens: string[]): { ids: [GId, GId]; text: string } | { error: string } {
  if (tokens.length !== 2) return { error: '线段需两个端点' }
  const a = lookup(tokens[0]), b = lookup(tokens[1])
  if (!a || !b) return { error: `找不到点 ${!a ? tokens[0] : tokens[1]}` }
  return { ids: [a, b], text: `${tokens[0]}${tokens[1]}` }
}

const L = '(?:[A-Z](?:[₁1′\']+)?)'
const TWO = `(${L})\\s*(${L})`

/** 解析并执行。逐句处理：解析失败/执行失败都进 uncovered。 */
export function applyConstruction(doc: GeomDoc, text: string): DslResult {
  const applied: string[] = []
  const uncovered: string[] = []
  const lookup: Lookup = (label) => {
    const p = findPointByLabel(doc, label)
    return p ? p.id : null
  }
  const fail = (raw: string, why: string) => uncovered.push(`「${raw}」：${why}`)

  const sentences = String(text || '')
    .split(/[；;\n。！？]/)
    .map((s) => s.trim())
    .filter(Boolean)

  for (const raw of sentences) {
    try {
      const s = raw.replace(/[（(].*?[)）]/g, '') // 去括号注释
      /* ---- 立体骨架 ---- */
      const twin = s.match(new RegExp(`(平行六面体|斜平行六面体|正方体|长方体|正四棱柱)\\s*(${L}{3,4}[-—－]${L}{3,4})`, ''))
      if (twin) {
        const labels = parseTwinLabels(twin[2])
        const kind = twin[1]
        if (!labels) { fail(raw, '顶点标注无法解析'); continue }
        const full = [...labels.base, ...labels.top]
        if (kind === '平行六面体' || kind === '斜平行六面体') buildParallelepiped(doc, { labels: full })
        else if (kind === '正方体') buildParallelepiped(doc, { u: [3.4, 0, 0], v: [0, 2.2, 0], w: [0, 0, 3.2], labels: full })
        else if (kind === '正四棱柱') buildParallelepiped(doc, { u: [2.4, 0, 0], v: [0, 2.4, 0], w: [0, 0, 3.8], labels: full })
        else buildParallelepiped(doc, { labels: full })
        applied.push(`${kind} ${canon(twin[2])}`)
        continue
      }
      /* 经典外接球模型（教材通用族，非具体题） */
      if (/墙角(模型|三棱锥)?|三棱锥.{0,6}两两垂直/.test(s)) {
        buildCornerModel(doc)
        applied.push('墙角模型：三棱锥（PA、PB、PC 两两垂直）+ 外接球 R=√(a²+b²+c²)/2')
        continue
      }
      if (/汉堡(模型)?|直棱柱.{0,4}外接球/.test(s)) {
        buildBurgerModel(doc)
        applied.push('汉堡模型：直三棱柱 + 上下底面外接圆圆心 O₁O₂ + 外接球')
        continue
      }
      if (/球.{0,4}内接.{0,4}锥|圆锥.{0,8}内接.{0,4}球|内接圆锥/.test(s)) {
        buildConeInSphere(doc)
        applied.push('球内接圆锥：P 在球顶，底面圆在球面上（A、B、M、N 为圆周点）')
        continue
      }
      if (/圆柱/.test(s)) {
        addCylinder(doc)
        applied.push('圆柱（底面 O，轴竖直，可拖底心变形）')
        continue
      }
      if (/平行平面|α\s*∥\s*β/.test(s)) {
        buildParallelPlanes(doc)
        applied.push('两平行平面 α ∥ β（水平板块）')
        continue
      }
      const pyr = s.match(new RegExp(`(四棱锥|三棱锥|正${'三'}棱锥|五棱锥|四面体)\\s*(?:(P|S|O)[-—－])?(${L}{3,4})`))
      if (pyr) {
        const name = pyr[1]
        const apexLabel = pyr[2] || (name === '四面体' ? 'D' : 'P')
        const baseLabels = canon(pyr[3]).replace(/₁/g, '').split('')
        if (name === '四面体') {
          const lbl = canon(pyr[3]).split('')
          if (lbl.length !== 4) { fail(raw, '四面体需要 4 个顶点标注'); continue }
          buildTetra(doc, lbl)
        } else if (name === '三棱锥') {
          buildTetra(doc, [...baseLabels, apexLabel])
        } else {
          buildPyramid4(doc, { apexLabel, labels: baseLabels })
        }
        applied.push(`${name} ${apexLabel}-${canon(pyr[3]).replace(/₁/g, '')}`)
        continue
      }
      const prism = s.match(new RegExp(`(直三棱柱|正三棱柱|三棱柱)\\s*(${L}{3}[-—－]${L}{3})`))
      if (prism) {
        const labels = parseTwinLabels(prism[2])
        if (!labels) { fail(raw, '顶点标注无法解析'); continue }
        buildPrism3(doc, { labels: [...labels.base, ...labels.top] })
        applied.push(`${prism[1]} ${canon(prism[2])}`)
        continue
      }

      /* ---- 交点（先于中点匹配，避免"的交点"被吞） ---- */
      const ix = s.match(new RegExp(`(${L})[为是]\\s*${TWO}\\s*与\\s*${TWO}\\s*的交点`))
      if (ix) {
        const [_, X, p1, p2, q1, q2] = ix
        const P = segsOfLabels(lookup, [p1, p2])
        const Q = segsOfLabels(lookup, [q1, q2])
        if ('error' in P) { fail(raw, P.error); continue }
        if ('error' in Q) { fail(raw, Q.error); continue }
        const pt = addIntersect(doc, P.ids, Q.ids, canon(X))
        applied.push(`${canon(X)} = ${P.text} ∩ ${Q.text}`)
        continue
      }

      /* ---- 中点 ---- */
      const mid = s.match(new RegExp(`(${L})[为是]?\\s*${TWO}\\s*的?中点`))
      if (mid && !mid[0].includes('交点')) {
        const [_, X, a, b] = mid
        const A = lookup(a), B = lookup(b)
        if (!A || !B) { fail(raw, `找不到点 ${!A ? a : b}`); continue }
        addMidpoint(doc, A, B, canon(X))
        applied.push(`${canon(X)} 为 ${canon(a)}${canon(b)} 中点`)
        continue
      }

      /* ---- 棱上点且比例：G在BD上且BG=2GD ---- */
      const rat = s.match(new RegExp(`(${L})\\s*在\\s*${TWO}\\s*上[，,]?且?\\s*(${L})(${L})\\s*[=＝]\\s*(\\d+)\\s*(${L})(${L})`))
      if (rat) {
        const [_, X, ea, eb, m1, mX, mm, nX, mn] = rat
        void m1
        const A = lookup(ea), B = lookup(eb)
        if (!A || !B) { fail(raw, `找不到点 ${!A ? ea : eb}`); continue }
        // "BG=2GD"：|端点X端|=k·|另一端X端| → k:1
        const fromA = normLabel(mX) === normLabel(ea) || normLabel(mn) === normLabel(eb)
        const k = Number(mm)
        if (!Number.isFinite(k) || k <= 0) { fail(raw, '比例无法解析'); continue }
        addRatioPoint(doc, A, B, k, 1, fromA ? 'a' : 'b', canon(X))
        applied.push(`${canon(X)} 在 ${canon(ea)}${canon(eb)} 上且 ${k}:1`)
        continue
      }

      /* ---- 棱上点 ∈：E∈A₁D₁，A₁E:ED₁=1:2 ---- */
      const inPt = s.match(new RegExp(`(${L})\\s*[∈在]\\s*${TWO}\\s*上?`))
      if (inPt) {
        const [_, X, a, b] = inPt
        const A = lookup(a), B = lookup(b)
        if (!A || !B) { fail(raw, `找不到点 ${!A ? a : b}`); continue }
        const ratio = s.match(new RegExp(`${canon(a).replace('₁', '[₁1]')}${canon(X).replace('₁', '[₁1]')}\\s*[:：]\\s*${canon(X).replace('₁', '[₁1]')}${canon(b).replace('₁', '[₁1]')}\\s*[=＝]\\s*(\\d+)\\s*[:：]\\s*(\\d+)`))
        if (ratio) {
          addRatioPoint(doc, A, B, Number(ratio[1]), Number(ratio[2]), 'a', canon(X))
          applied.push(`${canon(X)} ∈ ${canon(a)}${canon(b)}，${ratio[1]}:${ratio[2]}`)
        } else {
          addPointOnEdge(doc, A, B, 0.5, canon(X))
          applied.push(`${canon(X)} ∈ ${canon(a)}${canon(b)}（默认中点，可拖动调整）`)
        }
        continue
      }

      /* ---- n等分点：N为CC₁上靠近C的三等分点 ---- */
      const div = s.match(new RegExp(`(${L})[为是]?\\s*${TWO}\\s*上?靠近\\s*(${L})\\s*的(\\d+)等分点`))
      if (div) {
        const [_, X, a, b, near, k] = div
        const A = lookup(a), B = lookup(b)
        if (!A || !B) { fail(raw, `找不到点 ${!A ? a : b}`); continue }
        const fromA = normLabel(near) === normLabel(a)
        addRatioPoint(doc, A, B, fromA ? 1 : Number(k) - 1, fromA ? Number(k) - 1 : 1, 'a', canon(X))
        applied.push(`${canon(X)} 为 ${canon(a)}${canon(b)} 靠近 ${canon(near)} 的 ${k} 等分点`)
        continue
      }

      /* ---- 虚线/实线：DB₁、BE、AF画虚线 ---- */
      const dash = s.match(new RegExp(`^(.+?)(画|改为|改成|用)?虚线$`))
      if (dash && /虚线/.test(s)) {
        const names = dash[1].split(/[、，,和\s]+/).map((x) => x.trim()).filter(Boolean)
        let ok = 0
        for (const nm of names) {
          const seg = findSegment(doc, nm, lookup)
          if (seg) { seg.style = 'dashed'; ok++ }
        }
        if (ok === names.length && ok > 0) { applied.push(`${names.map(canon).join('、')} 设为虚线`); continue }
        fail(raw, ok === 0 ? '找不到对应线段（可先「连接」再设虚线）' : `仅 ${ok}/${names.length} 条匹配`)
        continue
      }

      /* ---- 截面：过A、M、N作截面 ---- */
      const sec = s.match(new RegExp(`过\\s*(${L})\\s*[、，,]\\s*(${L})\\s*[、，,]\\s*(${L})\\s*作?截面`))
      if (sec) {
        const ids: [GId, GId, GId] = [lookup(sec[1]), lookup(sec[2]), lookup(sec[3])].map((x) => x || '') as [GId, GId, GId]
        if (ids.some((x) => !x)) { fail(raw, '截面三点未全部找到'); continue }
        const r = addSection(doc, ids)
        if ('error' in r) { fail(raw, r.error); continue }
        applied.push(`过 ${canon(sec[1])}、${canon(sec[2])}、${canon(sec[3])} 作截面`)
        continue
      }

      /* ---- 向量：作向量AB ---- */
      const vec = s.match(new RegExp(`作?向量\\s*${TWO}$`))
      if (vec) {
        const P = segsOfLabels(lookup, [vec[1], vec[2]])
        if ('error' in P) { fail(raw, P.error); continue }
        addVector(doc, P.ids[0], P.ids[1])
        applied.push(`向量 ${P.text}`)
        continue
      }

      /* ---- 连接：连接DB₁、BE、AF / 画线段MN ---- */
      const conn = s.match(new RegExp(`^(连接|画线段?|作线段)(.+)$`))
      if (conn) {
        const names = conn[2].split(/[、，,和\s]+/).map((x) => x.trim()).filter(Boolean)
        const fails: string[] = []
        const done: string[] = []
        for (const nm of names) {
          const pair = nm.match(new RegExp(`^(${L})(${L})$`))
          if (!pair) { fails.push(nm); continue }
          const P = segsOfLabels(lookup, [pair[1], pair[2]])
          if ('error' in P) { fails.push(nm); continue }
          addSegment(doc, P.ids[0], P.ids[1])
          done.push(P.text)
        }
        if (done.length) applied.push(`连接 ${done.map(canon).join('、')}`)
        if (fails.length) fail(raw, `无法解析为线段：${fails.join('、')}`)
        else continue
        if (done.length) continue
        continue
      }

      fail(raw, '未识别的句式（支持：立体骨架 / 中点 / 棱上比例点 / 交点 / 连接 / 虚线 / 截面 / 向量）')
    } catch (e) {
      fail(raw, e instanceof Error ? e.message : '执行失败')
    }
  }
  return { applied, uncovered }
}

function findSegment(doc: GeomDoc, name: string, lookup: Lookup): { style: 'auto' | 'solid' | 'dashed' } | null {
  const m = name.match(new RegExp(`^(${L})(${L})$`))
  if (!m) return null
  const a = lookup(m[1]), b = lookup(m[2])
  if (!a || !b) return null
  const seg = doc.objects.find((o) => (o as { a?: GId; b?: GId; style?: string }).style !== undefined && ((o as { a?: GId }).a === a && (o as { b?: GId }).b === b || (o as { a?: GId }).a === b && (o as { b?: GId }).b === a)) as { style: 'auto' | 'solid' | 'dashed' } | undefined
  return seg || null
}

/** 快速骨架（模板按钮共用） */
export function skeleton(kind: string, doc: GeomDoc): string {
  switch (kind) {
    case 'parallelepiped': buildParallelepiped(doc); return '平行六面体 ABCD-A₁B₁C₁D₁'
    case 'cube': buildParallelepiped(doc, { u: [3.4, 0, 0], v: [0, 2.2, 0], w: [0, 0, 3.2] }); return '正方体 ABCD-A₁B₁C₁D₁'
    case 'cuboid': buildParallelepiped(doc, { u: [4.4, 0, 0], v: [0, 1.7, 0], w: [0, 0, 2.6] }); return '长方体 ABCD-A₁B₁C₁D₁'
    case 'pyramid4': buildPyramid4(doc); return '四棱锥 P-ABCD'
    case 'tetra': buildTetra(doc); return '三棱锥 P-ABC'
    case 'prism3': buildPrism3(doc); return '直三棱柱 ABC-A₁B₁C₁'
    case 'corner': buildCornerModel(doc); return '墙角模型（两两垂直三棱锥+外接球）'
    case 'burger': buildBurgerModel(doc); return '汉堡模型（直三棱柱+外接球）'
    case 'cone': buildConeInSphere(doc); return '球内接圆锥（P 顶点 + 底面圆周 A/B/M/N）'
    case 'cylinder': addCylinder(doc); return '圆柱（轴竖直）'
    case 'planes': buildParallelPlanes(doc); return '两平行平面 α ∥ β'
    default: return ''
  }
}

/** 供 UI 提示：已知点清单 */
export function knownPoints(doc: GeomDoc): GPoint[] {
  return doc.objects.filter((o) => (o as GPoint).def !== undefined) as GPoint[]
}
