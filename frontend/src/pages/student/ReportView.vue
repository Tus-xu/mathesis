<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">{{ helloText }}</div>
      <div class="sub">{{ nickname }}，亮点、薄弱点、思维漏洞——刷题时不见的进步，系统都看到了。</div>
    </div>

    <!-- 今日行动：到期复习/薄弱专练/今日一题，点击直达错题本与练题（BKT/FSRS 数据源）；空数据诚实空态 -->
    <div class="today-block">
      <div class="ta-head">
        <h3>🎯 今日行动</h3>
        <span v-if="dueToday > 0" class="ta-due">今日到期错题 {{ dueToday }} 道</span>
      </div>
      <div v-if="actionsLoading" class="state-tip">行动清单生成中…</div>
      <div v-else-if="actionsError" class="state-tip">行动清单加载失败，稍后刷新重试</div>
      <template v-else>
        <div class="ta-list">
          <button v-for="(a, i) in actions" :key="i" type="button" class="ta-card" @click="goAction(a)">
            <span class="ta-ic">{{ actionIcon(a.type) }}</span>
            <span class="ta-body">
              <span class="ta-title">{{ a.title }}<em v-if="a.duration_min"> · 约 {{ a.duration_min }} 分钟</em></span>
              <span class="ta-reason">{{ a.reason }}</span>
            </span>
            <span class="ta-go">→</span>
          </button>
        </div>
        <div v-if="!actions.length" class="state-tip">今天没有到期复习，也没有待突破的薄弱点——保持节奏。</div>
        <div v-if="actionNotice" class="ta-notice">{{ actionNotice.text }}</div>
      </template>
    </div>

    <!-- 迭代17：AI 周报解读（小婷的话） -->
    <div v-if="weeklyLoading || weekly.narrative" class="weekly-card">
      <div class="weekly-head">
        <span class="weekly-title">🗞️ {{ weeklyTitle }}</span>
        <span v-if="!weeklyLoading" class="weekly-tag">AI 生成</span>
      </div>
      <div v-if="weeklyLoading" class="weekly-skeleton"></div>
      <template v-else>
        <div class="weekly-text">{{ weekly.narrative }}</div>
        <div v-if="weekly.data" class="weekly-foot">
          <span>本周 {{ weekly.data.answer_count }} 题 · 正确率 {{ weekly.data.accuracy }}%</span>
          <span v-if="weekly.data.composite_score">综合分 {{ weekly.data.composite_score }}</span>
          <span v-if="weekly.data.streak_days">连续 {{ weekly.data.streak_days }} 天</span>
        </div>
      </template>
    </div>

    <div v-if="highlightsLoading || highlightsError || highlights.length" class="celebrate">
      <h3>本周亮点</h3>
      <div v-if="highlightsLoading" class="state-tip">加载中…</div>
      <div v-else-if="highlightsError" class="state-tip">亮点数据加载失败，稍后刷新重试</div>
      <div v-else class="items">
        <div v-for="(h, i) in highlights" :key="i" class="item">
          <div><span class="em">{{ iconZh(h.icon) }}</span> <b>{{ h.title }}</b></div>
          <div class="desc">{{ h.desc }}</div>
        </div>
      </div>
    </div>

    <div class="section-head">
      <h2>薄弱环节 · {{ weakPoints.length ? weakPoints.length + ' 个待突破' : (weakLoading ? '生成中' : '还没生成') }}</h2>
      <span class="more" @click="go('/graph')">→ 看完整雷达图</span>
    </div>
    <div v-if="weakLoading" class="state-tip">加载中…</div>
    <div v-else-if="weakError" class="state-tip">薄弱画像加载失败，稍后刷新重试</div>
    <div v-else-if="!weakPoints.length" class="state-tip">
      做题数据还不够，AI 暂时分析不出你的薄弱点——这是诚实的说法，不装。
      先去练一组题，画像马上就有。
      <div style="margin-top:10px;"><button class="more" style="border:1px solid var(--line);padding:7px 16px;border-radius:999px;cursor:pointer;font:inherit;font-size:12.5px;" @click="go('/practice')">→ 去练题中心</button></div>
    </div>
    <div v-else style="display:grid;grid-template-columns:repeat(2,1fr);gap:14px;">
      <div v-for="w in weakPoints" :key="w.kp_code" class="weak-card" :class="cardCls(w.level)">
        <div class="head">
          <div class="dot" :class="w.level"></div>
          <div class="name">{{ w.kp_name }}</div>
          <div class="pct" :style="w.level === 'ok' ? { color: 'var(--ok-deep)' } : {}">{{ pct(w.mastery) }}</div>
        </div>
        <div class="reason">{{ w.ai_reason }}</div>
        <div class="action">
          <button v-if="w.primary_action" class="primary" @click="go(w.primary_action.route)">
            → {{ w.primary_action.label }}{{ w.primary_action.minutes ? `（${w.primary_action.minutes}min）` : '' }}
          </button>
          <button v-if="w.secondary_action" @click="go(w.secondary_action.route)">{{ w.secondary_action.label }}</button>
        </div>
      </div>
    </div>

    <div class="section-head">
      <h2>掌握度总览 · 我的知识点画像</h2>
      <span class="more" @click="go('/graph')">→ 看知识图谱</span>
    </div>
    <div v-if="masteryLoading" class="state-tip">加载中…</div>
    <div v-else-if="masteryError" class="state-tip">掌握度数据加载失败，稍后刷新重试</div>
    <div v-else-if="!masteryItems.length" class="state-tip">
      还没有掌握度数据——完成一组练习或一份课堂作业后，系统会为每个知识点建立掌握度画像（BKT）。
      <div style="margin-top:10px;"><button class="more" style="border:1px solid var(--line);padding:7px 16px;border-radius:999px;cursor:pointer;font:inherit;font-size:12.5px;" @click="go('/practice')">→ 去练题中心</button></div>
    </div>
    <div v-else class="week-grid">
      <div class="week-trend">
        <h4>📈 近 30 天平均掌握度曲线</h4>
        <svg class="chart-svg" viewBox="0 0 600 180" preserveAspectRatio="none">
          <defs>
            <linearGradient id="gradMastery" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#6366f1" stop-opacity="0.3"/>
              <stop offset="100%" stop-color="#6366f1" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <line x1="0" y1="45" x2="600" y2="45" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <line x1="0" y1="90" x2="600" y2="90" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <line x1="0" y1="135" x2="600" y2="135" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <text x="5" y="42" font-size="10" fill="#94a3b8" font-weight="700">90</text>
          <text x="5" y="87" font-size="10" fill="#94a3b8" font-weight="700">60</text>
          <text x="5" y="132" font-size="10" fill="#94a3b8" font-weight="700">30</text>
          <text x="5" y="172" font-size="10" fill="#94a3b8" font-weight="700">0</text>
          <path :d="masteryLine" fill="none" stroke="#6366f1" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          <path :d="masteryArea" fill="url(#gradMastery)"/>
          <circle :cx="masteryLastPoint.x" :cy="masteryLastPoint.y" r="4" fill="#6366f1"/>
          <circle :cx="masteryLastPoint.x" :cy="masteryLastPoint.y" r="9" fill="#6366f1" opacity="0.2"/>
          <text x="30" y="165" font-size="9" fill="#94a3b8">{{ masteryFirstDateLabel }}</text>
          <text :x="masteryLastPoint.x" y="165" font-size="9" fill="#94a3b8" text-anchor="end">今天</text>
        </svg>
      </div>
      <div class="reason-chart">
        <h4>🧩 当前画像</h4>
        <div class="mastery-stats">
          <div class="ms-row">
            <span class="ms-lbl">平均掌握度</span>
            <b>{{ pct(avgMastery) }}</b>
          </div>
          <div class="ms-row">
            <span class="ms-lbl">7 日环比</span>
            <b :style="deltaStyle">{{ deltaVs7dText }}</b>
          </div>
          <div class="ms-row">
            <span class="ms-lbl">提示依赖度</span>
            <b>{{ pct(hintDependency) }}<span class="ms-sub">越低越独立</span></b>
          </div>
        </div>
        <div v-if="topWeak.length" class="mastery-weak">
          <div class="mw-title">最薄弱 Top{{ topWeak.length }}</div>
          <div v-for="w in topWeak" :key="w.kp_code" class="mw-row">
            <span class="mw-name">{{ w.kp_name }}</span>
            <span class="mw-bar"><span class="mw-fill" :style="{ width: Math.max(4, Math.round(w.mastery * 100)) + '%' }"></span></span>
            <span class="mw-pct">{{ pct(w.mastery) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="section-head">
      <h2>本周深度分析</h2>
    </div>
    <div class="week-grid">
      <div class="week-trend">
        <h4>📈 14 天掌握度趋势 · 含 FSRS 预测遗忘曲线</h4>
        <div v-if="trendLoading" class="state-tip">加载中…</div>
        <div v-else-if="trendError" class="state-tip">趋势数据加载失败，稍后刷新重试</div>
        <div v-else-if="!histPoints.length" class="state-tip">暂无趋势数据，完成几天练习后再来看</div>
        <svg v-else class="chart-svg" viewBox="0 0 600 180" preserveAspectRatio="none">
          <defs>
            <linearGradient id="grad1" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#f59e0b" stop-opacity="0.3"/>
              <stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <line x1="0" y1="45" x2="600" y2="45" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <line x1="0" y1="90" x2="600" y2="90" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <line x1="0" y1="135" x2="600" y2="135" stroke="#e2e8f0" stroke-dasharray="3,3"/>
          <text x="5" y="42" font-size="10" fill="#94a3b8" font-weight="700">90</text>
          <text x="5" y="87" font-size="10" fill="#94a3b8" font-weight="700">60</text>
          <text x="5" y="132" font-size="10" fill="#94a3b8" font-weight="700">30</text>
          <text x="5" y="172" font-size="10" fill="#94a3b8" font-weight="700">0</text>
          <path :d="historyLine" fill="none" stroke="#f59e0b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          <path :d="historyArea" fill="url(#grad1)"/>
          <path v-if="forecastLine" :d="forecastLine" fill="none" stroke="#dc2626" stroke-width="2" stroke-dasharray="4,3" opacity="0.6"/>
          <text v-if="forecastLine" x="595" y="75" font-size="9" fill="#dc2626" font-weight="700" text-anchor="end">不复习→遗忘↓</text>
          <circle :cx="lastPoint.x" :cy="lastPoint.y" r="4" fill="#f59e0b"/>
          <circle :cx="lastPoint.x" :cy="lastPoint.y" r="9" fill="#f59e0b" opacity="0.2"/>
          <text x="30" y="165" font-size="9" fill="#94a3b8">{{ firstDateLabel }}</text>
          <text :x="lastPoint.x" y="165" font-size="9" fill="#94a3b8" text-anchor="end">今天</text>
        </svg>
      </div>
      <div class="reason-chart">
        <h4>🧠 错因分布 · 12 类思维漏洞</h4>
        <div v-if="distLoading" class="state-tip">加载中…</div>
        <div v-else-if="distError" class="state-tip">错因数据加载失败，稍后刷新重试</div>
        <div v-else-if="!distItems.length" class="state-tip">暂无错因数据，做错题后这里会统计你的思维漏洞</div>
        <div v-else class="reason-bars">
          <div v-for="(d, i) in distItems" :key="d.type" class="reason-bar">
            <span class="lbl">{{ d.type_zh }}</span>
            <span class="fill-wrap"><span class="fill" :style="{ width: barWidth(d.ratio), background: barColor(i) }"></span></span>
            <span class="num" :style="i === 0 ? { color: 'var(--err-deep)' } : {}">{{ Math.round(d.ratio * 100) }}%</span>
          </div>
        </div>
      </div>
      <div v-if="honestyLoading || honestyError || honesty" class="honest-card">
        <div class="ic">🪞</div>
        <div class="body">
          <template v-if="honestyLoading">
            <h5>诚实提示</h5>
            <p>加载中…</p>
          </template>
          <template v-else-if="honestyError">
            <h5>诚实提示</h5>
            <p>加载失败，稍后刷新重试</p>
          </template>
          <template v-else>
            <h5>{{ honestyTitle }}</h5>
            <p>{{ honesty.message }}</p>
            <p v-if="honesty.suggestion">{{ honesty.suggestion }}</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/api/client'
import { butlerApi, studentApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const nickname = computed(() => auth.nickname)

// 周报标题使用当前登录用户真实昵称（不是管家人格名"小婷"）
const weeklyTitle = computed(() =>
  nickname.value && nickname.value !== '同学' ? `${nickname.value}的周报` : '我的学习周报'
)

// 亮点图标中文映射（兼容旧后端/缓存中的英文键，向后兼容）
const ICON_ZH = {
  trend_up: '进步趋势',
  independence: '独立解题',
  streak: '连续学习',
  review_done: '复习完成',
  breakthrough: '正确率突破',
}
const iconZh = (key) => ICON_ZH[key] || key

function go(route) {
  if (route) router.push(route).catch(() => {})
}

const pct = (m) => Math.round((m ?? 0) * 100) + '%'
const cardCls = (level) => (level === 'ok' ? '' : level)

/* ---------- 今日行动（/mastery/today-actions，B6 行动化出口） ---------- */
const actions = ref([])
const actionNotice = ref(null)
const dueToday = ref(0)
const actionsLoading = ref(true)
const actionsError = ref(false)

const ACTION_ICONS = { review: '🔁', weak: '🎯', daily: '🏆', challenge: '⚡' }
const actionIcon = (t) => ACTION_ICONS[t] || '📌'

/** 行动卡直达：复习→错题本；专练/挑战→练题中心定向该知识点；今日一题→练题中心 */
function goAction(a) {
  if (a.type === 'review') return go('/errors')
  if (a.kp_code) return go(`/practice?kp=${encodeURIComponent(a.kp_code)}`)
  go('/practice')
}

async function loadTodayActions() {
  actionsLoading.value = true
  actionsError.value = false
  try {
    const d = await studentApi.todayActions()
    actions.value = d?.actions || []
    actionNotice.value = d?.notice || null
    dueToday.value = d?.due_today || 0
  } catch {
    actionsError.value = true
  } finally {
    actionsLoading.value = false
  }
}

/* ---------- 掌握度总览（/mastery/summary + /mastery/trend，学生学情展示） ---------- */
const masteryItems = ref([]) // radar：全量知识点 BKT 掌握度
const hintDependency = ref(0)
const deltaVs7d = ref(null)
const masteryTrend30 = ref([]) // 近 30 天日快照 [{date, avg_mastery}]
const masteryLoading = ref(true)
const masteryError = ref(false)

const avgMastery = computed(() => {
  if (!masteryItems.value.length) return 0
  return masteryItems.value.reduce((s, r) => s + (r.mastery ?? 0), 0) / masteryItems.value.length
})
const topWeak = computed(() =>
  [...masteryItems.value].sort((a, b) => (a.mastery ?? 0) - (b.mastery ?? 0)).slice(0, 3)
)
const deltaVs7dText = computed(() => {
  if (deltaVs7d.value === null || deltaVs7d.value === undefined) return '暂无可比'
  const v = Math.round(deltaVs7d.value * 1000) / 10
  return v > 0 ? `+${v}% ↑` : v < 0 ? `${v}% ↓` : '持平'
})
const deltaStyle = computed(() => ({
  color: deltaVs7d.value > 0 ? 'var(--ok-deep)' : deltaVs7d.value < 0 ? 'var(--err-deep)' : 'var(--ink2)',
}))

// 曲线坐标复用页内趋势 SVG 口径：x ∈ [30, 570]，y = 180 - mastery*150
const masteryPts = computed(() => {
  const pts = masteryTrend30.value
  const n = pts.length
  return pts.map((p, i) => ({
    x: n > 1 ? X0 + (i * (X1 - X0)) / (n - 1) : X0,
    y: yOf(p.avg_mastery),
  }))
})
const masteryLastPoint = computed(() => masteryPts.value[masteryPts.value.length - 1] || { x: X1, y: 180 })
const masteryLine = computed(() =>
  masteryPts.value.map((p, i) => `${i ? 'L' : 'M'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
)
const masteryArea = computed(() => {
  if (!masteryPts.value.length) return ''
  return `${masteryLine.value} L ${masteryLastPoint.value.x.toFixed(1)} 180 L ${X0} 180 Z`
})
const masteryFirstDateLabel = computed(() => {
  const d = masteryTrend30.value[0]?.date
  if (!d) return ''
  const parts = String(d).split('-')
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : d
})

async function loadMastery() {
  masteryLoading.value = true
  masteryError.value = false
  try {
    const [summary, trend] = await Promise.all([
      studentApi.masterySummary(),
      studentApi.masteryTrend(30),
    ])
    masteryItems.value = summary?.radar || []
    hintDependency.value = summary?.hint_dependency ?? 0
    deltaVs7d.value = summary?.delta_vs_7d ?? null
    masteryTrend30.value = trend?.points || []
  } catch {
    masteryError.value = true
  } finally {
    masteryLoading.value = false
  }
}

/* ---------- 迭代17：AI 周报（小婷的话） ---------- */
const weekly = ref({ narrative: '', data: null })
const weeklyLoading = ref(true)
async function loadWeekly() {
  weeklyLoading.value = true
  try {
    weekly.value = await butlerApi.weeklyReport()
  } catch (e) {
    weekly.value = { narrative: '', data: null }
    console.warn('[ReportView] weekly-report 加载失败，隐藏周报卡：', e?.message || e)
  } finally {
    weeklyLoading.value = false
  }
}

/* ---------- 本周亮点 ---------- */
const highlights = ref([])
const highlightsLoading = ref(true)
const highlightsError = ref(false)
const helloText = computed(() =>
  highlights.value.length ? `这周你做得最好的 ${highlights.value.length} 件事 🎉` : '本周学习报告 📊'
)
async function loadHighlights() {
  highlightsLoading.value = true
  highlightsError.value = false
  try {
    const d = await api.get('/student/report/highlights')
    highlights.value = d?.items || []
  } catch {
    highlightsError.value = true
  } finally {
    highlightsLoading.value = false
  }
}

/* ---------- 薄弱环节 Top4 ---------- */
const weakPoints = ref([])
const weakLoading = ref(true)
const weakError = ref(false)
async function loadWeakPoints() {
  weakLoading.value = true
  weakError.value = false
  try {
    const d = await api.get('/student/report/weak-points')
    weakPoints.value = d?.items || []
  } catch {
    weakError.value = true
  } finally {
    weakLoading.value = false
  }
}

/* ---------- 14 天掌握度趋势 + 遗忘预测 ---------- */
const history = ref([])
const forecast = ref([])
const trendLoading = ref(true)
const trendError = ref(false)

// SVG 坐标：历史段 x ∈ [30, 570]，预测段延伸至 600；y = 180 - mastery*150（网格线 90→45, 60→90, 30→135）
const X0 = 30
const X1 = 570
const FX1 = 600
const yOf = (m) => 180 - Math.min(Math.max(m ?? 0, 0), 1) * 150

const histPoints = computed(() => {
  const n = history.value.length
  return history.value.map((p, i) => ({
    x: n > 1 ? X0 + (i * (X1 - X0)) / (n - 1) : X0,
    y: yOf(p.mastery),
  }))
})
const lastPoint = computed(() => histPoints.value[histPoints.value.length - 1] || { x: X1, y: 180 })
const historyLine = computed(() =>
  histPoints.value.map((p, i) => `${i ? 'L' : 'M'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
)
const historyArea = computed(() => {
  if (!histPoints.value.length) return ''
  return `${historyLine.value} L ${lastPoint.value.x.toFixed(1)} 180 L ${X0} 180 Z`
})
const forecastLine = computed(() => {
  const pts = histPoints.value
  const f = forecast.value
  if (!pts.length || !f.length) return ''
  const last = pts[pts.length - 1]
  const step = (FX1 - last.x) / f.length
  const seg = f.map((p, j) => `L ${(last.x + step * (j + 1)).toFixed(1)} ${yOf(p.mastery).toFixed(1)}`)
  return `M ${last.x.toFixed(1)} ${last.y.toFixed(1)} ${seg.join(' ')}`
})
const firstDateLabel = computed(() => {
  const d = history.value[0]?.date
  if (!d) return ''
  const parts = String(d).split('-')
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : d
})

async function loadTrend() {
  trendLoading.value = true
  trendError.value = false
  try {
    const d = await api.get('/student/report/mastery-trend-forecast', { days: 14 })
    history.value = d?.history || []
    forecast.value = d?.forecast || []
  } catch {
    trendError.value = true
  } finally {
    trendLoading.value = false
  }
}

/* ---------- 12 类思维漏洞分布 ---------- */
const distItems = ref([])
const distLoading = ref(true)
const distError = ref(false)
const BAR_COLORS = ['var(--err)', 'var(--warn)', 'var(--purple)', 'var(--brand)', 'var(--ink3)']
const barColor = (i) => BAR_COLORS[i % BAR_COLORS.length]
const barWidth = (r) => Math.min(100, Math.max(4, Math.round((r ?? 0) * 200))) + '%'
async function loadDistribution() {
  distLoading.value = true
  distError.value = false
  try {
    const d = await api.get('/student/report/error-distribution')
    distItems.value = (d?.items || []).slice(0, 6)
  } catch {
    distError.value = true
  } finally {
    distLoading.value = false
  }
}

/* ---------- 诚实提示 ---------- */
const honesty = ref(null)
const honestyLoading = ref(true)
const honestyError = ref(false)
const honestyTitle = computed(() => {
  const f = honesty.value?.fluctuation ?? 0
  const tail = f < 0 ? '有波动' : f > 0 ? '在提升' : '保持稳定'
  return `诚实提示：独立解题率${tail}`
})
async function loadHonesty() {
  honestyLoading.value = true
  honestyError.value = false
  try {
    honesty.value = await api.get('/student/report/honesty')
  } catch {
    honestyError.value = true
  } finally {
    honestyLoading.value = false
  }
}

onMounted(() => {
  loadTodayActions()
  loadWeekly()
  loadHighlights()
  loadWeakPoints()
  loadMastery()
  loadTrend()
  loadDistribution()
  loadHonesty()
})
</script>

<style scoped>
.state-tip {
  padding: 18px;
  border: 1px dashed var(--line);
  border-radius: var(--radius-lg);
  background: var(--card);
  color: var(--ink3);
  font-size: 12.5px;
  text-align: center;
}

/* ===== 今日行动（B6 行动化） ===== */
.today-block {
  background: var(--card);
  border: 1px solid var(--brand-border, #c7d2fe);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  margin-bottom: 18px;
}
.ta-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.ta-head h3 { font-size: 15px; font-weight: 800; color: var(--ink); }
.ta-due {
  font-size: 11.5px; font-weight: 700; color: var(--err-deep);
  background: var(--err-bg); padding: 3px 10px; border-radius: 99px;
}
.ta-list { display: grid; gap: 8px; }
.ta-card {
  display: flex; align-items: center; gap: 12px; width: 100%;
  padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px;
  background: var(--bg2, #f8fafc); font: inherit; text-align: left; cursor: pointer;
  transition: border-color .15s ease, transform .15s ease;
}
.ta-card:hover { border-color: var(--brand); transform: translateY(-1px); }
.ta-ic { font-size: 22px; flex-shrink: 0; }
.ta-body { flex: 1; display: flex; flex-direction: column; gap: 2px; }
.ta-title { font-size: 13px; font-weight: 700; color: var(--ink); }
.ta-title em { font-style: normal; font-size: 11px; color: var(--ink3); font-weight: 600; }
.ta-reason { font-size: 11.5px; color: var(--ink2); line-height: 1.5; }
.ta-go { color: var(--brand-deep); font-weight: 800; flex-shrink: 0; }
.ta-notice {
  margin-top: 10px; padding: 10px 14px; border-radius: 10px;
  background: var(--warn-bg, #fffbeb); color: var(--warn-deep, #b45309);
  font-size: 12px; line-height: 1.6;
}

/* ===== 掌握度总览 ===== */
.mastery-stats { display: grid; gap: 8px; margin-bottom: 14px; }
.ms-row {
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px; color: var(--ink2); padding: 7px 0; border-bottom: 1px dashed var(--line);
}
.ms-row:last-child { border-bottom: none; }
.ms-row b { font-size: 14px; color: var(--ink); }
.ms-sub { font-size: 10.5px; color: var(--ink3); font-weight: 400; margin-left: 6px; }
.mastery-weak { border-top: 1px solid var(--line); padding-top: 10px; }
.mw-title { font-size: 11.5px; font-weight: 800; color: var(--ink3); margin-bottom: 8px; }
.mw-row { display: flex; align-items: center; gap: 8px; margin-bottom: 7px; }
.mw-name { width: 108px; font-size: 12px; color: var(--ink2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
.mw-bar { flex: 1; height: 7px; border-radius: 99px; background: var(--bg2); overflow: hidden; }
.mw-fill { display: block; height: 100%; border-radius: 99px; background: linear-gradient(90deg, var(--err), var(--warn)); }
.mw-pct { width: 40px; text-align: right; font-size: 12px; font-weight: 700; color: var(--ink2); flex-shrink: 0; }

/* ===== AI 周报（小婷的话，迭代17） ===== */
.weekly-card {
  background: linear-gradient(135deg, var(--brand-faint), var(--card));
  border: 1px solid var(--warn-border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  margin-bottom: 18px;
}
.weekly-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.weekly-title { font-size: 15px; font-weight: 800; color: var(--ink); }
.weekly-tag {
  font-size: 10.5px; font-weight: 700; color: var(--brand-deep);
  background: var(--brand-soft); padding: 2px 8px; border-radius: 99px;
}
.weekly-text { font-size: 13.5px; line-height: 1.8; color: var(--ink2); }
.weekly-foot {
  display: flex; gap: 16px; flex-wrap: wrap; margin-top: 12px;
  padding-top: 10px; border-top: 1px dashed var(--warn-border);
  font-size: 11.5px; color: var(--ink3);
}
.weekly-skeleton {
  height: 14px; border-radius: 6px;
  background: linear-gradient(90deg, var(--brand-soft) 25%, var(--brand-faint) 50%, var(--brand-soft) 75%);
  background-size: 200% 100%; animation: wk-shimmer 1.4s infinite;
}
@keyframes wk-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
