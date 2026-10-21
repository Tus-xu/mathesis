<script setup lang="ts">
/**
 * 备课中心工作台顶栏（黄金图 §1：高 56px，白底 + 底部发丝线）。
 *
 * 左→右：课程标题「{课题}·第{n}课时」→ 元信息「{版本} · {班级} · {时长}分钟」
 * （品牌区属平台外框 TeacherV3Layout；宪法 §3 品牌只出现一次，内容区顶栏不再自绘 logo/品牌名）
 * 中部：三 Tab（激活 = 蓝字 + 2px 蓝下划线 + 浅蓝底；资料/历史/对话视图无对应 tab → 不高亮）
 * 右侧：对话（S2 三栏主面入口）/ 资料（真实 evidence 抽屉）/ 历史（R3.7 真实版本面板）。
 * 教师身份 chip 不在此渲染（平台顶栏已有，R4b IA ②）。
 *
 * 反空壳：标题/元信息/资料数全部来自 store 真实快照；缺省段省略不造数。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToastStore } from '@/stores/toast'
import { usePrepSessionStore, MINUTES_PER_LESSON_HOUR } from '@/features/teacher-prep-center/stores/prepSession'
import { useDeckEditorStore } from '@/features/teacher-prep-center/stores/deckEditor'
import { PrepCenterApiError, runPrepAction } from '@/features/teacher-prep-center/api/prepCenterApi'
import { newIdempotencyKey } from '@/features/teacher-prep-center/api/deckApi'
import { runExport, type ExportFormat, type RunExportResult } from '@/features/teacher-prep-center/api/exportApi'
import { SHELL, formatSessionMeta, textbookVersionSegment } from '@/features/teacher-prep-center/copy/prepCopy'
import { cleanInlineSlice, slicePreviewText, refSectionId } from '@/features/teacher-prep-center/math/sliceMarkdown'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import HistoryPanel from '@/features/teacher-prep-center/workspace/classroom/HistoryPanel.vue'
import TextbookViewerDrawer from '@/features/teacher-prep-center/workspace/classroom/TextbookViewerDrawer.vue'
import { useConversation } from '@/features/teacher-prep-center/workspace/conversation/useConversation'

const route = useRoute()
const router = useRouter()
const store = usePrepSessionStore()
const deckStore = useDeckEditorStore()
const toast = useToastStore()

const TABS = [
  { key: 'classroom', label: '课堂设计' },
  { key: 'ppt', label: 'PPT' },
  { key: 'plan', label: '教案' },
] as const

type TabKey = (typeof TABS)[number]['key']

/** 活动 tab：仅 课堂设计/PPT/教案 三视图有对应 tab；
 *  资料/历史/对话视图无 tab 对应 → null（不再错误高亮「课堂设计」，R4b IA ③）。 */
const activeTab = computed<TabKey | null>(() => {
  const t = route.params.tab
  if (t === 'ppt' || t === 'plan') return t
  if (t === 'materials' || t === 'overview' || t === 'chat') return null
  return 'classroom'
})

/** 对话三栏主面（S2）：/w/:sid/chat（R4b：抽屉形态移除，唯一实现在内容区三栏）。 */
const onChatView = computed(() => route.params.tab === 'chat')

const sessionId = computed(() => String(route.params.sessionId || store.currentSessionId || ''))

function switchTab(key: TabKey) {
  if (key === activeTab.value) return
  void router.replace(`/teacher-v3/prep/w/${sessionId.value}/${key}`)
}

/* ── 教师区（R4b IA ② 裁决）：平台顶栏已有「张老师」身份 chip 与退出登录，
 * 工作台顶栏不再重复渲染第二份身份 chip（golden 头像/用户名属平台外框，§7.4）。 ── */

/* 教材展示名（唯一权威字段 = artifact.context.source.title，后端 _source_title 已去重，
 * 格式「出版社 · 高中数学 · 册次」）。裁定 6.2 方案 A：顶栏仅取首段（出版社/版本段，如「人教A版」），
 * 不再拼末段；完整展示名由 PPT 封面/教案元信息消费。禁止前端自行 edition+volume 拼接（重复串来源）。 */
const compactTextbook = computed(() => textbookVersionSegment(store.currentCourseMeta.textbook) || null)

/* ── 课程标题区（LEAD-BAR §3.4）：{课时标题} · {第N课时} + 元信息 ───────────────
 * 缺字段整项省略且排版不塌；修掉「第一课时 · 第1课时」这类重复/空值拼接。 */
const LESSON_HOUR_RE = /第\s*[0-9一二三四五六七八九十]+\s*课时/

const courseTitleText = computed(() => {
  const base = (store.currentCourseTitle || '').trim()
  const hours = store.lessonHoursDisplay
  const lessonPart = hours != null ? SHELL.lessonHourSuffix(hours) : ''
  // 标题已含「第N课时」→ 不再追加（重复拼接的唯一来源）
  if (!base) return lessonPart
  if (!lessonPart || LESSON_HOUR_RE.test(base)) return base
  return base + SHELL.titleSeparator + lessonPart
})

/** 班级段：会话班级名解析（唯一口径 resolveClassName：class_id 目录 → class_name → 教师默认班级）。 */
const courseClassName = computed(() => {
  const ws = store.current
  if (!ws) return null
  return store.resolveClassName(ws.session) || null
})

/** 元信息段：教材版本 · 班级 · 时长（唯一拼接口径 formatSessionMeta；无会话 → 整段省略）。 */
const courseMetaText = computed(() => {
  if (!store.current) return ''
  const hours = store.lessonHoursDisplay
  return formatSessionMeta({
    textbook: compactTextbook.value,
    className: courseClassName.value,
    duration: hours != null ? SHELL.minutesSuffix(hours * MINUTES_PER_LESSON_HOUR) : null,
  })
})

