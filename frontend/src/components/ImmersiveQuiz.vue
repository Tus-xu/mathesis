<template>
  <!-- 沉浸式答题覆盖层（v4 视觉；由 PracticeView 以 v-model:open + props 供数，兼容 App 根部全局挂载的旧入口） -->
  <div v-if="visible" class="immersive-overlay">
    <div class="immersive-top">
      <div class="timer"><span class="ic">⏱</span> {{ mmss }}</div>
      <div class="progress-strip">
        <div
          v-for="(p, i) in items" :key="p.item_no"
          class="pip"
          :class="{ done: !!answers[p.item_no], current: i === idx && phase === 'quiz' }"
        ></div>
      </div>
      <div class="focus-modes">
        <button v-for="m in modes" :key="m" :class="{ active: mode === m }" @click="mode = m">{{ m }}</button>
      </div>
      <button class="exit-btn" @click="onExit">× 退出 (ESC)</button>
    </div>

    <!-- 无数据空态（旧全局入口无供数时不崩页） -->
    <div v-if="!items.length" class="immersive-body">
      <div class="immersive-content" style="text-align:center;color:var(--ink3);font-size:14px;line-height:2;">
        <div style="font-size:40px;">🧘</div>
        <div>暂无沉浸训练题组</div>
        <div style="font-size:12.5px;">请从「练题中心」点击「开始沉浸训练」进入</div>
      </div>
    </div>

    <!-- 作答区 -->
    <div v-else-if="phase === 'quiz'" class="immersive-body">
      <div class="immersive-content">
        <div class="q-head">
          <div class="lbl">
            <span>第 {{ idx + 1 }} / {{ items.length }} 题</span>
            <span :style="{ color: 'var(--warn-deep)' }">● {{ diffZh(q.difficulty) }}</span>
            <span v-if="q.kp_name">{{ q.kp_name }}</span>
          </div>
          <span class="badge" v-if="smartScore !== null">SmartScore {{ smartScore }} / 100</span>
        </div>
        <div class="q-text">
          <MarkdownView :text="q.text" mode="question" />
        </div>
        <div v-if="q.image && q.image.length" class="q-fig">
          <DynamicFigureViewer :items="q.image" :label="'题目配图'" :height="280" />
        </div>
        <div class="ans-list">
          <div
            v-for="(opt, oi) in q.options" :key="oi"
            class="ans"
            :class="optClass(oi)"
            @click="pick(oi)"
          >
            <span class="ltr">{{ opt.letter }}</span>
            <MarkdownView :text="opt.text" mode="option" :zoomable="false" />
          </div>
        </div>
        <div
          v-if="cur"
          class="feedback"
          :class="cur.verdict === 'correct' ? 'ok' : 'err'"
          style="margin-top:18px;padding:12px 16px;border-radius:10px;font-size:13.5px;line-height:1.7;"
        >
          <span class="em">{{ cur.verdict === 'correct' ? '✓' : '✗' }}</span>
          <b>{{ cur.verdict === 'correct' ? ' 答对了！继续保持。' : ' 差一点，这题答错了。' }}</b>
          {{ cur.verdict === 'correct' ? '' : '已自动收录进错题本，可在错题本中查看解析。' }}
        </div>
      </div>
    </div>

    <!-- 结算页 -->
    <div v-else class="immersive-body">
      <div class="immersive-content">
        <div class="q-head">
          <div class="lbl"><span>🎉 本次训练结算</span></div>
          <span class="badge" v-if="smartScore !== null">SmartScore {{ smartScore }} / 100</span>
        </div>
        <div style="display:flex;gap:14px;margin:14px 0 18px;">
          <div class="stat-card">
            <div class="stat-v">{{ correctCount }}/{{ items.length }}</div>
            <div class="stat-l">答对</div>
          </div>
          <div class="stat-card">
            <div class="stat-v">{{ mmss }}</div>
            <div class="stat-l">用时</div>
          </div>
          <div class="stat-card">
            <div class="stat-v">{{ hintUsed }}</div>
            <div class="stat-l">提示次数</div>
          </div>
        </div>

        <div v-if="summaryState === 'loading'" class="sum-box">正在生成训练总结…</div>
        <div v-else-if="summaryState === 'error'" class="sum-box">
          总结生成失败，可稍后在「学情报告」查看掌握度变化。
        </div>
        <template v-else-if="summary">
          <div v-if="summaryAllEmpty" class="sum-box">
            本次训练暂无掌握度对比数据（如首次训练无对比快照），继续练习后会生成变化总结。
          </div>
          <template v-else>
            <div v-if="summary.upgraded?.length" class="sum-box">
              <b style="color:var(--ok-deep);">↑ 升级 {{ summary.upgraded.length }} 个知识点</b>
              <div style="margin-top:4px;">{{ kpLines(summary.upgraded) }}</div>
            </div>
            <div v-if="summary.downgraded?.length" class="sum-box">
              <b style="color:var(--err-deep);">↓ 降级 {{ summary.downgraded.length }} 个知识点</b>
              <div style="margin-top:4px;">{{ kpLines(summary.downgraded) }}</div>
            </div>
            <div v-if="summary.flat?.length" class="sum-box">
              <b>→ 持平 {{ summary.flat.length }} 个知识点</b>
              <div style="margin-top:4px;">{{ kpLines(summary.flat) }}</div>
            </div>
          </template>
          <div class="sum-box" v-if="summary.recommendation">
            💡 <b>下一步推荐：</b>{{ summary.recommendation }}
          </div>
        </template>
        <div class="sum-box" style="color:var(--ink3);">答错的题目已自动收录进错题本，记得按时复习。</div>
      </div>
    </div>

    <div class="immersive-foot">
      <button class="nav-btn" :disabled="idx === 0 || phase !== 'quiz'" @click="prev">← 上一题</button>
      <div class="mid">
        <button v-if="phase === 'quiz'" :disabled="!items.length" @click="hint">💡 提示 ({{ hintUsed }}/3)</button>
      </div>
      <template v-if="phase === 'quiz' && items.length">
        <button v-if="idx < items.length - 1" class="nav-btn primary" :disabled="!cur" @click="next">下一题 → (Enter)</button>
        <button v-else class="nav-btn primary" :disabled="!allAnswered" @click="finish">完成 🎉</button>
      </template>
      <button v-else-if="phase === 'summary'" class="nav-btn primary" @click="closeAfterFinish">返回练习页</button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import MarkdownView from '@/components/MarkdownView.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'
