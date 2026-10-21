/**
 * P · 备课中心链路**演示样例**数据（IFC-P-a）
 *
 * ⚠️ WP-K / Q-1 修复（P0 假数据上生产）：
 * 本文件过去被 `/teacher-v3/prep`、`/teacher-v3/prep-templates` 等**线上路由**直接 import，
 * 于是"虚构示范校 + 虚构教师 + 虚构奖项 + 虚构文件 + 虚构上传解析置信度"会出现在真实产品页上。
 * 现在**所有**样例数据都被 `prepDemoEnabled()` 门控：
 *   - 仅当 `VITE_USE_MOCK === '1'`（显式开演示）才返回样例；
 *   - 否则访问器返回**诚实的空/未知**（空数组 / null / 只保留真实题库命中），
 *     由调用方渲染空态，绝不编造内容。
 *
 * 业务代码**不得**直接引用下面的 `*_DEMO` 常量，一律走导出的访问器函数。
 */

/** 生产门控：是否显式开启演示样例（默认关）。用函数而非常量，便于测试按需 stub。 */
export function prepDemoEnabled(): boolean {
  return import.meta.env.VITE_USE_MOCK === '1'
}

export interface Blueprint {
  id: string
  title: string
  school: string
  teacher: string
  award: string
  lessonType: string
  sections: number
}

export interface LibraryRef {
  id: string
  name: string
  date: string
  size: string
  kind: 'docx' | 'pdf'
}

/** ⚠️ 演示样例：虚构示范校/教师/奖项。**不得**在真实产品路由上渲染。 */
export const AILP_BLUEPRINTS_DEMO: Blueprint[] = [
  { id: 'bp-1', title: '椭圆及其标准方程', school: '人大附中', teacher: '王老师', award: '全国一等奖', lessonType: '新授课', sections: 8 },
  { id: 'bp-2', title: '导数的概念', school: '华师大二附中', teacher: '张老师', award: '省优课', lessonType: '新授课', sections: 7 },
  { id: 'bp-3', title: '函数的单调性', school: '北京四中', teacher: '刘老师', award: '部级优课', lessonType: '概念课', sections: 6 },
  { id: 'bp-4', title: '空间向量应用', school: '上海中学', teacher: '陈老师', award: '市优课', lessonType: '应用课', sections: 7 },
]

/** ⚠️ 演示样例：虚构文件名/日期/大小。**不得**在真实产品路由上渲染。 */
export const AILP_LIBRARY_REFS_DEMO: LibraryRef[] = [
  { id: 'lib-1', name: '立体几何复习课-参考教案.docx', date: '2026-08-15', size: '1.8MB', kind: 'docx' },
  { id: 'lib-2', name: '导数应用-单元教学设计.pdf', date: '2026-08-02', size: '2.3MB', kind: 'pdf' },
  { id: 'lib-3', name: '三角函数图像与性质-公开课教案.docx', date: '2026-07-20', size: '1.5MB', kind: 'docx' },
  { id: 'lib-4', name: '数列求和方法总结-复习课.docx', date: '2026-07-08', size: '980KB', kind: 'docx' },
]

/**
 * 优质课蓝本（真实数据源）。
 * 8100/8000 目前**没有**优质课蓝本端点 → 生产返回空数组（诚实空态），不返回虚构示范校。
 */
export function blueprintLibrary(): Blueprint[] {
  return prepDemoEnabled() ? AILP_BLUEPRINTS_DEMO : []
}

/**
 * 资源库引用（真实数据源）。
 * 目前**没有**"我的上传资料 → 可引用教案"端点 → 生产返回空数组（诚实空态）。
 */
export function libraryRefs(): LibraryRef[] {
  return prepDemoEnabled() ? AILP_LIBRARY_REFS_DEMO : []
}

/** 上传资料 AI 解析样例（确定性；置信度带演示标注）。 */
export interface ParsedKnowledge { name: string; confidence: number; desc: string; partial?: boolean }
export interface ParsedExample { no: number; title: string; stem: string; from: string; difficulty: '基础' | '中等' | '提高' }
export interface ParsedUpload {
  files: { name: string; size: string; pages: number }[]
  structure: { no: number; name: string; count: string; children?: { text: string; tag?: string }[]; collapsed?: boolean }[]
  knowledge: ParsedKnowledge[]
  examples: ParsedExample[]
}

