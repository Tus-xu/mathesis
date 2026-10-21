<template>
  <div class="exam-paper">
    <!-- 加载/错误 -->
    <div v-if="loading" class="card"><div class="muted">试卷加载中…</div></div>
    <div v-else-if="!paper" class="card"><div class="muted">{{ loadError || '试卷不存在' }}</div></div>

    <template v-else>
      <!-- 卷头 -->
      <div class="card head">
        <div class="head-l">
          <button class="link-btn" @click="goBack">← 返回</button>
          <h2>{{ paper.title }}</h2>
          <div class="meta">
            <span>💯 {{ paper.total_score }} 分</span>
            <span>⏱ {{ paper.duration_minutes }} 分钟</span>
            <span v-for="(g, gi) in paper.structure" :key="gi">{{ typeName(g.q_type) }} × {{ g.count }}</span>
            <span v-if="paper.bank_count" class="src-tag real">📚 题库真题 {{ paper.bank_count }}</span>
            <span v-if="paper.ai_count" class="src-tag ai">✨ AI 生成 {{ paper.ai_count }}（已过五闸）</span>
          </div>
        </div>
        <div class="timer" :class="{ warn: remainSec < 300 }">
          {{ submitted ? '已交卷' : fmtTime(remainSec) }}
        </div>
      </div>

      <!-- 试题 -->
      <div v-for="(it, idx) in paper.items" :key="it.item_no" class="card q-card">
        <div class="q-head">
          <span class="q-no">{{ idx + 1 }} / {{ paper.items.length }}</span>
          <span v-if="originNo(it)" class="q-origin-no">原卷第 {{ originNo(it) }} 题</span>
          <span class="q-type">{{ typeName(it.q_type) }}</span>
          <span v-if="it.source" class="src-tag real">📚 {{ it.source }}</span>
          <span v-else-if="it.ai_generated" class="src-tag ai">✨ AI 生成 · 已过五闸</span>
          <span v-if="it.difficulty" class="q-diff">{{ diffZh(it.difficulty) }}</span>
        </div>
        <MarkdownView class="q-stem" :text="it.question_text" />
        <div v-if="it.image && it.image.length" class="q-fig">
          <DynamicFigureViewer :items="it.image" label="题目配图" :height="260" />
        </div>

        <!-- 结果态 -->
        <template v-if="submitted">
          <div v-if="it.expected_answer" class="ans-line"><b>标准答案：</b><MarkdownView :text="String(it.expected_answer)" /></div>
          <div class="verdict" :class="verdictClass(it.verdict)">
            {{ verdictZh(it.verdict) }}<template v-if="it.score != null"> · {{ it.score }} 分</template>
          </div>
          <div v-if="myAnswerText(it.item_no)" class="ans-line muted"><b>我的作答：</b>{{ myAnswerText(it.item_no) }}</div>
          <button v-if="it.verdict === 'wrong'" class="btn btn-sm redo" @click="guideCorrect(it)">🎯 引导订正</button>
        </template>

        <!-- 作答态 -->
        <template v-else>
          <!-- 选择 -->
          <div v-if="it.q_type === 'choice' && it.options" class="opts">
            <button
              v-for="(txt, L) in it.options" :key="L"
              class="opt" :class="{ picked: isPicked(it.item_no, L) }"
              @click="toggleChoice(it.item_no, L)"
            >
              <span class="opt-letter">{{ L }}</span>
              <span class="opt-text"><MarkdownView :text="txt" /></span>
            </button>
            <div class="multi-hint">多选题：点击选项可多选，提交按所选字母组合判分</div>
          </div>
          <!-- 填空 -->
          <input
            v-else-if="it.q_type === 'blank'"
            v-model="answers[it.item_no]"
            class="blank-input"
            :placeholder="'输入答案（如 3、x=2、[0,1)）'"
          />
          <!-- 解答：文本 + 拍照上传（红线 5：大题禁止纯打字是唯一方式） -->
          <template v-else>
            <textarea
              v-model="answers[it.item_no]"
              class="sol-input"
              rows="4"
              placeholder="输入解答过程（支持 LaTeX）；也可拍照上传手写解答"
            ></textarea>
            <div class="photo-row">
              <button class="btn btn-sm" :disabled="sp.busy[it.item_no]" @click="sp.pick(it.item_no)">
                {{ sp.busy[it.item_no] ? (sp.busyText[it.item_no] || '处理中…') : '📷 拍照上传解答' }}
              </button>
              <input :ref="sp.setInput(it.item_no)" type="file" accept="image/*" hidden
                     @change="sp.onPicked(it.item_no, $event, { onText: (t) => appendText(it.item_no, t) })" />
              <span v-if="sp.fileIds[it.item_no]" class="photo-ok">✅ 手写解答已上传（判分时随卷提交）</span>
            </div>
          </template>
        </template>
      </div>

      <!-- 交卷 / 结果汇总 -->
      <div class="card foot">
        <template v-if="!submitted">
          <div class="muted">已答 {{ answeredCount }} / {{ paper.items.length }} 题{{ unAnsweredHint }}</div>
          <button class="btn btn-primary" :disabled="submitting" @click="submit">
            {{ submitting ? '判分中…' : '交卷' }}
          </button>
        </template>
        <template v-else>
          <div class="score-line">
            <b>总分 {{ scoreSum }} / {{ paper.total_score }}</b>
            <span class="muted">错题已自动进入错题本；点各题「🎯 引导订正」逐题弄懂</span>
          </div>
          <button class="btn" @click="goBack">返回试卷列表</button>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { studentApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import MarkdownView from '@/components/MarkdownView.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'
import { useSolutionPhoto } from '@/components/student/useSolutionPhoto'

const route = useRoute()
const router = useRouter()
const toast = useToastStore()
const sp = useSolutionPhoto(toast)

const loading = ref(true)
const loadError = ref('')
const paper = ref(null)
const answers = reactive({})      // item_no -> 字母 / 文本
const submitted = ref(false)
const submitting = ref(false)
const remainSec = ref(0)
let timer = null

const TYPE_ZH = { choice: '选择题', blank: '填空题', solution: '解答题' }
const DIFF_ZH = { easy: '易', medium: '中', hard: '难' }

const typeName = (t) => TYPE_ZH[t] || t
// 真题卷：题干前缀即原卷题号（如 "8. (5分) ..."）
const originNo = (it) => {
  const m = /^\s*(\d{1,2})\s*[.、．]/.exec(it.question_text || "")
  return m ? Number(m[1]) : null
}
const diffZh = (d) => DIFF_ZH[d] || d
const fmtTime = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
const verdictZh = (v) => ({ correct: '✅ 回答正确', partial: '🟡 部分正确', wrong: '❌ 回答错误', pending_review: '⏳ AI/教师批改中' }[v] || v || '—')
const verdictClass = (v) => ({ correct: 'good', partial: 'mid', wrong: 'bad' }[v] || 'mid')

const answeredCount = computed(() =>
  paper.value ? paper.value.items.filter((it) => {
    const a = answers[it.item_no]
    return (typeof a === 'string' && a.trim()) || sp.fileIds[it.item_no]
  }).length : 0)
const unAnsweredHint = computed(() => {
  if (!paper.value) return ''
  const left = paper.value.items.length - answeredCount.value
  return left > 0 ? `（还有 ${left} 题未作答）` : '（全部作答）'
})
const scoreSum = computed(() => {
  if (!paper.value) return 0
  return Math.round(paper.value.items.reduce((s, it) => s + (it.score || 0), 0) * 10) / 10
})

// om5：选择题多选可切换——新高考含多选题，提交按字母序拼接（"ACD"），判分归一排序比对
const isPicked = (no, L) => {
  const cur = String(answers[no] || '')
  return cur.includes(L)
}
function toggleChoice(no, L) {
  const cur = String(answers[no] || '')
  const set = new Set(cur.split(''))
  if (set.has(L)) set.delete(L)
  else set.add(L)
  answers[no] = [...set].sort().join('')
}

const myAnswerText = (no) => {
  const a = answers[no]
  if (typeof a === 'string' && a.trim()) return a
  return sp.fileIds[no] ? '（拍照上传的手写解答）' : ''
}

function appendText(no, text) {
  answers[no] = ((answers[no] || '') + (answers[no] ? '\n' : '') + text).trim()
}

function goBack() {
  router.push('/exam')
}

function guideCorrect(it) {
  const q = new URLSearchParams()
  q.set('explain', it.question_text || '')
  if (it.expected_answer) q.set('answer', `我的作答：${myAnswerText(it.item_no) || '（见试卷）'}；标准答案：${it.expected_answer}`)
  if (it.kp_code) q.set('kp', it.kp_code)
  router.push('/dialog?' + q.toString())
}

function startTimer() {
  remainSec.value = (paper.value?.duration_minutes || 45) * 60
  timer = setInterval(() => {
    if (submitted.value) return clearInterval(timer)
    remainSec.value = Math.max(0, remainSec.value - 1)
    if (remainSec.value === 0) {
      toast.info('考试时间到，自动交卷')
      submit()
    }
  }, 1000)
}

async function submit() {
  if (submitting.value || !paper.value) return
  submitting.value = true
  try {
    const items = paper.value.items.map((it) => {
      const row = { item_no: it.item_no, q_type: it.q_type }
      const a = answers[it.item_no]
      if (typeof a === 'string' && a.trim()) row.answer_text = a.trim()
      if (sp.fileIds[it.item_no]) row.file_id = sp.fileIds[it.item_no]
      return row
    })
    // api 客户端已解包 data 且非 0 code 会抛 ApiError——这里直接拿业务对象
    const res = await studentApi.practiceSubmit({
      quiz_id: paper.value.exam_id,
      items,
      client_submit_id: crypto.randomUUID?.() || String(Date.now()),
    })
    // 用返回判分回填到卷面
    const byNo = {}
    for (const r of res?.results || []) byNo[r.item_no] = r
    for (const it of paper.value.items) {
      const r = byNo[it.item_no]
      if (r) { it.verdict = r.verdict; it.score = r.score }
    }
    // 拉标准答案（detail 在已提交态返回 expected_answer）
    const det = await studentApi.examDetail(paper.value.exam_id)
    const m = {}
    for (const it of det?.items || []) m[it.item_no] = it
    for (const it of paper.value.items) {
      if (m[it.item_no]?.expected_answer) it.expected_answer = m[it.item_no].expected_answer
      if (m[it.item_no]?.verdict && it.verdict == null) { it.verdict = m[it.item_no].verdict; it.score = m[it.item_no].score }
    }
    submitted.value = true
    clearInterval(timer)
    toast.success(`交卷成功：${scoreSum.value} / ${paper.value.total_score}`)
  } catch (e) {
    toast.error(e?.message || '交卷失败，请重试')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  const id = route.params.id
  try {
    // api 客户端已解包 data（非 0 code 抛 ApiError，由 catch 统一提示）
    paper.value = await studentApi.examDetail(id)
    if (paper.value.items.some((it) => it.verdict)) submitted.value = true  // 已交卷态回显
    else startTimer()
  } catch (e) {
    loadError.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.exam-paper { max-width: 1080px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
.card { background: var(--bg-card, #fff); border: 1px solid var(--line, #eee2c8); border-radius: 14px; padding: 18px 22px; }
.muted { color: var(--ink3, #9a917d); font-size: 13px; }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.head h2 { margin: 6px 0; font-size: 20px; }
.meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: 12.5px; color: var(--ink2, #6b6353); }
.link-btn { background: none; border: none; color: var(--ink3, #9a917d); cursor: pointer; padding: 0; font-size: 13px; }
.timer { font-variant-numeric: tabular-nums; font-size: 26px; font-weight: 800; color: var(--brand-deep, #b45309); }
.timer.warn { color: #dc2626; animation: blink 1s infinite; }
@keyframes blink { 50% { opacity: 0.45; } }
.q-card { display: flex; flex-direction: column; gap: 10px; }
.q-head { display: flex; align-items: center; gap: 10px; font-size: 12.5px; }
.q-no { font-weight: 800; color: var(--brand-deep, #b45309); }
.q-type { color: var(--ink3, #9a917d); }
.q-diff { color: var(--ink3, #9a917d); }
.src-tag { font-size: 11.5px; padding: 1px 8px; border-radius: 999px; }
.src-tag.real { background: #ecfdf5; color: #047857; }
.src-tag.ai { background: #eff6ff; color: #1d4ed8; }
.q-stem :deep(img) { max-width: 100%; }
.q-fig { margin-top: 4px; }
.opts { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.opt { display: flex; gap: 10px; align-items: flex-start; text-align: left; padding: 10px 12px; border: 1.5px solid var(--line, #eee2c8); border-radius: 10px; background: #fff; cursor: pointer; font-size: 14px; }
.opt:hover { border-color: var(--brand, #f59e0b); }
.opt.picked { border-color: var(--brand, #f59e0b); background: #fffbeb; }
.opt-letter { font-weight: 800; color: var(--brand-deep, #b45309); }
.blank-input, .sol-input { width: 100%; border: 1.5px solid var(--line, #eee2c8); border-radius: 10px; padding: 10px 12px; font-size: 14px; font-family: inherit; }
.sol-input { resize: vertical; }
.photo-row { display: flex; align-items: center; gap: 12px; }
.photo-ok { font-size: 12.5px; color: #047857; }
.ans-line { font-size: 13.5px; line-height: 1.6; }
.verdict { font-size: 14px; font-weight: 700; padding: 6px 10px; border-radius: 8px; width: fit-content; }
.verdict.good { background: #ecfdf5; color: #047857; }
.verdict.mid { background: #fffbeb; color: #b45309; }
.verdict.bad { background: #fef2f2; color: #b91c1c; }
.redo { background: #fff7e6; border-color: var(--brand, #f59e0b); }
.foot { display: flex; justify-content: space-between; align-items: center; gap: 14px; }
.score-line { display: flex; flex-direction: column; gap: 2px; }
.q-origin-no { font-size: 11.5px; color: #047857; background: #ecfdf5; padding: 1px 8px; border-radius: 999px; }
.multi-hint { font-size: 11.5px; color: var(--ink3, #9a917d); }
</style>
