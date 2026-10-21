<template>
  <!--
    R2 BKT 掌握度卡（card.type === 'mastery_bkt'）：
    - 数据全部由后端 butler.student.mastery.snapshot.trend.read 工具从真实
      mastery_records / mastery_snapshots 下发（P(known)），前端不自取数、不自算，
      趋势点缺就是缺（null 不参与均值、点数不足不画线）——绝不合成假趋势。
    - 主图：各知识点 P(known) 水平条（弱在上），条色按既有阈值 <0.4 err /
      0.4-0.7 warn / ≥0.7 ok（对齐 growth_router.py:135-136 的 0.4/0.7 分级）；
    - 副图：近 14 天每日均值折线（由各知识点 trend 真实快照聚合）；
    - 模型标注如实：BKT-lite（答题 < 门槛）或 标准 BKT，不夸大算法。
    - 空态：empty_reason → 诚实文案 + 去练习按钮（open_page 协议）。
  -->
  <div class="mbc-card">
    <div class="mbc-head">
      <span class="mbc-title">🧠 知识点掌握度 P(known)</span>
      <span class="mbc-pill" :class="{ hot: isFullBkt }">{{ modelLabel }}</span>
    </div>

    <div v-if="card.degraded" class="mbc-degraded">⚠️ AI 分析暂不可用，以下为降级结果</div>
    <template v-else>
      <div v-if="accumulating" class="mbc-accum">📊 数据积累中 · 已积累 {{ validDays }} 天快照，趋势仅供参考</div>
      <!-- 后端对真实数据的一句摘要（snapshot_days 稀疏信息优先走上面的徽标，避免重复） -->
      <div v-else-if="card.message && items.length" class="mbc-msg">{{ card.message }}</div>
    </template>

    <!-- 空态：后端明确空集，诚实展示 -->
    <div v-if="!items.length" class="mbc-empty">
      <div class="mbc-empty-icon">📊</div>
      <div class="mbc-empty-text">{{ emptyText }}</div>
      <button v-if="emptyAction" class="mbc-empty-btn" @click="$emit('act', emptyAction)">
        {{ emptyAction.label || '去做几道题' }}
      </button>
    </div>

    <template v-else>
      <!-- 关键数字行：平均掌握度 / 薄弱 Top1 / 已掌握数 -->
      <div class="mbc-nums">
        <div class="mbc-num">
          <b>{{ pct(summary?.avg_mastery) }}%</b>
          <span>平均掌握度</span>
        </div>
        <div class="mbc-num">
          <b class="is-warn">{{ weakTop1Text }}</b>
          <span>最薄弱知识点</span>
        </div>
        <div class="mbc-num">
          <b class="is-ok">{{ masteredText }}</b>
          <span>已掌握（≥70%）</span>
        </div>
      </div>

      <!-- 主图：水平条 P(known)（0-100%，条色按 0.4/0.7 阈值） -->
      <div ref="mainRef" class="mbc-chart" :style="{ height: chartHeight + 'px' }"></div>

      <!-- 变化趋势文字（delta=今日快照-前一日，后端下发；无变化不列） -->
      <div v-if="deltaItems.length" class="mbc-deltas">
        <span v-for="d in deltaItems" :key="d.kp_code" class="mbc-delta" :class="d.cls">
          {{ d.arrow }} {{ d.kp_name }} {{ d.sign }}{{ Math.abs(Math.round(d.delta * 100)) }}%
        </span>
        <span class="mbc-delta-note">较前一日</span>
      </div>

      <!-- 副图：近 14 天每日均值折线（真实快照 ≥2 天才画，不合成） -->
      <template v-if="trendPoints.length >= 2">
        <button class="mbc-toggle" type="button" @click="trendOpen = !trendOpen">
          {{ trendOpen ? '收起 14 天趋势 ▲' : '近 14 天掌握度趋势 ▼' }}
        </button>
        <div v-show="trendOpen" ref="trendRef" class="mbc-trend"></div>
      </template>
      <div v-else class="mbc-notrend">趋势快照积累中（已积累 {{ validDays }} 天）</div>

      <div class="mbc-foot">
        来自掌握度记录 · BKT 知识追踪<template v-if="computedAtText"> · 计算于 {{ computedAtText }}</template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { chartPalette, useEcharts } from '@/composables/useEcharts'