/**
 * ⚠️ 演示样例：上传解析结论 + 置信度。**不得**在真实产品路由上渲染。
 *
 * 生产通道：8100 没有"上传教案 → 解析结构/知识点/例题"端点，因此真实产品页
 * **无法**展示解析结论。这时返回 `null`，调用方渲染诚实的"尚未解析/未知"态，
 * 而不是把虚构的 98% 置信度当成真实解析结果。
 */
export function buildParsedUpload(fileNames: string[]): ParsedUpload | null {
  if (!prepDemoEnabled()) return null
  return buildParsedUploadDemo(fileNames)
}

/** 演示样例构造（仅 `VITE_USE_MOCK=1` 路径；测试需显式 stub 环境变量）。 */
export function buildParsedUploadDemo(fileNames: string[]): ParsedUpload {
  const files = (fileNames.length ? fileNames : ['椭圆优秀教案参考.docx']).map((n, i) => ({
    name: n, size: `${(1.2 + i * 0.7).toFixed(1)} MB`, pages: 12 + i * 18,
  }))
  return {
    files,
    structure: [
      { no: 1, name: '教学目标', count: '3 条', children: [
        { text: '理解椭圆的定义，掌握椭圆标准方程的两种形式' },
        { text: '经历椭圆标准方程的推导过程，体会数形结合思想' },
        { text: '能根据给定条件求椭圆的标准方程，解决简单几何应用' },
      ] },
      { no: 2, name: '教学重点与难点', count: '2 项', children: [
        { text: '椭圆的定义与标准方程的建立；理解 a、b、c 的含义及其关系', tag: '重' },
        { text: '椭圆标准方程推导过程中坐标系的选择与化简', tag: '难' },
      ] },
      { no: 3, name: '教学过程', count: '核心 · 6 环节', children: [
        { text: '情境引入（5 min）' }, { text: '概念形成（10 min）' }, { text: '例题精讲（12 min）' },
        { text: '课堂练习（8 min）' }, { text: '课堂小结（5 min）' }, { text: '作业布置（5 min）' },
      ] },
      { no: 4, name: '板书设计', count: '折叠', collapsed: true },
      { no: 5, name: '教学反思', count: '折叠', collapsed: true },
    ],
    knowledge: [
      { name: '椭圆的定义', confidence: 98, desc: '平面内与两定点距离之和为常数的点的轨迹' },
      { name: '椭圆的标准方程', confidence: 95, desc: '焦点在 x 轴 / y 轴上的两种标准形式' },
      { name: 'a、b、c 的关系', confidence: 96, desc: 'a² = b² + c²，三者的几何意义与联系' },
      { name: '待定系数法', confidence: 92, desc: '求椭圆标准方程的基本方法与步骤' },
      { name: '数形结合思想', confidence: 90, desc: '坐标法思想，几何问题代数化' },
      { name: '椭圆的几何性质', confidence: 68, desc: '范围、对称性、顶点、离心率（文档中涉及较少）', partial: true },
    ],
    examples: [
      { no: 1, title: '求椭圆标准方程题', stem: '已知椭圆的两个焦点坐标分别是 (-2, 0)、(2, 0)，并且经过点 (5/2, -3/2)，求它的标准方程。', from: '来自 P.6 例题 2', difficulty: '基础' },
      { no: 2, title: '椭圆性质应用题', stem: '已知椭圆 x²/25 + y²/9 = 1 上一点 P 到一个焦点的距离为 6，求点 P 到另一个焦点的距离，并判断点 P 的位置。', from: '来自 P.8 例 3', difficulty: '中等' },
      { no: 3, title: '待定系数法综合题', stem: '已知椭圆经过两点 A(√3, -2) 和 B(-2√3, 1)，求椭圆的标准方程，并求其焦点坐标和离心率。', from: '来自 P.10 拓展题', difficulty: '提高' },
    ],
  }
}

/* ============ P2 · 环节资源预配（题目/公式/图形） ============
 * 题目：从真实题库列表按知识点关键词匹配（题库检索优先心智）。
 * 公式/图形：**演示词表**，仅 `VITE_USE_MOCK=1` 时返回；生产返回空（不编造公式与示意图）。
 * 诚实边界（Q-1）：过去公式与图形是无条件返回的演示素材；现已按 `prepDemoEnabled()` 门控。 */

