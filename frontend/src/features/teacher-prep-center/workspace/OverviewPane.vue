<script setup lang="ts">
/**
 * S7 本次备课 / 生成与导出 + 版本（VISUAL-SPEC-V3 §3-S7 / golden3.png）。
 *
 * - 面包屑 → 返回备课首页；头卡（图标 + 本次备课 + 标题 + 元信息 + 完成徽标 + 最后修改）；
 * - 信息条；备课完成情况 5 磁贴（标题/副行/勾选全部由真实数据驱动：教材依据 / blueprint /
 *   deck / lesson plan / deck QA；deck 为 null 时副行走「尚未生成课件」，禁止回退「课件已生成」）；
 * - 版本管理：真实 blueprint revision 列表（listBlueprintRevisions）；行主标题 = 版本语义摘要
 *   （后端 note；无 note 时取蓝图标题并剥掉章节号），绝不再重复会话标题/章节号；
 * - 导出备课成果：PPTX / PDF / 教案 三张格式卡，走真实导出链路
 *   （runExport：受理 → 真实阶段进度 → 签票下载）；失败给人话原因 + 重试；
 *   副行 = 真实页数 · 真实导出产物大小（取不到就省略，不估算）；
 * - 一致性检查（G21）：deck 与 lesson_plan 同源版本比对
 *   （approved_blueprint_revision / context_snapshot_hash），取不到 → 诚实「无法核对」。
 *
 * 反空壳：无真实后端的「保存为新版本」一律不渲染；页数/大小取不到就不显示数字。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { MINUTES_PER_LESSON_HOUR, usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  listBlueprintRevisions,
  PrepCenterApiError,
  type PrepBlueprintRevision,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import { getDeckQa, type DeckQaView } from '@/features/teacher-prep-center/api/deckApi'
import {
  formatBytes,
  getDeckArtifacts,
  getPlanArtifacts,
  runExport,
  type ArtifactSummary,
  type ExportFormat,
  type RunExportResult,
} from '@/features/teacher-prep-center/api/exportApi'
import {
  OVERVIEW as O,
  absoluteTime,
  formatSessionMeta,
  revisionSourceLabel,
  statusLabel,
  statusTone,
  textbookVersionSegment,
} from '@/features/teacher-prep-center/copy/prepCopy'
import PublishCoursewareDialog from '@/features/teacher-prep-center/publish/PublishCoursewareDialog.vue'

const router = useRouter()
const store = usePrepSessionStore()

const sessionId = computed(() => String(store.currentSessionId || ''))

/* ── 头卡 ───────────────────────────────────────────────────────── */
const title = computed(() => store.currentCourseTitle || '备课记录')
/** 头卡元信息：与顶栏同一 formatSessionMeta 口径（教材版本 · 班级 · 时长；班级按 class_id 解析）。 */
const meta = computed(() => {
  const ws = store.current
  if (!ws) return ''
  const hours = store.lessonHoursDisplay
  return formatSessionMeta({
    textbook: textbookVersionSegment(store.currentCourseMeta.textbook),
    className: store.resolveClassName(ws.session),
    duration: hours != null ? `${hours * MINUTES_PER_LESSON_HOUR}分钟` : null,
  })
})
const lastModified = computed(() => absoluteTime(store.current?.session.updated_at))

const deck = computed(() => store.current?.artifact.teaching_deck ?? null)
const lessonPlan = computed(() => store.current?.artifact.lesson_plan ?? null)
const deckId = computed(() => {
  const id = (deck.value as { deck_id?: unknown } | null)?.deck_id
  return typeof id === 'string' && id ? id : ''
})
const deckRevision = computed(() => {
  const r = (deck.value as { revision?: unknown } | null)?.revision
  return typeof r === 'number' ? r : 0
})
const planReady = computed(() => !!lessonPlan.value?.spec_id)
const planRevision = computed(() => lessonPlan.value?.revision ?? 0)

/* 发布到班级课堂：与导出并列的独立出口（发布 = 不可变快照，不替代导出）。 */
const publishOpen = ref(false)
const publishDeckTitle = computed(() => {
  const t = (deck.value as { title?: unknown } | null)?.title
  if (typeof t === 'string' && t.trim()) return t.trim()
  return store.currentCourseTitle || '本次课件'
})

