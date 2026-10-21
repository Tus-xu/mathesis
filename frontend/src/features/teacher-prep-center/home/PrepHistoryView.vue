<script setup lang="ts">
/**
 * S8 备课历史（跨会话）（VISUAL-SPEC-V3 §3-S8 / GOLDEN_OBSERVATION_HISTORY）。
 *
 * - 左侧真实会话列表（store.fetchSessions → GET /prep/sessions）：彩色圆角图标 + 标题 +
 *   `YYYY-MM-DD HH:MM · 状态` + 右侧状态下拉胶囊（可切换筛选 / 选择会话查看版本）；
 * - 点击行进入对应会话（进行中 → 课堂设计；已完成 → 本次备课总览）；
 * - 右侧「版本管理」：选中会话的真实 blueprint revision 列表（listBlueprintRevisions）。
 *
 * 反空壳：后端无蓝图回滚接口 → 不渲染「恢复此版本」按钮；无真实数据不造行。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
// --pc-* 视觉 token 随路由根组件注入（本视图为独立路由入口）
import '@/features/teacher-prep-center/tokens/prep.css'
import {
  MINUTES_PER_LESSON_HOUR,
  usePrepSessionStore,
} from '@/features/teacher-prep-center/stores/prepSession'
import type { PrepSessionSummary } from '@/features/teacher-prep-center/api/prepCenterApi'
import {
  listBlueprintRevisions,
  type PrepBlueprintRevision,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import {
  HOME,
  HISTORY as H,
  absoluteTime,
  formatSessionMeta,
  revisionSourceLabel,
  statusLabel,
  statusTone,
  textbookVersionSegment,
} from '@/features/teacher-prep-center/copy/prepCopy'

const router = useRouter()
const store = usePrepSessionStore()

onMounted(() => {
  void store.fetchSessions()
  // 班级目录（真实 /teacher-v3/classes）：供列表按会话 class_id 解析班级名
  void store.fetchClasses()
})

const sessions = computed(() => store.sessions)
const isPermissionError = computed(
  () => store.sessionsErrorKind === 'forbidden' || store.sessionsErrorKind === 'unauthenticated',
)
const listError = computed(() =>
  isPermissionError.value ? H.listForbidden : store.sessionsError || H.listFailed,
)

/* ── 状态下拉筛选 ───────────────────────────────────────────────── */
const statusFilter = ref<string | null>(null)
const openMenu = ref<string | null>(null)
const filtered = computed(() =>
  statusFilter.value ? sessions.value.filter((s) => s.status === statusFilter.value) : sessions.value,
)
function toggleFilter(status: string) {
  statusFilter.value = statusFilter.value === status ? null : status
  openMenu.value = null
}

/* ── 行 ─────────────────────────────────────────────────────────── */
function titleOf(s: PrepSessionSummary): string {
  return (s.title ?? '').trim() || s.intent?.lesson_scope || s.intent?.knowledge_points?.[0] || HOME.untitledFallback
}
function timeOf(s: PrepSessionSummary): string {
  return absoluteTime(s.updated_at || s.created_at)
}
/** 元信息：会话元信息（教材版本 · 班级 · 时长，同一 formatSessionMeta 口径）+ 最近修改时间。 */
function metaOf(s: PrepSessionSummary): string {
  const hours = store.normalizeLessonHours(s.intent?.lesson_hours)
  const meta = formatSessionMeta({
    textbook: textbookVersionSegment(store.sourceTitleOf(s.intent?.source_id)),
    className: store.resolveClassName(s),
    duration: hours != null ? `${hours * MINUTES_PER_LESSON_HOUR}分钟` : null,
  })
  const t = timeOf(s)
  return t ? `${meta} · ${t}` : meta
}
function openSession(s: PrepSessionSummary) {
  const tab = statusTone(s.status) === 'done' ? 'overview' : 'classroom'
  void router.push(`/teacher-v3/prep/w/${s.session_id}/${tab}`)
}

/* ── 版本管理（选中会话的真实 revision）────────────────────────── */
const selected = ref<PrepSessionSummary | null>(null)
const revisions = ref<PrepBlueprintRevision[]>([])
const currentRevision = ref<number | null>(null)
const revLoading = ref(false)
const revFailed = ref(false)

async function selectForVersions(s: PrepSessionSummary) {
  openMenu.value = null
  selected.value = s
  revLoading.value = true
  revFailed.value = false
  try {
    const data = await listBlueprintRevisions(s.session_id)
    revisions.value = data.items ?? []
    currentRevision.value = data.current_revision ?? null
  } catch {
    revisions.value = []
    revFailed.value = true
  } finally {
    revLoading.value = false
  }
}
function retryVersions() {
  if (selected.value) void selectForVersions(selected.value)
}
function revTitle(item: PrepBlueprintRevision): string {
  const t = (item.body as { title?: unknown } | undefined)?.title
  return (typeof t === 'string' && t) || item.title || '教学蓝图'
}
function revSummary(item: PrepBlueprintRevision): string {
  if (item.note) return item.note
  const flow = item.body?.lesson_flow
  const stages = Array.isArray(flow) ? flow.length : item.stages
  return `${stages} 个环节`
}
</script>

