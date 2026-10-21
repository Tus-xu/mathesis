<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">🎓 课堂</div>
      <div class="sub">老师布置的任务、我的班级、AI 双师课堂——师生同一条线。</div>
    </div>

    <div class="cls-tabs">
      <button :class="{ active: tab === 'tasks' }" @click="tab = 'tasks'">📋 课堂任务</button>
      <button :class="{ active: tab === 'class' }" @click="tab = 'class'">🏫 我的班级</button>
      <button :class="{ active: tab === 'dual' }" @click="tab = 'dual'">🎬 双师课堂</button>
    </div>

    <TasksView v-if="tab === 'tasks'" />
    <ClassView v-else-if="tab === 'class'" />
    <div v-else class="dual-entry">
      <!-- 老师课堂：老师发布到本班的课件（教师原创优先，AI 只负责讲） -->
      <section class="tc-section">
        <div class="tc-head">
          <div class="tc-head-main">
            <div class="tc-title">🎓 老师课堂</div>
            <div class="tc-sub">老师发布到你们班的课件 · AI 主讲逐页讲解</div>
          </div>
          <button class="tc-refresh" :disabled="teacherLoading" title="刷新老师课堂" @click="loadTeacherCoursewares">
            {{ teacherLoading ? '加载中…' : '↻ 刷新' }}
          </button>
        </div>

        <div v-if="teacherLoading && !teacherItems.length" class="tc-empty">正在加载老师发布的课堂…</div>
        <div v-else-if="teacherFailed && !teacherItems.length" class="tc-empty">
          <span>老师课堂加载失败，请稍后重试</span>
          <button class="tc-inline-btn" @click="loadTeacherCoursewares">重试</button>
        </div>
        <div v-else-if="!teacherItems.length" class="tc-empty">
          <span>{{ teacherEmpty }}</span>
          <button class="tc-inline-btn" @click="tab = 'tasks'">去课堂任务</button>
        </div>
        <div v-else class="tc-grid">
          <article
            v-for="c in teacherItems" :key="c.id"
            class="tc-card" :class="{ opening: openingId === c.id }"
            role="button" tabindex="0"
            @click="openCourseware(c)" @keydown.enter.prevent="openCourseware(c)"
          >
            <div class="tc-card-top">
              <span v-if="c.teacherName" class="tc-teacher">👩‍🏫 {{ c.teacherName }}</span>
              <span v-else class="tc-teacher muted">老师课件</span>
              <span class="tc-status" :class="c.tone">{{ c.statusText }}</span>
            </div>
            <div class="tc-card-title" :title="c.title">{{ c.title }}</div>
            <div class="tc-card-meta">
              <span v-if="c.className">🏫 {{ c.className }}</span>
              <span>{{ c.pageCount }} 页</span>
              <span v-if="c.publishedText">{{ c.publishedText }}</span>
            </div>
            <div class="tc-bar"><div class="tc-fill" :style="{ width: c.progressPct + '%' }"></div></div>
            <div class="tc-card-foot">
              <span>我的进度 {{ c.progressPct }}%</span>
              <span>练习正确率 {{ c.opened && c.practiceAccuracy != null ? c.practiceAccuracy + '%' : '—' }}</span>
              <span class="tc-go">{{ openingId === c.id ? '打开中…' : c.actionText + ' →' }}</span>
            </div>
          </article>
        </div>
      </section>

      <div class="de-card" @click="$router.push('/dual')">
        <div class="de-ic">🎬</div>
        <div class="de-body">
          <div class="de-title">AI 双师课堂</div>
          <div class="de-sub">输入知识点，AI 主讲 + AI 助教生成可交互课件；播放页为全屏课堂模式</div>
        </div>
        <div class="de-go">进入 →</div>
      </div>
      <div class="de-recent" v-if="recentLoading || recentDual.length">
        <div class="de-r-title">🕘 最近课堂</div>
        <div v-if="recentLoading && !recentDual.length" class="de-r-item" style="color:var(--ink3, #94a3b8);">加载中…</div>
        <div v-for="s in recentDual" :key="s.session_id || s.id" class="de-r-item" @click="$router.push('/dual/' + (s.session_id || s.id))">
          <span class="de-r-dot" :class="s.status"></span>
          <span class="de-r-name">{{ s.title }}</span>
          <span class="de-r-meta">{{ s.slide_count }} 页 · {{ statusZh(s.status) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/client'
import { studentApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import { mapTeacherCoursewares, teacherCoursewareEmptyText } from '@/utils/dualClassroom'
import TasksView from '@/pages/student/TasksView.vue'
import ClassView from '@/pages/student/ClassView.vue'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const valid = ['tasks', 'class', 'dual']
const tab = ref(valid.includes(route.query.tab) ? route.query.tab : 'tasks')

watch(() => route.query.tab, (t) => { if (valid.includes(t)) tab.value = t })
watch(tab, (t) => router.replace({ query: { ...route.query, tab: t } }))

/* ---------- 老师课堂：老师发布到本班的课件 ---------- */
const teacherItems = ref([])
const teacherLoading = ref(false)
const teacherFailed = ref(false)
const openingId = ref('')
const teacherEmpty = computed(() => teacherCoursewareEmptyText({
  loading: teacherLoading.value,
  failed: teacherFailed.value,
  count: teacherItems.value.length,
}))

async function loadTeacherCoursewares() {
  if (teacherLoading.value) return
  teacherLoading.value = true
  teacherFailed.value = false
  try {
    const d = await studentApi.studentCoursewares()
    const payload = d?.data || d
    teacherItems.value = mapTeacherCoursewares(payload?.items)
  } catch {
    // 未登录/离线/后端未就绪时如实置失败态（不伪装成「老师没发布」）
    teacherFailed.value = true
    teacherItems.value = []
  } finally {
    teacherLoading.value = false
  }
}

function refreshWhenVisible() {
  if (typeof document !== 'undefined' && !document.hidden && tab.value === 'dual') {
    loadTeacherCoursewares()
  }
}
onMounted(() => { document.addEventListener('visibilitychange', refreshWhenVisible) })
onBeforeUnmount(() => { document.removeEventListener('visibilitychange', refreshWhenVisible) })

async function openCourseware(c) {
  if (!c?.id || openingId.value) return
  openingId.value = c.id
  try {
    const d = await studentApi.openStudentCourseware(c.id)
    const sid = (d?.data || d)?.session_id
    if (!sid) throw new Error('未获取到课堂会话，请稍后重试')
    // 交接老师/班级名给播放页顶部 badge（session.source_ref 通常只有 id，不含姓名）
    try {
      sessionStorage.setItem('dcx-teacher-deck', JSON.stringify({
        sessionId: String(sid),
        coursewareId: c.id,
        teacherName: c.teacherName,
        className: c.className,
        title: c.title,
      }))
    } catch { /* 隐私模式：badge 诚实省略姓名 */ }
    router.push('/dual/' + sid)
  } catch (e) {
    toast.error(e?.message || '打开老师课堂失败，请稍后重试')
  } finally {
    openingId.value = ''
  }
}

/* ---------- 最近课堂（沿用既有 /classroom/sessions） ---------- */
const recentDual = ref([])
const recentLoading = ref(false)
// 「最近课堂」跟随 tab 拉取（修复原 onMounted 守卫：仅 ?tab=dual 直挂载时才拉取，
// 之后切到双师 tab 永不刷新）；immediate 覆盖首挂载即 dual 的场景。
watch(tab, async (t) => {
  if (t !== 'dual') return
  loadTeacherCoursewares()
  recentLoading.value = true
  try {
    const d = await api.get('/classroom/sessions', { limit: 5 })
    // 老师课件会话已在「老师课堂」区独立呈现；「最近课堂」只保留 AI 自学课堂，
    // 避免同一份老师课件在页面上出现两次（历史老师课堂仍可从老师课堂列表进入）。
    recentDual.value = (d?.sessions || d?.items || [])
      .filter((s) => s?.source_type !== 'teacher_deck')
      .slice(0, 5)
  } catch { /* 未登录/离线时静默 */ } finally {
    recentLoading.value = false
  }
}, { immediate: true })
function statusZh(s) {
  return ({ ready: '已完成', generating: '备课中', failed: '生成失败' })[s] || s
}
</script>

<!-- 课堂容器页复用全局样式；局部样式仅 tab 与入口卡 -->
<style scoped>
.cls-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.cls-tabs button {
  padding: 9px 20px; border-radius: 999px; border: 1px solid var(--line, #e2e8f0); background: var(--card, #fff);
  font: inherit; font-size: 13.5px; font-weight: 600; color: var(--ink2, #475569); cursor: pointer;
}
.cls-tabs button.active { background: var(--primary-subtle, #eef2ff); border-color: var(--primary-border, #c7d2fe); color: var(--primary, #4f46e5); }
.dual-entry { display: flex; flex-direction: column; gap: 14px; }
.de-card {
  display: flex; align-items: center; gap: 16px; padding: 22px 24px;
  background: linear-gradient(135deg, #eef2ff 0%, #fff 60%); border: 1px solid var(--primary-border, #c7d2fe);
  border-radius: 18px; cursor: pointer; transition: all .2s ease;
}
.de-card:hover { transform: translateY(-2px); box-shadow: 0 10px 40px -12px rgba(79, 70, 229, .25); }
.de-ic { font-size: 34px; }
.de-title { font-size: 16px; font-weight: 800; color: var(--ink, #0f172a); }
.de-sub { font-size: 12.5px; color: var(--ink2, #475569); margin-top: 4px; }
.de-go { margin-left: auto; color: var(--primary, #4f46e5); font-weight: 800; font-size: 13.5px; }
.de-r-title { font-size: 12.5px; color: var(--ink3, #94a3b8); font-weight: 700; margin-bottom: 8px; }
.de-r-item {
  display: flex; align-items: center; gap: 10px; padding: 11px 14px; margin-bottom: 8px;
  background: var(--card, #fff); border: 1px solid var(--line, #e2e8f0); border-radius: 12px;
  cursor: pointer; font-size: 13px;
}
.de-r-item:hover { border-color: var(--primary-border, #c7d2fe); }
.de-r-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ink3, #94a3b8); }
.de-r-dot.ready { background: var(--ok, #059669); }
.de-r-dot.generating { background: var(--warn, #d97706); }
.de-r-dot.failed { background: var(--err, #dc2626); }
.de-r-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink, #0f172a); }
.de-r-meta { color: var(--ink3, #94a3b8); font-size: 12px; }

/* ===== 老师课堂（老师发布到本班的课件；位于 AI 自学课堂上方） ===== */
.tc-section {
  border: 1px solid var(--line, #e2e8f0); border-radius: 18px; padding: 16px 18px 18px;
  background: var(--card, #fff); display: flex; flex-direction: column; gap: 12px;
}
.tc-head { display: flex; align-items: flex-start; gap: 12px; }
.tc-head-main { min-width: 0; }
.tc-title { font-size: 15px; font-weight: 800; color: var(--ink, #0f172a); }
.tc-sub { margin-top: 3px; font-size: 12.5px; color: var(--ink2, #475569); }
.tc-refresh {
  margin-left: auto; flex: none; border: 1px solid var(--line, #e2e8f0); background: #fff;
  border-radius: 999px; padding: 6px 14px; font: inherit; font-size: 12px; font-weight: 700;
  color: var(--ink2, #475569); cursor: pointer;
}
.tc-refresh:hover:not(:disabled) { border-color: var(--primary-border, #c7d2fe); color: var(--primary, #4f46e5); }
.tc-refresh:disabled { opacity: .6; cursor: default; }
.tc-empty {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  border: 1px dashed var(--line, #e2e8f0); border-radius: 12px; padding: 18px 16px;
  font-size: 13px; color: var(--ink2, #475569); line-height: 1.6;
}
.tc-inline-btn {
  border: 1px solid var(--primary-border, #c7d2fe); background: var(--primary-subtle, #eef2ff);
  color: var(--primary, #4f46e5); border-radius: 999px; padding: 6px 14px;
  font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; flex: none;
}
.tc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; }
.tc-card {
  border: 1px solid var(--line, #e2e8f0); border-radius: 14px; padding: 13px 14px;
  display: flex; flex-direction: column; gap: 8px; cursor: pointer; background: #fff;
  transition: all .16s ease;
}
.tc-card:hover, .tc-card:focus-visible {
  border-color: var(--primary-border, #c7d2fe); transform: translateY(-2px);
  box-shadow: 0 10px 30px -14px rgba(79, 70, 229, .3); outline: none;
}
.tc-card.opening { opacity: .7; pointer-events: none; }
.tc-card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tc-teacher { font-size: 12px; font-weight: 700; color: var(--ink2, #475569); }
.tc-teacher.muted { color: var(--ink3, #94a3b8); font-weight: 600; }
.tc-status { flex: none; font-size: 11px; font-weight: 800; border-radius: 999px; padding: 2px 9px; }
.tc-status.idle { color: #475569; background: #f1f5f9; }
.tc-status.doing { color: #4f46e5; background: #eef2ff; }
.tc-status.done { color: #047857; background: #ecfdf5; }
.tc-card-title {
  font-size: 14px; font-weight: 800; color: var(--ink, #0f172a); line-height: 1.5;
  overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  min-height: 42px;
}
.tc-card-meta { display: flex; flex-wrap: wrap; gap: 8px; font-size: 11.5px; color: var(--ink3, #94a3b8); }
.tc-bar { height: 6px; border-radius: 999px; background: var(--line, #e2e8f0); overflow: hidden; }
.tc-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #4f46e5, #818cf8); transition: width .4s ease; }
.tc-card-foot { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 11px; color: var(--ink2, #475569); }
.tc-go { margin-left: auto; color: var(--primary, #4f46e5); font-weight: 800; font-size: 12px; }

/* 容器页保留子页自己的任务摘要（待完成数 / 刷新），不再隐藏 greeting。 */
</style>