/* ── 导出卡「页数·大小」真实数据源（反空壳：size_bytes=实际文件字节；未导出则不显示大小）── */
const deckArtifacts = ref<Partial<Record<string, ArtifactSummary>>>({})
const planArtifacts = ref<Partial<Record<string, ArtifactSummary>>>({})
/** 真实页数：导出产物面 `/decks/{id}/artifacts` 的 pages（= spec.slides 长度）；取不到 → null。 */
const deckPagesFromApi = ref<number | null>(null)

const deckPages = computed<number | null>(() => {
  if (deckPagesFromApi.value != null) return deckPagesFromApi.value
  const slides = (deck.value as { slides?: unknown } | null)?.slides
  return Array.isArray(slides) ? slides.length : null
})

async function loadArtifactSizes() {
  const dId = deckId.value
  const pId = lessonPlan.value?.spec_id ?? ''
  deckArtifacts.value = {}
  planArtifacts.value = {}
  deckPagesFromApi.value = null
  if (dId) {
    try {
      const v = await getDeckArtifacts(dId)
      deckArtifacts.value = v.artifacts ?? {}
      deckPagesFromApi.value = typeof v.pages === 'number' && v.pages >= 0 ? v.pages : null
    } catch {
      /* 读不到就不显示大小（诚实省略，不估算） */
    }
  }
  if (pId) {
    try {
      const v = await getPlanArtifacts(pId)
      planArtifacts.value = v.artifacts ?? {}
    } catch {
      /* 同上 */
    }
  }
}
watch([deckId, () => lessonPlan.value?.spec_id ?? ''], () => void loadArtifactSizes(), { immediate: true })

/** 真实大小文案（kind 对应格式的最近一次真实导出产物；无产物返回空串）。 */
function sizeOf(artifacts: Partial<Record<string, ArtifactSummary>>, kind: string): string {
  const a = artifacts[kind]
  return a ? formatBytes(a.size_bytes) : ''
}

/* ── 教学内容已检查：真实 deck QA（GET /decks/{id}/qa）────────────── */
const qa = ref<DeckQaView | null>(null)
async function loadQa(id: string) {
  if (!id) {
    qa.value = null
    return
  }
  try {
    qa.value = await getDeckQa(id)
  } catch {
    qa.value = null
  }
}
watch(deckId, (id) => void loadQa(id), { immediate: true })

/* ── 备课完成情况（5 chip，状态全部来自真实数据）──────────────────── */
interface Chip {
  key: string
  title: string
  desc: string
  done: boolean
}
const hasTextbook = computed(
  () => (store.current?.evidence?.length ?? 0) > 0 || !!store.current?.session.intent?.section_id || !!store.current?.artifact.context?.locator,
)
const blueprintConfirmed = computed(() => store.current?.artifact.blueprint?.status === 'confirmed')

const qaChecked = computed(() => qa.value?.status === 'pass')
/** 内容检查副行（唯一分支口径；ppt 磁贴同状态复用同一文案，禁止双分支互相矛盾）。 */
const qaDesc = computed(() => {
  if (!deck.value) return O.chipDeckPendingDesc
  if (!qa.value || qa.value.status === 'unknown') return '尚未进行内容检查'
  if (qa.value.status === 'fail') {
    const n = qa.value.summary?.p0_count ?? 0
    return n > 0 ? `发现 ${n} 处待修复问题` : '检查未通过'
  }
  return '公式、图形、排版检查通过'
})

/** 「教材已确认」chip 副文案：教材版本段 · 班级（唯一口径；无则回落中性文案）。 */
const textbookChipDesc = computed(() => {
  const ws = store.current
  if (!ws) return '教材依据已确认'
  const parts = [textbookVersionSegment(store.currentCourseMeta.textbook), store.resolveClassName(ws.session)].filter(Boolean)
  return parts.length ? parts.join(' · ') : '教材依据已确认'
})

/** ppt 磁贴副行（真实数据）：有课件 → 真实页数 · 真实导出产物大小（取不到的部分诚实省略）；
 *  无课件 → 与 qaDesc 同一「尚未生成课件」分支（修掉 deck=null 仍回退「课件已生成」的假状态）。 */
