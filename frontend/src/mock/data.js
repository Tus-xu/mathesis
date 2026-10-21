/**
 * mock/data.js —— 内存模拟数据（对齐 v4 愿景演示内容）
 * 仅用于本地预览：会话/消息/复习计划等，页面刷新后重置。
 */
import { uuid } from '../components/chat/messageModel'

export const MOCK_USER = {
  nickname: '小婷',
  roles: [{ role: 'student' }],
  grade: '高二（3）班',
  avatar: '婷',
}

export function iso(offsetMin = 0, base = Date.now()) {
  return new Date(base + offsetMin * 60000).toISOString()
}

/* ---------- 会话 ---------- */
const conv = (id, title, updatedAt, extra = {}) => ({
  id,
  title,
  workspace: 'student',
  created_at: updatedAt,
  updated_at: updatedAt,
  message_count: 0,
  pinned: false,
  ...extra,
})

export const seedConversations = [
  conv('c1', '求函数极值问题', iso(-8)),
  conv('c2', '导数与单调性讲解', iso(-120), { message_count: 6 }),
  conv('c3', '三角函数单调区间', iso(-200)),
  conv('c4', '数列求和错位相减', iso(-1440)),
  conv('c5', '立体几何体积', iso(-1500)),
  conv('c6', '圆锥曲线焦点', iso(-2880)),
  conv('c7', '概率古典概型', iso(-4320)),
]

/* ---------- 消息 ---------- */
const md = (content) => ({ type: 'markdown', content })
const card = (data) => ({ type: 'card', data })

const env = (blocks, meta = {}) => ({ msg_id: uuid(), meta, blocks })

/* 演示会话 c2：导数与单调性讲解（对齐 v4 对话页展示内容） */
const demoC2 = [
  {
    id: 'm1',
    role: 'user',
    clientMsgId: 'c2u1',
    createdAt: iso(-130),
    envelope: env([
      md('求函数 $f(x)=x^3-3x$ 在 $[-1,3]$ 上的最大值和最小值'),
    ]),
  },
  {
    id: 'm2',
    role: 'assistant',
    clientMsgId: 'c2u1',
    createdAt: iso(-129),
    envelope: env(
      [
        md('小婷，这道题确实需要一步步想清楚。\n\n我们先看题目要求的是什么——在闭区间 **$[-1, 3]$** 上找 **$f(x) = x^3 - 3x$** 的最大值和最小值。\n\n**你记得吗？求闭区间上函数的最值，通常会用到什么方法？**'),
      ],
      { skill: 'socratic_solver', confidence: 0.96 }
    ),
  },
  {
    id: 'm3',
    role: 'user',
    clientMsgId: 'c2u2',
    createdAt: iso(-124),
    envelope: env([md('先求导？找出极值点？')]),
  },
  {
    id: 'm4',
    role: 'assistant',
    clientMsgId: 'c2u2',
    createdAt: iso(-123),
    envelope: env(
      [
        md('对了一半！**求导 → 找极值点** 没错，但闭区间最值还有个关键步骤——**还要代入端点比较**。\n\n现在请你试试：\n① 求 $f\'(x) = ?$\n② 令 $f\'(x) = 0$，解出 $x = ?$\n③ 这些 $x$ 在不在区间 $[-1, 3]$ 内？\n\n算完告诉我，我帮你核对下一步。'),
      ],
      { skill: 'socratic_solver', confidence: 0.93 }
    ),
  },
  {
    id: 'm5',
    role: 'user',
    clientMsgId: 'c2u3',
    createdAt: iso(-118),
    envelope: env([
      md('$f\'(x) = 3x^2-3 = 0$，所以 $x = \\pm 1$，两个都在区间内'),
      card({
        type: 'quiz_set',
        chain: 'variant',
        chain_id: 'chain-c2-1',
        items: [
          {
            item_no: 1,
            q_type: 'choice',
            difficulty: 'easy',
            kp_name: '导数与单调性',
            kp_code: 'DR-02',
            variant_note: '变式 1 · 换数字',
            question_text: '你刚才提到的极值点是 $x = \\pm 1$，要找闭区间最值还需要：',
            options: ['直接用 $f(-1)$ 和 $f(1)$ 比较', '代入端点 $x = -1, 3$ 比较', '代入所有候选点（含端点）：$x = -1, 1, 3$', '不需要比较，直接选 $f(0) = 0$'],
            answer: 'C',
            answer_analysis: '闭区间最值必须比较所有候选点（含端点）。很多同学漏算端点 $x=3$，导致错。',
          },
        ],
      }),
    ]),
  },
  {
    id: 'm6',
    role: 'assistant',
    clientMsgId: 'c2u3',
    createdAt: iso(-117),
    envelope: env(
      [
        md('**你选了 C，正确！**\n\n这就是"闭区间最值"的关键——**必须比较所有候选点（含端点）**。\n\n现在我们来代入验证：\n• $f(-1) = 2$\n• $f(1) = -2$\n• $f(3) = 18$\n\n所以最大值 = **18**，最小值 = **-2**。你做出来后告诉我，下一步帮你做变式巩固。'),
      ],
      { skill: 'socratic_solver', confidence: 0.97 }
    ),
  },
]