/* ── 资料抽屉（真实 evidence rail；开关入 store）── */
/** 抽屉条目（R4a）：只保留本会话章节 chunk（混入章节剔除），文本经 sliceMarkdown 预览清洗
 *  ——折叠预览不得出现 `![](`/`\-`/`$$`/`\frac` 源码（G22）。 */
const drawerEvidence = computed(() => {
  const sid = store.current?.artifact.context?.section?.id || null
  return (store.current?.evidence || [])
    .filter((e) => !sid || refSectionId(e.ref) === sid)
    .map((e) => ({
      ref: e.ref,
      label: slicePreviewText(e.title || '').slice(0, 60),
      // 摘要保留行内数学（KaTeXText 渲染），文本段经清洗 —— 未闭合定界符自动降级剥除
      snippet: cleanInlineSlice((e.snippet || e.title || '').slice(0, 400)),
    }))
})
const showMaterials = computed(() => store.materialsOpen)
const materialsDisabled = computed(() => !store.current || drawerEvidence.value.length === 0)
const materialsTitle = computed(() =>
  materialsDisabled.value ? '完成教材定位后可用' : `教材与资料 ${drawerEvidence.value.length}`,
)
function toggleMaterials() {
  if (!materialsDisabled.value) store.toggleMaterials()
}

/* ── 对话三栏主面入口（R4b：抽屉实现移除，唯一实现 = 内容区三栏 /w/:sid/chat）──
 * 数据同源 = useConversation 单例（不新建第二份消息状态），条数 = 真实消息数；
 * 无消息时不渲染可用入口（反空壳：数字/入口必须有真实数据支撑）。 */
const { state: conversation } = useConversation()
const conversationCount = computed(() => conversation.messages.length)
const conversationDisabled = computed(() => !store.current)
const conversationTitle = computed(() =>
  conversationDisabled.value ? '进入备课会话后可对话' : `备课对话 ${conversationCount.value} 条`,
)
function goChat() {
  if (conversationDisabled.value || !sessionId.value) return
  if (onChatView.value) return
  void router.push(`/teacher-v3/prep/w/${sessionId.value}/chat`)
}

/* ── 历史（R3.7）：真实 blueprint revision 列表（HistoryPanel 懒加载）── */
function onHistoryClick() {
  if (sessionId.value) void store.openHistory(sessionId.value)
}

/* ── R6.5 同步收口：课堂设计（蓝图）改动 → 课件/教案未同步提示 + 一键同步 ───────
 * 未同步判定完全由前端可算的真实数据推导：蓝图 revision > 某侧 spec 的
 * approved_blueprint_revision → 该侧未同步；任一侧锚点缺失（宽松 deck 对象 / 无投影）
 * → 判为「未知」，保守不误报未同步。
 * 可执行性以后端快照 available_actions 为权威信号（前端不发明动作与目标）。 */
type SyncState = 'fresh' | 'stale' | 'unknown'

const blueprint = computed(() => store.current?.artifact.blueprint ?? null)

/** 蓝图 revision（status!=='confirmed' 时与 spec 锚点不同口径 → 不参与判定）。 */
const blueprintRevision = computed<number | null>(() => {
  if (blueprint.value?.status !== 'confirmed') return null
  const rev = blueprint.value?.revision
  return typeof rev === 'number' ? rev : null
})

function approvedRevision(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

/** 课件侧同步态（projection 缺失时回退服务端投影的顶层同名字段；仍无 → 未知）。 */
const deckSync = computed<SyncState>(() => {
  const rev = blueprintRevision.value
  const deck = store.current?.artifact.teaching_deck
  if (!deck || rev === null) return 'unknown'
  const proj = deck.projection
  const raw = proj && typeof proj === 'object'
    ? (proj as Record<string, unknown>).approved_blueprint_revision
    : deck.approved_blueprint_revision
  const approved = approvedRevision(raw)
  return approved === null ? 'unknown' : rev > approved ? 'stale' : 'fresh'
})

/** 教案侧同步态。 */
const planSync = computed<SyncState>(() => {
  const rev = blueprintRevision.value
  const plan = store.current?.artifact.lesson_plan
  if (!plan || rev === null) return 'unknown'
  const approved = approvedRevision(plan.approved_blueprint_revision)
  return approved === null ? 'unknown' : rev > approved ? 'stale' : 'fresh'
})

const hasUnsynced = computed(() => deckSync.value === 'stale' || planSync.value === 'stale')

/** 服务端下发的 project_specs（蓝图 confirmed 才存在；不存在即不可执行）。 */
const projectAction = computed(() => store.current?.available_actions.find((a) => a.action === 'project_specs') ?? null)

const syncing = ref(false)
const canSync = computed(() => !!projectAction.value && !projectAction.value.disabled && !syncing.value)

const syncStateText = computed(() => {
  const label = (s: SyncState) => (s === 'stale' ? '未同步' : s === 'fresh' ? '已同步' : '状态未知')
  return `课件 ${label(deckSync.value)} · 教案 ${label(planSync.value)}`
})

const syncTitle = computed(() => {
  if (!store.current) return '会话未就绪'
  if (syncing.value) return '正在同步到教案与课件…'
  if (!projectAction.value) return '教学蓝图确认后可同步'
  if (projectAction.value.disabled) return '当前不可同步，请稍后重试'
  return hasUnsynced.value
    ? `${syncStateText.value} · 点击按当前课堂设计重新生成教案与课件`
    : `按当前课堂设计重新生成教案与课件（${syncStateText.value}）`
})

/** 一键同步：跑权威动作 project_specs（双 spec），用返回快照收口未同步标记。 */
async function syncToPlan() {
  const sid = sessionId.value || store.currentSessionId
  const entry = projectAction.value
  const base = store.current
  if (!sid || !base || !entry || entry.disabled || syncing.value) return
  syncing.value = true
  const exec = (revision: number) =>
    runPrepAction(sid, {
      action: 'project_specs',
      target: entry.target,
      expectedRevision: revision,
      // 与课堂设计页同口径：一次产出教案 + 课件两份 spec
      payload: { kinds: ['lesson_plan', 'deck'] },
      idempotencyKey: newIdempotencyKey(),
    })
  try {
    let snapshot
    try {
      snapshot = await exec(base.session.revision)
    } catch (e) {
      // 409 = 快照已过期：拉权威快照后以最新 revision 重试一次（不死循环）
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        await store.refreshCurrent(sid)
        const fresh = store.current
        if (!fresh || fresh.session.revision === base.session.revision) throw e
        snapshot = await exec(fresh.session.revision)
      } else {
        throw e
      }
    }
    // 返回即完整工作区快照：直接替换 store（未同步标记随真实数据自动消失）
    if (store.currentSessionId === sid) store.current = snapshot
    toast.success('已同步到教案与课件')
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    toast.error(errText(e))
  } finally {
    syncing.value = false
  }
}

