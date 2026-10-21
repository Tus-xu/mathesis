/**
 * 备课中心 · 可见文案与错误映射（唯一来源）。
 *
 * 铁律（LEAD-BAR §1.3 / §2.2 / §3.1 / VISUAL-SPEC-V3 §1.4）：
 * - 所有上屏文案为**中文标签**，禁止英文码（classroom/ppt/plan 只允许出现在 URL）；
 * - 禁止把后端错误原文/内部修订标记等工程口径直接上屏；统一经 mapErrorText 归一；
 * - 文案集中此处，组件内不再散落字面量。
 */
import { PrepCenterApiError, type PrepCenterErrorKind } from '@/features/teacher-prep-center/api/prepCenterApi'

/* ── 首页（S1 · VISUAL-SPEC-V3 §3）────────────────────────────────── */
export const HOME = {
  /** 页标题行 */
  pageTitle: '备课中心',
  pageSubtitle: 'AI 助力，让备课更高效',
  topResources: '教学资源库',
  topRecent: '最近使用',
  /** NL 主输入占位（基线文案） */
  placeholder: '告诉 AI 你今天要准备什么课...',
  /** 「从教材开始」预填语句（落进真实 NL 链路，非装饰） */
  prefillChapter: '从教材开始：请帮我定位要备课的教材章节',
  /** 四个入口卡（每卡必须真实可用；不可用者整卡不渲染；固定 4 卡 = golden2 口径，
   *  基线裁决 3：第 3 卡「从已有PPT开始」按 Golden 恢复，「继续备课」归列表行动作） */
  entryFromTextbook: '从教材开始',
  entryFromTextbookDesc: '选择教材版本与章节，AI 生成备课方案',
  entryFromPlan: '从已有教案开始',
  entryFromPlanDesc: '上传或选择已有教案，快速生成 PPT',
  entryFromPpt: '从已有PPT开始',
  entryFromPptDesc: '上传课件，AI 分析并生成教学设计',
  /** 「从已有PPT开始」预填语句（落进真实 NL 创建链路，非装饰按钮） */
  prefillFromPpt: '从已有PPT开始：我已有一份 PPT 课件，请帮我基于课件生成教学设计',
  entryHistory: '备课历史',
  entryHistoryDesc: '回顾与继续最近的备课',
  /** 列表行中部「两句灰色描述」诚实回退（golden2 列表行七要素之第 3 要素；
   *  只用真实字段派生，禁止编造教学内容） */
  descKnowledgePrefix: '知识点：',
  descScopePrefix: '备课范围：',
  /** 真实描述不足 2 句时，由真实课题名生成的一句摘要（非虚构教学内容） */
  descSummary: (title: string) => `围绕「${title}」展开备课`,
  /** 末位回退占位（R4afix2 精致化）：不用「暂无描述」这类生硬文本块，
   *  改为与整体视觉语言一致的极简占位破折号（行本身已是浅灰小字）。 */
  descNoData: '—',
  /** 最近备课区 */
  recentTitle: '最近备课',
  viewAll: '查看全部',
  /** 元信息班级段：缺失时整段省略（宪法 §7 禁止「未指定班级」这类系统机制占位上屏；
   *  原 metaClassFallback 占位已删除，口径见 formatSessionMeta） */
  /** 会话标题缺失时的中性兜底（禁止出现「未命名XX」这类工程黑话） */
  untitledFallback: '备课记录',
  modifiedPrefix: '最近修改: ',
  continueCta: '继续备课',
  viewCta: '查看',
  moreAria: '更多操作',
  /** 行操作菜单（真实调用后端归档接口） */
  menuDelete: '删除备课',
  deleteTitle: '删除这次备课？',
  deleteBody: '删除后将从「最近备课」中移除；服务端为归档处理，不会物理删除数据。',
  deleteCancel: '取消',
  deleteConfirm: '删除',
  deleteDone: '已删除该备课',
  deleteFailed: '删除失败，请稍后重试。',
  /** 空态（结构化卡：图标 + 一句说明 + 唯一主按钮） */
  emptyTitle: '还没有备课记录',
  emptyHint: '在上方输入框用一句话描述今天要准备的课，AI 会定位教材章节并开始备课。',
  emptyCta: '开始第一次备课',
  /** 加载/失败/无权限 */
  listFailed: '最近备课加载失败，请重试。',
  listForbidden: '当前账号暂无备课数据访问权限，请联系管理员或切换账号后重试。',
  retry: '重试',
  /** 创建中（阶段文案来自真实受理状态，非假进度） */
  creatingTitle: '正在创建备课会话…',
  creatingHint: '已受理你的备课请求，正在进入工作台。',
  creatingElapsedPrefix: '已用时 ',
  creatingElapsedSuffix: ' 秒',
  createFailedSoft: '暂时无法创建备课会话，可稍后重试或直接继续编辑已有备课。',
  /** 右下 AI 入口（胶囊，非大圆钮） */
  aiAssistant: 'AI 备课助手',
} as const

