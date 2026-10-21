<template>
  <div class="view">
    <div class="greeting task-head">
      <div>
        <div class="hello">课堂任务中心 · <span style="color:var(--err);">{{ todoCount }} 项</span> 待完成</div>
        <div class="sub">老师下发的作业、组卷与课堂课件，都在这里统一接收；完成情况实时同步给老师。</div>
      </div>
      <button class="task-refresh" type="button" :disabled="refreshing" @click="loadAll">
        {{ refreshing ? '刷新中…' : '↻ 刷新' }}
      </button>
    </div>

    <div class="task-tabs">
      <button
        v-for="t in tabs" :key="t.key"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >{{ t.label }} <span class="num">{{ t.count }}</span></button>
    </div>

    <div v-if="cwError" class="task-warn">
      老师课件暂时加载失败（作业不受影响）：{{ cwError }}
      <button type="button" @click="loadCoursewares">重试</button>
    </div>

    <div v-if="initialLoading" class="empty-state">
      <div class="es-icon">⏳</div>
      <div class="es-text">任务加载中…</div>
    </div>
    <div v-else-if="errorMsg && !allTasks.length" class="empty-state">
      <div class="es-icon">⚠️</div>
      <div class="es-text">{{ errorMsg }}</div>
      <button class="guide-btn" @click="loadAll">重试</button>
    </div>
    <div v-else class="task-list">
      <div
        v-for="t in shownTasks" :key="t.id"
        class="task-item" :class="[t.cls, { done: t.done }]"
        :data-kind="t.kind"
      >
        <div class="type-ic" :class="t.iconCls">{{ t.icon }}</div>
        <div class="body">
          <div class="tt"><span class="type-badge" :class="t.iconCls">{{ t.typeLabel }}</span>{{ t.tt }}</div>
          <div class="meta">
            <span v-if="t.teacher">👨‍🏫 {{ t.teacher }}</span>
            <span v-if="t.className">🏫 {{ t.className }}</span>
            <span :class="{ deadline: t.deadlineCls }">{{ t.deadline }}</span>
            <span v-if="t.kp">📍 关联 {{ t.kp }}</span>
          </div>
          <div v-if="t.progress !== undefined" class="progress"><div class="f" :style="{ width: t.progress + '%' }"></div></div>
          <div v-if="t.progressText" class="progress-text">{{ t.progressText }}</div>
          <div v-else-if="t.doneText" style="font-size:11.5px;color:var(--ok-deep);font-weight:700;">{{ t.doneText }}</div>
        </div>
        <div class="right">
          <button v-if="t.primary" class="primary" :disabled="!!openingId" @click.stop="startTask(t)">
            {{ openingId === t.id ? '打开中…' : t.primary }}
          </button>
        </div>
      </div>

      <div v-if="!shownTasks.length" class="empty-state">
        <div class="es-icon">🎉</div>
        <div class="es-text">{{ emptyText }}</div>
        <button v-if="doneCount && activeTab === 'todo'" class="guide-btn" @click="activeTab = 'done'">
          查看已完成（{{ doneCount }}）
        </button>
        <button v-else-if="classGuide" class="guide-btn" @click="goClassTab">{{ classGuide.button }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '@/api/client'
import { studentApi } from '@/api'
import { mapTeacherCoursewares } from '@/utils/dualClassroom'
import { useToastStore } from '@/stores/toast'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
/** 默认「全部」：已完成的作业也必须能被学生再次找到（旧版默认「待完成」会让已完成任务凭空消失）。 */
const activeTab = ref('all')

/* ---------- 作业 / 组卷任务（GET /api/student/assignments?status=all，客户端分类） ---------- */
const tasks = ref([])
const loading = ref(true)
const errorMsg = ref('')
const refreshing = ref(false)

const TYPE_MAP = {
  quiz: { icon: '📝', iconCls: 'quiz', label: '练习', action: '▶ 开始' },
  pre_class: { icon: '🎓', iconCls: 'preclass', label: '学前作业', action: '▶ 开始' },
  homework: { icon: '📝', iconCls: 'homework', label: '课后习题', action: '▶ 开始' },
  watch: { icon: '🎬', iconCls: 'video', label: '视频', action: '▶ 观看' },
}

function isDone(it) {
  const p = it.progress || { done: 0, total: 0 }
  return p.total > 0 && p.done >= p.total
}

function pad(n) { return String(n).padStart(2, '0') }
function fmtDeadline(iso, overdue) {
  if (!iso) return { text: '', cls: false }
  const d = new Date(iso)
  if (isNaN(d)) return { text: '', cls: false }
  const txt = `截止 ${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  return overdue
    ? { text: `⏰ 已逾期 · ${txt}`, cls: true }
    : { text: `⏰ ${txt}`, cls: true }
}

function mapTask(it) {
  const done = isDone(it)
  const p = it.progress || { done: 0, total: 0 }
  const tm = TYPE_MAP[it.type] || { icon: '📋', iconCls: 'quiz', label: '练习', action: '▶ 开始' }
  const dl = fmtDeadline(it.deadline, it.overdue && !done)
  const pct = p.total > 0 ? Math.round((p.done / p.total) * 100) : 0
  return {
    id: `asg:${it.assignment_id}`,
    assignmentId: it.assignment_id,
    kind: 'assignment',
    cls: it.overdue && !done ? 'urgent' : 'normal',
    icon: tm.icon,
    iconCls: tm.iconCls,
    typeLabel: tm.label,
    tt: it.title,
    teacher: it.teacher_name || '',
    className: '',
    deadline: dl.text,
    deadlineCls: dl.cls,
    kp: (it.kp_labels || []).join('、'),
    progress: done ? undefined : pct,
    progressText: done ? '' : (p.done > 0 ? `已完成 ${p.done}/${p.total}` : `未开始 · 共 ${p.total} 项`),
    doneText: done ? '✓ 已完成' : '',
    done,
    status: it.overdue && !done ? 'overdue' : done ? 'done' : 'todo',
    primary: done ? '↻ 查看作答' : tm.action,
    sortAt: it.created_at || it.deadline || '',
  }
}

/* ---------- 老师课件（课堂任务统一收件：课件也是老师下发的任务） ---------- */
const coursewares = ref([])
const cwLoading = ref(true)
const cwError = ref('')

function mapCourseware(cw) {
  const done = cw.progressPct >= 100
  const published = cw.publishedText ? `发布于 ${cw.publishedText}` : ''
  return {
    id: `cw:${cw.id}`,
    coursewareId: cw.id,
    deckId: cw.deckId || '',
    kind: 'courseware',
    cls: 'normal',
    icon: '🎬',
    iconCls: 'video',
    typeLabel: '老师课件',
    tt: cw.title,
    teacher: cw.teacherName || '',
    className: cw.className || '',
    deadline: published,
    deadlineCls: false,
    kp: '',
    progress: done ? undefined : cw.progressPct,
    progressText: done
      ? ''
      : cw.pageCount
        ? `${cw.opened ? `已看 ${cw.progressPct}%` : '未开始'} · 共 ${cw.pageCount} 页`
        : '',
    doneText: done ? '✓ 已学完' : '',
    done,
    status: done ? 'done' : 'todo',
    primary: done ? '↻ 复习' : (cw.opened ? '继续学习' : '▶ 打开'),
    sortAt: cw.publishedAt || '',
  }
}

/* ---------- 班级成员资格（pending 空态引导：D3 列表只对 confirmed 生效） ---------- */
const myClasses = ref([])

async function loadMyClasses() {
  try {
    const data = await api.get('/classes/mine')
    myClasses.value = data?.items || []
  } catch { /* 班级信息拉取失败不阻塞任务列表，空态按普通无任务处理 */ }
}

const hasPendingClass = computed(() =>
  myClasses.value.some((c) => c.confirmed === false)
)

const classGuide = computed(() => {
  if (allTasks.value.length) return null
  if (hasPendingClass.value) {
    return { text: '等待老师确认加入班级后可见任务', button: '去我的班级查看' }
  }
  if (!myClasses.value.length) {
    return { text: '还没有加入班级——加入后就能看到老师下发的任务', button: '去加入班级' }
  }
  return null
})

function goClassTab() {
  router.push({ query: { ...route.query, tab: 'class' } })
}

/** 作业列表分页拉全（后端单页上限 50；旧版只取 20 条会让第 21 条后的任务静默消失）。 */
async function loadAssignments() {
  const collected = []
  let page = 1
  let total = 0
  try {
    do {
      const data = await api.get('/student/assignments', { status: 'all', page, size: 50 })
      const items = data?.items || []
      total = Number(data?.total || 0)
      collected.push(...items)
      if (!items.length) break
      page += 1
    } while (collected.length < total && page <= 10)
    tasks.value = collected.map(mapTask)
    errorMsg.value = ''
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '任务加载失败'
    if (!collected.length) tasks.value = []
  }
}

async function loadCoursewares() {
  cwLoading.value = true
  cwError.value = ''
  try {
    const d = await studentApi.studentCoursewares()
    const payload = d?.data || d
    const mapped = mapTeacherCoursewares(payload?.items)
      .map(mapCourseware)
      .sort((a, b) => String(b.sortAt || '').localeCompare(String(a.sortAt || '')))
    // 同一份课件可能被老师重复发布多次（每次生成一个新 courseware_id）：
    // 课堂任务只保留最新一版，避免同一课题刷屏；历史版本由老师端下架/管理。
    const seen = new Set()
    coursewares.value = mapped.filter((c) => {
      // 同一 deck 的多次发布（标题可能带“配图版”后缀）只保留最新一版。
      const key = c.deckId || `${c.tt}|${c.teacher}|${c.className}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  } catch (e) {
    cwError.value = e instanceof ApiError ? e.message : '请稍后重试'
    if (!coursewares.value.length) coursewares.value = []
  } finally {
    cwLoading.value = false
  }
}