import { closeImmersive, immersiveOpen } from '@/composables/useImmersive'
import { useConfirm } from '@/composables/useConfirm'
import { useToastStore } from '@/stores/toast'

const props = defineProps({
  /** v-model:open —— 父级（PracticeView）控制开关 */
  open: { type: Boolean, default: false },
  /** 题目：[{ item_no, question_text, options(数组或{A:文本}映射), difficulty, kp_code, kp_name }] */
  questions: { type: Array, default: () => [] },
  /** 题组 id（practice/start 返回的 quiz_id，提交/总结都靠它） */
  quizId: { type: String, default: '' },
})
const emit = defineEmits(['update:open', 'exit', 'finish'])

const toast = useToastStore()
const { confirm } = useConfirm()

/* App.vue 根部全局挂载 + DialogView openImmersive() 的旧入口兼容：无 v-model 供数时也能开（走空态） */
const visible = computed(() => props.open || immersiveOpen.value)

const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F']
const modes = ['🎧 雨声', '🌙 深夜', '☀️ 晨光']
const mode = ref('🌙 深夜')

/* ==================== 题目规范化 ==================== */
function normalizeOptions(options) {
  if (Array.isArray(options)) {
    return options.map((o, i) =>
      o && typeof o === 'object'
        ? { letter: o.letter || LETTERS[i], text: String(o.text ?? '') }
        : { letter: LETTERS[i], text: String(o) }
    )
  }
  if (options && typeof options === 'object') {
    return Object.keys(options).sort().map((k) => ({ letter: k, text: String(options[k]) }))
  }
  return []
}

const items = computed(() =>
  (props.questions || []).map((it, i) => ({
    item_no: it.item_no ?? i + 1,
    difficulty: it.difficulty || 'medium',
    kp_code: it.kp_code || '',
    kp_name: it.kp_name || '',
    text: String(it.question_text ?? it.text ?? ''),
    image: Array.isArray(it.image) ? it.image : [],
    options: normalizeOptions(it.options ?? it.optionsArr),
  }))
)

