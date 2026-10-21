<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">模拟考试 · <span style="color:var(--brand-deep);">{{ loading ? '…' : `${total} 套` }}</span></div>
      <div class="sub">全真模拟 + 专题训练，限时作答，AI 自动判分，答错自动进错题本。</div>
    </div>

    <div class="section-head">
      <h2>📚 我的套卷 · 按时间</h2>
      <div class="resource-filters">
        <span
          v-for="f in filters" :key="f.key"
          class="filter" :class="{ active: activeFilter === f.key }"
          @click="activeFilter = f.key"
        >{{ f.label }}</span>
        <button class="primary-btn" style="margin-left:auto;" :disabled="generating" @click="generate">
          {{ generating ? '组卷中…' : '＋ 生成全真模卷' }}
        </button>
      </div>
    </div>

    <!-- 阶段5：真题套卷 · 按年份 -->
    <div class="card" style="padding:14px 20px;margin-bottom:14px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
        <strong style="font-size:14px;">📕 真题套卷 · 按年份（全真题，不掺 AI 题）</strong>
        <span style="font-size:12px;color:var(--ink3);" v-if="realLoading">加载中…</span>
      </div>
      <!-- S9（V2 文档）：年份/卷别筛选器替代 40+ 按钮墙；题量诚实标注为"已收录" -->
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
        <select v-model="filterYear" class="rp-select">
          <option value="">全部年份</option>
          <option v-for="y in rpYearOptions" :key="y" :value="y">{{ y }} 年</option>
        </select>
        <select v-model="filterVol" class="rp-select">
          <option value="">全部卷别</option>
          <option v-for="v in rpVolOptions" :key="v" :value="v">{{ v }}</option>
        </select>
        <span style="font-size:12px;color:var(--ink3);align-self:center;">{{ filteredPapers.length }} 套可组卷</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button v-for="y in filteredPapers" :key="y.year + y.vol" class="year-btn"
                :disabled="generatingYear !== 0"
                @click="startRealPaper(y)">
          {{ generatingYear === y.year + y.vol ? '组卷中…' : `${y.year} ${y.vol} · ${y.count} 题（${y.choice || 0}选择 ${y.blank || 0}填空 ${y.solution || 0}解答）` }}
        </button>
        <span v-if="!realLoading && !realYears.length" style="font-size:12.5px;color:var(--ink3);">
          暂无可组卷年份（真题卷需题库该年份 ≥5 题）
        </span>
      </div>
      <div style="font-size:11.5px;color:var(--ink3);margin-top:8px;">
        💡 "已收录 N 题" = 题库该卷真题数；组卷按原卷题号重组已收录真题，不掺 AI 补题。
      </div>
    </div>

        <div v-if="loading" class="state-box">加载中…</div>
    <div v-else-if="error" class="state-box">⚠ {{ error }}<button style="margin-left:10px;" @click="load">重试</button></div>
    <div v-else-if="!exams.length" class="state-box">
      <div style="font-size:15px;font-weight:800;color:var(--ink);margin-bottom:6px;">📝 还没有模考记录</div>
      <div style="margin-bottom:16px;">生成一套全真模卷，限时作答、AI 自动判分，答错自动进错题本</div>
      <button class="primary-btn" :disabled="generating" @click="generate">
        {{ generating ? '组卷中…' : '＋ 生成首套全真模卷' }}
      </button>
    </div>
    <div v-else-if="!shownExams.length" class="state-box">该分类下暂无套卷</div>

<div v-else class="exam-grid">
      <div v-for="e in shownExams" :key="e.exam_id" class="exam-card" style="cursor:pointer;" @click="openPaper(e)">
        <div v-if="e.best_score != null" class="badge-best">🏆 最高 {{ e.best_score }}</div>
        <span class="year-tag" :style="e.type === 'topic' ? { background: 'var(--purple)' } : {}">{{ e.typeLabel }}</span>
        <LatexText class="tt" :text="e.title" />
        <div class="exam-meta">
          <div class="row"><span class="k">💯 满分</span><b>{{ e.total_score ?? '--' }}</b></div>
          <div class="row"><span class="k">⏱ 用时</span><b>{{ e.duration_minutes ?? '--' }} 分钟</b></div>
          <div class="row"><span class="k">📅 日期</span><b>{{ e.dateText }}</b></div>
        </div>
        <div class="exam-note">{{ e.note }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/client'
import { useToastStore } from '@/stores/toast'
import LatexText from '@/components/LatexText.vue'

const router = useRouter()
const toast = useToastStore()
const activeFilter = ref('all')
const loading = ref(true)
const error = ref('')
const generating = ref(false)
const exams = ref([])
const total = ref(0)

const filters = [
  { key: 'all', label: '全部' },
  { key: 'full_mock', label: '全真模拟' },
  { key: 'topic', label: '专题训练' },
  { key: 'done', label: '已做' },
]

// ===== 阶段5：真题套卷（按年份，全真题不掺 AI 题） =====
const realYears = ref([])
const filterYear = ref('')
const filterVol = ref('')
const rpYearOptions = computed(() => [...new Set(realYears.value.map((y) => y.year))].sort((a, b) => b - a))
const rpVolOptions = computed(() => {
  const pool = filterYear.value ? realYears.value.filter((y) => y.year === filterYear.value) : realYears.value
  return [...new Set(pool.map((y) => y.vol))]
})
const filteredPapers = computed(() => realYears.value.filter((y) =>
  (!filterYear.value || y.year === filterYear.value) && (!filterVol.value || y.vol === filterVol.value)
))
watch(filterYear, () => {
  if (filterVol.value && !rpVolOptions.value.includes(filterVol.value)) filterVol.value = ''
})
const realLoading = ref(false)
const generatingYear = ref(0)

async function loadRealPapers() {
  realLoading.value = true
  try {
    const data = await api.get('/student/exam/real-papers')
    realYears.value = (data?.years || []).filter((y) => y.count >= 5)
  } catch { realYears.value = [] } finally { realLoading.value = false }
}

async function startRealPaper(y) {
  if (generatingYear.value) return
  generatingYear.value = y.year + y.vol
  try {
    const data = await api.post('/student/exam/generate', { type: 'real_paper', year: y.year, vol: y.vol })
    toast.success(`真题卷已组好（${data.bank_count} 道真题），开始作答`)
    router.push('/exam/' + data.exam_id)
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : '真题卷组卷失败')
  } finally { generatingYear.value = 0 }
}