/* ── 导出闭环（R5 / VISUAL-SPEC-V2 §6.4）：居中对话框 + 真实作业进度 + 票据下载 ──
 * - 格式卡：课件 PPTX / 教案 PDF / 教案 Markdown（产出说明 + 预计文件名）；
 * - 禁用项 = 真实数据缺失（无课件 / 无教案），并给出中文原因（非「即将支持」假禁用）；
 * - 「开始导出」→ 受理真实作业 → 轮询阶段文案（取自服务端 job）→ 完成给下载按钮
 *   （tickets 签票 + downloads 取字节，文件名取 Content-Disposition）；失败给中文原因 + 重试。
 * - 全程无前端假进度；同一时刻只允许一个导出作业。 */
type ExportKind = 'deck' | 'lesson_plan'
type ExportCardKey = 'deck:pptx' | 'lesson_plan:pdf' | 'lesson_plan:markdown'

interface ExportCardDef {
  key: ExportCardKey
  kind: ExportKind
  format: ExportFormat
  title: string
  desc: string
  ext: string
}

const EXPORT_CARDS: ExportCardDef[] = [
  { key: 'deck:pptx', kind: 'deck', format: 'pptx', title: '课件 PPTX', desc: '可编辑的 PowerPoint 文件，含全部渲染页与数学图形', ext: 'pptx' },
  { key: 'lesson_plan:pdf', kind: 'lesson_plan', format: 'pdf', title: '教案 PDF', desc: '排版好的教案文档，适合打印与教研归档', ext: 'pdf' },
  { key: 'lesson_plan:markdown', kind: 'lesson_plan', format: 'markdown', title: '教案 Markdown', desc: '纯文本源码，便于二次编辑与迁移', ext: 'md' },
]

interface ExportSlot {
  stage: string
  error: string
  filename: string
  blob: Blob | null
}

const exportOpen = ref(false)
const exportDialogRef = ref<HTMLElement | null>(null)
const exportActiveKey = ref<ExportCardKey | null>(null)
const exportAbort = ref<AbortController | null>(null)
const exportSlots = reactive<Record<ExportCardKey, ExportSlot>>({
  'deck:pptx': { stage: '', error: '', filename: '', blob: null },
  'lesson_plan:pdf': { stage: '', error: '', filename: '', blob: null },
  'lesson_plan:markdown': { stage: '', error: '', filename: '', blob: null },
})

const lessonPlan = computed(() => store.current?.artifact.lesson_plan ?? null)
const deckExportReady = computed(() => !!deckStore.deckId)
const planExportReady = computed(() => !!lessonPlan.value?.spec_id)

function exportDisabledReason(kind: ExportKind): string {
  return kind === 'deck'
    ? deckExportReady.value ? '' : '尚未生成课件，无法导出'
    : planExportReady.value ? '' : '尚未生成教案，无法导出'
}

function openExport() {
  exportOpen.value = true
  void nextTick(() => exportDialogRef.value?.focus())
}

function closeExport() {
  exportOpen.value = false
}

function onExportKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && exportOpen.value) {
    e.stopPropagation()
    closeExport()
  }
}

/** 预计文件名（真实课题名派生；真实文件名以 Content-Disposition 为准）。 */
function predictedFilename(card: ExportCardDef): string {
  const raw = (courseTitleText.value || '备课').replace(/[\\/:*?"<>|]/g, '').trim() || '备课'
  return card.kind === 'deck' ? `${raw}.${card.ext}` : `${raw}-教案.${card.ext}`
}

/** 解析导出目标（deckId/spec_id + 最新 revision）；数据缺失返回 null。 */
function resolveExportTarget(kind: ExportKind): { id: string; revision: number } | null {
  if (kind === 'deck') {
    const id = deckStore.deckId
    if (!id) return null
    const deck = store.current?.artifact.teaching_deck
    const revision = typeof deck?.revision === 'number' ? deck.revision : deckStore.revision
    return { id, revision }
  }
  const lp = lessonPlan.value
  if (!lp?.spec_id) return null
  return { id: lp.spec_id, revision: lp.revision }
}

/** 后端真实错误文案（detail 非字符串时不得把对象拼上屏）。 */
function errText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    const detail: unknown = e.detail
    if (typeof detail === 'string' && detail.trim()) return detail
    return e.message && !e.message.includes('[object Object]') ? e.message : '操作失败，请稍后重试。'
  }
  if (e instanceof Error) return e.message
  return String(e)
}

