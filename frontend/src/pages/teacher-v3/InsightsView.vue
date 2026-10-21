<template>
  <div class="tv3-page-shell" data-testid="tv3-insights">
    <TeacherPageHeader title="学情洞察" subtitle="班级聚合 · 每生掌握 · 备课画像同步" icon="📊">
      <span v-if="currentClass" class="tv3-tag tv3-tag--primary" style="font-size: 12px">{{ currentClass.name }}</span>
      <select v-if="classes.length" v-model="classId" class="tv3-input" style="width: 160px; background: #fff; border-color: var(--tv3-line); color: var(--tv3-ink)" data-testid="tv3-ins-class">
        <option v-for="c in classes" :key="c.id" :value="c.id">{{ c.name }}</option>
      </select>
      <button
        class="tv3-btn tv3-btn--sm tv3-btn--gold" :disabled="syncing" data-testid="tv3-ins-sync-profile"
        title="把本班真实批改事实重算为备课画像（R19）；成功后备课会话的 AI 修订可引用学情证据"
        @click="syncProfile"
      >{{ syncing ? '同步中…' : '⟳ 同步到备课画像' }}</button>
    </TeacherPageHeader>

    <!-- 无班级引导 -->
    <TeacherEmptyState
      v-if="classLoaded && !classes.length"
      title="还没有班级"
      desc="建班并让学生加入后，这里会展示班级学情聚合与每生掌握明细。"
      cta="去班级管理"
      @cta-click="$router.push('/teacher-v3/classes')"
    />

    <div v-else-if="overview" style="display: flex; flex-direction: column; gap: 14px">
      <!-- 顶部指标（A5 learning-overview 确定性聚合） -->
      <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px">
        <div class="tv3-card" style="padding: 16px">
          <div class="tv3-kpi__label">班级平均掌握度</div>
          <div class="tv3-kpi__num" :style="{ color: masteryColor(overview.avg_mastery) }" data-testid="tv3-ins-avg-mastery">
            {{ overview.avg_mastery == null ? '—' : Math.round(overview.avg_mastery * 100) + '%' }}
          </div>
          <div style="font-size: 11.5px; color: var(--tv3-ink3); margin-top: 4px">来自已确认批改的 BKT 掌握度</div>
        </div>
        <div class="tv3-card" style="padding: 16px">
          <div class="tv3-kpi__label">待批积压</div>
          <div class="tv3-kpi__num" :style="{ color: overview.pending_review_count > 0 ? 'var(--tv3-rose)' : 'var(--tv3-teal)' }" data-testid="tv3-ins-pending">{{ overview.pending_review_count }}</div>
          <div style="font-size: 11.5px; color: var(--tv3-ink3); margin-top: 4px">
            <router-link v-if="overview.pending_review_count > 0" to="/teacher-v3/grading" style="color: var(--tv3-primary)">去批改工作台 →</router-link>
            <template v-else>全部已批完</template>
          </div>
        </div>
        <div class="tv3-card" style="padding: 16px">
          <div class="tv3-kpi__label">近 {{ overview.window_days }} 天提交</div>
          <div class="tv3-kpi__num" data-testid="tv3-ins-submitted">{{ totalSubmitted }} 份</div>
          <svg class="tv3-kpi__spark" viewBox="0 0 100 26" preserveAspectRatio="none">
            <rect v-for="(d, i) in trendBars" :key="i" :x="i * (100 / trendBars.length)" :y="24 - d.h" :width="100 / trendBars.length - 1.2" :height="d.h" fill="var(--tv3-primary)" opacity="0.75" />
          </svg>
        </div>
        <div class="tv3-card" style="padding: 16px">
          <div class="tv3-kpi__label">高频错因（近窗口）</div>
          <div class="tv3-kpi__num" style="color: var(--tv3-amber); font-size: 18px">{{ topCluster?.label || '—' }}</div>
          <div style="font-size: 11.5px; color: var(--tv3-ink3); margin-top: 4px">{{ topCluster ? `${topCluster.count} 人次错题聚集` : '暂无错题聚集' }}</div>
        </div>
      </div>

      <div style="display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; align-items: start">
        <!-- KP 热度（wrong_rate：越红越薄弱） -->
        <div class="tv3-card">
          <div class="tv3-card__head">
            <span class="tv3-card__title">知识点薄弱热力</span>
            <span class="tv3-card__sub">窗口内已判作答的错误率 · 点击去组卷补练</span>
          </div>
          <div class="tv3-card__body">
            <div v-if="!overview.kp_heat.length" style="padding: 18px; text-align: center; color: var(--tv3-ink3); font-size: 12.5px">窗口内还没有已判作答；发布作业并等学生提交后出现。</div>
            <div v-else class="tv3-heat" data-testid="tv3-ins-kp-heat">
              <div v-for="k in overview.kp_heat" :key="k.code" class="tv3-heat__row" style="cursor: pointer" @click="$router.push({ path: '/teacher-v3/quiz', query: { kp: k.name } })">
                <span class="tv3-heat__name">{{ k.name }}</span>
                <div class="tv3-heat__barwrap">
                  <div class="tv3-heat__bar" :style="{ width: k.wrong_rate * 100 + '%', background: heatColor(k.wrong_rate) }" />
                </div>
                <span class="tv3-heat__val">{{ Math.round(k.wrong_rate * 100) }}% 错</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 可行动洞察（A-既有 insights 确定性聚合） -->
        <div class="tv3-card">
          <div class="tv3-card__head">
            <span class="tv3-card__title">可行动洞察</span>
            <span class="tv3-card__sub">确定性聚合 · 附证据</span>
          </div>
          <div class="tv3-card__body" style="display: flex; flex-direction: column; gap: 8px" data-testid="tv3-ins-actions">
            <div v-if="!insights.length" style="padding: 14px; text-align: center; color: var(--tv3-ink3); font-size: 12.5px">暂无洞察（需要作业与提交数据积累）。</div>
            <div v-for="ins in insights" :key="ins.insight_id" class="tv3-prep__diffitem">
              <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
                <span class="tv3-tag" style="font-size: 10px">{{ kindLabel(ins.kind) }}</span>
                <b style="font-size: 12.5px">{{ ins.summary }}</b>
              </div>
              <div style="font-size: 11.5px; color: var(--tv3-ink2); line-height: 1.6; margin-top: 3px">{{ ins.evidence }}</div>
              <div v-if="ins.recommended_actions.length" style="display: flex; gap: 5px; flex-wrap: wrap; margin-top: 5px">
                <span v-for="a in ins.recommended_actions" :key="a" class="tv3-tag tv3-tag--gold" style="font-size: 10px">{{ a }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 每生明细（A4 roster） -->
      <div class="tv3-card">
        <div class="tv3-card__head">
          <span class="tv3-card__title">每生学情</span>
          <span class="tv3-card__sub">{{ roster?.students.filter((s) => s.confirmed).length ?? 0 }} 名在班学生 · 薄弱 KP 取掌握度最低 3 项</span>
        </div>
        <div class="tv3-card__body" style="overflow-x: auto" data-testid="tv3-ins-roster">
          <div v-if="!roster || !roster.students.length" style="padding: 18px; text-align: center; color: var(--tv3-ink3); font-size: 12.5px">班级还没有学生。把邀请码发给学生，确认入班后这里出现花名册。</div>
          <table v-else class="tv3-table">
            <thead>
              <tr>
                <th style="text-align: left">学生</th>
                <th>状态</th>
                <th>提交</th>
                <th>待批</th>
                <th>平均掌握度</th>
                <th style="text-align: left">薄弱知识点</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in roster.students" :key="s.user_id" :data-testid="`tv3-ins-student-${s.user_id}`">
                <td style="font-weight: 600; text-align: left">{{ s.nickname }}</td>
                <td><span class="tv3-tag" :class="s.confirmed ? 'tv3-tag--ok' : 'tv3-tag--warn'" style="font-size: 10px">{{ s.confirmed ? '在班' : '待确认' }}</span></td>
                <td class="tv3-table__score">{{ s.submission_count }}</td>
                <td class="tv3-table__score" :style="{ color: s.pending_count > 0 ? 'var(--tv3-rose)' : undefined }">{{ s.pending_count }}</td>
                <td class="tv3-table__score" :style="{ color: masteryColor(s.avg_mastery ?? undefined) }">{{ s.avg_mastery == null ? '—' : Math.round(s.avg_mastery * 100) + '%' }}</td>
                <td style="text-align: left">
                  <span v-if="!s.weak_kps.length" style="color: var(--tv3-ink4); font-size: 11.5px">—</span>
                  <span v-for="w in s.weak_kps" :key="w.code" class="tv3-tag tv3-tag--danger" style="font-size: 10px; margin-right: 4px">{{ w.name }} {{ Math.round(w.mastery * 100) }}%</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 有班级但无学情数据（诚实空态 + 引导） -->
    <TeacherLoading v-else-if="loading" />
    <div v-else-if="loadError" class="tv3-card" style="padding: 28px; text-align: center" data-testid="tv3-ins-error">
      <div style="font-size: 15px; font-weight: 800; color: var(--tv3-rose); margin-bottom: 6px">学情加载失败</div>
      <div style="font-size: 12.5px; color: var(--tv3-ink3); margin-bottom: 14px">{{ loadError }}</div>
      <button class="tv3-btn tv3-btn--primary" type="button" @click="load">重试</button>
    </div>
    <TeacherEmptyState
      v-else
      title="暂无学情数据"
      desc="先用「作业发布」发一份学前作业或课后习题；学生提交、你批改确认后，这里会出现真实学情。"
      cta="去发布作业"
      @cta-click="$router.push('/teacher-v3/assign')"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * InsightsView —— 班级学情（tel PLAN §1.2④）
 * 数据源全部切换 8000（D5：8100 /insights/overview 固定 avg=0 空壳，前端停止消费）：
 * - A5 GET /api/teacher/classes/{id}/learning-overview：提交趋势/KP 错误率热度/待批积压/掌握度均值/错因聚类；
 * - A4 GET /api/teacher/classes/{id}/roster：每生提交数/待批数/掌握度/薄弱 KP；
 * - A-既有 GET /api/teacher/classes/{id}/insights：可行动洞察；
 * - 「同步到备课画像」→ 8100 POST /prep/profiles/recalculate（T1，教师显式触发，无静默改画像）。
 * 无 LLM、无编造：无提交时诚实空态并引导先发布作业。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api8000, telKey, type ApiActionableInsight, type ApiClassItem, type ApiLearningOverview, type ApiRosterStudent } from '@/api/api8000'