const props = defineProps({
  card: { type: Object, required: true }, // {type:'mastery_bkt', model, summary, items[], ...}
})
defineEmits(['act'])

/* ===== 数据归一 ===== */
const items = computed(() =>
  (Array.isArray(props.card?.items) ? props.card.items : []).filter((it) => it && typeof it === 'object')
)
const summary = computed(() => (props.card?.summary && typeof props.card.summary === 'object' ? props.card.summary : null))

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : NaN
}
function pct(m) {
  const n = num(m)
  return Number.isFinite(n) ? Math.round(n * 100) : '--'
}

/* ===== 模型标注（如实，不夸大） ===== */
const threshold = computed(() => {
  const n = num(props.card?.min_answers_threshold)
  return Number.isFinite(n) && n > 0 ? n : 20
})
const isFullBkt = computed(() => props.card?.model === 'bkt')
const modelLabel = computed(() =>
  isFullBkt.value ? '标准 BKT' : `BKT-lite（答题 < ${threshold.value} 题）`
)

/* ===== 空态 ===== */
const EMPTY_ZH = {
  no_mastery_data: '还没有练习数据——完成一次练习后，这里会出现各知识点的掌握概率',
  insufficient_history: '数据积累中：再练几次，掌握度趋势会更完整',
}
const emptyText = computed(() => props.card?.message || EMPTY_ZH[props.card?.empty_reason] || '暂时没有可展示的掌握度数据')
const emptyAction = computed(() => {
  if (props.card?.empty_reason !== 'no_mastery_data') return null
  return { kind: 'open_page', to: '/practice', label: '去做几道题' }
})

/* ===== 快照有效天数（趋势去重日期数，真实快照口径） ===== */
const validDays = computed(() => {
  const dates = new Set()
  for (const it of items.value) {
    for (const p of Array.isArray(it?.trend) ? it.trend : []) {
      if (p && p.date && num(p.mastery) >= 0 && num(p.mastery) <= 1) dates.add(p.date)
    }
  }
  return dates.size
})
const accumulating = computed(() => items.value.length > 0 && validDays.value > 0 && validDays.value < 3)

/* ===== 关键数字 ===== */
const weakTop1Text = computed(() => {
  const w = summary.value?.weak_top1
  if (!w) {
    // summary 缺失时从 items 兜底（仍是真实下发数据，不合成）
    const weakest = items.value.slice().sort((a, b) => num(a?.mastery) - num(b?.mastery))[0]
    if (!weakest || !Number.isFinite(num(weakest.mastery))) return '--'
    return `${shortName(weakest.kp_name || weakest.kp_code)} ${pct(weakest.mastery)}%`
  }
  if (!Number.isFinite(num(w.mastery))) return '--'
  return `${shortName(w.kp_name || w.kp_code)} ${pct(w.mastery)}%`
})
const masteredText = computed(() => {
  const m = summary.value?.mastered_count
  const t = summary.value?.total_kps
  if (Number.isFinite(num(m)) && Number.isFinite(num(t))) return `${num(m)}/${num(t)}`
  // summary 缺失时按同一阈值从 items 统计
  const c = items.value.filter((it) => num(it?.mastery) >= 0.7).length
  return Number.isFinite(num(m)) ? `${num(m)}/${items.value.length}` : `${c}/${items.value.length}`
})
function shortName(s) {
  const str = String(s || '')
  return str.length > 8 ? `${str.slice(0, 8)}…` : str
}

/* ===== 主图：水平条（弱在上；条色按 state，阈值对齐 growth_router 0.4/0.7） ===== */
const pal = chartPalette()
const mainRef = ref(null)
const trendRef = ref(null)
const trendChart = useEcharts(trendRef)
const mainChart = useEcharts(mainRef)

const sortedItems = computed(() =>
  items.value.slice().sort((a, b) => num(a?.mastery) - num(b?.mastery)) // 升序：最弱在最上（inverse 轴）
)
const chartHeight = computed(() => Math.max(110, sortedItems.value.length * 26 + 26))

