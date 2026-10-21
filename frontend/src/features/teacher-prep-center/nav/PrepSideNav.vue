<script setup lang="ts">
/**
 * 备课中心左导航（VISUAL-SPEC-V3 §2 + R4b IA 收尾 / golden1 左栏）。
 *
 * 三套形态（宽 208，白底右发丝线）：
 * - `home`：首页态（G-golden2，R1fix-S1 裁决）——我的备课(高亮)/教材与资料/学情分析/
 *   题库与组卷/作业与批改 ｜ 资源中心/课堂互动/教学管理/学校资源（两组，发丝分隔）
 *   + 底部「收起侧栏」（真实折叠，折叠后 56 只显示图标）；教学管理诚实禁用「即将开放」。
 * - `chat`：S2 对话三栏主面左栏（golden1）——备课中心头 + 折叠；同一组备课导航 +
 *   「最近备课」4 条（真实会话列表，选中条带蓝点+chevron，副行=版本·班级·时长）+
 *   底部「教材与资料」胶囊（真实跳转当前会话资料页）。
 * - `session`：会话态（golden3/golden4）——返回备课中心 +「当前备课」tab 组 +
 *   「教学工具」组 + 底部「当前备课进度」。**勾选状态完全由 props 的真实状态决定**，
 *   组件内不写死任何进度。
 *
 * IA 收尾（R1 遗留 · R4b 裁决）：与平台侧栏重复的 题库与组卷 / 学情分析 / 资源中心
 * （及作业与批改 / 课堂互动）不再出现在备课左导航 —— 平台侧栏常驻可达（G1 外框），
 * 能力不丢（E4），备课导航只保留备课专属项（golden1 清单）。
 * R4afix2 主控裁决补充：会话态「教学工具」组按 Golden G-golden4 补回 4 项
 * （教材与资料/题库与组卷/学情分析/资源中心，golden4 左栏属内容区组件）。
 * R1fix-S1 裁决例外（仅 home 态）：G-golden2 左栏 = 内容区组件，Golden 明列条目按
 * Golden 补回（含 资源中心/教学管理/学校资源），详见 HOME_ITEMS_*。
 * 「教研中心」在教师端无真实路由/页面：**诚实禁用**（G12 禁止亮而无效，也禁止假入口）。
 * 跳转一律 router.push（禁止 <a href> 硬跳）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAV, formatSessionMeta, textbookVersionSegment } from '@/features/teacher-prep-center/copy/prepCopy'
import { MINUTES_PER_LESSON_HOUR, usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import type { PrepSessionSummary } from '@/features/teacher-prep-center/api/prepCenterApi'

/** 会话真实进度（由父组件从后端快照推导后传入；本组件不猜测）。 */
export interface PrepProgress {
  textbook: boolean
  design: boolean
  ppt: boolean
  plan: boolean
}

const props = withDefaults(
  defineProps<{
    mode?: 'home' | 'session' | 'chat'
    sessionId?: string
    progress?: PrepProgress | null
  }>(),
  { mode: 'home', sessionId: '', progress: null },
)

const route = useRoute()
const router = useRouter()
const store = usePrepSessionStore()

const collapsed = ref(false)
const groupOpen = ref({ current: true, tools: true })