import { v3Api } from '@/api/teacherV3'
import { pickDefaultClassId } from './publishFlow'
import { TeacherPageHeader, TeacherEmptyState, TeacherLoading } from '@/components/teacherV3/common'
import { useToastStore } from '@/stores/toast'
import { updateTv3Context } from '@/stores/teacherContext'
import { teachingClasses } from '@/utils/teachingClasses'

const toast = useToastStore()
const classes = ref<ApiClassItem[]>([])
const classId = ref('')
const classLoaded = ref(false)
const loading = ref(true)
const loadError = ref('')
const overview = ref<ApiLearningOverview | null>(null)
const roster = ref<{ class_id: string; students: ApiRosterStudent[] } | null>(null)
const insights = ref<ApiActionableInsight[]>([])
const syncing = ref(false)

const currentClass = computed(() => classes.value.find((c) => c.id === classId.value) ?? null)
const totalSubmitted = computed(() => (overview.value?.submission_trend || []).reduce((a, d) => a + d.submitted, 0))
const trendBars = computed(() => {
  const trend = overview.value?.submission_trend || []
  const max = Math.max(...trend.map((d) => d.submitted), 1)
  return trend.map((d) => ({ h: (d.submitted / max) * 24 }))
})
const topCluster = computed(() => [...(overview.value?.error_clusters || [])].sort((a, b) => b.count - a.count)[0] ?? null)