const deckChipDesc = computed(() => {
  if (!deck.value) return O.chipDeckPendingDesc
  const parts: string[] = []
  if (deckPages.value != null) parts.push(O.pagesCount(deckPages.value))
  const size = sizeOf(deckArtifacts.value, 'pptx')
  if (size) parts.push(size)
  return parts.join(' · ') || O.chipDeckReadyDesc
})
/** plan 磁贴副行：有教案 → 教案内容口径；无 → 诚实待生成。 */
const planChipDesc = computed(() => (planReady.value ? O.chipPlanDesc : O.chipPlanPendingDesc))

const chips = computed<Chip[]>(() => [
  {
    key: 'textbook',
    title: hasTextbook.value ? O.chipTextbook : O.chipTextbookPending,
    desc: textbookChipDesc.value,
    done: hasTextbook.value,
  },
  {
    key: 'design',
    title: blueprintConfirmed.value ? O.chipDesign : O.chipDesignPending,
    desc: O.chipDesignDesc,
    done: blueprintConfirmed.value,
  },
  {
    key: 'ppt',
    title: deck.value ? O.chipPpt : O.chipPptPending,
    desc: deckChipDesc.value,
    done: !!deck.value,
  },
  {
    key: 'plan',
    title: planReady.value ? O.chipPlan : O.chipPlanPending,
    desc: planChipDesc.value,
    done: planReady.value,
  },
  {
    key: 'check',
    title: qaChecked.value ? O.chipCheck : O.chipCheckPending,
    desc: qaDesc.value,
    done: qaChecked.value,
  },
])

const allDone = computed(() => !!deck.value && planReady.value)
const badgeLabel = computed(() => (allDone.value ? O.doneBadge : statusLabel(store.current?.session.status)))
const badgeTone = computed(() => (allDone.value ? 'done' : statusTone(store.current?.session.status)))
const infoBarText = computed(() =>
  allDone.value ? O.infoBar : '本次备课尚未全部完成，可继续在课堂设计 / PPT / 教案中完善。',
)

/* ── 一致性检查（G21）：同源版本真实比对 ─────────────────────────────
 * 判定数据只来自 deck 与 lesson_plan 各自的版本锚点
 * （approved_blueprint_revision / context_snapshot_hash）：
 * 两侧同名锚点都在且相等 → 一致；任一锚点两侧都在但不等 → 不一致；
 * 锚点取不到 / 产物缺失 → 诚实「无法核对」（绝不猜一致）。 */
type ConsistencyState = 'equal' | 'stale' | 'unknown'
interface Lineage {
  rev: number | null
  hash: string | null
}

function lineageOf(v: Record<string, unknown> | null): Lineage {
  const proj = (v?.projection ?? {}) as Record<string, unknown>
  const pick = (key: string): unknown => v?.[key] ?? proj[key]
  const rev = pick('approved_blueprint_revision')
  const hash = pick('context_snapshot_hash')
  return {
    rev: typeof rev === 'number' && Number.isFinite(rev) ? rev : null,
    hash: typeof hash === 'string' && hash ? hash : null,
  }
}

const consistency = computed<{ state: ConsistencyState; desc: string }>(() => {
  const d = deck.value as Record<string, unknown> | null
  const p = lessonPlan.value as Record<string, unknown> | null
  if (!d || !p) return { state: 'unknown', desc: O.consistencyDescUnknown }
  const a = lineageOf(d)
  const b = lineageOf(p)
  const pairs: [unknown, unknown][] = []
  if (a.rev != null && b.rev != null) pairs.push([a.rev, b.rev])
  if (a.hash && b.hash) pairs.push([a.hash, b.hash])
  if (!pairs.length) return { state: 'unknown', desc: O.consistencyDescNoLineage }
  if (pairs.some(([x, y]) => x !== y)) return { state: 'stale', desc: O.consistencyDescStale }
  return { state: 'equal', desc: O.consistencyDescEqual }
})
const consistencyPill = computed(() =>
  consistency.value.state === 'equal'
    ? O.consistencyEqual
    : consistency.value.state === 'stale'
      ? O.consistencyStale
      : O.consistencyUnknown,
)