/* ── 图标（内联 SVG 线性 1.4px stroke；禁止 emoji 充当功能图标）────── */
const ICONS: Record<string, string[]> = {
  board: [
    'M3.4 3.6h13.2a1 1 0 0 1 1 1v8.2a1 1 0 0 1-1 1H3.4a1 1 0 0 1-1-1V4.6a1 1 0 0 1 1-1Z',
    'M10 13.8v2.8',
    'M7.2 16.6h5.6',
    'M5.6 6.6h5.4',
    'M5.6 9.4h8.8',
  ],
  plus: ['M10 4.2v11.6', 'M4.2 10h11.6'],
  folder: [
    'M2.6 5.4A1.4 1.4 0 0 1 4 4h3.1l1.6 1.7h7.3A1.4 1.4 0 0 1 17.4 7.1v7.5A1.4 1.4 0 0 1 16 16H4a1.4 1.4 0 0 1-1.4-1.4V5.4Z',
  ],
  book: [
    'M10 4.6S8.2 3.2 5.2 3.2v11c3 0 4.8 1.4 4.8 1.4V4.6Z',
    'M10 4.6s1.8-1.4 4.8-1.4v11c-3 0-4.8 1.4-4.8 1.4V4.6Z',
  ],
  school: [
    'M3.4 8.6 10 4.2l6.6 4.4',
    'M5.2 9.6v6.2h9.6V9.6',
    'M8.2 15.8v-3.2h3.6v3.2',
  ],
  globe: [
    'M10 3.2a6.8 6.8 0 1 1 0 13.6 6.8 6.8 0 0 1 0-13.6Z',
    'M3.4 10h13.2',
    'M10 3.2c1.9 1.9 2.9 4.2 2.9 6.8s-1 4.9-2.9 6.8c-1.9-1.9-2.9-4.2-2.9-6.8s1-4.9 2.9-6.8Z',
  ],
  users: [
    'M7.2 9.4a2.6 2.6 0 1 0 0-5.2 2.6 2.6 0 0 0 0 5.2Z',
    'M2.6 16.2c0-2.6 2.1-4.2 4.6-4.2s4.6 1.6 4.6 4.2',
    'M13.4 5.2a2.4 2.4 0 0 1 0 4.6',
    'M14.2 12.2c1.8.4 3.2 1.8 3.2 4',
  ],
  chart: ['M3.6 16.4h12.8', 'M6.2 16.4V9.6', 'M10 16.4V5.8', 'M13.8 16.4v-4.6'],
  layers: [
    'M10 3.2 16.8 7 10 10.8 3.2 7 10 3.2Z',
    'M3.2 10.4 10 14.2l6.8-3.8',
    'M3.2 13.4 10 17.2l6.8-3.8',
  ],
  clipboard: [
    'M7.6 3.4h4.8v2.2H7.6z',
    'M6.4 4.5H5a1 1 0 0 0-1 1v10.1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V5.5a1 1 0 0 0-1-1h-1.4',
    'M7.4 11.6l1.8 1.8 3.4-3.6',
  ],
  presentation: [
    'M3.4 3.8h13.2a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H3.4a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z',
    'M8.6 6.9l3.6 2.1-3.6 2.1V6.9Z',
    'M10 13.8v2.8',
    'M7.2 16.6h5.6',
  ],
  file: ['M5.8 3.2h5l3.4 3.4v10.2H5.8V3.2Z', 'M10.8 3.2v3.4h3.4', 'M8 11h4', 'M8 13.4h3'],
  clock: ['M10 3.4a6.6 6.6 0 1 1 0 13.2 6.6 6.6 0 0 1 0-13.2Z', 'M10 6.6V10l2.6 1.8'],
  chevronLeft: ['M12.2 4.8 7 10l5.2 5.2'],
  chevronDown: ['M5 7.6 10 12.4l5-4.8'],
  chevronRight: ['M7.6 4.8 12.8 10l-5.2 5.2'],
}

interface NavItem {
  key: string
  label: string
  icon: string
  to: string
  /** 路径精确匹配（避免 /prep 误吞 /prep/w/... 子路径）。 */
  exact?: boolean
  /** 会话 tab：按路由 tab 段判激活。 */
  tabKey?: string
  /** 真实能力缺位时诚实禁用（禁止亮而无效 / 假入口）。 */
  disabled?: boolean
  /** 禁用原因（hover 可见）。 */
  disabledReason?: string
}

const ROUTES = {
  home: '/teacher-v3/prep',
  history: '/teacher-v3/prep/history',
  resources: '/teacher-v3/resources',
  resourcesSchool: '/teacher-v3/resources?nav=school',
  resourcesPublic: '/teacher-v3/resources?nav=public',
  insights: '/teacher-v3/insights',
  bank: '/teacher-v3/bank',
  assign: '/teacher-v3/assign',
  classroom: '/teacher-v3/classroom',
} as const