function masteryColor(m: number | null | undefined): string {
  if (m == null) return 'var(--tv3-ink3)'
  if (m >= 0.8) return 'var(--tv3-teal)'
  if (m >= 0.6) return 'var(--tv3-amber)'
  return 'var(--tv3-rose)'
}
/** 热度色：wrong_rate 越高越红 */
function heatColor(wrongRate: number): string {
  if (wrongRate >= 0.5) return 'linear-gradient(90deg, var(--tv3-rose), #e8627c)'
  if (wrongRate >= 0.3) return 'linear-gradient(90deg, var(--tv3-amber), #d99a3d)'
  return 'linear-gradient(90deg, var(--tv3-teal), #3eb5a8)'
}
function kindLabel(kind: string): string {
  return ({ error_cluster: '错因聚类', submission_trend: '提交趋势', low_mastery: '掌握薄弱', review_backlog: '批改积压' } as Record<string, string>)[kind] || kind
}

async function load() {
  if (!classId.value) return
  loading.value = true
  loadError.value = ''
  const cid = classId.value
  const [ov, ro, ins] = await Promise.allSettled([
    api8000.insights.learningOverview(cid, 14),
    api8000.insights.roster(cid),
    api8000.insights.classInsights(cid, true),
  ])
  overview.value = ov.status === 'fulfilled' ? ov.value : null
  roster.value = ro.status === 'fulfilled' ? ro.value : null
  insights.value = ins.status === 'fulfilled' ? ins.value.insights : []
  if (!overview.value) {
    const reason = ov.status === 'rejected' ? ov.reason : null
    const code = Number(reason?.code || 0)
    if (code === 40302) loadError.value = '你不是这个班的授课教师，无法查看学情。'
    else loadError.value = reason?.message || '学情服务暂时不可用，请稍后重试'
  }
  loading.value = false
}

