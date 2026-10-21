/**
 * 教师工作台 V3 · 数据契约（前端唯一类型来源）
 * 依据：d:\teacher-v2-agents\artifacts\02-prototype-v21\SPEC.md §5.4 §6 §7 §9
 * 后端约定：mock 挂载 /api/teacher-v3/*，按四个后端域分节——
 *   catalog（基础目录）/ recognition（识别）/ generation（AI 生成，SSE）/ figures（图形）/ grading（批改）
 * 前端所有页面禁止私造平行结构，一律 import 本文件。
 */

/* ============ 课件 deck（SPEC §5.4） ============ */

export type V3SlideLayout =
  | 'cover' | 'review' | 'definition' | 'derivation' | 'example' | 'variation'
  | 'summary' | 'solution-flow' | 'keypoints' | 'blank'

export interface V3Deck {
  id: string
  title: string
  template_id: string
  source: 'topic' | 'photo' | 'lesson-push' | 'review-notes'
  photo_context?: V3PhotoIngestContext
  /** C1 AI 备课台：生成时的接地上下文（课型/章节/材料名），编辑器顶栏回显（IFC-C1-a，PROTOTYPE-ONLY） */
  brief_context?: V3DeckBriefContext
  slides: V3Slide[]
  updated_at: string
}

/** C1：AI 备课台 → 课件工坊的接入载荷（前端 view model，随 IFC-C1-a 评审） */
export interface V3BriefPayload {
  text: string
  /** 教材章节完整路径（如「选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程」），未选为 null */
  chapter: string | null
  /** 课型：决定大纲环节结构（新授课/习题课/讲评课/复习课/公开课） */
  course_type: string
  class_id: string
  template_id: string
  /** 附带文档的文件名（原型不解析，仅记录；解析属后端 M2） */
  docs: string[]
  /** 附带照片（dataURL，进拍照出课件链路） */
  photos: string[]
}

/** C1：deck 上的接地上下文回显 */
export interface V3DeckBriefContext {
  course_type: string
  chapter: string
  material: string
}

export interface V3Slide {
  id: string
  layout: V3SlideLayout
  elements: V3Element[]
  notes?: string
  anchor_bar?: string        // 分页续页锚条："接上页 S=½·|AB|·d"
  fill_rate?: number         // 0-1 装填率（分页引擎写入）
}

export interface V3ElementBase {
  id: string
  left: number               // 1280×720 逻辑坐标
  top: number
  width: number
  height: number
  z: number
  teacher_confirmed?: boolean
}

export type V3Element =
  | (V3ElementBase & { type: 'text'; html: string; font_size: number; color?: string; bold?: boolean })
  | (V3ElementBase & { type: 'formula'; latex: string; font_size: number; display?: boolean })
  | (V3ElementBase & { type: 'geometry'; preset_id: string; params: Record<string, number>; toggles?: Record<string, boolean>; recipe_id?: string })
  | (V3ElementBase & { type: 'functionPlot'; expr: string; params: Record<string, { value: number; min: number; max: number; step: number }>; domain: [number, number]; live_sliders?: boolean })
  | (V3ElementBase & { type: 'dynamicDemo'; demo_id: string; caption?: string })
  | (V3ElementBase & { type: 'image'; src: string; alt?: string; draw_recipe?: V3DrawRecipe })
  | (V3ElementBase & { type: 'anchorPhoto'; src: string; upgrade_state: 'none' | 'library' | 'rebuilt' | 'demo'; upgrade_target?: string })
  | (V3ElementBase & { type: 'pageNo'; no: number })

/* ============ 绘图工作台 DrawBoard（P1） ============ */

/** 自由画布的结构化笔迹配方：可整体重开再编辑（R1 编辑性的落地形态） */
export interface V3DrawRecipe {
  records: V3DrawRecord[]
  boundingbox?: [number, number, number, number]
}

export interface V3DrawRecordBase {
  id: string
  color: string
  width: number
  /** 整体平移量（preset 图形结构复杂，移动走 offset；其余 kind 直接改写坐标） */
  offset?: [number, number]
}

/** 自由画布元素记录：pen 手绘 / line 直线 / circle 圆 / polygon 多边形 / point 点 / text 标注 / preset 规整图形 */
export type V3DrawRecord =
  | (V3DrawRecordBase & { kind: 'pen'; pts: [number, number][] })
  | (V3DrawRecordBase & { kind: 'line'; a: [number, number]; b: [number, number] })
  | (V3DrawRecordBase & { kind: 'circle'; c: [number, number]; r: number })
  | (V3DrawRecordBase & { kind: 'polygon'; verts: [number, number][] })
  | (V3DrawRecordBase & { kind: 'point'; pos: [number, number] })
  | (V3DrawRecordBase & { kind: 'text'; pos: [number, number]; text: string })
  | (V3DrawRecordBase & { kind: 'preset'; preset_id: string; params: Record<string, number>; toggles?: Record<string, boolean> })
  /** 立体几何构造文档（geom 模块 GeomDoc 快照；doc 结构见 components/mathx/geom/model.ts） */
  | (V3DrawRecordBase & { kind: 'geomdoc'; doc: unknown })