/** 首页态左导航（G-golden2 左栏 = 内容区组件，两组 9 项 + 底部收起侧栏）。
 *  R1fix-S1 裁决：Golden 明列条目按 Golden 补回（R4b 平台去重裁决对首页态让位），
 *  高亮落在「我的备课」。点击真实跳转：我的备课→/teacher-v3/prep（本页）、
 *  教材与资料/资源中心→/teacher-v3/resources、学情分析→/teacher-v3/insights、
 *  题库与组卷→/teacher-v3/bank、作业与批改→/teacher-v3/assign、课堂互动→/teacher-v3/classroom、
 *  学校资源→/teacher-v3/resources?nav=school（ResourcesView 真实分类深链）；
 *  教学管理无真实页面 → 诚实禁用 + tooltip「即将开放」（G12 禁亮而无效，也禁假页面）。 */
const HOME_ITEMS_PRIMARY: NavItem[] = [
  { key: 'myPrep', label: NAV.myPrep, icon: 'folder', to: ROUTES.home, exact: true },
  { key: 'textbook', label: NAV.textbook, icon: 'book', to: ROUTES.resources },
  { key: 'analysis', label: NAV.analysis, icon: 'chart', to: ROUTES.insights },
  { key: 'bank', label: NAV.bank, icon: 'clipboard', to: ROUTES.bank },
  { key: 'assign', label: NAV.assign, icon: 'file', to: ROUTES.assign },
]

const HOME_ITEMS_SECONDARY: NavItem[] = [
  { key: 'resources', label: NAV.resources, icon: 'layers', to: ROUTES.resources },
  { key: 'classroom', label: NAV.classroom, icon: 'users', to: ROUTES.classroom },
  {
    key: 'teachingManage',
    label: NAV.teachingManage,
    icon: 'board',
    to: '',
    disabled: true,
    disabledReason: NAV.comingSoon,
  },
  { key: 'school', label: NAV.schoolRes, icon: 'school', to: ROUTES.resourcesSchool },
]

const HOME_NAV_GROUPS: NavItem[][] = [HOME_ITEMS_PRIMARY, HOME_ITEMS_SECONDARY]

/** 备课专属导航（golden1 左栏；home / chat 两态共用，IA 收尾后唯一清单）。 */
const PREP_ITEMS: NavItem[] = [
  { key: 'newPrep', label: NAV.newPrep, icon: 'plus', to: ROUTES.home, exact: true },
  { key: 'myPrep', label: NAV.myPrep, icon: 'folder', to: ROUTES.history },
  { key: 'school', label: NAV.schoolResources, icon: 'school', to: ROUTES.resourcesSchool },
  { key: 'public', label: NAV.publicResources, icon: 'globe', to: ROUTES.resourcesPublic },
  {
    key: 'research',
    label: NAV.researchCenter,
    icon: 'users',
    to: '',
    disabled: true,
    disabledReason: NAV.researchCenterUnavailable,
  },
]

/** 会话态「教学工具」组：Golden G-golden4 左栏明列 4 项（R4afix2 主控裁决）——
 *  教材与资料 / 题库与组卷 / 学情分析 / 资源中心。
 *  R4b 曾按「与平台侧栏重复」删掉后 3 项，裁决驳回：Golden G-golden4 左栏属内容区组件，
 *  按 Golden 补回（同 R1fix-S1 口径）。跳转同 S1 返工：有真实页跳真实页
 *  （/teacher-v3/bank、/teacher-v3/insights、/teacher-v3/resources 均为真实路由），
 *  无真实页则诚实禁用 + tooltip（G12 禁亮而无效、禁假入口）。 */
const TOOL_ITEMS: NavItem[] = [
  { key: 'textbook', label: NAV.textbook, icon: 'book', to: ROUTES.resources },
  { key: 'bank', label: NAV.bank, icon: 'clipboard', to: ROUTES.bank },
  { key: 'analysis', label: NAV.analysis, icon: 'chart', to: ROUTES.insights },
  { key: 'resources', label: NAV.resources, icon: 'layers', to: ROUTES.resources },
]