<template>
  <div class="pc-hist">
    <!-- 备课中心不渲染第二套侧栏（用户裁决 2026-10-04，宪法 §3） -->

    <div class="pc-hist__main">
      <header class="pc-hist__head">
        <div>
          <h1 class="pc-hist__title">{{ H.pageTitle }}</h1>
          <p class="pc-hist__sub">{{ H.pageSubtitle }}</p>
        </div>
        <button
          v-if="statusFilter"
          type="button"
          class="pc-hist__filter-chip"
          @click="statusFilter = null"
        >
          {{ H.filterOnly(statusLabel(statusFilter)) }}
          <span aria-hidden="true">×</span>
        </button>
      </header>

      <div class="pc-hist__cols">
        <!-- 列表 -->
        <section class="pc-hist__list">
          <p v-if="store.sessionsLoading && !sessions.length" class="pc-hist__state">正在加载备课历史…</p>

          <div v-else-if="store.sessionsError" class="pc-hist__state">
            {{ listError }}
            <button type="button" class="pc-hist__retry" @click="store.fetchSessions()">{{ H.retry }}</button>
          </div>

          <ul v-else-if="filtered.length" class="pc-hist__rows">
            <li v-for="s in filtered" :key="s.session_id" class="pc-hist__row-wrap">
              <div class="pc-hist__row" @click="openSession(s)">
                <span class="pc-hist__icon" :class="`is-${statusTone(s.status)}`" aria-hidden="true">
                  <svg viewBox="0 0 20 20">
                    <path d="M5.8 3.2h5l3.4 3.4v10.2H5.8V3.2Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                    <path d="M10.8 3.2v3.4h3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                  </svg>
                </span>
                <span class="pc-hist__row-main">
                  <span class="pc-hist__row-title">{{ titleOf(s) }}</span>
                  <span class="pc-hist__row-meta">{{ metaOf(s) }}</span>
                </span>

                <!-- 状态下拉胶囊（切换筛选 / 选择会话查看版本） -->
                <button
                  type="button"
                  class="pc-hist__pill"
                  :class="`is-${statusTone(s.status)}`"
                  :aria-expanded="openMenu === s.session_id"
                  @click.stop="openMenu = openMenu === s.session_id ? null : s.session_id"
                >
                  <span class="pc-hist__pill-dot" aria-hidden="true"></span>
                  {{ statusLabel(s.status) }}
                  <svg viewBox="0 0 12 12" aria-hidden="true">
                    <path d="M2.6 4.6 6 8l3.4-3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>

                <div v-if="openMenu === s.session_id" class="pc-hist__menu-backdrop" @click.stop="openMenu = null"></div>
                <div v-if="openMenu === s.session_id" class="pc-hist__menu" role="menu">
                  <button type="button" class="pc-hist__menu-item" role="menuitem" @click.stop="toggleFilter(s.status)">
                    {{ H.filterOnly(statusLabel(s.status)) }}
                  </button>
                  <button type="button" class="pc-hist__menu-item" role="menuitem" @click.stop="selectForVersions(s)">
                    {{ H.selectForVersions }}
                  </button>
                </div>
              </div>
            </li>
          </ul>

          <div v-else class="pc-hist__empty">
            <span class="pc-hist__empty-icon" aria-hidden="true">
              <svg viewBox="0 0 22 22">
                <path d="M11 5.4S9 4 5.6 4v11.4c3.4 0 5.4 1.4 5.4 1.4V5.4Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
                <path d="M11 5.4s2-1.4 5.4-1.4v11.4c-3.4 0-5.4 1.4-5.4 1.4V5.4Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
              </svg>
            </span>
            <p class="pc-hist__empty-title">{{ H.emptyTitle }}</p>
            <p class="pc-hist__empty-hint">{{ H.emptyHint }}</p>
            <button type="button" class="pc-hist__empty-cta" @click="router.push('/teacher-v3/prep')">{{ H.emptyCta }}</button>
          </div>
        </section>

        <!-- 版本管理 -->
        <aside class="pc-hist__versions">
          <h3 class="pc-hist__versions-title">{{ H.versionsTitle }}</h3>
          <p v-if="!selected" class="pc-hist__state">{{ H.versionsHint }}</p>
          <p v-else-if="revLoading" class="pc-hist__state">{{ H.versionsLoading }}</p>
          <p v-else-if="revFailed" class="pc-hist__state">
            {{ H.versionsFailed }}
            <button type="button" class="pc-hist__retry" @click="retryVersions">{{ H.retry }}</button>
          </p>
          <template v-else>
            <p class="pc-hist__versions-session">{{ titleOf(selected) }}</p>
            <ul v-if="revisions.length" class="pc-hist__revs">
              <li
                v-for="item in revisions"
                :key="item.revision_no"
                class="pc-hist__rev"
                :class="{ 'is-current': item.revision_no === currentRevision }"
              >
                <span class="pc-hist__rev-no">V{{ item.revision_no }}</span>
                <span class="pc-hist__rev-main">
                  <span class="pc-hist__rev-title">{{ revTitle(item) }}</span>
                  <span class="pc-hist__rev-sum">{{ revSummary(item) }} · {{ revisionSourceLabel(item.source) }}</span>
                </span>
                <span class="pc-hist__rev-time">{{ absoluteTime(item.created_at) }}</span>
              </li>
            </ul>
            <p v-else class="pc-hist__state">{{ H.versionsEmpty }}</p>
          </template>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pc-hist {
  display: flex;
  align-items: stretch;
  /* --pc-viewport-h：嵌入外框后内容区可用高度（R1 G1；默认 100vh） */
  min-height: var(--pc-viewport-h, 100vh);
  background: var(--pc-bg);
  font-family: var(--pc-font);
}
.pc-hist__main {
  flex: 1 1 auto;
  min-width: 0;
  padding: 22px 24px 28px;
}
.pc-hist__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.pc-hist__title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-hist__sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-hist__filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border: none;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}