function openPaper(e) {
  router.push('/exam/' + e.exam_id)
}

function fmtDate(iso) {
  if (!iso) return '--'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get('/student/exam/history', { size: 50 })
    total.value = data?.total ?? 0
    exams.value = (data?.items || []).map((it) => ({
      ...it,
      typeLabel: { topic: '专题训练', real_paper: '真题卷' }[it.type] || '全真模拟',
      dateText: fmtDate(it.created_at),
      note: it.attempts > 0
        ? `✓ 已做 ${it.attempts} 次 · 最高 ${it.best_score ?? '--'} / ${it.total_score ?? '--'} · 最近 ${it.last_score ?? '--'}`
        : '✗ 未做 · 点击开始作答',
    }))
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '加载失败，请稍后重试'
    exams.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function generate() {
  if (generating.value) return
  generating.value = true
  try {
    const data = await api.post('/student/exam/generate', { type: 'full_mock' })
    const qCount = (data?.items || []).length
    toast.success(`✅ 组卷成功：《${data?.title || '全真模卷'}》 ${qCount} 题 · 限时 ${data?.duration_minutes ?? '--'} 分钟`)
    await load()
  } catch (e) {
    toast.error(e instanceof ApiError ? `组卷失败：${e.message}` : '组卷失败，请稍后重试')
  } finally {
    generating.value = false
  }
}

const shownExams = computed(() => {
  if (activeFilter.value === 'done') return exams.value.filter((e) => e.attempts > 0)
  if (activeFilter.value === 'all') return exams.value
  return exams.value.filter((e) => e.type === activeFilter.value)
})

onMounted(() => { load(); loadRealPapers() })
</script>

<style scoped>
.state-box {
  padding: 40px 20px;
  text-align: center;
  color: var(--ink3);
  font-size: 13px;
  background: var(--card-bg, #fff);
  border: 1px dashed var(--line, #e5e7eb);
  border-radius: var(--radius-lg, 12px);
}
/* 主 CTA（页头组卷 + 空态首套卷共用） */
.primary-btn {
  padding: 8px 18px;
  border: 1px solid var(--brand);
  background: var(--brand);
  color: #fff;
  border-radius: 8px;
  font: inherit;
  font-size: 12.5px;
  font-weight: 700;
  cursor: pointer;
}
.primary-btn:disabled { opacity: 0.6; cursor: not-allowed; }
/* 套卷卡：标题独占一行，分数/用时/日期分行左对齐 */
.exam-card { cursor: default; }
.exam-card .tt { display: block; }
.exam-meta { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; font-size: 12.5px; color: var(--ink2); }
.exam-meta .row { display: flex; align-items: baseline; }
.exam-meta .k { width: 76px; flex-shrink: 0; color: var(--ink3); }
.exam-meta b { font-family: var(--font-num); font-weight: 800; color: var(--ink); }
.exam-note { font-size: 11.5px; color: var(--ink3); }
.year-btn {
  padding: 7px 14px;
  border: 1.5px solid var(--line, #eee2c8);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink2, #6b6353);
}
.year-btn:hover:not(:disabled) { border-color: var(--brand, #f59e0b); color: var(--brand-deep, #b45309); }
.year-btn:disabled { opacity: 0.6; cursor: wait; }

.rp-select {
  padding: 6px 12px; border-radius: 8px; border: 1px solid var(--line, #e2e8f0);
  background: var(--card, #fff); color: var(--ink, #0f172a); font: inherit; font-size: 12.5px;
}
</style>