/** 触发浏览器下载：Blob → 隐藏 a[download] → 释放 URL（文件名取真实 Content-Disposition）。 */
function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** 执行单张格式卡的导出（受理 → 真实轮询 → 签票下载）；一次只允许一个作业。 */
async function runCardExport(card: ExportCardDef) {
  if (exportAbort.value) {
    toast.info('导出进行中，请稍候…')
    return
  }
  const initial = resolveExportTarget(card.kind)
  if (!initial) {
    toast.error(exportDisabledReason(card.kind) || '暂无可导出的内容')
    return
  }
  const slot = exportSlots[card.key]
  slot.stage = '正在提交导出任务…'
  slot.error = ''
  slot.filename = ''
  slot.blob = null
  exportActiveKey.value = card.key
  const controller = new AbortController()
  exportAbort.value = controller
  const exec = (target: { id: string; revision: number }): Promise<RunExportResult> =>
    runExport({
      kind: card.kind,
      id: target.id,
      format: card.format,
      expectedRevision: target.revision,
      signal: controller.signal,
      onStage: (label) => {
        slot.stage = label
      },
    })
  try {
    let result: RunExportResult
    try {
      result = await exec(initial)
    } catch (e) {
      // 乐观并发冲突（快照 revision 过期）：拉权威快照后以最新 revision 重试一次，不死循环
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        slot.stage = '内容已更新，正在重新导出…'
        const sid = sessionId.value || store.currentSessionId
        if (sid) await store.refreshCurrent(sid)
        const fresh = resolveExportTarget(card.kind)
        if (!fresh) throw e
        result = await exec(fresh)
      } else {
        throw e
      }
    }
    slot.blob = result.blob
    slot.filename = result.filename
    slot.stage = '导出完成'
    toast.success(`导出完成：${result.filename}`)
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      slot.stage = ''
      return
    }
    slot.error = errText(e)
    slot.stage = ''
  } finally {
    exportAbort.value = null
    exportActiveKey.value = null
  }
}

/** 下载已完成的导出产物（真实票据下载字节已在 slot.blob）。 */
function downloadCard(card: ExportCardDef) {
  const slot = exportSlots[card.key]
  if (slot.blob && slot.filename) triggerBrowserDownload(slot.blob, slot.filename)
}

/** 重试：清空该卡状态后重新发起。 */
function retryCard(card: ExportCardDef) {
  const slot = exportSlots[card.key]
  slot.error = ''
  slot.blob = null
  slot.filename = ''
  void runCardExport(card)
}

/** 卡片「开始导出」入口（吞掉 promise，模板内不产生未处理拒绝）。 */
function onCardExport(card: ExportCardDef) {
  void runCardExport(card)
}

/* ── 顶栏三段式弹性布局（VISUAL-SPEC-V3 §2.3）────────────────────────────
 * 左（课程标题 + 元信息）flex: 0 1 auto；中（三 Tab 分段控件）flex: none 居中；
 * 右（动作组 + 教师区）flex: 0 0 auto。
 * 空间不足时右组按优先级收敛：先收「预览演示 / 同步到教案」进 ··· 菜单（菜单项真实可用），
 * 再把「已保存」收成绿勾图标（tooltip 保留真实时间）；「资料 / 历史 / 教师区」永不折叠。
 * 折叠级别由实测宽度推导（不写死断点）：左段自然宽（标题/元信息按 CSS max-width 截断）
 * + Tab 宽 + 右组当前宽 ≤ 可用宽 时，取能装下的最小级别。 */
const HEADER_PAD = 40 // padding 0 20px ×2
const HEADER_GAP = 16 // 段间距（与 CSS gap 同口径）
const LEFT_GAP = 16 // 左段内部间距（与 CSS gap 同口径）
const COURSE_TITLE_MAX = 320
const COURSE_META_MAX = 360
/** 三段之间必须保留的最小视觉间距（不足即读作「拥挤」，与黄金图不拥挤的观感对齐）。 */
const MIN_SLACK = 32

const headerEl = ref<HTMLElement | null>(null)
const courseTitleEl = ref<HTMLElement | null>(null)
const courseMetaEl = ref<HTMLElement | null>(null)
const tabsEl = ref<HTMLElement | null>(null)
const rightEl = ref<HTMLElement | null>(null)

/** 0 = 全展示；1 = 预览/同步 进 ··· 菜单；2 = 「已保存」仅图标。 */
const collapseLevel = ref(0)
const showMoreMenu = ref(false)
const moreWrapRef = ref<HTMLElement | null>(null)

/* N4：··· 菜单补齐 Esc / 点击外部关闭（此前只有「点菜单项 / 再点 ···」能关）。
 * - Esc：window keydown（导出对话框用 stopPropagation 也拦不住同一 node 上的其它监听，
 *   两者同时打开时一起关 —— 导出遮罩 z-index 100 本就盖住菜单，语义正确）；
 * - 外点：document pointerdown（先于 click 触发；··· 按钮与菜单项都在 wrap 内 → 不误关）。 */
function onShellKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && showMoreMenu.value) showMoreMenu.value = false
}
function onDocPointerDown(e: PointerEvent) {
  if (!showMoreMenu.value) return
  const wrap = moreWrapRef.value
  if (wrap && e.target instanceof Node && !wrap.contains(e.target)) showMoreMenu.value = false
}