/* ==================== 作答状态 ==================== */
const idx = ref(0)
const answers = ref({}) // item_no -> { chosen, verdict, elapsed }
const submitting = ref(false)
const smartScore = ref(null)
const hintUsed = ref(0)
const hintsPerItem = ref({}) // item_no -> 本题提示次数（随 submit 上报 hint_count）
const phase = ref('quiz') // quiz / summary
const summary = ref(null)
const summaryState = ref('idle') // idle / loading / error / ready

const secs = ref(0)
let timer = null
const mmss = computed(() => `${String(Math.floor(secs.value / 60)).padStart(2, '0')}:${String(secs.value % 60).padStart(2, '0')}`)

const q = computed(() => items.value[idx.value] || { options: [] })
const cur = computed(() => answers.value[q.value.item_no] || null)
const answeredCount = computed(() => Object.keys(answers.value).length)
const correctCount = computed(() => Object.values(answers.value).filter((a) => a.verdict === 'correct').length)
const allAnswered = computed(() => items.value.length > 0 && answeredCount.value >= items.value.length)
const summaryAllEmpty = computed(() => {
  const s = summary.value
  if (!s) return true
  return !s.upgraded?.length && !s.flat?.length && !s.downgraded?.length
})

function pct(v) { return Math.round((Number(v) || 0) * 100) }
function kpLines(list) {
  return (list || []).map((e) => `${e.kp_name || e.kp_code}（${pct(e.from)}%→${pct(e.to)}%）`).join(' · ')
}
function diffZh(d) {
  return { easy: '易', medium: '中', hard: '难' }[d] || d
}
function optClass(oi) {
  const a = cur.value
  if (!a) return {}
  if (oi === a.chosen) return a.verdict === 'correct' ? { correct: true } : { wrong: true }
  return {}
}

/* ==================== 逐题提交判分（client_submit_id 幂等，形态与 PracticeView 一致） ==================== */
let qStartAt = Date.now()

async function pick(oi) {
  const item = q.value
  if (!item.item_no || cur.value || submitting.value || phase.value !== 'quiz') return
  const opt = item.options[oi]
  if (!opt) return
  submitting.value = true
  try {
    const data = await api.post('/student/practice/submit', {
      quiz_id: props.quizId,
      client_submit_id: `${props.quizId}:${item.item_no}`,
      items: [{
        item_no: item.item_no,
        q_type: 'choice',
        answer_text: opt.letter,
        kp_code: item.kp_code || undefined,
        hint_count: hintsPerItem.value[item.item_no] || 0,
      }],
    })
    const r = (data.results || []).find((x) => x.item_no === item.item_no) || {}
    answers.value = {
      ...answers.value,
      [item.item_no]: {
        chosen: oi,
        verdict: r.verdict === 'correct' ? 'correct' : 'wrong',
        elapsed: Math.max(1, Math.round((Date.now() - qStartAt) / 1000)),
      },
    }
    if (data.submission_id) loadSmartScore(data.submission_id)
  } catch (e) {
    toast.error(e.message || '判分失败，请重试')
  } finally {
    submitting.value = false
  }
}

async function loadSmartScore(submissionId) {
  try {
    const d = await api.get('/student/practice/smart-score', { submission_id: submissionId })
    smartScore.value = d.smart_score
  } catch {
    // submission_id 无效（404）等场景：按契约不展示计分角标
    smartScore.value = null
  }
}

/* ==================== 提示埋点（learning-events，喂独立解题率/诚实提示） ==================== */
async function hint() {
  if (hintUsed.value >= 3) { toast.info('提示次数已用完（3/3）'); return }
  hintUsed.value++
  const item = q.value
  if (item.item_no) {
    hintsPerItem.value = { ...hintsPerItem.value, [item.item_no]: (hintsPerItem.value[item.item_no] || 0) + 1 }
  }
  reportHintEvent(item)
  toast.info(`💡 提示 (${hintUsed.value}/3)：先圈出题目关键条件，想想它对应哪个公式或定义，再动笔。`)
}