const STATE_COLOR = { weak: pal.err, improving: pal.warn, mastered: pal.ok }
function stateColor(it) {
  const s = String(it?.state || '')
  if (STATE_COLOR[s]) return STATE_COLOR[s]
  // state 缺失时按同一 0.4/0.7 阈值取色（口径一致）
  const m = num(it?.mastery)
  if (!Number.isFinite(m)) return pal.ink3
  return m < 0.4 ? pal.err : m < 0.7 ? pal.warn : pal.ok
}

function renderMain() {
  if (!sortedItems.value.length) return
  mainChart.render({
    animation: false,
    grid: { left: 92, right: 40, top: 8, bottom: 22 },
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (p) => {
        const it = p?.data?.raw || {}
        return [
          `<b>${p.name}</b>`,
          `P(known)=${pct(it.mastery)}%（${String(it.state === 'weak' ? '薄弱' : it.state === 'improving' ? '提升中' : it.state === 'mastered' ? '已掌握' : '—')}）`,
          Number.isFinite(num(it.practice_count)) ? `练习 ${num(it.practice_count)} 题 · 答对 ${num(it.correct_count)} 题` : '',
        ].filter(Boolean).join('<br/>')
      },
    },
    xAxis: {
      type: 'value', min: 0, max: 100,
      axisLabel: { color: pal.ink3, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: pal.line } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: sortedItems.value.map((it) => shortName(it.kp_name || it.kp_code || '知识点')),
      axisLabel: { color: pal.ink2, fontSize: 11, width: 84, overflow: 'truncate' },
      axisLine: { lineStyle: { color: pal.line } },
      axisTick: { show: false },
    },
    series: [
      {
        name: 'P(known)',
        type: 'bar',
        barWidth: 12,
        itemStyle: { borderRadius: [0, 6, 6, 0] },
        label: {
          show: true, position: 'right',
          color: pal.ink2, fontSize: 10,
          formatter: (p) => `${pct(p?.data?.raw?.mastery)}%`,
        },
        data: sortedItems.value.map((it) => ({
          value: Math.round(num(it.mastery) * 100) || 0,
          itemStyle: { color: stateColor(it) },
          raw: it,
        })),
      },
    ],
  })
}

/* ===== delta 文字（今日快照-前一日，后端下发） ===== */
const deltaItems = computed(() =>
  items.value
    .filter((it) => Number.isFinite(num(it?.delta)) && Math.abs(num(it.delta)) >= 0.005)
    .sort((a, b) => num(b.delta) - num(a.delta))
    .slice(0, 4)
    .map((it) => ({
      kp_code: it.kp_code,
      kp_name: shortName(it.kp_name || it.kp_code),
      delta: num(it.delta),
      arrow: num(it.delta) > 0 ? '📈' : '📉',
      sign: num(it.delta) > 0 ? '+' : '-',
      cls: num(it.delta) > 0 ? 'up' : 'down',
    }))
)

/* ===== 副图：近 14 天每日均值（各知识点真实快照的均值；null 不参与；≥2 天才画） ===== */
const trendOpen = ref(false)
const trendPoints = computed(() => {
  const byDate = new Map()
  for (const it of items.value) {
    for (const p of Array.isArray(it?.trend) ? it.trend : []) {
      if (!p || !p.date) continue
      const m = num(p.mastery)
      if (!Number.isFinite(m)) continue // null 快照：不参与均值
      if (!byDate.has(p.date)) byDate.set(p.date, [])
      byDate.get(p.date).push(m)
    }
  }
  return [...byDate.entries()]
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
    .map(([date, arr]) => ({
      date,
      v: Math.round((arr.reduce((s, x) => s + x, 0) / arr.length) * 1000) / 10,
    }))
})

function renderTrend() {
  if (trendPoints.value.length < 2) return
  trendChart.render({
    animation: false,
    grid: { left: 34, right: 12, top: 14, bottom: 24 },
    tooltip: { trigger: 'axis', confine: true, valueFormatter: (v) => `${v}%` },
    xAxis: {
      type: 'category',
      data: trendPoints.value.map((p) => p.date.slice(5)), // MM-DD
      axisLabel: { color: pal.ink3, fontSize: 10 },
      axisLine: { lineStyle: { color: pal.line } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', min: 0, max: 100,
      axisLabel: { color: pal.ink3, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: pal.line } },
    },
    series: [
      {
        name: '每日平均掌握度',
        type: 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: pal.brand, width: 2 },
        itemStyle: { color: pal.brand },
        areaStyle: { color: hexA(pal.brand, 0.08) },
        data: trendPoints.value.map((p) => p.v),
      },
    ],
  })
}

