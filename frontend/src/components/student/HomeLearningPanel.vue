<template>
  <aside class="lp" aria-label="学情脉搏">
    <div class="lp-glow" aria-hidden="true"></div>
    <header class="lp-head">
      <div class="lp-title-wrap">
        <span class="lp-live" aria-hidden="true"></span>
        <span class="lp-title">学情脉搏</span>
        <span class="lp-algo" title="BKT 估算知识掌握概率，FSRS 计算记忆保持率与复习时间">
          BKT <i>·</i> FSRS
        </span>
      </div>
      <button type="button" class="lp-refresh" :disabled="pulse.loading" title="刷新学情" @click="$emit('refresh')">
        ↻
      </button>
    </header>

    <div v-if="pulse.loading" class="lp-skeleton" aria-busy="true">
      <div class="sk-ring"></div>
      <div class="sk-lines"><i></i><i></i><i></i></div>
    </div>

    <template v-else-if="pulse.hasData">
      <div class="lp-primary">
        <div class="lp-ring" :class="{ 'is-empty': masteryPct === null }">
          <svg viewBox="0 0 84 84" aria-hidden="true">
            <circle class="lp-ring-track" cx="42" cy="42" r="35" />
            <circle
              class="lp-ring-fill" cx="42" cy="42" r="35"
              :stroke-dasharray="`${ringDash} 220`"
            />
          </svg>
          <div class="lp-ring-copy">
            <b>{{ masteryPct === null ? '--' : masteryPct }}</b>
            <span v-if="masteryPct !== null">%</span>
          </div>
        </div>
        <div class="lp-copy">
          <div class="lp-kicker">BKT 掌握 · {{ pulse.masteredCount }}/{{ pulse.totalKpCount || '--' }} 考点</div>
          <div class="lp-now">
            <template v-if="pulse.dueCount > 0">今天有 <b>{{ pulse.dueCount }}</b> 道题到复习期</template>
            <template v-else-if="pulse.topWeak">下一步先补「{{ pulse.topWeak.kp_name }}」</template>
            <template v-else>今天从一道题开始</template>
          </div>
          <div class="lp-reason">
            <template v-if="pulse.topDue">
              FSRS 预计「{{ pulse.topDue.kp_name || '到期错题' }}」保持率
              <b>{{ duePct(pulse.topDue.retrievability) }}</b>，越晚越容易忘
            </template>
            <template v-else-if="deltaText">{{ deltaText }}</template>
            <template v-else>完成练习后，这里会实时更新掌握概率</template>
          </div>
          <button type="button" class="lp-cta" @click="onPrimary">
            {{ pulse.dueCount > 0 ? '开始复习' : '开始今日练习' }}
            <span aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      <div class="lp-stats">
        <span><b>{{ pulse.streakDays }}</b> 天连击</span>
        <span><b>{{ pulse.weekAnswered }}</b> 题本周</span>
        <span v-if="pulse.weekAccuracy !== null"><b>{{ pulse.weekAccuracy }}%</b> 正确率</span>
      </div>

      <section v-if="pulse.weakest.length" class="lp-section">
        <div class="lp-section-head">
          <span>薄弱点</span>
          <span class="lp-section-note">BKT 掌握概率</span>
        </div>
        <button
          v-for="w in pulse.weakest.slice(0, 2)" :key="w.kp_code || w.kp_name"
          type="button" class="lp-weak"
          :title="w.ai_reason || '查看该知识点的练习建议'"
          @click="$emit('ask', { text: weakAsk(w), skill: 'quiz_gen' })"
        >
          <span class="lp-weak-name">{{ w.kp_name || w.kp_code }}</span>
          <span class="lp-bar"><i :style="{ width: pct(w.mastery) }"></i></span>
          <span class="lp-weak-pct">{{ pct(w.mastery) }}</span>
        </button>
      </section>

      <section v-if="pulse.topDue" class="lp-section lp-section--due">
        <div class="lp-section-head">
          <span>FSRS 复习队列</span>
          <span class="lp-section-note">保持率实时计算</span>
        </div>
        <button type="button" class="lp-due" @click="$emit('review', pulse.topDue.source)">
          <span class="lp-due-r" :class="riskClass(pulse.topDue.retrievability)">
            {{ duePct(pulse.topDue.retrievability) }}
          </span>
          <span class="lp-due-text">{{ pulse.topDue.preview }}</span>
          <span class="lp-due-go">复习</span>
        </button>
      </section>

      <footer class="lp-foot">
        <span>先做最该做的事，再做想做的事</span>
        <button type="button" @click="router.push('/me?tab=report')">完整学情 →</button>
      </footer>
    </template>

    <div v-else class="lp-empty">
      <div class="lp-empty-title">还没有足够的学情数据</div>
      <p>做几道题、收录一道错题后，这里会显示 BKT 掌握概率与 FSRS 复习队列。</p>
      <button type="button" class="lp-cta" @click="router.push('/practice')">开始一次诊断练习 <span>→</span></button>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