/* ── 左导航（VISUAL-SPEC-V3 §2.1 / §2.2 + R4b IA 收尾）───────────────────
 * R4b IA 裁决：与平台侧栏（备课中心/课堂互动/题库与组卷/作业与批改/资源中心）重复的
 * 入口一律不再出现在备课左导航（平台侧栏常驻可达，E4 不丢能力）；只保留备课专属项
 * （golden1 左栏：新建备课/我的备课/校本资源/公共资源/教研中心 + 最近备课 + 教材与资料）。
 * R1fix-S1 裁决（仅首页态）：G-golden2 左栏 = 内容区组件，Golden 明列条目按 Golden 补回
 * （我的备课/教材与资料/学情分析/题库与组卷/作业与批改 ｜ 资源中心/课堂互动/教学管理/学校资源），
 * 高亮落在「我的备课」；chat 态仍沿用 golden1 清单，不受影响。
 * R4afix2 裁决（会话态）：「教学工具」组按 Golden G-golden4 补回 4 项
 * （教材与资料/题库与组卷/学情分析/资源中心）。 */
export const NAV = {
  home: '备课中心',
  newPrep: '新建备课',
  myPrep: '我的备课',
  textbook: '教材与资料',
  schoolResources: '校本资源',
  publicResources: '公共资源',
  researchCenter: '教研中心',
  /** 教研中心无真实后端页面：诚实禁用 + 原因（G12 禁止亮而无效，也禁止假入口）。 */
  researchCenterUnavailable: '教研中心暂未上线，暂不提供入口',
  /** 首页态左导航（G-golden2 两组条目；缺真实页面者诚实禁用 + tooltip） */
  analysis: '学情分析',
  bank: '题库与组卷',
  assign: '作业与批改',
  resources: '资源中心',
  classroom: '课堂互动',
  teachingManage: '教学管理',
  schoolRes: '学校资源',
  comingSoon: '即将开放',
  recent: '最近备课',
  recentEmpty: '暂无最近备课',
  collapse: '收起侧栏',
  expand: '展开侧栏',
  groupCurrent: '当前备课',
  groupTools: '教学工具',
  tabClassroom: '课堂设计',
  tabPpt: 'PPT',
  tabPlan: '教案',
  tabMaterials: '资料',
  tabHistory: '历史',
  progressTitle: '当前备课进度',
  progressTextbook: '教材定位',
  progressDesign: '教学设计',
  progressPpt: 'PPT 生成',
  progressPlan: '教案生成',
} as const

/* ── 顶栏（工作台共享） ────────────────────────────────────────────── */
export const SHELL = {
  brandShort: '智学备课',
  /** 教师身份兜底占位（真实档案缺姓名时） */
  teacherPlaceholder: '教师',
  /** 课程标题分隔符与课时片段 */
  titleSeparator: ' · ',
  lessonHourSuffix: (n: number) => `第${n}课时`,
  /** 元信息分隔符 */
  metaSeparator: ' · ',
  minutesSuffix: (n: number) => `${n}分钟`,
} as const