/** 会话态「当前备课」组：会话内 tab（历史 = overview）。 */
const SESSION_TABS = [
  { key: 'classroom', label: NAV.tabClassroom, icon: 'board' },
  { key: 'ppt', label: NAV.tabPpt, icon: 'presentation' },
  { key: 'plan', label: NAV.tabPlan, icon: 'file' },
  { key: 'materials', label: NAV.tabMaterials, icon: 'folder' },
  { key: 'overview', label: NAV.tabHistory, icon: 'clock' },
] as const

const sessionTabItems = computed<NavItem[]>(() =>
  SESSION_TABS.map((t) => ({
    key: t.key,
    label: t.label,
    icon: t.icon,
    tabKey: t.key,
    to: `/teacher-v3/prep/w/${props.sessionId}/${t.key}`,
  })),
)

/** 当前会话 tab（缺省 classroom；与工作台路由同口径）。 */
const activeTabKey = computed(() => {
  const t = String(route.params.tab || '')
  return (SESSION_TABS as readonly { key: string }[]).some((x) => x.key === t) ? t : 'classroom'
})

function isActive(item: NavItem): boolean {
  if (item.disabled) return false
  if (item.tabKey) return item.tabKey === activeTabKey.value
  const path = route.path.replace(/\/+$/, '')
  const to = item.to.split('?')[0].replace(/\/+$/, '')
  if (item.exact) return path === to
  return path === to || path.startsWith(`${to}/`)
}

function go(item: NavItem | string) {
  const target = typeof item === 'string' ? { to: item, disabled: false } : item
  if (target.disabled || !target.to) return
  if (route.path === target.to) return
  void router.push(target.to)
}

/* ── 「最近备课」4 条（golden1 左栏；真实会话列表，副行 = 版本·班级·时长）───── */
const recentSessions = computed<PrepSessionSummary[]>(() => store.sessions.slice(0, 4))

function recentTitle(s: PrepSessionSummary): string {
  return (
    (s.title ?? '').trim() ||
    s.intent?.knowledge_points?.[0] ||
    s.intent?.lesson_scope ||
    '备课记录'
  )
}

/** 副行元信息（与首页列表同一口径：formatSessionMeta，章节号不进元信息）。 */
function recentMeta(s: PrepSessionSummary): string {
  const hours = store.normalizeLessonHours(s.intent?.lesson_hours)
  return formatSessionMeta({
    textbook: textbookVersionSegment(store.sourceTitleOf(s.intent?.source_id)),
    className: store.resolveClassName(s),
    duration: hours != null ? `${hours * MINUTES_PER_LESSON_HOUR}分钟` : null,
  })
}

function isRecentActive(s: PrepSessionSummary): boolean {
  return !!props.sessionId && s.session_id === props.sessionId
}

function openRecent(s: PrepSessionSummary) {
  if (isRecentActive(s)) return
  void router.push(`/teacher-v3/prep/w/${s.session_id}/chat`)
}

onMounted(() => {
  if (props.mode === 'chat') {
    if (!store.sessions.length && !store.sessionsLoading) void store.fetchSessions()
    void store.fetchClasses()
  }
})

/* ── 真实进度（仅由 props 决定；缺省全部未完成）────────────────────── */
const progress = computed<PrepProgress>(
  () => props.progress ?? { textbook: false, design: false, ppt: false, plan: false },
)
const checklist = computed(() => [
  { key: 'textbook', label: NAV.progressTextbook, done: progress.value.textbook },
  { key: 'design', label: NAV.progressDesign, done: progress.value.design },
  { key: 'ppt', label: NAV.progressPpt, done: progress.value.ppt },
  { key: 'plan', label: NAV.progressPlan, done: progress.value.plan },
])
const doneCount = computed(() => checklist.value.filter((c) => c.done).length)
const progressPct = computed(() => Math.round((doneCount.value / checklist.value.length) * 100))

/** 底部「教材与资料」胶囊（golden1 左栏底部）：真实跳当前会话资料页。 */
function openSessionMaterials() {
  if (!props.sessionId) return
  void router.push(`/teacher-v3/prep/w/${props.sessionId}/materials`)
}
</script>