const props = defineProps({
  pulse: { type: Object, required: true },
})
const emit = defineEmits(['review', 'ask', 'refresh'])
const router = useRouter()

const masteryPct = computed(() => {
  const total = Number(props.pulse.totalKpCount) || 0
  if (total > 0) {
    return Math.min(100, Math.round(((Number(props.pulse.masteredCount) || 0) / total) * 100))
  }
  const rows = props.pulse.mastery?.radar || []
  if (!rows.length) return null
  const avg = rows.reduce((sum, r) => sum + (Number(r.mastery) || 0), 0) / rows.length
  return Math.round(avg * 100)
})
const ringDash = computed(() => ((masteryPct.value ?? 0) / 100) * 219.9)
const deltaText = computed(() => {
  const d = props.pulse.delta7d
  if (d === null || d === undefined) return ''
  const v = Math.round(d * 1000) / 10
  if (v > 0) return `较 7 天前 +${v}%，继续保持`
  if (v < 0) return `较 7 天前 ${v}%，先把到期复习清掉`
  return '掌握度与 7 天前持平'
})

function pct(v) {
  const n = Number(v)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '--'
}
function duePct(v) {
  const n = Number(v)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '--'
}
function riskClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return ''
  if (n < 0.6) return 'is-err'
  if (n < 0.8) return 'is-warn'
  return 'is-ok'
}
function weakAsk(w) {
  return `请针对「${w.kp_name || w.kp_code}」先诊断我的薄弱环节，再出 3 道难度递进的变式题带我巩固。`
}
function onPrimary() {
  if (props.pulse.topDue?.source) {
    emit('review', props.pulse.topDue.source)
    return
  }
  if (props.pulse.topWeak) {
    emit('ask', { text: weakAsk(props.pulse.topWeak), skill: 'quiz_gen' })
    return
  }
  router.push('/practice')
}
</script>

<style scoped>
.lp {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 16px 16px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-xl);
  background:
    linear-gradient(180deg, rgba(238, 242, 255, .72), rgba(255, 255, 255, .96) 42%),
    var(--card);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  text-align: left;
}
.lp-glow {
  position: absolute;
  right: -48px;
  top: -60px;
  width: 180px;
  height: 180px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(79, 70, 229, .14), transparent 68%);
  pointer-events: none;
}
.lp-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 12px; }
.lp-title-wrap { display: flex; align-items: center; gap: 7px; min-width: 0; }
.lp-live {
  width: 7px; height: 7px; border-radius: 50%; flex: none;
  background: var(--ok); box-shadow: 0 0 0 3px var(--ok-bg);
}
.lp-title { font-size: 14px; font-weight: 800; letter-spacing: .02em; color: var(--ink); }
.lp-algo {
  font-size: 9.5px; font-weight: 800; letter-spacing: .04em;
  padding: 2px 7px; border-radius: 999px;
  color: var(--brand-deep); background: var(--brand-soft); border: 1px solid var(--primary-border);
  white-space: nowrap;
}
.lp-algo i { font-style: normal; opacity: .55; margin: 0 1px; }
.lp-refresh {
  width: 26px; height: 26px; border-radius: 8px; border: 1px solid var(--line);
  background: rgba(255,255,255,.7); color: var(--ink3); cursor: pointer; font-size: 14px;
}
.lp-refresh:hover { color: var(--brand); border-color: var(--primary-border); }
.lp-refresh:disabled { opacity: .5; cursor: wait; }

.lp-primary { display: flex; align-items: center; gap: 14px; }
.lp-ring { position: relative; width: 78px; height: 78px; flex: none; }
.lp-ring svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.lp-ring circle { fill: none; stroke-width: 7; stroke-linecap: round; }
.lp-ring-track { stroke: var(--brand-soft); }
.lp-ring-fill { stroke: var(--brand); transition: stroke-dasharray .6s ease; }
.lp-ring-copy {
  position: absolute; inset: 0; display: flex; align-items: baseline; justify-content: center; gap: 1px;
  color: var(--ink); font-family: var(--font-num);
}
.lp-ring-copy b { font-size: 24px; font-weight: 900; line-height: 78px; }
.lp-ring-copy span { font-size: 11px; font-weight: 800; color: var(--ink3); }
.lp-ring.is-empty .lp-ring-copy b { color: var(--ink3); }
.lp-copy { flex: 1; min-width: 0; }
.lp-kicker { font-size: 10.5px; font-weight: 700; color: var(--brand-deep); letter-spacing: .02em; }
.lp-now { margin-top: 3px; font-size: 14.5px; font-weight: 800; line-height: 1.35; color: var(--ink); }
.lp-now b { color: var(--brand-deep); font-family: var(--font-num); }
.lp-reason { margin-top: 3px; font-size: 11px; line-height: 1.5; color: var(--ink3); }
.lp-reason b { color: var(--warn-deep); font-family: var(--font-num); }
.lp-cta {
  margin-top: 9px; height: 32px; padding: 0 16px; border: 0; border-radius: 999px;
  background: var(--gradient-brand); color: #fff; font: inherit; font-size: 12.5px; font-weight: 800;
  cursor: pointer; box-shadow: 0 6px 16px -8px rgba(79, 70, 229, .5);
  transition: transform .18s ease, filter .18s ease;
}
.lp-cta:hover { transform: translateY(-1px); filter: brightness(1.04); }
.lp-cta span { margin-left: 3px; }

