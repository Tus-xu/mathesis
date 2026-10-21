/**
 * B7 · 构图导演任务模板（六类首批支持）
 * 每个任务 = 能力声明 + 边界 + 预设（GeoFigure）+ 参数 + 构造步骤生成器（依赖可解释）。
 * 自然语言输入做关键词路由：命中则进入引导，未命中则如实告知支持范围——不假装支持。
 */
import type { FigurePresetDef } from '@/components/mathx/presets'
import { FIGURE_PRESETS, defaultParams } from '@/components/mathx/presets'

export interface DirectorStep {
  no: number
  text: string
  object: string
  deps: string[]
}

export interface DirectorTask {
  id: string
  title: string
  /** 首批支持的六类之一 */
  category: string
  keywords: string[]
  capability: string
  limitation?: string
  presetId: string
  /** 步骤生成器：参数 → 构造步骤（依赖可解释） */
  buildSteps: (p: Record<string, number>) => DirectorStep[]
}

const preset = (id: string): FigurePresetDef => {
  const found = FIGURE_PRESETS.find((x) => x.id === id)
  if (!found) throw new Error('preset missing: ' + id)
  return found
}
export const taskPreset = (t: DirectorTask): FigurePresetDef => preset(t.presetId)
export const taskDefaults = (t: DirectorTask): Record<string, number> => defaultParams(t.presetId)