export const CANNED_FIGURES: Record<string, { name: string; svg: string }> = {
  椭圆: {
    name: '椭圆焦点示意图',
    svg: '<svg viewBox="0 0 280 150" width="240" height="128" xmlns="http://www.w3.org/2000/svg"><line x1="15" y1="75" x2="265" y2="75" stroke="#cbd5e1"/><line x1="140" y1="18" x2="140" y2="135" stroke="#cbd5e1"/><ellipse cx="140" cy="75" rx="100" ry="48" fill="rgba(79,70,229,0.08)" stroke="#4f46e5" stroke-width="2"/><circle cx="90" cy="75" r="4" fill="#06b6d4"/><circle cx="190" cy="75" r="4" fill="#06b6d4"/><text x="82" y="92" font-size="12" fill="#0891b2" font-style="italic">F₁</text><text x="194" y="92" font-size="12" fill="#0891b2" font-style="italic">F₂</text><circle cx="172" cy="50" r="3.5" fill="#4338ca"/><text x="178" y="46" font-size="11" fill="#4338ca" font-style="italic">M</text><line x1="90" y1="75" x2="172" y2="50" stroke="#818cf8" stroke-dasharray="4 3"/><line x1="190" y1="75" x2="172" y2="50" stroke="#818cf8" stroke-dasharray="4 3"/></svg>',
  },
  双曲线: {
    name: '双曲线两支示意图',
    svg: '<svg viewBox="0 0 280 150" width="240" height="128" xmlns="http://www.w3.org/2000/svg"><line x1="15" y1="75" x2="265" y2="75" stroke="#cbd5e1"/><line x1="140" y1="18" x2="140" y2="135" stroke="#cbd5e1"/><path d="M 172 15 C 148 55, 148 95, 172 135" fill="none" stroke="#4f46e5" stroke-width="2"/><path d="M 108 15 C 132 55, 132 95, 108 135" fill="none" stroke="#4f46e5" stroke-width="2"/><circle cx="120" cy="75" r="4" fill="#06b6d4"/><circle cx="160" cy="75" r="4" fill="#06b6d4"/><text x="112" y="92" font-size="12" fill="#0891b2" font-style="italic">F₁</text><text x="164" y="92" font-size="12" fill="#0891b2" font-style="italic">F₂</text><line x1="100" y1="20" x2="180" y2="130" stroke="#94a3b8" stroke-dasharray="5 4"/><line x1="180" y1="20" x2="100" y2="130" stroke="#94a3b8" stroke-dasharray="5 4"/></svg>',
  },
  导数: {
    name: '切线与瞬时变化率图',
    svg: '<svg viewBox="0 0 280 150" width="240" height="128" xmlns="http://www.w3.org/2000/svg"><line x1="15" y1="130" x2="265" y2="130" stroke="#cbd5e1"/><line x1="20" y1="12" x2="20" y2="135" stroke="#cbd5e1"/><path d="M 25 120 C 90 30, 170 30, 255 110" fill="none" stroke="#4f46e5" stroke-width="2"/><circle cx="140" cy="58" r="4" fill="#06b6d4"/><line x1="90" y1="100" x2="205" y2="24" stroke="#f59e0b" stroke-width="1.6"/><text x="146" y="48" font-size="11" fill="#0891b2" font-style="italic">P</text><text x="196" y="20" font-size="11" fill="#d97706" font-style="italic">切线</text></svg>',
  },
  立体: {
    name: '正方体截面演示',
    svg: '<svg viewBox="0 0 280 150" width="240" height="128" xmlns="http://www.w3.org/2000/svg"><polygon points="70,110 150,110 150,40 70,40" fill="none" stroke="#94a3b8" stroke-width="1.4"/><polygon points="130,85 210,85 210,15 130,15" fill="none" stroke="#94a3b8" stroke-width="1.4"/><line x1="70" y1="110" x2="130" y2="85" stroke="#94a3b8"/><line x1="150" y1="110" x2="210" y2="85" stroke="#94a3b8"/><line x1="70" y1="40" x2="130" y2="15" stroke="#94a3b8"/><line x1="150" y1="40" x2="210" y2="15" stroke="#94a3b8"/><polygon points="100,105 190,85 175,35 92,52" fill="rgba(6,182,212,0.18)" stroke="#06b6d4" stroke-width="1.8"/></svg>',
  },
}

