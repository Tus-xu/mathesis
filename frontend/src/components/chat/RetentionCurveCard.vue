<template>
  <!--
    R2 FSRS 记忆保持率卡（card.type === 'fsrs_retention'）：
    - 数据全部由后端 butler.student.fsrs.retention.read 工具对真实 error_records 的
      stability 现算下发（R(t)=(1+t/9S)⁻¹，fsrs.py:40-44），前端不自取数、不自算曲线、
      绝不合成假数据：curve.points 缺失就隐藏图并如实说明。
    - 主图：最多 3 条错题的保持率曲线（R 最低在前）+ 0.85 到期线 / 0.6 衰减线两条
      参考线 + 当前 R 点；副图（可折叠）：稳定度阶梯（按复习次数推导，如实标注）。
    - 空态：empty_reason → 诚实文案 + 「去做几道题」按钮（走 open_page 协议，
      由 MessageBubble 转发 action 事件、DialogView 白名单确认跳转）。
    - 历史回放：envelope.blocks {type:'card', data:<同构>} 经 messageModel 通用通道还原。
  -->
  <div class="rcc-card">
    <div class="rcc-head">
      <span class="rcc-title">📉 记忆保持率 R(t)</span>
      <span class="rcc-pill">FSRS 记忆模型</span>
    </div>

    <div v-if="card.degraded" class="rcc-degraded">⚠️ AI 分析暂不可用，以下为降级结果</div>

    <!-- 后端对真实数据的一句摘要（有数据时展示；空态文案在 emptyText 里） -->
    <div v-else-if="card.message && rawItems.length" class="rcc-msg">{{ card.message }}</div>

    <!-- 空态：后端返回空集 / 数据不足，诚实展示，不画假曲线 -->
    <div v-if="!rawItems.length" class="rcc-empty">
      <div class="rcc-empty-icon">🌱</div>
      <div class="rcc-empty-text">{{ emptyText }}</div>
      <button v-if="emptyAction" class="rcc-empty-btn" @click="$emit('act', emptyAction)">
        {{ emptyAction.label || '去做几道题' }}
      </button>
    </div>

    <template v-else>
      <!-- 错题切换 chips（焦点 = 关键数字行与阶梯图的对象） -->
      <div class="rcc-chips">
        <button
          v-for="(it, i) in shownItems"
          :key="it.record_id || i"
          class="rcc-chip"
          :class="{ active: i === focusIdx }"
          type="button"
          :title="it.question_preview"
          @click="focusIdx = i"
        >
          <span class="rcc-chip-r" :class="riskCls(it.retrievability)">{{ pct(it.retrievability) }}%</span>
          <span class="rcc-chip-name">{{ it.kp_name || it.kp_code || '错题' }}</span>
        </button>
        <button
          v-if="overflowCount > 0"
          class="rcc-more"
          type="button"
          @click="$emit('act', { kind: 'open_page', to: '/errors', label: '错题本' })"
        >其余 {{ overflowCount }} 条见错题本 →</button>
      </div>

      <!-- 关键数字行：当前 R / 稳定度 S / 距遗忘 / 下次复习 + 记忆等级 -->
      <div class="rcc-nums">
        <div class="rcc-num">
          <b :class="riskCls(focus?.retrievability)">{{ pct(focus?.retrievability) }}%</b>
          <span>当前保持率 R</span>
        </div>
        <div class="rcc-num">
          <b>{{ fmtDays(focus?.stability) }} 天</b>
          <span>记忆稳定度 S</span>
        </div>
        <div class="rcc-num">
          <b>{{ forgetText }}</b>
          <span>距遗忘线 R=0.6</span>
        </div>
        <div class="rcc-num">
          <b>{{ nextReviewText }}</b>
          <span>下次复习</span>
        </div>
        <span class="rcc-level" :class="levelCls(focus?.level)">{{ levelZh(focus?.level) }}</span>
      </div>

      <!-- 算法含义一句话：由 stability_ladder 真实档位推导，无数据不出这句 -->
      <div v-if="liftSentence" class="rcc-sentence">💡 {{ liftSentence }}</div>

      <!-- 主图：R(t) 曲线；curve.points 缺失时如实隐藏（不合成） -->
      <div v-if="hasCurve" ref="mainRef" class="rcc-chart"></div>
      <div v-else class="rcc-nocurve">曲线数据不足，仅显示上方关键数字</div>

      <button class="rcc-toggle" type="button" @click="toggleLadder">
        {{ ladderOpen ? '收起稳定度阶梯 ▲' : '每次复习能把记忆撑多久？▼' }}
      </button>
      <div v-show="ladderOpen" ref="ladderRef" class="rcc-ladder"></div>
      <p v-show="ladderOpen" class="rcc-note">
        阶梯由你的复习次数按 FSRS 经验档位（0.4→1→3→7→15 天）推导，非实测曲线
      </p>

      <div class="rcc-foot">
        来自你的错题本 · FSRS R(t)=(1+t/9S)⁻¹<template v-if="computedAtText"> · 计算于 {{ computedAtText }}</template>
      </div>
      <button v-if="hasCurve" class="rcc-zoom" type="button" @click="openZoom">🔍 放大查看</button>
    </template>

    <!-- 放大 Modal（teleport 到 body，仅放大同一份后端数据，不重新取数） -->
    <teleport to="body">
      <div v-if="zoomOpen" class="rcc-modal" @click.self="closeZoom" @keydown.esc="closeZoom">
        <div class="rcc-modal-box" role="dialog" aria-label="记忆保持率大图">
          <header class="rcc-modal-head">
            <b>记忆保持率 R(t)</b>
            <button class="rcc-modal-close" type="button" aria-label="关闭" @click="closeZoom">×</button>
          </header>
          <div v-if="hasCurve" ref="zoomRef" class="rcc-modal-chart"></div>
          <p class="rcc-note">实线 = 当前稳定度 S 下的遗忘曲线；虚线参考：0.85 到期线、0.6 衰减线</p>
        </div>
      </div>
    </teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { chartPalette, useEcharts } from '@/composables/useEcharts'