const simpleConv = (id, q, a) => [
  { id: `${id}q`, role: 'user', clientMsgId: `${id}u`, createdAt: iso(-10), envelope: env([md(q)]) },
  { id: `${id}a`, role: 'assistant', clientMsgId: `${id}u`, createdAt: iso(-9), envelope: env([md(a)], { skill: 'socratic_solver' }) },
]

export const seedMessages = {
  c2: demoC2,
  c1: simpleConv('c1', '求函数 $f(x)=x^3-3x+1$ 的极值', '先求导 $f\'(x)=3x^2-3$，令其为零得 $x=\\pm 1$。判断两侧符号即可得出极大值点 $x=-1$（$f(-1)=3$）、极小值点 $x=1$（$f(1)=-1$）。'),
  c3: simpleConv('c3', '求 $y=2\\sin(2x+\\frac{\\pi}{3})$ 的单调递增区间', '由 $-\\frac{\\pi}{2}+2k\\pi \\le 2x+\\frac{\\pi}{3} \\le \\frac{\\pi}{2}+2k\\pi$ 解得 $x \\in [-\\frac{5\\pi}{12}+k\\pi, \\frac{\\pi}{12}+k\\pi]$。'),
  c4: simpleConv('c4', '数列 $a_n=(2n-1)\\cdot 3^n$ 求和', '用错位相减法：$S_n = 3 + 3\\cdot3^2+\\dots$，两边乘 3 后相减，得到 $S_n = 3 + (n-1)3^{n+1}$。'),
  c5: simpleConv('c5', '三棱锥体积怎么算？', '关键找底面和高：$V = \\frac{1}{3}Sh$。先选定底面求面积，再找顶点到底面的距离（常需建系或等体积法）。'),
  c6: simpleConv('c6', '椭圆焦点坐标怎么求？', '对 $\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$（$a>b$），$c=\\sqrt{a^2-b^2}$，焦点为 $(\\pm c, 0)$。'),
  c7: simpleConv('c7', '古典概型概率公式', '$P(A)=\\frac{事件A包含的基本事件数}{总基本事件数}$。注意先算清总数，避免重复/遗漏。'),
}

/* 复习计划（新会话弹出「复习时间到」提醒） */
export const reviewPlan = {
  due_today: 2,
  due_items: [
    { record_id: 'r1', question_text: '求函数 $f(x)=x^3-3x$ 在 $[-1,3]$ 上的最大值和最小值', kp_name: '导数与极值' },
    { record_id: 'r2', question_text: '判断函数 $f(x)=x^3-3x$ 的单调区间', kp_name: '导数与函数单调性' },
  ],
}

/* 学生端静态学情（供对话页/复习提醒之外的原组件兜底） */
export const masterySummary = {
  total_score: 67,
  delta: 9,
  last_week: 58,
  target: 75,
  independent_rate: 0.52,
  streak: 7,
  weak_points: [{ kp_name: '导数与函数单调性', mastery: 0.12 }],
}

export const labRecommend = {
  daily_question: { question_text: '求函数 $f(x)=x^3-3x^2+2$ 在 $[0,4]$ 上的最值', kp_name: '导数与极值' },
  weak_top1: { kp_name: '导数与函数单调性', mastery: 0.12 },
  pending_review: 3,
  streak: 7,
  exams: [{ id: 'e1', title: '2024 · 新课标 I 卷' }],
}

export const knowledgeGraph = {
  chapters: [
    { id: 'ch1', name: '函数的概念与基本初等函数' },
    { id: 'ch2', name: '导数及其应用' },
    { id: 'ch3', name: '三角函数' },
  ],
  nodes: [
    { kp_code: 'HS-01', kp_name: '函数的概念与表示', mastery: 0.85 },
    { kp_code: 'HS-02', kp_name: '函数的单调性', mastery: 0.72 },
    { kp_code: 'DR-01', kp_name: '导数概念与几何意义', mastery: 0.45 },
    { kp_code: 'DR-02', kp_name: '导数与函数单调性', mastery: 0.12 },
  ],
}

/* ================= M2 迭代16 · 学情聚合接口 mock（对齐改造方案 §3） =================
 * 人设与上方 masterySummary 自洽：综合分 67（上周 58 / 目标 75）、连击 7 天、
 * 薄弱 Top1 = 导数与函数单调性（DR-02，mastery 0.12）、错题 14 道（3 道今日到期）。
 * 日期一律写死，保证确定性（禁止 Math.random/Date.now 进响应）。
 */

/* ---------- 模块0 全局基础能力 ---------- */