async function loadAll() {
  refreshing.value = true
  await Promise.all([loadAssignments(), loadCoursewares(), loadMyClasses()])
  refreshing.value = false
}

const allTasks = computed(() =>
  [...tasks.value, ...coursewares.value].sort((a, b) => {
    if (a.done !== b.done) return a.done ? 1 : -1
    return String(b.sortAt || '').localeCompare(String(a.sortAt || ''))
  })
)

const tabs = computed(() => [
  { key: 'all', label: '全部', count: allTasks.value.length },
  { key: 'todo', label: '待完成', count: allTasks.value.filter((t) => t.status === 'todo' || t.status === 'overdue').length },
  { key: 'overdue', label: '已逾期', count: allTasks.value.filter((t) => t.status === 'overdue').length },
  { key: 'done', label: '已完成', count: allTasks.value.filter((t) => t.status === 'done').length },
])

const todoCount = computed(() => allTasks.value.filter((t) => t.status === 'todo' || t.status === 'overdue').length)
const doneCount = computed(() => allTasks.value.filter((t) => t.status === 'done').length)

const shownTasks = computed(() => {
  if (activeTab.value === 'all') return allTasks.value
  return allTasks.value.filter((t) => t.status === activeTab.value)
})

const emptyText = computed(() => {
  if (!allTasks.value.length) return classGuide.value ? classGuide.value.text : '暂无任务，老师还没有下发作业或发布课件'
  if (activeTab.value === 'todo' && doneCount.value) return `待完成任务已全部完成，共完成 ${doneCount.value} 项`
  return '这个分类下没有任务'
})