const props = defineProps({
  card: { type: Object, required: true }, // {type:'fsrs_retention', items[], empty_reason?, message?, degraded?}
})
defineEmits(['act'])

/* ===== 数据归一（全部防御式：字段缺失不崩，缺数据就诚实少显示） ===== */
const rawItems = computed(() => (Array.isArray(props.card?.items) ? props.card.items : []))
// R 最低（最快忘）在前，最多画 3 条——其余给「见错题本」链接
const shownItems = computed(() =>
  rawItems.value
    .filter((it) => it && typeof it === 'object')
    .slice()
    .sort((a, b) => num(a?.retrievability) - num(b?.retrievability))
    .slice(0, 3)
)
const overflowCount = computed(() => Math.max(0, rawItems.value.length - shownItems.value.length))

const focusIdx = ref(0)
watch(shownItems, () => { focusIdx.value = 0 })
const focus = computed(() => shownItems.value[focusIdx.value] || shownItems.value[0] || null)

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : NaN
}
function pct(r) {
  const n = num(r)
  return Number.isFinite(n) ? Math.round(n * 100) : '--'
}
function fmtDays(d) {
  const n = num(d)
  if (!Number.isFinite(n)) return '--'
  return n >= 10 ? String(Math.round(n)) : String(Math.round(n * 10) / 10)
}

/* ===== 空态 ===== */
const EMPTY_ZH = {
  no_error_records: '还没有错题数据——完成一次练习或复习，这里会出现你的记忆曲线',
  insufficient_history: '数据积累中：再复习几次，你的记忆曲线会更完整',
}
const emptyText = computed(() => props.card?.message || EMPTY_ZH[props.card?.empty_reason] || '暂时没有可展示的记忆数据')
const emptyAction = computed(() => {
  if (props.card?.empty_reason !== 'no_error_records') return null
  return { kind: 'open_page', to: '/practice', label: '去做几道题' }
})