/* GET /student/growth/overview —— 用户全域学情聚合 */
export const growthOverview = {
  composite_score: 67,
  score_delta_week: 9,
  last_week_score: 58,
  target_score: 75,
  independent_rate: 0.52,
  streak_days: 7,
  error_total: 14,
  error_due_count: 3,
  mastered_kp_count: 12,
  total_kp_count: 52,
  week_answer_count: 30,
  week_correct_count: 23,
  /* 迭代17 §1-D 超集字段：AI 管家问候语（时段 + 真实数据） */
  greeting: '晚上好，小婷！你的综合分 67，比上周进步 9 分，连击 7 天节奏很稳，今天也一起进步吧。',
}

/* GET /student/growth/panel —— 右侧全局面板 */
export const growthPanel = {
  golden_window: { start: '21:00', end: '21:50', label: '薄弱学科攻坚 · 精力峰值' },
  today_actions: [
    { key: 'review_errors', title: '复习 3 道错题', count: 3, route: '/errors' },
    { key: 'variant_drill', title: '导数变式训练', count: 5, route: '/practice' },
    { key: 'final_challenge', title: '压轴挑战', count: 1, route: '/practice' },
    { key: 'teacher_homework', title: '老师作业', count: 2, route: '/tasks' },
  ],
  week_brief: { independent_rate: 0.52, score_delta: 9, streak_days: 7 },
  encouragement: '小婷，这周你做对了 23 道导数题（上周才 14 道）——进步不是"某天突然会了"，是每天多做 1.3 道。明天继续保持，你能在月底前把"压轴第二问"突破 50%。',
  gaokao_countdown: { days: 297, exam_date: '2027-06-07' },
}

/* POST /agent/route-intent —— 功能跳转意图识别（关键词规则表，未命中走 miss） */
export const routeIntentReply = {
  rules: [
    { keywords: ['错题', '复习'], route: '/errors', route_name: '错题本', query: {}, reply: '好的，带你去错题本，今天有 3 道到期。' },
    { keywords: ['练题', '刷题', '训练', '出题', '变式'], route: '/practice', route_name: '练题中心', query: { kp: '导数' }, reply: '好，给你安排导数与单调性的 5 题变式训练，约 20 分钟。' },
    { keywords: ['报告', '学情', '分析', '最弱', '薄弱', '哪部分'], route: '/report', route_name: '学情报告', query: {}, reply: '这周你的薄弱 Top1 是导数与函数单调性（12%），详细分析在报告页。', data_hint: { weak_top1: '导数与函数单调性', mastery: 0.12, composite_score: 67 } },
    { keywords: ['图谱', '版图', '知识点', '考点'], route: '/graph', route_name: '知识图谱', query: {}, reply: '你的学习版图已掌握 12/52，3 个临危考点在图谱里标红了。' },
    { keywords: ['模考', '真题', '考试', '套卷'], route: '/exam', route_name: '模拟考试', query: {}, reply: '推荐先做 2024 · 新课标 I 卷，限时 120 分钟，做完自动判分。' },
    { keywords: ['作业', '任务'], route: '/tasks', route_name: '课堂任务', query: {}, reply: '王老师布置的 2 项作业还没完成，带你去任务中心。' },
    { keywords: ['班级', '同学'], route: '/class', route_name: '我的班级', query: {}, reply: '高二（3）班本周活跃 42/48，去看看班级动态吧。' },
    { keywords: ['总览', '首页', '今天学什么'], route: '/overview', route_name: '学情总览', query: {}, reply: '今天 3 件事已为你排好：复习错题、导数变式、压轴挑战。' },
  ],
  miss: { matched: false, route: null, route_name: '', query: {}, reply: '' },
}

/* ---------- 模块1/6 对话学习闭环 ---------- */

/* GET /student/growth/loop-progress —— 当日对话 7 步闭环进度 */
export const loopProgress = {
  steps: [
    { key: 'encounter', done: true, count: 1 },
    { key: 'socratic', done: true, count: 4 },
    { key: 'answer', done: true, count: 3 },
    { key: 'judge', done: true, count: 3 },
    { key: 'variant', done: true, count: 2 },
    { key: 'record', done: false, count: 0 },
    { key: 'action', done: false, count: 0 },
  ],
  done_count: 5,
  total: 7,
}

/* ---------- 模块2 练题中心 ---------- */

/* GET /student/practice/group-recommend —— 今日训练组推荐 */
export const practiceGroupRecommend = {
  title: '导数与单调性 · 5 题变式（做完离开红区）',
  kp_code: 'DR-02',
  kp_name: '导数与函数单调性',
  count: 5,
  est_minutes: 20,
  mix: { easy: 3, medium: 2, hard: 1 },
  mastery_now: 0.12,
  mastery_forecast: 0.25,
  reason: '这是你当前最薄弱的知识点（掌握度 12%）。近 21 天你在该考点错 7 道，按 3:2:1 难度配比训练，预计完成后掌握度提升到 25%，脱离红区。',
}