/** 图形库条目（教师个人 + 教研组共享，SPEC §5.7 配方共享的扩展） */
export interface V3FigureLibraryItem {
  id: string
  name: string
  kind: 'free' | 'fx'
  author: string
  shared: boolean
  updated_at: string
  thumb: string                 // SVG 快照
  records?: V3DrawRecord[]      // kind=free：结构化配方
  expr?: string                 // kind=fx：函数表达式
}

/* ============ 课件模板（生成前选择，SPEC §5.2/§5.9） ============ */

export interface V3DeckTemplate {
  id: string
  name: string
  style: 'academic' | 'chalkboard' | 'geometric' | 'classic' | 'minimal'
  swatch: { bg: string; primary: string; accent: string; light: boolean }
  page_kinds: string[]       // 该模板覆盖的版式页
  recommended_for: string
  sample_topic: string       // 自渲染小样的示例课题
}

/* ============ 拍照链路（SPEC §5.10/§5.11） ============ */

export type V3PhotoScope = 'stem' | 'stem+solution' | 'stem+keypoints'
export type V3PhotoMode = 'blank-board' | 'full-solution' | 'keypoints'
export type V3FontTier = 'compact' | 'standard' | 'large'

export interface V3PhotoIngestConfig {
  scope: V3PhotoScope
  mode: V3PhotoMode
  template_id: string
  font_tier: V3FontTier
  margin_notes: boolean
}

export interface V3PhotoIngestContext {
  config: V3PhotoIngestConfig
  photos: number
  question_label: string
}

export interface V3RecognizeBlock {
  type: 'stem' | 'sub' | 'figure' | 'solution-step'
  latex?: string
  text?: string
  image_region?: { x: number; y: number; w: number; h: number }   // 原图归一化区域
  confidence: number
  editable: boolean          // R2：识别结果可编辑
}

export interface V3RecognizePageResult {
  page_id: string
  blocks: V3RecognizeBlock[]
  figure_suggestion?: { preset_id: string; reason: string }       // 图形库近模板推荐
}

/* ============ 图形（preset / 重建 / 配方，SPEC §5.6/§5.7） ============ */

export type V3FigureCategory = 'solid' | 'conic' | 'function' | 'plane' | 'stat'

export interface V3ParamSpec {
  key: string
  label: string
  min: number
  max: number
  step: number
  def: number
  unit?: string
}

export interface V3ToggleSpec {
  key: string
  label: string
  def: boolean
}

export interface V3FigurePreset {
  id: string                     // 'conic/ellipse'
  category: V3FigureCategory
  name: string
  desc: string
  params: V3ParamSpec[]
  toggles?: V3ToggleSpec[]
  /** 构造难度：L1 模板参数化 / L2 构造式（含截面） */
  level: 1 | 2
}

export interface V3FigureRebuildCandidate {
  id: string
  preset_id?: string
  params: Record<string, number>
  passed_validation: boolean     // 构造校验门（SPEC §5.7）：不过不上屏
  match_note: string
}

export interface V3Recipe {
  id: string
  name: string
  category: V3FigureCategory
  author: string
  school_shared: boolean
  params: V3ParamSpec[]
  usage_count: number
  note: string
}

/* ============ 教案（SPEC §6） ============ */

/** 教师上传件的质量体检（RESEARCH_LESSON_PREP §5.3）：反套话 + 栏目完整度，决定「值不值得当模板」 */
export interface V3TemplateQualityReport {
  file: string
  score: number                 // 0-100
  board_coverage: string[]      // 齐全的栏目
  missing_boards: string[]      // 缺失栏目
  cliche_hits: string[]         // 套话命中
  recommended: boolean          // 是否建议作为模板
  suggestion: string
}

export interface V3LessonTemplate {
  id: string
  name: string
  style_tag: string
  sections: string[]            // 环节 schema
  recommended_for: string
  sample_topic: string
  /** V3.1：模板来源——内置标准 / 教师上传提炼（个人知识库档，仅本人可见） */
  source?: 'builtin' | 'teacher_upload'
  /** 教师上传件的体检报告（source=teacher_upload 时给出） */
  quality_report?: V3TemplateQualityReport
}