<template>
  <nav
    class="pc-sidenav"
    :class="[`pc-sidenav--${mode}`, { 'is-collapsed': mode !== 'session' && collapsed }]"
    aria-label="备课中心导航"
  >
    <!-- 首页态（G-golden2 两组条目：我的备课(高亮)…作业与批改 ｜ 资源中心…学校资源） -->
    <template v-if="mode === 'home'">
      <ul
        v-for="(group, gi) in HOME_NAV_GROUPS"
        :key="gi"
        class="pc-sidenav__list"
        :class="{ 'pc-sidenav__list--divided': gi > 0 }"
      >
        <li v-for="item in group" :key="item.key">
          <button
            type="button"
            class="pc-sidenav__item"
            :class="{ 'is-active': isActive(item), 'is-disabled': item.disabled }"
            :title="item.disabled ? item.disabledReason : collapsed ? item.label : ''"
            :aria-current="isActive(item) ? 'page' : undefined"
            :disabled="item.disabled"
            @click="go(item)"
          >
            <span class="pc-sidenav__icon" aria-hidden="true">
              <svg viewBox="0 0 20 20">
                <path
                  v-for="(d, i) in ICONS[item.icon]"
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
            <span v-if="!collapsed" class="pc-sidenav__label">{{ item.label }}</span>
          </button>
        </li>
      </ul>

      <div class="pc-sidenav__foot">
        <button
          type="button"
          class="pc-sidenav__item pc-sidenav__collapse"
          :title="collapsed ? NAV.expand : NAV.collapse"
          @click="collapsed = !collapsed"
        >
          <span class="pc-sidenav__icon" :class="{ 'is-flip': collapsed }" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                :d="ICONS.chevronLeft[0]"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span v-if="!collapsed" class="pc-sidenav__label">{{ NAV.collapse }}</span>
        </button>
      </div>
    </template>

    <!-- S2 对话三栏左栏（golden1）：备课中心头 + 折叠 / 备课导航 / 最近备课 4 条 / 教材与资料胶囊 -->
    <template v-else-if="mode === 'chat'">
      <div class="pc-sidenav__chathead">
        <span v-if="!collapsed" class="pc-sidenav__chathead-title">{{ NAV.home }}</span>
        <button
          type="button"
          class="pc-sidenav__chathead-fold"
          :title="collapsed ? NAV.expand : NAV.collapse"
          @click="collapsed = !collapsed"
        >
          <span class="pc-sidenav__icon" :class="{ 'is-flip': collapsed }" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                :d="ICONS.chevronLeft[0]"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
        </button>
      </div>

      <ul class="pc-sidenav__list">
        <li v-for="item in PREP_ITEMS" :key="item.key">
          <button
            type="button"
            class="pc-sidenav__item"
            :class="{ 'is-active': isActive(item), 'is-disabled': item.disabled }"
            :title="item.disabled ? item.disabledReason : collapsed ? item.label : ''"
            :aria-current="isActive(item) ? 'page' : undefined"
            :disabled="item.disabled"
            @click="go(item)"
          >
            <span class="pc-sidenav__icon" aria-hidden="true">
              <svg viewBox="0 0 20 20">
                <path
                  v-for="(d, i) in ICONS[item.icon]"
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
            <span v-if="!collapsed" class="pc-sidenav__label">{{ item.label }}</span>
          </button>
        </li>
      </ul>

      <!-- 最近备课（真实会话列表；选中条蓝点 + chevron，副行 = 版本·班级·时长） -->
      <div v-if="!collapsed" class="pc-sidenav__recent">
        <div class="pc-sidenav__group-title">{{ NAV.recent }}</div>
        <ul v-if="recentSessions.length" class="pc-sidenav__list">
          <li v-for="s in recentSessions" :key="s.session_id">
            <button
              type="button"
              class="pc-sidenav__recent-item"
              :class="{ 'is-active': isRecentActive(s) }"
              @click="openRecent(s)"
            >
              <span class="pc-sidenav__recent-dot" aria-hidden="true"></span>
              <span class="pc-sidenav__recent-main">
                <span class="pc-sidenav__recent-title">{{ recentTitle(s) }}</span>
                <span class="pc-sidenav__recent-meta">{{ recentMeta(s) }}</span>
              </span>
              <svg v-if="isRecentActive(s)" class="pc-sidenav__recent-chevron" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
              </svg>
            </button>
          </li>
        </ul>
        <p v-else class="pc-sidenav__recent-empty">{{ NAV.recentEmpty }}</p>
      </div>

      <!-- 底部「教材与资料」胶囊（golden1 左栏底部；真实跳当前会话资料页） -->
      <div class="pc-sidenav__foot">
        <button
          type="button"
          class="pc-sidenav__capsule"
          :disabled="!sessionId"
          :title="sessionId ? '打开本次备课的教材与资料' : '进入备课会话后可用'"
          @click="openSessionMaterials"
        >
          <span class="pc-sidenav__icon" aria-hidden="true">
            <svg viewBox="0 0 20 20">
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
          </span>
          <span v-if="!collapsed" class="pc-sidenav__label">{{ NAV.textbook }}</span>
        </button>
      </div>
    </template>

    <!-- 会话态（golden3 / golden4） -->
    <template v-else>
      <div class="pc-sidenav__top">
        <button
          type="button"
          class="pc-sidenav__item"
          :class="{ 'is-active': isActive({ key: 'back', label: NAV.home, icon: 'chevronLeft', to: ROUTES.home, exact: true }) }"
          @click="go(ROUTES.home)"
        >
          <span class="pc-sidenav__icon" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                :d="ICONS.chevronLeft[0]"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          <span class="pc-sidenav__label">{{ NAV.home }}</span>
        </button>
      </div>

      <div class="pc-sidenav__group">
        <button type="button" class="pc-sidenav__group-title" :aria-expanded="groupOpen.current" @click="groupOpen.current = !groupOpen.current">
          <span class="pc-sidenav__group-chevron" :class="{ 'is-open': groupOpen.current }" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                :d="ICONS.chevronDown[0]"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          {{ NAV.groupCurrent }}
        </button>
        <ul v-show="groupOpen.current" class="pc-sidenav__list">
          <li v-for="item in sessionTabItems" :key="item.key">
            <button
              type="button"
              class="pc-sidenav__item"
              :class="{ 'is-active': isActive(item) }"
              :aria-current="isActive(item) ? 'page' : undefined"
              @click="go(item)"
            >
              <span class="pc-sidenav__icon" aria-hidden="true">
                <svg viewBox="0 0 20 20">
                  <path
                    v-for="(d, i) in ICONS[item.icon]"
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
              <span class="pc-sidenav__label">{{ item.label }}</span>
            </button>
          </li>
        </ul>
      </div>

      <div class="pc-sidenav__group">
        <button type="button" class="pc-sidenav__group-title" :aria-expanded="groupOpen.tools" @click="groupOpen.tools = !groupOpen.tools">
          <span class="pc-sidenav__group-chevron" :class="{ 'is-open': groupOpen.tools }" aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path
                :d="ICONS.chevronDown[0]"
                fill="none"
                stroke="currentColor"
                stroke-width="1.4"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </span>
          {{ NAV.groupTools }}
        </button>
        <ul v-show="groupOpen.tools" class="pc-sidenav__list">
          <li v-for="item in TOOL_ITEMS" :key="item.key">
            <button
              type="button"
              class="pc-sidenav__item"
              :class="{ 'is-active': isActive(item) }"
              :aria-current="isActive(item) ? 'page' : undefined"
              @click="go(item)"
            >
              <span class="pc-sidenav__icon" aria-hidden="true">
                <svg viewBox="0 0 20 20">
                  <path
                    v-for="(d, i) in ICONS[item.icon]"
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
              <span class="pc-sidenav__label">{{ item.label }}</span>
            </button>
          </li>
        </ul>
      </div>

      <!-- 当前备课进度：勾选状态 100% 来自 props（父组件从后端快照推导） -->
      <div class="pc-sidenav__progress">
        <div class="pc-sidenav__progress-head">
          <span class="pc-sidenav__progress-title">{{ NAV.progressTitle }}</span>
          <span class="pc-sidenav__progress-pct">{{ progressPct }}%</span>
        </div>
        <div
          class="pc-sidenav__progress-track"
          role="progressbar"
          :aria-valuenow="progressPct"
          aria-valuemin="0"
          aria-valuemax="100"
        >
          <span class="pc-sidenav__progress-bar" :style="{ width: `${progressPct}%` }"></span>
        </div>
        <ul class="pc-sidenav__checklist">
          <li v-for="c in checklist" :key="c.key" class="pc-sidenav__check" :class="{ 'is-done': c.done }">
            <span class="pc-sidenav__check-icon" aria-hidden="true">
              <svg v-if="c.done" viewBox="0 0 16 16">
                <path d="M3.6 8.4 6.6 11.4 12.4 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <svg v-else viewBox="0 0 16 16">
                <path d="M8 3.6a4.4 4.4 0 1 1 0 8.8 4.4 4.4 0 0 1 0-8.8Z" fill="none" stroke="currentColor" stroke-width="1.4" />
              </svg>
            </span>
            <span class="pc-sidenav__check-label">{{ c.label }}</span>
          </li>
        </ul>
      </div>
    </template>
  </nav>