/* GET /student/practice/difficulty-mix —— 难度配比统计 */
export const difficultyMix = {
  items: [
    { level: 'easy', count: 3, ratio: 0.6 },
    { level: 'medium', count: 2, ratio: 0.4 },
    { level: 'hard', count: 1, ratio: 0.2 },
  ],
  explanation: '近 21 天你在易题上正确率 78%、中档题 41%、难题 12%。按"踮踮脚够得着"原则，给出 3:2:1 配比：易题保底信心，中档题主攻突破，1 道难题拉伸上限。',
}

/* GET /student/practice/smart-score —— SmartScore */
export const smartScore = {
  smart_score: 62,
  breakdown: { accuracy: 0.7, speed: 0.6, independence: 0.52 },
}

/* GET /student/practice/summary —— 训练总结 */
export const practiceSummary = {
  upgraded: [
    { kp_code: 'DR-02', kp_name: '导数与函数单调性', from: 0.12, to: 0.25 },
    { kp_code: 'DR-01', kp_name: '导数概念与几何意义', from: 0.45, to: 0.52 },
  ],
  flat: [
    { kp_code: 'HS-02', kp_name: '函数的单调性', from: 0.72, to: 0.74 },
  ],
  downgraded: [],
  recommendation: '导数与单调性已离开红区边缘，但还不稳。建议明天再练 3 道中档变式巩固，并复习今天做错的 1 道端点漏判题。',
}

/* ---------- 模块3 错题本 ---------- */

/* GET /student/error-records/memory-heatmap —— FSRS 记忆稳定性热力图（4 周 × 7 天，确定性生成） */
const HEAT_LEVELS = [
  'lv4', 'lv4', 'lv3', 'lv2', 'lv3', 'lv4', 'empty',
  'lv3', 'lv2', 'decay', 'lv3', 'lv2', 'lv4', 'lv3',
  'lv2', 'lv1', 'lv3', 'decay', 'lv2', 'lv3', 'lv2',
  'decay', 'lv1', 'lv2', 'lv3', 'lv1', 'empty', 'lv4',
]
export const memoryHeatmap = {
  weeks: 4,
  days: 7,
  cells: HEAT_LEVELS.map((level, i) => ({
    week: Math.floor(i / 7),
    weekday: i % 7,
    level,
    record_ids: level === 'empty' ? [] : [`r${i + 1}`],
    count: level === 'empty' ? 0 : 1,
  })),
}

/* GET /student/error-records/due-queue —— 到期错题队列（retrievability 升序） */
export const dueQueue = {
  total: 3,
  items: [
    {
      record_id: 'r1', seq: 1,
      question_preview: '求函数 $f(x)=x^3-3x$ 在 $[-1,3]$ 上的最大值和最小值',
      kp_code: 'DR-02', kp_name: '导数与函数单调性',
      created_at: '2026-08-06T21:12:00Z', wrong_count: 2, review_count: 1,
      retrievability: 0.58, hours_to_forget: 3, urgency_label: '即将遗忘',
    },
    {
      record_id: 'r2', seq: 2,
      question_preview: '判断函数 $f(x)=x^3-3x$ 的单调区间',
      kp_code: 'DR-02', kp_name: '导数与函数单调性',
      created_at: '2026-08-05T20:40:00Z', wrong_count: 1, review_count: 1,
      retrievability: 0.64, hours_to_forget: 9, urgency_label: '今日必复习',
    },
    {
      record_id: 'r3', seq: 3,
      question_preview: '求 $y=2\\sin(2x+\\frac{\\pi}{3})$ 的单调递增区间',
      kp_code: 'TG-02', kp_name: '三角函数单调性',
      created_at: '2026-08-03T19:55:00Z', wrong_count: 1, review_count: 2,
      retrievability: 0.71, hours_to_forget: 21, urgency_label: '建议今日完成',
    },
  ],
}

/* GeoGebra 交互构造（mock：抛物线开口参数演示，可拖滑块/点动点） */
export const mockGgb = {
  type: 'ggb',
  view: '2d',
  caption: '抛物线开口参数演示',
  commands: [
    '# perspective: 2d',
    '# view: -6 -3 6 4',
    'a=Slider(0.3,2,0.1)',
    'f(x)=a*x^2',
    'P=Point(f)',
    'SetColor(f,40,60,120)',
    'SetLineThickness(f,4)',
    'SetCaption(a,"开口系数 a")',
    'ShowLabel(P,true)',
  ],
}

/* GET /student/error-records/{record_id}/detail —— 错题详情扩展（原有字段 + FSRS 字段） */
export const errorDetailExt = {
  record_id: 'r1',
  question_text: '求函数 $f(x)=x^3-3x$ 在 $[-1,3]$ 上的最大值和最小值',
  kp_code: 'DR-02',
  kp_name: '导数与函数单调性',
  error_type: 'logic',
  error_type_zh: '逻辑疏漏',
  my_answer: '最大值为 $f(-1)=2$，最小值为 $f(1)=-2$',
  correct_answer: '最大值 $f(3)=18$，最小值 $f(1)=-2$',
  analysis: '漏判区间端点 x=3：闭区间最值必须比较所有候选点（含端点）。',
  created_at: '2026-08-06T21:12:00Z',
  entered_at: '2026-08-06T21:14:00Z',
  wrong_count: 2,
  review_count: 1,
  memory_stability: 4.2,
  retrievability: 0.58,
  fsrs_level: 'decay',
  variants_hint: '建议出 2 道变式：① 换数字（改三次函数系数）② 换条件（改开区间，讨论端点取不到的情况）。',
  image: [mockGgb],
}

