/**
 * 教师工作台 V3 · Mock 服务（挂载 /api/teacher-v3/*）
 * 契约依据：src/types/teacherV3.ts + src/api/teacherV3.ts
 * 域划分与四个后端智能体对齐：catalog / recognition(SSE) / generation(SSE) / figures / grading
 * 深链路：
 *  - photo-ingest SSE：meta → photo → block×n → paginate → done（识别块全部 editable，教师可改）
 *  - generation deck SSE：meta → outline → slide×n → done（AI 只出草稿，teacher_confirmed=false）
 */
import {
  V3_CLASSES, V3_DECKS, V3_DECK_TEMPLATES, V3_DRAW_LIBRARY, V3_GRADING_ASSIGNMENTS, V3_GRADING_DATA, V3_INSIGHTS,
  V3_LESSON_PLANS, V3_LESSON_TEMPLATES, V3_QUIZ_QUESTIONS, V3_RECOGNIZE_BLOCKS, V3_RECIPES,
  V3_RESOURCES, V3_TASKS, V3_TEN_BOARDS, V3_TODAY, V3_QUIZ_KP_TREE, V3_FOLDERS, V3_TEXTBOOK_CHAPTERS, buildRebuiltTextbook,
} from './teacherV3Data'
// 相对路径导入（非 @/ 别名）：本文件被 vite.config.js 的加载链引入，别名在 config 打包阶段不可解析
import { FIGURE_PRESETS } from '../components/mathx/presets'
import type { V3Deck, V3FigureLibraryItem, V3LessonPlan, V3Slide, V3Task, V3LessonTemplate } from '@/types/teacherV3'

function readBody(req: any): Promise<any> {
  return new Promise((resolve) => {
    let buf = ''
    req.on('data', (c: any) => { buf += c })
    req.on('end', () => { try { resolve(buf ? JSON.parse(buf) : {}) } catch { resolve({}) } })
  })
}
function ok(res: any, data: unknown) {
  if (res.headersSent || res.writableEnded) return
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code: 0, message: 'ok', data }))
}
function sendSse(res: any, event: string, data: unknown) {
  if (res.writableEnded || res.destroyed) return
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}
function startSse(res: any) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  res.write('retry: 3000\n\n')
}
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))
const isTeacher = (req: any) => (req.headers?.authorization || '').includes('mock-token-teacher-preview')
function fail(res: any, status: number, code: number, message: string) {
  if (res.headersSent || res.writableEnded) return
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code, message, data: null }))
}

/* ================= 内存态（页面间共享，模拟后端持久化） ================= */
const decks: V3Deck[] = JSON.parse(JSON.stringify(V3_DECKS))
const plans: V3LessonPlan[] = JSON.parse(JSON.stringify(V3_LESSON_PLANS))
const tasks: V3Task[] = [...V3_TASKS]
const recipes = [...V3_RECIPES]
const grading = JSON.parse(JSON.stringify(V3_GRADING_DATA))
/* B4：作业发布内存态（示例作业 + 教师新发布的实例）；类型层不扩 shared types（IFC-PRODUCT-03 登记中） */
const steps = (arr: [string, 'ok' | 'ai-flag' | 'corrected'][]) => arr.map(([latex, status]) => ({ latex, status }))
const assignments: any[] = JSON.parse(JSON.stringify(V3_GRADING_ASSIGNMENTS))
const publishedDetails: Record<string, any> = {}
const drawLibrary: V3FigureLibraryItem[] = JSON.parse(JSON.stringify(V3_DRAW_LIBRARY))
const quizQuestions = JSON.parse(JSON.stringify(V3_QUIZ_QUESTIONS))
const folders = JSON.parse(JSON.stringify(V3_FOLDERS)) as { id: string; name: string; desc?: string; count: number; updated_at: string }[]
const kpTree = JSON.parse(JSON.stringify(V3_QUIZ_KP_TREE)) as any[]
const lessonTemplates: V3LessonTemplate[] = JSON.parse(JSON.stringify(V3_LESSON_TEMPLATES))

let _id = 100
const uid = (p: string) => `${p}-${++_id}`

/** 专题夹实时计数（引用式：数 question.folder_refs） */
function folderCount(id: string) { return quizQuestions.filter((q: any) => (q.folder_refs || []).includes(id)).length }
function refreshFolderCounts() { for (const f of folders) f.count = folderCount(f.id) }

/** 知识点树：按 id 查节点 / 递归删除（V3.2 树编辑） */
function findKp(nodes: any[], id: string): any {
  for (const n of nodes) {
    if (n.id === id) return n
    const hit = n.children?.length ? findKp(n.children, id) : null
    if (hit) return hit
  }
  return null
}
function removeKp(nodes: any[], id: string): boolean {
  const i = nodes.findIndex((n) => n.id === id)
  if (i >= 0) { nodes.splice(i, 1); return true }
  for (const n of nodes) if (n.children?.length && removeKp(n.children, id)) return true
  return false
}

/**
 * 中文数学口语 → LaTeX（mock 规则层演示；real = 规则 → LLM → KaTeX 编译验证三层）。
 * 覆盖高中高频结构：根号下 / n次方 / 分之 / 加减 / 绝对值 / 三角对数。
 * 仅做确定性替换供原型演示，完整语义解析由后端 P1 实现。
 */
function voiceToLatex(spoken: string): string {
  let s = String(spoken || '').trim()
  // 句式引导词
  s = s.replace(/^(帮我|请|把|转换成|转成|输入|说出|说)(可编辑)?(的)?(公式)?/g, '')
  s = s.replace(/(转换|转)成(可编辑)?(的)?公式$/g, '')
  // "x分之y"（分母在前）→ \frac{y}{x}
  s = s.replace(/([^，。 ]{1,12}?)分之([^，。 ]{1,12}?)(?=[，。]|$)/g, '\\frac{$2}{$1}')
  // "根号下 X"（到逗号/句号/结尾）
  s = s.replace(/根号下([^，。 ]{1,20}?)(?=[，。 ]|$)/g, '\\sqrt{$1}')
  // "X的n次方 / X平方 / X立方"
  s = s.replace(/的平方/g, '^{2}').replace(/平方/g, '^{2}')
  s = s.replace(/的立方/g, '^{3}')
  s = s.replace(/的(\d+)次方/g, '^{$1}')
  s = s.replace(/的([a-z])次方/g, '^{$1}')
  // 运算与符号词
  s = s.replace(/加减/g, '\\pm')
  s = s.replace(/加上|加/g, '+').replace(/减去|减/g, '-').replace(/乘以|乘/g, '\\times').replace(/除以|除/g, '\\div')
  s = s.replace(/绝对值([^，。 ]{1,12}?)(?=[，。 ]|$)/g, '\\left|$1\\right|')
  s = s.replace(/等于/g, '=').replace(/大于等于/g, '\\geq').replace(/小于等于/g, '\\leq').replace(/不等于/g, '\\neq')
  s = s.replace(/约等于/g, '\\approx')
  s = s.replace(/无穷大/g, '\\infty')
  // 三角/对数/指数
  s = s.replace(/正弦/g, '\\sin ').replace(/余弦/g, '\\cos ').replace(/正切/g, '\\tan ')
  s = s.replace(/自然对数/g, '\\ln ').replace(/对数/g, '\\log ')
  s = s.replace(/e的/g, 'e^')
  // 单字母变量间补乘号省略（保持可读），英文数字字母原样
  s = s.replace(/\s+/g, ' ').trim()
  // 结果若仍含中文且无 LaTeX 指令，包 \text{} 兜底展示
  if (/[\u4e00-\u9fa5]/.test(s) && !/\\/.test(s)) s = `\\text{${s}}`
  return s
}

/** 大纲环节目标草案（围绕课题与课型的可执行表述，教师可改） */
function goalOf(b: { id: string; name: string }, topic: string, lessonType: string): string {
  const map: Record<string, string> = {
    'bd-context': `对齐课标与 ${topic} 的学情：明确起点（已会什么）与障碍（容易错在哪）。`,
    'bd-objectives': `用可检验行为写出 ${topic} 的 3 条目标（每个目标对应一道可出的题）。`,
    'bd-keypoints': `标出 ${topic} 的重点与难点，并各给一个突破动作（实验 / 反例 / 分步板书）。`,
    'bd-intro': `用一个 90 秒内讲完的情境引出 ${topic} 的问题（与教材或本班经验挂钩）。`,
    'bd-explore': `让学生经历 ${topic} 的生成过程：先动手/观察，再归纳，最后符号化。`,
    'bd-examples': `精讲 1 道覆盖 ${topic} 主干方法的例题，板书分步并标注每步依据。`,
    'bd-variation': `配 1-2 道变式（改一个条件），检验方法迁移而非记忆。`,
    'bd-pitfalls': `用 2 个反例暴露 ${topic} 的常见误用，由学生判断并说理由。`,
    'bd-summary': `用主线图收束 ${topic}，当堂 1 道小题回收掌握度。`,
    'bd-homework': `按基础/巩固/挑战三层布置，每层 1-2 题，挑战层衔接下一课。`,
  }
  if (lessonType === '讲评课' && b.id === 'bd-intro') return `出示 ${topic} 的得分分布，让学生先看自己的位置。`
  return map[b.id] || `围绕「${topic}」完成本环节。`
}

/**
 * B0 反污染：内容源按「章节（教师显式选择）→ 课题关键词」路由。
 * 原型内置三套真实内容源（椭圆 / 双曲线 / 导数），未命中的课题在封面标注「演示模板页」，
 * 不再用固定椭圆内容冒充任意课题（审计决定性反例 #1/#2 的根治）。
 */
type FixtureKey = 'ellipse' | 'hyperbola' | 'derivative'
const CHAPTER_FIXTURES: { key: FixtureKey; chapterKw: string[]; topicKw: string[]; deckId: string; planId: string; outline: string[] }[] = [
  { key: 'hyperbola', chapterKw: ['双曲线'], topicKw: ['双曲线'], deckId: 'deck-hyperbola', planId: 'plan-hyperbola', outline: ['复习对照 · 椭圆', '双曲线的定义', '标准方程推导', '例题精讲', '变式训练', '课堂小结'] },
  { key: 'derivative', chapterKw: ['导数', '单调性'], topicKw: ['导数', '单调性', '极值'], deckId: 'deck-derivative', planId: 'plan-derivative', outline: ['情境引入', '判定链推导', '例题精讲', '变式训练', '课堂小结'] },
  { key: 'ellipse', chapterKw: ['椭圆'], topicKw: ['椭圆', '焦点弦', '离心率'], deckId: 'deck-ellipse', planId: 'plan-ellipse', outline: ['复习回顾', '概念定义', '标准方程推导', '例题精讲', '变式训练', '课堂小结'] },
]
function resolveFixture(topic: string, chapter?: string): (typeof CHAPTER_FIXTURES)[number] | null {
  const c = String(chapter || '')
  const t = String(topic || '')
  // 课型含"复习/综合/专题/讲评"时允许跨章：仅在章节显式命中时路由，不做课题关键词硬套
  return CHAPTER_FIXTURES.find((f) => f.chapterKw.some((k) => c.includes(k)))
    || CHAPTER_FIXTURES.find((f) => f.topicKw.some((k) => t.includes(k)))
    || null
}

