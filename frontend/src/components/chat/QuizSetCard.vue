<template>
  <!-- F2 出题卡：card.type==='quiz_set'（契约见 M2 前端指南 §5.7）
       闭环迭代13：对话内直接作答——点选项即时判分 + 自动解析 + 答错收录错题 + 讲解入口
       （选择题在对话内完成闭环；填空/解答题保留「进入作答页」沉浸式判分）
       v1.5 变式链模式（card.chain==='variant'，对齐愿景 03 对话内轻练）：
       逐题子卡（✦ 变式 i/N + 变式方式 + 难度）→ ABCD 点选即时判分（对/错横幅 + 掌握度增减）
       → 进度链（原题 → 变式1 ✓ → 变式2 ✗ → 变式3）→ 全做完收尾横幅 + 四按钮出口 -->
  <div class="quiz-card" :class="{ 'quiz-chain': isChain }">
    <div class="qc-head">
      <span class="qc-title">{{ isChain ? `🔄 举一反三 · 变式链 · ${items.length} 题` : `📝 智能出题 · ${items.length} 题` }}</span>
      <span class="tag gray">AI 生成</span>
      <span v-if="inlineJudge" class="qc-badge">对话内直接作答</span>
    </div>

    <!-- ============ v1.5 变式链模式 ============ -->
    <template v-if="isChain">
      <template v-for="(it, idx) in items" :key="idx">
        <!-- 逐题解锁（对齐愿景 03：做完一道紧接着来下一道）：上一题判分后才出现 -->
        <template v-if="idx === 0 || result[idx - 1]">
        <!-- 过场鼓励语：上一题判分后出现（对齐愿景「答对啦！那来一道… 👇」） -->
        <div v-if="idx > 0 && result[idx - 1]" class="qc-transition">
          {{ result[idx - 1].correct ? '答对啦！下一道难度升半档，继续 👇' : '这个坑记住了，下一道检验一下 👇' }}
        </div>
        <div class="qc-variant">
          <div class="qc-item-head">
            <span class="qc-chain-no">✦ 变式 {{ idx + 1 }}/{{ items.length }}</span>
            <MarkdownView v-if="it.variant_note" class="qc-vnote" :text="texInline(it.variant_note)" :zoomable="false" />
            <span class="tag gray">{{ DIFF_ZH[it.difficulty] || it.difficulty || '' }}</span>
          </div>
          <div v-if="itemFigure(it)" class="qc-figure">
            <MathFigure :figure="it.figure" question-figure />
          </div>
          <div class="qc-stem-box"><MarkdownView :text="tex(it.question_text)" /></div>

          <div class="qc-options grid">
            <div
              v-for="(opt, oi) in it.options"
              :key="oi"
              class="qc-opt"
              :class="optClass(idx, oi)"
              @click="onPick(idx, oi)"
            >
              <span class="qc-opt-key">{{ LETTERS[oi] }}</span>
              <span class="qc-opt-body"><MarkdownView :text="texInline(opt)" /></span>
              <span v-if="result[idx]?.chosen === oi" class="qc-opt-note">
                {{ result[idx].correct ? '✓ 你的答案' : '✗ 你的答案' }}
              </span>
              <span v-else-if="result[idx] && isRightOption(it, oi)" class="qc-opt-note right-note">✓ 正确答案</span>
            </div>
          </div>

          <!-- 判分横幅：对 +1 掌握 / 错 -1 需复习 + 错因入口 -->
          <div v-if="result[idx]" class="qc-judge" :class="result[idx].correct ? 'ok' : 'no'">
            <span class="qc-judge-txt">
              {{ result[idx].correct
                ? '✅ 回答正确！'
                : `⚠ 差一点！正确答案是 ${it.answer}——已收录错题本，会提醒你复习` }}
            </span>
            <button class="qc-explain-btn" @click="emitExplain(it, idx)">
              {{ result[idx].correct ? '💬 再讲一遍思路' : '💬 看讲解' }}
            </button>
            <span class="qc-delta" :class="result[idx].correct ? 'up' : 'down'">
              {{ result[idx].correct ? '+1 掌握' : '-1 需复习' }}
            </span>
          </div>

          <!-- 解析（判分后自动展开） -->
          <details v-if="it.answer || it.answer_analysis" class="qc-answer" :open="!!result[idx]">
            <summary>答案与解析</summary>
            <MarkdownView v-if="it.answer" :text="`**答案：${it.answer}**`" />
            <template v-if="it.answer_analysis">
              <div class="qc-ana-label">解析</div>
              <MarkdownView :text="tex(it.answer_analysis)" />
            </template>
          </details>

          <!-- 进度链：原题 → 变式1 ✓ → 变式2 ✗ → 变式3 -->
          <div class="qc-trail">
            <span class="qc-trail-node origin">原题</span>
            <template v-for="(t, ti) in items" :key="ti">
              <span class="qc-trail-arrow">→</span>
              <span class="qc-trail-node" :class="trailClass(ti)">
                变式{{ ti + 1 }}{{ trailMark(ti) }}
              </span>
            </template>
          </div>
        </div>
        </template>
      </template>

      <!-- 生成中占位：链未出齐时提示进度（v1.6 逐题渐进生成） -->
      <div v-if="!chainComplete" class="qc-pending">
        ⏳ 变式 {{ items.length + 1 }}/{{ chainTotal }} 正在生成…
      </div>

      <!-- 收尾横幅：全链路走通 / 部分正确 -->
      <div v-if="allJudged" class="qc-complete" :class="wrongCount ? 'part' : 'full'">
        <template v-if="!wrongCount">
          🎉 {{ items.length }} 道变式全链路走通！这一类题你已经真正掌握了。
        </template>
        <template v-else>
          💪 变式链完成：{{ items.length }} 对 {{ correctCount }}。错的 {{ wrongCount }} 道已自动收录错题本，会提醒你复习一遍。
        </template>
      </div>

      <!-- 四按钮出口（对齐愿景收尾卡） -->
      <div v-if="allJudged" class="qc-actions">
        <button class="btn btn-primary btn-sm" @click="$emit('chainAction', 'variant')">🔄 再来一组同类型变式</button>
        <button class="btn btn-ghost btn-sm" @click="$emit('chainAction', 'errorBook')">📖 查看错题讲解</button>
        <button class="btn btn-ghost btn-sm" @click="$emit('chainAction', 'mastery')">📈 看看我的学情</button>
        <button class="btn btn-ghost btn-sm" @click="$emit('chainAction', 'finish')">🏁 今天就到这</button>
      </div>
    </template>

    <!-- ============ 普通出题卡（原有渲染，不变） ============ -->
    <template v-else>
      <div v-for="(it, idx) in items" :key="idx" class="qc-item">
        <div class="qc-item-head">
          <span class="qc-no">第 {{ it.item_no ?? idx + 1 }} 题</span>
          <span class="tag">{{ QTYPE_ZH[it.q_type] || it.q_type || '题目' }}</span>
          <span class="tag gray">{{ DIFF_ZH[it.difficulty] || it.difficulty || '' }}</span>
          <span v-if="kpZh(it)" class="tag cyan">{{ kpZh(it) }}</span>
          <span v-if="it.source === 'kb_variant' || it.source === 'user_variant'" class="tag orange" :title="it.variant_note || it.kb_ref || ''">变式题</span>
          <MarkdownView v-if="it.variant_note" class="qc-vnote" :text="texInline(it.variant_note)" :zoomable="false" />
          <span v-if="it.verified === false" class="tag red">未校验</span>
        </div>
        <div v-if="itemFigure(it)" class="qc-figure">
          <MathFigure :figure="it.figure" question-figure />
        </div>
        <div class="qc-stem-box"><MarkdownView :text="tex(it.question_text)" /></div>

        <!-- 选择题：对话内直接作答 -->
        <div v-if="Array.isArray(it.options) && it.options.length" class="qc-options">
          <div
            v-for="(opt, oi) in it.options"
            :key="oi"
            class="qc-opt"
            :class="optClass(idx, oi)"
            @click="onPick(idx, oi)"
          >
            <span class="qc-opt-key">{{ LETTERS[oi] }}</span>
            <span class="qc-opt-body"><MarkdownView :text="texInline(opt)" /></span>
            <span v-if="inlineJudge && result[idx]?.chosen === oi" class="qc-opt-note">
              {{ result[idx].correct ? '✓ 正确' : '✗ 你的答案' }}
            </span>
            <span v-else-if="inlineJudge && result[idx] && isRightOption(it, oi) && !result[idx].correct" class="qc-opt-note right-note">✓ 正确答案</span>
          </div>
        </div>

        <!-- 填空/解答题：对话内直接作答（v1.9：写答案/拍照上传手写解答 → 提交判分，
             与作答页同一 practice/submit 契约；错题后端自动收录） -->
        <div v-else class="qc-longform">
          <textarea
            v-if="(it.q_type || 'solution') === 'solution'"
            class="textarea qc-lf-input"
            rows="4"
            v-model="lfAnswers[idx]"
            :disabled="!!lfResult[idx]"
            placeholder="写出解题步骤，AI 初批按步骤给分；也可拍照上传手写解答自动识别"
          ></textarea>
          <input
            v-else
            class="input qc-lf-input"
            v-model="lfAnswers[idx]"
            :disabled="!!lfResult[idx]"
            placeholder="输入答案（支持分数、根号等表达式）"
          />
          <div class="qc-lf-row">
            <button
              class="btn btn-sm qc-photo-btn"
              :disabled="sp.busy[idx] || !!lfResult[idx]"
              @click="sp.pick(idx)"
            >
              <span v-if="sp.busy[idx]" class="spinner"></span>
              {{ sp.busy[idx] ? sp.busyText[idx] || '处理中…' : '📷 拍照上传解答' }}
            </button>
            <span v-if="sp.fileIds[idx] && !lfResult[idx]" class="tag green">✓ 已附手写照片</span>
            <input
              :ref="sp.setInput(idx)"
              type="file"
              accept="image/jpeg,image/png"
              capture="environment"
              class="qc-photo-input"
              @change="sp.onPicked(idx, $event, { onText: (t) => appendOcrText(idx, t) })"
            />
          </div>
          <!-- 判分结果（AI 初批评语 + 得分 + 错题讲解入口） -->
          <div
            v-if="lfResult[idx]"
            class="qc-judge"
            :class="lfResult[idx].verdict === 'correct' ? 'ok' : 'no'"
          >
            <span class="qc-judge-txt">
              <template v-if="lfResult[idx].verdict === 'correct'">✅ 回答正确！已计入学情</template>
              <!-- solution 契约：AI 初批留痕 verdict=pending_review + score/comment（待教师复核），
                   score 为 null 才是真降级（ocr_pending/llm_unavailable） -->
              <template v-else-if="lfResult[idx].verdict === 'pending_review' && lfResult[idx].score != null">
                🤖 AI 初批完成{{ lfResult[idx].score > 0 ? '' : '，存在错误——已收录错题本' }}（教师可复核）
              </template>
              <template v-else-if="lfResult[idx].verdict === 'pending_review'">⏳ 已提交，AI 初批暂不可用，转人工待批（不影响判分记录）</template>
              <template v-else>✗ 有错误——已自动收录错题本，会提醒你复习</template>
              <span v-if="lfResult[idx].comment" class="qc-lf-comment">{{ lfResult[idx].comment }}</span>
            </span>
            <span v-if="lfResult[idx].score != null" class="qc-delta" :class="lfResult[idx].verdict === 'correct' ? 'up' : 'down'">
              {{ lfResult[idx].score }} 分
            </span>
            <button
              v-if="lfResult[idx].verdict !== 'correct'"
              class="qc-explain-btn"
              @click="emitExplain(it, idx)"
            >💬 讲解这道错题 · 举一反三</button>
          </div>
        </div>

        <!-- 判分结果条（答对/答错反馈 + 错题收录 + 讲解入口） -->
        <div v-if="inlineJudge && result[idx]" class="qc-judge" :class="result[idx].correct ? 'ok' : 'no'">
          <span class="qc-judge-txt">
            {{ result[idx].correct
              ? '✅ 答对了！掌握 +1，已计入学情'
              : '✗ 差一点！已自动收录错题本，明晚提醒复习' }}
          </span>
          <button v-if="!result[idx].correct" class="qc-explain-btn" @click="emitExplain(it, idx)">
            💬 讲解这道错题 · 举一反三
          </button>
          <button v-else class="qc-explain-btn subtle" @click="emitExplain(it, idx)">💬 再讲一遍思路</button>
        </div>

        <!-- 解析（判分后自动展开；解答题提交判分后同样展开） -->
        <details v-if="(it.answer || it.answer_analysis)" class="qc-answer" :open="(inlineJudge && !!result[idx]) || !!lfResult[idx]">
          <summary>答案与解析</summary>
          <MarkdownView v-if="it.answer" :text="tex(it.answer)" />
          <template v-if="it.answer_analysis">
            <div class="qc-ana-label">解析</div>
            <MarkdownView :text="tex(it.answer_analysis)" />
          </template>
        </details>
      </div>

      <div class="qc-foot">
        <!-- 选择题已判分后可再来一组变式 -->
        <button v-if="inlineJudge && anyJudged" class="btn btn-primary btn-sm" @click="$emit('more')">🔄 再来一组变式</button>
        <!-- 填空/解答题：对话内提交判分（v1.9），作答页保留为沉浸式备选 -->
        <button
          v-if="hasLongForm && !lfSubmitted"
          class="btn btn-primary btn-sm"
          :disabled="lfSubmitting || !lfAnyAnswer"
          @click="submitLongForm"
        >
          <span v-if="lfSubmitting" class="spinner"></span>
          {{ lfSubmitting ? 'AI 判分中…' : '提交判分' }}
        </button>
        <button v-if="hasLongForm && !lfSubmitted" class="btn btn-ghost btn-sm" @click="$emit('enter', card)">进入作答页 →</button>
        <span class="qc-hint">{{ footHint }}</span>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import MarkdownView from '@/components/MarkdownView.vue'