.lp-stats {
  display: flex; gap: 12px; margin-top: 12px; padding: 8px 2px;
  border-top: 1px dashed var(--line); border-bottom: 1px dashed var(--line);
  color: var(--ink3); font-size: 10.5px;
}
.lp-stats span { display: inline-flex; align-items: baseline; gap: 3px; }
.lp-stats b { font-size: 13px; font-weight: 900; color: var(--ink); font-family: var(--font-num); }

.lp-section { margin-top: 11px; }
.lp-section-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  font-size: 11px; font-weight: 800; color: var(--ink2); margin-bottom: 6px;
}
.lp-section-note { font-size: 9.5px; font-weight: 600; color: var(--ink3); }
.lp-weak {
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 7px;
  border: 1px solid transparent; border-radius: 9px; background: transparent;
  font: inherit; cursor: pointer; text-align: left;
}
.lp-weak:hover { background: rgba(255,255,255,.82); border-color: var(--line); }
.lp-weak-name {
  width: 108px; flex: none; font-size: 11.5px; font-weight: 700; color: var(--ink);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lp-bar { flex: 1; height: 5px; border-radius: 999px; background: var(--bg2); overflow: hidden; }
.lp-bar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--warn), var(--peach)); }
.lp-weak-pct { width: 36px; text-align: right; font-size: 10.5px; font-weight: 800; color: var(--ink2); font-family: var(--font-num); }
.lp-due {
  display: flex; align-items: center; gap: 9px; width: 100%; padding: 8px 9px;
  border: 1px solid var(--line); border-radius: 11px; background: rgba(255,255,255,.86);
  font: inherit; cursor: pointer; text-align: left; transition: border-color .18s ease, box-shadow .18s ease;
}
.lp-due:hover { border-color: var(--primary-border); box-shadow: var(--shadow-sm); }
.lp-due-r {
  flex: none; width: 42px; height: 28px; border-radius: 7px; display: inline-flex;
  align-items: center; justify-content: center; font-size: 11px; font-weight: 900; font-family: var(--font-num);
}
.lp-due-r.is-err { color: var(--err-deep); background: var(--err-bg); border: 1px solid var(--err-border); }
.lp-due-r.is-warn { color: var(--warn-deep); background: var(--warn-bg); border: 1px solid var(--warn-border); }
.lp-due-r.is-ok { color: var(--ok-deep); background: var(--ok-bg); border: 1px solid var(--ok-border); }
.lp-due-text {
  flex: 1; min-width: 0; font-size: 11px; line-height: 1.45; color: var(--ink2);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.lp-due-go { flex: none; font-size: 10.5px; font-weight: 800; color: var(--brand-deep); }

.lp-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin-top: 10px; font-size: 9.5px; color: var(--ink3);
}
.lp-foot button { border: 0; background: none; padding: 0; font: inherit; font-size: 10px; font-weight: 800; color: var(--brand-deep); cursor: pointer; }

.lp-skeleton { display: flex; align-items: center; gap: 14px; padding: 10px 0 16px; }
.sk-ring { width: 78px; height: 78px; border-radius: 50%; background: var(--skeleton-bg); animation: lp-pulse 1.4s ease-in-out infinite; }
.sk-lines { flex: 1; display: grid; gap: 8px; }
.sk-lines i { display: block; height: 10px; border-radius: 6px; background: var(--skeleton-bg); animation: lp-pulse 1.4s ease-in-out infinite; }
.sk-lines i:nth-child(2) { width: 78%; }
.sk-lines i:nth-child(3) { width: 56%; }
@keyframes lp-pulse { 50% { opacity: .55; } }

.lp-empty { padding: 10px 2px 8px; }
.lp-empty-title { font-size: 13.5px; font-weight: 800; color: var(--ink); }
.lp-empty p { margin: 6px 0 12px; font-size: 11.5px; line-height: 1.6; color: var(--ink3); }

@media (max-width: 720px) {
  .lp { padding: 14px; }
  .lp-primary { gap: 11px; }
  .lp-ring { width: 66px; height: 66px; }
  .lp-ring-copy b { line-height: 66px; font-size: 21px; }
  .lp-now { font-size: 13.5px; }
}
</style>