/** tel T1：同步到备课画像（教师显式触发；需要本班已有备课会话与已发布作业） */
async function syncProfile() {
  const cid = classId.value
  if (!cid) return
  syncing.value = true
  try {
    const convs = await v3Api.prep.conversations()
    const conv = convs.data.items.find((c) => c.class_id === cid)
    if (!conv) {
      toast.error('本班还没有备课会话：先到备课中心以该班发起一次备课，再回来同步画像')
      return
    }
    const assigns = await api8000.assignments.list({ class_id: cid, status: 'published' })
    const target = assigns.assignments[0]
    if (!target) {
      toast.error('本班还没有已发布作业：发布作业并等学生提交后再同步画像')
      return
    }
    const r = await v3Api.prep.recalculateProfile({
      session_id: conv.session_id,
      class_id: cid,
      assignment_id: target.assignment_id,
      idempotency_key: telKey('profile'),
    })
    toast.success(`备课画像已重算${r.data.replayed ? '（指纹一致，幂等回读）' : ''}：可在备课会话用 AI 修订引用学情证据`)
  } catch (e) {
    toast.error(e instanceof Error ? `画像同步失败：${e.message}` : '画像同步失败，请重试')
  } finally {
    syncing.value = false
  }
}

onMounted(async () => {
  try {
    const r = await api8000.classes.list()
    classes.value = teachingClasses(r.items)
    classId.value = pickDefaultClassId(classes.value)
  } catch { /* 无班级 → 引导态 */ } finally {
    classLoaded.value = true
  }
  if (classId.value) await load()
  else loading.value = false
})

watch(classId, (nv, ov) => {
  if (nv && nv !== ov) void load()
})

watch([overview, currentClass], () => {
  updateTv3Context({
    route: '/teacher-v3/insights',
    class_name: currentClass.value?.name,
    topic: '班级学情',
    extra: overview.value ? `待批 ${overview.value.pending_review_count} · 掌握度 ${overview.value.avg_mastery == null ? '—' : Math.round(overview.value.avg_mastery * 100) + '%'}` : undefined,
  })
})
</script>

<style scoped>
.tv3-page-shell {
  margin: -22px; padding: 22px; min-height: calc(100vh - var(--tv3-topbar-h));
  background: var(--teacher-bg-gradient);
  box-sizing: border-box;
}
.tv3-kpi__label { font-size: 11.5px; color: var(--tv3-ink3); }
.tv3-kpi__num { font-family: var(--tv3-font-num); font-size: 26px; font-weight: 800; margin-top: 2px; }
.tv3-kpi__spark { width: 100%; height: 26px; margin-top: 4px; }
.tv3-heat { display: flex; flex-direction: column; gap: 8px; }
.tv3-heat__row { display: flex; align-items: center; gap: 10px; padding: 4px 6px; border-radius: 8px; }
.tv3-heat__row:hover { background: var(--tv3-bg2); }
.tv3-heat__name { width: 130px; font-size: 12.5px; flex-shrink: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tv3-heat__barwrap { flex: 1; height: 18px; border-radius: 6px; background: var(--tv3-bg2); overflow: hidden; }
.tv3-heat__bar { height: 100%; border-radius: 6px; transition: width 0.4s ease; }
.tv3-heat__val { font-family: var(--tv3-font-num); font-size: 12.5px; font-weight: 700; width: 56px; text-align: right; }
.tv3-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.tv3-table th { padding: 8px 10px; text-align: center; font-size: 11.5px; color: var(--tv3-ink3); border-bottom: 1px solid var(--tv3-line); white-space: nowrap; }
.tv3-table td { padding: 8px 10px; text-align: center; border-bottom: 1px solid var(--tv3-line2); }
.tv3-table__score { font-family: var(--tv3-font-num); font-weight: 600; }
</style>