async function reportHintEvent(item) {
  // 埋点 fail-open：任何失败都不影响作答主链路
  try {
    await api.post('/student/learning-events', {
      kind: 'hint_used',
      question_text: String(item.text || '').slice(0, 2000),
      kp_code: item.kp_code || undefined,
      kp_name: item.kp_name || undefined,
      source: 'practice',
      correct: true,
    })
  } catch {
    // 旧版后端 learning-events 仅收 quiz_judge：回退通用埋点端点（同一 events 表，诚实提示口径可读）
    try {
      await api.post('/ops/events', {
        events: [{
          event: 'hint_used',
          props: { quiz_id: props.quizId, item_no: item.item_no, kp_code: item.kp_code || '', source: 'immersive_practice' },
        }],
      })
    } catch { /* 埋点失败静默 */ }
  }
}

/* ==================== 导航 / 完成 / 退出 ==================== */
function next() {
  if (idx.value < items.value.length - 1) {
    idx.value++
    qStartAt = Date.now()
  }
}
function prev() {
  if (idx.value > 0) idx.value--
}

async function finish() {
  if (!allAnswered.value || !props.quizId) return
  phase.value = 'summary'
  stopTimer()
  summaryState.value = 'loading'
  try {
    summary.value = await api.get('/student/practice/summary', { quiz_id: props.quizId })
    summaryState.value = 'ready'
    toast.success(`本次 ${items.value.length} 题完成！错题已自动收录错题本 🎉`)
  } catch (e) {
    summaryState.value = 'error'
    toast.error(e.message || '总结生成失败')
  }
}

async function onExit() {
  // 结算页或尚未作答：直接退出；作答中途：二次确认
  if (phase.value === 'summary') { closeAfterFinish(); return }
  if (answeredCount.value > 0) {
    const ok = await confirm({
      title: '退出沉浸训练',
      message: `已答 ${answeredCount.value}/${items.value.length} 题，确认退出？已提交的判分结果会保留。`,
      confirmText: '退出',
      cancelText: '继续作答',
    })
    if (!ok) return
  }
  emit('exit', answeredCount.value)
  doClose()
  toast.info('已退出沉浸模式，进度已自动保存')
}

function closeAfterFinish() {
  emit('finish', summary.value)
  doClose()
}

function doClose() {
  closeImmersive() // 兼容旧全局入口
  emit('update:open', false)
}

/* ==================== 计时 / 键盘 ==================== */
function startTimer() {
  stopTimer()
  timer = setInterval(() => { secs.value++ }, 1000)
}
function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}
function onKey(e) {
  if (!visible.value) return
  if (e.key === 'Escape') onExit()
  if (e.key === 'Enter' && phase.value === 'quiz' && cur.value) {
    if (idx.value < items.value.length - 1) next()
    else if (allAnswered.value) finish()
  }
}

function resetSession() {
  idx.value = 0
  answers.value = {}
  hintsPerItem.value = {}
  smartScore.value = null
  hintUsed.value = 0
  phase.value = 'quiz'
  summary.value = null
  summaryState.value = 'idle'
  secs.value = 0
  qStartAt = Date.now()
}

watch(visible, (v) => {
  if (v) {
    resetSession()
    startTimer()
    document.addEventListener('keydown', onKey)
  } else {
    stopTimer()
    document.removeEventListener('keydown', onKey)
  }
})
onBeforeUnmount(() => {
  stopTimer()
  document.removeEventListener('keydown', onKey)
})
</script>

<style scoped>
.feedback.ok { background: var(--ok-bg); border: 1px solid var(--ok-border); color: var(--ok-deep); }
.feedback.err { background: var(--err-bg); border: 1px solid var(--err-border); color: var(--err-deep); }
.stat-card {
  flex: 1;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 14px 10px;
  text-align: center;
}
.stat-card .stat-v { font-family: var(--font-num); font-size: 22px; font-weight: 900; color: var(--ink); }
.stat-card .stat-l { font-size: 11.5px; color: var(--ink3); margin-top: 4px; }
.sum-box {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 12px 14px;
  font-size: 13px;
  color: var(--ink);
  line-height: 1.7;
  margin-bottom: 10px;
}
.q-fig { margin: 10px 0; text-align: center; }
.q-fig img { max-width: 100%; max-height: 260px; border: 1px solid var(--line); border-radius: 8px; background: #fff; }
</style>