.pc-hist__cols {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

/* 列表 */
.pc-hist__rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pc-hist__row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  padding: 0 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.pc-hist__row:hover {
  border-color: var(--pc-primary);
}
.pc-hist__icon {
  flex: none;
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-hist__icon svg {
  width: 20px;
  height: 20px;
}
.pc-hist__icon.is-done {
  background: var(--pc-success-soft);
  color: var(--pc-success);
}
.pc-hist__icon.is-draft {
  background: var(--pc-warning-soft);
  color: var(--pc-warning);
}
.pc-hist__icon.is-danger {
  background: var(--pc-danger-soft);
  color: var(--pc-danger);
}
.pc-hist__icon.is-muted {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-hist__row-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pc-hist__row-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--pc-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-hist__row-meta {
  font-size: 12px;
  color: var(--pc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-hist__pill {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-hist__pill svg {
  width: 12px;
  height: 12px;
}
.pc-hist__pill.is-done {
  background: var(--pc-success-soft);
  color: var(--pc-success);
}
.pc-hist__pill.is-draft {
  background: var(--pc-warning-soft);
  color: var(--pc-warning);
}
.pc-hist__pill.is-danger {
  background: var(--pc-danger-soft);
  color: var(--pc-danger);
}
.pc-hist__pill.is-muted {
  background: var(--pc-bg);
  color: var(--pc-text-muted);
}
.pc-hist__pill-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--pc-radius-full);
  background: currentColor;
}
.pc-hist__pill.is-muted .pc-hist__pill-dot,
.pc-hist__pill.is-draft .pc-hist__pill-dot {
  display: none;
}
.pc-hist__menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.pc-hist__menu {
  position: absolute;
  top: 54px;
  right: 12px;
  z-index: 41;
  min-width: 148px;
  padding: 6px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  box-shadow: var(--pc-shadow-hover);
}
.pc-hist__menu-item {
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
.pc-hist__menu-item:hover {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}

.pc-hist__state {
  margin: 0;
  padding: 16px 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-hist__retry {
  margin-left: 8px;
  padding: 2px 10px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  font-size: 12px;
  font-family: inherit;
  color: var(--pc-text-2);
  cursor: pointer;
}

/* 空态 */
.pc-hist__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 48px 24px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  text-align: center;
}
.pc-hist__empty-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: 6px;
  border-radius: 12px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-hist__empty-icon svg {
  width: 22px;
  height: 22px;
}
.pc-hist__empty-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-hist__empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-hist__empty-cta {
  margin-top: 12px;
  height: 34px;
  padding: 0 18px;
  border: none;
  border-radius: var(--pc-radius-btn);
  background: var(--pc-primary);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.pc-hist__empty-cta:hover {
  background: var(--pc-primary-hover);
}

/* 版本管理面板 */
.pc-hist__versions {
  padding: 16px 18px;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
}
.pc-hist__versions-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-hist__versions-session {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text-2);
}
.pc-hist__revs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-hist__rev {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
}
.pc-hist__rev.is-current {
  background: var(--pc-primary-soft);
  border-color: var(--pc-primary);
}
.pc-hist__rev-no {
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
.pc-hist__rev-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pc-hist__rev-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-hist__rev-sum {
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-hist__rev-time {
  flex: none;
  font-size: 11px;
  color: var(--pc-text-faint);
}
</style>