/* ── 会话元信息归一（VISUAL-SPEC-V3 §2.3 / §3-S1；顶栏 · 首页列表 · 历史列表唯一拼接口径）──
 * 形如 `人教A版 · 高二(1)班 · 45分钟`：
 * - 教材版本去尾部年份（`人教A版2019` → `人教A版`）；
 * - 剥掉混入的章节号（`3.1.2 xxx` 或 `… 3.1.2 xxx`）——章节号一律不进元信息；
 * - 班级缺失 → 整段诚实省略（宪法 §7：禁止「未指定班级」这类系统机制占位上屏）；
 * - 空段不产生游离分隔符。 */
const YEAR_TAIL_RE = /(19|20)\d{2}$/
const LEADING_CHAPTER_RE = /^\d+(\.\d+)*\s*/
const INLINE_CHAPTER_RE = /\s*\d+(\.\d+)+\s*.*$/

/** 教材版本段清洗（去前导/内嵌章节号 + 去尾部年份）。 */
function cleanTextbookVersion(raw: string | null | undefined): string {
  let t = (raw ?? '').trim()
  if (!t) return ''
  t = t.replace(LEADING_CHAPTER_RE, '').replace(INLINE_CHAPTER_RE, '').replace(YEAR_TAIL_RE, '')
  return t.trim()
}

/**
 * 会话元信息（唯一来源）：`教材版本 · 班级 · 时长`。
 * 顶栏、首页列表、历史列表必须都经此函数，禁止各处自行拼接。
 * 班级缺失 → 该段直接不显示（诚实省略；禁止渲染「未指定班级」占位文案）。
 */
export function formatSessionMeta(input: {
  /** 教材版本名（如 `人教A版2019`；冗余年份会被去掉） */
  textbook?: string | null
  /** 班级名（可能为 null；缺失 → 该段省略） */
  className?: string | null
  /** 时长（如 `45分钟`；缺失则整段省略） */
  duration?: string | null
}): string {
  const parts: string[] = []
  const textbook = cleanTextbookVersion(input.textbook)
  if (textbook) parts.push(textbook)
  const className = (input.className ?? '').trim()
  if (className) parts.push(className)
  const duration = (input.duration ?? '').trim()
  if (duration) parts.push(duration)
  return parts.join(SHELL.metaSeparator)
}

/**
 * 教材版本段（唯一来源）：完整展示名 `人教A版2019 · 高中数学 · 选择性必修第一册`
 * → 取首段并去尾部年份 → `人教A版`。
 * 顶栏 / 首页列表 / 历史列表 / overview 头卡与完成情况 chip 共用，避免各处自行 `split('·')[0]`
 * 拼出带年份的 `人教A版2019`（口径漂移来源）。
 */
export function textbookVersionSegment(rawTitle: string | null | undefined): string {
  const first = (rawTitle ?? '').split('·').map((s) => s.trim()).filter(Boolean)[0] ?? ''
  return cleanTextbookVersion(first)
}

/* ── 课件页眉/封面课题名收尾（渲染层唯一口径）──────────────────────────
 * 去掉「· 教学演示」这类无信息量的尾巴（该串由后端 PPTX 渲染器派生，不属于课件正文）；
 * 行首章节号剥离复用 `stores/deckEditor.stripChapterNo`，由调用方组合
 * （copy 层不 import stores，避免 store ↔ copy 循环依赖）。 */
const DECK_TITLE_TAIL_RE = /\s*[·・|｜]\s*(?:教学演示|课件演示|教学课件|教学PPT|课件|演示)\s*$/