/** 教案生成五件套（V3.1：消灭「一句话黑盒」，RESEARCH_LESSON_PREP §5.1） */
export interface V3PlanGenForm {
  topic: string
  class_id: string
  lesson_type: string
  template_id: string
  textbook_version?: string     // 教材版本（人教A版 / 北师大版 / 苏教版…）
  chapter?: string              // 章节（级联选择：模块 ▸ 章 ▸ 知识点）
  lesson_no?: number            // 课时序号
  duration?: number             // 一节课时长（40/45）→ 环节分钟预算
  key_points?: string           // 想强调的重难点（选填）
  extra_requirements?: string   // 补充要求（选填：情境导入 / 只讲第 1 课时…）
  example_source?: 'bank' | 'ai'  // 例题来源：题库检索优先 / AI 配题兜底
}

/** 大纲环节（两段式第一段产物，教师可增删环节 / 调时长 / 改目标） */
export interface V3OutlineSection {
  id: string
  name: string
  minutes: number
  goal?: string                 // 本环节目标草案（可编辑）
  example_suggestion?: string   // 例题建议（attachable 板块）
}

export interface V3PlanOutline {
  topic: string
  duration: number
  sections: V3OutlineSection[]
  total_minutes: number
  notes?: string[]               // 生成依据说明（教材版本 / 章节 / 学情锚点 / 例题来源）
  /** P 大纲确认页富模块（确定性 mock 编排，教师可改；IFC-P-a） */
  objectives?: string[]
  keypoints?: { major: string[]; hard: string[] }
  blackboard?: { main: string[]; side: string[] }
  homework?: { tier: 'basic' | 'raise' | 'expand'; label: string; items: string[]; minutes: string }[]
  difficulty?: string
}

/** 板块上"挂"的例题：结构化数学题（P3），题干 LaTeX 可编辑、可复用、可进试卷 */
export interface V3AttachedExample {
  id: string
  label: string                 // 例 1 / 变式 2 / 作业 A-1
  q_type: 'choice' | 'fill' | 'solve'
  difficulty: 'easy' | 'medium' | 'hard'
  stem_latex: string
  options?: string[]
  answer: string
  /** 答案解析（题库题自带；扫描题可后补） */
  analysis?: string
  /** 知识点（分组展示与筛选用） */
  kp_name?: string
  source?: string               // 校本 / 区库 / 拍照入库
  /** 答案随题挂载开关：教师版含答案，学生版/课堂任务侧按答案策略控制可见 */
  include_answer?: boolean
}

/**
 * 教案板块（P3 重构：由模板环节 → 十板块框架）。
 * teacher_activity / student_activity / design_intent 保留原三栏；
 * 新增 examples（挂例题）与 cliche（反套话）字段，均可选以兼容旧数据。
 */
export interface V3PlanSection {
  id: string
  name: string                  // 板块名（十板块之一）
  minutes: number
  teacher_activity: string      // 允许内联 $..$ 公式
  student_activity: string
  design_intent: string
  confirmed?: boolean
  /** 挂例题：结构化题目列表（例题/变式/作业板块常见） */
  examples?: V3AttachedExample[]
  /** 反套话：本板块内容是否套话过重 */
  cliche?: boolean
  /** 命中的套话明细（供教师一眼看清哪里空） */
  cliche_hits?: string[]
  /** 拍照填充（R8 原图在场）：原图缩略图锚定 + 识别是否已一次性填充三栏 */
  photo?: {
    src: string
    filled?: boolean
  }
}

export interface V3LessonPlan {
  id: string
  topic: string
  class_id: string
  lesson_type: string
  template_id: string
  objectives: string[]
  key_points: string[]
  sections: V3PlanSection[]
  board_design_note: string
  homework_tiers: { tier: string; items: string[] }[]
  refs: string[]
}

/* ============ 批改三视图（SPEC §7） ============ */

export type V3ErrorTag = '计算错误' | '概念混淆' | '步骤缺失' | '审题错误' | '方法选择' | '表达不规范'
export type V3GradingView = 'byQuestion' | 'byStudent' | 'byTier'

export interface V3GradingQuestion {
  q_no: number
  stem_latex: string            // 题干（MathField 可编辑）
  full_score: number
  accuracy: number              // 0-1
  error_dist: { tag: V3ErrorTag; count: number }[]
  clusters: V3AnswerCluster[]
}

export interface V3AnswerCluster {
  id: string
  kind: 'correct' | 'partial' | 'wrong' | 'blank'
  tag?: V3ErrorTag
  count: number
  sample: {
    student: string
    score: number
    photo_region: { x: number; y: number; w: number; h: number }
    recognized_steps: { latex: string; status: 'ok' | 'ai-flag' | 'corrected' }[]
    feedback?: string
  }[]
}

export interface V3GradingTier {
  tier: 'A' | 'B' | 'C'
  label: string
  students: { name: string; avg: number; weak_tags: V3ErrorTag[] }[]
  accuracy: number
}

