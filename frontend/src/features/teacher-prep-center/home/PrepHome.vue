<script setup lang="ts">
/**
 * 备课中心首页（/teacher-v3/prep）—— S1（VISUAL-SPEC-V3 §3 + GOLDEN_OBSERVATION_HOME）。
 *
 * 结构：左导航（home 形态）+ 右侧主区：
 *   页标题行（备课中心 + 副标题 + 教学资源库/最近使用）
 *   → NL 胶囊主输入卡（真实创建会话 → 跳工作台）
 *   → 四个入口卡（等宽 4 列，全部真实可用；golden2 固定卡组，基线裁决 3）
 *   → 「最近备课 N」+ 查看全部 → 列表行（类型色图标 + 班级元信息 + 两句灰色描述 + 状态徽标 + 最近修改 + 继续/查看 + ···）
 *   → 右下「AI 备课助手」胶囊（聚焦 NL 输入）
 *
 * 铁律：
 * - 数据全部来自真实后端（store.fetchSessions → GET /prep/sessions）；已归档会话不显示；
 * - 元信息只含 `教材版本 · 班级 · 时长`，**章节号一律不进元信息**；班级缺失诚实省略；
 * - 禁止后端错误原文上屏；每卡/每按钮 = 真实动作或整项不渲染；
 * - 一屏可见，不出现纵向滚动条（反失重）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
// --pc-* 视觉 token 必须随路由根组件注入（index.ts 的出口不参与路由加载）
import '@/features/teacher-prep-center/tokens/prep.css'
import { useToastStore } from '@/stores/toast'
import {
  MINUTES_PER_LESSON_HOUR,
  usePrepSessionStore,
} from '@/features/teacher-prep-center/stores/prepSession'
import type { PrepSessionSummary } from '@/features/teacher-prep-center/api/prepCenterApi'
import {
  HOME,
  absoluteTime,
  formatSessionMeta,
  statusLabel,
  statusTone,
  textbookVersionSegment,
} from '@/features/teacher-prep-center/copy/prepCopy'
import type { V3ClassInfo } from '@/types/teacherV3'

const router = useRouter()
const store = usePrepSessionStore()
const toast = useToastStore()

/** 首页默认展示条数（超出由「查看全部」展开，避免一次渲染长列表）。 */
const PAGE_SIZE = 5

const nlInput = ref('')
const expanded = ref(false)
const nlBarEl = ref<HTMLDivElement | null>(null)
const nlInputEl = ref<HTMLTextAreaElement | null>(null)
const recentEl = ref<HTMLElement | null>(null)
const createStartedAt = ref<number | null>(null)
const elapsedSeconds = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null

const creating = computed(() => store.creating)
const canSend = computed(() => nlInput.value.trim().length > 0 && !store.creating)

/* ── 班级选择胶囊（真实班级目录 `GET /teacher-v3/classes`；无数据不渲染）── */
const classMenuOpen = ref(false)
const selectedClassId = ref<string | null>(null)

function classLabelOf(c: V3ClassInfo): string {
  return (c.name ?? '').trim() || c.class_id
}
const selectedClassLabel = computed(() => {
  const c = store.classes.find((x) => x.class_id === selectedClassId.value)
  return c ? classLabelOf(c) : ''
})
function pickClass(classId: string) {
  selectedClassId.value = classId
  classMenuOpen.value = false
}

/* ── 最近备课（真实列表；archived 已在 store.fetchSessions 过滤）────── */
const sessions = computed(() => store.sessions)
const hasSessions = computed(() => sessions.value.length > 0)
const visibleSessions = computed(() =>
  expanded.value ? sessions.value : sessions.value.slice(0, PAGE_SIZE),
)
const canExpand = computed(() => sessions.value.length > PAGE_SIZE)

const isPermissionError = computed(
  () => store.sessionsErrorKind === 'forbidden' || store.sessionsErrorKind === 'unauthenticated',
)
const listErrorMessage = computed(() =>
  isPermissionError.value ? HOME.listForbidden : store.sessionsError || HOME.listFailed,
)

function sessionTitle(s: PrepSessionSummary): string {
  return (
    (s.title ?? '').trim() ||
    s.intent?.knowledge_points?.[0] ||
    s.intent?.lesson_scope ||
    HOME.untitledFallback
  )
}

/** 教材展示名压缩为「版本段」（唯一来源 textbookVersionSegment；形如 `人教A版`，冗余年份已去）。 */
function compactTextbook(s: PrepSessionSummary): string {
  return textbookVersionSegment(store.sourceTitleOf(s.intent?.source_id))
}