/* ===== 语义色 / 等级（与 ErrorsView.vue:465 既有学生端叫法一致） ===== */
function riskCls(r) {
  const n = num(r)
  if (!Number.isFinite(n)) return ''
  if (n < 0.6) return 'is-err'
  if (n < 0.85) return 'is-warn'
  return 'is-ok'
}
const LEVEL_ZH = { lv4: '非常稳', lv3: '稳定', lv2: '一般', lv1: '偏弱', decay: '快忘了', new: '刚收录' }
function levelZh(lv) {
  return LEVEL_ZH[String(lv || '')] || '待评估'
}
function levelCls(lv) {
  if (lv === 'lv4' || lv === 'lv3') return 'is-ok'
  if (lv === 'lv2' || lv === 'lv1') return 'is-warn'
  return 'is-err'
}

/* ===== 关键文字推导 ===== */
const forgetText = computed(() => {
  if (!focus.value) return '--'
  if (focus.value.is_due) return '已到期'
  const h = num(focus.value.hours_to_forget)
  if (!Number.isFinite(h)) return '--'
  if (h < 48) return `${Math.max(1, Math.round(h))} 小时`
  return `${Math.round(h / 24)} 天`
})
const nextReviewText = computed(() => {
  const iso = focus.value?.next_review_at
  if (!iso) return '待安排'
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(d.getTime())) return '待安排'
  return `${d.getMonth() + 1}/${d.getDate()}`
})
const computedAtText = computed(() => {
  const iso = props.card?.generated_at
  if (!iso) return ''
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? '' : `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
})

/** 稳定度阶梯取值：后端实际放在 curve.stability_ladder（learning_viz_tools.py:96-100），
 *  顶层 stability_ladder 为方案契约旧位——两处兼容，缺就是缺，不合成 */
function ladderOf(it) {
  const c = it?.curve
  if (Array.isArray(c?.stability_ladder) && c.stability_ladder.length) return c.stability_ladder
  if (Array.isArray(it?.stability_ladder)) return it.stability_ladder
  return []
}

/** 「这次复习把稳定度从 X 天提到 Y 天」——只由 stability_ladder 真实档位推导 */
const liftSentence = computed(() => {
  const it = focus.value
  if (!it) return ''
  const ladder = ladderOf(it)
    .filter((s) => s && Number.isFinite(Number(s.stability)))
    .sort((a, b) => (a.review_count || 0) - (b.review_count || 0))
  const rc = Number(it.review_count) || 0
  const cur = ladder.find((s) => Number(s.review_count) === rc)
  const prev = ladder.find((s) => Number(s.review_count) === rc - 1)
  if (cur && prev && rc >= 1) {
    return `最近一次复习把记忆稳定度从 ${fmtDays(prev.stability)} 天提到 ${fmtDays(cur.stability)} 天——S 越大，曲线越平缓，忘得越慢`
  }
  if (rc === 0) {
    const first = ladder.find((s) => Number(s.review_count) === 1)
    if (first) return `这道题还没复习过：第 1 次复习后，稳定度预计达到 ${fmtDays(first.stability)} 天`
  }
  return ''
})

/* ===== 主图：R(t) 曲线（points 全部来自后端现算，前端只做单位换算 0-1 → 0-100） ===== */
const pal = chartPalette()
const hasCurve = computed(() =>
  shownItems.value.some((it) => Array.isArray(it?.curve?.points) && it.curve.points.length > 1)
)

const mainRef = ref(null)
const ladderRef = ref(null)
const zoomRef = ref(null)
const mainChart = useEcharts(mainRef)
const ladderChart = useEcharts(ladderRef)
const zoomChart = useEcharts(zoomRef)

function curveSeriesList() {
  const series = []
  shownItems.value.forEach((it, i) => {
    const pts = Array.isArray(it?.curve?.points) ? it.curve.points : []
    if (pts.length < 2) return
    const isFocus = i === focusIdx.value
    const color = [pal.err, pal.warn, pal.brand2][i] || pal.brand2
    series.push({
      name: it.kp_name || it.kp_code || `错题 ${i + 1}`,
      type: 'line',
      smooth: true,
      symbol: 'none',
      lineStyle: { width: isFocus ? 2.5 : 1.4, color, opacity: isFocus ? 1 : 0.55 },
      areaStyle: isFocus
        ? { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: hexA(color, 0.18) },
            { offset: 1, color: hexA(color, 0.02) },
          ] } }
        : undefined,
      data: pts
        .filter((p) => p && Number.isFinite(Number(p.t)) && Number.isFinite(Number(p.r)))
        .map((p) => [Number(p.t), Math.round(Number(p.r) * 1000) / 10]),
    })
  })
  // 当前 R 点（竖标位置）：单独散点序列，数据同样来自后端字段
  const f = focus.value
  if (f && Number.isFinite(num(f.current_t)) && Number.isFinite(num(f.retrievability))) {
    series.push({
      name: '当前位置',
      type: 'line',
      symbol: 'circle',
      symbolSize: 9,
      lineStyle: { width: 0 },
      itemStyle: { color: pal.brand },
      tooltip: { formatter: () => `当前：第 ${Math.round(num(f.current_t))} 天 · R=${pct(f.retrievability)}%` },
      data: [[Number(f.current_t), Math.round(num(f.retrievability) * 1000) / 10]],
      z: 5,
    })
  }
  return series
}

function baseGridOption(series, { big = false } = {}) {
  const th = shownItems.value[0]?.curve?.thresholds || { due: 0.85, decay: 0.6 }
  const horizon = Math.max(
    7,
    ...shownItems.value.flatMap((it) =>
      (Array.isArray(it?.curve?.points) ? it.curve.points : []).map((p) => Number(p?.t) || 0)
    )
  )
  // 图例只列真实曲线（排除「阈值」辅助序列与单点「当前位置」）
  const legendData = series
    .filter((s) => s.name && s.name !== '阈值' && s.name !== '当前位置')
    .map((s) => s.name)
  return {
    animation: false,
    grid: { left: 34, right: big ? 18 : 10, top: big ? 30 : 14, bottom: big ? 34 : 22 },
    tooltip: {
      trigger: 'item',
      confine: true,
      valueFormatter: (v) => `${v}%`,
    },
    legend: big && legendData.length > 1
      ? { data: legendData, top: 0, left: 'center', icon: 'roundRect', itemWidth: 14, itemHeight: 8, textStyle: { color: pal.ink2, fontSize: 11 } }
      : undefined,
    xAxis: {
      type: 'value',
      min: 0,
      max: horizon,
      name: '天',
      nameTextStyle: { color: pal.ink3, fontSize: 10 },
      axisLabel: { color: pal.ink3, fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: { color: pal.ink3, fontSize: 10, formatter: '{value}%' },
      splitLine: { lineStyle: { color: pal.line } },
    },
    series: [
      ...series,
      {
        name: '阈值', type: 'line', data: [], silent: true,
        markLine: {
          silent: true,
          symbol: 'none',
          animation: false,
          data: [
            { yAxis: Math.round(num(th.due) * 100), lineStyle: { color: pal.warn, type: 'dashed', width: 1 }, label: { formatter: `到期线 ${num(th.due)}`, color: pal.warn, fontSize: 10, position: 'insideEndTop' } },
            { yAxis: Math.round(num(th.decay) * 100), lineStyle: { color: pal.err, type: 'dashed', width: 1 }, label: { formatter: `衰减线 ${num(th.decay)}`, color: pal.err, fontSize: 10, position: 'insideEndBottom' } },
          ],
        },
      },
    ],
  }
}

function renderMain() {
  if (!hasCurve.value) return
  mainChart.render(baseGridOption(curveSeriesList()))
}

/* ===== 副图：稳定度阶梯（stability_ladder，按复习次数推导——卡上如实标注） ===== */
const ladderOpen = ref(false)
function toggleLadder() {
  ladderOpen.value = !ladderOpen.value
  if (ladderOpen.value) nextTick(renderLadder)
}
function renderLadder() {
  const it = focus.value
  const ladder = ladderOf(it)
    .filter((s) => s && Number.isFinite(Number(s.stability)))
    .sort((a, b) => (a.review_count || 0) - (b.review_count || 0))
  if (ladder.length < 2) return // 不足两档不成阶梯（诚实：不画单点假趋势）
  const data = ladder.map((s) => [Number(s.review_count) || 0, Number(s.stability)])
  const last = data[data.length - 1]
  ladderChart.render({
    animation: false,
    grid: { left: 34, right: 12, top: 14, bottom: 22 },
    tooltip: { trigger: 'item', confine: true, valueFormatter: (v) => `${v} 天` },
    xAxis: {
      type: 'value', min: 0, max: Math.max(4, last[0]),
      name: '复习次数', nameTextStyle: { color: pal.ink3, fontSize: 10 },
      axisLabel: { color: pal.ink3, fontSize: 10, formatter: (v) => `${Math.round(v)}` },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value', min: 0,
      axisLabel: { color: pal.ink3, fontSize: 10, formatter: '{value}天' },
      splitLine: { lineStyle: { color: pal.line } },
    },
    series: [
      {
        name: '稳定度 S（天）',
        type: 'line',
        step: 'end',
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { color: pal.brand, width: 2 },
        itemStyle: { color: pal.brand },
        areaStyle: { color: hexA(pal.brand, 0.08) },
        data,
        tooltip: { formatter: (p) => `复习 ${p.value[0]} 次后 · S=${p.value[1]} 天` },
      },
      {
        name: '当前档位',
        type: 'line',
        symbol: 'circle',
        symbolSize: 10,
        lineStyle: { width: 0 },
        itemStyle: { color: pal.ok },
        label: { show: true, formatter: `S=${last[1]}天`, color: pal.ok, fontSize: 10, position: 'top' },
        tooltip: { formatter: () => `当前：复习 ${last[0]} 次 · 稳定度 ${last[1]} 天` },
        data: [last],
        z: 5,
      },
    ],
  })
}

watch([focusIdx, shownItems], () => {
  nextTick(() => {
    renderMain()
    if (ladderOpen.value) renderLadder()
    if (zoomOpen.value) renderZoom()
  })
})

/* ===== 放大 Modal ===== */
const zoomOpen = ref(false)
function openZoom() {
  zoomOpen.value = true
  nextTick(renderZoom)
}
function closeZoom() {
  zoomOpen.value = false
}
function renderZoom() {
  if (!hasCurve.value) return
  zoomChart.render(baseGridOption(curveSeriesList(), { big: true }))
}

/* 首次渲染（含历史回放 / v-show 场景由 onMounted 补刀） */
nextTick(renderMain)

/* 十六进制 → rgba（面积渐变用） */
function hexA(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim())
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`
}
</script>

<style scoped>
.rcc-card {
  margin-top: 10px; padding: 10px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-subtle, #f8fafc);
}
.rcc-head { display: flex; align-items: center; gap: 8px; padding: 0 2px 8px; }
.rcc-title { font-size: 13px; font-weight: 700; color: var(--ink, #1e293b); }
.rcc-pill {
  font-size: 10.5px; color: var(--ink3, #94a3b8);
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px;
}
.rcc-degraded {
  margin: 0 2px 8px; font-size: 12px; color: #92400e;
  background: var(--warn-bg, #fffbeb); border: 1px solid var(--warn-border, #fde68a);
  border-radius: var(--radius-sm, 6px); padding: 5px 9px;
}
.rcc-msg { margin: 0 2px 8px; font-size: 12px; color: var(--ink2, #334155); line-height: 1.6; }

/* 空态（对齐 VideoCardsCard vc-empty 虚线框语言） */
.rcc-empty {
  padding: 18px 10px; text-align: center;
  border: 1px dashed var(--border); border-radius: var(--radius-sm, 6px);
}
.rcc-empty-icon { font-size: 22px; margin-bottom: 6px; }
.rcc-empty-text { font-size: 12.5px; color: var(--ink3, #94a3b8); line-height: 1.7; }
.rcc-empty-btn {
  margin-top: 10px; padding: 6px 16px; border: none; border-radius: 999px; cursor: pointer;
  background: linear-gradient(135deg, var(--primary, #5b5bd6) 0%, #7c7ce0 100%);
  color: #fff; font-size: 12.5px; font-weight: 600;
}
.rcc-empty-btn:hover { filter: brightness(1.06); }

/* 错题切换 chips */
.rcc-chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 0 2px 8px; }
.rcc-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px; border: 1px solid var(--border); border-radius: 999px;
  background: var(--bg-white, #fff); cursor: pointer; max-width: 100%;
}
.rcc-chip.active { border-color: var(--primary-border, #c7d2fe); background: var(--primary-subtle, #eef2ff); }
.rcc-chip-r { font-size: 11px; font-weight: 800; font-family: var(--font-num, monospace); }
.rcc-chip-r.is-err { color: var(--err, #dc2626); }
.rcc-chip-r.is-warn { color: var(--warn, #d97706); }
.rcc-chip-r.is-ok { color: var(--ok, #059669); }
.rcc-chip-name {
  font-size: 11.5px; color: var(--ink2, #334155); max-width: 96px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rcc-more {
  border: none; background: transparent; padding: 2px 4px; cursor: pointer;
  font-size: 11px; color: var(--primary, #4f46e5);
}
.rcc-more:hover { text-decoration: underline; }

/* 关键数字行 */
.rcc-nums {
  display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap;
  padding: 8px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-white, #fff); margin-bottom: 8px; position: relative;
}
.rcc-num { flex: 1; min-width: 72px; display: flex; flex-direction: column; gap: 2px; }
.rcc-num b { font-size: 15px; font-weight: 800; color: var(--ink, #0f172a); font-family: var(--font-num, monospace); }
.rcc-num b.is-err { color: var(--err, #dc2626); }
.rcc-num b.is-warn { color: var(--warn, #d97706); }
.rcc-num b.is-ok { color: var(--ok, #059669); }
.rcc-num span { font-size: 10.5px; color: var(--ink3, #94a3b8); }
.rcc-level {
  position: absolute; top: -8px; right: 8px;
  font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 999px;
}
.rcc-level.is-ok { background: var(--ok-bg, #ecfdf5); color: var(--ok-deep, #047857); border: 1px solid var(--ok-border, #a7f3d0); }
.rcc-level.is-warn { background: var(--warn-bg, #fffbeb); color: var(--warn-deep, #92400e); border: 1px solid var(--warn-border, #fde68a); }
.rcc-level.is-err { background: var(--err-bg, #fef2f2); color: var(--err-deep, #991b1b); border: 1px solid var(--err-border, #fecaca); }

.rcc-sentence {
  font-size: 12px; color: var(--ink2, #334155); line-height: 1.6;
  background: var(--brand-soft, #eef2ff); border-radius: 8px; padding: 6px 9px; margin-bottom: 8px;
}

.rcc-chart { width: 100%; height: 160px; }
.rcc-nocurve {
  padding: 14px 10px; text-align: center; font-size: 12px; color: var(--ink3, #94a3b8);
  border: 1px dashed var(--border); border-radius: var(--radius-sm, 6px);
}
.rcc-toggle {
  width: 100%; margin-top: 6px; padding: 5px 0;
  border: none; background: transparent; cursor: pointer;
  font-size: 11.5px; color: var(--primary, #4f46e5);
}
.rcc-toggle:hover { text-decoration: underline; }
.rcc-ladder { width: 100%; height: 130px; }
.rcc-note { margin: 4px 2px 0; font-size: 10.5px; color: var(--ink3, #94a3b8); line-height: 1.6; }
.rcc-foot { margin-top: 6px; font-size: 10.5px; color: var(--ink3, #94a3b8); }
.rcc-zoom {
  margin-top: 6px; border: none; background: transparent; cursor: pointer; padding: 0;
  font-size: 11px; color: var(--primary, #4f46e5);
}
.rcc-zoom:hover { text-decoration: underline; }

/* 放大 Modal */
.rcc-modal {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.rcc-modal-box {
  width: min(640px, 94vw); background: var(--bg-white, #fff);
  border-radius: 14px; padding: 14px 16px; box-shadow: 0 18px 48px rgba(15, 23, 42, 0.2);
}
.rcc-modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.rcc-modal-head b { font-size: 14px; color: var(--ink, #0f172a); }
.rcc-modal-close {
  width: 26px; height: 26px; border: none; border-radius: 8px; background: transparent;
  color: var(--ink3, #94a3b8); font-size: 16px; cursor: pointer;
}
.rcc-modal-close:hover { background: var(--bg-subtle, #f1f5f9); color: var(--ink, #0f172a); }
.rcc-modal-chart { width: 100%; height: 320px; }
</style>
