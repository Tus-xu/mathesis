<script setup lang="ts">
/**
 * 对话面单条消息（W1-C）。
 *
 * - 教师消息靠右（浅蓝气泡 #EFF4FF）；AI 消息靠左（白卡 + 头像 + 名称 + 时间）；
 * - 内容支持行内 KaTeX（复用 math/KatexText.vue，只读不改）；
 * - 富组件消息（选择卡 / 上下文卡 / 蓝图卡 / 教材依据 / 失败卡 / 节点修订卡）就地渲染；
 * - 未注册组件类型一律不渲染（fail-closed，不把内部码上屏）；
 * - 发送中 / 失败态：行内状态 + 重试按钮（禁止一闪而过的 toast）；
 * - assistant 侧文本渲染前统一过 cleanInlineSlice 清洗（切片/LLM 源码不上屏，
 *   `$…$` 保留给 KaTeX；教师消息原样回显）；未闭合 `$` 兜底不渲染残缺公式；
 * - golden1 对齐：prose 与卡并为一体（无独立气泡壳）、蓝图卡浅蓝小节标题、
 *   上下文卡「label：值」行内式、次按钮无边框浅底、卡内右下小时间戳、
 *   连发卡纵向堆叠为轻分隔（无名称行）。
 */
import { computed, nextTick, ref, watch } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import { cleanInlineSlice, forMarkdown, hasMarkdownStructure } from '@/features/teacher-prep-center/math/sliceMarkdown'
import { renderMarkdown } from '@/utils/markdown'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import SelectionCard from './SelectionCard.vue'
import type { PrepChatMessage, SelectionCardInfo } from './useConversation'

const store = usePrepSessionStore()

const props = withDefaults(
  defineProps<{
    message: PrepChatMessage
    /** 该选择卡是否处于「待选择」活跃态。 */
    selectionActive?: boolean
    selectedId?: string | null
    sourceTitle?: string | null
    /** 确认卡动作执行中（禁用按钮 + 处理中文案，非假进度）。 */
    actionBusy?: boolean
    /** 决策卡是否处于「可操作」态（最后一条且非处理中）。 */
    decisionActive?: boolean
  }>(),
  { selectionActive: false, selectedId: null, sourceTitle: null, actionBusy: false, decisionActive: false },
)

const emit = defineEmits<{
  /** 选择卡点选：option + 卡上下文（kind / source_id，供 pickSection 分流，存量卡为 null）。 */
  (e: 'pick', option: { id: string; label: string }, card: SelectionCardInfo): void
  (e: 'retry', id: string): void
  (e: 'locate'): void
  (e: 'overflow', on: boolean): void
  /** 蓝图确认卡三按钮（G10）：真实 confirm action / 聚焦输入框 / 打开教材依据面板。 */
  (e: 'confirm-blueprint', payload: { blueprintId: string }): void
  (e: 'modify-request'): void
  (e: 'view-evidence'): void
  /** 决策卡按钮（改选确认等）：结构化回传，不经 LLM。 */
  (e: 'decide', option: { id: string; label: string; action: string; section_id?: string | null; textbook_id?: string | null; text?: string | null }): void
}>()

const isUser = computed(() => props.message?.role === 'user')
const component = computed(() => props.message?.component ?? null)
const type = computed(() => component.value?.type ?? '')
const propsOf = computed(() => (component.value?.props ?? {}) as Record<string, unknown>)

const bodyText = computed<string>(() => {
  const p = propsOf.value
  if (typeof p.text === 'string' && p.text) return p.text
  return props.message?.text ?? ''
})

/* ── assistant 侧文本清洗（G22 同源：cleanInlineSlice）──────────────────────
 * assistant 消息常混入切片/LLM 原始标记（`##`、`![](`、`\-`、`\frac`、未闭合 `$`…），
 * 直出会花屏。所有 assistant 侧上屏文本渲染前统一过 cleanInlineSlice（数学段
 * `$…$` 原样保留给 KaTeX，文本段剥源码）；**教师消息不清洗**（原文必须原样回显）。
 * 不改 katex.ts 内核（15 个文件共用），清洗只发生在本组件展示层。 */

/** 未闭合 `$` 兜底（clipEvidence 同思路）：`$` 不成对 → 丢掉尾 `$`，绝不渲染残缺公式。 */
function ensurePairedMath(text: string): string {
  const dollars = (text.match(/\$/g) || []).length
  if (dollars % 2 === 1) return text.slice(0, text.lastIndexOf('$'))
  return text
}

/** assistant 侧展示文本统一清洗：剥 markdown/LaTeX 源码 + 未闭合 `$` 兜底。 */
function cleanAssistantText(src: string | null | undefined): string {
  return ensurePairedMath(cleanInlineSlice(src))
}

/** 纯文本气泡展示文本（assistant 清洗 / user 原样）。 */
const displayBody = computed<string>(() =>
  isUser.value ? bodyText.value : cleanAssistantText(bodyText.value),
)

/**
 * 结构化长文本（含 ## 标题 / HTML 表格 / 教材插图）→ markdown 渲染。
 * 2026-10-11：备课生成产物携带教材切片的标题层级与统计表，纯文本直出会把
 * 「## 三、教学问题诊断分析」内联进段落、表格压成数字流。走 utils/markdown 内核
 * （marked gfm 表格 + KaTeX + DOMPurify 消毒），HTML 表格先由 forMarkdown 转管道表。
 * 短回复（无结构）仍走既有 cleanInlineSlice + KatexText 路径，golden 样式不变。
 */
const assistantMdHtml = computed<string | null>(() => {
  if (isUser.value) return null
  if (!hasMarkdownStructure(bodyText.value)) return null
  return renderMarkdown(forMarkdown(bodyText.value))
})