export interface V3GradingAssignment {
  id: string
  title: string
  class_id: string
  submitted: number
  total: number
  graded: number
  questions: V3GradingQuestion[]
  tiers: V3GradingTier[]
}

/* ============ 工作台 / 目录 ============ */

export interface V3TodayData {
  teacher: { name: string; subject: string; grade_group: string }
  schedule: { time: string; class_name: string; topic: string; status: 'done' | 'next' | 'later'; missing?: string[] }[]
  todos: { id: string; time: string; text: string; kind: 'grade' | 'prep' | 'review' | 'meeting' }[]
  class_brief: { class_id: string; name: string; avg: number; submit_rate: number; trend: number[]; weak_kp: string }[]
}

export interface V3ClassInfo { class_id: string; name: string; students: number }

export interface V3Task {
  task_id: string
  title: string
  capability: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  progress: number
  stage: string
}

/* ============ AI 元素级 diff（R5：AI 只出草稿不覆写） ============ */

export interface V3ElementDiff {
  slide_id: string
  element_id?: string
  op: 'add' | 'modify' | 'remove'
  after?: V3Element
  reason?: string
}

/* ============ AI 管家 butler（悬浮球 + 侧边栏，chat-to-action） ============ */

/** 前端自动采集的页面上下文（Copilot 式自动附加；上下文条可视，教师知道 AI 看到了什么） */
export interface V3ButlerContext {
  route: string                       // 当前路由，如 /teacher-v3/slides
  route_title?: string                // 页面名，如 课件工坊
  deck_id?: string                    // 当前打开的课件
  slide_index?: number                // 当前页
  selection?: { type: V3Element['type']; summary: string }   // 当前选中元素摘要
  class_id?: string
}

/** 对话输入：文本 + 可选附件（图片 dataURL / 文件名） + 两个能力开关 */
export interface V3ButlerChatInput {
  message: string
  context: V3ButlerContext
  images?: string[]                   // 题目照片等（dataURL）
  files?: string[]                    // 文件名列表（PDF/Word/PPT）
  web_search?: boolean
  kb_search?: boolean
}

/** 会话消息（前端渲染模型：正文气泡 + 任意数量卡片） */
export interface V3ButlerMessage {
  id: string
  role: 'teacher' | 'butler'
  text?: string                       // markdown（$..$ 公式内联）
  cards?: V3ButlerCard[]
  citations?: V3ButlerCitation[]
  pending?: boolean                   // 流式接收中
}

/** 卡片五类：公式/图形可拖拽进画布，action/link 为动作确认卡，tool 为打开伴随工具卡（C2，IFC-C2-a） */
export type V3ButlerCard =
  | { type: 'formula'; id: string; latex: string; confidence: number; alternatives?: string[]; source: 'voice' | 'photo' | 'chat' }
  | { type: 'figure'; id: string; preset_id: string; params: Record<string, number>; label: string }
  | { type: 'tool'; id: string; tool: 'resource' | 'draw'; title: string; summary?: string }
  | { type: 'action'; id: string; title: string; summary: string; params?: Record<string, unknown>; status: 'pending' | 'executed' | 'cancelled'; confirm_required: true }
  | { type: 'link'; id: string; title: string; route: string; query?: Record<string, string>; note?: string }

export interface V3ButlerCitation {
  index: number
  title: string
  url: string
  snippet?: string
}

/** 后端下发的结构化前端动作（前端 Action Registry 执行） */
export interface V3ButlerAction {
  action: 'navigate' | 'prefill' | 'insert'
  route?: string
  query?: Record<string, string>
  element?: V3Element                 // insert：落布元素（teacher_confirmed=false）
  toast?: string
}

/** 语音公式链专用（POST /butler/voice-formula SSE） */
export interface V3VoiceFormulaInput {
  text?: string                       // P0 原型：文本模拟语音输入（"负b加减根号下…"）
  audio?: string                      // P1：音频 dataURL
  context: V3ButlerContext
}

/** butler SSE 事件（复用 v3Sse 通道，与 photo-ingest/generation 同构） */
export type V3ButlerSseEvent =
  | { event: 'meta'; data: { session_id: string; intent: string; note?: string } }
  | { event: 'thinking'; data: { text: string } }
  | { event: 'token'; data: { text: string } }
  | { event: 'tool_call'; data: { tool: string; label: string } }
  | { event: 'tool_result'; data: { tool: string; ok: boolean; summary: string } }
  | { event: 'card'; data: V3ButlerCard }
  | { event: 'action'; data: V3ButlerAction }
  | { event: 'citation'; data: { sources: V3ButlerCitation[] } }
  | { event: 'asr_partial'; data: { text: string } }
  | { event: 'asr_final'; data: { text: string } }
  | { event: 'done'; data: { finish_reason?: string } }