</template>

<style scoped>
.pc-sidenav {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  width: var(--pc-sidenav-w);
  background: var(--pc-panel);
  border-right: 1px solid var(--pc-line);
  font-family: var(--pc-font);
  user-select: none;
  transition: width 0.16s ease;
  /* 内容超出视口时导航内部滚动（不越界、不裁切）：flex 子项必须 min-height: 0，
     否则 overflow-y 不生效。 */
  min-height: 0;
  overflow-y: auto;
}
/* 首页态：随内容行内排布（不遮挡、不悬浮） */
.pc-sidenav--home {
  flex: none;
  align-self: stretch;
}
/* 会话态 / 对话态：固定于顶栏之下（不改动工作台结构），工作区主体由 shell 让出宽度。
 * R1 外框嵌入（G1）：fixed 锚点必须落在**外框内容区**（--pc-frame-left/top 由外框按
 * 平台侧栏宽/平台顶栏高覆写），否则会盖住平台左侧导航（实测 R1 首拍即此缺陷）。 */
.pc-sidenav--session,
.pc-sidenav--chat {
  position: fixed;
  top: calc(var(--pc-frame-top, 0px) + var(--pc-header-h));
  left: var(--pc-frame-left, 0px);
  bottom: 0;
  z-index: 30;
  overflow-y: auto;
}
.pc-sidenav.is-collapsed {
  width: var(--pc-sidenav-w-collapsed);
}