import MathFigure from '@/components/chat/MathFigure.vue'
import { wrapBareLatex } from '@/utils/latex'
import { DIFFICULTIES, KP_ZH } from '@/components/student/labels'
import { studentApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import { useSolutionPhoto } from '@/components/student/useSolutionPhoto'
import { uuid } from '@/utils/uuid'

const itemFigure = (it) =>
  !!(it && it.figure && Array.isArray(it.figure.frames) && it.figure.frames.length)

const props = defineProps({
  card: { type: Object, required: true },
})
const emit = defineEmits(['enter', 'explain', 'more', 'wrong', 'answered', 'chainAction'])

const toast = useToastStore()
const sp = useSolutionPhoto(toast)

// 裸 LaTeX（无 $ 定界符）智能包装；选项用行内模式避免展示模式撑开选项行
// 题干清洗：剥掉（MATH-PEP-BIXI2-…）等来源编码，避免"教材ID"当知识点/标题显示
const stripSourceMark = (v) => String(v ?? '').replace(/[（(]?MATH-[A-Z0-9]+[A-Z0-9-]*[)）]?/g, '').replace(/^[，。、\s]+/, '')
const tex = (v) => wrapBareLatex(stripSourceMark(v ?? ''))
// kp 英文代码 → 中文（geometry→立体几何），无映射则不显示
const kpZh = (it) => (it.kp_name || KP_ZH[String(it.kp_code || '')] || '')
const texInline = (v) => wrapBareLatex(v ?? '', { inline: true })

const QTYPE_ZH = { choice: '选择', blank: '填空', solution: '解答' }
const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F']
// 难度文案统一走 labels.js 单一来源（M2 §4.6）
const DIFF_ZH = DIFFICULTIES

const items = computed(() => (Array.isArray(props.card.items) ? props.card.items : []))
// v1.5 变式链模式：后端 card.chain==='variant'（或 source 兜底）
const isChain = computed(
  () => props.card.chain === 'variant' || props.card.source === 'user_variant',
)
// 是否有选择题（可对话内直接作答）
const hasChoice = computed(() => items.value.some((it) => Array.isArray(it.options) && it.options.length))
// 是否有填空/解答（需沉浸式作答页）
const hasLongForm = computed(() => items.value.some((it) => !Array.isArray(it.options) || !it.options.length))
// 对话内判分模式：至少 1 道选择题时启用
const inlineJudge = computed(() => hasChoice.value)

// 判分结果：{ [idx]: { chosen: oi, correct: bool } }
const result = reactive({})
const anyJudged = computed(() => Object.keys(result).length > 0)

/* ===== v1.9 填空/解答题对话内作答（practice/submit 契约，与作答页同源） ===== */
const lfAnswers = reactive({}) // idx -> 答案文本
const lfResult = reactive({}) // idx -> { verdict, score, comment, degraded }
const lfSubmitting = ref(false)
const lfSubmitted = ref(false)
// 对话内 AI 出题无 DB 题组归属：local_* quiz_id + 每题携带 expected_answer/question_text/kp_code
const lfQuizId = props.card.quiz_id || props.card.quizId || `local_${uuid()}`
const lfSubmitId = uuid() // 幂等键：同一次作答重试不重复判分

const longFormIdxs = computed(() =>
  items.value.map((it, i) => ({ it, i })).filter(({ it }) => !Array.isArray(it.options) || !it.options.length).map(({ i }) => i),
)
const lfAnyAnswer = computed(() =>
  longFormIdxs.value.some((i) => (lfAnswers[i] || '').trim() || sp.fileIds[i]),
)
const footHint = computed(() => {
  if (inlineJudge.value && !hasLongForm.value) return '点选项即判分 · 答错自动收录错题并讲解'
  if (hasLongForm.value && !inlineJudge.value) return '写答案或拍照上传手写解答，提交即 AI 判分'
  return '选择题点选项即判分 · 解答题写步骤提交判分'
})

function appendOcrText(idx, text) {
  const t = String(text || '').trim()
  if (!t) return
  lfAnswers[idx] = lfAnswers[idx] ? `${lfAnswers[idx]}\n${t}` : t
}

async function submitLongForm() {
  if (lfSubmitting.value || lfSubmitted.value) return
  const payloadItems = longFormIdxs.value.map((i) => {
    const it = items.value[i]
    return {
      item_no: it.item_no ?? i + 1,
      q_type: it.q_type || 'solution',
      answer_text: (lfAnswers[i] || '').trim(),
      ...(it.kp_code ? { kp_code: it.kp_code } : {}),
      ...(sp.fileIds[i] ? { file_id: sp.fileIds[i] } : {}),
      ...(it.answer ? { expected_answer: it.answer } : {}),
      ...(it.question_text ? { question_text: it.question_text } : {}),
    }
  })
  if (!payloadItems.length) return
  lfSubmitting.value = true
  try {
    const data = await studentApi.practiceSubmit({
      quiz_id: lfQuizId,
      client_submit_id: lfSubmitId,
      items: payloadItems,
    })
    const byNo = new Map((data?.results || []).map((r) => [r.item_no, r]))
    longFormIdxs.value.forEach((i) => {
      const it = items.value[i]
      const r = byNo.get(it.item_no ?? i + 1)
      if (r) lfResult[i] = r
    })
    lfSubmitted.value = true
    if ((data?.results || []).some((r) => r.verdict === 'wrong')) {
      toast.info('判分完成：有错误的题已收录错题本')
    } else {
      toast.success('判分完成')
    }
  } catch (e) {
    toast.error(e?.message || '提交失败，请重试')
  } finally {
    lfSubmitting.value = false
  }
}

// v1.6 链总长（逐题渐进发卡时 items 逐道增长，出齐才算链完成）
const chainTotal = computed(() => Number(props.card.chain_total) || items.value.length)
const chainComplete = computed(() => items.value.length >= chainTotal.value)
const allJudged = computed(
  () => chainComplete.value && items.value.length > 0 && items.value.every((_, i) => result[i]),
)
const correctCount = computed(() => Object.values(result).filter((r) => r.correct).length)
const wrongCount = computed(() => Object.values(result).filter((r) => !r.correct).length)

/** 答案归一化匹配：A-D 字母 或 文本包含 */
function isRightOption(it, oi) {
  const ans = String(it.answer ?? '').trim()
  const opts = Array.isArray(it.options) ? it.options : []
  if (!ans || !opts[oi]) return false
  const letter = LETTERS[oi]
  if (/^[A-F]$/i.test(ans)) return ans.toUpperCase() === letter
  const norm = (s) => String(s).replace(/^[A-F][.、．]?\s*/i, '').replace(/\s+/g, '').toLowerCase()
  const o = norm(opts[oi])
  const a = norm(ans)
  return !!(o && a && (o === a || o.includes(a) || a.includes(o)))
}

/** 选项状态 class：未判分=''（可点选）；已判分=所选对 right/错 wrong，未选的正确项标 right */
function optClass(idx, oi) {
  const r = result[idx]
  if (!r) return ''
  if (r.chosen === oi) return r.correct ? 'right' : 'wrong'
  if (isRightOption(items.value[idx], oi)) return 'right'
  return ''
}

function onPick(idx, oi) {
  if (result[idx]) return // 已判分，不可改（模拟真实作答）
  const it = items.value[idx]
  const correct = isRightOption(it, oi)
  result[idx] = { chosen: oi, correct }
  // 迭代15 L0-3：每次判分都上报学习事件总线（对错都报），错题收录/学情/复习由服务端统一分发
  emit('answered', props.card, it, idx, { correct, chosen: LETTERS[oi] || '' })
  if (!correct) {
    // 答错：收录错题（父组件处理）+ 反馈
    emit('wrong', props.card, it, idx)
  }
}

/* 迭代15 B4：讲解请求携带学生作答结果（错选选项/正误），
   让后端讲解能定位"你当时为什么选 X"——不靠模型猜 */
function emitExplain(it, idx) {
  const r = result[idx]
  const lf = lfResult[idx]
  const outcome = r
    ? { chosen: LETTERS[r.chosen] || '', correct: r.correct }
    : lf
      ? { chosen: '', correct: lf.verdict === 'correct', score: lf.score }
      : null
  emit('explain', props.card, it, idx, outcome)
}

/* 进度链节点状态：已判 ok/no；下一道待答 current；其余 todo */
function trailClass(ti) {
  const r = result[ti]
  if (r) return r.correct ? 'ok' : 'no'
  const firstTodo = items.value.findIndex((_, i) => !result[i])
  return ti === firstTodo ? 'current' : 'todo'
}
function trailMark(ti) {
  const r = result[ti]
  if (!r) return ''
  return r.correct ? ' ✓' : ' ✗'
}
</script>

<style scoped>
.quiz-card {
  margin-top: var(--space-2); border: 1px solid var(--primary-border);
  border-radius: var(--radius-md); padding: var(--space-3) var(--space-4);
  background: var(--bg-white);
}
.qc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.qc-title { font-weight: 700; font-size: 13px; color: var(--primary); }
.qc-badge {
  font-size: 10.5px; padding: 2px 8px; border-radius: var(--radius-full);
  background: #EEF1FF; color: var(--primary); font-weight: 600;
}
.qc-vnote { font-size: 11px; color: #7C5CF0; font-weight: 600; }
/* variant_note 经 markdown/KaTeX 渲染：行内化 + 去段落间距，保持头行不换行 */
.qc-vnote :deep(p) { margin: 0; display: inline; }
.qc-vnote :deep(.katex) { font-size: 1em; }
.qc-item { padding: 8px 0; border-top: 1px dashed var(--border); }
/* 题目配图放在题干内部（不是悬浮在题卡之前的讲解步骤图） */
.qc-figure { margin: 6px 0 8px; }
.qc-figure :deep(.math-figure) { margin-top: 0; padding: 8px 10px; }
.qc-stem-box {
  background: #fff; border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px; margin: 4px 0 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.05); font-size: 14px; line-height: 1.7;
}
.qc-stem-box :deep(.md-body p) { margin: 0 0 4px; }
.qc-stem-box:empty { display: none; }
.qc-item:first-of-type { border-top: none; }
.qc-item-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-bottom: 4px; }
.qc-no { font-size: 12px; font-weight: 700; color: var(--text-secondary); }
.qc-options { display: flex; flex-direction: column; gap: 4px; margin: 6px 0; }
.qc-options.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
@media (max-width: 560px) { .qc-options.grid { grid-template-columns: 1fr; } }
.qc-opt {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm);
  font-size: 13px; cursor: pointer; transition: all 0.15s; background: var(--bg-white);
}
.qc-opt:hover { border-color: var(--primary-light); background: #EEF1FF; }
.qc-opt-key {
  width: 20px; height: 20px; border-radius: 6px; flex-shrink: 0;
  background: var(--bg-muted); color: var(--text-secondary);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.qc-opt-body { flex: 1; min-width: 0; }
.qc-opt-body :deep(.md-body p) { margin: 0; }
.qc-opt-body :deep(.md-body) { font-size: 13px; line-height: 1.5; }
.qc-opt-note { font-size: 11px; font-weight: 700; flex-shrink: 0; }
.qc-opt-note.right-note { color: #16A34A; }

/* 判分状态 */
.qc-opt.sel { border-color: var(--primary); background: #EEF1FF; color: var(--primary); font-weight: 600; }
.qc-opt.sel .qc-opt-key { background: var(--primary); color: #fff; }
.qc-opt.right {
  border-color: #86D3A6; background: #F0FBF4; font-weight: 600;
}
.qc-opt.right .qc-opt-key { background: #16A34A; color: #fff; }
.qc-opt.right .qc-opt-note { color: #16A34A; }
.qc-opt.wrong {
  border-color: #F2A3A6; background: #FEF3F3; font-weight: 600;
}
.qc-opt.wrong .qc-opt-key { background: #E5484D; color: #fff; }
.qc-opt.wrong .qc-opt-note { color: #E5484D; }

/* 判分结果条 */
.qc-judge {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-top: 6px; padding: 8px 12px; border-radius: var(--radius-sm);
  font-size: 12.5px;
}
.qc-judge.ok { background: #F0FBF4; color: #166534; }
.qc-judge.no { background: #FEF3F3; color: #7F1D1D; }
.qc-judge-txt { flex: 1; min-width: 160px; }
.qc-delta { font-weight: 700; font-size: 12.5px; flex-shrink: 0; }
.qc-delta.up { color: #16A34A; }
.qc-delta.down { color: #E5484D; }
.qc-explain-btn {
  padding: 5px 12px; border-radius: var(--radius-sm); border: none;
  background: var(--primary); color: #fff; font-size: 12px; font-weight: 600;
  cursor: pointer; transition: all 0.15s;
}
.qc-explain-btn:hover { background: var(--primary-hover); }
.qc-explain-btn.subtle {
  background: #EEF1FF; color: var(--primary);
}
.qc-explain-btn.subtle:hover { background: #DFE6FF; }

.qc-answer { margin-top: 6px; font-size: 13px; }
.qc-answer :deep(.md-body) { max-height: 300px; overflow-y: auto; }
.qc-answer summary { cursor: pointer; color: var(--primary); font-weight: 600; font-size: 12px; }
.qc-ana-label { font-size: 12px; font-weight: 700; color: var(--text-secondary); margin: 6px 0 2px; }
.qc-foot { display: flex; align-items: center; gap: 10px; margin-top: 10px; flex-wrap: wrap; }
.qc-hint { font-size: 11px; color: var(--text-muted); }

/* ===== v1.9 填空/解答题对话内作答 ===== */
.qc-longform { margin: 6px 0; }
.qc-lf-input { width: 100%; font-size: 13px; }
.qc-lf-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.qc-photo-btn { display: inline-flex; align-items: center; gap: 6px; }
.qc-photo-input { display: none; }
.qc-lf-comment { display: block; margin-top: 4px; font-size: 12px; opacity: 0.9; }

/* ===== v1.5 变式链模式 ===== */
.quiz-chain { border-color: #D9D2F7; background: linear-gradient(180deg, #FBFAFF 0%, var(--bg-white) 100%); }
.qc-chain-no { font-size: 12px; font-weight: 700; color: #7C5CF0; }
.qc-variant {
  border: 1px solid var(--border); border-radius: var(--radius-md);
  padding: 10px 12px; margin-top: 8px; background: var(--bg-white);
}
.qc-transition {
  margin-top: 10px; font-size: 12.5px; color: var(--text-secondary);
  padding-left: 2px;
}
.qc-trail {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-top: 10px; padding-top: 8px; border-top: 1px dashed var(--border);
  font-size: 11px;
}
.qc-trail-node {
  padding: 2px 10px; border-radius: var(--radius-full);
  background: var(--bg-muted); color: var(--text-muted); font-weight: 600;
}
.qc-trail-node.origin { background: transparent; color: var(--text-muted); }
.qc-trail-node.current { background: #EEF1FF; color: var(--primary); }
.qc-trail-node.ok { background: #F0FBF4; color: #16A34A; }
.qc-trail-node.no { background: #FEF3F3; color: #E5484D; }
.qc-trail-arrow { color: var(--text-muted); }
.qc-complete {
  margin-top: 10px; padding: 10px 14px; border-radius: var(--radius-sm);
  font-size: 13px; font-weight: 600;
}
.qc-complete.full { background: #F0FBF4; color: #166534; }
.qc-complete.part { background: #FFF8E6; color: #92600A; }
.qc-pending {
  margin-top: 10px; padding: 8px 12px; border-radius: var(--radius-sm);
  background: var(--bg-muted); color: var(--text-muted);
  font-size: 12px; font-weight: 600; text-align: center;
  animation: qcPulse 1.6s ease-in-out infinite;
}
@keyframes qcPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
.qc-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.btn-ghost {
  border: 1px solid var(--border); background: var(--bg-white); color: var(--text-primary);
  border-radius: var(--radius-sm); cursor: pointer; transition: all 0.15s;
}
.btn-ghost:hover { border-color: var(--primary-light); color: var(--primary); background: #EEF1FF; }
</style>