/* ---------- 进入任务：作业→作答页；课件→打开课堂会话 ---------- */
const openingId = ref('')

function openCourseware(cw) {
  if (!cw?.coursewareId || openingId.value) return
  openingId.value = cw.id
  studentApi.openStudentCourseware(cw.coursewareId)
    .then((d) => {
      const sid = (d?.data || d)?.session_id
      if (!sid) throw new Error('未获取到课堂会话，请稍后重试')
      try {
        sessionStorage.setItem('dcx-teacher-deck', JSON.stringify({
          sessionId: String(sid),
          coursewareId: cw.coursewareId,
          teacherName: cw.teacher,
          className: cw.className,
          title: cw.tt,
        }))
      } catch { /* 隐私模式：badge 诚实省略姓名 */ }
      router.push('/dual/' + sid)
    })
    .catch((e) => toast.error(e?.message || '打开老师课件失败，请稍后重试'))
    .finally(() => { openingId.value = '' })
}

function startTask(t) {
  if (t.kind === 'courseware') return openCourseware(t)
  router.push(`/tasks/${t.assignmentId || t.id}`)
}

/* ---------- 自动刷新：回到页面/每 30 秒静默刷新，老师刚发布的作业无需手动找 ---------- */
let pollTimer = null
function onVisibility() {
  if (document.visibilityState === 'visible') loadAll()
}
function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    if (document.visibilityState !== 'hidden') loadAll()
  }, 30000)
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

const initialLoading = computed(() => loading.value && cwLoading.value && !allTasks.value.length)

onMounted(() => {
  loading.value = true
  loadAll().finally(() => { loading.value = false })
  startPolling()
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<style scoped>
.task-head { display: flex; align-items: flex-start; gap: 14px; }
.task-head > div { flex: 1; min-width: 0; }
.task-refresh {
  flex: none; align-self: center; border: 1px solid var(--line, #e2e8f0); background: #fff;
  color: var(--ink2, #475569); border-radius: 999px; padding: 7px 16px;
  font: inherit; font-size: 12.5px; font-weight: 700; cursor: pointer;
}
.task-refresh:hover:not(:disabled) { border-color: var(--primary-border, #c7d2fe); color: var(--primary, #4f46e5); }
.task-refresh:disabled { opacity: .6; cursor: default; }
.task-warn {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 10px; padding: 9px 12px; border-radius: 10px;
  background: var(--warn-soft, #fffbeb); color: var(--warn-deep, #b45309); font-size: 12.5px;
}
.task-warn button {
  border: 1px solid currentColor; background: transparent; color: inherit; border-radius: 999px;
  padding: 3px 10px; font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer;
}
.type-badge {
  display: inline-block;
  font-size: 10.5px;
  font-weight: 800;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 999px;
  margin-right: 8px;
  vertical-align: 2px;
  background: var(--bg2, #f1f5f9);
  color: var(--ink2, #475569);
}
.type-badge.preclass { background: var(--purple-soft, #f3e8ff); color: var(--purple, #7c3aed); }
.type-badge.homework { background: var(--warn-soft, #fef3c7); color: var(--warn-deep, #b45309); }
.type-badge.quiz { background: var(--brand-soft, #eef2ff); color: var(--brand-deep, #4338ca); }
.type-badge.video { background: var(--purple-soft, #f3e8ff); color: var(--purple, #7c3aed); }

.task-item .type-ic.preclass { background: var(--purple-soft, #f3e8ff); }
.task-item .type-ic.homework { background: var(--warn-soft, #fef3c7); }

.guide-btn {
  margin-top: 12px;
  padding: 8px 18px;
  border: 1px solid var(--brand, #4f46e5);
  border-radius: 999px;
  background: var(--brand-soft, #eef2ff);
  color: var(--brand-deep, #4338ca);
  font: inherit;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
}
</style>