/* GET /student/error-records/filter —— 多维筛选（分页） */
export const errorFilter = {
  items: [
    { record_id: 'r1', question_preview: '求函数 $f(x)=x^3-3x$ 在 $[-1,3]$ 上的最大值和最小值', kp_code: 'DR-02', kp_name: '导数与函数单调性', error_type: 'logic', stability: 'critical', created_at: '2026-08-06T21:12:00Z' },
    { record_id: 'r2', question_preview: '判断函数 $f(x)=x^3-3x$ 的单调区间', kp_code: 'DR-02', kp_name: '导数与函数单调性', error_type: 'concept', stability: 'decaying', created_at: '2026-08-05T20:40:00Z' },
    { record_id: 'r3', question_preview: '求 $y=2\\sin(2x+\\frac{\\pi}{3})$ 的单调递增区间', kp_code: 'TG-02', kp_name: '三角函数单调性', error_type: 'calculation', stability: 'stable', created_at: '2026-08-03T19:55:00Z' },
  ],
  total: 14,
  page: 1,
  size: 20,
}

/* ---------- 模块4 学情报告 ---------- */

/* GET /student/report/highlights —— 本周亮点（3 项） */
export const reportHighlights = {
  items: [
    { icon: '⚡', title: '数列求和 突破', desc: '"错位相减法"正确率从 22% → 78%，是本周掌握度提升最大的知识点。' },
    { icon: '📚', title: '独立解题率 提升', desc: '从 35% → 52%，超过班级平均 47%，看提示的次数明显变少了。' },
    { icon: '🔥', title: '7 天连击 不间断', desc: '平均每天学习 38 分钟，错题入本率 100%。' },
  ],
}

/* GET /student/report/weak-points —— 薄弱环节 Top4 */
export const reportWeakPoints = {
  items: [
    {
      kp_code: 'DR-02', kp_name: '导数与函数单调性', mastery: 0.12, level: 'err',
      ai_reason: '7 道错题中 5 道错因是"逻辑疏漏"——反复漏判闭区间端点，属于步骤完整性问题而非不会求导。',
      primary_action: { label: '练 5 题变式', route: '/practice', minutes: 20 },
      secondary_action: { label: '看错题归因', route: '/errors' },
    },
    {
      kp_code: 'DR-01', kp_name: '导数概念与几何意义', mastery: 0.45, level: 'warn',
      ai_reason: '切线方程类题目常把"在某点"与"过某点"混淆，属于概念辨析不清。',
      primary_action: { label: '巩固 3 题', route: '/practice', minutes: 12 },
      secondary_action: { label: '对话讲解', route: '/dialog' },
    },
    {
      kp_code: 'TG-02', kp_name: '三角函数单调性', mastery: 0.38, level: 'warn',
      ai_reason: '含参单调区间讨论时漏掉 k 的取值范围，计算类错因占多数。',
      primary_action: { label: '专项 15 分钟', route: '/practice', minutes: 15 },
      secondary_action: { label: '复习到期错题', route: '/errors' },
    },
    {
      kp_code: 'HS-02', kp_name: '函数的单调性', mastery: 0.72, level: 'ok',
      ai_reason: '整体稳定，仅抽象函数单调性证明偶有卡壳，保持现有节奏即可。',
      primary_action: { label: '保持每日 1 题', route: '/practice', minutes: 8 },
      secondary_action: { label: '查看图谱', route: '/graph' },
    },
  ],
}

/* GET /student/report/mastery-trend-forecast —— 14 天趋势 + 遗忘预测（7 天虚线） */
export const masteryTrendForecast = {
  history: [
    { date: '2026-07-31', mastery: 0.41 },
    { date: '2026-08-01', mastery: 0.42 },
    { date: '2026-08-02', mastery: 0.43 },
    { date: '2026-08-03', mastery: 0.44 },
    { date: '2026-08-04', mastery: 0.45 },
    { date: '2026-08-05', mastery: 0.46 },
    { date: '2026-08-06', mastery: 0.48 },
    { date: '2026-08-07', mastery: 0.49 },
    { date: '2026-08-08', mastery: 0.5 },
    { date: '2026-08-09', mastery: 0.51 },
    { date: '2026-08-10', mastery: 0.52 },
    { date: '2026-08-11', mastery: 0.53 },
    { date: '2026-08-12', mastery: 0.54 },
    { date: '2026-08-13', mastery: 0.55 },
  ],
  forecast: [
    { date: '2026-08-14', mastery: 0.53 },
    { date: '2026-08-15', mastery: 0.52 },
    { date: '2026-08-16', mastery: 0.5 },
    { date: '2026-08-17', mastery: 0.49 },
    { date: '2026-08-18', mastery: 0.48 },
    { date: '2026-08-19', mastery: 0.47 },
    { date: '2026-08-20', mastery: 0.46 },
  ],
}