/**
 * G5 prose 正文（KatexText 路径）：卡消息必须先渲染针对老师该句的 prose，再渲染结构化卡。
 * golden1：prose 与卡并为一体（卡内首段，无独立气泡壳）。assistant 侧先清洗再渲染；
 * 后端已为新卡消息落库 `text`；存量行无 text 时不渲染空段（向后兼容）。
 */
const proseText = computed<string>(() => cleanAssistantText(props.message?.text ?? '').trim())

/**
 * AI 蓝色加粗开场句（golden1「好的，我已理解您的备课需求。」· R2fix-S2）：
 * assistant 消息 prose 的**首句**以 #2563EB 加粗渲染、其余正文常规。
 * 仅展示层分级（拆首句），不改动 LLM 内容本身：
 * - 首句 = 第一个句末标点（。！？!?）为止（含标点），且不在 `$...$` 行内公式中间截断；
 * - 首句过长（>80 字）→ 不分级（避免整段蓝粗），无句末标点的短句（≤40 字）整句作为首句；
 * - user 消息不参与分级。
 */
const OPENING_MAX_CHARS = 80
const OPENING_WHOLE_MAX_CHARS = 40
const SENTENCE_END = '。！？!?'

function splitOpening(text: string): { head: string; rest: string } | null {
  const t = text
  if (!t) return null
  let inMath = false
  for (let i = 0; i < t.length; i += 1) {
    const ch = t[i]
    if (ch === '$') {
      inMath = !inMath
    } else if (!inMath && SENTENCE_END.includes(ch)) {
      if (i + 1 > OPENING_MAX_CHARS) return null
      return { head: t.slice(0, i + 1), rest: t.slice(i + 1) }
    }
  }
  return t.length <= OPENING_WHOLE_MAX_CHARS ? { head: t, rest: '' } : null
}

/** 纯文本气泡路径（displayBody）与卡消息 prose 路径（proseText）共用同一分级口径。 */
const plainOpening = computed(() => (isUser.value ? null : splitOpening(displayBody.value)))
const proseOpening = computed(() => (isUser.value ? null : splitOpening(proseText.value)))

/** 会话级抑制（G6）：章节一旦确定（真实投影有 section）→ 不再渲染章节胶囊云。 */
const sectionLocked = computed<boolean>(() => {
  const ws = store.current
  return !!(ws?.artifact?.context?.section?.id || ws?.session?.intent?.section_id)
})
const lockedSectionTitle = computed<string>(() => {
  const ws = store.current
  return (
    ws?.artifact?.context?.section?.title ||
    store.currentCourseTitle ||
    '当前章节'
  )
})

/* ── 蓝图确认卡（G10）：备课信息 2×2 + 教学意图 bullets + 下一步建议 + 三按钮 ── */
const liveBlueprintStatus = computed<string>(() => {
  const bp = store.current?.artifact?.blueprint
  const pid = String(propsOf.value.blueprint_id ?? '')
  if (bp && pid && String(bp.blueprint_id ?? '') === pid && bp.status) return String(bp.status)
  return String(propsOf.value.status ?? '')
})
const blueprintInfo = computed<{ label: string; value: string }[]>(() => {
  const raw = propsOf.value.info
  if (Array.isArray(raw) && raw.length) {
    return raw
      .filter((i): i is { label: string; value: string } => !!i && typeof (i as { label?: unknown }).label === 'string')
      .map((i) => ({ label: i.label, value: cleanAssistantText(i.value) }))
  }
  // 存量行（无 info 字段）：用真实投影回填（缺省诚实省略，不造数）
  const meta = store.currentCourseMeta
  const rows: { label: string; value: string }[] = []
  const title = String(propsOf.value.title ?? '')
  if (title) rows.push({ label: '课题', value: cleanAssistantText(title) })
  if (meta.textbook) rows.push({ label: '教材', value: cleanAssistantText(meta.textbook) })
  const cls = store.current?.session?.class_name
  if (cls) rows.push({ label: '年级班级', value: cleanAssistantText(cls) })
  const hours = store.lessonHoursDisplay
  if (hours != null) rows.push({ label: '课时', value: `${hours} 课时（${meta.minutes} 分钟）` })
  return rows
})
const blueprintBullets = computed<string[]>(() => {
  const raw = propsOf.value.intent_bullets
  if (Array.isArray(raw) && raw.length) return raw.map((b) => cleanAssistantText(String(b))).slice(0, 3)
  // 存量行：从 buckets 的「设计意图/教学目标」收敛 3 条（真实数据，不编造）
  const buckets = Array.isArray(propsOf.value.buckets)
    ? (propsOf.value.buckets as { title?: string; items?: { text?: string }[] }[])
    : []
  const pick = buckets.find((b) => String(b.title ?? '').includes('设计意图') || String(b.title ?? '').includes('目标'))
  return (pick?.items ?? []).map((it) => cleanAssistantText(String(it?.text ?? ''))).filter(Boolean).slice(0, 3)
})
const nextStepText = computed<string>(() => cleanAssistantText(String(propsOf.value.next_step ?? '')))
/** 动作行仅在等老师确认时渲染（已确认/已驳回不显示亮而无效的按钮）。 */
const showConfirmActions = computed(() => liveBlueprintStatus.value === 'proposal')

/* ── 决策卡（改选确认等）：少量明确按钮，非章节胶囊云 ───────────────────────── */
const decisionTitle = computed(() => String(propsOf.value.title ?? '请确认'))
const decisionOptions = computed<
  { id: string; label: string; action: string; section_id?: string | null; textbook_id?: string | null; text?: string | null }[]
