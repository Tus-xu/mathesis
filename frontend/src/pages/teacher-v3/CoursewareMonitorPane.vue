<script lang="ts">
/**
 * 纯展示映射（从组件导出，供契约测试直接断言；不依赖后端实例）。
 * 数字口径：
 * - avg_progress_pct 约定为 0–100；
 * - practice_accuracy 后端可能给 0–1 比值或 0–100 百分数 → 统一归一化展示；
 * - current_page / page_dropoff.page 来自 ClassroomSession.progress.slide_index（0 基）→ 展示 +1。
 */
export function coursewareStatusText(status: string): string {
  return (
    ({
      draft: '准备中',
      preparing: '准备中',
      published: '已发布',
      archived: '已下架',
      failed: '发布失败',
    } as Record<string, string>)[status] || status || '未知状态'
  )
}

export function coursewareStatusTone(status: string): string {
  if (status === 'published') return 'tv3-tag--ok'
  if (status === 'draft' || status === 'preparing') return 'tv3-tag--warn'
  if (status === 'failed') return 'tv3-tag--danger'
  return ''
}

export function accuracyPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  // 8000 契约固定为 0–100（courseware_monitor `round(correct / answered * 100, 1)`）。
  // 不再把 ≤1 猜成比值：1% 必须显示 1%，不能显示 100%。
  return `${Math.max(0, Math.min(100, Math.round(v)))}%`
}