/* ── 版本管理（真实 blueprint revision）──────────────────────────── */
const revisions = ref<PrepBlueprintRevision[]>([])
const currentRevision = ref<number | null>(null)
const revLoading = ref(false)
const revFailed = ref(false)

async function loadRevisions(id: string) {
  if (!id) return
  revLoading.value = true
  revFailed.value = false
  try {
    const data = await listBlueprintRevisions(id)
    revisions.value = data.items ?? []
    currentRevision.value = data.current_revision ?? null
  } catch {
    revisions.value = []
    revFailed.value = true
  } finally {
    revLoading.value = false
  }
}
watch(sessionId, (id) => void loadRevisions(id), { immediate: true })

/** 剥掉章节号（如 `3.1.2 椭圆的简单几何性质` → `椭圆的简单几何性质`）。 */
function stripChapterNo(t: string): string {
  return t.replace(/^\s*\d+(?:\.\d+)*\s*/, '').trim()
}

/**
 * 版本行主标题（黄金图版本行为语义名）：优先后端 note（版本语义摘要，
 * 如「AI 定向修改环节 2：探究椭圆的范围与对称性」）；无 note 时取蓝图标题并剥掉章节号。
 * **绝不**把「章节号 + 会话标题」重复上屏（原缺陷：两行显示同一串文本）。
 */
function revTitle(item: PrepBlueprintRevision): string {
  const note = (item.note || '').trim()
  if (note) return note
  const raw = (item.body as { title?: unknown } | undefined)?.title
  const t = typeof raw === 'string' && raw ? raw : item.title || ''
  return stripChapterNo(t) || '教学蓝图'
}
/** 版本行副标题：环节数 + 总时长（与主标题不同源，禁止两行同文）。 */
function revSummary(item: PrepBlueprintRevision): string {
  const flow = item.body?.lesson_flow
  const stages = Array.isArray(flow) ? flow.length : item.stages
  return `${stages} 个环节 · ${item.total_minutes ? `${item.total_minutes} 分钟` : '时长未设置'}`
}
function openAllVersions() {
  if (sessionId.value) void store.openHistory(sessionId.value)
}

/* ── 导出备课成果（真实链路）────────────────────────────────────── */
type CardKey = 'deck:pptx' | 'deck:pdf' | 'lesson_plan:pdf'
interface ExportCard {
  key: CardKey
  kind: 'deck' | 'lesson_plan'
  format: ExportFormat
  title: string
  desc: string
}
const EXPORT_CARDS: ExportCard[] = [
  { key: 'deck:pptx', kind: 'deck', format: 'pptx', title: O.exportPptx, desc: '' },
  { key: 'deck:pdf', kind: 'deck', format: 'pdf', title: O.exportPdf, desc: '' },
  { key: 'lesson_plan:pdf', kind: 'lesson_plan', format: 'pdf', title: O.exportPlan, desc: '' },
]

interface Slot {
  stage: string
  error: string
  filename: string
  blob: Blob | null
}
const slots = ref<Record<CardKey, Slot>>({
  'deck:pptx': { stage: '', error: '', filename: '', blob: null },
  'deck:pdf': { stage: '', error: '', filename: '', blob: null },
  'lesson_plan:pdf': { stage: '', error: '', filename: '', blob: null },
})
const activeKey = ref<CardKey | null>(null)
const abort = ref<AbortController | null>(null)

/** 导出卡副行（真实数据）：课件卡 = 真实页数 · 对应格式真实产物大小；教案卡 = 真实产物大小；
 *  页数/大小任一取不到就省略该段（不估算、不假填）。 */
function cardDesc(card: ExportCard): string {
  const parts: string[] = []
  if (card.kind === 'deck' && deckPages.value != null) parts.push(O.pagesCount(deckPages.value))
  const size = sizeOf(card.kind === 'deck' ? deckArtifacts.value : planArtifacts.value, card.format)
  if (size) parts.push(size)
  return parts.join(' · ')
}
function readyOf(card: ExportCard): boolean {
  return card.kind === 'deck' ? !!deckId.value : planReady.value
}
function targetOf(card: ExportCard): { id: string; revision: number } | null {
  if (card.kind === 'deck') return deckId.value ? { id: deckId.value, revision: deckRevision.value } : null
  return lessonPlan.value?.spec_id ? { id: lessonPlan.value.spec_id, revision: planRevision.value } : null
}