/** 左段自然宽（标题/元信息 scrollWidth 按 CSS max-width 截断，不随挤压变小；
 *  品牌区已移除（宪法 §3 品牌只出现一次），度量口径只含课程标题与元信息）。 */
function leftNeed(): number {
  const title = Math.min(courseTitleEl.value?.scrollWidth ?? 0, COURSE_TITLE_MAX)
  const meta = Math.min(courseMetaEl.value?.scrollWidth ?? 0, COURSE_META_MAX)
  const course = title && meta ? title + LEFT_GAP + meta : title + meta
  return course + (course ? LEFT_GAP : 0)
}

let measuring = false
/** 逐级试算能装下的最小折叠级别（只观测顶栏宽度，不自激）。 */
async function measureHeader() {
  const el = headerEl.value
  if (!el || measuring) return
  const avail = el.clientWidth
  if (avail <= 0) return
  measuring = true
  try {
    const base = HEADER_PAD + HEADER_GAP * 2 + leftNeed() + (tabsEl.value?.offsetWidth ?? 0)
    for (const level of [0, 1, 2] as const) {
      if (collapseLevel.value !== level) {
        collapseLevel.value = level
        await nextTick()
      }
      if (base + (rightEl.value?.offsetWidth ?? 0) + MIN_SLACK <= avail) return
    }
  } finally {
    measuring = false
  }
}

/** 右组溢出菜单项（真实动作；空数组 → 不渲染 ···）。 */
interface OverflowItem {
  key: 'preview' | 'sync'
  label: string
  title: string
  disabled: boolean
  run: () => void
}
const overflowItems = computed<OverflowItem[]>(() => {
  if (collapseLevel.value < 1 || activeTab.value !== 'ppt' || !deckStore.deckId) return []
  return [
    { key: 'preview', label: '预览演示', title: '全屏预览当前课件', disabled: false, run: () => deckStore.openPreview() },
    {
      key: 'sync',
      label: syncing.value ? '同步中…' : '同步到教案',
      title: syncTitle.value,
      disabled: !canSync.value,
      run: () => void syncToPlan(),
    },
  ]
})
function onOverflowClick(item: OverflowItem) {
  showMoreMenu.value = false
  if (!item.disabled) item.run()
}
watch(overflowItems, (items) => {
  if (!items.length) showMoreMenu.value = false
})
watch(
  () => [activeTab.value, courseTitleText.value, courseMetaText.value, deckStore.deckId, deckStore.savedClockText] as const,
  () => void measureHeader(),
  { flush: 'post' },
)

function onHeaderResize() {
  void measureHeader()
}

onMounted(() => {
  window.addEventListener('resize', onHeaderResize)
  void nextTick().then(measureHeader)
  window.addEventListener('keydown', onExportKeydown)
  window.addEventListener('keydown', onShellKeydown)
  document.addEventListener('pointerdown', onDocPointerDown)
  // 班级目录（真实 /teacher-v3/classes）：供顶栏在会话无 class_id 时退化为默认班级
  void store.fetchClasses()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onHeaderResize)
  window.removeEventListener('keydown', onExportKeydown)
  window.removeEventListener('keydown', onShellKeydown)
  document.removeEventListener('pointerdown', onDocPointerDown)
  exportAbort.value?.abort() // 卸载即取消在途导出轮询，避免泄漏
})
</script>