/** 剥掉课题名末尾的无信息量尾巴；剥完为空则回退原文（不丢信息）。 */
export function stripDeckTitleTail(raw: string | null | undefined): string {
  const t = (raw ?? '').trim()
  const stripped = t.replace(DECK_TITLE_TAIL_RE, '').trim()
  return stripped || t
}

/** 课程标题中的课时标记（「第N课时」）：已存在则不重复追加（修「第一课时 · 第1课时」重复拼接）。 */
const LESSON_HOUR_RE = /第\s*[0-9一二三四五六七八九十]+\s*课时/

/**
 * 课程标题 + 课时后缀（展示层口径）：`课题 · 第N课时`。
 * 与 PrepCenterShell 顶栏 courseTitleText 同一规则（顶栏在 FROZEN 壳层，此处为
 * S2 会话头卡的独立消费口径）：标题已含「第N课时」→ 原样返回；hours 缺失 → 只显示标题。
 */
export function formatCourseTitle(base: string | null | undefined, hours: number | null): string {
  const t = (base ?? '').trim()
  const hourPart = hours != null ? SHELL.lessonHourSuffix(hours) : ''
  if (!t) return hourPart
  if (!hourPart || LESSON_HOUR_RE.test(t)) return t
  return t + SHELL.titleSeparator + hourPart
}

/* ── S2 会话头卡 / 返回链 / 状态 pill（golden1 中栏顶部 · R2fix-S2）──────
 * 四步进度文案 = golden1 口径（对话交流/教学设计/生成PPT/生成教案）；
 * 步进状态由真实备课状态驱动（组件内推导），此处只放文案。 */
export const CHAT_HEAD = {
  /** 返回链（真实返回 /teacher-v3/prep 备课中心首页） */
  back: '返回备课中心',
  backAria: '返回备课中心首页',
  /** 会话状态 pill：进行中=绿点/已完成=绿勾（真实 session.status 驱动） */
  statusDoing: '进行中',
  statusDone: '已完成',
  /** 四步进度（带连接线；当前步高亮、完成步打勾） */
  stepChat: '对话交流',
  stepDesign: '教学设计',
  stepPpt: '生成PPT',
  stepPlan: '生成教案',
  /** 头卡标题缺省（真实数据缺失时的诚实占位） */
  titleFallback: HOME.untitledFallback,
} as const

/* ── 底部 AI 条（VISUAL-SPEC-V3 §5.6 单一 composer 铁律）─────────────
 * placeholder 按 Tab 映射：课堂设计 / PPT / 教材与资料 三页语义各不相同。
 * 教案页输入在右面板（§3-S5.4）、本次备课页无底部 AI 条（golden3），故此处无对应文案。 */
export const COMPOSER = {
  /** S2 对话三栏主面（golden1 底部 composer） */
  chat: '告诉 AI 你想怎么备这节课…',
  /** S3 课堂设计：改这节课的教学流程 */
  classroom: '告诉 AI 你想怎么改这节课…',
  /** S3 课堂设计：已展开某个教学环节时的定向文案 */
  classroomTarget: (title: string) => `告诉 AI 你想怎么改「${title}」…`,
  /** S4 PPT：改当前页 */
  ppt: '告诉 AI 如何修改当前页…',
  /** S6 教材与资料（golden4） */
  materials: '告诉 AI 你想怎么改这部分教学内容…',
} as const

/* ── 会话状态徽标（VISUAL-SPEC-V3 §3-S1）──────────────────────────── */
/** 状态 → 中文徽标（draft/active/cancelled/archived 为后端 tp_sessions 的真实状态域）。 */
const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  active: '教学设计进行中',
  processing: '教学设计进行中',
  archived: '已完成',
  completed: '已完成',
  cancelled: '已取消',
  failed: '已失败',
}

/** 会话状态 → 中文徽标（未知状态与进行中同口径，不臆造新词）。 */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return '教学设计进行中'
  return STATUS_LABELS[status] ?? '教学设计进行中'
}