>(() => {
  const raw = propsOf.value.options
  if (!Array.isArray(raw)) return []
  return raw
    .filter((o): o is Record<string, unknown> => !!o && typeof (o as { id?: unknown }).id === 'string')
    .map((o) => ({
      id: String(o.id),
      label: String(o.label ?? ''),
      action: String(o.action ?? 'send_text'),
      section_id: o.section_id == null ? null : String(o.section_id),
      textbook_id: o.textbook_id == null ? null : String(o.textbook_id),
      text: o.text == null ? null : String(o.text),
    }))
})

const timeText = computed<string>(() => {
  const iso = props.message?.createdAt
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
})

/* ── 选择卡 ─────────────────────────────────────────────────────────── */
const selectionOptions = computed<{ id: string; label: string }[]>(() => {
  const raw = propsOf.value.options
  if (!Array.isArray(raw)) return []
  return raw
    .filter((o): o is { id: string; label: string } => !!o && typeof (o as { id?: unknown }).id === 'string')
    .map((o) => ({ id: o.id, label: String(o.label ?? '') }))
})
/** 后端 card props 的 kind / source_id（catalog 契约；存量卡缺省 → null，卡内按章节卡兼容渲染）。 */
const selectionCard = computed<SelectionCardInfo>(() => ({
  kind: typeof propsOf.value.kind === 'string' && propsOf.value.kind ? propsOf.value.kind : null,
  sourceId: typeof propsOf.value.source_id === 'string' && propsOf.value.source_id ? propsOf.value.source_id : null,
}))

/* ── 上下文卡 ───────────────────────────────────────────────────────── */
const contextRows = computed<{ label: string; value: string }[]>(() => {
  const p = propsOf.value
  const rows: { label: string; value: string }[] = []
  const push = (label: string, value: unknown) => {
    if (typeof value === 'string' && value.trim()) rows.push({ label, value: value.trim() })
  }
  push('教材', p.source_title)
  push('章节', p.section_title)
  push('课时', p.lesson_scope)
  // 历史会话可能带「45 分钟被填成 lesson_hours=45」的越界值 → 与顶栏同一归一规则
  const hours = store.normalizeLessonHours(p.lesson_hours)
  if (hours != null) rows.push({ label: '课时数', value: `第 ${hours} 课时` })
  const kps = Array.isArray(p.knowledge_points) ? (p.knowledge_points as unknown[]) : []
  const objectives = Array.isArray(p.objectives) ? (p.objectives as unknown[]) : []
  if (kps.length) rows.push({ label: '知识点', value: kps.map((k) => String(k)).slice(0, 6).join('、') })
  if (objectives.length) rows.push({ label: '教学目标', value: objectives.map((o) => String(o)).slice(0, 4).join('；') })
  return rows
})

/* ── 蓝图卡 ─────────────────────────────────────────────────────────── */
/* golden1 ③：卡头标题条与状态 chip 已去除（状态由动作行/已确认行表达，
 * 课题信息在「备课信息确认」区承载）——原 blueprintTitle/blueprintStatusText 随之移除。 */
const blueprintBuckets = computed<{ title: string; count: number }[]>(() => {
  const buckets = propsOf.value.buckets
  if (!Array.isArray(buckets)) return []
  return buckets
    .map((b) => {
      const item = b as { title?: unknown; items?: unknown }
      return {
        title: String(item.title ?? ''),
        count: Array.isArray(item.items) ? item.items.length : 0,
      }
    })
    .filter((b) => b.title)
})

/**
 * 蓝图卡「一行元信息」（紧凑版）：只保留最高信号的三个计数
 * （教学流程环节数 / 重点 / 难点），其余汇总为要点总数。
 * 十个分类逐个铺开会把会话条撑成两屏（失重布局），此处收敛为单行。
 */
const blueprintMeta = computed(() => {
  const buckets = blueprintBuckets.value
  if (!buckets.length) return ''
  const pick = (kw: string) => buckets.find((b) => b.title.includes(kw))
  const parts: string[] = []
  const flow = pick('流程') ?? buckets[0]
  parts.push(`${flow.title} ${flow.count} 个环节`)
  const key = pick('重点')
  if (key) parts.push(`重点 ${key.count}`)
  const hard = pick('难点')
  if (hard) parts.push(`难点 ${hard.count}`)
  const total = buckets.reduce((n, b) => n + b.count, 0)
  parts.push(`要点共 ${total} 条`)
  return parts.join(' · ')
})

/* ── 教材依据卡 ─────────────────────────────────────────────────────── */
/** 卡片型消息（§3.2）：必须占满会话条宽度，禁止塞进气泡。 */
const CARD_TYPES = new Set([
  'selection_card',
  'context_card',
  'blueprint_card',
  'evidence_rail',
  'diagnostic_card',
  'basic_mode_card',
  'error_card',
  'node_revision_card',
  'job_status_card',
])
const isCard = computed(() => CARD_TYPES.has(type.value))

/**
 * 卡内片段预览：教材切片原文常带 markdown 标题符与硬换行，
 * 直接上屏会把会话条撑成一大段裸文本（失重布局）。此处只做展示层归一
 * （去行首 `#`、把硬换行折叠成空格），**不截断字符**——截断会切断 `$...$`
 * 造成公式残缺；视觉边界由 CSS 行数钳制负责。原文完整内容在「教材与资料」抽屉。
 */