export function progressPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${Math.max(0, Math.min(100, Math.round(v)))}%`
}

export function displayPage(page: number | null | undefined): string {
  if (page == null || !Number.isFinite(page)) return '—'
  return `第 ${Math.max(1, Math.floor(page) + 1)} 页`
}

export function clockOf(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export function updatedClock(d: Date | null): string {
  if (!d) return '—'
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
</script>

<script setup lang="ts">
/**
 * 课堂 · 双师课堂（Phase D 教师侧实时学情）。
 *
 * - 班级列表来自 8000 `/classes/mine`；课件列表来自 `/teacher/classroom-coursewares`；
 * - 点「查看学情」打开抽屉：班级汇总 4 数字 + 页级留存 + 每生表格；
 * - 每 3 秒轮询 `/monitor`（页面失焦暂停；回到前台立即补一次），显示「更新于 HH:MM:SS」；
 * - 空态诚实：没发布 → 引导去备课中心；已发布但没人打开 → 直说还没有学生进入；
 * - 不存在假进度/假人数：所有数字直接来自接口（null → 「—」）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api8000, type ApiClassItem, type ApiCoursewareItem, type ApiCoursewareMonitor } from '@/api/api8000'
import { useToastStore } from '@/stores/toast'
import { teachingClasses } from '@/utils/teachingClasses'

interface ClassOption {
  class_id: string
  name: string
  students: number
}

const router = useRouter()
const toast = useToastStore()

const classes = ref<ClassOption[]>([])
const classesLoading = ref(true)
const classesError = ref('')
const selectedClassId = ref('')

const items = ref<ApiCoursewareItem[]>([])
const listLoading = ref(false)
const listError = ref('')

const drawerOpen = ref(false)
const active = ref<ApiCoursewareItem | null>(null)
const monitor = ref<ApiCoursewareMonitor | null>(null)
const monitorLoading = ref(false)
const monitorError = ref('')
const monitorBusy = ref(false)
const updatedAt = ref<Date | null>(null)

const archiveArmed = ref(false)
const archiving = ref(false)
let archiveTimer: ReturnType<typeof setTimeout> | null = null

const POLL_MS = 3000
/** 有课件仍在准备（draft/preparing）时，列表每 5s 静默刷新到 published；
   全部发布后停止（不做无意义的常驻轮询）。 */
const PREPARING_REFRESH_MS = 5000
let pollTimer: ReturnType<typeof setInterval> | null = null
let listTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false

const currentClass = computed(() => classes.value.find((c) => c.class_id === selectedClassId.value) || null)
const dropoffMax = computed(() => Math.max(1, ...(monitor.value?.page_dropoff || []).map((d) => d.count)))
const updatedText = computed(() => updatedClock(updatedAt.value))

function errText(e: unknown): string {
  const err = e as { code?: number; status?: number; message?: string } | null
  const code = Number(err?.code ?? 0)
  if (code === 40302) {
    return '你不是这个班的授课教师（可能在该班是学生身份）。请让班主任在「班级管理」把你加为教师，或切换到自己的教学班。'
  }
  if (code === 40301) return '当前登录身份不是教师，请切换到教师身份后重试。'
  if (err?.message) {
    if (err.message === 'class_scope_denied') return '你不是这个班的授课教师，无法查看课堂课件。'
    return err.message
  }
  return '请求失败，请稍后重试'
}

async function loadClasses() {
  classesLoading.value = true
  classesError.value = ''
  try {
    const r = await api8000.classes.list()
    // 只保留我作为教师/助教/班主任的已确认班级：学生身份的班不能开课/监控，
    // 否则 8000 会返回 class_scope_denied（用户看到的“课件加载失败”）。
    classes.value = teachingClasses(r.items || []).map((c: ApiClassItem) => ({
      class_id: c.id,
      name: c.name,
      students: c.students,
    }))
    if (!selectedClassId.value || !classes.value.some((c) => c.class_id === selectedClassId.value)) {
      selectedClassId.value = classes.value[0]?.class_id || ''
    }
  } catch (e) {
    classes.value = []
    classesError.value = errText(e)
  } finally {
    classesLoading.value = false
  }
}

async function loadList(silent = false) {
  if (!selectedClassId.value) {
    items.value = []
    return
  }
  const classId = selectedClassId.value
  if (!silent) listLoading.value = true
  listError.value = ''
  try {
    const r = await api8000.coursewares.list({ class_id: classId })
    if (classId !== selectedClassId.value) return // 班级已切换：丢弃旧班级的迟到响应
    items.value = r.items || []
  } catch (e) {
    if (!silent && classId === selectedClassId.value) {
      items.value = []
      listError.value = errText(e)
    }
  } finally {
    if (!silent) listLoading.value = false
    scheduleListRefresh()
  }
}

function stopListRefresh() {
  if (listTimer) {
    clearTimeout(listTimer)
    listTimer = null
  }
}

/** 列表里仍有准备中的课件 → 5s 后静默刷新（学生端只看到 published，这里要跟上）。 */
function scheduleListRefresh() {
  stopListRefresh()
  if (disposed) return
  if (!items.value.some((i) => i.status === 'draft' || i.status === 'preparing')) return
  listTimer = setTimeout(async () => {
    if (typeof document !== 'undefined' && document.visibilityState !== 'hidden') {
      await loadList(true)
    } else {
      scheduleListRefresh()
    }
  }, PREPARING_REFRESH_MS)
}

async function bootstrap() {
  await loadClasses()
  await loadList()
}

function onClassChange(ev: Event) {
  const value = (ev.target as HTMLSelectElement).value
  if (value === selectedClassId.value) return
  selectedClassId.value = value
  items.value = []
  listError.value = ''
  closeDrawer()
  void loadList()
}

function refreshAll() {
  void bootstrap()
}

/* ---------- 学情抽屉：3s 真实轮询（失焦暂停） ---------- */
async function loadMonitor(silent = false) {
  if (!active.value || monitorBusy.value) return
  const coursewareId = active.value.courseware_id
  monitorBusy.value = true
  if (!silent) monitorLoading.value = true
  try {
    const m = await api8000.coursewares.monitor(coursewareId)
    if (!active.value || active.value.courseware_id !== coursewareId) return // 抽屉已切换：丢弃迟到响应
    monitor.value = m
    monitorError.value = ''
    updatedAt.value = new Date()
  } catch (e) {
    if (active.value?.courseware_id === coursewareId) monitorError.value = errText(e)
  } finally {
    monitorBusy.value = false
    monitorLoading.value = false
  }
}

function startPolling() {
  stopPolling()
  void loadMonitor()
  pollTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    void loadMonitor(true)
  }, POLL_MS)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onVisibility() {
  if (document.visibilityState === 'hidden') return
  if (items.value.some((i) => i.status === 'draft' || i.status === 'preparing')) void loadList(true)
  if (drawerOpen.value) void loadMonitor(true)
}function openMonitor(cw: ApiCoursewareItem) {
  disarmArchive()
  active.value = cw
  monitor.value = null
  monitorError.value = ''
  updatedAt.value = null
  drawerOpen.value = true
  startPolling()
}

function closeDrawer() {
  drawerOpen.value = false
  stopPolling()
  active.value = null
  monitor.value = null
  monitorError.value = ''
  updatedAt.value = null
  disarmArchive()
}

/* ---------- 软下架（两步确认；学生不再看到，历史会话保留） ---------- */
function disarmArchive() {
  archiveArmed.value = false
  if (archiveTimer) {
    clearTimeout(archiveTimer)
    archiveTimer = null
  }
}

function onArchiveClick() {
  if (!active.value || archiving.value) return
  if (!archiveArmed.value) {
    archiveArmed.value = true
    if (archiveTimer) clearTimeout(archiveTimer)
    archiveTimer = setTimeout(disarmArchive, 4000)
    return
  }
  void archiveActive()
}

async function archiveActive() {
  const cw = active.value
  if (!cw) return
  archiving.value = true
  try {
    await api8000.coursewares.archive(cw.courseware_id)
    disarmArchive()
    closeDrawer()
    toast.success('已下架：学生端不再显示这份课堂课件')
    await loadList()
  } catch (e) {
    toast.error(`下架失败：${errText(e)}`)
  } finally {
    archiving.value = false
  }
}

/* ---------- 展示 ---------- */
function dropWidth(count: number): number {
  return Math.max(2, Math.round((count / dropoffMax.value) * 100))
}

function openedHint(cw: ApiCoursewareItem): string {
  if (cw.status !== 'published') return '准备完成后学生可见'
  if (!cw.students_total) return '班级还没有确认学生'
  if (!cw.opened) return '还没有学生打开'
  if (cw.opened >= cw.students_total) return '全班已进入'
  return `${cw.opened} 人已进入`
}

onMounted(() => {
  void bootstrap()
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
  stopListRefresh()
  disarmArchive()
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <section class="cwm" data-testid="cwm">
    <div class="cwm__bar">
      <div class="cwm__bar-text">
        <div class="cwm__heading">班级双师课堂</div>
        <div class="cwm__desc">
          发布后的备课课件由 AI 主讲逐页讲解；这里显示真实学习进度与练习正确率（每 3 秒刷新）。
        </div>
      </div>
      <div class="cwm__bar-actions">
        <select
          class="tv3-input cwm__select"
          :value="selectedClassId"
          data-testid="cwm-class-select"
          aria-label="选择班级"
          :disabled="classesLoading || !classes.length"
          @change="onClassChange"
        >
          <option v-if="!classes.length" value="">暂无班级</option>
          <option v-for="c in classes" :key="c.class_id" :value="c.class_id">{{ c.name }} · {{ c.students }} 人</option>
        </select>
        <button type="button" class="tv3-btn tv3-btn--sm" :disabled="listLoading" @click="refreshAll">
          {{ listLoading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="classesError" class="tv3-card cwm__state" data-testid="cwm-classes-error">
      <b>班级目录加载失败</b>
      <p>{{ classesError }}</p>
      <button type="button" class="tv3-btn tv3-btn--sm" @click="refreshAll">重试</button>
    </div>

    <div v-else-if="classesLoading && !classes.length" class="tv3-card cwm__state">正在加载班级…</div>

    <div v-else-if="!classes.length" class="tv3-card cwm__empty" data-testid="cwm-no-class">
      <div class="cwm__empty-title">还没有可用于教学的班级</div>
      <p class="cwm__empty-desc">只显示我作为教师 / 助教 / 班主任的已确认班级。若你在某班是学生身份，请让班主任把教师身份加入该班，或先建自己的班。</p>
      <button type="button" class="tv3-btn tv3-btn--primary" @click="router.push('/teacher-v3/classes')">
        去班级管理
      </button>
    </div>

    <div v-else-if="listError" class="tv3-card cwm__state" data-testid="cwm-list-error">
      <b>课堂课件加载失败</b>
      <p>{{ listError }}</p>
      <button type="button" class="tv3-btn tv3-btn--sm" @click="loadList()">重试</button>
    </div>

    <div v-else-if="listLoading && !items.length" class="tv3-card cwm__state">正在加载课堂课件…</div>

    <div v-else-if="!items.length" class="tv3-card cwm__empty" data-testid="cwm-empty">
      <div class="cwm__empty-title">{{ currentClass?.name || '这个班' }} 还没有发布课堂课件</div>
      <p class="cwm__empty-desc">
        在「备课中心」打开一份课件，点「发布到班级课堂」并选择班级。学生端「课堂」会立刻出现老师课件。
      </p>
      <button type="button" class="tv3-btn tv3-btn--primary" data-testid="cwm-goto-prep" @click="router.push('/teacher-v3/prep')">
        去备课中心发布
      </button>
    </div>

    <div v-else class="cwm__grid" data-testid="cwm-list">
      <article
        v-for="cw in items"
        :key="cw.courseware_id"
        class="tv3-card cwm__card"
        :data-testid="`cwm-card-${cw.courseware_id}`"
      >
        <div class="cwm__card-head">
          <div class="cwm__card-title" :title="cw.title">{{ cw.title || '未命名课件' }}</div>
          <span class="tv3-tag" :class="coursewareStatusTone(cw.status)">{{ coursewareStatusText(cw.status) }}</span>
        </div>
        <div class="cwm__card-meta">
          {{ cw.class_name || currentClass?.name || '' }} · {{ cw.page_count }} 页
          <template v-if="cw.published_at"> · 发布于 {{ clockOf(cw.published_at) }}</template>
        </div>

        <div class="cwm__stats">
          <div class="cwm__stat">
            <b>{{ cw.opened }}<span class="cwm__stat-den">/{{ cw.students_total }}</span></b>
            <span>已打开</span>
          </div>
          <div class="cwm__stat">
            <b>{{ progressPct(cw.avg_progress_pct) }}</b>
            <span title="仅统计已打开课件的学生">已参与平均进度</span>
          </div>
          <div class="cwm__stat">
            <b>{{ accuracyPct(cw.practice_accuracy) }}</b>
            <span title="仅统计已作答的练习题">已作答正确率</span>
          </div>
          <div class="cwm__stat">
            <b>{{ clockOf(cw.updated_at) }}</b>
            <span>更新</span>
          </div>
        </div>

        <div class="cwm__card-foot">
          <span class="cwm__card-hint" :class="{ 'is-warn': cw.status === 'published' && !cw.opened }">{{ openedHint(cw) }}</span>
          <button
            type="button"
            class="tv3-btn tv3-btn--sm tv3-btn--primary"
            :disabled="cw.status !== 'published'"
            :title="cw.status === 'published' ? '查看每生进度与页级留存' : '准备完成后可查看学情'"
            :data-testid="`cwm-view-${cw.courseware_id}`"
            @click="openMonitor(cw)"
          >
            查看学情
          </button>
        </div>
      </article>
    </div>

    <!-- 学情抽屉（真实聚合：班级汇总 / 页级留存 / 每生明细） -->
    <Teleport to="body">
      <div v-if="drawerOpen && active" class="cwm-drawer__mask" @click.self="closeDrawer">
        <aside
          class="cwm-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="课堂学情"
          data-testid="cwm-drawer"
        >
          <header class="cwm-drawer__head">
            <div class="cwm-drawer__head-text">
              <div class="cwm-drawer__title">{{ active.title }}</div>
              <div class="cwm-drawer__sub">
                {{ active.class_name || currentClass?.name || '' }} · {{ active.page_count }} 页
                <span class="cwm-drawer__updated" data-testid="cwm-updated">更新于 {{ updatedText }}</span>
              </div>
            </div>
            <button type="button" class="cwm-drawer__x" aria-label="关闭" @click="closeDrawer">×</button>
          </header>

          <div class="cwm-drawer__body">
            <div v-if="monitorLoading && !monitor" class="cwm-drawer__state">正在加载学情…</div>
            <div v-else-if="monitorError && !monitor" class="cwm-drawer__state cwm-drawer__state--error" data-testid="cwm-monitor-error">
              <b>学情加载失败</b>
              <p>{{ monitorError }}</p>
              <button type="button" class="tv3-btn tv3-btn--sm" @click="loadMonitor()">重试</button>
            </div>

            <template v-else-if="monitor">
              <div class="cwm-drawer__summary" data-testid="cwm-summary">
                <div class="cwm-summary-card">
                  <b>{{ monitor.opened }}<span class="cwm__stat-den">/{{ monitor.students_total }}</span></b>
                  <span>已打开</span>
                </div>
                <div class="cwm-summary-card">
                  <b>{{ monitor.completed }}</b>
                  <span>已完成</span>
                </div>
                <div class="cwm-summary-card">
                  <b>{{ progressPct(monitor.avg_progress_pct) }}</b>
                  <span title="仅统计已打开课件的学生">已参与平均进度</span>
                </div>
                <div class="cwm-summary-card">
                  <b>{{ accuracyPct(monitor.practice_accuracy) }}</b>
                  <span title="仅统计已作答的练习题">已作答正确率</span>
                </div>
              </div>

              <p v-if="monitorError" class="cwm-drawer__stale">
                最近一次刷新失败（{{ monitorError }}），当前显示的是 {{ updatedText }} 的数据。
              </p>

              <section v-if="monitor.page_dropoff.length" class="cwm-drawer__section" data-testid="cwm-dropoff">
                <div class="cwm-drawer__section-title">
                  页级留存 <span>多少学生到过这一页</span>
                </div>
                <div v-for="d in monitor.page_dropoff" :key="d.page" class="cwm-drop">
                  <span class="cwm-drop__label">{{ displayPage(d.page) }}</span>
                  <div class="cwm-drop__track">
                    <i class="cwm-drop__bar" :style="{ width: `${dropWidth(d.count)}%` }" />
                  </div>
                  <span class="cwm-drop__count">{{ d.count }} 人</span>
                </div>
              </section>

              <section class="cwm-drawer__section">
                <div class="cwm-drawer__section-title">
                  学生明细 <span>{{ monitor.students.length }} 人</span>
                </div>
                <div v-if="!monitor.students.length || !monitor.opened" class="cwm-drawer__empty" data-testid="cwm-no-students">
                  课件已发布，但还没有学生打开。学生进入「课堂」后，这里会每 3 秒更新真实进度。
                </div>
                <div v-else class="cwm-drawer__table-wrap">
                  <table class="cwm-table" data-testid="cwm-students">
                    <thead>
                      <tr>
                        <th>学生</th>
                        <th>状态</th>
                        <th>当前页</th>
                        <th>已看</th>
                        <th>自检</th>
                        <th>练习</th>
                        <th>最后活跃</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="s in monitor.students" :key="s.user_id" :data-testid="`cwm-student-${s.user_id}`">
                        <td class="cwm-table__name">{{ s.name }}</td>
                        <td>
                          <span class="tv3-tag" :class="s.opened ? 'tv3-tag--ok' : ''">
                            {{ s.opened ? '已进入' : '未打开' }}
                          </span>
                        </td>
                        <td>{{ s.opened ? displayPage(s.current_page) : '—' }}</td>
                        <td>{{ s.pages_seen ? `${s.pages_seen} 页` : '—' }}</td>
                        <td>
                          <template v-if="s.self_check_ok || s.self_check_again">
                            <span class="cwm-table__ok">✓{{ s.self_check_ok }}</span>
                            <span class="cwm-table__again">↻{{ s.self_check_again }}</span>
                          </template>
                          <template v-else>—</template>
                        </td>
                        <td>
                          <template v-if="s.practice_answered">
                            {{ s.practice_answered }} 题 · 对 {{ s.practice_correct }}
                          </template>
                          <template v-else>—</template>
                        </td>
                        <td>{{ clockOf(s.last_active) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <div class="cwm-drawer__foot">
                <span class="cwm-drawer__poll">每 3 秒自动刷新 · 页面切到后台时暂停</span>
                <button
                  type="button"
                  class="tv3-btn tv3-btn--sm"
                  :class="{ 'tv3-btn--danger': archiveArmed }"
                  :disabled="archiving"
                  data-testid="cwm-archive"
                  @click="onArchiveClick"
                >
                  {{ archiving ? '下架中…' : archiveArmed ? '确认下架（学生不再可见）' : '下架课件' }}
                </button>
              </div>
            </template>
          </div>
        </aside>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.cwm { display: flex; flex-direction: column; gap: 14px; }
.cwm__bar {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 4px 2px 0;
}
.cwm__bar-text { flex: 1; min-width: 0; }
.cwm__heading { font-size: 17px; font-weight: 800; color: var(--tv3-ink); }
.cwm__desc { margin-top: 4px; font-size: 12.5px; line-height: 1.6; color: var(--tv3-ink3); }
.cwm__bar-actions { flex: none; display: flex; align-items: center; gap: 8px; }
.cwm__select { width: 210px; }
.cwm__state { padding: 28px; font-size: 13px; color: var(--tv3-ink2); line-height: 1.7; }
.cwm__state b { font-size: 14px; }
.cwm__state p { margin: 6px 0 10px; color: var(--tv3-ink3); }
.cwm__empty { padding: 40px 24px; text-align: center; }
.cwm__empty-title { font-size: 16px; font-weight: 800; color: var(--tv3-ink); }
.cwm__empty-desc { max-width: 460px; margin: 8px auto 16px; font-size: 13px; line-height: 1.7; color: var(--tv3-ink3); }

.cwm__grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 14px; }
.cwm__card { padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.cwm__card-head { display: flex; align-items: center; gap: 10px; }
.cwm__card-title {
  flex: 1; min-width: 0; font-size: 14.5px; font-weight: 700; color: var(--tv3-ink);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cwm__card-meta { font-size: 12px; color: var(--tv3-ink3); }
.cwm__stats {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px;
  margin-top: 4px; padding: 10px 12px; border-radius: 12px; background: var(--tv3-bg2);
}
.cwm__stat { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cwm__stat b { font-size: 16px; font-weight: 800; color: var(--tv3-ink); font-family: var(--tv3-font-num); }
.cwm__stat > span { font-size: 10.5px; color: var(--tv3-ink3); }
.cwm__stat-den { font-size: 11px; font-weight: 600; color: var(--tv3-ink3); }
.cwm__card-foot { display: flex; align-items: center; gap: 10px; margin-top: 2px; }
.cwm__card-hint { flex: 1; font-size: 11.5px; color: var(--tv3-ink3); }
.cwm__card-hint.is-warn { color: var(--tv3-amber); }

/* ── 学情抽屉 ────────────────────────────────────────────── */
.cwm-drawer__mask {
  position: fixed; inset: 0; z-index: 1300;
  background: rgba(10, 30, 58, 0.34);
  display: flex; justify-content: flex-end;
}
.cwm-drawer {
  width: min(760px, 94vw); height: 100%;
  background: var(--tv3-card); border-left: 1px solid var(--tv3-line);
  box-shadow: -18px 0 48px rgba(10, 30, 58, 0.22);
  display: flex; flex-direction: column;
  animation: cwm-slide-in 0.18s ease-out;
}
@keyframes cwm-slide-in { from { transform: translateX(24px); opacity: 0.6; } to { transform: translateX(0); opacity: 1; } }
.cwm-drawer__head {
  flex: none; display: flex; align-items: flex-start; gap: 12px;
  padding: 16px 20px; border-bottom: 1px solid var(--tv3-line2);
}
.cwm-drawer__head-text { flex: 1; min-width: 0; }
.cwm-drawer__title { font-size: 16px; font-weight: 800; color: var(--tv3-ink); }
.cwm-drawer__sub { margin-top: 4px; font-size: 12px; color: var(--tv3-ink3); display: flex; gap: 10px; flex-wrap: wrap; }
.cwm-drawer__updated { font-family: var(--tv3-font-num); color: var(--tv3-primary); }
.cwm-drawer__x {
  flex: none; width: 30px; height: 30px; border: none; border-radius: 8px;
  background: transparent; color: var(--tv3-ink3); font-size: 19px; line-height: 1; cursor: pointer;
}
.cwm-drawer__x:hover { background: var(--tv3-bg2); }
.cwm-drawer__body { flex: 1; min-height: 0; overflow-y: auto; padding: 16px 20px 24px; }
.cwm-drawer__state { padding: 48px 0; text-align: center; font-size: 13px; color: var(--tv3-ink3); line-height: 1.7; }
.cwm-drawer__state--error b { color: var(--tv3-rose); }
.cwm-drawer__summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.cwm-summary-card {
  padding: 12px 14px; border: 1px solid var(--tv3-line); border-radius: 12px; background: var(--tv3-bg2);
  display: flex; flex-direction: column; gap: 3px;
}
.cwm-summary-card b { font-size: 22px; font-weight: 800; color: var(--tv3-ink); font-family: var(--tv3-font-num); }
.cwm-summary-card > span { font-size: 11.5px; color: var(--tv3-ink3); }
.cwm-drawer__stale {
  margin: 10px 0 0; padding: 8px 10px; border-radius: 8px;
  background: var(--tv3-amber-soft); color: var(--tv3-amber); font-size: 12px; line-height: 1.6;
}
.cwm-drawer__section { margin-top: 18px; }
.cwm-drawer__section-title { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; font-size: 13.5px; font-weight: 700; color: var(--tv3-ink); }
.cwm-drawer__section-title span { font-size: 11.5px; font-weight: 400; color: var(--tv3-ink3); }

.cwm-drop { display: flex; align-items: center; gap: 10px; padding: 3px 0; }
.cwm-drop__label { flex: none; width: 58px; font-size: 12px; color: var(--tv3-ink2); }
.cwm-drop__track { flex: 1; height: 10px; border-radius: 999px; background: var(--tv3-bg2); overflow: hidden; }
.cwm-drop__bar { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--tv3-primary), #3b74cf); }
.cwm-drop__count { flex: none; width: 52px; text-align: right; font-size: 11.5px; color: var(--tv3-ink3); font-family: var(--tv3-font-num); }

.cwm-drawer__empty {
  padding: 22px; border: 1px dashed var(--tv3-line); border-radius: 12px;
  font-size: 12.5px; line-height: 1.7; color: var(--tv3-ink3); text-align: center;
}
.cwm-drawer__table-wrap { overflow-x: auto; border: 1px solid var(--tv3-line); border-radius: 12px; }
.cwm-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.cwm-table th {
  position: sticky; top: 0; padding: 9px 10px; text-align: left; white-space: nowrap;
  background: var(--tv3-bg2); color: var(--tv3-ink3); font-weight: 600; border-bottom: 1px solid var(--tv3-line);
}
.cwm-table td { padding: 9px 10px; border-bottom: 1px solid var(--tv3-line2); color: var(--tv3-ink2); white-space: nowrap; }
.cwm-table tr:last-child td { border-bottom: none; }
.cwm-table__name { font-weight: 600; color: var(--tv3-ink); }
.cwm-table__ok { color: var(--tv3-teal); font-weight: 600; margin-right: 6px; }
.cwm-table__again { color: var(--tv3-amber); font-weight: 600; }

.cwm-drawer__foot {
  display: flex; align-items: center; gap: 12px;
  margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--tv3-line2);
}
.cwm-drawer__poll { flex: 1; font-size: 11.5px; color: var(--tv3-ink3); }
</style>