function errText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    const d = e.detail
    if (typeof d === 'string' && d.trim()) return d
    return e.message && !e.message.includes('[object Object]') ? e.message : '导出失败，请稍后重试。'
  }
  if (e instanceof Error) return e.message
  return '导出失败，请稍后重试。'
}

function triggerDownload(blob: Blob, filename: string) {
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

async function startExport(card: ExportCard) {
  if (abort.value) return
  const target = targetOf(card)
  if (!target) return
  const slot = slots.value[card.key]
  slot.stage = '正在提交导出任务…'
  slot.error = ''
  slot.filename = ''
  slot.blob = null
  activeKey.value = card.key
  const controller = new AbortController()
  abort.value = controller
  const exec = (t: { id: string; revision: number }): Promise<RunExportResult> =>
    runExport({
      kind: card.kind,
      id: t.id,
      format: card.format,
      expectedRevision: t.revision,
      signal: controller.signal,
      onStage: (label) => {
        slot.stage = label
      },
    })
  try {
    let result: RunExportResult
    try {
      result = await exec(target)
    } catch (e) {
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        slot.stage = '内容已更新，正在重新导出…'
        if (sessionId.value) await store.refreshCurrent(sessionId.value)
        const fresh = targetOf(card)
        if (!fresh) throw e
        result = await exec(fresh)
      } else {
        throw e
      }
    }
    slot.blob = result.blob
    slot.filename = result.filename
    slot.stage = '导出完成'
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') {
      slot.stage = ''
      return
    }
    slot.error = errText(e)
    slot.stage = ''
  } finally {
    abort.value = null
    activeKey.value = null
  }
}

function downloadSlot(card: ExportCard) {
  const slot = slots.value[card.key]
  if (slot.blob && slot.filename) triggerDownload(slot.blob, slot.filename)
}
function retrySlot(card: ExportCard) {
  const slot = slots.value[card.key]
  slot.error = ''
  slot.blob = null
  slot.filename = ''
  void startExport(card)
}

onBeforeUnmount(() => abort.value?.abort())
</script>