const FORMULA_LEXICON: Record<string, string[]> = {
  椭圆: ['\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1\\;(a>b>0)', '|MF_{1}|+|MF_{2}|=2a\\;(2a>2c)', 'a^{2}=b^{2}+c^{2}'],
  双曲线: ['\\frac{x^{2}}{a^{2}}-\\frac{y^{2}}{b^{2}}=1\\;(a>0,b>0)', '\\big||MF_{1}|-|MF_{2}|\\big|=2a\\;(0<2a<|F_{1}F_{2}|)', 'b^{2}=c^{2}-a^{2}'],
  导数: ["f'(x)>0 \\Rightarrow f(x)\\ \\text{单调递增}", "v(t)=h'(t)", "\\frac{f(x_{2})-f(x_{1})}{x_{2}-x_{1}}=f'(\\xi)"],
  立体: ['E、F、G 为棱中点，截面 \\to 六边形', 'S_{\\triangle} = \\frac{1}{2}\\cdot|AB|\\cdot d'],
  抛物线: ['y^{2}=2px\\;(p>0)', '|PF| = x_{0} + \\frac{p}{2}'],
}

export interface PhaseResource {
  questions: { stem: string; source: string; difficulty: string }[]
  formulas: string[]
  figure?: { name: string; svg: string }
}

interface BankLike { stem_latex: string; source?: string; difficulty: string; kp_name: string }
/** 资源库条目：`thumb` 是服务端下发的**缩略图**，不是图形素材，Q-1 起不再当 SVG 用。 */
interface LibLike { name: string; thumb: string }

function topicKey(text: string): string | null {
  if (/椭圆/.test(text)) return '椭圆'
  if (/双曲线/.test(text)) return '双曲线'
  if (/抛物线/.test(text)) return '抛物线'
  if (/导数|单调|极值|含参|切线/.test(text)) return '导数'
  if (/截面|立体|正方体|棱/.test(text)) return '立体'
  return null
}

/**
 * 按课题+环节名匹配预配资源：题目（真实题库）+ 公式/图形（演示，仅 `VITE_USE_MOCK=1`）。
 *
 * Q-1 改动（两处，均为"不再编造"）：
 * 1. 公式与图形只在显式演示模式返回；生产只返回真实题库命中，无命中就是空。
 * 2. 删除 `libHit.thumb → figure.svg` 这条混用：服务端缩略图不是图形素材，
 *    且它是唯一一条**不可信字符串进入 `v-html`** 的路径（潜在注入面），直接去掉。
 */
export function matchPhaseResources(
  bank: BankLike[],
  lib: LibLike[],
  topic: string,
  sectionName: string,
): PhaseResource {
  void lib
  const demo = prepDemoEnabled()
  const key = topicKey(`${topic} ${sectionName}`)
  const empty: PhaseResource = { questions: [], formulas: [], figure: undefined }
  if (!key) return empty
  const kwBank = bank.filter((q) => (q.kp_name || '').includes(key) || q.stem_latex.includes(key))
  const pool = kwBank.length ? kwBank : bank
  const toQ = (q: BankLike) => ({ stem: q.stem_latex, source: q.source || '题库', difficulty: q.difficulty === 'easy' ? '基础' : q.difficulty === 'medium' ? '中等' : '较难' })
  let questions: { stem: string; source: string; difficulty: string }[] = []
  if (/例题|精讲/.test(sectionName)) {
    questions = [...pool].sort((a) => (a.difficulty === 'easy' ? -1 : 1)).slice(0, 2).map(toQ)
  } else if (/变式|训练|练习/.test(sectionName)) {
    questions = pool.filter((q) => q.difficulty !== 'easy').slice(0, 2).map(toQ)
  } else if (/检测|小结|测试/.test(sectionName)) {
    questions = pool.filter((q) => q.difficulty === 'easy').slice(0, 1).map(toQ)
  } else if (/作业/.test(sectionName)) {
    questions = pool.slice(-1).map(toQ)
  }
  // 公式 / 图形：仅演示模式
  const formulas = demo
    ? (FORMULA_LEXICON[key] || []).slice(0, /概念|定义|推导|探究/.test(sectionName) ? 2 : 1)
    : []
  let figure: PhaseResource['figure']
  if (demo && /概念|探究|引入|演示|图形|小结/.test(sectionName)) {
    figure = CANNED_FIGURES[key]
  }
  if (!questions.length && !formulas.length && !figure) return empty
  return { questions, formulas, figure }
}