/* ============ C1.1 要求编译器（规则词表，确定性）：教师自然语言要求 → 大纲结构改造 + 逐条回应台账 ============
 * 审计迭代（2026-09-06 用户反馈）：备课台的自由文本要求此前被丢弃，大纲永远回显固定模板——"提前编排"。
 * 现在要求被真实编译进大纲（改页名/拆例题/插环节/删环节），未支持的要求如实标注 uncovered。
 * 原型边界：词表规则匹配（非大模型理解），内容源仍为内置 fixture；词表外要求诚实降级，不假装听懂。 */
type OutlineItem = { title: string; kind: string }
type ReqEntry = { id: number; text: string; status: 'applied' | 'uncovered'; pages: number[]; note?: string; _refs?: string[] }

const OUTLINE_REQ_LEXICON = {
  intro: [
    { kw: ['拉链'], label: '拉链实验' },
    { kw: ['实验'], label: '动手实验' },
    { kw: ['几何画板', '动画', '演示'], label: '动画演示' },
    { kw: ['生活', '实际', '情境'], label: '生活情境' },
  ],
  extras: [
    { kw: ['易错', '辨析', '错例'], title: '易错辨析', kind: 'review' },
    { kw: ['真题', '高考题', '模考'], title: '真题演练', kind: 'example' },
    { kw: ['当堂检测', '随堂测', '课堂检测', '小测'], title: '当堂检测', kind: 'variation' },
    { kw: ['小组讨论', '小组合作', '合作探究', '探究活动', '小组探究'], title: '小组探究', kind: 'blank' },
    { kw: ['数学文化', '数学史'], title: '数学文化 · 背景', kind: 'blank' },
  ],
} as const

function compileRequirements(texts: string[], outline: OutlineItem[], topic: string): ReqEntry[] {
  const reqs: ReqEntry[] = []
  const topicCore = topic.replace(/（[^）]*）|\([^)]*\)/g, '').trim()
  let id = 0
  const insertBeforeSummary = (item: OutlineItem) => {
    const si = outline.findIndex((o) => o.kind === 'summary')
    if (si >= 0) outline.splice(si, 0, item)
    else outline.push(item)
  }
  for (const raw of texts) {
    for (const seg0 of raw.split(/[，。；,;\n]+/)) {
      const seg = seg0.trim()
      if (seg.length < 2) continue
      if (topicCore && (seg.includes(topicCore) || topicCore.includes(seg))) continue // 课题本身不算要求
      id += 1
      const req: ReqEntry = { id, text: seg, status: 'applied', pages: [] }
      const pageRefs: string[] = []
      let handled = false
      // ① 移除类要求：只删「复习对照/复习铺垫」等复习页（标题前缀判定），不得误删易错辨析等 review 型环节
      if (/不要复习|无需复习|跳过复习|删掉复习|去掉复习/.test(seg)) {
        for (let i = outline.length - 1; i >= 0; i--) {
          if (/^复习/.test(outline[i].title)) outline.splice(i, 1)
        }
        req.note = '已移除复习类页面'
        handled = true
      }
      // ② 明确要视频 → 诚实未支持（课件内嵌视频引用属 C2）
      if (!handled && /视频|微课/.test(seg)) {
        req.status = 'uncovered'
        req.note = '课件内嵌视频引用属下一批次（C2）：当前可先在课堂直接打开 B站播放'
        handled = true
      }
      // ③ 引入方式：改「引入」页名；无引入页则在封面后插入
      if (!handled) {
        const intro = OUTLINE_REQ_LEXICON.intro.find((l) => l.kw.some((k) => seg.includes(k)))
        if (intro) {
          const ii = outline.findIndex((o) => /引入/.test(o.title))
          if (ii >= 0) {
            outline[ii].title = `${outline[ii].title.split(' · ')[0]} · ${intro.label}`
            pageRefs.push(outline[ii].title)
          } else {
            const item: OutlineItem = { title: `情境引入 · ${intro.label}`, kind: 'blank' }
            const ci = outline.findIndex((o) => o.kind === 'cover')
            outline.splice(ci >= 0 ? ci + 1 : 0, 0, item)
            pageRefs.push(item.title)
          }
          handled = true
        }
      }
      // ④ 例题分层：例题页拆「基础 / 提升」两页
      if (!handled && /从基础到提升|由浅入深|分层|梯度|基础到提高|从易到难/.test(seg)) {
        const ei = outline.findIndex((o) => o.kind === 'example')
        if (ei >= 0) {
          const base = outline[ei].title.split(' · ')[0]
          outline[ei].title = `${base} · 基础`
          outline.splice(ei + 1, 0, { title: `${base} · 提升`, kind: 'example' })
          pageRefs.push(`${base} · 基础`, `${base} · 提升`)
          handled = true
        }
      }
      // ⑤ 环节增补：一段话里的多个环节都吃掉（如「加易错辨析和当堂检测」）
      if (!handled) {
        for (const extra of OUTLINE_REQ_LEXICON.extras) {
          if (extra.kw.some((k) => seg.includes(k))) {
            if (!outline.some((o) => o.title === extra.title)) insertBeforeSummary({ title: extra.title, kind: extra.kind })
            pageRefs.push(extra.title)
            handled = true
          }
        }
      }
      // ⑥ 多课时 → 结构性建议（不假装自动拆分）
      if (!handled && /两课时|2\s*课时|第二课时/.test(seg)) {
        req.note = '原型不自动拆分多课时：生成后用「＋页」扩充，或按课时分开生成'
        handled = true
      }
      if (!handled) {
        req.status = 'uncovered'
        req.note = '原型规则词表未覆盖该要求：已按通用结构生成，可在下方逐页手改'
      }
      req._refs = [...pageRefs]
      reqs.push(req)
    }
  }
  /* 页码在全部结构变更结束后按标题统一回查（中途插删会移位） */
  for (const req of reqs) {
    req.pages = (req._refs || []).map((t) => outline.findIndex((o) => o.title === t)).filter((i) => i >= 0)
    delete req._refs
  }
  return reqs
}