/** 元信息：`教材版本 · 班级 · 时长`（唯一拼接口径 formatSessionMeta；章节号不出现）。 */
function sessionMeta(s: PrepSessionSummary): string {
  const hours = store.normalizeLessonHours(s.intent?.lesson_hours)
  return formatSessionMeta({
    textbook: compactTextbook(s),
    className: store.resolveClassName(s),
    duration: hours != null ? `${hours * MINUTES_PER_LESSON_HOUR}分钟` : null,
  })
}

/** 行中部「两句灰色描述」（golden2 列表行七要素之第 3 要素，每行固定 2 行灰字）。
 *  数据源只用真实字段（教学目标 > 知识点 > 备课范围）；与标题互为回声的片段剔除
 *  （杜绝「标题回声」假描述）；不足 2 句用诚实回退——由真实课题名派生的一句摘要，
 *  仍不足则「暂无描述」。禁止编造教学内容。 */
function sessionDescLines(s: PrepSessionSummary): [string, string] {
  const title = sessionTitle(s)
  const isEcho = (t: string) => !!t && (title.includes(t) || t.includes(title))
  const lines: string[] = []
  const objs = Array.isArray(s.intent?.objectives) ? (s.intent?.objectives as { text?: string }[]) : []
  for (const o of objs) {
    const t = (o?.text ?? '').trim()
    if (t && !isEcho(t)) lines.push(t)
    if (lines.length >= 2) break
  }
  if (lines.length < 2) {
    const kps = (s.intent?.knowledge_points ?? [])
      .map((k) => (k ?? '').trim())
      .filter((k) => k && !isEcho(k))
    if (kps.length) lines.push(`${HOME.descKnowledgePrefix}${kps.join('、')}`)
  }
  if (lines.length < 2) {
    const scope = (s.intent?.lesson_scope ?? '').trim()
    if (scope && !isEcho(scope)) lines.push(`${HOME.descScopePrefix}${scope}`)
  }
  if (lines.length < 2) lines.push(HOME.descSummary(title))
  while (lines.length < 2) lines.push(HOME.descNoData)
  return [lines[0], lines[1]]
}

/** 行类型图标（golden2「每类多彩类型图标」）：按会话真实 intent 内容形态着色——
 *  有教学目标输入 → 文档/翠绿（教案设计型）；已锚定教材章节（source_id/section_id）→
 *  书本/靛蓝（教材型）；仅知识点输入 → 图表/琥珀；无内容 → 时钟/中性灰。
 *  相同真实形态同色，不臆造教材/教案/PPT 之外的假类型。 */
function rowKindOf(s: PrepSessionSummary): { icon: string; tone: 'indigo' | 'emerald' | 'amber' | 'muted' } {
  const intent = s.intent
  if (Array.isArray(intent?.objectives) && intent.objectives.length) return { icon: 'file', tone: 'emerald' }
  if (intent?.source_id || intent?.section_id) return { icon: 'book', tone: 'indigo' }
  if ((intent?.knowledge_points ?? []).length) return { icon: 'chart', tone: 'amber' }
  return { icon: 'clock', tone: 'muted' }
}

function sessionModified(s: PrepSessionSummary): string {
  return absoluteTime(s.updated_at || s.created_at)
}

/** 行右侧按钮：已完成 → 查看；其余（进行中/草稿）→ 继续备课。 */
function rowCta(s: PrepSessionSummary): string {
  return statusTone(s.status) === 'done' ? HOME.viewCta : HOME.continueCta
}

/* ── 真实动作 ─────────────────────────────────────────────────────── */
function focusInput() {
  nlBarEl.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  nlInputEl.value?.focus()
}

/** 把引导语预填进 NL 主输入并聚焦（落进真实 NL 链路，非装饰按钮）。 */
function prefill(text: string) {
  nlInput.value = text
  focusInput()
}