<template>
  <section class="pc-ov">
    <button type="button" class="pc-ov__back" @click="router.push('/teacher-v3/prep')">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M9.6 4 5.6 8l4 4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      {{ O.back }}
    </button>

    <!-- 头卡 -->
    <header class="pc-ov__head">
      <span class="pc-ov__head-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M12 5.4C10 4 7 3.4 3.4 3.4v14.2c3.6 0 6.6.6 8.6 2 2-1.4 5-2 8.6-2V3.4c-3.6 0-6.6.6-8.6 2Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
          <path d="M12 5.4v14.2" stroke="currentColor" stroke-width="1.2" />
        </svg>
      </span>
      <div class="pc-ov__head-text">
        <p class="pc-ov__kicker">{{ O.kicker }}</p>
        <h2 class="pc-ov__title">{{ title }}</h2>
        <p v-if="meta" class="pc-ov__meta">{{ meta }}</p>
      </div>
      <div class="pc-ov__head-side">
        <span class="pc-ov__badge" :class="`is-${badgeTone}`">{{ badgeLabel }}</span>
        <span v-if="lastModified" class="pc-ov__modified">{{ O.lastModifiedPrefix }}{{ lastModified }}</span>
      </div>
    </header>

    <!-- 信息条 -->
    <p class="pc-ov__info">
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3" />
        <path d="M8 7.4v3.6M8 5.2h.01" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
      </svg>
      {{ infoBarText }}
    </p>

    <!-- 备课完成情况 -->
    <section class="pc-card">
      <h3 class="pc-card__title">
        <svg class="pc-card__icon" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3.4 8.4 6.4 11.4 12.6 5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ O.completionTitle }}
      </h3>
      <div class="pc-ov__chips">
        <div
          v-for="c in chips"
          :key="c.key"
          class="pc-ov__chip"
          :class="{ 'is-done': c.done }"
          data-testid="ov-chip"
          :data-key="c.key"
          :data-done="c.done ? '1' : '0'"
        >
          <span class="pc-ov__chip-check" aria-hidden="true">
            <svg v-if="c.done" viewBox="0 0 16 16"><path d="M3.6 8.4 6.6 11.4 12.4 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
            <svg v-else viewBox="0 0 16 16"><circle cx="8" cy="8" r="4.4" fill="none" stroke="currentColor" stroke-width="1.4" /></svg>
          </span>
          <span class="pc-ov__chip-title" data-testid="ov-chip-title">{{ c.title }}</span>
          <span class="pc-ov__chip-desc" data-testid="ov-chip-desc">{{ c.desc }}</span>
        </div>
      </div>
    </section>

    <div class="pc-ov__cols">
      <!-- 版本管理 -->
      <section class="pc-card">
        <h3 class="pc-card__title">
          <svg class="pc-card__icon" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3" />
            <path d="M8 4.8V8l2.2 1.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
          </svg>
          {{ O.versionsTitle }}
        </h3>

        <p v-if="revLoading" class="pc-ov__state">正在加载版本…</p>
        <p v-else-if="revFailed" class="pc-ov__state">
          版本列表加载失败
          <button type="button" class="pc-ov__link" @click="loadRevisions(sessionId)">重试</button>
        </p>
        <ul v-else-if="revisions.length" class="pc-ov__revs">
          <li
            v-for="item in revisions"
            :key="item.revision_no"
            class="pc-ov__rev"
            :class="{ 'is-current': item.revision_no === currentRevision }"
          >
            <span class="pc-ov__rev-no">V{{ item.revision_no }}</span>
            <span class="pc-ov__rev-main">
              <span class="pc-ov__rev-title">{{ revTitle(item) }}</span>
              <span class="pc-ov__rev-sum">{{ revSummary(item) }}</span>
            </span>
            <span class="pc-ov__rev-time">{{ absoluteTime(item.created_at) }}</span>
            <span class="pc-ov__rev-author">{{ revisionSourceLabel(item.source) }}</span>
            <span v-if="item.revision_no === currentRevision" class="pc-ov__rev-badge">{{ O.currentVersion }}</span>
          </li>
        </ul>
        <p v-else class="pc-ov__state">该备课暂无历史版本。</p>

        <button v-if="revisions.length" type="button" class="pc-ov__link pc-ov__link--block" @click="openAllVersions">
          {{ O.viewAllVersions }} ›
        </button>
      </section>

      <!-- 导出备课成果 + 一致性检查（右列） -->
      <div class="pc-ov__col">
      <section class="pc-card">
        <h3 class="pc-card__title">
          <svg class="pc-card__icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 2.2v7.6m0 0 2.9-2.9M8 9.8 5.1 6.9M3.2 12.2V13.6h9.6v-1.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ O.exportTitle }}
        </h3>
        <p class="pc-ov__export-desc">{{ O.exportDesc }}</p>

        <ul class="pc-ov__exports">
          <li v-for="card in EXPORT_CARDS" :key="card.key" class="pc-ov__export">
            <div class="pc-ov__export-head">
              <span class="pc-ov__export-title">{{ card.title }}</span>
              <span v-if="cardDesc(card)" class="pc-ov__export-sub" data-testid="ov-export-sub" :data-key="card.key">{{ cardDesc(card) }}</span>
            </div>
            <p v-if="slots[card.key].stage" class="pc-ov__export-stage">
              <span v-if="activeKey === card.key" class="pc-ov__spinner" aria-hidden="true"></span>
              {{ slots[card.key].stage }}
            </p>
            <p v-if="slots[card.key].error" class="pc-ov__export-error">{{ slots[card.key].error }}</p>
            <button
              v-if="slots[card.key].blob"
              type="button"
              class="pc-ov__btn is-primary"
              @click="downloadSlot(card)"
            >
              {{ O.exportDownload }}
            </button>
            <button
              v-else-if="slots[card.key].error"
              type="button"
              class="pc-ov__btn"
              @click="retrySlot(card)"
            >
              {{ O.exportRetry }}
            </button>
            <button
              v-else
              type="button"
              class="pc-ov__btn"
              :disabled="!readyOf(card) || !!activeKey"
              :title="readyOf(card) ? O.exportStart : O.exportNotReady"
              @click="startExport(card)"
            >
              {{ readyOf(card) ? O.exportStart : O.exportNotReady }}
            </button>
          </li>
        </ul>

        <!-- 发布到班级课堂（与导出并列的真实出口：学生端 AI 主讲 + 学情回流） -->
        <div class="pc-ov__publish" data-testid="ov-publish-row">
          <div class="pc-ov__publish-text">
            <span class="pc-ov__publish-title">发布到班级课堂</span>
            <span class="pc-ov__publish-desc">
              学生端「课堂」按这份课件逐页学；已打开人数、进度与练习正确率实时回到「课堂 · 双师课堂」。
            </span>
          </div>
          <button
            type="button"
            class="pc-ov__btn is-primary pc-ov__publish-btn"
            :disabled="!deckId"
            :title="deckId ? '选择班级并发布当前课件快照' : '先完成课件生成'"
            data-testid="ov-publish"
            @click="publishOpen = true"
          >
            发布到班级课堂
          </button>
        </div>
      </section>

      <!-- 一致性检查（G21）：同源版本比对（approved_blueprint_revision / context_snapshot_hash）；
           锚点取不到 → 诚实「无法核对」，绝不猜一致 -->
      <section class="pc-card pc-ov__consistency" data-testid="ov-consistency" :data-state="consistency.state">
        <h3 class="pc-card__title">
          <svg class="pc-card__icon" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 1.9 13 3.7v3.9c0 2.9-2 5.3-5 6.5-3-1.2-5-3.6-5-6.5V3.7L8 1.9Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
            <path d="M5.8 7.9 7.4 9.5 10.4 6.3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ O.consistencyTitle }}
          <span
            class="pc-ov__pill"
            :class="`is-${consistency.state}`"
            data-testid="ov-consistency-pill"
          >{{ consistencyPill }}</span>
        </h3>
        <p class="pc-ov__consistency-desc">{{ consistency.desc }}</p>
        <p class="pc-ov__info pc-ov__info--nested">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.3" />
            <path d="M8 7.4v3.6M8 5.2h.01" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          </svg>
          {{ O.consistencyInfo }}
        </p>
      </section>
      </div>
    </div>

    <!-- 发布到班级课堂 -->
    <PublishCoursewareDialog
      :open="publishOpen"
      :session-id="sessionId"
      :deck-id="deckId"
      :deck-title="publishDeckTitle"
      @close="publishOpen = false"
    />
  </section>