<template>
  <div class="pc-shell">
    <header ref="headerEl" class="pc-header">
    <!-- 左：课程上下文（flex: 0 1 auto；课程标题超长时省略号截断。
         品牌（logo + 品牌名）只在平台外框 TeacherV3Layout 渲染（宪法 §3 品牌只出现一次），
         顶栏从课程标题开始，原品牌与分隔线一并移除。 -->
    <div class="pc-header__left">
      <div class="pc-header__course">
        <span v-if="courseTitleText" ref="courseTitleEl" class="pc-header__course-title">{{ courseTitleText }}</span>
        <span v-if="courseTitleText && courseMetaText" class="pc-header__course-sep" aria-hidden="true">·</span>
        <span v-if="courseMetaText" ref="courseMetaEl" class="pc-header__course-meta">{{ courseMetaText }}</span>
      </div>
    </div>

    <!-- 中：三 Tab 分段控件（flex: none；容器浅灰胶囊，激活项白底 + 主蓝字 + 2px 蓝下划线） -->
    <nav ref="tabsEl" class="pc-header__tabs" role="tablist" aria-label="备课工作台视图">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        role="tab"
        class="pc-header__tab"
        :class="{ 'is-active': activeTab === tab.key }"
        :aria-selected="activeTab === tab.key"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 右：动作组（按空间收敛）+ 教师区（flex: 0 0 auto，永不折叠） -->
    <div ref="rightEl" class="pc-header__right">
      <!-- PPT Tab：已保存 / 预览演示 / 同步到教案（R6.5 真实一键同步：可用性由 available_actions 推导） -->
      <template v-if="activeTab === 'ppt' && deckStore.deckId">
        <!-- 保存态：仅真实持久化时间可呈现；无时间则整项隐藏（不出现「已保存 」空值）。
             空间不足时收敛为绿勾图标 + tooltip（真实时间不丢）。 -->
        <span v-if="deckStore.saveState === 'saving'" class="pc-header__save is-saving">保存中…</span>
        <span v-else-if="deckStore.saveState === 'error'" class="pc-header__save is-error">保存失败</span>
        <span
          v-else-if="deckStore.savedClockText"
          class="pc-header__save is-saved"
          :title="'已保存 ' + deckStore.savedClockText"
        >
          <svg class="pc-header__save-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.4 8.3 6.3 11.2 12.6 4.9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span v-if="collapseLevel < 2">已保存 {{ deckStore.savedClockText }}</span>
        </span>
      </template>

      <!-- 溢出菜单：空间不足时承载「预览演示 / 同步到教案」（项全部真实可用，非摆设） -->
      <div v-if="overflowItems.length" ref="moreWrapRef" class="pc-header__more-wrap">
        <button
          type="button"
          class="pc-header__tool pc-header__more"
          aria-haspopup="menu"
          :aria-expanded="showMoreMenu"
          title="更多操作"
          @click="showMoreMenu = !showMoreMenu"
        >
          ···
        </button>
        <div v-if="showMoreMenu" class="pc-header__menu" role="menu">
          <button
            v-for="item in overflowItems"
            :key="item.key"
            type="button"
            class="pc-header__menu-item"
            role="menuitem"
            :disabled="item.disabled"
            :title="item.title"
            @click="onOverflowClick(item)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <template v-if="activeTab === 'ppt' && deckStore.deckId && collapseLevel === 0">
        <button type="button" class="pc-header__tool pc-header__tool--outline" @click="deckStore.openPreview()">
          <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M5.4 3.8 12.1 8l-6.7 4.2V3.8Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
          </svg>
          预览演示
        </button>
        <button
          type="button"
          class="pc-header__tool"
          :disabled="!canSync"
          :title="syncTitle"
          @click="syncToPlan"
        >
          <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h3.2l1.6 1.6H13a1.5 1.5 0 0 1 1.5 1.5v7.4A1.5 1.5 0 0 1 13 14H3a1.5 1.5 0 0 1-1.5-1.5V3.5Z" fill="none" stroke="currentColor" stroke-width="1.3" />
          </svg>
          {{ syncing ? '同步中…' : '同步到教案' }}
          <!-- 未同步标记：真实 revision 比对结果（课件/教案任一侧落后于课堂设计） -->
          <span v-if="hasUnsynced && !syncing" class="pc-header__sync-dot" aria-hidden="true"></span>
        </button>
      </template>
      <!-- 教案 Tab：同步到课件（与 PPT 侧同一权威动作 project_specs / kinds=[lesson_plan, deck]，
           一键把当前课堂设计投影为教案与课件两份 spec）。
           保存态不在此渲染：教案编辑器（usePlanEditor）无真实「最近保存时间」来源，
           禁止借用课件侧 deckStore 时间伪造（诚实省略，反空壳 §8）。 -->
      <template v-if="activeTab === 'plan'">
        <button
          type="button"
          class="pc-header__tool"
          :disabled="!canSync"
          :title="syncTitle"
          @click="syncToPlan"
        >
          <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h3.2l1.6 1.6H13a1.5 1.5 0 0 1 1.5 1.5v7.4A1.5 1.5 0 0 1 13 14H3a1.5 1.5 0 0 1-1.5-1.5V3.5Z" fill="none" stroke="currentColor" stroke-width="1.3" />
          </svg>
          {{ syncing ? '同步中…' : '同步到课件' }}
          <!-- 未同步标记：真实 revision 比对结果（课件/教案任一侧落后于课堂设计） -->
          <span v-if="hasUnsynced && !syncing" class="pc-header__sync-dot" aria-hidden="true"></span>
        </button>
      </template>
      <!-- 导出（R5 / §6.4）：居中对话框；课件 + 教案全部格式 -->
      <button
        v-if="activeTab === 'ppt' || activeTab === 'plan'"
        type="button"
        class="pc-header__tool pc-header__tool--outline"
        aria-haspopup="dialog"
        :aria-expanded="exportOpen"
        title="导出课件与教案"
        @click="openExport"
      >
        <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M8 2.2v7.6m0 0 2.9-2.9M8 9.8 5.1 6.9M3.2 12.2V13.6h9.6v-1.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        导出
      </button>
      <button
        type="button"
        class="pc-header__tool"
        :class="{ 'is-active': onChatView }"
        :disabled="conversationDisabled"
        :title="conversationTitle"
        @click="goChat"
      >
        <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M2 3.6A1.6 1.6 0 0 1 3.6 2h8.8A1.6 1.6 0 0 1 14 3.6v6a1.6 1.6 0 0 1-1.6 1.6H7.6L4.3 13.7v-2.5H3.6A1.6 1.6 0 0 1 2 9.6v-6Z"
            fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"
          />
        </svg>
        对话
        <span v-if="conversationCount" class="pc-header__conv-count">{{ conversationCount }}</span>
      </button>
      <button
        type="button"
        class="pc-header__tool"
        :disabled="materialsDisabled"
        :title="materialsTitle"
        @click="toggleMaterials"
      >
        <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.2l1.6 1.6H13a1.5 1.5 0 0 1 1.5 1.5v7.4A1.5 1.5 0 0 1 13 14H3a1.5 1.5 0 0 1-1.5-1.5V3.5Z"
            fill="none" stroke="currentColor" stroke-width="1.3"
          />
        </svg>
        资料
      </button>
      <button type="button" class="pc-header__tool" :disabled="!store.current" title="教学蓝图历史版本" @click="onHistoryClick">
        <svg class="pc-header__tool-icon" viewBox="0 0 16 16" aria-hidden="true">
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M8 4.8V8l2.2 1.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </svg>
        历史
      </button>
    </div>

    <!-- 资料抽屉（真实 evidence：教材 SourceLocator 引用） -->
    <Teleport to="body">
      <div v-if="showMaterials" class="pc-materials-mask" @click.self="store.toggleMaterials(false)">
        <aside class="pc-materials">
          <div class="pc-materials__head">
            <span>教材与资料</span>
            <span class="pc-materials__count">{{ drawerEvidence.length }}</span>
            <button type="button" class="pc-materials__close" @click="store.toggleMaterials(false)">×</button>
          </div>
          <p class="pc-materials__hint">来自真实教材检索命中（SourceLocator 引用），由备课 AI 提供依据。</p>
          <ul class="pc-materials__list">
            <li v-for="(item, i) in drawerEvidence" :key="`${item.ref}-${i}`" class="pc-materials__item" :title="item.ref">
              <span class="pc-materials__ref">{{ item.label }}</span>
              <KatexText class="pc-materials__snippet" :text="item.snippet" />
            </li>
          </ul>
        </aside>
      </div>
    </Teleport>

    <!-- 备课对话：R4b 起为内容区三栏主面（/w/:sid/chat），抽屉实现移除（单实现冻结 G2）。 -->

    <!-- 历史版本面板（真实 revision 快照；R3.7） -->
    <HistoryPanel />
    <!-- 教材原文查看抽屉（真实切片原文；R3.6） -->
    <TextbookViewerDrawer />

    <!-- 导出对话框（VISUAL-SPEC-V2 §6.4：居中 520；Esc / 点击遮罩关闭） -->
    <Teleport to="body">
      <div v-if="exportOpen" class="pc-export-mask" @click.self="closeExport">
        <div
          ref="exportDialogRef"
          class="pc-export"
          role="dialog"
          aria-modal="true"
          aria-label="导出课件与教案"
          tabindex="-1"
          @keydown="onExportKeydown"
        >
          <div class="pc-export__head">
            <span class="pc-export__title">导出</span>
            <button type="button" class="pc-export__close" aria-label="关闭" @click="closeExport">×</button>
          </div>
          <p class="pc-export__hint">导出为真实产物文件；课件导出会渲染全部页面并做视觉质检，约 40-60 秒。</p>
          <ul class="pc-export__list">
            <li
              v-for="card in EXPORT_CARDS"
              :key="card.key"
              class="pc-export__card"
              :class="{ 'is-disabled': !!exportDisabledReason(card.kind) }"
            >
              <div class="pc-export__card-main">
                <span class="pc-export__card-title">{{ card.title }}</span>
                <span class="pc-export__card-desc">{{ card.desc }}</span>
                <span class="pc-export__card-file">
                  预计文件名：{{ exportSlots[card.key].filename || predictedFilename(card) }}
                </span>
                <span v-if="exportSlots[card.key].stage" class="pc-export__card-stage">
                  <span v-if="exportActiveKey === card.key" class="pc-export__spinner" aria-hidden="true"></span>
                  {{ exportSlots[card.key].stage }}
                </span>
                <span v-if="exportSlots[card.key].error" class="pc-export__card-error">{{ exportSlots[card.key].error }}</span>
              </div>
              <div class="pc-export__card-actions">
                <button
                  v-if="exportSlots[card.key].blob"
                  type="button"
                  class="pc-export__btn pc-export__btn--primary"
                  @click="downloadCard(card)"
                >
                  下载文件
                </button>
                <button v-else-if="exportSlots[card.key].error" type="button" class="pc-export__btn" @click="retryCard(card)">
                  重试
                </button>
                <button
                  v-else
                  type="button"
                  class="pc-export__btn"
                  :class="{ 'pc-export__btn--primary': !exportDisabledReason(card.kind) }"
                  :disabled="!!exportDisabledReason(card.kind) || !!exportActiveKey"
                  :title="exportDisabledReason(card.kind) || '开始导出'"
                  @click="onCardExport(card)"
                >
                  开始导出
                </button>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </Teleport>
    </header>
    <!-- 备课中心不渲染第二套侧栏（用户裁决 2026-10-04）：平台外框已有全局导航，
         会话内流转由顶栏 tabs（课堂设计/PPT/教案）+ 对话/资料/历史 承载；
         金字塔参考图 golden1/golden4 中的内容区左栏按宪法 §3（平台入口不得在备课中心
         内部重复）与用户裁决废除。 -->
  </div>