function previewText(src: unknown): string {
  // 存量快照里的 HTML <table> 先转管道行（否则剥标签后单元格拼成数字流/字面标签汤）
  const piped = forMarkdown(String(src ?? ''))
  return piped
    .split('\n')
    .map((l) => l.replace(/^#{1,6}\s*/, '').trim())
    .filter(Boolean)
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const evidenceRefs = computed<{ title: string; snippet: string; kind: string }[]>(() => {
  const refs = propsOf.value.refs
  if (!Array.isArray(refs)) return []
  return refs.slice(0, 3).map((r) => {
    const item = r as { title?: unknown; snippet?: unknown; kind?: unknown }
    const title = previewText(item.title)
    const snippet = previewText(item.snippet)
    // 后端部分切片 title（块首行）与 snippet（块正文）为同一段文字：重复渲染会
    // 让同一条依据出现两遍。同源时只保留正文（信息更完整），标题位留空不渲染。
    // （同源比对在清洗前进行：比对的是后端原文，展示层另行清洗。）
    const duplicated = !!title && !!snippet && (snippet === title || snippet.startsWith(title))
    return { title: duplicated ? '' : title, snippet, kind: String(item.kind ?? '') }
  })
})
const evidenceTotal = computed(() => (Array.isArray(propsOf.value.refs) ? (propsOf.value.refs as unknown[]).length : 0))

/**
 * 会话条内证据卡：只展示主依据（第一条）的「标题 + 2–3 行摘要 + 行数省略」。
 * 与黄金图右栏「教材依据」卡的引文块（浅底 + 大引号 + ≤4 行）气质一致，
 * 完整原文由「教材与资料」抽屉 + 本卡「展开全文」承载；
 * 此处绝不把 8–10 行裸原文铺满会话条（失重布局验收项）。
 */
const evidencePrimary = computed(() => evidenceRefs.value[0] ?? null)
/** 证据标题展示文本（与摘要同口径清洗：切片源码不上屏）。 */
const evidenceTitle = computed<string>(() => cleanAssistantText(evidencePrimary.value?.title ?? ''))

/**
 * 摘要收敛（2–3 行容量）：优先在**最后一个完整句边界**收尾（全角 `。；！？`
 * 或后跟空白的半角 `.`，避免切断 `3.1.2` / `(a>b>0)`），再保证行内公式
 * `$...$` 定界符成对（落在公式中间时退回到上一个 `$`）——截断绝不产生残缺公式。
 */
const EVIDENCE_SUMMARY_CHARS = 180
function clipEvidence(src: string): string {
  if (src.length <= EVIDENCE_SUMMARY_CHARS) return src
  const head = src.slice(0, EVIDENCE_SUMMARY_CHARS)
  const sentence = head.match(/^[\s\S]*(?:[。；！？]|\.(?=\s))/)
  let cut = sentence ? sentence[0] : head
  const dollars = (cut.match(/\$/g) || []).length
  if (dollars % 2 === 1) cut = cut.slice(0, cut.lastIndexOf('$'))
  return `${cut.trim()}…`
}
/** 摘要内容：未展开 → 收敛摘要；展开全文 → 原始片段。渲染前统一清洗（切片源码不上屏）。 */
const evidenceSummary = computed(() => {
  const r = evidencePrimary.value
  if (!r) return ''
  return cleanAssistantText(evidenceOpen.value ? r.snippet : clipEvidence(r.snippet))
})
/** 摘要超出容量才有「展开全文」（短文本不渲染多余入口）。 */
const evidenceNeedsExpand = computed(() => {
  const r = evidencePrimary.value
  if (!r) return false
  return r.snippet.length > EVIDENCE_SUMMARY_CHARS
})
const evidenceOpen = ref(false)
const rootRef = ref<HTMLElement | null>(null)
/**
 * 卡片内「展开全文」超出会话条折叠高度上限 → 请求会话条放开高度（不靠裁切硬压）；
 * 展开后把**本卡**滚入视野（block:'nearest'）——此前流内无条件 scrollToBottom 会把
 * 视野跳到最后一条消息，展开靠前的卡时用户看不到任何变化（N5「点击无可见反馈」根因）。
 */
watch(evidenceOpen, (on) => {
  emit('overflow', on)
  if (on) {
    void nextTick(() => {
      rootRef.value?.querySelector('.pc-evref')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }
})

/* ── 失败卡 ─────────────────────────────────────────────────────────── */
const failureText = computed(() => {
  const p = propsOf.value
  if (typeof p.error_message === 'string' && p.error_message) return cleanAssistantText(p.error_message)
  const label = String(p.error_label ?? '')
  if (label === 'provider_unavailable') return 'AI 服务暂时不可用，本次生成没有完成。'
  if (label === 'timeout') return '本次生成超时，未写入任何内容。'
  return '本轮生成没有完成，可重试。'
})

/* ── 节点修订卡 ─────────────────────────────────────────────────────── */
const nodeRevision = computed(() => {
  const p = propsOf.value
  const idx = typeof p.node_index === 'number' ? p.node_index : null
  return {
    no: idx === null ? '' : String(idx + 1).padStart(2, '0'),
    title: String(p.node_title ?? ''),
    summary: cleanAssistantText(String(p.summary ?? '')),
  }
})

/**
 * 是否渲染为「纯文本气泡」。
 * - 无组件 / 已注册文本组件 → 文本气泡；
 * - **未知组件类型**：只要有可读文本就降级为文本气泡（绝不静默丢内容），
 *   无文本时 fail-closed（不把内部码上屏）。
 */
const isPlain = computed(() => {
  if (!component.value) return true
  if (type.value === 'user_text' || type.value === 'assistant_text') return true
  if (CARD_TYPES.has(type.value)) return false
  return !!bodyText.value
})
</script>

<template>
  <div ref="rootRef" class="pc-msg" :class="[isUser ? 'pc-msg--user' : 'pc-msg--ai', { 'pc-msg--card': isCard }]">
    <span v-if="!isUser" class="pc-msg__avatar" aria-hidden="true">
      <span class="pc-msg__avatar-glow"></span>
      <svg viewBox="0 0 24 24"><path d="M12 2.5c.5 0 1 .3 1.3.8l2.8 6.4 7 .7c.6.1 1.1.6 1.1 1.2s-.3 1.1-.9 1.3l-6 2.6 1.8 6.8c.2.6-.1 1.2-.7 1.4-.2.1-.4.1-.5.1-.4 0-.8-.2-1-.6L12 16.5l-4 6.7c-.3.4-.8.6-1.2.6-.2 0-.4 0-.6-.1-.5-.2-.8-.8-.7-1.4l1.8-6.8L1.4 13c-.6-.2-1-1.1-.9-1.3.1-.6.5-1.1 1.1-1.2l7-.7 2.8-6.4c.3-.5.8-.8 1.3-.8z" fill="currentColor" /></svg>
    </span>

    <div class="pc-msg__col">
      <!-- 纯文本消息（AI 首句蓝色加粗分级：golden1 开场句样式；assistant 侧已清洗） -->
      <template v-if="isPlain">
        <div class="pc-msg__bubble">
          <!-- 结构化长文本（标题/表格/插图）→ markdown 渲染；短回复走既有 golden 开场分级 -->
          <div v-if="assistantMdHtml" class="pc-msg__md" v-html="assistantMdHtml"></div>
          <template v-else-if="plainOpening"><span class="pc-msg__open"><KatexText :text="plainOpening.head" /></span><KatexText v-if="plainOpening.rest" :text="plainOpening.rest" /></template>
          <KatexText v-else :text="displayBody" />
        </div>
        <!-- AI meta 弱化（golden1 ⑥）：去掉卡外名称行，仅保留弱化小时间戳 -->
        <span v-if="!isUser && timeText" class="pc-msg__time-under">{{ timeText }}</span>
      </template>

      <!-- 卡消息：prose 与卡并为一体（golden1 ①，无独立气泡壳），再渲染结构化卡 -->
      <template v-else>
        <!-- 选择卡（G6 会话级抑制：章节已确定 → 不再渲染章节胶囊云）；
             golden1 ①：prose 与卡并为一体——选择卡无卡壳，prose 以无气泡壳的引导段先行 -->
        <div v-if="type === 'selection_card' && !sectionLocked">
          <div v-if="proseText" class="pc-msg__prose-lead">
            <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
            <KatexText v-else :text="proseText" />
          </div>
          <SelectionCard
            :options="selectionOptions"
            :active="selectionActive"
            :selected-id="selectedId"
            :source-title="sourceTitle"
            :kind="selectionCard.kind"
            :source-id="selectionCard.sourceId"
            @pick="(o) => emit('pick', o, selectionCard)"
          />
        </div>
        <div v-else-if="type === 'selection_card'" class="pc-msg__card">
          <div v-if="proseText" class="pc-msg__prose-lead">
            <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
            <KatexText v-else :text="proseText" />
          </div>
          <p class="pc-msg__card-faint">
            已锁定章节《{{ lockedSectionTitle }}》。想换章节？直接回复章节名就行（例如「改选 1.2.1 …」）。
          </p>
          <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
        </div>

        <!-- 上下文卡（golden1 ④：dl 两列表格 → 「label：值」行内式） -->
        <div v-else-if="type === 'context_card'" class="pc-msg__card">
          <div v-if="proseText" class="pc-msg__prose-lead">
            <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
            <KatexText v-else :text="proseText" />
          </div>
          <div class="pc-msg__card-head">
            <span class="pc-msg__card-title">教材与学情已定位</span>
          </div>
          <div v-if="contextRows.length" class="pc-msg__ctx">
            <p v-for="row in contextRows" :key="row.label" class="pc-msg__ctx-row">
              <span class="pc-msg__ctx-label">{{ row.label }}：</span><span class="pc-msg__ctx-value">{{ row.value }}</span>
            </p>
          </div>
          <p v-else class="pc-msg__card-faint">已记录本节课的教材与章节信息。</p>
          <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
        </div>

        <!-- 蓝图确认卡（G10 / golden1：prose 并为一体 + 备课信息浅蓝小节 + 教学意图 bullets + 下一步建议 + 动作行）；
             golden1 ③：卡头标题条与状态 chip 去除，改卡内浅蓝小节标题；
             「查看教学流程 ›」入口迁移到卡内动作区（emit('locate') 保留，禁止删功能） -->
        <div v-else-if="type === 'blueprint_card'" class="pc-msg__card pc-confirm">
          <div v-if="proseText" class="pc-msg__prose-lead">
            <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
            <KatexText v-else :text="proseText" />
          </div>

          <!-- 备课信息（golden1 ③：浅蓝小节标题条 + 浅蓝底圆角信息区） -->
          <template v-if="blueprintInfo.length">
            <p class="pc-confirm__section">备课信息确认</p>
            <div class="pc-confirm__info">
              <div v-for="item in blueprintInfo" :key="item.label" class="pc-confirm__cell">
                <span class="pc-confirm__label">{{ item.label }}</span>
                <span class="pc-confirm__value"><KatexText :text="item.value" /></span>
              </div>
            </div>
          </template>

          <!-- 教学意图 3 bullets -->
          <div v-if="blueprintBullets.length" class="pc-confirm__block">
            <p class="pc-confirm__block-title">教学意图</p>
            <ul class="pc-confirm__bullets">
              <li v-for="(b, i) in blueprintBullets" :key="i"><KatexText :text="b" /></li>
            </ul>
          </div>

          <!-- 下一步建议 -->
          <div v-if="nextStepText" class="pc-confirm__block">
            <p class="pc-confirm__block-title">下一步建议</p>
            <p class="pc-confirm__next"><KatexText :text="nextStepText" /></p>
          </div>

          <!-- 存量行兜底：无新字段时保留紧凑元信息行 -->
          <p v-if="!blueprintInfo.length && blueprintMeta" class="pc-msg__meta-line">{{ blueprintMeta }}</p>
          <p v-else-if="!blueprintInfo.length && !blueprintBullets.length" class="pc-msg__card-faint">
            教学流程与要点已整理，可在下方「课堂设计」查看完整设计。
          </p>

          <!-- 动作行：主按钮「确认，继续生成」+ 次按钮「修改需求」「查看教材依据」 -->
          <div v-if="showConfirmActions" class="pc-confirm__actions">
            <button
              type="button"
              class="pc-confirm__btn pc-confirm__btn--primary"
              :disabled="actionBusy"
              @click="emit('confirm-blueprint', { blueprintId: String(propsOf.blueprint_id ?? '') })"
            >
              {{ actionBusy ? '处理中…' : '确认，继续生成' }}
            </button>
            <button type="button" class="pc-confirm__btn" @click="emit('modify-request')">修改需求</button>
            <button type="button" class="pc-confirm__btn" @click="emit('view-evidence')">查看教材依据</button>
          </div>
          <p v-else-if="liveBlueprintStatus === 'confirmed'" class="pc-confirm__done">已确认 · 将按这份蓝图生成教学流程与课件、教案</p>
          <p v-else-if="liveBlueprintStatus === 'rejected'" class="pc-confirm__done">未采用</p>

          <!-- 卡内动作区（golden1 ③：「查看教学流程」入口迁入，功能保留）+ 右下小时间戳（golden1 有时间戳） -->
          <div class="pc-confirm__foot">
            <button type="button" class="pc-msg__link" @click="emit('locate')">查看教学流程 <span aria-hidden="true">›</span></button>
            <span class="pc-msg__card-spacer"></span>
            <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
          </div>
        </div>

      <!-- 教材依据卡（标题 + 2–3 行摘要 + 行数省略 + 展开全文；prose 并为一体） -->
      <div v-else-if="type === 'evidence_rail'" class="pc-msg__card">
        <div v-if="proseText" class="pc-msg__prose-lead">
          <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
          <KatexText v-else :text="proseText" />
        </div>
        <div class="pc-msg__card-head">
          <span class="pc-msg__card-title">教材依据</span>
          <span class="pc-msg__chip">{{ evidenceTotal }} 条</span>
          <span class="pc-msg__card-spacer"></span>
          <button v-if="evidenceNeedsExpand" type="button" class="pc-msg__link" @click="evidenceOpen = !evidenceOpen">
            {{ evidenceOpen ? '收起' : '展开全文' }}
          </button>
        </div>
        <div v-if="evidencePrimary" class="pc-evref" :class="{ 'is-open': evidenceOpen }">
          <p v-if="evidenceTitle" class="pc-evref__title"><KatexText :text="evidenceTitle" /></p>
          <div class="pc-evref__clip">
            <div class="pc-evref__body"><KatexText :text="evidenceSummary" /></div>
          </div>
        </div>
        <p v-else class="pc-msg__card-faint">暂无教材引用片段。</p>
        <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
      </div>

      <!-- 失败卡 -->
      <div v-else-if="type === 'basic_mode_card' || type === 'error_card'" class="pc-msg__card pc-msg__card--error">
        <div v-if="proseText" class="pc-msg__prose-lead">
          <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
          <KatexText v-else :text="proseText" />
        </div>
        <div class="pc-msg__card-head">
          <span class="pc-msg__card-title">本轮没有完成</span>
        </div>
        <p class="pc-msg__error">{{ failureText }}</p>
        <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
      </div>

      <!-- 节点修订卡 -->
      <div v-else-if="type === 'node_revision_card'" class="pc-msg__card">
        <div v-if="proseText" class="pc-msg__prose-lead">
          <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
          <KatexText v-else :text="proseText" />
        </div>
        <div class="pc-msg__card-head">
          <span class="pc-msg__card-title">已更新环节 {{ nodeRevision.no }} {{ nodeRevision.title }}</span>
        </div>
        <p v-if="nodeRevision.summary" class="pc-msg__card-body"><KatexText :text="nodeRevision.summary" /></p>
        <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
      </div>

        <!-- 决策卡（改选确认等）：明确确认动作，非章节胶囊云 -->
        <div v-else-if="type === 'decision_card'" class="pc-msg__card pc-decision">
          <div v-if="proseText" class="pc-msg__prose-lead">
            <template v-if="proseOpening"><span class="pc-msg__open"><KatexText :text="proseOpening.head" /></span><KatexText v-if="proseOpening.rest" :text="proseOpening.rest" /></template>
            <KatexText v-else :text="proseText" />
          </div>
          <div class="pc-msg__card-head">
            <span class="pc-msg__card-title">{{ decisionTitle }}</span>
          </div>
          <div class="pc-decision__row">
            <button
              v-for="opt in decisionOptions"
              :key="opt.id"
              type="button"
              class="pc-confirm__btn"
              :class="{ 'pc-confirm__btn--primary': opt.action === 'select_section' }"
              :disabled="!decisionActive || actionBusy"
              @click="emit('decide', opt)"
            >
              {{ opt.label }}
            </button>
          </div>
          <p v-if="!decisionActive" class="pc-msg__card-faint">已处理</p>
          <span v-if="timeText" class="pc-msg__card-time">{{ timeText }}</span>
        </div>
      </template>

      <!-- 未知组件：fail-closed，不渲染内部码 -->

      <!-- 发送态 / 排队态 / 失败态 -->
      <div v-if="message?.pending === 'sending'" class="pc-msg__status">发送中…</div>
      <!-- 排队态（服务端「上一条仍在处理中」）：中性提示，不是失败；空闲后自动发出 -->
      <div v-else-if="message?.pending === 'queued'" class="pc-msg__status pc-msg__status--queued">
        排队中 · 上一条消息回复后自动发送
      </div>
      <div v-else-if="message?.pending === 'failed'" class="pc-msg__status pc-msg__status--error">
        <span>{{ message.errorText || '发送失败，请重试。' }}</span>
        <button type="button" class="pc-msg__retry" @click="emit('retry', message.id)">重试</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pc-msg {
  display: flex;
  gap: 10px;
  max-width: 100%;
}
.pc-msg--user {
  justify-content: flex-end;
}
.pc-msg--ai {
  justify-content: flex-start;
}
.pc-msg__avatar {
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: linear-gradient(135deg, #6366f1 0%, #2563eb 50%, #06b6d4 100%);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
  position: relative;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.35);
}
.pc-msg__avatar-glow {
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(99,102,241,.4), rgba(6,182,212,.3));
  z-index: -1;
  filter: blur(6px);
  opacity: .7;
}
.pc-msg__avatar svg {
  width: 18px;
  height: 18px;
  filter: drop-shadow(0 1px 1px rgba(0,0,0,.15));
}
.pc-msg__col {
  min-width: 0;
  max-width: 82%;
  display: flex;
  flex-direction: column;
}
/* 卡片型消息占满会话条宽度（§3.2：禁止塞进气泡） */
.pc-msg--card .pc-msg__col {
  flex: 1;
  max-width: 100%;
}
.pc-msg--user .pc-msg__col {
  align-items: flex-end;
}
/* AI meta 弱化（golden1 ⑥）：名称行已去；纯文本气泡仅保留右下弱化小时间戳 */
.pc-msg__time-under {
  align-self: flex-end;
  margin-top: 2px;
  font-size: 11px;
  color: var(--pc-text-faint, #9ca3af);
}
.pc-msg__bubble {
  padding: 11px 15px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--pc-text, #0f172a);
  overflow-wrap: anywhere;
}
/* AI 蓝色加粗开场句（golden1：首句 #2563EB 加粗，其余正文常规） */
.pc-msg__open {
  color: var(--pc-primary, #2563eb);
  font-weight: 700;
}
/* 结构化长文本 markdown 渲染（2026-10-11）：标题层级 / gfm 表格 / 教材插图 */
.pc-msg__md :deep(h1),
.pc-msg__md :deep(h2),
.pc-msg__md :deep(h3),
.pc-msg__md :deep(h4) {
  margin: 14px 0 6px;
  font-size: 14.5px;
  font-weight: 700;
  color: var(--pc-text, #0f172a);
}
.pc-msg__md :deep(h1):first-child,
.pc-msg__md :deep(h2):first-child,
.pc-msg__md :deep(h3):first-child {
  margin-top: 0;
}
.pc-msg__md :deep(p) {
  margin: 6px 0;
}
.pc-msg__md :deep(table) {
  margin: 8px 0;
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
}
.pc-msg__md :deep(th),
.pc-msg__md :deep(td) {
  border: 1px solid #dbe3f0;
  padding: 5px 9px;
  text-align: center;
  white-space: nowrap;
}
.pc-msg__md :deep(th) {
  background: #f1f5fd;
  font-weight: 600;
}
.pc-msg__md :deep(img) {
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid #e5eaf5;
  margin: 6px 0;
}
.pc-msg__md :deep(ul),
.pc-msg__md :deep(ol) {
  margin: 6px 0;
  padding-left: 20px;
}
.pc-msg--user .pc-msg__bubble {
  background: linear-gradient(135deg, #eef2ff 0%, #e0e7ff 100%);
  border: 1px solid #c7d2fe;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.12);
}
.pc-msg--ai .pc-msg__bubble {
  background: linear-gradient(135deg, #f8faff 0%, #fff 100%);
  border: 1px solid #e5eaf5;
  border-top-left-radius: 4px;
  box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
}
/* 卡内首段 prose（golden1 ①：prose 与卡并为一体，无独立气泡壳） */
.pc-msg__prose-lead {
  margin: 0 0 10px;
  font-size: 14px;
  line-height: 1.7;
  color: var(--pc-text, #0f172a);
  overflow-wrap: anywhere;
}
/* 卡内右下小时间戳（golden1 有时间戳；卡外名称行已去） */
.pc-msg__card-time {
  display: block;
  text-align: right;
  margin-top: 6px;
  font-size: 11px;
  color: var(--pc-text-faint, #9ca3af);
}
/* golden1 ⑦：连发卡（蓝图卡+证据卡等）纵向堆叠——独立 border+shadow 减弱为轻分隔。
   兄弟相邻选择器：仅当上一条可见消息同为卡消息时收拢（用户气泡会打断组合）。 */
.pc-msg--card + .pc-msg--card {
  margin-top: -16px; /* 抵消会话条 16px 间距，卡组相接 */
}
.pc-msg--card + .pc-msg--card .pc-msg__card {
  border-top: none;
  border-top-left-radius: 0;
  border-top-right-radius: 0;
  box-shadow: none;
}
.pc-msg--card + .pc-msg--card .pc-msg__avatar {
  visibility: hidden; /* 连发组只保留首卡头像（占位保列对齐） */
}
.pc-msg__card {
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: 12px;
  padding: 14px 16px;
  font-family: var(--pc-font);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.pc-msg__card--error {
  background: #fff8f8;
  border-color: #f4d4d4;
}
.pc-msg__card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.pc-msg__card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text, #0f172a);
}
.pc-msg__chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
  font-size: 12px;
  font-weight: 600;
}
/* 上下文卡（golden1 ④）：「label：值」行内式（原 dl 两列表格移除） */
.pc-msg__ctx {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pc-msg__ctx-row {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text-2, #1f2937);
  overflow-wrap: anywhere;
}
.pc-msg__ctx-label {
  color: var(--pc-text-muted, #6b7280);
}
.pc-msg__card-spacer {
  flex: 1;
}
.pc-msg__link {
  border: none;
  background: transparent;
  color: var(--pc-primary, #2563eb);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  white-space: nowrap;
}
.pc-msg__link:hover {
  background: var(--pc-primary-soft, #eff4ff);
}
/* 蓝图卡一行元信息（单行省略，绝不换行撑高会话条） */
.pc-msg__meta-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--pc-text-muted, #6b7280);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 会话条内证据卡：标题 + 摘要（3 行钳制 + 省略渐隐） */
.pc-evref__title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text-2, #1f2937);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-evref__clip {
  position: relative;
  /* 2 行摘要上限：KaTeX 显示式在文本内产生块级子元素，-webkit-line-clamp 会失效，
     因此用「高度钳制 + 溢出隐藏」保证行数上限恒定（2 × 1.7em）。 */
  max-height: 3.4em;
  overflow: hidden;
}
.pc-evref.is-open .pc-evref__clip {
  max-height: none;
}
/* 行数省略：折叠态底部压一条白→透明的渐隐遮罩（行高不齐/公式行被截时的统一处理） */
.pc-evref:not(.is-open) .pc-evref__clip::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 1.4em;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0) 0%, #fff 80%);
  pointer-events: none;
}
/* 省略号：右下角浮一个 `…`（不遮挡正文语义） */
.pc-evref:not(.is-open) .pc-evref__clip::before {
  content: '…';
  position: absolute;
  right: 0;
  bottom: 0;
  z-index: 1;
  padding-left: 10px;
  background: #fff;
  color: var(--pc-text-faint, #9ca3af);
  line-height: 1.7;
}
.pc-evref__body {
  font-size: 12px;
  line-height: 1.7;
  color: var(--pc-text-muted, #6b7280);
}
.pc-msg__card-faint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--pc-text-faint, #9ca3af);
}
.pc-msg__card-body {
  margin: 0;
  font-size: 13px;
  color: var(--pc-text-2, #1f2937);
  line-height: 1.6;
}
.pc-msg__error {
  margin: 0;
  font-size: 13px;
  color: var(--pc-danger, #dc2626);
  line-height: 1.6;
}
.pc-msg__status {
  margin-top: 6px;
  font-size: 12px;
  color: var(--pc-text-faint, #9ca3af);
}
/* 排队态：中性蓝（不是错误红），措辞明确「会自动发送」 */
.pc-msg__status--queued {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
}
.pc-msg__status--error {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  border-radius: 8px;
  background: #fef2f2;
  border: 1px solid #f4d4d4;
  color: var(--pc-danger, #dc2626);
}
.pc-msg__retry {
  border: 1px solid var(--pc-danger, #dc2626);
  background: transparent;
  color: var(--pc-danger, #dc2626);
  border-radius: 999px;
  padding: 2px 12px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-msg__retry:hover {
  background: var(--pc-danger, #dc2626);
  color: #fff;
}

/* ── 蓝图确认卡（G10 / golden1）：备课信息浅蓝小节 + 教学意图 bullets + 下一步 + 动作行 ── */
/* golden1 ③：卡内浅蓝小节标题（原卡头标题条移除后的节标题形态） */
.pc-confirm__section {
  margin: 0 0 8px;
  padding: 7px 12px;
  border-radius: var(--pc-radius-badge, 8px);
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
  font-size: 13px;
  font-weight: 700;
}
/* golden1 ②：去边框，保留浅蓝底圆角 */
.pc-confirm__info {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 14px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--pc-primary-softer, #f5f8ff);
  margin-bottom: 10px;
}
.pc-confirm__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.pc-confirm__label {
  font-size: 12px;
  color: var(--pc-text-muted, #6b7280);
}
.pc-confirm__value {
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text, #0f172a);
  overflow-wrap: anywhere;
}
.pc-confirm__block {
  margin: 10px 0 0;
}
.pc-confirm__block-title {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 700;
  color: var(--pc-primary, #2563eb);
}
.pc-confirm__bullets {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--pc-text-2, #1f2937);
}
.pc-confirm__next {
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--pc-text-2, #1f2937);
}
.pc-confirm__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 12px;
}
/* golden1 ⑤：次按钮无边框浅底（主按钮维持实心蓝） */
.pc-confirm__btn {
  border: none;
  background: var(--pc-canvas, #eef2f8);
  color: var(--pc-text-2, #1f2937);
  border-radius: var(--pc-radius-btn, 8px);
  padding: 7px 16px;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-confirm__btn:hover:not(:disabled) {
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
}
.pc-confirm__btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.pc-confirm__btn--primary {
  background: var(--pc-primary, #2563eb);
  border-color: var(--pc-primary, #2563eb);
  color: #fff;
  font-weight: 600;
}
.pc-confirm__btn--primary:hover:not(:disabled) {
  background: var(--pc-primary-hover, #1d4fd8);
  color: #fff;
}
/* 卡内动作区脚行（golden1 ③：「查看教学流程」+ 右下时间戳） */
.pc-confirm__foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.pc-confirm__foot .pc-msg__card-time {
  margin-top: 0;
}
.pc-confirm__done {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--pc-primary, #2563eb);
}
.pc-decision__row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