/* ── 菜单项 ─────────────────────────────────────────────────────── */
.pc-sidenav__list {
  list-style: none;
  margin: 0;
  padding: 0 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
/* 首页态两组之间发丝分隔（G-golden2 左栏「｜」两组结构） */
.pc-sidenav__list--divided {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--pc-line);
}
.pc-sidenav__top {
  padding: 6px 0 2px;
}
.pc-sidenav__item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 38px;
  padding: 0 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--pc-text-2);
  font-size: 14px;
  font-family: inherit;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}
.pc-sidenav__item:hover {
  background: var(--pc-section-bg);
  color: var(--pc-primary);
}
/* 诚实禁用项（教研中心未上线）：弱化呈现 + not-allowed，绝不「亮而无效」 */
.pc-sidenav__item.is-disabled,
.pc-sidenav__item:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
  background: transparent;
}
.pc-sidenav__item.is-active {
  background: var(--pc-selected);
  color: var(--pc-primary);
  font-weight: 600;
}
/* 激活态左侧 3px 主蓝竖条（贴导航左缘） */
.pc-sidenav__item.is-active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--pc-primary);
}
.pc-sidenav__icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
}
.pc-sidenav__icon svg {
  width: 18px;
  height: 18px;
}
.pc-sidenav__icon.is-flip {
  transform: rotate(180deg);
}
.pc-sidenav__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 折叠：只留图标（居中） */
.pc-sidenav.is-collapsed .pc-sidenav__item {
  justify-content: center;
  padding: 0;
}