</template>

<style scoped>
.pc-header {
  position: relative;
  display: flex;
  align-items: center;
  gap: 16px;
  height: var(--pc-header-h);
  padding: 0 20px;
  background: var(--pc-panel);
  border-bottom: 1px solid var(--pc-line);
  font-family: var(--pc-font);
  user-select: none;
}

/* 左段（flex: 0 1 auto；空间不足时课程标题省略号截断，不挤压中部 Tab） */
.pc-header__left {
  flex: 0 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 16px;
}

/* 课程上下文（课程标题 · 元信息；两段均可收缩省略。品牌区已移除，
   顶栏从课程标题开始——品牌属平台外框，宪法 §3 品牌只出现一次） */
.pc-header__course {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.pc-header__course-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  max-width: 320px;
}
.pc-header__course-sep {
  flex: none;
  font-size: 13px;
  color: var(--pc-text-faint);
}
.pc-header__course-meta {
  font-size: 13px;
  color: var(--pc-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  max-width: 360px;
}

/* 中部 Tab 分段控件（flex: none 居中；容器浅灰胶囊，激活项白底 + 主蓝字 + 2px 蓝下划线） */
.pc-header__tabs {
  flex: 0 0 auto;
  margin: 0 auto;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-bg);
}
.pc-header__tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  height: 34px;
  padding: 0 20px;
  border: none;
  border-radius: var(--pc-radius-full);
  background: transparent;
  font-size: 14px;
  font-family: inherit;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-header__tab:hover {
  color: var(--pc-text-2);
}
.pc-header__tab.is-active {
  color: var(--pc-primary);
  background: var(--pc-panel);
  font-weight: 600;
}
.pc-header__tab.is-active::after {
  content: '';
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 1px;
  height: var(--pc-tab-underline);
  border-radius: var(--pc-tab-underline);
  background: var(--pc-primary);
}