</template>

<style scoped>
.pc-ov {
  padding: 20px 24px 160px;
  font-family: var(--pc-font);
  max-width: 1320px;
  margin: 0 auto;
}
.pc-ov__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 12px;
  padding: 0;
  border: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-ov__back:hover {
  color: var(--pc-primary);
}
.pc-ov__back svg {
  width: 14px;
  height: 14px;
}

/* 头卡 */
.pc-ov__head {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-panel);
}
.pc-ov__head-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-ov__head-icon svg {
  width: 24px;
  height: 24px;
}
.pc-ov__head-text {
  min-width: 0;
}
.pc-ov__kicker {
  margin: 0;
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-ov__title {
  margin: 2px 0 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-ov__meta {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-ov__head-side {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.pc-ov__badge {
  padding: 3px 12px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  font-weight: 600;
}
.pc-ov__badge.is-done {
  background: var(--pc-success-soft);
  color: var(--pc-success);
}
.pc-ov__badge.is-draft {
  background: var(--pc-warning-soft);
  color: var(--pc-warning);
}
.pc-ov__badge.is-danger {
  background: var(--pc-danger-soft);
  color: var(--pc-danger);
}
.pc-ov__badge.is-muted {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-ov__modified {
  font-size: 12px;
  color: var(--pc-text-faint);
}

/* 信息条 */
.pc-ov__info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 0;
  padding: 10px 14px;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary-softer);
  font-size: 13px;
  color: var(--pc-text-2);
}
.pc-ov__info svg {
  flex: none;
  width: 15px;
  height: 15px;
  color: var(--pc-primary);
}

/* 卡片 */
.pc-card {
  padding: 16px 18px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-card__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-card__icon {
  width: 16px;
  height: 16px;
  color: var(--pc-primary);
}

/* 完成情况 chips */
.pc-ov__chips {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.pc-ov__chip {
  position: relative;
  padding: 14px 14px 14px 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-bg);
}
.pc-ov__chip.is-done {
  background: var(--pc-panel);
}
.pc-ov__chip-check {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 16px;
  height: 16px;
  color: var(--pc-text-faint);
}
.pc-ov__chip.is-done .pc-ov__chip-check {
  color: var(--pc-success);
}
.pc-ov__chip-check svg {
  width: 16px;
  height: 16px;
}
.pc-ov__chip-title {
  display: block;
  padding-right: 20px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-ov__chip-desc {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--pc-text-muted);
}

/* 两列 */
.pc-ov__cols {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 16px;
  margin-top: 16px;
}
/* 右列（导出 + 一致性检查纵向堆叠） */
.pc-ov__col {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* 一致性检查 */
.pc-ov__consistency-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text-2);
}
.pc-ov__pill {
  margin-left: auto;
  padding: 3px 12px;
  border-radius: var(--pc-radius-full);
  font-size: 12px;
  font-weight: 600;
}
.pc-ov__pill.is-equal {
  background: var(--pc-success-soft);
  color: var(--pc-success);
}
.pc-ov__pill.is-stale {
  background: var(--pc-warning-soft);
  color: var(--pc-warning);
}
.pc-ov__pill.is-unknown {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-ov__info--nested {
  margin: 12px 0 0;
}

/* 版本行 */
/* 列表最多显示 4 行（行高 64 + 行距 8），超出走内部滚动 —— 保证卡片底部
   （含「查看全部版本」）在 941 视口内可见（原缺陷：第 5/6 行被裁在屏幕下边缘）。 */
.pc-ov__revs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 280px;
  overflow-y: auto;
}
.pc-ov__rev {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
}
.pc-ov__rev.is-current {
  background: var(--pc-primary-soft);
  border-color: var(--pc-primary);
}
.pc-ov__rev-no {
  flex: none;
  min-width: 30px;
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--pc-primary);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  text-align: center;
}
.pc-ov__rev-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pc-ov__rev-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-ov__rev-sum {
  font-size: 12px;
  color: var(--pc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-ov__rev-time {
  flex: none;
  font-size: 12px;
  color: var(--pc-text-faint);
}
.pc-ov__rev-author {
  flex: none;
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-ov__rev-badge {
  flex: none;
  padding: 2px 8px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  color: var(--pc-primary);
  font-size: 11px;
  font-weight: 600;
}
.pc-ov__state {
  margin: 0;
  padding: 12px 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-ov__link {
  border: none;
  background: transparent;
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-ov__link--block {
  display: block;
  margin-top: 12px;
  padding: 0;
}

/* 导出卡 */
.pc-ov__export-desc {
  margin: 0 0 12px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--pc-text-muted);
}
.pc-ov__exports {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.pc-ov__export {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-bg);
}
.pc-ov__export-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.pc-ov__export-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-ov__export-sub {
  font-size: 12px;
  color: var(--pc-text-muted);
}

/* 发布到班级课堂（导出卡内的并列出口） */
.pc-ov__publish {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 12px;
  padding: 12px 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-primary-softer);
}
.pc-ov__publish-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pc-ov__publish-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-ov__publish-desc {
  font-size: 12px;
  line-height: 1.55;
  color: var(--pc-text-muted);
}
.pc-ov__publish-btn {
  width: auto;
  flex: none;
  margin-top: 0;
}
.pc-ov__export-stage {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: 12.5px;
  color: var(--pc-primary);
}
.pc-ov__export-error {
  margin: 8px 0 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--pc-danger);
}
.pc-ov__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--pc-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-ov-spin 0.8s linear infinite;
}
@keyframes pc-ov-spin {
  to {
    transform: rotate(360deg);
  }
}
.pc-ov__btn {
  width: 100%;
  margin-top: auto;
  height: 30px;
  padding: 0 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
}
.pc-ov__btn:hover:not(:disabled) {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-ov__btn.is-primary {
  border-color: transparent;
  background: var(--pc-primary);
  color: #fff;
}
.pc-ov__btn.is-primary:hover:not(:disabled) {
  background: var(--pc-primary-hover);
  color: #fff;
}
.pc-ov__btn:disabled {
  cursor: not-allowed;
  color: var(--pc-text-faint);
  background: var(--pc-bg);
}
</style>