/** server 端安全富文本：latex 混文本 → 内联 span（原型简版；real = KaTeX 服务端渲染） */
function renderRichSafe(text: string): string {
  const escaped = String(text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return escaped.replace(/\$([^$]+)\$/g, '<i style="font-family:Georgia,serif">$1</i>')
}

export async function handleTeacherV3Api(req: any, res: any): Promise<boolean> {
  const url = String(req.url || '')
  if (!url.startsWith('/teacher-v3/')) return false
  const method = String(req.method || 'GET').toUpperCase()
  if (!isTeacher(req)) { fail(res, 403, 40301, 'role_denied'); return true }
  const path = url.split('?')[0]
  const body = method === 'GET' || method === 'DELETE' ? {} : await readBody(req)

  /* ============ catalog 基础目录域 ============ */
  if (method === 'GET' && path === '/teacher-v3/today') return ok(res, V3_TODAY), true
  if (method === 'GET' && path === '/teacher-v3/classes') return ok(res, { items: V3_CLASSES }), true
  if (method === 'GET' && path === '/teacher-v3/tasks') {
    const t = tasks.find((x) => x.task_id === 'task-photo-1')
    if (t && t.status === 'running') { t.progress = Math.min(100, t.progress + 11); if (t.progress >= 100) { t.status = 'succeeded'; t.stage = '完成' } }
    return ok(res, { items: tasks, running: tasks.filter((x) => x.status === 'running' || x.status === 'queued').length }), true
  }
  if (method === 'GET' && path === '/teacher-v3/deck-templates') return ok(res, { items: V3_DECK_TEMPLATES }), true
  if (method === 'GET' && path === '/teacher-v3/lesson-templates') return ok(res, { items: lessonTemplates }), true
  if (method === 'GET' && path === '/teacher-v3/recipes') return ok(res, { items: recipes }), true
  if (method === 'GET' && path === '/teacher-v3/textbook-chapters') return ok(res, V3_TEXTBOOK_CHAPTERS), true
  if (method === 'POST' && path === '/teacher-v3/textbooks/rebuild-index') {
    // V3.3：上传教材 → AI 重建「知识库目录」（mock 确定性样例；real = 教材 PDF/图片 → 版面分析 → 目录树）
    const fileName = String(body.file || '新教材.pdf')
    const rebuilt = buildRebuiltTextbook(fileName, body.version)
    return ok(res, rebuilt), true
  }

  /* ============ V3.1：上传教案 → 质量体检 → 提炼个人模板 ============ */
  if (method === 'POST' && path === '/teacher-v3/plan-templates/import') {
    // 体检（反套话 + 栏目完整度）：帮老师挑出值得当模板的 1-3 份（RESEARCH_LESSON_PREP §5.3）
    const files: string[] = Array.isArray(body.files) && body.files.length ? body.files : ['上传教案.docx']
    const qualityPool = [
      { file: files[0] || '上传教案.docx', score: 86, board_coverage: ['教材分析', '学情分析', '教学目标', '重难点', '教学过程三栏', '板书设计'], missing_boards: ['教后反思'], cliche_hits: [], recommended: true, suggestion: '栏目齐全、套话率低，适合作为个人模板' },
      { file: files[1] || '往年常规教案.docx', score: 58, board_coverage: ['教学目标', '教学过程'], missing_boards: ['学情分析', '重难点', '板书设计', '教后反思'], cliche_hits: ['「激发学习兴趣」未落到具体活动', '「培养分析问题与解决问题的能力」口号式'], recommended: false, suggestion: '栏目缺 4 项且套话密度高，不建议作模板' },
      { file: files[2] || '备课组详案.docx', score: 74, board_coverage: ['教材分析', '学情分析', '教学目标', '重难点', '教学过程三栏'], missing_boards: ['板书设计'], cliche_hits: [], recommended: false, suggestion: '仅缺板书设计，补齐后可用' },
    ]
    return ok(res, { reports: qualityPool.slice(0, Math.max(1, files.length)) }), true
  }
  const tplExtract = path === '/teacher-v3/plan-templates/extract'
  if (method === 'POST' && tplExtract) {
    const t: V3LessonTemplate = {
      id: uid('lt-mine'), name: `我的模板 · ${body.name || '上传提炼'}`,
      style_tag: '提炼自上传教案 · 三栏写法 · 栏目已体检',
      sections: ['教材分析·学情', '教学目标（素养线）', '重难点与突破', '情境引入', '新知探究', '例题变式', '课堂检测', '分层作业'],
      recommended_for: '个人模板 · 仅本人可见', sample_topic: body.name || '',
      source: 'teacher_upload',
      quality_report: { file: body.file || '上传教案.docx', score: 86, board_coverage: ['教材分析', '学情分析', '教学目标', '重难点', '教学过程三栏', '板书设计'], missing_boards: ['教后反思'], cliche_hits: [], recommended: true, suggestion: '栏目齐全、套话率低，适合作为个人模板' },
    }
    lessonTemplates.push(t)
    return ok(res, t), true
  }

  if (method === 'GET' && path === '/teacher-v3/quiz/questions') {
    // 服务端筛选：kp（多码逗号分隔）/ q（题干模糊）/ difficulty / q_type / source / folder（专题夹引用过滤）
    let items = quizQuestions as any[]
    const q = url.includes('?') ? Object.fromEntries(new URL(url, 'http://mock.local').searchParams.entries()) : {}
    if (q.kp) { const codes = String(q.kp).split(','); items = items.filter((x) => codes.includes(x.kp_code)) }
    if (q.q) { const kw = String(q.q).trim(); if (kw) items = items.filter((x) => (x.stem_latex || '').includes(kw)) }
    if (q.difficulty) items = items.filter((x) => x.difficulty === q.difficulty)
    if (q.q_type) items = items.filter((x) => x.q_type === q.q_type)
    if (q.source) items = items.filter((x) => x.source === q.source)
    if (q.folder) items = items.filter((x) => (x.folder_refs || []).includes(q.folder))
    return ok(res, { items, total: items.length }), true
  }
  if (method === 'POST' && path === '/teacher-v3/quiz/questions') {
    // V3.1：自编题录入（结构化手输，source='自编'）
    const q = {
      id: uid('q-self'),
      kp_name: body.kp_name || '自编', kp_code: body.kp_code || 'YD-01',
      kp_path: body.kp_path || [], q_type: body.q_type || 'solve', difficulty: body.difficulty || 'medium',
      stem_latex: body.stem_latex || '', answer: body.answer || '', analysis: body.analysis || '',
      source: '自编' as const, folder_refs: [] as string[], usage_count: 0,
    }
    quizQuestions.unshift(q)
    return ok(res, q), true
  }
  const quizDelMatch = path.match(/^\/teacher-v3\/quiz\/questions\/([^/]+)$/)
  if (method === 'DELETE' && quizDelMatch) {
    // V3.3：删除一道题（含拍照入库题；同步清理所在专题夹的引用计数）
    const i = quizQuestions.findIndex((x: any) => x.id === quizDelMatch[1])
    if (i < 0) return fail(res, 404, 40402, '题目不存在'), true
    quizQuestions.splice(i, 1)
    refreshFolderCounts()
    return ok(res, { ok: true as const }), true
  }
  if (method === 'GET' && path === '/teacher-v3/quiz/kp-tree') return ok(res, { tree: kpTree }), true

  /* ============ V3.2：知识点树编辑（个人定制层）+ AI 识别归属 ============ */
  if (method === 'POST' && path === '/teacher-v3/quiz/kp-tree') {
    const parent = body.parent_id ? findKp(kpTree, String(body.parent_id)) : null
    const node = { id: uid('kp-x'), name: String(body.name || '新知识点'), kp_codes: [uid('KP').toUpperCase()] }
    if (parent) { if (!parent.children) parent.children = []; parent.children.push(node) }
    else kpTree.push(node as never)
    return ok(res, node), true
  }
  const kpItemMatch = path.match(/^\/teacher-v3\/quiz\/kp-tree\/([^/]+)$/)
  if (method === 'PATCH' && kpItemMatch) {
    const n = findKp(kpTree, kpItemMatch[1])
    if (!n) return fail(res, 404, 40401, '知识点不存在'), true
    n.name = String(body.name || n.name)
    return ok(res, { ok: true as const }), true
  }
  if (method === 'DELETE' && kpItemMatch) {
    const done = removeKp(kpTree, kpItemMatch[1])
    if (!done) return fail(res, 404, 40401, '知识点不存在'), true
    return ok(res, { ok: true as const }), true
  }
  if (method === 'POST' && path === '/teacher-v3/quiz/kp-suggest') {
    // mock 启发式：按题干/图片名关键词匹配分类树叶子（真实链路 = 题干/图片 → VLM → 树内最近叶节点 + 备选）
    let text = String(body.stem || '')
    if (!text && body.src) {
      try { text = decodeURIComponent(String(body.src)) } catch { text = String(body.src) }
    }
    const leafsWithPath: { code: string; path: string[]; name: string; kw: string }[] = []
    const walk = (nodes: any[], prefix: string[]) => {
      for (const n of nodes) {
        const p = [...prefix, n.name]
        if (n.children?.length) walk(n.children, p)
        else leafsWithPath.push({ code: n.kp_codes?.[0] || n.id, path: p, name: n.name, kw: n.name })
      }
    }
    walk(kpTree, [])
    const scored = leafsWithPath
      .map((l) => ({ ...l, score: (text.includes(l.kw.slice(0, 2)) ? 2 : 0) + (text.includes(l.kw) ? 2 : 0) }))
      .sort((a, b) => b.score - a.score)
    const top = scored[0]
    const fallback = leafsWithPath[0]
    const best = top && top.score > 0 ? top : fallback
    const alternates = scored.filter((s) => s.code !== best.code).slice(0, 2).map(({ code, path, name }) => ({ code, path, name }))
    return ok(res, {
      suggestion: { code: best.code, path: best.path, name: best.name, confidence: top && top.score > 0 ? 0.92 : 0.41 },
      alternates,
      note: top && top.score > 0 ? '按题干关键词匹配分类树（mock 启发式）' : '题干信息不足，给默认归属；可在树中自行调整',
    }), true
  }

  /* ============ V3.1：我的专题夹（引用式收集，不挪题） ============ */
  if (method === 'GET' && path === '/teacher-v3/folders') { refreshFolderCounts(); return ok(res, { items: folders }), true }
  if (method === 'POST' && path === '/teacher-v3/folders') {
    const f = { id: uid('fld'), name: String(body.name || '新专题夹'), desc: body.desc || '', count: 0, updated_at: new Date().toISOString().slice(0, 10) }
    folders.push(f)
    return ok(res, f), true
  }
  const folderMatch = path.match(/^\/teacher-v3\/folders\/([^/]+)$/)
  if (method === 'DELETE' && folderMatch) {
    const i = folders.findIndex((f) => f.id === folderMatch[1])
    if (i >= 0) folders.splice(i, 1)
    return ok(res, { ok: true as const }), true
  }
  const folderQMatch = path.match(/^\/teacher-v3\/folders\/([^/]+)\/questions$/)
  if (method === 'POST' && folderQMatch) {
    for (const qid of (body.question_ids || []) as string[]) {
      const q = quizQuestions.find((x: any) => x.id === qid)
      if (q) { if (!Array.isArray(q.folder_refs)) q.folder_refs = []; if (!q.folder_refs.includes(folderQMatch[1])) q.folder_refs.push(folderQMatch[1]) }
    }
    refreshFolderCounts()
    return ok(res, { ok: true as const, count: folderCount(folderQMatch[1]) }), true
  }
  const folderQDel = path.match(/^\/teacher-v3\/folders\/([^/]+)\/questions\/([^/]+)$/)
  if (method === 'DELETE' && folderQDel) {
    const q = quizQuestions.find((x: any) => x.id === folderQDel[2])
    if (q && Array.isArray(q.folder_refs)) q.folder_refs = q.folder_refs.filter((r: string) => r !== folderQDel[1])
    refreshFolderCounts()
    return ok(res, { ok: true as const, count: folderCount(folderQDel[1]) }), true
  }
  if (method === 'POST' && path === '/teacher-v3/quiz/scan-import') {
    const q = {
      id: uid('q-img'),
      kp_name: body.kp_name || '拍照入库',
      kp_code: body.kp_code || 'SCAN-00',
      kp_path: body.kp_path || [],
      q_type: body.as_image ? ('image' as const) : ('solve' as const),
      difficulty: 'medium' as const,
      stem_latex: body.as_image ? '（图片题）扫描原图题目。' : '（拍照识别题）扫描原图题目。',
      stem_image: body.src,
      answer: body.solution_src ? '见解答过程' : '待批改',
      /** V3.2：解答过程原图与题干分开上传、分开存储（原样保留，不强转文字） */
      solution_image: body.solution_src || undefined,
      analysis: body.solution_src ? '（解答过程已按原图入库，可在下方对照查看）' : undefined,
      source: '拍照入库' as const,
    }
    quizQuestions.push(q)
    return ok(res, q), true
  }
  if (method === 'GET' && path === '/teacher-v3/insights/overview') return ok(res, V3_INSIGHTS), true
  if (method === 'GET' && path === '/teacher-v3/resources') return ok(res, { items: V3_RESOURCES }), true

  /* ============ 课件 decks ============ */
  if (method === 'GET' && path === '/teacher-v3/decks') {
    return ok(res, {
      items: decks.map((d) => ({
        id: d.id, title: d.title, template_id: d.template_id, source: d.source,
        slide_count: d.slides.length, class_name: d.photo_context ? '高二(3)班' : '高二(5)班', updated_at: d.updated_at,
      })),
    }), true
  }
  const deckMatch = path.match(/^\/teacher-v3\/decks\/([^/]+)$/)
  if (method === 'GET' && deckMatch) {
    const d = decks.find((x) => x.id === deckMatch[1])
    if (!d) return ok(res, null), true
    return ok(res, d), true
  }
  if (method === 'PATCH' && deckMatch) {
    const d = decks.find((x) => x.id === deckMatch[1])
    if (!d) return ok(res, null), true
    if (body.title !== undefined) d.title = body.title
    if (body.template_id !== undefined) d.template_id = body.template_id
    d.updated_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
    return ok(res, d), true
  }
  const addSlideMatch = path.match(/^\/teacher-v3\/decks\/([^/]+)\/slides$/)
  if (method === 'POST' && addSlideMatch) {
    const d = decks.find((x) => x.id === addSlideMatch[1])
    if (!d) return ok(res, null), true
    const slide: V3Slide = body.slide
    const idx = body.after_id ? d.slides.findIndex((s) => s.id === body.after_id) + 1 : d.slides.length
    d.slides.splice(idx, 0, slide)
    return ok(res, { deck: d }), true
  }
  const delSlideMatch = path.match(/^\/teacher-v3\/decks\/([^/]+)\/slides\/([^/]+)$/)
  if (method === 'DELETE' && delSlideMatch) {
    const d = decks.find((x) => x.id === delSlideMatch[1])
    if (!d) return ok(res, null), true
    d.slides = d.slides.filter((s) => s.id !== delSlideMatch[2])
    return ok(res, { deck: d }), true
  }
  if (method === 'PATCH' && delSlideMatch) {
    const d = decks.find((x) => x.id === delSlideMatch[1])
    if (!d) return ok(res, null), true
    const s = d.slides.find((x) => x.id === delSlideMatch[2])
    if (!s) return ok(res, null), true
    if (body.notes !== undefined) s.notes = body.notes
    if (body.anchor_bar !== undefined) s.anchor_bar = body.anchor_bar
    if (body.elements !== undefined) s.elements = body.elements
    return ok(res, d), true
  }
  const exportMatch = path.match(/^\/teacher-v3\/decks\/([^/]+)\/export$/)
  if (method === 'POST' && exportMatch) {
    const taskId = uid('task')
    /* B3 导出真实性：文件生成服务（PPTX 渲染）属 M2-B 后端门，原型阶段任务停留在排队态并如实标注，不伪装完成 */
    tasks.unshift({
      task_id: taskId, title: `导出课件（${body.format}）· 原型`, capability: 'generation',
      status: 'queued', progress: 0,
      stage: '等待文件生成服务（原型未接入，本任务仅演示进度流，不产出文件）',
    })
    return ok(res, { task_id: taskId }), true
  }

  /* ============ 教案 plans ============ */
  if (method === 'GET' && path === '/teacher-v3/plans') {
    return ok(res, {
      items: plans.map((p) => ({ id: p.id, topic: p.topic, class_id: p.class_id, lesson_type: p.lesson_type, section_count: p.sections.length, confirmed: !!(p as any).confirmed, updated_at: '2026-09-02' })),
    }), true
  }
  const planMatch = path.match(/^\/teacher-v3\/plans\/([^/]+)$/)
  if (method === 'GET' && planMatch) {
    const p = plans.find((x) => x.id === planMatch[1])
    return ok(res, p ?? null), true
  }
  if (method === 'PATCH' && planMatch) {
    const p = plans.find((x) => x.id === planMatch[1])
    if (p && body && typeof body === 'object') Object.assign(p, body)
    return ok(res, p ?? null), true
  }
  const planConfirm = path.match(/^\/teacher-v3\/plans\/([^/]+)\/confirm$/)
  if (method === 'POST' && planConfirm) {
    const p = plans.find((x) => x.id === planConfirm[1])
    if (p) (p as any).confirmed = true
    return ok(res, p ?? null), true
  }
  const pushMatch = path.match(/^\/teacher-v3\/plans\/([^/]+)\/push-to-deck$/)
  if (method === 'POST' && pushMatch) {
    const src = plans.find((x) => x.id === pushMatch[1])
    /* B0 反污染：教案直通同样按课题路由内容源 */
    const fixture = resolveFixture(String(src?.topic || ''))
    const deck = JSON.parse(JSON.stringify(decks.find((d) => d.id === (fixture?.deckId || 'deck-ellipse'))))
    deck.id = uid('deck')
    deck.title = `${src?.topic ?? '教案'} · 教案直通`
    deck.template_id = body.template_id
    deck.source = 'lesson-push'
    deck.updated_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
    decks.unshift(deck)
    return ok(res, { deck_id: deck.id }), true
  }

  /* ============ recognition 识别域 ============ */
  if (method === 'POST' && path === '/teacher-v3/recognition/photo-ingest') {
    startSse(res)
    const cfg = body.config || {}
    const photos = Math.max(1, Math.min(6, Array.isArray(body.photos) ? body.photos.length : 1))
    sendSse(res, 'meta', { question_label: body.question_label || '拍照例题', photos, config: cfg, steps_total: photos + V3_RECOGNIZE_BLOCKS.length + 2 })
    await sleep(420)
    for (let i = 0; i < photos; i++) {
      sendSse(res, 'photo', { index: i, status: 'received', note: i === 0 ? '原图已锚定（后续每页保留对照）' : '原图已锚定' })
      await sleep(300)
    }
    for (const b of V3_RECOGNIZE_BLOCKS) {
      sendSse(res, 'block', { ...b, page_id: 'p0' })
      await sleep(260)
    }
    sendSse(res, 'paginate', {
      note: '长解答自动分页：步骤边界换页，字号下限 20pt，未缩放内容',
      pages: 2, anchor_bars: ['接上页 · l:y=√3(x−1)', '接上页 · 弦长与面积'],
      font_tier: cfg.font_tier || 'standard',
    })
    await sleep(320)
    const deck = JSON.parse(JSON.stringify(decks.find((d) => d.id === 'deck-photo')))
    deck.id = uid('deck')
    deck.title = body.question_label || '椭圆焦点弦例题（拍照生成）'
    deck.photo_context = { config: cfg, photos, question_label: body.question_label || '' }
    deck.updated_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
    decks.unshift(deck)
    tasks.unshift({ task_id: uid('task'), title: `拍照生成《${deck.title}》`, capability: 'recognition', status: 'succeeded', progress: 100, stage: '完成' })
    sendSse(res, 'done', { deck_id: deck.id, slides: deck.slides.length })
    res.end()
    return true
  }
  if (method === 'POST' && path === '/teacher-v3/recognition/rebuild') {
    return ok(res, {
      candidates: [
        { id: 'rb1', preset_id: 'conic/ellipse-coordinate', params: { a: 2, b: 1.73 }, passed_validation: true, match_note: '与原图区域高度匹配：椭圆+焦点+弦（a=2,b=√3）' },
        { id: 'rb2', preset_id: 'conic/ellipse', params: { a: 2, b: 1.73 }, passed_validation: true, match_note: '仅椭圆轮廓，无坐标轴标注' },
        { id: 'rb3', preset_id: 'plane/circle-line', params: { r: 1.9, d: 1 }, passed_validation: false, match_note: '构造校验未通过：焦点不在圆上，不允许上屏' },
      ],
    }), true
  }
  if (method === 'POST' && path === '/teacher-v3/recognition/photo-to-formula') {
    // P2：单张照片 → 公式识别（mock 确定性样例，含置信度）。真实实现走 VLM；红线 R2 要求识别结果必须进编辑器审查。
    const samples = [
      { latex: '\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1\\;(a>b>0)', confidence: 0.86, note: '椭圆标准方程' },
      { latex: '|MF_{1}|+|MF_{2}|=2a\\;(2a>2c)', confidence: 0.9, note: '椭圆定义' },
      { latex: 'y=\\frac{b}{a}\\sqrt{a^{2}-x^{2}}', confidence: 0.74, note: '上半椭圆显式' },
    ]
    const pick = samples[(body.src?.length || 0) % samples.length]
    return ok(res, { latex: pick.latex, confidence: pick.confidence, note: pick.note }), true
  }
  if (method === 'POST' && path === '/teacher-v3/recognition/plan-photo-draft') {
    // P5：教案板块拍照填充（mock 确定性样例）。识别整理成三栏草稿（R2：教师可改），real 实现走 VLM 读图 + 板块语义改写。
    const board = String(body.board_name || '新知探究')
    const topic = String(body.topic || '本课题')
    const drafts: Record<string, { t: string; s: string; d: string }> = {
      '复习引入': { t: `出示 ${topic} 的教材场景图，提问学生已有认知，引出本课要解决的问题。`, s: '观察题目情境，回顾相关已学知识，尝试表述思路。', d: '用生活/教材情境激活前测，定位学生起点。' },
      '新知探究': { t: `引导学生从实例归纳 ${topic} 的定义，用 $\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1$ 逐步建式。`, s: '经历抽象过程，指出定义中需注意的限制条件。', d: '以认知冲突驱动探究，避免直接给结论。' },
      '例题精讲': { t: `讲透一道 ${topic} 高考真题：先启发动笔，再对照标准答案提炼步骤。`, s: '独立试做并核对，说出每一步的依据。', d: '示范规范解题路径，渗透通性通法。' },
      '课堂小结与检测': { t: '用思维导图带领学生梳理本课知识结构，配一道限时小题即时检测。', s: '自主小结知识框架，完成检测小题自查。', d: '当堂反馈，暴露未掌握的环节。' },
    }
    const w = drafts[board] || { t: `围绕「${topic}」组织本板块教学，引导学生动手与思考。`, s: '参与活动，记录要点并尝试应用。', d: '落实本课目标，关注全体学生参与度。' }
    return ok(res, { teacher_activity: w.t, student_activity: w.s, design_intent: w.d, confidence: 0.81 }), true
  }

  /* ============ generation AI 生成域（SSE） ============ */
  if (method === 'POST' && path === '/teacher-v3/generation/deck-outline') {
    /* B3 大纲 Gate 第一段：课题 → 大纲草稿（页标题+页型），教师可改/可增删，确认后才逐页生成。
       大纲确认是"真 Gate"（执行提示词 §16.2）：mock 纯增量端点，不改既有契约。
       C1 环节语义大纲：课型决定结构骨架（习题/讲评/复习各有环节链，环节 kind 随页下发）；
       章节参与内容源路由（教师显式选章节优先于课题关键词）。输入扩展登记 IFC-C1-a。 */
    const topic = String(body.topic || '')
    const chapter = String(body.chapter || '')
    const courseType = String(body.course_type || '新授课')
    const fixture = resolveFixture(topic, chapter)
    const OUTLINE_BY_TYPE: Record<string, { title: string; kind: string }[]> = {
      习题课: [
        { title: '复习铺垫', kind: 'review' }, { title: '典型例题', kind: 'example' },
        { title: '方法提炼', kind: 'blank' }, { title: '分层练习', kind: 'variation' }, { title: '纠错小结', kind: 'summary' },
      ],
      讲评课: [
        { title: '得分概览', kind: 'blank' }, { title: '错因聚焦', kind: 'review' },
        { title: '典型错例剖析', kind: 'example' }, { title: '变式再练', kind: 'variation' }, { title: '方法归纳', kind: 'summary' },
      ],
      复习课: [
        { title: '知识梳理', kind: 'review' }, { title: '典型例题', kind: 'example' },
        { title: '综合练习', kind: 'variation' }, { title: '方法归纳', kind: 'blank' }, { title: '课堂小结', kind: 'summary' },
      ],
    }
    let outline: { title: string; kind: string }[]
    if (OUTLINE_BY_TYPE[courseType]) {
      outline = OUTLINE_BY_TYPE[courseType].map((o) => ({ ...o }))
    } else {
      outline = (fixture?.outline || ['情境引入', '概念定义', '例题精讲', '变式训练', '课堂小结']).map((t, i) => ({
        title: t,
        kind: i === 0 ? 'cover' : /定义|概念/.test(t) ? 'definition' : /推导|探究/.test(t) ? 'derivation' : /例题/.test(t) ? 'example' : /变式|练习/.test(t) ? 'variation' : /小结|回顾/.test(t) ? 'summary' : 'blank',
      }))
    }
    /* C1.1：教师自然语言要求真实编译进大纲（改页名/拆例题/插环节/删环节），并产出逐条回应台账 */
    const reqTexts: string[] = Array.isArray(body.requirements)
      ? body.requirements.map((r: unknown) => String(r || '').trim()).filter(Boolean)
      : []
    const reqs = compileRequirements(reqTexts, outline, topic)
    /* 备小研改版：每页建议时长（kind→分钟，确定性映射），供大纲确认卡片展示（IFC-WS-a） */
    const MINUTES_BY_KIND: Record<string, number> = { cover: 2, review: 5, definition: 10, derivation: 10, example: 12, variation: 10, summary: 6, blank: 5 }
    const outlineWithMinutes = outline.map((o) => ({ ...o, minutes: MINUTES_BY_KIND[o.kind] ?? 6 }))
    const notes = [
      fixture ? `已按${chapter ? '章节' : '课题'}匹配内置内容源（椭圆/双曲线/导数）` : '该课题暂无内置内容源：将生成流程演示页并在封面标注，请勿直接上课使用',
      chapter ? `章节锚定：${chapter}` : '',
      OUTLINE_BY_TYPE[courseType] ? `课型结构：${courseType}（环节链可在大纲中调整）` : '',
      reqs.length ? `已按规则词表编译 ${reqs.filter((r) => r.status === 'applied').length}/${reqs.length} 条要求进大纲（原型：词表匹配，非大模型理解）` : '',
    ].filter(Boolean)
    return ok(res, {
      outline: outlineWithMinutes,
      matched: !!fixture,
      course_type: courseType,
      chapter: chapter || undefined,
      reqs,
      note: notes.join('；'),
    }), true
  }
  /* 备小研改版：拍照链路的识别确认步——fixture 识别预览（不接真实 OCR，诚实标注），可编辑文本仅供预览校对；IFC-WS-a */
  if (method === 'POST' && path === '/teacher-v3/recognition/preview') {
    const imgs = Array.isArray(body.photos) ? body.photos : []
    const items = imgs.slice(0, 6).map((_: unknown, i: number) => ({
      photo_id: `photo_${String(i + 1).padStart(2, '0')}`,
      confidence: i % 3 === 1 ? 0.78 : 0.96,
      warn: i % 3 === 1,
      text: i % 3 === 1
        ? '设双曲线与椭圆 x²/25 + y²/9 = 1 有相同的焦点，且经过点 (3, 2)，求双曲线的标准方程。'
        : '已知双曲线 x²/9 − y²/16 = 1 的左、右焦点分别为 F₁、F₂，过 F₁ 的直线交双曲线的左支于 A、B 两点，求 △ABF₂ 的周长。',
      kps: i % 3 === 1 ? ['双曲线标准方程', '待确认'] : ['双曲线定义', '焦点弦'],
    }))
    return ok(res, { items, note: '演示识别（fixture）：原型阶段未接真实 OCR；生成时后端按原图重新识别，此处文本用于提前校对' }), true
  }
  if (method === 'POST' && path === '/teacher-v3/generation/deck') {
    startSse(res)
    sendSse(res, 'meta', { topic: body.topic, template_id: body.template_id, class_id: body.class_id, chapter: body.chapter || undefined, course_type: body.course_type || '新授课' })
    await sleep(400)
    /* C1：章节显式参与内容源路由（resolveFixture 章节关键词优先于课题关键词，B0 既有规则） */
    const fixture = resolveFixture(String(body.topic || ''), String(body.chapter || ''))
    /* B3 真大纲 Gate：教师确认的 outline（若有）决定页数与每页标题；未传时回退章节默认大纲 */
    const userOutline: { title?: string; kind?: string }[] = Array.isArray(body.outline) && body.outline.length ? body.outline : []
    const outlineTitles = userOutline.length ? userOutline.map((o) => String(o.title || '新页')) : (fixture?.outline || ['情境引入', '概念定义', '例题精讲', '变式训练', '课堂小结'])
    sendSse(res, 'outline', { items: outlineTitles, matched: !!fixture, confirmed: userOutline.length > 0 })
    const tpl = decks.find((d) => d.id === (fixture?.deckId || 'deck-ellipse'))
    if (!tpl) { sendSse(res, 'done', { deck_id: '', slides: 0, note: '模板缺失' }); res.end(); return true }
    const builtSlides: V3Slide[] = []
    const KIND_LAYOUT: Record<string, string> = { variation: 'example', blank: 'blank' }
    for (let i = 0; i < outlineTitles.length; i++) {
      await sleep(520)
      /* 版式选择：优先按教师在大纲 Gate 选的页型匹配版式；无匹配再按页序循环 */
      const wantedKind = userOutline[i]?.kind || ''
      const wantedLayout = KIND_LAYOUT[wantedKind] || wantedKind
      const byKind = wantedLayout ? tpl.slides.find((x) => x.layout === wantedLayout) : undefined
      const base = byKind || tpl.slides[i % tpl.slides.length]
      const s = JSON.parse(JSON.stringify(base))
      s.id = uid('sl')
      for (const e of s.elements) { e.teacher_confirmed = false; e.id = uid('e') }
      const titleEl = s.elements.find((e: any) => e.type === 'text')
      if (titleEl && outlineTitles[i]) titleEl.html = outlineTitles[i]
      builtSlides.push(s)
      sendSse(res, 'slide', { index: i, slide: s, note: `按确认大纲生成 第 ${i + 1}/${outlineTitles.length} 页（未确认草稿）` })
    }
    await sleep(280)
    const deck = JSON.parse(JSON.stringify(tpl))
    deck.id = uid('deck')
    deck.title = body.topic
    deck.template_id = body.template_id
    deck.source = 'topic'
    deck.updated_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
    deck.slides = builtSlides
    /* C1：接地上下文回显（课型/章节/材料），编辑器顶栏展示；契约登记 IFC-C1-a */
    if (body.chapter || (body.course_type && body.course_type !== '新授课') || body.material_name) {
      deck.brief_context = {
        course_type: String(body.course_type || '新授课'),
        chapter: String(body.chapter || ''),
        material: String(body.material_name || ''),
      }
    }
    if (!fixture) {
      /* 未命中内置内容源：诚实标注演示模板页，不冒充该课题的真实内容（R0 反伪造） */
      const cover = deck.slides[0]
      cover?.elements?.push({
        id: uid('e'), z: 9, type: 'text', left: 92, top: 468, width: 640, height: 76,
        html: '⚠ 原型说明：该课题的内容源尚未内置（当前覆盖椭圆 / 双曲线 / 导数），本稿为流程演示页，请勿直接用于上课。',
        font_size: 15, color: '#b1382c',
      })
      sendSse(res, 'note', { text: '该课题暂无内置内容源，已生成流程演示稿并在封面标注' })
    }
    decks.unshift(deck)
    sendSse(res, 'done', { deck_id: deck.id, slides: deck.slides.length })
    res.end()
    return true
  }
  if (method === 'POST' && path === '/teacher-v3/generation/plan/outline') {
    // V3.1 两段式第一段：五件套 → 大纲（环节 / 时长预算 / 目标草案 / 例题建议）。
    // 教师确认/编辑大纲后才走 generation/plan 成稿（消灭流程性黑盒，RESEARCH_LESSON_PREP §5.2 方案 A）
    const duration = Math.max(30, Math.min(60, Number(body.duration) || 45))
    const topic = String(body.topic || '本课题')
    const lessonType = String(body.lesson_type || '新授课')
    // 课型裁剪：习题课/讲评课压掉「新知探究」，复习课换「整体回顾」；骨架不换（P3 定案）
    let boards = V3_TEN_BOARDS
    if (lessonType === '习题课' || lessonType === '讲评课') boards = V3_TEN_BOARDS.filter((b) => b.id !== 'bd-context' && b.id !== 'bd-explore')
    if (lessonType === '复习课') boards = V3_TEN_BOARDS.filter((b) => b.id !== 'bd-context')
    const exampleSrc = body.example_source === 'ai' ? 'AI 配题' : '题库检索'
    const sections = boards.map((b) => ({
      id: b.id,
      name: b.name,
      minutes: b.minutes,
      goal: goalOf(b, topic, lessonType),
      example_suggestion: b.attachable ? `${exampleSrc}：围绕「${topic}」配 1 道${b.id === 'bd-variation' ? '变式' : b.id === 'bd-homework' ? '分层作业' : '基础例题'}（难度可调）` : undefined,
    }))
    /* P 大纲确认页富模块：教学目标 / 重难点 / 板书 / 分层作业（确定性编排，教师可改；IFC-P-a）
       + requirements[]：教师自然语言要求 → 诚实回应（调整进 notes / 例题建议 / 学情锚点） */
    const core = topic.replace(/（.*?）|\(.*?\)/g, '').trim() || '本课'
    const reqTexts: string[] = Array.isArray(body.requirements) ? body.requirements.map((r: unknown) => String(r || '').trim()).filter(Boolean) : []
    const notes2: string[] = []
    let difficulty = '中等'
    const objectives = [
      `理解${core}的核心定义，能准确说出其中的关键条件与限制`,
      `掌握${core}的基本方法（坐标法 / 待定系数法），会解决课本层级的标准问题`,
      `经历${core}的形成过程，体会数形结合与化归思想，发展数学运算与逻辑推理素养`,
    ]
    const major = [`${core}的定义与基本量之间的关系`, `用${core}解决问题时「定位条件 → 选方法 → 书写规范」的三步链`]
    const hard = [`${core}中含参或含根号式子的化简与分类讨论`, `定义中的限制条件为什么必要（反例辨析）`]
    for (const seg of reqTexts.flatMap((t) => t.split(/[，。；,;\n]+/)).map((x) => x.trim()).filter((x) => x.length >= 2)) {
      if (/难度|简单|容易/.test(seg)) { difficulty = /难|提高|加深/.test(seg) ? '较高' : '较低'; notes2.push(`已按要求调整预设难度：${difficulty}（例题与练习建议随之标注）`) ; continue }
      if (/增加例题|多道例题|补充例题/.test(seg)) { for (const s of sections) if (s.id === 'bd-examples') s.example_suggestion = `${exampleSrc}：按教师要求加配 1 道例题（${seg}）`; notes2.push(`已按要求增加例题建议：${seg}`); continue }
      if (/增加互动|互动环节|小组/.test(seg)) { sections.splice(Math.min(3, sections.length), 0, { id: 'bd-extra-activity', name: '互动探究', minutes: 5, goal: `围绕「${core}」组织小组活动：先猜想再验证，教师巡视收集典型想法` } as (typeof sections)[number]); notes2.push('已按要求插入「互动探究」环节（5 分钟）'); continue }
      if (/真题|高考/.test(seg)) { for (const s of sections) if (s.id === 'bd-variation') s.example_suggestion = '高考真题：选用近年真题变式，标注考点与年份（原型为建议文案）'; notes2.push('已按要求在变式环节标记高考真题建议'); continue }
      if (/学情|本班/.test(seg)) { const wk = (V3_TODAY.class_brief.find((b) => b.class_id === body.class_id)?.weak_kp) || '待补充学情数据'; notes2.push(`已结合本班学情锚点：薄弱点「${wk}」，例题入口降低起点`); continue }
      if (/情境|引入|实验|动画/.test(seg)) { for (const s of sections) if (s.id === 'bd-intro') s.goal = `引入方式按教师要求调整：${seg}。${s.goal}`; notes2.push(`已按要求调整情境引入：${seg}`); continue }
      notes2.push(`原型规则词表未覆盖该要求：「${seg}」已记录，可在编辑器继续手改`)
    }
    // 按一节课时长等比缩放（0 分钟板块不缩放），保证 total = duration
    const raw = sections.filter((s) => s.minutes > 0)
    const rawSum = raw.reduce((a, s) => a + s.minutes, 0) || 1
    const k = duration / rawSum
    for (const s of raw) s.minutes = Math.max(1, Math.round(s.minutes * k))
    const total = raw.reduce((a, s) => a + s.minutes, 0)
    const notes = [
      `依据：${body.textbook_version || '人教A版（2019）'}${body.chapter ? ` · ${body.chapter}` : ''}`,
      body.lesson_no ? `第 ${body.lesson_no} 课时` : '',
      `课型：${lessonType} · 一节课 ${duration} 分钟`,
      body.class_id ? `班级学情锚点：${(V3_CLASSES.find((c) => c.class_id === body.class_id)?.name) || ''}（薄弱：${V3_TODAY.class_brief.find((b) => b.class_id === body.class_id)?.weak_kp || '待补充学情数据'}）` : '',
      body.key_points ? `强调重难点：${body.key_points}` : '',
      `例题来源：${exampleSrc}（先检索相似题，避免重复出题）`,
    ].filter(Boolean)
    return ok(res, {
      topic, duration, sections, total_minutes: total,
      notes: [...notes, ...notes2],
      objectives,
      keypoints: { major, hard },
      blackboard: {
        main: [`§ ${topic}`, `一、${core}的定义（图形 + 符号双表征）`, `二、基本方法三步链`, `三、易错点与限制条件`],
        side: ['例题演板', '学生练习', '推导草稿', '图形演示区'],
      },
      homework: [
        { tier: 'basic', label: '基础题（必做）', items: [`${core}：课本对应习题 A 组 1-3（定义与基本量直查）`], minutes: '预计 10 分钟' },
        { tier: 'raise', label: '提升题（选做）', items: [`${core}：B 组 1-2（含参 / 综合应用）`], minutes: '预计 15 分钟' },
        { tier: 'expand', label: '拓展探究', items: [`动手实验 / 跨学科应用：围绕「${core}」写一条发现`], minutes: '预计 20 分钟' },
      ],
      difficulty,
    }), true
  }
  if (method === 'POST' && path === '/teacher-v3/generation/plan') {
    startSse(res)
    sendSse(res, 'meta', { topic: body.topic, lesson_type: body.lesson_type, template_id: body.template_id, boards: V3_TEN_BOARDS.length, note: body.outline ? '按教师确认的大纲生成（可增删环节 / 调时长）' : '按十板块骨架生成' })
    await sleep(400)
    // 教师确认后的大纲优先；未携带时回退十板块（兼容旧流程）
    const outline: { id: string; name: string; minutes: number }[] = Array.isArray(body.outline) && body.outline.length
      ? body.outline.map((s: any, i: number) => ({ id: s.id || `bd-${i}`, name: s.name, minutes: Number(s.minutes) || 0 }))
      : V3_TEN_BOARDS.map((b) => ({ id: b.id, name: b.name, minutes: b.minutes }))
    sendSse(res, 'outline', { sections: outline.map((s) => s.name) })
    /* B0 反污染：教案内容源同样按章节/课题路由（椭圆 / 双曲线 / 导数），不再固定克隆 plan-ellipse */
    const fixture = resolveFixture(String(body.topic || ''), String(body.chapter || ''))
    const src = plans.find((p) => p.id === (fixture?.planId || 'plan-ellipse')) || plans[0]
    for (let i = 0; i < outline.length; i++) {
      await sleep(360)
      const o = outline[i]
      const b = V3_TEN_BOARDS.find((x) => x.id === o.id)
      /* 优先按板块 id 取内容源对应板块（十板块骨架对齐）；自定义环节按位置回退 */
      const byId = src.sections.find((x) => x.id === o.id)
      const s: any = JSON.parse(JSON.stringify(byId || src.sections[Math.min(i, (src?.sections.length || 1) - 1)] || {}))
      s.id = o.id
      s.name = o.name
      s.minutes = o.minutes
      s.confirmed = false
      // 例题/变式/作业板块自动附例题（挂例题）；教学目标板块演示套话检测（反套话）
      if (b?.attachable) {
        s.examples = (b.id === 'bd-examples' ? src?.sections.find((x) => x.id === 'bd-examples')?.examples
          : b.id === 'bd-variation' ? src?.sections.find((x) => x.id === 'bd-variation')?.examples
          : src?.sections.find((x) => x.id === 'bd-homework')?.examples) || [
          { id: uid('ex'), label: b.id === 'bd-examples' ? '例 1' : b.id === 'bd-variation' ? '变式 1' : '作业题', q_type: 'solve', difficulty: 'medium', stem_latex: `结合「${body.topic || '本课题'}」给出的一道例题。`, answer: '略', source: '校本' },
        ]
      }
      if (o.id === 'bd-objectives') {
        s.teacher_activity = '引导学生自主探究，激发学习兴趣，培养学生分析问题与解决问题的能力。'
        s.student_activity = '体会知识的形成过程，增强学好数学的信心。'
        s.cliche = true
        s.cliche_hits = ['空泛的「激发……兴趣」，未说清用什么激', '口号式能力表述，缺具体题例']
      }
      sendSse(res, 'section', { index: i, section: s })
    }
    await sleep(240)
    const plan = JSON.parse(JSON.stringify(src))
    plan.id = uid('plan')
    plan.topic = body.topic
    plan.class_id = body.class_id
    plan.lesson_type = body.lesson_type
    plan.template_id = body.template_id
    if (!Array.isArray(plan.sections)) plan.sections = []
    plans.unshift(plan)
    sendSse(res, 'done', { plan_id: plan.id })
    res.end()
    return true
  }
  const aiElMatch = path.match(/^\/teacher-v3\/generation\/decks\/([^/]+)\/ai-element$/)
  if (method === 'POST' && aiElMatch) {
    startSse(res)
    sendSse(res, 'meta', { deck_id: aiElMatch[1], slide_id: body.slide_id, hint: body.hint })
    await sleep(360)
    sendSse(res, 'suggest', { note: `理解意图：${body.hint || '优化当前页'}` })
    await sleep(420)
    sendSse(res, 'diff', {
      diffs: [
        { slide_id: body.slide_id, op: 'add', after: { id: uid('e'), type: 'formula', left: 460, top: 520, width: 460, height: 52, z: 2, latex: 'e=\\frac{c}{a}=\\frac{1}{2}', font_size: 22, display: true, teacher_confirmed: false }, reason: '补充离心率表达式，呼应「焦点弦」考点' },
        { slide_id: body.slide_id, op: 'modify', element_id: 'e1', after: null, reason: '建议收紧题干行距（不改动内容）' },
      ],
    })
    await sleep(220)
    sendSse(res, 'done', { applied: false, note: 'diff 已生成，等待教师逐条采纳' })
    res.end()
    return true
  }

  /* ============ figures 图形域 ============ */
  if (method === 'GET' && path === '/teacher-v3/figures/presets') {
    return ok(res, {
      items: FIGURE_PRESETS.map(({ build, miniSvg, boundingbox, axis, ...rest }) => rest),
    }), true
  }
  if (method === 'POST' && path === '/teacher-v3/figures/recipes') {
    const r = { id: uid('rcp'), name: body.name, category: 'solid', author: '李文澜', school_shared: !!body.school_shared, params: [], usage_count: 0, note: body.note || '' }
    recipes.unshift(r as any)
    return ok(res, r), true
  }

  /* ============ draw 绘图工作台域（P1） ============ */
  if (method === 'GET' && path === '/teacher-v3/draw/library') return ok(res, { items: drawLibrary }), true
  if (method === 'POST' && path === '/teacher-v3/draw/library') {
    const item = {
      id: uid('fig'), name: String(body.name || '未命名图形'), kind: body.kind === 'fx' ? 'fx' as const : 'free' as const,
      author: '我', shared: !!body.shared, updated_at: new Date().toISOString().slice(0, 16).replace('T', ' '),
      thumb: String(body.thumb || ''), records: body.records, expr: body.expr,
    }
    drawLibrary.unshift(item)
    return ok(res, item), true
  }
  if (method === 'POST' && path === '/teacher-v3/draw/hand-recognize') {
    startSse(res)
    const strokes = Math.max(1, Number(body.strokes) || 1)
    sendSse(res, 'meta', { model: 'mock-hand-latex', strokes, note: '手写公式识别为「演示」：固定样例池，未接入真实识别服务；结果必须经编辑器审查' })
    await sleep(420)
    sendSse(res, 'recognizing', { stage: '演示流程：笔迹分割 → 符号分类 → LaTeX 组装', progress: 40 })
    await sleep(560)
    const pool = [
      'a^{2}+b^{2}=c^{2}',
      '\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1',
      "f'(x)=3x^{2}-2",
      '\\sin^{2}\\theta+\\cos^{2}\\theta=1',
      'e=\\frac{c}{a}',
      'y=2x-\\frac{1}{x}',
      '\\int_{1}^{e}\\frac{1}{x}\\,\\mathrm{d}x=1',
      '\\sqrt{a^{2}+b^{2}}',
    ]
    sendSse(res, 'result', { latex: pool[strokes % pool.length], confidence: 0.82 + (strokes % 5) * 0.03, demo: true })
    await sleep(240)
    sendSse(res, 'done', { editable: true, note: '以上为演示识别结果（非真实识别），请在编辑器中核对修改后使用' })
    res.end()
    return true
  }

  /* ============ butler AI 管家域（悬浮球 chat-to-action） ============ */
  if (method === 'GET' && path === '/teacher-v3/butler/tools') {
    return ok(res, {
      tools: [
        { name: 'search_web', label: '联网搜索', kind: 'read', confirm_required: false, description: '联网检索教学资料，返回带引用的结果' },
        { name: 'kb_search', label: '个人知识库检索', kind: 'read', confirm_required: false, description: '在教师上传的教材/教案/课件中检索' },
        { name: 'quiz_import_question', label: '题目入库', kind: 'write', confirm_required: true, description: '识别/编辑后的题目结构化写入题库（教师确认后执行）' },
        { name: 'navigate', label: '页面跳转', kind: 'frontend', confirm_required: false, description: '跳转到课件工坊/备课中心等页面并预填参数' },
        { name: 'prefill', label: '表单预填', kind: 'frontend', confirm_required: false, description: '在当前页面预填生成表单参数' },
        { name: 'insert_element', label: '插入画布元素', kind: 'frontend', confirm_required: false, description: '把公式/图形卡片插入当前课件页（teacher_confirmed=false）' },
      ],
    }), true
  }

  if (method === 'POST' && path === '/teacher-v3/butler/chat') {
    startSse(res)
    const msg = String(body.message || '')
    const text = msg.toLowerCase()
    const hasImages = Array.isArray(body.images) && body.images.length > 0
    const ctx = body.context || {}
    const sessionId = uid('bs')

    /* 意图路由（P0 规则层演示；real = 规则 + 小模型分类 + 大模型 FC 三层） */
    const wantsDeck = /做.{0,16}?(课件|ppt|演示文稿)|生成.{0,12}?(课件|ppt)|备课/.test(text)
    const wantsQuizImport = (hasImages && /存(入|到).{0,6}(题库|题目|错题)|入库/.test(text)) || (/存(入|到).{0,6}(题库|错题)/.test(text) && hasImages)
    const wantsSearch = body.web_search === true || /联网|搜索|查一查|最新/.test(text)
    const wantsFormula = /根号|平方|分之|阶乘|绝对值|正弦|余弦|正切|对数|极限|导数/.test(msg)
    /* B0 反假执行：插入类跨工件请求不再落到"通用公式卡"兜底（审计反例 #6） */
    const wantsInsert = /插入|放到|放进|加到|添加到/.test(text) && /(课件|第\s*\d+\s*页|页面|画布)/.test(text)
    /* C2：找资源 / 画数学图 → 调用伴随工具（优先级高于做课件：'备课找题'应进资源台而非课件向导） */
    const wantsFindResource = /找.{0,10}(一些|几道|道|个)?题|找(个|点|些)?(素材|资源)|推荐.{0,10}(题|素材|视频)/.test(text)
    const wantsDrawFigure = /画.{0,16}(图|动图|演示)|动态图|构图/.test(text) && !/课件|ppt/.test(text)

    if (wantsQuizImport) {
      /* 剧本B：题目图片 + 存入题库 → 识别预览卡 → 确认动作卡（R2：教师确认后才入库） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'quiz_import', note: '图片已附带，走拍照识别链路' })
      await sleep(300)
      sendSse(res, 'thinking', { text: '先解读题目照片，再生成入库草稿；原图保留对照，入库前由教师确认。' })
      await sleep(260)
      sendSse(res, 'tool_call', { tool: 'photo_recognize', label: '识别题目照片' })
      await sleep(620)
      const kp = /圆锥|椭圆|双曲线|抛物线/.test(msg) ? '圆锥曲线' : /函数|导数/.test(msg) ? '导数及其应用' : /三角/.test(msg) ? '三角函数' : '数列'
      sendSse(res, 'tool_result', { tool: 'photo_recognize', ok: true, summary: `已识别题干与解答（知识点建议：${kp}），识别块均可编辑` })
      await sleep(200)
      sendSse(res, 'card', {
        type: 'action', id: uid('act'), title: '存入题库', confirm_required: true, status: 'pending',
        summary: `将识别的题目写入「${kp}」分类，题干与解析可在题库中继续编辑`,
        params: { kp_name: kp, q_type: 'solve', difficulty: 'medium' },
      })
      await sleep(180)
      sendSse(res, 'token', { text: '已生成入库草稿。请核对右侧动作卡的内容，点击「执行」后才会真正写入题库；原图与识别结果均可在题库中查看修改。' })
    } else if (wantsFindResource) {
      /* C2 剧本：找题/找素材/找资源 → 打开伴随资源台（AI 调用工具，不在聊天里丢题） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'find_resource', note: '资源检索带上下文进伴随资源台，教师确认后才插入' })
      await sleep(280)
      sendSse(res, 'thinking', { text: '找资源应该带着当前课题/环节去伴随资源台，候选有来源和推荐理由，而不是在聊天里丢几道题。' })
      await sleep(240)
      sendSse(res, 'token', { text: '已按当前页面上下文为你准备资源候选：按教学意图分镜头、来源分四层（我的/备课组/教材官方/外部），插入前可预览，插入后可撤销。' })
      await sleep(200)
      sendSse(res, 'card', { type: 'tool', id: uid('tl'), tool: 'resource', title: '打开伴随资源', summary: '按当前上下文找题 / 素材 / 视频卡' })
    } else if (wantsDrawFigure) {
      /* C2 剧本：画图/动态演示 → 打开数学绘图（含构图导演），完成后插回当前工作位 */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'draw_figure', note: '数学绘图是全局工具：任何页面画完插回当前工作' })
      await sleep(280)
      sendSse(res, 'thinking', { text: '数学图形应在绘图工作台里做成结构化对象（函数 / 构图导演 / 手写公式），我可以直接帮你打开。' })
      await sleep(240)
      sendSse(res, 'token', { text: '已打开数学绘图：完成后点「插入」，图形会回到你当前的工作位置；关掉也不丢，会进暂存图形。' })
      await sleep(200)
      sendSse(res, 'card', { type: 'tool', id: uid('tl'), tool: 'draw', title: '打开数学绘图', summary: '函数绘图 / 构图导演 / 手写公式' })
    } else if (wantsDeck) {
      /* 剧本A：我要做PPT → 理解摘要 + 预填跳转（进入既有生成工作流，模板选择/大纲确认保留） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'deck_generate', note: '跳转课件工坊并预填' })
      await sleep(300)
      const topicMatch = msg.match(/[《「“"]([^》」”"]+)[》」”"]/)
      const topic = topicMatch ? topicMatch[1] : msg.replace(/帮|我|请|做|个|一|份|生成|课件|ppt|演示文稿|关于|的|主题/g, '').slice(0, 18) || '椭圆及其标准方程'
      sendSse(res, 'thinking', { text: '生成课件需要教师控制模板与大纲，跳转课件工坊并预填表单最合适。' })
      await sleep(240)
      sendSse(res, 'token', { text: `好的，将前往课件工坊并预填：主题「${topic}」、班级高二(3)班、模板学术蓝。模板与大纲仍由您在工坊内选定确认。` })
      await sleep(220)
      sendSse(res, 'card', {
        type: 'link', id: uid('lnk'), title: `课件工坊 · 主题生成（${topic}）`,
        route: '/teacher-v3/slides', query: { mode: 'topic', topic, class_id: 'c2-03', template_id: 'tpl-academic-blue' },
        note: '预填后仍需您确认模板与大纲',
      })
      await sleep(160)
      sendSse(res, 'action', { action: 'navigate', route: '/teacher-v3/slides', query: { mode: 'topic', topic, class_id: 'c2-03', template_id: 'tpl-academic-blue' }, toast: `已预填主题「${topic}」` })
    } else if (wantsInsert) {
      /* 跨工件插入：诚实说明当前边界 + 给出可达路径，不伪造插入结果（回执与真实执行在 B6 实现） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'insert_request', note: '跨工件操作在原型阶段的边界说明' })
      await sleep(280)
      const pageM = msg.match(/第\s*(\d+)\s*页/)
      const target = pageM ? `第 ${pageM[1]} 页` : '目标页'
      sendSse(res, 'thinking', { text: `插入到课件${target}需要"目标课件 + 页码 + 内容"三者都明确，当前会话缺少已打开的课件上下文。` })
      await sleep(220)
      sendSse(res, 'token', { text: `这个插入操作我暂时不能直接执行（需要目标课件上下文，完整执行与回执在管家动作版接入）。现在可以：① 打开目标课件后，在资源中心对该素材点「插入课件」并选页；② 或把公式/图形卡从本面板直接拖入画布。${pageM ? `已记住你要的是${target}。` : ''}` })
      await sleep(180)
      sendSse(res, 'card', { type: 'link', id: uid('lnk'), title: '课件工坊 · 打开目标课件后可插入', route: '/teacher-v3/slides', query: {}, note: '在编辑器内通过「资源/管家卡片拖入」完成插入' })
    } else if (wantsSearch) {
      sendSse(res, 'meta', { session_id: sessionId, intent: 'web_search' })
      await sleep(280)
      sendSse(res, 'tool_call', { tool: 'search_web', label: '联网检索' })
      await sleep(560)
      sendSse(res, 'tool_result', { tool: 'search_web', ok: true, summary: '检索到 3 条相关来源' })
      await sleep(200)
      sendSse(res, 'token', { text: '结合检索结果：椭圆的第一定义为平面上到两定点距离之和为常数（大于两定点间距）的点的轨迹，人教A版教材同时给出第二定义（焦点-准线）。建议课堂用绳长实验引入第一定义，再从第二定义过渡到离心率。' })
      await sleep(240)
      sendSse(res, 'citation', { sources: [
        { index: 1, title: '人教A版选择性必修一 · 2.2 椭圆', url: 'https://www.pep.com.cn/gzsx/xrjdgzsx/ssl', snippet: '教材原文：平面内与两个定点F₁、F₂的距离的和等于常数…' },
        { index: 2, title: '课程标准（2017版2020修订）· 圆锥曲线', url: 'http://www.moe.gov.cn', snippet: '经历从具体情境中抽象出椭圆的过程…' },
      ] })
    } else if (wantsFormula) {
      /* 数学口语直接出公式卡（与语音公式链路同一解析层） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'formula_parse' })
      await sleep(260)
      const latex = voiceToLatex(msg)
      sendSse(res, 'token', { text: '识别为如下公式，可拖入课件或点开编辑：' })
      await sleep(200)
      sendSse(res, 'card', { type: 'formula', id: uid('fml'), latex, confidence: 0.88, source: 'chat' })
    } else {
      /* 默认：诚实的能力边界说明（B0 反假执行：不用固定椭圆问答冒充任意问题的回答） */
      sendSse(res, 'meta', { session_id: sessionId, intent: 'fallback' })
      await sleep(280)
      sendSse(res, 'thinking', { text: ctx.route_title ? `结合当前页面（${ctx.route_title}）判断意图。` : '判断意图。' })
      await sleep(200)
      const reply = `我是原型阶段的规则引擎（未接入大模型），当前能可靠执行这些事：①「帮我做一份《××》课件」→ 预填跳转工坊；② 口述公式（说"根号下 x 加一"）；③ 附题目照片说「存入题库」；④ 联网检索演示。数学问答与跨工件插入将在真实服务接入后开放——我不想用编造的回答占用你的时间。`
      for (const seg of reply.match(/[\s\S]{1,14}/g) || []) {
        sendSse(res, 'token', { text: seg })
        await sleep(90)
      }
    }
    await sleep(160)
    sendSse(res, 'done', { finish_reason: 'stop' })
    res.end()
    return true
  }

  if (method === 'POST' && path === '/teacher-v3/butler/voice-formula') {
    /* 语音公式链（P0：text 模拟语音；P1 挂真 ASR）：
       asr_partial*（教师能看到 AI 听到了什么）→ asr_final → card(formula) → done。
       解析三层 = 规则引擎（下述映射）→ LLM（real）→ KaTeX 编译验证；结果必须进编辑器审查（R2）。 */
    startSse(res)
    const spoken = String(body.text || '')
    sendSse(res, 'meta', { session_id: uid('bs'), intent: 'voice_formula', note: 'P0 文本模拟语音输入；P1 接入讯飞/Whisper ASR' })
    const chunks = spoken.match(/[\s\S]{1,6}/g) || []
    for (const c of chunks) {
      await sleep(150)
      sendSse(res, 'asr_partial', { text: c })
    }
    await sleep(180)
    sendSse(res, 'asr_final', { text: spoken })
    await sleep(260)
    const latex = voiceToLatex(spoken)
    const alts = latex.includes('sqrt') ? [latex.replace(/\\sqrt\{([^{}]+)\}/, '$1'), latex] : [latex]
    sendSse(res, 'card', { type: 'formula', id: uid('fml'), latex, confidence: 0.86, alternatives: alts, source: 'voice' })
    await sleep(160)
    sendSse(res, 'done', { finish_reason: 'stop', note: '识别结果可编辑可拖拽，未直接定稿' })
    res.end()
    return true
  }

  const butlerConfirmMatch = path.match(/^\/teacher-v3\/butler\/actions\/([^/]+)\/confirm$/)
  if (method === 'POST' && butlerConfirmMatch) {
    /* 写动作确认（R 红线：教师点了执行才入库；这里复用题库 quizQuestions 的内存态）。
       B0 诚实标注：样例题为演示数据，非真实识别结果。 */
    const kp = String((body.params as Record<string, unknown>)?.kp_name || '圆锥曲线')
    const q = {
      id: uid('q'),
      kp_name: kp,
      kp_code: 'CV-01',
      kp_path: ['数学', '圆锥曲线'],
      q_type: 'solve',
      difficulty: 'medium',
      stem_latex: '\\text{（演示数据）设椭圆}\\frac{x^{2}}{4}+\\frac{y^{2}}{3}=1\\text{的左右焦点为}F_1,F_2\\text{，过}F_1\\text{的直线交椭圆于}A,B\\text{，求}\\triangle ABF_2\\text{的周长。}',
      answer: '8',
      analysis: '由椭圆定义 |AF₁|+|AF₂|=2a=4，|BF₁|+|BF₂|=4，周长=8。（本题为原型演示样例，非真实识别结果）',
      source: '拍照入库',
      year: '2026',
      use_count: 0,
      folder_refs: [],
    }
    quizQuestions.unshift(q)
    tasks.unshift({ task_id: uid('task'), title: `原型演示 · 管家入库（${kp}）`, capability: 'butler', status: 'succeeded', progress: 100, stage: '完成（演示数据）' })
    return ok(res, { ok: true as const, result: { question_id: q.id, kp_name: kp, demo: true } }), true
  }

  /* ============ grading 批改域 ============ */
  if (method === 'GET' && path === '/teacher-v3/grading/assignments') {
    /* B4：新发布实例排前（待提交态），示例数据殿后并标注 */
    const items = [...assignments].sort((a) => (a.id.startsWith('ga-pub') ? -1 : 1))
    return ok(res, { items }), true
  }
  const gaMatch = path.match(/^\/teacher-v3\/grading\/assignments\/([^/]+)$/)
  if (method === 'GET' && gaMatch) {
    if (publishedDetails[gaMatch[1]]) return ok(res, publishedDetails[gaMatch[1]]), true
    return ok(res, grading), true
  }
  /* B4：发布作业为实例（演示：未真实发送学生端） */
  if (method === 'POST' && path === '/teacher-v3/assignments/publish') {
    const id = uid('ga-pub')
    const cls = V3_CLASSES.find((c) => c.class_id === body.class_id) || V3_CLASSES[0]
    const questions = (Array.isArray(body.questions) ? body.questions : []).map((q: any, i: number) => ({
      q_no: i + 1,
      stem_latex: q.stem_latex || '',
      full_score: Number(q.full_score) || 5,
      answer: q.answer || '',
      kp_name: q.kp_name || '',
      standard_answer: q.answer || '待补标准答案',
      rubric: [{ point: '按步骤给分（原型默认评分点，可在批改时调整）', score: Number(q.full_score) || 5 }],
      accuracy: 0, error_dist: [], clusters: [],
    }))
    const inst = {
      id, title: String(body.title || '未命名作业'), class_id: cls.class_id, class_name: cls.name,
      submitted: 0, total: cls.students, graded: 0, updated_at: new Date().toISOString().slice(0, 10),
      source: 'published' as const, deadline: body.deadline || '', answer_policy: body.answer_policy || 'manual',
      allow_photo: body.allow_photo !== false, status: 'collecting' as const, is_sample: false,
    }
    assignments.unshift(inst)
    publishedDetails[id] = {
      id, title: inst.title, class_id: cls.class_id, submitted: 0, total: cls.students, graded: 0,
      deadline: inst.deadline, answer_policy: inst.answer_policy, status: 'collecting',
      questions,
      tiers: [],
    }
    return ok(res, { ok: true as const, assignment_id: id, demo: true, note: '演示：作业实例已创建（mock 内存态），未真实发送学生端' }), true
  }
  /* B4：模拟学生提交（确定性演示数据，明确标注非真实学生）→ 生成聚类 fixture */
  const simMatch = path.match(/^\/teacher-v3\/grading\/assignments\/([^/]+)\/simulate-submissions$/)
  if (method === 'POST' && simMatch) {
    const inst = assignments.find((a) => a.id === simMatch[1])
    const detail = publishedDetails[simMatch[1]]
    if (!inst || !detail) return fail(res, 404, 40402, '作业不存在'), true
    const NAMES = ['王雨桐', '陈子豪', '刘一鸣', '林小满', '赵启铭', '孙浩然', '周可欣', '吴宇轩', '郑好', '冯天佑', '何雨欣', '李嘉明']
    for (const q of detail.questions) {
      const full = q.full_score
      const good = NAMES.slice(0, 6).map((n) => ({ name: n, score: full }))
      const mid = NAMES.slice(6, 10).map((n) => ({ name: n, score: Math.max(1, Math.floor(full * 0.6)) }))
      const bad = NAMES.slice(10).map((n) => ({ name: n, score: Math.max(0, Math.floor(full * 0.3)) }))
      q.clusters = [
        { id: uid('c'), kind: 'correct', count: good.length, members: good, sample: [{ student: good[0].name, score: full, photo_region: { x: 0.1, y: 0.1, w: 0.5, h: 0.3 }, recognized_steps: steps([['（演示作答）按标准答案完整完成', 'ok']]) }] },
        { id: uid('c'), kind: 'partial', tag: '步骤缺失', count: mid.length, members: mid, sample: [{ student: mid[0].name, score: mid[0].score, photo_region: { x: 0.1, y: 0.45, w: 0.5, h: 0.3 }, recognized_steps: steps([['（演示作答）前两步正确', 'ok'], ['最后一步跳步（演示分歧）', 'ai-flag']]), feedback: 'AI 起草：末步骤缺失，按评分点扣后两步分' }] },
        { id: uid('c'), kind: 'wrong', tag: '概念混淆', count: bad.length, members: bad, sample: [{ student: bad[0]?.name || NAMES[10], score: bad[0]?.score ?? 1, photo_region: { x: 0.1, y: 0.75, w: 0.5, h: 0.2 }, recognized_steps: steps([['（演示作答）思路偏离标准解法', 'ai-flag']]), feedback: 'AI 起草：方法选择错误，建议对照标准答案重讲' }] },
      ]
      q.accuracy = 0.5
      q.error_dist = [{ tag: '步骤缺失' as const, count: mid.length }, { tag: '概念混淆' as const, count: bad.length }]
    }
    inst.submitted = detail.submitted = inst.total - 2
    inst.status = detail.status = 'grading'
    return ok(res, { ok: true as const, demo: true, note: '已注入确定性演示作答（非真实学生数据）' }), true
  }
  const clusterMatch = path.match(/^\/teacher-v3\/grading\/assignments\/([^/]+)\/clusters\/([^/]+)\/confirm$/)
  if (method === 'POST' && clusterMatch) {
    /* B4：教师确认即终审。body 可携带 reviews（逐步骤判定 + 最终得分），全部存 mock 内存态 */
    const sets: any[] = [grading, ...Object.values(publishedDetails)]
    for (const g of sets) {
      for (const q of g.questions) {
        const c = q.clusters.find((x: { id: string }) => x.id === clusterMatch[2])
        if (c) {
          for (const s of c.sample) s.feedback = body.feedback
          ;(c as any).confirmed = { at: new Date().toISOString().slice(0, 16).replace('T', ' '), reviews: body.reviews || [] }
          if (publishedDetails[clusterMatch[1]]) {
            const det = publishedDetails[clusterMatch[1]]
            det.graded = Math.min(det.submitted, (det.graded || 0) + (c.count || 1))
            const inst = assignments.find((a) => a.id === clusterMatch[1])
            if (inst) inst.graded = det.graded
          }
        }
      }
    }
    return ok(res, { ok: true as const }), true
  }
  const reviewMatch = path.match(/^\/teacher-v3\/grading\/assignments\/([^/]+)\/review-pack$/)
  if (method === 'POST' && reviewMatch) {
    startSse(res)
    sendSse(res, 'meta', { assignment_id: reviewMatch[1], questions: grading.questions.length })
    await sleep(380)
    for (let i = 0; i < grading.questions.length; i++) {
      const q = grading.questions[i]
      await sleep(480)
      sendSse(res, 'slide', {
        index: i, q_no: q.q_no, accuracy: q.accuracy,
        top_error: q.error_dist[0]?.tag || '无',
        clusters: q.clusters.map((c: any) => ({ id: c.id, kind: c.kind, count: c.count })),
      })
    }
    await sleep(240)
    /* B4：讲评课件为真实 Artifact（改写为讲评页结构），可从回执直接打开 */
    const srcDeck = decks.find((d) => d.id === 'deck-photo') || decks[0]
    const deck = JSON.parse(JSON.stringify(srcDeck))
    deck.id = uid('deck-review')
    deck.title = '讲评 · ' + grading.title
    deck.source = 'review-notes'
    deck.slides = [
      {
        id: uid('rv'), layout: 'cover' as const, elements: [
          { id: uid('e'), z: 1, type: 'text', left: 90, top: 210, width: 800, height: 80, html: '讲评 · ' + grading.title, font_size: 40, bold: true, color: '#1e3a2f' },
          { id: uid('e'), z: 2, type: 'text', left: 92, top: 306, width: 620, height: 40, html: '按错误率排序 · 每题：错因分布 → 第一处分歧 → 变式', font_size: 18, color: '#4a5568' },
        ],
      },
      ...grading.questions.map((q: any, i: number) => ({
        id: uid('rv'), layout: 'example' as const, anchor_bar: i === 0 ? '' : '接上页 · 第 ' + q.q_no + ' 题',
        elements: [
          { id: uid('e'), z: 1, type: 'text', left: 70, top: 44, width: 700, height: 46, html: '第 ' + q.q_no + ' 题 · 正确率 ' + Math.round(q.accuracy * 100) + '%', font_size: 26, bold: true, color: '#1e3a2f' },
          { id: uid('e'), z: 2, type: 'text', left: 70, top: 110, width: 1060, height: 60, html: renderRichSafe(q.stem_latex), font_size: 20 },
          { id: uid('e'), z: 3, type: 'text', left: 70, top: 190, width: 1060, height: 60, html: '<b>标准答案：</b>' + renderRichSafe(q.standard_answer || '见解析'), font_size: 17, color: '#0e9488' },
          { id: uid('e'), z: 4, type: 'text', left: 70, top: 270, width: 1060, height: 60, html: '<b>主错因：</b>' + ((q.error_dist[0] || {}).tag || '—') + '（' + ((q.error_dist[0] || {}).count || 0) + ' 人）· 第一处分歧见批改记录', font_size: 16, color: '#b45309' },
          { id: uid('e'), z: 5, type: 'text', left: 70, top: 350, width: 1060, height: 60, html: '<b>变式建议：</b>按错因生成变式（错因驱动例题链，后续批次接入）', font_size: 15, color: '#64748b' },
        ],
      })),
    ]
    deck.updated_at = new Date().toISOString().slice(0, 16).replace('T', ' ')
    decks.unshift(deck)
    const topError = grading.questions.reduce((a: any, q: any) => (((q.error_dist[0] || {}).count || 0) > ((a.error_dist && a.error_dist[0] ? a.error_dist[0].count : 0)) ? q : a), grading.questions[0])
    sendSse(res, 'done', { deck_id: deck.id, slides: deck.slides.length, top_error: (topError.error_dist[0] || {}).tag || '', top_q: topError.q_no })
    res.end()
    return true
  }

  return false
}