function scrollToRecent() {
  recentEl.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

function autoGrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 96)}px`
}

function openSession(s: PrepSessionSummary) {
  void router.push(`/teacher-v3/prep/w/${s.session_id}/classroom`)
}

function go(path: string) {
  void router.push(path)
}

function reload() {
  void store.fetchSessions()
}

async function submitNl() {
  const message = nlInput.value.trim()
  if (!message || store.creating) return
  createStartedAt.value = Date.now()
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => {
    if (createStartedAt.value != null) {
      elapsedSeconds.value = Math.floor((Date.now() - createStartedAt.value) / 1000)
    }
  }, 1000)
  const sessionId = await store.createFromMessage(message, selectedClassId.value)
  stopTimer()
  if (sessionId) void router.push(`/teacher-v3/prep/w/${sessionId}/classroom`)
  /* 失败：createError 已置位（产品化中文），页内联软提示展示，不阻断继续操作 */
}

function stopTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
  createStartedAt.value = null
}

/* ── 行操作菜单（删除 = 真实调用后端归档接口）────────────────────── */
const menuFor = ref<string | null>(null)
const pendingDelete = ref<PrepSessionSummary | null>(null)
const deleting = ref(false)
const deleteError = ref('')

function toggleMenu(s: PrepSessionSummary) {
  menuFor.value = menuFor.value === s.session_id ? null : s.session_id
}

function askDelete(s: PrepSessionSummary) {
  menuFor.value = null
  deleteError.value = ''
  pendingDelete.value = s
}

function cancelDelete() {
  if (deleting.value) return
  pendingDelete.value = null
  deleteError.value = ''
}

async function confirmDelete() {
  const s = pendingDelete.value
  if (!s || deleting.value) return
  deleting.value = true
  deleteError.value = ''
  const err = await store.archiveSession(s.session_id, s.revision)
  deleting.value = false
  if (err) {
    deleteError.value = err
    return
  }
  pendingDelete.value = null
  toast.success(HOME.deleteDone)
}

/* ── 入口卡（固定 4 卡 = golden2 / 基线裁决 3）──────────────────────── */
type EntryTone = 'indigo' | 'emerald' | 'rose' | 'sky'

interface EntryCard {
  key: string
  title: string
  desc: string
  tone: EntryTone
  icon: string
  run: () => void
}

/** 入口卡（固定 4 卡：从教材开始/从已有教案开始/从已有PPT开始/备课历史）。
 *  基线裁决 3：第 3 卡「从已有PPT开始」按 Golden 恢复（红 PPT 徽标），「继续备课」
 *  从 4 卡移除——其价值由列表行「继续备课/查看」行动作承载。
 *  宪法 IA：第 4 卡原「教学资源库」跳平台 /teacher-v3/resources 属平台重复入口，改
 *  「备课历史」（真实路由 /teacher-v3/prep/history，备课中心自身能力，非平台二级系统）。
 *  每卡必须真实可用：①③ 落进真实 NL 创建链路（预填 + 聚焦，提交即建真实会话），
 *  ②④ 真实路由跳转；不可用者整卡不渲染（VISUAL-SPEC-V3 §3-S1 / §5.2）。 */
const ENTRIES: EntryCard[] = [
  {
    key: 'textbook',
    title: HOME.entryFromTextbook,
    desc: HOME.entryFromTextbookDesc,
    tone: 'indigo',
    icon: 'book',
    run: () => prefill(HOME.prefillChapter),
  },
  {
    key: 'plan',
    title: HOME.entryFromPlan,
    desc: HOME.entryFromPlanDesc,
    tone: 'emerald',
    icon: 'file',
    run: () => go('/teacher-v3/prep-templates'),
  },
  {
    key: 'ppt',
    title: HOME.entryFromPpt,
    desc: HOME.entryFromPptDesc,
    tone: 'rose',
    icon: 'presentation',
    run: () => prefill(HOME.prefillFromPpt),
  },
  {
    key: 'history',
    title: HOME.entryHistory,
    desc: HOME.entryHistoryDesc,
    tone: 'sky',
    icon: 'clock',
    run: () => go('/teacher-v3/prep/history'),
  },
]

/* ── 图标（内联 SVG 线性 1.4px stroke；禁止 emoji）────────────────── */
const ICONS: Record<string, string[]> = {
  book: [
    'M10 4.6S8.2 3.2 5.2 3.2v11c3 0 4.8 1.4 4.8 1.4V4.6Z',
    'M10 4.6s1.8-1.4 4.8-1.4v11c-3 0-4.8 1.4-4.8 1.4V4.6Z',
  ],
  file: ['M5.8 3.2h5l3.4 3.4v10.2H5.8V3.2Z', 'M10.8 3.2v3.4h3.4', 'M8 11h4', 'M8 13.4h3'],
  presentation: [
    'M3.4 3.8h13.2a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3.4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z',
    'M8.6 6.9l3.6 2.1-3.6 2.1V6.9Z',
    'M10 13.8v2.8',
    'M7.2 16.6h5.6',
  ],
  clock: ['M10 3.4a6.6 6.6 0 1 1 0 13.2 6.6 6.6 0 0 1 0-13.2Z', 'M10 6.6V10l2.6 1.8'],
  chevronRight: ['M7.8 5 12.8 10l-5 5'],
  sparkle: [
    'M9.4 3.2c.5 3 1.2 3.7 4.2 4.2-3 .5-3.7 1.2-4.2 4.2-.5-3-1.2-3.7-4.2-4.2 3-.5 3.7-1.2 4.2-4.2Z',
    'M15.2 11.6c.3 1.6.6 1.9 2.2 2.2-1.6.3-1.9.6-2.2 2.2-.3-1.6-.6-1.9-2.2-2.2 1.6-.3 1.9-.6 2.2-2.2Z',
  ],
  send: ['M3.2 10.2 16.8 3.6 11.4 16.4 9.2 10.8 3.2 10.2Z'],
}

onBeforeUnmount(stopTimer)

onMounted(() => {
  void store.fetchSessions()
  // 真实班级目录：默认选中首项（无数据 → 胶囊不渲染；class_id 传 null，不阻塞输入与提交）
  void store.fetchClasses().then(() => {
    if (selectedClassId.value == null && store.classes.length) {
      selectedClassId.value = store.classes[0].class_id
    }
  })
})
</script>

<template>
  <div class="pc-home">
    <!-- 备课中心不渲染第二套侧栏（用户裁决 2026-10-04，宪法 §3）：
         平台外框已有全局导航，首页即备课流程起点。 -->

    <div class="pc-home__main">
      <!-- 创建中（真实已用时；阶段文案来自真实受理状态，无假进度） -->
      <div v-if="creating" class="pc-home__creating">
        <div class="pc-home__creating-card">
          <div class="pc-home__creating-spinner" aria-hidden="true"></div>
          <h2>{{ HOME.creatingTitle }}</h2>
          <p>{{ HOME.creatingHint }}</p>
          <p class="pc-home__creating-elapsed">
            {{ HOME.creatingElapsedPrefix }}{{ elapsedSeconds }}{{ HOME.creatingElapsedSuffix }}
          </p>
          <div class="pc-home__skeleton">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>

      <div v-else class="pc-home__inner">
        <!-- 1) 页标题行 -->
        <header class="pc-home__head">
          <div class="pc-home__head-text">
            <h1 class="pc-home__title">{{ HOME.pageTitle }}</h1>
            <p class="pc-home__subtitle">{{ HOME.pageSubtitle }}</p>
          </div>
          <div class="pc-home__head-actions">
            <button type="button" class="pc-home__ghost" @click="go('/teacher-v3/resources')">
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path
                  v-for="(d, i) in ICONS.book"
                  :key="i"
                  :d="d"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              {{ HOME.topResources }}
            </button>
            <button type="button" class="pc-home__ghost" @click="scrollToRecent">
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <path
                  v-for="(d, i) in ICONS.clock"
                  :key="i"
                  :d="d"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              {{ HOME.topRecent }}
            </button>
          </div>
        </header>

        <!-- 2) NL 胶囊主输入卡（radius 999 · 高 56） -->
        <div ref="nlBarEl" class="pc-home__nl" :class="{ 'is-error': !!store.createError }">
          <span class="pc-home__nl-icon" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                v-for="(d, i) in ICONS.sparkle"
                :key="i"
                :d="d"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <div v-if="store.classes.length" class="pc-home__nl-class">
            <button
              type="button"
              class="pc-home__class-btn"
              :aria-expanded="classMenuOpen"
              aria-haspopup="listbox"
              aria-label="选择班级"
              @click="classMenuOpen = !classMenuOpen"
            >
              <span class="pc-home__class-name">{{ selectedClassLabel }}</span>
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path d="M2.6 4.6 6 8l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
            <div v-if="classMenuOpen" class="pc-home__class-backdrop" @click="classMenuOpen = false"></div>
            <div v-if="classMenuOpen" class="pc-home__class-menu" role="listbox">
              <button
                v-for="c in store.classes"
                :key="c.class_id"
                type="button"
                role="option"
                :aria-selected="c.class_id === selectedClassId"
                class="pc-home__class-item"
                :class="{ 'is-active': c.class_id === selectedClassId }"
                @click="pickClass(c.class_id)"
              >
                {{ classLabelOf(c) }}
              </button>
            </div>
          </div>
          <textarea
            ref="nlInputEl"
            v-model="nlInput"
            class="pc-home__nl-input"
            rows="1"
            :placeholder="HOME.placeholder"
            aria-label="描述你今天想准备的课"
            @input="autoGrow"
            @keydown.enter.exact.prevent="submitNl"
          ></textarea>
          <button
            type="button"
            class="pc-home__nl-send"
            :disabled="!canSend"
            aria-label="发送给 AI 开始备课"
            @click="submitNl"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path :d="ICONS.send[0]" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
        <p v-if="store.createError" class="pc-home__softnote" role="status">{{ HOME.createFailedSoft }}</p>

        <!-- 3) 入口卡（等宽；全部真实可用，数量随可用目标变化） -->
        <div class="pc-home__entries" :style="{ gridTemplateColumns: `repeat(${ENTRIES.length}, minmax(0, 1fr))` }">
          <button
            v-for="e in ENTRIES"
            :key="e.key"
            type="button"
            class="pc-home__entry"
            @click="e.run()"
          >
            <span class="pc-home__entry-icon" :class="`is-${e.tone}`" aria-hidden="true">
              <svg viewBox="0 0 20 20">
                <path
                  v-for="(d, i) in ICONS[e.icon]"
                  :key="i"
                  :d="d"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.4"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
            </span>
            <span class="pc-home__entry-text">
              <span class="pc-home__entry-name">{{ e.title }}</span>
              <span class="pc-home__entry-desc">{{ e.desc }}</span>
            </span>
            <span class="pc-home__entry-chevron" aria-hidden="true">
              <svg viewBox="0 0 20 20">
                <path :d="ICONS.chevronRight[0]" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
          </button>
        </div>

        <!-- 4/5) 最近备课 -->
        <section ref="recentEl" class="pc-home__recent">
          <div class="pc-home__recent-head">
            <h2 class="pc-home__section-title">
              {{ HOME.recentTitle }}
              <span v-if="hasSessions" class="pc-home__count">{{ sessions.length }}</span>
            </h2>
            <button type="button" class="pc-home__link" @click="go('/teacher-v3/prep/history')">
              {{ HOME.viewAll }} ›
            </button>
          </div>

          <!-- 加载态（骨架） -->
          <div v-if="store.sessionsLoading && !hasSessions" class="pc-home__skeleton-list">
            <div v-for="i in 3" :key="i" class="pc-home__skeleton-row">
              <span class="pc-home__skeleton-line"></span>
              <span class="pc-home__skeleton-line is-short"></span>
            </div>
          </div>

          <!-- 失败 / 无权限态（含重试，不阻断其它操作） -->
          <div v-else-if="store.sessionsError" class="pc-home__state" role="status">
            <span class="pc-home__state-icon is-danger" aria-hidden="true">
              <svg viewBox="0 0 22 22">
                <path d="M11 6.6v6.2M11 15.8h.01" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
                <path d="M11 2.8a8.2 8.2 0 1 1 0 16.4 8.2 8.2 0 0 1 0-16.4Z" fill="none" stroke="currentColor" stroke-width="1.4" />
              </svg>
            </span>
            <p class="pc-home__state-title">{{ listErrorMessage }}</p>
            <button type="button" class="pc-home__state-cta" @click="reload">{{ HOME.retry }}</button>
          </div>

          <!-- 正常：列表 -->
          <ul v-else-if="hasSessions" class="pc-home__rows">
            <li v-for="s in visibleSessions" :key="s.session_id" class="pc-home__row-wrap">
              <div class="pc-home__row" @click="openSession(s)">
                <span class="pc-home__row-icon" :class="`is-${rowKindOf(s).tone}`" aria-hidden="true">
                  <svg viewBox="0 0 20 20">
                    <path
                      v-for="(d, i) in ICONS[rowKindOf(s).icon]"
                      :key="i"
                      :d="d"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.4"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </span>
                <span class="pc-home__row-main">
                  <span class="pc-home__row-title">{{ sessionTitle(s) }}</span>
                  <span class="pc-home__row-meta">{{ sessionMeta(s) }}</span>
                </span>
                <span class="pc-home__row-desc">
                  <span v-for="(line, i) in sessionDescLines(s)" :key="i" class="pc-home__row-desc-line">{{ line }}</span>
                </span>
                <span class="pc-home__row-side">
                  <span class="pc-home__badge" :class="`is-${statusTone(s.status)}`">
                    {{ statusLabel(s.status) }}
                  </span>
                  <span v-if="sessionModified(s)" class="pc-home__row-modified">
                    {{ HOME.modifiedPrefix }}{{ sessionModified(s) }}
                  </span>
                </span>
                <button type="button" class="pc-home__row-cta" @click.stop="openSession(s)">
                  {{ rowCta(s) }}
                </button>
                <button
                  type="button"
                  class="pc-home__row-more"
                  :aria-label="HOME.moreAria"
                  :aria-expanded="menuFor === s.session_id"
                  @click.stop="toggleMenu(s)"
                >
                  <span aria-hidden="true">···</span>
                </button>

                <!-- 行操作菜单（真实后端归档接口） -->
                <div v-if="menuFor === s.session_id" class="pc-home__menu-backdrop" @click.stop="menuFor = null"></div>
                <div v-if="menuFor === s.session_id" class="pc-home__menu" role="menu">
                  <button type="button" class="pc-home__menu-item" role="menuitem" @click.stop="askDelete(s)">
                    {{ HOME.menuDelete }}
                  </button>
                </div>
              </div>
            </li>
          </ul>

          <!-- 空态（结构化卡：图标 + 一句引导 + 唯一主按钮） -->
          <div v-else class="pc-home__state pc-home__state--empty">
            <span class="pc-home__state-icon is-primary" aria-hidden="true">
              <svg viewBox="0 0 22 22">
                <path d="M11 5.4S9 4 5.6 4v11.4c3.4 0 5.4 1.4 5.4 1.4V5.4Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                <path d="M11 5.4s2-1.4 5.4-1.4v11.4c-3.4 0-5.4 1.4-5.4 1.4V5.4Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
              </svg>
            </span>
            <p class="pc-home__state-title">{{ HOME.emptyTitle }}</p>
            <p class="pc-home__state-hint">{{ HOME.emptyHint }}</p>
            <button type="button" class="pc-home__state-cta is-primary" @click="focusInput">{{ HOME.emptyCta }}</button>
          </div>
        </section>
      </div>

      <!-- 右下「AI 备课助手」胶囊（真实行为：聚焦并滚动到本页 NL 主输入卡） -->
      <button
        type="button"
        class="pc-home__ai"
        :aria-label="HOME.aiAssistant"
        :title="HOME.aiAssistant"
        @click="focusInput"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path
            v-for="(d, i) in ICONS.sparkle"
            :key="i"
            :d="d"
            fill="none"
            stroke="currentColor"
            stroke-width="1.4"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        {{ HOME.aiAssistant }}
      </button>
    </div>

    <!-- 删除确认（真实归档前的二次确认；失败给中文软提示） -->
    <Teleport to="body">
      <div v-if="pendingDelete" class="pc-home__mask" @click.self="cancelDelete">
        <div class="pc-home__dialog" role="dialog" aria-modal="true" aria-label="删除备课">
          <p class="pc-home__dialog-title">{{ HOME.deleteTitle }}</p>
          <p class="pc-home__dialog-body">{{ HOME.deleteBody }}</p>
          <p v-if="deleteError" class="pc-home__dialog-error" role="status">{{ deleteError }}</p>
          <div class="pc-home__dialog-actions">
            <button type="button" class="pc-home__btn" @click="cancelDelete">{{ HOME.deleteCancel }}</button>
            <button type="button" class="pc-home__btn is-danger" :disabled="deleting" @click="confirmDelete">
              {{ HOME.deleteConfirm }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* ── 页面：左导航 + 主区（一屏可见，不产生纵向滚动条）────────────── */
.pc-home {
  display: flex;
  align-items: stretch;
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  min-height: var(--pc-viewport-h, 100vh);
  background: var(--pc-bg);
  font-family: var(--pc-font);
  line-height: var(--pc-lh);
  color: var(--pc-text);
}
.pc-home__main {
  position: relative;
  flex: 1 1 auto;
  min-width: 0;
}
.pc-home__inner {
  padding: 22px 24px 28px;
}

/* ── 1) 页标题行 ────────────────────────────────────────────────── */
.pc-home__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.pc-home__title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-home__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-home__head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}
.pc-home__ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-home__ghost:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-home__ghost svg {
  width: 16px;
  height: 16px;
}

/* ── 2) NL 主输入卡（胶囊 999 · 高 56）───────────────────────────── */
.pc-home__nl {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 56px;
  padding: 8px 8px 8px 18px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  transition: border-color 0.15s ease;
}
.pc-home__nl:focus-within {
  border-color: var(--pc-primary);
}
.pc-home__nl-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  color: var(--pc-primary);
}
.pc-home__nl-icon svg {
  width: 20px;
  height: 20px;
}
/* 班级选择胶囊（紧凑：高 28 / 13px / 胶囊圆角；不改变输入卡 56 高度） */
.pc-home__nl-class {
  position: relative;
  flex: none;
}
.pc-home__class-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-bg);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.pc-home__class-btn:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-home__class-btn svg {
  width: 12px;
  height: 12px;
}
.pc-home__class-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pc-home__class-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.pc-home__class-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 41;
  min-width: 132px;
  padding: 6px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  box-shadow: var(--pc-shadow-hover);
}
.pc-home__class-item {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-home__class-item:hover {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-home__class-item.is-active {
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-home__nl-input {
  flex: 1 1 auto;
  min-width: 0;
  height: 40px;
  padding: 9px 0;
  border: none;
  outline: none;
  resize: none;
  overflow: hidden;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  color: var(--pc-text);
  background: transparent;
}
.pc-home__nl-input::placeholder {
  color: var(--pc-text-faint);
}
.pc-home__nl-send {
  flex: none;
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--pc-radius-full);
  /* golden2 实心亮蓝发送钮（#2563EB 系）：禁用态不再降透明度成「近似灰钮」观感 */
  background: var(--pc-primary);
  color: #fff;
  cursor: pointer;
  transition: background 0.15s ease;
}
.pc-home__nl-send:hover:not(:disabled) {
  background: var(--pc-primary-hover);
}
.pc-home__nl-send:disabled {
  cursor: not-allowed;
}
.pc-home__nl-send svg {
  width: 18px;
  height: 18px;
}
.pc-home__softnote {
  margin: 10px 2px 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}

/* ── 3) 入口卡（4 列等宽 · 高 ≥96 · 描述完整不截断）────────────────── */
.pc-home__entries {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-top: 20px;
}
.pc-home__entry {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 96px;
  padding: 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.pc-home__entry:hover {
  border-color: var(--pc-primary);
}
.pc-home__entry-icon {
  flex: none;
  width: 40px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
}
.pc-home__entry-icon svg {
  width: 20px;
  height: 20px;
}
.pc-home__entry-icon.is-indigo {
  background: var(--pc-tone-indigo-bg);
  color: var(--pc-tone-indigo-fg);
}
.pc-home__entry-icon.is-sky {
  background: var(--pc-tone-sky-bg);
  color: var(--pc-tone-sky-fg);
}
.pc-home__entry-icon.is-emerald {
  background: var(--pc-tone-emerald-bg);
  color: var(--pc-tone-emerald-fg);
}
.pc-home__entry-icon.is-rose {
  background: var(--pc-tone-rose-bg);
  color: var(--pc-tone-rose-fg);
}
.pc-home__entry-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1 1 auto;
}
.pc-home__entry-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 描述 = golden「一句描述」完整句：允许折行，禁止省略号截断 */
.pc-home__entry-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--pc-text-muted);
  white-space: normal;
  word-break: break-word;
}
.pc-home__entry-chevron {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--pc-text-faint);
}
.pc-home__entry-chevron svg {
  width: 16px;
  height: 16px;
}

/* ── 4/5) 最近备课 ──────────────────────────────────────────────── */
.pc-home__recent {
  margin-top: 24px;
}
.pc-home__recent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}
.pc-home__section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-home__count {
  min-width: 20px;
  height: 20px;
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
.pc-home__link {
  border: none;
  background: transparent;
  flex: none;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-primary);
  cursor: pointer;
  white-space: nowrap;
}
.pc-home__link:hover {
  color: var(--pc-primary-hover);
}

.pc-home__rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-home__row-wrap {
  margin: 0;
}
/* 列表行：高 68 · 1px 描边 12 圆角 */
.pc-home__row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 68px;
  padding: 0 12px 0 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.pc-home__row:hover {
  border-color: var(--pc-primary);
}
.pc-home__row-icon {
  flex: none;
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
}
.pc-home__row-icon svg {
  width: 20px;
  height: 20px;
}
/* 行类型图标按真实来源着色（golden2 多彩类型图标；相同真实形态同色） */
.pc-home__row-icon.is-indigo {
  background: var(--pc-tone-indigo-bg);
  color: var(--pc-tone-indigo-fg);
}
.pc-home__row-icon.is-emerald {
  background: var(--pc-tone-emerald-bg);
  color: var(--pc-tone-emerald-fg);
}
.pc-home__row-icon.is-amber {
  background: var(--pc-tone-amber-bg);
  color: var(--pc-tone-amber-fg);
}
.pc-home__row-icon.is-muted {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-home__row-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1 1 auto;
  min-width: 0;
}
.pc-home__row-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-home__row-meta {
  font-size: 13px;
  color: var(--pc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 行中部「两句灰色描述」：固定 2 行灰字（golden2 列表行七要素第 3 要素） */
.pc-home__row-desc {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
}
.pc-home__row-desc-line {
  font-size: 13px;
  color: var(--pc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-home__row-side {
  flex: none;
  width: 168px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.pc-home__badge {
  padding: 2px 10px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  white-space: nowrap;
}
.pc-home__badge.is-done {
  background: var(--pc-success-soft);
  color: var(--pc-success);
}
.pc-home__badge.is-draft {
  background: var(--pc-warning-soft);
  color: var(--pc-warning);
}
.pc-home__badge.is-danger {
  background: var(--pc-danger-soft);
  color: var(--pc-danger);
}
.pc-home__badge.is-muted {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-home__row-modified {
  font-size: 12px;
  color: var(--pc-text-faint);
  white-space: nowrap;
}
.pc-home__row-cta {
  flex: none;
  height: 32px;
  padding: 0 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-home__row-cta:hover {
  border-color: var(--pc-primary);
  background: var(--pc-primary-soft);
}
.pc-home__row-more {
  flex: none;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: transparent;
  color: var(--pc-text-muted);
  font-size: 16px;
  line-height: 1;
  font-family: inherit;
  cursor: pointer;
}
.pc-home__row-more:hover {
  background: var(--pc-bg);
  color: var(--pc-text-2);
}
.pc-home__menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.pc-home__menu {
  position: absolute;
  top: 58px;
  right: 8px;
  z-index: 41;
  min-width: 132px;
  padding: 6px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  box-shadow: var(--pc-shadow-hover);
}
.pc-home__menu-item {
  display: block;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-danger);
  cursor: pointer;
}
.pc-home__menu-item:hover {
  background: var(--pc-danger-soft);
}

/* 加载骨架 */
.pc-home__skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-home__skeleton-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
}
.pc-home__skeleton-line {
  height: 12px;
  border-radius: 6px;
  background: var(--pc-bg);
  animation: pc-pulse 1.4s ease-in-out infinite;
}
.pc-home__skeleton-line.is-short {
  width: 40%;
}
@keyframes pc-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.45;
  }
}

/* 结构化状态卡（失败 / 无权限 / 空态） */
.pc-home__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 40px 32px;
  text-align: center;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-home__state-icon {
  width: 44px;
  height: 44px;
  margin-bottom: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-home__state-icon.is-primary {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-home__state-icon.is-danger {
  background: var(--pc-danger-soft);
  color: var(--pc-danger);
}
.pc-home__state-icon svg {
  width: 22px;
  height: 22px;
}
.pc-home__state-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-home__state-hint {
  margin: 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-home__state-cta {
  margin-top: 14px;
  height: 34px;
  padding: 0 18px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text);
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.pc-home__state-cta:hover {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-home__state-cta.is-primary {
  border-color: transparent;
  background: var(--pc-primary);
  color: #fff;
}
.pc-home__state-cta.is-primary:hover {
  background: var(--pc-primary-hover);
  color: #fff;
}

/* ── 右下「AI 备课助手」胶囊 ─────────────────────────────────────── */
.pc-home__ai {
  position: fixed;
  /* 右下让位平台全局悬浮 AI 助教 Dock（R1 G1；--pc-dock-clear 嵌入时由外框覆写） */
  right: calc(24px + var(--pc-dock-clear, 0px));
  bottom: 24px;
  z-index: 900;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 40px;
  padding: 0 16px;
  border: none;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  box-shadow: 0 8px 22px rgba(37, 99, 235, 0.28);
  transition: background 0.15s ease;
}
.pc-home__ai:hover {
  background: var(--pc-primary-hover);
}
.pc-home__ai svg {
  width: 16px;
  height: 16px;
}

/* ── 创建中 ─────────────────────────────────────────────────────── */
.pc-home__creating {
  padding: 72px 24px;
}
.pc-home__creating-card {
  max-width: 560px;
  margin: 0 auto;
  padding: 36px 40px;
  text-align: center;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-panel);
}
.pc-home__creating-card h2 {
  margin: 14px 0 8px;
  font-size: 18px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-home__creating-card p {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-home__creating-elapsed {
  font-size: 12px;
  color: var(--pc-text-faint);
  font-variant-numeric: tabular-nums;
}
.pc-home__creating-spinner {
  width: 34px;
  height: 34px;
  margin: 0 auto;
  border-radius: 50%;
  border: 3px solid var(--pc-primary-soft);
  border-top-color: var(--pc-primary);
  animation: pc-spin 0.9s linear infinite;
}
@keyframes pc-spin {
  to {
    transform: rotate(360deg);
  }
}
.pc-home__creating .pc-home__skeleton {
  display: flex;
  gap: 10px;
  margin-top: 22px;
}
.pc-home__creating .pc-home__skeleton span {
  height: 10px;
  flex: 1;
  border-radius: 5px;
  background: var(--pc-bg);
  animation: pc-pulse 1.4s ease-in-out infinite;
}

/* ── 删除确认对话框 ─────────────────────────────────────────────── */
.pc-home__mask {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(15, 23, 42, 0.32);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--pc-font);
}
.pc-home__dialog {
  width: 380px;
  max-width: 92vw;
  padding: 22px 24px;
  background: var(--pc-panel);
  border-radius: var(--pc-radius-panel);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.22);
}
.pc-home__dialog-title {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-home__dialog-body {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text-muted);
}
.pc-home__dialog-error {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--pc-danger);
}
.pc-home__dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
.pc-home__btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-home__btn:hover:not(:disabled) {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-home__btn.is-danger {
  border-color: transparent;
  background: var(--pc-danger);
  color: #fff;
}
.pc-home__btn.is-danger:hover:not(:disabled) {
  background: #b91c1c;
  color: #fff;
}
.pc-home__btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