export const DIRECTOR_TASKS: DirectorTask[] = [
  {
    id: 'cube-section',
    title: '正方体过三棱三点作截面',
    category: '多面体截面',
    keywords: ['正方体', '棱柱', '截面', '三点定面', '取点'],
    capability: '在三条棱上取点（比例可拖），实时重算截面多边形；虚线标注被遮挡棱。',
    limitation: '仅支持凸多面体平面截面；圆柱/圆锥截面属曲线截线，暂不支持。',
    presetId: 'solid/cube-section',
    buildSteps: (p) => [
      { no: 1, text: `在棱 AB 上取点 M（比例 ${(p.t1 * 100).toFixed(0)}%）`, object: '点 M ∈ AB', deps: ['正方体 ABCD-A₁B₁C₁D₁'] },
      { no: 2, text: `在棱 CC₁ 上取点 N（比例 ${(p.t2 * 100).toFixed(0)}%）`, object: '点 N ∈ CC₁', deps: ['正方体'] },
      { no: 3, text: `在棱 DD₁ 上取点 P（比例 ${(p.t3 * 100).toFixed(0)}%）`, object: '点 P ∈ DD₁', deps: ['正方体'] },
      { no: 4, text: '过 M、N、P 三点确定平面 α', object: '平面 α = 面(M,N,P)', deps: ['M', 'N', 'P'] },
      { no: 5, text: '平面 α 与多面体各棱求交，连成截面多边形（金色高亮）', object: '截面多边形', deps: ['α', '各棱'] },
      { no: 6, text: '被遮挡棱画虚线，可见棱实线', object: '虚实线标注', deps: ['截面'] },
    ],
  },
  {
    id: 'pyramid-section',
    title: '正四棱锥过三棱三点作截面',
    category: '多面体截面',
    keywords: ['棱锥', '四棱锥', '截面', '锥'],
    capability: '正四棱锥三条棱上取点（比例可拖），实时重算截面；斜二测投影。',
    limitation: 'n≥3 的奇数棱锥截面演示为同一算法；顶点标注暂不支持逐点改名字。',
    presetId: 'solid/pyramid-section',
    buildSteps: (p) => [
      { no: 1, text: `底面正方形 ABCD，顶点 S（锥高比例已定）`, object: '四棱锥 S-ABCD', deps: [] },
      { no: 2, text: `在棱 SB 上取点 M（比例 ${(p.t1 * 100).toFixed(0)}%）`, object: '点 M ∈ SB', deps: ['四棱锥'] },
      { no: 3, text: `在棱 SC 上取点 N（比例 ${(p.t2 * 100).toFixed(0)}%）`, object: '点 N ∈ SC', deps: ['四棱锥'] },
      { no: 4, text: `在棱 SD 上取点 P（比例 ${(p.t3 * 100).toFixed(0)}%）`, object: '点 P ∈ SD', deps: ['四棱锥'] },
      { no: 5, text: '过 M、N、P 作截面（与各棱求交）', object: '截面多边形', deps: ['M', 'N', 'P'] },
    ],
  },
  {
    id: 'sphere-section',
    title: '球的截面（平面截球）',
    category: '球',
    keywords: ['球', '截面', '切面', '球冠'],
    capability: '截面圆半径 r′=√(r²−d²) 随截面到球心距离 d 实时变化；虚线标赤道参考。',
    limitation: '二维截面示意图（非 3D 旋转视图）；球冠曲面面积计算属后端能力。',
    presetId: 'solid/sphere-section',
    buildSteps: (p) => [
      { no: 1, text: `作球 O，半径 r = ${p.r}`, object: '球 O(r)', deps: [] },
      { no: 2, text: `取截面平面，到球心距离 d = ${p.d}`, object: '平面 α ∥ 水平面', deps: ['球'] },
      { no: 3, text: `截面圆半径 r′ = √(r²−d²) = ${Math.sqrt(Math.max(0, p.r * p.r - p.d * p.d)).toFixed(2)}`, object: '截面圆 O′(r′)', deps: ['α'] },
      { no: 4, text: 'd = r 时截面退化为切点（r′=0）', object: '退化情形', deps: ['r′'] },
    ],
  },
  {
    id: 'line-plane',
    title: '空间直线与平面的位置关系',
    category: '线面关系',
    keywords: ['直线', '平面', '位置关系', '线面', '平行', '相交', '垂直'],
    capability: '相交（夹角 θ 可调）/ 平行 / 在平面内 三种位置切换演示。',
    limitation: '教学示意图（斜二测），非可旋转 3D 视图。',
    presetId: 'plane/line-plane',
    buildSteps: (p) => {
      const mode = Math.round(p.mode)
      const name = mode === 0 ? '相交（夹角 θ）' : mode === 1 ? '平行（l ∥ α）' : '在平面内（l ⊂ α）'
      return [
        { no: 1, text: '作平面 α（平行四边形表示）', object: '平面 α', deps: [] },
        { no: 2, text: `作直线 l：${name}`, object: '直线 l', deps: ['α'] },
        ...(mode === 0 ? [{ no: 3, text: `标注线面夹角 θ = ${p.theta}°，作出 l 在 α 内的射影 l′`, object: '射影 l′ 与角 θ', deps: ['l', 'α'] }] : []),
        { no: 3 + (mode === 0 ? 1 : 0), text: mode === 1 ? '结论：l ∥ α ⇔ l 与 α 内无公共点' : mode === 2 ? '结论：l ⊂ α ⇔ l 上所有点都在 α 内' : '结论：l ∩ α = A（斜交）或 l ⊥ α（θ=90°）', object: '判定结论', deps: ['l'] },
      ]
    },
  },
  {
    id: 'ellipse-tangent',
    title: '椭圆的切线与焦点弦',
    category: '圆锥曲线',
    keywords: ['椭圆', '切线', '焦点弦', '切点'],
    capability: '椭圆上取点 T（参数角可调），作 T 处切线与两条焦半径；演示光学性质（入射=反射）。',
    limitation: '焦点弦随 T 过焦点 F₁ 的情形请在 T 靠近长轴端点观察；双曲线/抛物线切线后续加入。',
    presetId: 'conic/ellipse-tangent',
    buildSteps: (p) => {
      const c = Math.sqrt(p.a * p.a - p.b * p.b)
      return [
        { no: 1, text: `作椭圆 x²/${p.a * p.a}+y²/${p.b * p.b}=1，焦点 F₁F₂（c=${c.toFixed(2)}）`, object: '椭圆 + 焦点', deps: [] },
        { no: 2, text: `取椭圆上点 T（离心角 ${(p.t * 57.3).toFixed(0)}°）`, object: '点 T ∈ 椭圆', deps: ['椭圆'] },
        { no: 3, text: '作 T 处切线：x·x₀/a² + y·y₀/b² = 1', object: '切线 ℓ', deps: ['T'] },
        { no: 4, text: '连焦半径 F₁T、F₂T，演示"入射角=反射角"（光学性质）', object: '焦半径对', deps: ['T', '焦点'] },
      ]
    },
  },
  {
    id: 'function-intersection',
    title: '函数交点与参数变化',
    category: '函数',
    keywords: ['函数', '交点', '参数', '变化'],
    capability: '直线 y=kx 与抛物线 y=x²/2 的交点随 k 实时重算（x=0 与 x=2k），交点坐标标注。',
    limitation: '当前内置这一对函数；任意表达式交点请用「函数绘图」+ 数值观察。',
    presetId: 'function/intersection',
    buildSteps: (p) => [
      { no: 1, text: '作抛物线 y = x²/2', object: '抛物线', deps: [] },
      { no: 2, text: `作直线 y = ${p.k}x`, object: '直线', deps: [] },
      { no: 3, text: `解方程 x²/2 = ${p.k}x → x = 0 与 x = ${2 * p.k}`, object: '交点 O 与 A', deps: ['抛物线', '直线'] },
      { no: 4, text: 'k 变化时观察交点 A 的移动（k>0 两个交点，k=0 相切于原点）', object: '参数联动', deps: ['交点'] },
    ],
  },
  {
    id: 'cube-net',
    title: '正方体展开（1-4-1 型）',
    category: '展开与投影',
    keywords: ['展开', '正方体', '展开图', '投影'],
    capability: '展开度滑杆：0=直观图，1=完全展开（十字 1-4-1），六面同色便于折叠对照。',
    limitation: '仅 1-4-1 型展开；其余 10 种展开型与旋转投影属后续批次。',
    presetId: 'solid/cube-net',
    buildSteps: (p) => [
      { no: 1, text: '作正方体直观图（六面）', object: '正方体', deps: [] },
      { no: 2, text: `展开动画：当前展开度 ${(p.u * 100).toFixed(0)}%`, object: '展开过程', deps: ['正方体'] },
      { no: 3, text: '完全展开为十字 1-4-1 型，可让学生判断折叠后相对面', object: '1-4-1 展开图', deps: ['展开'] },
    ],
  },
]

export interface DirectorParseResult {
  task: DirectorTask | null
  matchedKeywords: string[]
  fallback: boolean
}

/** 自然语言 → 任务路由：关键词命中计数最高者；零命中如实返回 fallback */
export function parseDirectorInput(text: string): DirectorParseResult {
  const t = String(text || '')
  const scored = DIRECTOR_TASKS.map((task) => {
    const hits = task.keywords.filter((k) => t.includes(k))
    return { task, hits, score: hits.length }
  }).sort((a, b) => b.score - a.score)
  const best = scored[0]
  if (!best || best.score === 0) {
    return { task: null, matchedKeywords: [], fallback: true }
  }
  return { task: best.task, matchedKeywords: best.hits, fallback: false }
}

export const SUPPORTED_SUMMARY = DIRECTOR_TASKS.map((t) => `${t.title}（${t.category}）`).join('、')

/** 便捷：按任务与参数生成步骤（供 UI 与单测共用） */
export function buildTaskSteps(t: DirectorTask, p: Record<string, number>) {
  return t.buildSteps(p)
}