/* GET /student/report/error-distribution —— 12 类思维漏洞分布（按 count 降序） */
export const errorDistribution = {
  items: [
    { type: 'step_omission', type_zh: '步骤遗漏', count: 5, ratio: 0.36, parent_type: 'logic' },
    { type: 'concept_confusion', type_zh: '概念混淆', count: 3, ratio: 0.21, parent_type: 'concept' },
    { type: 'sign_error', type_zh: '符号错误', count: 2, ratio: 0.14, parent_type: 'calculation' },
    { type: 'formula_misuse', type_zh: '公式误用', count: 2, ratio: 0.14, parent_type: 'formula' },
    { type: 'condition_misread', type_zh: '条件漏读', count: 1, ratio: 0.07, parent_type: 'reading' },
    { type: 'range_ignored', type_zh: '定义域忽略', count: 1, ratio: 0.07, parent_type: 'concept' },
  ],
  total: 14,
}

/* GET /student/report/honesty —— 诚实提示 */
export const reportHonesty = {
  hint_count_week: 11,
  independent_rate_now: 0.52,
  independent_rate_prev: 0.35,
  fluctuation: 0.17,
  message: '本周你看了 11 次提示，独立解题率从 35% 提升到 52%——这是真实进步，不是"看答案看会的"。',
  suggestion: '下周试着在点"看提示"前先写 2 分钟草稿，哪怕只写半行思路，独立解题率还能再涨一截。',
}

/* ---------- 模块5 知识图谱 ---------- */

/* GET /student/knowledge-graph/pie —— ALEKS 学习版图 */
export const kgPie = {
  total: 52,
  mastered: { count: 12, ratio: 0.23 },
  consolidating: { count: 5, ratio: 0.1 },
  critical: { count: 3, ratio: 0.06 },
  unlearned: { count: 32, ratio: 0.61 },
  center_text: '已掌握 12 / 52',
  eta: { to_50pct_weeks: 6, to_80pct_weeks: 18 },
}

/* GET /student/knowledge-graph/tree —— 章节树形视图 */
export const kgTree = {
  chapters: [
    {
      chap: '第 1 章', title: '函数的概念与基本初等函数', count_text: '2 / 6 已掌握',
      nodes: [
        { kp_code: 'HS-01', name: '函数的概念与表示', mastery: 0.85, state: 'mastered', shape: 'circle' },
        { kp_code: 'HS-02', name: '函数的单调性', mastery: 0.72, state: 'mastered', shape: 'diamond' },
        { kp_code: 'HS-03', name: '函数的奇偶性', mastery: 0.56, state: 'improving', shape: 'circle' },
        { kp_code: 'HS-04', name: '指数与指数函数', mastery: 0, state: 'unlearned', shape: 'circle' },
      ],
    },
    {
      chap: '第 2 章', title: '导数及其应用', count_text: '0 / 8 已掌握',
      nodes: [
        { kp_code: 'DR-01', name: '导数概念与几何意义', mastery: 0.45, state: 'improving', shape: 'diamond' },
        { kp_code: 'DR-02', name: '导数与函数单调性', mastery: 0.12, state: 'weak', shape: 'diamond' },
        { kp_code: 'DR-03', name: '导数与极值最值', mastery: 0.3, state: 'weak', shape: 'hex' },
        { kp_code: 'DR-06', name: '导数与不等式综合', mastery: 0, state: 'unlearned', shape: 'hex' },
      ],
    },
    {
      chap: '第 3 章', title: '三角函数', count_text: '1 / 7 已掌握',
      nodes: [
        { kp_code: 'TG-01', name: '任意角与弧度制', mastery: 0.78, state: 'mastered', shape: 'circle' },
        { kp_code: 'TG-02', name: '三角函数单调性', mastery: 0.38, state: 'weak', shape: 'diamond' },
        { kp_code: 'TG-03', name: '三角恒等变换', mastery: 0.62, state: 'improving', shape: 'hex' },
      ],
    },
  ],
}

/* GET /student/knowledge-graph/nodes/{kp_code}/deps —— 追根溯源依赖链（反向 BFS ≤4） */
export const kgNodeDeps = {
  kp_code: 'DR-02',
  chain: [
    { kp_code: 'DR-02', kp_name: '导数与函数单调性', mastery: 0.12, state: 'weak' },
    { kp_code: 'DR-01', kp_name: '导数概念与几何意义', mastery: 0.45, state: 'improving' },
    { kp_code: 'HS-02', kp_name: '函数的单调性', mastery: 0.72, state: 'mastered' },
    { kp_code: 'HS-01', kp_name: '函数的概念与表示', mastery: 0.85, state: 'mastered' },
  ],
  weakest_prereq: 'DR-01',
}