/* ── 底部（收起侧栏）────────────────────────────────────────────── */
.pc-sidenav__foot {
  margin-top: auto;
  padding: 8px;
  border-top: 1px solid var(--pc-line);
}
.pc-sidenav__collapse {
  color: var(--pc-text-muted);
}

/* ── 分组（会话态）──────────────────────────────────────────────── */
.pc-sidenav__group {
  padding: 0 0 2px;
}
.pc-sidenav__group + .pc-sidenav__group {
  margin-top: 6px;
}
.pc-sidenav__group-title {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 5px 14px;
  border: none;
  background: transparent;
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  font-family: inherit;
  color: var(--pc-text-faint);
  text-align: left;
  cursor: pointer;
}
.pc-sidenav__group-chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  transition: transform 0.15s ease;
}
.pc-sidenav__group-chevron svg {
  width: 12px;
  height: 12px;
}
.pc-sidenav__group-chevron:not(.is-open) {
  transform: rotate(-90deg);
}

/* ── 当前备课进度（会话态，贴底）────────────────────────────────── */
.pc-sidenav__progress {
  margin-top: auto;
  padding: 8px 16px 10px;
  border-top: 1px solid var(--pc-line);
}
.pc-sidenav__progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pc-sidenav__progress-title {
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-sidenav__progress-pct {
  font-size: 12px;
  font-weight: 600;
  color: var(--pc-primary);
  font-variant-numeric: tabular-nums;
}
.pc-sidenav__progress-track {
  margin-top: 6px;
  height: 4px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-line);
  overflow: hidden;
}
.pc-sidenav__progress-bar {
  display: block;
  height: 100%;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary);
  transition: width 0.2s ease;
}
.pc-sidenav__checklist {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pc-sidenav__check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--pc-text-faint);
}
.pc-sidenav__check.is-done {
  color: var(--pc-text-2);
}
.pc-sidenav__check-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  color: var(--pc-text-faint);
}
.pc-sidenav__check.is-done .pc-sidenav__check-icon {
  color: var(--pc-success);
}
.pc-sidenav__check-icon svg {
  width: 16px;
  height: 16px;
}

/* ── 对话态（golden1 左栏）：备课中心头 / 最近备课 / 教材与资料胶囊 ───────── */
.pc-sidenav__chathead {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px 6px;
}
.pc-sidenav__chathead-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-sidenav__chathead-fold {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-sidenav__chathead-fold:hover {
  background: var(--pc-section-bg);
  color: var(--pc-primary);
}

.pc-sidenav__recent {
  margin-top: 10px;
  min-height: 0;
}
.pc-sidenav__recent-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
}
.pc-sidenav__recent-item:hover {
  background: var(--pc-section-bg);
}
.pc-sidenav__recent-item.is-active {
  background: var(--pc-selected);
}
.pc-sidenav__recent-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: var(--pc-radius-full);
  background: transparent;
}
.pc-sidenav__recent-item.is-active .pc-sidenav__recent-dot {
  background: var(--pc-primary);
}
.pc-sidenav__recent-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.pc-sidenav__recent-title {
  font-size: 13px;
  color: var(--pc-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pc-sidenav__recent-item.is-active .pc-sidenav__recent-title {
  color: var(--pc-primary);
  font-weight: 600;
}
.pc-sidenav__recent-meta {
  font-size: 11px;
  color: var(--pc-text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pc-sidenav__recent-chevron {
  flex: none;
  width: 12px;
  height: 12px;
  color: var(--pc-primary);
}
.pc-sidenav__recent-empty {
  margin: 2px 14px 0;
  font-size: 12px;
  color: var(--pc-text-faint);
}

.pc-sidenav__capsule {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-full);
  background: var(--pc-panel);
  color: var(--pc-text-2);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-sidenav__capsule:hover:not(:disabled) {
  border-color: var(--pc-primary);
  color: var(--pc-primary);
}
.pc-sidenav__capsule:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
}
</style>