/** 会话状态是否为「进行中」语义（用于徽标高亮）。 */
export function isActiveStatus(status: string | null | undefined): boolean {
  return status === 'active' || status === 'processing'
}

/** 状态语义色调（徽标/行首图标共用；纯呈现映射，不含文案）。 */
export type StatusTone = 'active' | 'draft' | 'done' | 'danger' | 'muted'

/**
 * 会话状态 → 色调。仅按**真实状态域**映射：
 * draft=草稿（琥珀）／archived·completed=已完成（绿）／cancelled·failed=异常（红）／
 * active·processing=进行中（蓝）。空状态回落中性灰。
 */
export function statusTone(status: string | null | undefined): StatusTone {
  if (status === 'draft') return 'draft'
  if (status === 'archived' || status === 'completed') return 'done'
  if (status === 'cancelled' || status === 'failed') return 'danger'
  if (status == null || status === '') return 'muted'
  return 'active'
}

/* ── 错误映射（禁止后端原文上屏）────────────────────────────────────── */
const KIND_TEXT: Record<PrepCenterErrorKind, string> = {
  unauthenticated: '登录状态已失效，请重新登录后重试。',
  forbidden: '当前账号暂无权限执行该操作。',
  conflict: '内容已被更新，请刷新后重试。',
  not_found: '未找到对应的备课内容。',
  rate_limited: '操作过于频繁，请稍后重试。',
  network: '网络连接失败，请检查网络后重试。',
  error: '操作失败，请稍后重试。',
}

/**
 * 把任意错误归一为可上屏的中文文案。
 * - PrepCenterApiError → 按 kind 映射（绝不回显服务端 detail 原文）；
 * - 其它 Error → 通用文案（同样不回显技术堆栈）。
 */
export function mapErrorText(e: unknown, fallback: string = KIND_TEXT.error): string {
  if (e instanceof PrepCenterApiError) return KIND_TEXT[e.kind] ?? fallback
  return fallback
}

/** 错误 kind（供无权限态判定；非 API 错误返回 null）。 */
export function errorKindOf(e: unknown): PrepCenterErrorKind | null {
  return e instanceof PrepCenterApiError ? e.kind : null
}