/* GET /student/knowledge-graph/nodes/{kp_code}/recommend —— 节点学习推荐 */
export const kgNodeRecommend = {
  strategy: 'prereq_first',
  reason: '前置"导数概念与几何意义"掌握度仅 45%（<0.7），直接练单调性容易卡在切线/定义辨析上，先把前置补到 0.7 再主攻本节点。',
  action_label: '先补前置 · 导数概念 3 题',
  route: '/practice',
  minutes: 15,
}

/* ---------- 模块6 学情总览 ---------- */

/* GET /student/growth/today-3 —— 今日 3 件事（组1 主推 / 组2 备选） */
export const today3 = {
  groups: [
    [
      { key: 'review_due', title: '复习 3 道到期错题', why: 'FSRS 显示这 3 道记忆即将衰减到 60% 以下，今天不复习就会忘。', est_minutes: 15, benefit: '保住已学成果', route: '/errors', done: false },
      { key: 'weak_variant', title: '导数与单调性 5 题变式', why: '薄弱 Top1（12%），做完预计提升到 25%，脱离红区。', est_minutes: 20, benefit: '薄弱点突破', route: '/practice', done: false },
      { key: 'final_challenge', title: '压轴挑战 1 题', why: '函数与导数综合大题第二问，拉伸上限。', est_minutes: 12, benefit: '冲刺高分段', route: '/practice', done: false },
    ],
    [
      { key: 'weak_second', title: '三角函数单调性 3 题', why: '次薄弱点（38%），错因多为符号计算。', est_minutes: 12, benefit: '巩固次薄弱', route: '/practice', done: false },
      { key: 'review_next', title: '预习明日到期 2 道', why: '明天有 2 道错题进入复习窗口，提前做不堆积。', est_minutes: 10, benefit: '减轻明日负担', route: '/errors', done: false },
      { key: 'teacher_homework', title: '完成王老师作业', why: '本周作业截止明晚 22:00，已完成 1/2。', est_minutes: 25, benefit: '跟上班级进度', route: '/tasks', done: false },
    ],
  ],
}

/* GET /student/growth/score-trend —— 综合分 7 天柱状图 */
export const scoreTrend = {
  score: 67,
  delta_week: 9,
  target: 75,
  daily: [
    { date: '2026-08-07', score: 58 },
    { date: '2026-08-08', score: 60 },
    { date: '2026-08-09', score: 61 },
    { date: '2026-08-10', score: 63 },
    { date: '2026-08-11', score: 64 },
    { date: '2026-08-12', score: 66 },
    { date: '2026-08-13', score: 67 },
  ],
}

/* GET /student/growth/feature-entries —— 功能入口聚合计数 */
export const featureEntries = {
  entries: [
    { key: 'errors', title: '错题本', stat_text: '14 道 · 3 道今日到期', badge: '3', route: '/errors' },
    { key: 'practice', title: '练题中心', stat_text: '今日推荐 5 题导数变式', badge: '5', route: '/practice' },
    { key: 'report', title: '学情报告', stat_text: '综合分 67 · 周环比 +9', badge: '', route: '/report' },
    { key: 'graph', title: '知识图谱', stat_text: '已掌握 12 / 52 · 3 临危', badge: '', route: '/graph' },
    { key: 'exam', title: '模拟考试', stat_text: '2024 新课标 I 卷待做', badge: '新', route: '/exam' },
    { key: 'tasks', title: '课堂任务', stat_text: '2 项作业待完成', badge: '2', route: '/tasks' },
  ],
}

/* ---------- 模块7 扩展页面（迭代16 第二批） ---------- */

/* GET /classes/{class_id}/feed —— 班级动态（近 14 天，倒序 ≤20 条） */
export const classFeed = {
  days: 14,
  items: [
    { kind: 'practice', actor_name: '王浩', text: '王浩 完成了一组练习，得分 80', created_at: iso(-60) },
    { kind: 'event', event: 'review_done', actor_name: '李思颖', text: '李思颖 完成了错题复习', created_at: iso(-180) },
    { kind: 'event', event: 'exam_submit', actor_name: '张一凡', text: '张一凡 完成了一次模拟考试', created_at: iso(-1500) },
    { kind: 'practice', actor_name: '陈雨桐', text: '陈雨桐 完成了一组练习，得分 92', created_at: iso(-1560) },
    { kind: 'member_join', actor_name: '赵新宇', text: '赵新宇 加入了班级', created_at: iso(-2880) },
  ],
}

/* GET /classes/{class_id}/hot-errors —— 班级高频错题 Top N（按 kp 聚合） */
export const classHotErrors = {
  days: 30,
  items: [
    { kp_code: 'DR-02', kp_name: '导数与函数单调性', error_count: 23, member_count: 15, top_error_type: 'logic' },
    { kp_code: 'TG-02', kp_name: '三角函数单调性', error_count: 14, member_count: 9, top_error_type: 'calculation' },
    { kp_code: 'HS-03', kp_name: '函数的奇偶性', error_count: 9, member_count: 7, top_error_type: 'concept' },
    { kp_code: 'DR-03', kp_name: '导数与极值最值', error_count: 7, member_count: 5, top_error_type: 'reading' },
    { kp_code: 'TG-03', kp_name: '三角恒等变换', error_count: 5, member_count: 4, top_error_type: 'formula' },
  ],
}