watch(trendOpen, (v) => {
  if (v) nextTick(renderTrend)
})
watch(items, () => nextTick(renderMain))

const computedAtText = computed(() => {
  const iso = props.card?.generated_at
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

nextTick(renderMain)

function hexA(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}
</script>

<style scoped>
.mbc-card {
  margin-top: 10px; padding: 10px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-subtle, #f8fafc);
}
.mbc-head { display: flex; align-items: center; gap: 8px; padding: 0 2px 8px; }
.mbc-title { font-size: 13px; font-weight: 700; color: var(--ink, #1e293b); }
.mbc-pill {
  font-size: 10.5px; color: var(--ink3, #94a3b8);
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px;
}
.mbc-pill.hot { color: var(--primary, #4f46e5); border-color: var(--primary-border, #c7d2fe); }
.mbc-degraded, .mbc-accum {
  margin: 0 2px 8px; font-size: 12px; border-radius: var(--radius-sm, 6px); padding: 5px 9px;
}
.mbc-msg { margin: 0 2px 8px; font-size: 12px; color: var(--ink2, #334155); line-height: 1.6; }
.mbc-degraded {
  color: #92400e; background: var(--warn-bg, #fffbeb); border: 1px solid var(--warn-border, #fde68a);
}
.mbc-accum {
  color: var(--ink3, #94a3b8); background: var(--bg-white, #fff); border: 1px dashed var(--border);
}

/* 空态（对齐 VideoCardsCard vc-empty 虚线框语言） */
.mbc-empty {
  padding: 18px 10px; text-align: center;
  border: 1px dashed var(--border); border-radius: var(--radius-sm, 6px);
}
.mbc-empty-icon { font-size: 22px; margin-bottom: 6px; }
.mbc-empty-text { font-size: 12.5px; color: var(--ink3, #94a3b8); line-height: 1.7; }
.mbc-empty-btn {
  margin-top: 10px; padding: 6px 16px; border: none; border-radius: 999px; cursor: pointer;
  background: linear-gradient(135deg, var(--primary, #5b5bd6) 0%, #7c7ce0 100%);
  color: #fff; font-size: 12.5px; font-weight: 600;
}
.mbc-empty-btn:hover { filter: brightness(1.06); }

.mbc-nums {
  display: flex; gap: 6px; flex-wrap: wrap; position: relative;
  padding: 8px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-white, #fff); margin-bottom: 8px;
}
.mbc-num { flex: 1; min-width: 90px; display: flex; flex-direction: column; gap: 2px; }
.mbc-num b { font-size: 14px; font-weight: 800; color: var(--ink, #0f172a); font-family: var(--font-num, monospace); }
.mbc-num b.is-ok { color: var(--ok, #059669); }
.mbc-num b.is-warn { color: var(--warn, #d97706); }
.mbc-num span { font-size: 10.5px; color: var(--ink3, #94a3b8); }

.mbc-chart { width: 100%; }
.mbc-deltas { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 6px; padding: 0 2px; }
.mbc-delta {
  font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
}
.mbc-delta.up { color: var(--ok-deep, #047857); background: var(--ok-bg, #ecfdf5); }
.mbc-delta.down { color: var(--err-deep, #991b1b); background: var(--err-bg, #fef2f2); }
.mbc-delta-note { font-size: 10.5px; color: var(--ink3, #94a3b8); }

.mbc-toggle {
  width: 100%; margin-top: 6px; padding: 5px 0;
  border: none; background: transparent; cursor: pointer;
  font-size: 11.5px; color: var(--primary, #4f46e5);
}
.mbc-toggle:hover { text-decoration: underline; }
.mbc-trend { width: 100%; height: 140px; }
.mbc-notrend {
  margin-top: 6px; padding: 8px 10px; text-align: center;
  font-size: 11.5px; color: var(--ink3, #94a3b8);
  border: 1px dashed var(--border); border-radius: var(--radius-sm, 6px);
}
.mbc-foot { margin-top: 6px; font-size: 10.5px; color: var(--ink3, #94a3b8); }
</style>
