/**
 * C2 · 伴随资源台候选数据（IFC-C2-a）
 * 设计依据：Desktop《教师端V3-伴随式资源库与B站教学素材创新方案.md》§5-§7
 *  - 教学意图镜头（P0）：第一层是"为这里找什么"（意图 chips），不是搜索框
 *  - 四层来源：我的 / 备课组 / 教材·官方 / 外部（外部只做引用卡，不伪装自有）
 *  - 候选 3-5 条，每条回答：是什么/来自哪里/为什么推荐/放哪里/几分钟/进入作品后变成什么
 *
 * ⚠️ WP-K / Q-1 修复（P0 假数据上生产）：
 * 下面的 `POOL` 全是**虚构内容**——虚构的"去年同课"个人历史、虚构备课组同事（王老师/李老师）、
 * 虚构教材页码（人教A版 P48 例 2）、虚构 B 站视频（BV 号/UP 主/播放量/发布时间）。
 * 这些曾经无条件喂给**始终挂载**的伴随资源台，即真实产品页上会出现编造的教学资源。
 * 现在 `buildCandidates()` 由 `companionDemoEnabled()` 门控：
 *   - 仅当 `VITE_USE_MOCK === '1'` 才返回虚构候选；
 *   - 否则返回空数组，由面板渲染"资源台未接入"的诚实说明（不是"没搜到"）。
 */

/** 生产门控：是否显式开启演示候选（默认关）。 */
export function companionDemoEnabled(): boolean {
  return import.meta.env.VITE_USE_MOCK === '1'
}

export const COMPANION_INTENTS = ['概念发生', '例题精讲', '变式迁移', '理解检查', '板书素材', '动态演示'] as const
export type CompanionIntent = (typeof COMPANION_INTENTS)[number]

export type CompanionLayer = 'mine' | 'team' | 'official' | 'external'
export const COMPANION_LAYERS: { key: CompanionLayer; label: string }[] = [
  { key: 'mine', label: '我的' },
  { key: 'team', label: '备课组' },
  { key: 'official', label: '教材·官方' },
  { key: 'external', label: '外部' },
]

export interface CompanionCandidate {
  id: string
  kind: 'question' | 'plan-fragment' | 'deck-fragment' | 'figure' | 'video' | 'link'
  layer: CompanionLayer
  title: string
  source: string
  why: string
  minutes?: number
  /** 进入作品后变成什么（按钮/回执用） */
  becomes: string
  intents: CompanionIntent[]
  preview?: { latex?: string; thumb?: string }
  video?: {
    bvid: string
    up: string
    upAvatar?: string
    start: string
    startSec: number
    end: string
    duration: string
    pre: string
    pause?: string
    post: string
    views?: string
    published?: string
    /** 视频缩略图（SVG 占位或真实封面 URL） */
    thumb?: string
    /** 教学标签：适合的教学场景 */
    teachingTags?: string[]
    /** 难度/年级标签 */
    grade?: string
  }
  figure?: { kind: 'free' | 'fx'; thumb: string; records?: unknown[]; expr?: string }
  url?: string
  /** 外部资源的许可/可用性诚实标注 */
  note?: string
}