/* 资源中心：B 站视频（GET /student/videos、POST /student/videos/recommend）
   前端 mock 只用于离线演示，字段与后端 /api/student/videos 契约完全一致 */
export const videoTopics = [
  { key: 'featured', label: '精选推荐', icon: '⭐', desc: '一数系统课精选，从零打基础' },
  { key: 'function', label: '函数与导数', icon: '📈', desc: '单调性、极值、零点与导数应用' },
  { key: 'trig', label: '三角函数', icon: '🌊', desc: '图像变换、恒等变换与解三角形' },
  { key: 'sequence', label: '数列', icon: '🔢', desc: '等差等比、通项求法与数列不等式' },
  { key: 'solid', label: '立体几何', icon: '🧊', desc: '平行垂直证明、空间角与向量法' },
  { key: 'analytic', label: '解析几何', icon: '🎯', desc: '圆锥曲线、直线与曲线联立' },
  { key: 'prob', label: '概率统计', icon: '🎲', desc: '排列组合、分布列与统计案例' },
  { key: 'inequality', label: '集合与不等式', icon: '⚖️', desc: '基本不等式、恒成立与参数讨论' },
  { key: 'review', label: '高考总复习', icon: '🔥', desc: '一轮复习与高频题型串讲' },
]

export const videoItems = [
  {
    bvid: 'BV147411K7xu',
    title: '高中数学基础与解法全集（涵盖所有）|从零开始拯救所有学渣！',
    author: '一数',
    cover: 'https://i0.hdslb.com/bfs/archive/8c963f3eef8a75aef37807da987b2b6805863821.jpg',
    duration: '176:02:25',
    duration_sec: 633745,
    play: 243613266,
    match_reason: '体系化打基础，适合从零补起',
    url: 'https://www.bilibili.com/video/BV147411K7xu',
  },
  {
    bvid: 'BV1Ff4y1d7i7',
    title: '你凭本事刷到了这个视频！【高中数学】从零开始1.3小时掌握导数全部知识点',
    author: '一数',
    cover: 'https://i1.hdslb.com/bfs/archive/5f91184839297306881094e0857c39d59d8a03c7.jpg',
    duration: '83:47',
    duration_sec: 5027,
    play: 8335765,
    match_reason: '导数与单调性体系串讲，覆盖值域最值',
    url: 'https://www.bilibili.com/video/BV1Ff4y1d7i7',
  },
  {
    bvid: 'BV1VD4y1D7UB',
    title: '2027高考数学必刷100讲【常规版】',
    author: '一数',
    cover: 'https://i1.hdslb.com/bfs/archive/6a51c78459df3bc46fec46cb0e3e02614a638203.jpg',
    duration: '169:05:57',
    duration_sec: 608757,
    play: 36057123,
    match_reason: '按题型过一遍高频考法',
    url: 'https://www.bilibili.com/video/BV1VD4y1D7UB',
  },
  {
    bvid: 'BV1E4c4ePEJT',
    title: '高考必考「数列求通项」方法大总结！',
    author: '一数',
    cover: 'https://i1.hdslb.com/bfs/archive/5e7e00e9be2f3078c29c0142db993d956c5d77a3.jpg',
    duration: '46:11',
    duration_sec: 2771,
    play: 5487153,
    match_reason: '通项与求和方法归纳',
    url: 'https://www.bilibili.com/video/BV1E4c4ePEJT',
  },
]

export const videoBrowseMock = { topics: videoTopics, items: videoItems, source: 'bilibili', live: true, page: 1, page_size: 12 }
export const videoRecommendMock = {
  kp_name: '一元二次函数值域与最值',
  items: videoItems,
  source: 'xingchen',
  unavailable: false,
  notice: '',
  confidence: 0.95,
}

/* GET /student/assignments —— 课堂任务（真实形态：含 progress/overdue/deadline） */
export const assignmentsList = {
  total: 3,
  items: [
    {
      assignment_id: 'asg_mock_01',
      title: '导数与单调性 10 题限时练',
      type: 'quiz',
      deadline: iso(2880),
      status: 'published',
      progress: { done: 4, total: 10 },
      overdue: false,
    },
    {
      assignment_id: 'asg_mock_02',
      title: '观看《三角恒等变换》双师课堂回放',
      type: 'watch',
      deadline: iso(-1440),
      status: 'published',
      progress: { done: 0, total: 1 },
      overdue: true,
    },
    {
      assignment_id: 'asg_mock_03',
      title: '函数奇偶性单元小测',
      type: 'quiz',
      deadline: iso(7200),
      status: 'published',
      progress: { done: 0, total: 8 },
      overdue: false,
    },
  ],
}
