<template>
  <Teleport to="body">
    <div v-if="open" class="q-drawer" @click.self="$emit('close')">
      <aside class="q-panel" role="dialog" aria-modal="true" aria-label="题目详情">
        <header class="q-head">
          <div>
            <div class="q-eyebrow">题库题目 <span v-if="detail?.source">· {{ detail.source }}</span></div>
            <h2 class="q-title">{{ typeZh(detail?.q_type) }}<span v-if="detail?.year"> · {{ detail.year }}</span></h2>
          </div>
          <button type="button" class="q-close" aria-label="关闭" @click="$emit('close')">✕</button>
        </header>

        <div class="q-scroll">
          <div v-if="loading" class="q-loading">题目加载中…</div>
          <div v-else-if="error" class="q-error">
            {{ error }}
            <button type="button" @click="load">重试</button>
          </div>
          <template v-else-if="detail">
            <div class="q-meta">
              <span class="q-chip">{{ diffZh(detail.difficulty) }}</span>
              <span v-if="detail.is_real_exam" class="q-chip q-chip--exam">真题</span>
              <span v-for="name in detail.kp_names || []" :key="name" class="q-chip q-chip--kp">{{ name }}</span>
            </div>

            <article class="q-stem">
              <MarkdownView :text="detail.stem || ''" :zoomable="true" />
            </article>

            <div v-if="options.length" class="q-options">
              <div v-for="o in options" :key="o.key" class="q-option">
                <span class="q-option-ltr">{{ o.key }}</span>
                <MarkdownView :text="o.text" :zoomable="true" mode="option" />
              </div>
            </div>

            <div class="q-answer-box">
              <button type="button" class="q-reveal" @click="revealed = !revealed">
                {{ revealed ? '收起答案与解析' : '显示答案与解析' }}
              </button>
              <template v-if="revealed">
                <div class="q-answer"><span>答案</span><MarkdownView :text="detail.answer || '暂无答案'" /></div>
                <div v-if="detail.analysis" class="q-analysis"><span>解析</span><MarkdownView :text="detail.analysis" /></div>
              </template>
            </div>
          </template>
        </div>

        <footer class="q-actions">
          <button type="button" class="q-btn q-btn--primary" :disabled="!detail" @click="ask('socratic')">
            💡 让 AI 带我解
          </button>
          <button type="button" class="q-btn" :disabled="!detail" @click="ask('quiz_gen')">
            🔄 出 3 道同类变式
          </button>
          <button type="button" class="q-btn" :disabled="!detail" @click="$emit('practice', detail)">
            开始练这道
          </button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { questionBankApi } from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  question: { type: Object, default: null },
})
const emit = defineEmits(['close', 'ask', 'practice'])

const detail = ref(null)
const loading = ref(false)
const error = ref('')
const revealed = ref(false)

const options = computed(() => {
  const raw = detail.value?.options
  if (!raw) return []
  if (Array.isArray(raw)) {
    return raw.map((text, i) => ({ key: String.fromCharCode(65 + i), text: String(text) }))
  }
  return Object.entries(raw).map(([key, text]) => ({ key, text: String(text) }))
})

function typeZh(t) {
  return ({ choice: '选择题', blank: '填空题', solution: '解答题' })[t] || '题目'
}
function diffZh(d) {
  return ({ easy: '基础', medium: '进阶', hard: '压轴' })[d] || d || '难度未标'
}

async function load() {
  const id = props.question?.id
  if (!id) return
  loading.value = true
  error.value = ''
  revealed.value = false
  try {
    detail.value = await questionBankApi.detail(id)
  } catch (e) {
    detail.value = null
    error.value = e?.message || '题目加载失败'
  } finally {
    loading.value = false
  }
}