/** ⚠️ 演示候选池（虚构来源/理由/视频）。**仅** `VITE_USE_MOCK=1` 时可用。 */
const POOL: CompanionCandidate[] = [
  /* ===== 我的 ===== */
  {
    id: 'c-mine-plan-1', kind: 'plan-fragment', layer: 'mine',
    title: '去年同课 · 「和为定值 → 差为定值」问题链',
    source: '我的教案 · 双曲线（去年高二(3)班）', why: '你去年用三问递进处理「差的绝对值」必要性，与本页「改变条件」思路一致', minutes: 6,
    becomes: '插入当前片段 · 文字块', intents: ['概念发生'],
    preview: { latex: '① 圆的定义改一个字会怎样？② 差的绝对值为定值时轨迹还存在吗？③ 不加绝对值画出的是哪一支？' },
  },
  {
    id: 'c-mine-deck-1', kind: 'deck-fragment', layer: 'mine',
    title: '去年课件 · 标准方程推导页（一次平方即可）',
    source: '我的课件 · 双曲线（去年）', why: '推导页含两次平方与一次平方的对照板书，可直接复用为对照素材', minutes: 5,
    becomes: '插入当前片段 · 文字块', intents: ['概念发生', '板书素材'],
    preview: { latex: '对照椭圆两次平方：\\sqrt{(x+c)^2+y^2}-\\sqrt{(x-c)^2+y^2}=\\pm 2a 移项后一次平方即可' },
  },
  {
    id: 'c-mine-fig-2', kind: 'figure', layer: 'mine',
    title: '正方体截面示意（可重开构造）',
    source: '我的图形库 · 含结构化构造记录', why: '板书立体截面时先让学生猜形状再展示验证，与你当前例题题型匹配', minutes: 3,
    becomes: '插入为图形素材 / 绘图台继续编辑', intents: ['板书素材', '动态演示'],
    figure: { kind: 'free', thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><polygon points="38,60 72,60 72,26 38,26" fill="none" stroke="#c3cad6"/><polygon points="55,43 89,43 89,9 55,9" fill="none" stroke="#4f46e5"/><polygon points="46,52 82,35 65,17 33,34" fill="rgba(6,182,212,0.18)" stroke="#0891b2"/></svg>', records: [{ id: 'r1', kind: 'preset', preset_id: 'solid/cube-section', params: { t1: 0.5, t2: 0.55, t3: 0.45 }, color: '#4f46e5', width: 1.6 }] },
  },
  {
    id: 'c-mine-q-1', kind: 'question', layer: 'mine',
    title: '自编 · 与椭圆同焦点且过定点的方程',
    source: '我的题库 · 自编（用 6 次）', why: '同焦点条件训练待定系数法，适合放在变式第一位', minutes: 4,
    becomes: '插入当前片段 · 例题', intents: ['变式迁移'],
    preview: { latex: '与椭圆 \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 有相同焦点，且过点 (2,\\sqrt{3}) 的椭圆方程为' },
  },
  {
    id: 'c-mine-deck-2', kind: 'deck-fragment', layer: 'mine',
    title: '去年课件 · 例 1 求标准方程（分三步板书）',
    source: '我的课件 · 双曲线（去年）', why: '例 1 与当前教材例题同型，板书分步可直接沿用', minutes: 8,
    becomes: '插入当前片段 · 文字块', intents: ['例题精讲'],
    preview: { latex: '例 1：焦点在 x 轴、a=4、b=3，求标准方程。\\quad 定位 → 定 a²b² → 写方程' },
  },
  {
    id: 'c-mine-plan-2', kind: 'plan-fragment', layer: 'mine',
    title: '去年当堂检测 · 定义三连问（2 分钟回收）',
    source: '我的教案 · 双曲线（去年）', why: '三道直查小题覆盖定义/方程/判别，当堂 2-5 分钟可完成', minutes: 3,
    becomes: '插入当前片段 · 文字块', intents: ['理解检查'],
    preview: { latex: '① 2a=|F_1F_2| 时轨迹？② 焦点在 y 轴时方程？③ 判别看什么？' },
  },
  /* ===== 备课组 ===== */
  {
    id: 'c-team-plan-1', kind: 'plan-fragment', layer: 'team',
    title: '备课组共案 · 双曲线引入的差异处理',
    source: '备课组共案 · 主备人王老师', why: '共案比你的版本多了参数范围讨论，可对照后只取这一段', minutes: 5,
    becomes: '对照后只取这一段 · 文字块', intents: ['概念发生', '例题精讲'],
    preview: { latex: '共案新增：先设 2a 与 |F_1F_2| 分三类讨论，再引出 0<2a<|F_1F_2| 的必要性' },
  },
  {
    id: 'c-team-q-1', kind: 'question', layer: 'team',
    title: '校本 · 过焦点弦的弦长计算',
    source: '校本题库 · 焦点弦（用 21 次）', why: '承载「联立—韦达—弦长」主干方法，计算量适中可当例题精讲', minutes: 8,
    becomes: '插入当前片段 · 例题', intents: ['例题精讲'],
    preview: { latex: '过椭圆 \\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1 右焦点且斜率为 1 的直线交椭圆于 A、B，求 |AB|' },
  },
  {
    id: 'c-team-deck-1', kind: 'deck-fragment', layer: 'team',
    title: '同事公开课 · 「判别反转」对照页',
    source: '备课组 · 李老师公开课件', why: '椭圆看分母 vs 双曲线看系数正负的对照版式，可直接借鉴', minutes: 3,
    becomes: '对照后只取这一段 · 文字块', intents: ['例题精讲', '板书素材'],
    preview: { latex: '\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1 看分母大小　⇌　\\frac{x^{2}}{a^{2}}-\\frac{y^{2}}{b^{2}}=1 看项系数正负' },
  },
  {
    id: 'c-team-q-2', kind: 'question', layer: 'team',
    title: '校本 · 含参单调性分类讨论',
    source: '校本题库 · 含参讨论（易错题集）', why: '暴露「分类不完备」高频错因，适合变式后接错因辨析', minutes: 10,
    becomes: '插入当前片段 · 例题', intents: ['变式迁移'],
    preview: { latex: '讨论 a 的取值对 f(x)=ax^{3}-3x^{2}+1 单调性的影响' },
  },
  /* ===== 教材·官方 ===== */
  {
    id: 'c-off-q-1', kind: 'question', layer: 'official',
    title: '教材例 2 · 已知焦点求标准方程',
    source: '人教A版选择性必修一 P48 例 2', why: '教材例题与当前定义推导无缝衔接，板书分步规范', minutes: 6,
    becomes: '插入当前片段 · 例题', intents: ['例题精讲'],
    preview: { latex: '焦点在 y 轴、c=5、a=3 的双曲线，求它的标准方程' },
  },
  {
    id: 'c-off-plan-1', kind: 'plan-fragment', layer: 'official',
    title: '教材节选 · 拉链情境图（§2.3 章头）',
    source: '人教A版选择性必修一 P46', why: '教材章头即拉链情境，与教材对齐的引入比外部素材更稳', minutes: 3,
    becomes: '插入当前片段 · 文字块', intents: ['概念发生'],
    preview: { latex: '教材章头：拉开拉链时笔尖的运动轨迹满足什么条件？' },
  },
  {
    id: 'c-off-q-2', kind: 'question', layer: 'official',
    title: '课本习题 2-2 第 1、2 题（当堂检测）',
    source: '人教A版选择性必修一 P52', why: '定义与标准方程的直查题，2-5 分钟可完成回收掌握度', minutes: 4,
    becomes: '插入当前片段 · 检测题', intents: ['理解检查'],
    preview: { latex: '求焦点在 x 轴、a=4、b=3 的双曲线标准方程；判断 2a=|F_1F_2| 时轨迹是否存在' },
  },
  {
    id: 'c-off-link-1', kind: 'link', layer: 'official',
    title: '网络画板 · 双曲线参数联动构造',
    source: 'netpad.net.cn · 官方开放资源', why: '国产引擎、可谈商务授权，参数可调适合动态演示', minutes: 3,
    becomes: '引用来源 · 引用卡', intents: ['动态演示'],
    url: 'https://www.netpad.net.cn/',
    note: '官方开放平台资源；嵌入第三方产品需商务授权（官网生态含希沃/WPS/讯飞）',
  },
  /* ===== 外部 ===== */
  {
    id: 'c-ext-video-1', kind: 'video', layer: 'external',
    title: '拉链实验与双曲线定义（片段）',
    source: 'B站 · UP主「一数」', why: '02:31 起正好演示「距离差」，可在给出定义前暂停提问', minutes: 2,
    becomes: '挂到当前片段 · 外部视频引用卡', intents: ['概念发生', '动态演示'],
    video: {
      bvid: 'BV1GJ411x7h7', up: '一数', upAvatar: '📐',
      start: '02:31', startSec: 151, end: '04:10', duration: '1分39秒',
      pre: '你观察到哪两个量的差保持不变？',
      pause: '04:10 后视频直接给出定义，建议在此暂停',
      post: '为什么必须是差的绝对值？不加绝对值会怎样？',
      views: '128.5万', published: '2024-09',
      grade: '高二',
      teachingTags: ['概念引入', '实验演示', '情境创设'],
      thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><defs><linearGradient id="g1" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#1a3a6e"/><stop offset="100%" stop-color="#0d2137"/></linearGradient></defs><rect width="320" height="180" fill="url(#g1)"/><path d="M160,90 Q180,30 220,30 T280,90 Q260,150 220,150 T160,90" fill="none" stroke="#f59e0b" stroke-width="2.5" opacity="0.9"/><path d="M160,90 Q140,30 100,30 T40,90 Q60,150 100,150 T160,90" fill="none" stroke="#60a5fa" stroke-width="2.5" opacity="0.9"/><circle cx="70" cy="90" r="5" fill="#fbbf24"/><circle cx="250" cy="90" r="5" fill="#fbbf24"/><text x="70" y="110" text-anchor="middle" fill="#93c5fd" font-size="11" font-family="sans-serif">F₁</text><text x="250" y="110" text-anchor="middle" fill="#93c5fd" font-size="11" font-family="sans-serif">F₂</text><text x="160" y="155" text-anchor="middle" fill="#fcd34d" font-size="13" font-weight="bold" font-family="sans-serif">拉链实验 · 双曲线定义</text><text x="160" y="28" text-anchor="middle" fill="#fff" font-size="12" font-weight="600" font-family="sans-serif" opacity="0.85">【高中数学】双曲线的定义与几何意义</text></svg>',
    },
    url: 'https://www.bilibili.com/video/BV1GJ411x7h7?t=151',
    note: '外部引用 · 官方站外播放器；不下载不转存，课堂播放依赖教室网络，建议备好无视频 Plan B',
  },
  {
    id: 'c-ext-video-2', kind: 'video', layer: 'external',
    title: '正方体截面动态演示（片段）',
    source: 'B站 · UP主「几何可视化」', why: '截面形状随点位连续变化，比静态图更适合引入猜想', minutes: 2,
    becomes: '挂到当前片段 · 外部视频引用卡', intents: ['动态演示', '概念发生'],
    video: {
      bvid: 'BV1xx411c7mD', up: '几何可视化', upAvatar: '🎲',
      start: '00:45', startSec: 45, end: '02:10', duration: '1分25秒',
      pre: '猜一猜：平面截正方体，截面最多是几边形？',
      pause: '02:10 后出现结论，先让学生说完再放',
      post: '六边形截面的六个顶点分别在哪些棱上？',
      views: '56.2万', published: '2024-11',
      grade: '高一',
      teachingTags: ['空间想象', '动态演示', '猜想验证'],
      thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><defs><linearGradient id="g2" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#1e3a5f"/><stop offset="100%" stop-color="#0f172a"/></linearGradient></defs><rect width="320" height="180" fill="url(#g2)"/><polygon points="110,120 190,120 190,50 110,50" fill="none" stroke="#64748b" stroke-width="1.5" opacity="0.6"/><polygon points="150,95 230,95 230,25 150,25" fill="none" stroke="#64748b" stroke-width="1.5" opacity="0.6"/><polygon points="110,120 150,95 230,95 190,120" fill="none" stroke="#64748b" stroke-width="1.5" opacity="0.6"/><polygon points="190,120 230,95 230,25 190,50" fill="none" stroke="#64748b" stroke-width="1.5" opacity="0.6"/><polygon points="150,95 190,50 230,25 190,70" fill="none" stroke="#64748b" stroke-width="1.5" opacity="0.6"/><polygon points="130,108 200,78 210,52 140,82" fill="rgba(245,158,11,0.25)" stroke="#f59e0b" stroke-width="2"/><text x="160" y="160" text-anchor="middle" fill="#fcd34d" font-size="12" font-weight="bold" font-family="sans-serif">正方体截面 · 六边形</text><text x="160" y="22" text-anchor="middle" fill="#fff" font-size="12" font-weight="600" font-family="sans-serif" opacity="0.85">【立体几何】正方体截面的所有可能</text></svg>',
    },
    url: 'https://www.bilibili.com/video/BV1xx411c7mD?t=45',
    note: '外部引用 · 官方站外播放器；不下载不转存',
  },
  {
    id: 'c-ext-link-1', kind: 'link', layer: 'external',
    title: 'GeoGebra · Conic Sections 交互组件',
    source: 'geogebra.org · 外部公开资源', why: '参数拖动体验好，可作灵感来源或课堂演示', minutes: 3,
    becomes: '引用来源 · 引用卡', intents: ['动态演示', '概念发生'],
    url: 'https://www.geogebra.org/m/classic-conics',
    note: '许可注意：教师个人课堂教学免费；嵌入第三方商业产品需与 GeoGebra 签许可',
  },
]

/** 教学意图自动镜头：按当前上下文猜"为这里找什么"（猜不中就返回 null，让教师手选） */
export function detectIntent(ctx: { selectionSummary?: string; slideKind?: string; sectionName?: string }): CompanionIntent | null {
  const text = [ctx.selectionSummary, ctx.sectionName, ctx.slideKind].filter(Boolean).join(' ')
  if (!text) return null
  if (/例题|精讲|讲题/.test(text)) return '例题精讲'
  if (/变式|练习|作业|迁移/.test(text)) return '变式迁移'
  if (/检测|小测|检查|小结|回收/.test(text)) return '理解检查'
  if (/板书|对照表|版式/.test(text)) return '板书素材'
  if (/演示|动画|动图|视频|图形/.test(text)) return '动态演示'
  if (/引入|概念|定义|探究|推导|情境/.test(text)) return '概念发生'
  return null
}

/**
 * 按意图 + 来源层（+ 可选关键词）取候选：确定性排序，3-5 条，不搞瀑布流。
 *
 * Q-1：**没有接入真实资源库时返回空数组** —— 面板据此显示"资源台未接入"，
 * 而不是把虚构的"备课组同事的课件""B 站视频"当成真实候选端给老师。
 */
export function buildCandidates(intent: CompanionIntent, layer: CompanionLayer, query = ''): CompanionCandidate[] {
  if (!companionDemoEnabled()) return []
  const q = query.trim()
  const hits = POOL.filter((c) => {
    if (c.layer !== layer) return false
    if (!c.intents.includes(intent)) return false
    if (q && ![c.title, c.source, c.why].some((t) => t.includes(q))) return false
    return true
  })
  return hits.slice(0, 5)
}