/* ── 问候语（按本地时间；供后续页面复用）──────────────────────────── */
export function greetingWord(now: Date = new Date()): string {
  const h = now.getHours()
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

/** 问候整句：有真实姓名 →「{问候}，{姓名} 👋」；无 →「{问候}，老师 👋」（不使用假姓名）。 */
export function greetingLine(name: string | null | undefined, now: Date = new Date()): string {
  const n = (name ?? '').trim()
  return n ? `${greetingWord(now)}，${n} 👋` : `${greetingWord(now)}，老师 👋`
}

/* ── 时间（真实时间戳派生，禁止前端编造）──────────────────────────── */
/** 相对时间（真实 updated_at 派生）。 */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return '—'
  const diff = now.getTime() - t.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  const days = Math.floor(diff / 86_400_000)
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  return t.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

/** 绝对时间 `YYYY-MM-DD HH:MM`（列表「最近修改」；无真实时间 → 空串，调用方省略整段）。 */
export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}`
}
/* ── S6 教材与资料（VISUAL-SPEC-V3 §3-S6 / golden4）──────────────────── */
export const MATERIALS = {
  objectivesTitle: '教学目标',
  materialsTitle: '教材与资料',
  sourceTitle: '教材来源',
  extraTitle: '拓展资料',
  readerLoading: '正在读取教材原文…',
  readerFailed: '教材原文加载失败，请稍后重试。',
  readerEmpty: '本次备课还没有定位教材章节；定位后这里会显示教材原文。',
  readerRetry: '重试',
  searchPlaceholder: '在教材中检索',
  searchNoMatch: '未找到匹配内容',
  searchMatches: (n: number) => `${n} 处匹配`,
  zoomIn: '放大',
  zoomOut: '缩小',
  fullscreen: '全屏',
  exitFullscreen: '退出全屏',
  definition: '定义',
  materials: '资料',
  aiSuggest: 'AI 生成建议',
  locate: '定位原文',
  joinEvidence: '加入备课依据',
  joined: '已加入依据',
  joinNoticeOk: '已写入会话依据（教材锚定已确认）',
  joinFail: '写入失败，请重试',
  relatedImages: '相关图片',
  imagesEmpty: '本节切片暂无插图',
  myMaterials: '我的资料',
  uploadFile: '上传资料',
  uploading: '正在上传…',
  uploadDone: (name: string) => `已上传至资料库：${name}`,
  uploadFail: '上传失败，请重试',
  extraHint: '与本课相关的教材片段（探究 / 思考 / 前置知识）',
  resourcesEmpty: '暂无个人资料，可点击下方上传',
  pagesLabel: (a: number, b: number) => (a === b ? `第 ${a} 页` : `第 ${a}–${b} 页`),
  evidenceFallback: '教材依据',
  groupAnalysis: '教材解析',
  groupExamples: '例题素材',
  /* G-golden4 五类树 + 我的资料组（R4afix S7 视觉门禁返工） */
  groupMindmap: '思维导图',
  myUploadsTitle: '个人上传',
  uploadsEmpty: '暂无上传资料',
  mindmapEmpty: '暂无思维导图',
  examplesEmpty: '暂无例题素材',
  extraEmpty: '暂无拓展资料',
  analysisEmpty: '暂无教材解析条目',
  backToReader: '返回教材原文',
  locateFigure: '定位',
  pageOf: (n: number, m: number) => `${n} 页/共 ${m} 页`,
  prevPage: '上一页',
  nextPage: '下一页',
  jumpTo: '跳转',
  jumpPlaceholder: '页码',
  viewSingle: '单页',
  viewContinuous: '连续',
} as const

/* ── S7 本次备课 / 生成与导出 + 版本（VISUAL-SPEC-V3 §3-S7 / golden3）──── */
export const OVERVIEW = {
  back: '返回备课首页',
  kicker: '本次备课',
  doneBadge: '已完成',
  lastModifiedPrefix: '最后修改: ',
  infoBar: 'AI 已完成本次备课的全部内容，您可以导出或继续编辑。',
  completionTitle: '备课完成情况',
  chipTextbook: '教材已确认',
  chipDesign: '教学设计已确认',
  chipPpt: 'PPT已完成',
  chipPlan: '教案已完成',
  chipCheck: '数学内容已检查',
  /* 磁贴状态驱动文案（真实数据未就绪时的诚实标签，禁止假「已完成」） */
  chipTextbookPending: '教材等你确认',
  chipDesignPending: '教学设计等你确认',
  chipPptPending: 'PPT 待生成',
  chipPlanPending: '教案待生成',
  chipCheckPending: '数学内容待检查',
  chipDeckPendingDesc: '尚未生成课件',
  chipPlanPendingDesc: '尚未生成教案',
  /* 磁贴副行（真实数据可得时的真实字段；缺失走 pending 分支，禁止回退「已生成」假状态） */
  chipDesignDesc: '教学目标、重难点、教学过程',
  chipPlanDesc: '完整教学过程与设计意图',
  chipDeckReadyDesc: '课件已生成',
  versionsTitle: '版本管理',
  currentVersion: '当前版本',
  viewAllVersions: '查看全部版本',
  exportTitle: '导出备课成果',
  exportDesc: '已生成完整的 PPT 和教案，支持多种格式导出，便于您在课堂中使用。',
  exportPptx: '导出 PPTX',
  exportPdf: '导出 PDF',
  exportPlan: '导出教案',
  exportStart: '开始导出',
  exportDownload: '下载文件',
  exportRetry: '重试',
  exportNotReady: '尚未生成，无法导出',
  pagesCount: (n: number) => `${n} 页`,
  /* S6 一致性检查卡（真实版本比对：approved_blueprint_revision + context_snapshot_hash） */
  consistencyTitle: '一致性检查',
  consistencyEqual: '一致',
  consistencyStale: '不一致',
  consistencyUnknown: '无法核对',
  consistencyDescEqual: 'PPT 与教案内容已同步，未发现不一致。',
  consistencyDescStale: 'PPT 与教案源自不同蓝图版本，内容可能不一致。',
  consistencyDescUnknown: '课件或教案尚未生成，暂无法核对。',
  consistencyDescNoLineage: '课件与教案缺少版本对照信息，暂无法核对。',
  consistencyInfo: '您可以在“课堂设计 / PPT / 教案”中继续修改，修改后可再次导出或保存新版本。',
} as const

/* ── R3 生成链（G11 空态出路 / G13 真实进度 / G14 单页重试）────────────
 * 空态下一步按钮文案必须自陈动作与前置（确认教学路径 / 生成课件 / 生成教案）；
 * 进度文案只描述服务端真实阶段，数字一律来自 job/SSE，禁止前端造数。 */
export const GENERATE = {
  /* S4/S5 空态（G11：可点击且真实有效的下一步） */
  emptyDeckTitle: '课件尚未生成',
  emptyDeckDesc: '确认教学路径后，会按它生成课件与教案。',
  emptyPlanTitle: '教案尚未生成',
  emptyPlanDesc: '确认教学路径后，会按它生成课件与教案。',
  genDeck: '生成课件',
  genPlan: '生成教案',
  genBoth: '生成课件与教案',
  confirmThenGen: '确认教学路径并生成',
  goToClassroom: '去课堂设计确认教学路径',
  /* 真实进度（G13：阶段文案全部来自服务端 job 状态/事件） */
  working: '正在生成…',
  phaseConfirm: '正在确认教学路径…',
  phaseProject: '正在按教学路径生成课件与教案…',
  phaseEnrich: '正在逐页充实课件内容…',
  pageProgress: (done: number, total: number) => `已完成 ${done} / ${total} 页`,
  genDone: '生成完成',
  /* 失败与单页重试（G14：保留已完成页，只重试失败页/块） */
  genFailed: '生成未完成，已完成部分已保留',
  retryAll: '重试生成',
  retryPage: '重试该页',
  retryBlock: '重试该块',
  pageFailed: (no: number | string) => `第 ${no} 页生成失败`,
  blockFailed: '该内容块生成失败',
} as const

/* ── S8 备课历史（总览面板 8）──────────────────────────────────────── */
export const HISTORY = {
  pageTitle: '备课历史',
  pageSubtitle: '汇总你的全部备课记录，随时回到任意一次备课。',
  versionsTitle: '版本管理',
  versionsHint: '选择一个备课，查看它的历史版本。',
  versionsLoading: '正在加载版本…',
  versionsEmpty: '该备课暂无历史版本。',
  versionsFailed: '版本列表加载失败，请稍后重试。',
  retry: '重试',
  listFailed: '备课历史加载失败，请稍后重试。',
  listForbidden: '当前账号暂无备课数据访问权限，请联系管理员或切换账号后重试。',
  emptyTitle: '还没有备课记录',
  emptyHint: '回到备课中心，用一句话开始你的第一次备课。',
  emptyCta: '去备课中心',
  filterAll: '全部状态',
  filterOnly: (label: string) => `只看「${label}」`,
  selectForVersions: '查看历史版本',
} as const

/* ── 版本来源（blueprint revision source → 中文作者名）────────────── */
const REVISION_SOURCE_LABELS: Record<string, string> = {
  teacher: '教师',
  ai_suggestion: 'AI',
  system: '系统',
}

/** 蓝图修订来源 → 中文作者（未知来源不臆造，返回原始标识）。 */
export function revisionSourceLabel(source: string | null | undefined): string {
  if (!source) return '系统'
  return REVISION_SOURCE_LABELS[source] ?? source
}