/* 右段（flex: 0 0 auto，永不压缩；空间不足由 collapseLevel 收敛） */
.pc-header__right {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.pc-header__tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-bg);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-header__tool:hover:not(:disabled) {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-header__tool:disabled {
  cursor: not-allowed;
  color: var(--pc-text-faint);
}
.pc-header__tool--outline {
  border: 1px solid var(--pc-line);
  background: var(--pc-panel);
  color: var(--pc-primary);
}
.pc-header__tool--outline:hover {
  background: var(--pc-primary-soft);
}
.pc-header__save {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--pc-success);
  white-space: nowrap;
}
.pc-header__save.is-saving { color: var(--pc-warning); }
.pc-header__save.is-error { color: var(--pc-danger); }
.pc-header__save-icon {
  width: 14px;
  height: 14px;
}
/* 溢出按钮（承载被收敛的 预览演示 / 同步到教案） */
.pc-header__more-wrap { position: relative; }
.pc-header__more {
  justify-content: center;
  min-width: 34px;
  padding: 0 8px;
  font-size: 15px;
  letter-spacing: 1px;
}
.pc-header__menu-item:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
  background: transparent;
}
.pc-header__tool-icon {
  width: 15px;
  height: 15px;
}
/* R6.5 未同步标记（6px 待办点；颜色/圆角取既有 token，不改顶栏几何） */
.pc-header__sync-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-warning);
}
/* 「对话」条数徽标（真实消息数；无消息时整段不渲染） */
.pc-header__conv-count {
  flex: none;
  min-width: 18px;
  padding: 0 5px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
  text-align: center;
}

/* 「对话」入口当前态（三栏主面已打开；R4b） */
.pc-header__tool.is-active {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}

/* 溢出菜单容器（··· 菜单） */
.pc-header__menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 140px;
  padding: 6px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  box-shadow: var(--pc-shadow-hover);
  z-index: 60;
}
.pc-header__menu-item {
  display: block;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-header__menu-item:hover {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
/* ── 导出对话框（居中 520；VISUAL-SPEC-V2 §6.4）──────────────────────── */
.pc-export-mask {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(15, 23, 42, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--pc-font);
}
.pc-export {
  width: 520px;
  max-width: 92vw;
  max-height: 86vh;
  overflow: auto;
  background: var(--pc-panel);
  border-radius: 14px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
  padding: 20px 22px 22px;
  outline: none;
}
.pc-export__head {
  display: flex;
  align-items: center;
}
.pc-export__title {
  font-size: 17px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-export__close {
  margin-left: auto;
  border: none;
  background: transparent;
  font-size: 20px;
  line-height: 1;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-export__hint {
  margin: 6px 0 14px;
  font-size: 12.5px;
  color: var(--pc-text-faint);
  line-height: 1.6;
}
.pc-export__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pc-export__card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--pc-line);
  border-radius: 12px;
  background: var(--pc-bg);
}
.pc-export__card.is-disabled { opacity: 0.72; }
.pc-export__card-main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}
.pc-export__card-title { font-size: 14px; font-weight: 600; color: var(--pc-text); }
.pc-export__card-desc { font-size: 12.5px; color: var(--pc-text-muted); line-height: 1.55; }
.pc-export__card-file { font-size: 12px; color: var(--pc-text-faint); }
.pc-export__card-stage {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--pc-primary);
}
.pc-export__card-error { font-size: 12.5px; color: var(--pc-danger); line-height: 1.5; }
.pc-export__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--pc-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-export-spin 0.8s linear infinite;
}
@keyframes pc-export-spin { to { transform: rotate(360deg); } }
.pc-export__card-actions { flex: none; }
.pc-export__btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-export__btn:hover:not(:disabled) { border-color: var(--pc-primary); color: var(--pc-primary); }
.pc-export__btn--primary { border-color: transparent; background: var(--pc-primary); color: #fff; }
.pc-export__btn--primary:hover:not(:disabled) { background: var(--pc-primary); color: #fff; }
.pc-export__btn:disabled {
  cursor: not-allowed;
  color: var(--pc-text-faint);
  background: var(--pc-bg);
}

/* 资料抽屉 */
.pc-materials-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.24);
  z-index: 90;
}
.pc-materials {
  position: absolute;
  top: 0;
  right: 0;
  width: 380px;
  max-width: 86vw;
  height: 100%;
  background: var(--pc-panel);
  box-shadow: var(--pc-shadow-hover);
  padding: 18px;
  overflow: auto;
}
.pc-materials__head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-materials__count {
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  font-weight: 600;
}
.pc-materials__close {
  margin-left: auto;
  border: none;
  background: transparent;
  font-size: 20px;
  line-height: 1;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-materials__hint {
  margin: 8px 0 14px;
  font-size: 12px;
  color: var(--pc-text-faint);
}
.pc-materials__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pc-materials__item {
  padding: 10px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  box-shadow: var(--pc-shadow-card);
}
.pc-materials__ref {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-primary);
  margin-bottom: 4px;
}
.pc-materials__snippet {
  display: block;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text-2);
}

/* 备课对话：三栏主面内嵌消息流（R4b），抽屉样式已随抽屉形态一并移除。 */
</style>

<style>
/* 备课中心不再渲染第二套侧栏（用户裁决 2026-10-04，宪法 §3）：
   工作区主体恒定全宽，无让位、无折叠分支。 */
.pc-workspace__body {
  padding-left: 0;
}
</style>