function ask(skill) {
  if (!detail.value) return
  const stem = String(detail.value.stem || '').slice(0, 1200)
  const text = skill === 'quiz_gen'
    ? `请基于这道题出 3 道由易到难的同类变式题，先出第一题，并告诉我每题考查点：\n${stem}`
    : `请用苏格拉底方式带我解这道题，不要直接给答案，先问我第一步该怎么想：\n${stem}`
  emit('ask', {
    text,
    display: skill === 'quiz_gen' ? '🔄 同类变式 · 由易到难' : '💡 让 AI 带我解这道题',
    skill,
  })
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}
watch(
  () => [props.open, props.question?.id],
  ([open, id], old) => {
    if (!open) return
    if (!old || id !== old[1] || !detail.value) load()
  },
  { immediate: true },
)
watch(
  () => props.open,
  (open) => {
    if (open) {
      revealed.value = false
      document.addEventListener('keydown', onKeydown)
    } else {
      document.removeEventListener('keydown', onKeydown)
    }
  },
)
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.q-drawer {
  position: fixed; inset: 0; z-index: 1400; display: flex; justify-content: flex-end;
  background: rgba(15, 23, 42, .28); backdrop-filter: blur(3px);
}
.q-panel {
  width: min(640px, 94vw); height: 100%; display: flex; flex-direction: column;
  background: var(--card); border-left: 1px solid var(--line);
  box-shadow: -20px 0 60px rgba(15, 23, 42, .16);
  animation: q-in .24s cubic-bezier(.16, 1, .3, 1);
}
@keyframes q-in { from { transform: translateX(24px); opacity: .5; } }
.q-head { display: flex; justify-content: space-between; gap: 12px; padding: 20px 22px 15px; border-bottom: 1px solid var(--line); }
.q-eyebrow { font-size: 10.5px; color: var(--ink3); font-weight: 700; letter-spacing: .03em; }
.q-title { margin: 4px 0 0; font-size: 17px; font-weight: 800; color: var(--ink); }
.q-close { width: 30px; height: 30px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); color: var(--ink3); cursor: pointer; }
.q-close:hover { color: var(--ink); border-color: var(--border-strong); }
.q-scroll { flex: 1; min-height: 0; overflow: auto; padding: 16px 22px 24px; }
.q-loading, .q-error { padding: 24px; text-align: center; color: var(--ink3); font-size: 12.5px; }
.q-error { color: var(--err-deep); }
.q-error button { margin-left: 8px; border: 0; background: none; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }
.q-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.q-chip { padding: 2px 8px; border-radius: 999px; background: var(--bg2); color: var(--ink2); font-size: 10px; font-weight: 700; }
.q-chip--exam { background: var(--warn-bg); color: var(--warn-deep); }
.q-chip--kp { background: var(--brand-soft); color: var(--brand-deep); }
.q-stem { padding: 14px 15px; border-radius: 12px; border: 1px solid var(--line); background: var(--bg-white); }
.q-stem :deep(.md-body) { font-size: 14px; line-height: 1.8; color: var(--ink); }
.q-stem :deep(.md-body table) { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 12px; }
.q-stem :deep(.md-body th), .q-stem :deep(.md-body td) { border: 1px solid var(--line); padding: 4px 8px; }
.q-options { display: flex; flex-direction: column; gap: 7px; margin-top: 12px; }
.q-option { display: flex; gap: 9px; align-items: flex-start; padding: 9px 11px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); }
.q-option-ltr { width: 22px; height: 22px; flex: none; display: grid; place-items: center; border-radius: 6px; background: var(--bg2); color: var(--ink2); font-size: 11px; font-weight: 900; }
.q-option :deep(.md-body) { font-size: 12.5px; line-height: 1.6; }
.q-option :deep(.md-body p) { margin: 0; }
.q-answer-box { margin-top: 16px; padding: 12px 13px; border-radius: 11px; background: var(--bg2); }
.q-reveal { width: 100%; height: 34px; border: 1px dashed var(--border-strong); border-radius: 8px; background: var(--card); color: var(--ink2); font: inherit; font-size: 12px; font-weight: 800; cursor: pointer; }
.q-reveal:hover { border-color: var(--primary-border); color: var(--brand-deep); }
.q-answer, .q-analysis { margin-top: 10px; font-size: 12.5px; line-height: 1.7; color: var(--ink2); }
.q-answer > span, .q-analysis > span { display: inline-block; margin-bottom: 3px; padding: 1px 7px; border-radius: 999px; background: var(--ok-bg); color: var(--ok-deep); font-size: 10px; font-weight: 800; }
.q-analysis > span { background: var(--brand-soft); color: var(--brand-deep); }
.q-answer :deep(.md-body), .q-analysis :deep(.md-body) { font-size: 12.5px; line-height: 1.7; }
.q-actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--line); background: var(--card); }
.q-btn { height: 34px; padding: 0 14px; border: 1px solid var(--line); border-radius: 9px; background: var(--card); color: var(--ink2); font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer; }
.q-btn:hover:not(:disabled) { border-color: var(--primary-border); color: var(--brand-deep); }
.q-btn:disabled { opacity: .45; cursor: not-allowed; }
.q-btn--primary { border-color: transparent; background: var(--gradient-brand); color: #fff; }
.q-btn--primary:hover:not(:disabled) { color: #fff; filter: brightness(1.04); }
@media (max-width: 640px) { .q-panel { width: 100%; } }
</style>
