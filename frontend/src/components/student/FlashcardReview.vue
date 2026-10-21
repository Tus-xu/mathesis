<template>
  <div class="fc-overlay">
    <div class="fc-shell">
      <div class="fc-head">
        <div>
          <strong>🎴 错题闪卡复习</strong>
          <span class="fc-count" v-if="cards.length && !endDone">第 {{ index + 1 }} / {{ cards.length }} 张 · 已评 {{ reviewed }} 张</span>
        </div>
        <button class="secondary fc-exit" @click="$emit('close')">✕ 退出复习</button>
      </div>

      <div v-if="loading" class="fc-state">复习卡片加载中…</div>
      <div v-else-if="loadError" class="fc-state">⚠ {{ loadError }}<button class="secondary" style="margin-left:8px;" @click="load">重试</button></div>
      <div v-else-if="!cards.length" class="fc-state">
        <div style="font-size:15px;font-weight:700;">🎉 今天没有需要复习的错题</div>
        <div style="color:var(--ink3);font-size:12.5px;margin-top:4px;">记忆保持得很好，去做新题吧</div>
        <button class="secondary" style="margin-top:12px;" @click="$emit('close')">返回错题本</button>
      </div>

      <div v-else-if="endDone" class="fc-state">
        <div style="font-size:18px;font-weight:900;">🎉 本轮复习完成</div>
        <div style="color:var(--ink2);font-size:13px;margin-top:8px;line-height:1.9;">
          共完成 <b>{{ reviewed }}</b> 张 · 🟢 很熟 {{ stats[4] }} · ✅ 记住了 {{ stats[3] }} · 🟡 勉强 {{ stats[2] }} · 🔴 没记住 {{ stats[1] }}<br/>
          没记住/勉强的题目已提到前面，明天再来一遍就稳了。
        </div>
        <div style="display:flex;gap:10px;margin-top:14px;justify-content:center;">
          <button class="primary" @click="load">再来一轮</button>
          <button class="secondary" @click="$emit('close')">返回错题本</button>
        </div>
      </div>

      <template v-else-if="card">
        <div class="fc-stage">
          <div
            class="flashcard-perspective"
            :class="{ clicked: flipped }"
            @click="flip" tabindex="0" role="button"
            @keydown.enter="flip" @keydown.space.prevent="flip"
            :aria-label="flipped ? '卡片已翻面，显示答案' : '题目卡片，点击翻面'"
          >
            <div class="flashcard-inner" :class="{ flipped }">
              <div class="fc-face fc-front">
                <div class="fc-label">题目 · 先独立想一想再翻面</div>
                <LatexText class="fc-q" :text="card.question_text || '（题干加载中…）'" />
                <div v-if="card.image?.length || card.photoUrl" class="fc-fig" @click.stop>
                  <DynamicFigureViewer :items="fcFigItems(card)" :label="'题目配图'" :height="200" :toolbar="false" />
                </div>
                <div class="fc-tags" v-if="card.kp_name || card.kp_code || card.error_type">
                  <span class="fc-tag">{{ card.kp_name || card.kp_code || '未标注知识点' }}</span>
                  <span v-if="card.error_type" class="fc-tag err">{{ errorTypeZh(card.error_type) }}</span>
                </div>
              </div>
              <div class="fc-face fc-back">
                <div class="fc-label ok">答案 · 对照着记住错因</div>
                <div class="fc-answer">{{ card.answer_text || '（暂无正解文本，可回错题本查看 AI 诊断）' }}</div>
                <div class="fc-why" v-if="card.note">💡 你的备注：{{ card.note }}</div>
              </div>
            </div>
          </div>

          <div v-if="!flipped" class="fc-hint">点击卡片翻面 · 或按 空格/回车</div>
          <div v-else class="fc-ratings">
            <button class="fc-rate again" :disabled="submitting" @click="rate(1)">🔄 没记住</button>
            <button class="fc-rate hard" :disabled="submitting" @click="rate(2)">🟡 勉强</button>
            <button class="fc-rate good" :disabled="submitting" @click="rate(3)">✅ 记住了</button>
            <button class="fc-rate easy" :disabled="submitting" @click="rate(4)">🟢 很熟</button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '@/api/client'
import { filesApi } from '@/api'
import { useToastStore } from '@/stores/toast'
import LatexText from '@/components/LatexText.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'

const emit = defineEmits(['close'])
const toast = useToastStore()

const cards = ref([])
const index = ref(0)
const flipped = ref(false)
const submitting = ref(false)
const loading = ref(true)
const loadError = ref('')
const reviewed = ref(0)
const stats = reactive({ 1: 0, 2: 0, 3: 0, 4: 0 })

const card = computed(() => cards.value[index.value] || null)
// 闪卡渲染源：image 列（data-URI + ggb 对象）+ 原图照片
function fcFigItems(c) {
  const arr = Array.isArray(c?.image) ? c.image.slice() : []
  if (c?.photoUrl) arr.push(c.photoUrl)
  return arr
}
const endDone = computed(() => cards.value.length > 0 && index.value >= cards.value.length)

const ERROR_TYPES = { concept: '概念不清', formula: '公式记错', calculation: '计算失误', logic: '思路错误', reading: '审题偏差' }
function errorTypeZh(t) { return (t && ERROR_TYPES[t]) || '未分类' }

async function load() {
  loading.value = true
  loadError.value = ''
  Object.keys(stats).forEach((k) => { stats[k] = 0 })
  reviewed.value = 0
  index.value = 0
  flipped.value = false
  try {
    const due = await api.get('/student/error-records/due-queue')
    let items = due?.items || []
    if (!items.length) {
      const f = await api.get('/student/error-records/filter', { page: 1, size: 50 })
      items = (f?.items || []).slice(0, 20)
    }
    cards.value = items.map((it) => ({
      record_id: it.record_id,
      question_text: it.question_preview || it.question_text || '',
      answer_text: '',
      error_type: it.error_type || '',
      kp_code: it.kp_code || '',
      kp_name: it.kp_name || '',
      note: '',
      image: [],
      photoUrl: '',
      _loaded: false,
    }))
  } catch (e) {
    loadError.value = e?.message || '复习卡片加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

async function ensureDetail() {
  const c = card.value
  if (!c || c._loaded) return
  try {
    const d = await api.get(`/student/error-records/${c.record_id}/detail`)
    if (!c._loaded) {
      c.question_text = d?.question_text || c.question_text
      c.answer_text = d?.answer_text || ''
      c.error_type = d?.error_type || c.error_type
      c.kp_name = d?.kp_name || c.kp_name
      c.kp_code = d?.kp_code || c.kp_code
      c.note = d?.note || ''
      c.image = d?.image || []
      if (d?.file_id) {
        try {
          const du = await filesApi.contentUrl(d.file_id)
          if (du?.url) c.photoUrl = du.url
        } catch { /* 图片暂不可用：不阻塞翻面 */ }
      }
      c._loaded = true
    }
  } catch { /* 详情失败不阻塞翻面，正面仍有题干 */ }
}

function flip() {
  if (submitting.value) return
  flipped.value = !flipped.value
  if (flipped.value) ensureDetail()
}

async function rate(v) {
  const c = card.value
  if (!c || submitting.value) return
  submitting.value = true
  try {
    // 四档对齐 OpenTutor（Again/Hard/Good/Easy）→ 映射 2 档复习结果（forgotten/remembered）
    await api.post(`/student/error-records/${c.record_id}/review`, { result: v <= 2 ? 'forgotten' : 'remembered' })
    stats[v] += 1
    reviewed.value += 1
    index.value += 1
    flipped.value = false
  } catch (e) {
    toast.error(`评分提交失败：${e?.message || '请稍后重试'}`)
  } finally {
    submitting.value = false
  }
}

function keydown(e) {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.target.closest && e.target.closest('button')) return
  if (endDone.value || loading.value) return
  if (!flipped.value) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip() }
    return
  }
  if (submitting.value) return
  const k = parseInt(e.key, 10)
  if (k >= 1 && k <= 4) { e.preventDefault(); rate(k) }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); rate(1) }
  else if (e.key === 'ArrowRight') { e.preventDefault(); rate(3) }
}

onMounted(() => { load(); window.addEventListener('keydown', keydown) })
onBeforeUnmount(() => window.removeEventListener('keydown', keydown))
</script>

<style scoped>
.fc-overlay {
  position: fixed; inset: 0; z-index: 70;
  background: rgba(244,246,250,.96); backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.fc-shell { width: 100%; max-width: 560px; }
.fc-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.fc-count { font-size: 12px; color: var(--ink3, #9aa1ac); margin-left: 10px; }
.fc-exit { font-size: 12px; }
.fc-state { background: #fff; border: 1px dashed var(--line, #e5e7eb); border-radius: 16px; padding: 40px 24px; text-align: center; color: var(--ink2, #646a73); font-size: 13.5px; }

/* OpenTutor 同款 3D 翻转 */
.flashcard-perspective { perspective: 800px; cursor: pointer; -webkit-user-select: none; user-select: none; }
.flashcard-inner {
  position: relative; width: 100%; min-height: 300px;
  transform-style: preserve-3d; transition: transform 500ms ease;
}
.flashcard-inner.flipped { transform: rotateY(180deg); }
.fc-face {
  position: absolute; inset: 0; backface-visibility: hidden;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  border-radius: 18px; padding: 26px 30px; text-align: center;
  box-shadow: 0 6px 24px rgba(15,23,42,.08); background: #fff; border: 1px solid var(--line, #eef1f5);
}
.fc-back { transform: rotateY(180deg); background: linear-gradient(160deg, #f0fdf4, #ffffff); border-color: rgba(22,163,74,.25); }
.fc-label { font-size: 11px; font-weight: 700; color: var(--ink3, #9aa1ac); margin-bottom: 12px; letter-spacing: 1px; }
.fc-label.ok { color: #16a34a; }
.fc-q { font-size: 15px; font-weight: 600; line-height: 1.8; color: #1f2937; max-width: 100%; overflow-wrap: break-word; }
.fc-fig { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 12px; }
.fc-fig img { max-width: 100%; max-height: 220px; border: 1px solid var(--line, #eef1f5); border-radius: 10px; background: #fff; }
.fc-tags { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; justify-content: center; }
.fc-tag { font-size: 11px; font-weight: 700; color: var(--brand, #3b7bff); background: #eaf1ff; border-radius: 999px; padding: 3px 10px; }
.fc-tag.err { color: #b91c1c; background: #fef2f2; }
.fc-answer { font-size: 14px; line-height: 1.8; color: #14532d; white-space: pre-line; max-height: 200px; overflow: auto; }
.fc-why { margin-top: 12px; font-size: 12px; color: var(--ink2, #646a73); background: #fffbeb; border: 1px dashed #fde68a; border-radius: 10px; padding: 8px 12px; }
.fc-hint { text-align: center; font-size: 12px; color: var(--ink3, #9aa1ac); margin-top: 12px; }
.fc-ratings { display: flex; gap: 8px; justify-content: center; margin-top: 16px; }
.fc-rate { border: 1px solid var(--line, #eef1f5); border-radius: 999px; padding: 9px 16px; font: inherit; font-size: 13px; font-weight: 800; cursor: pointer; background: #fff; }
.fc-rate:disabled { opacity: .5; cursor: not-allowed; }
.fc-rate.again { color: #b91c1c; }
.fc-rate.hard { color: #b45309; }
.fc-rate.good { color: #15803d; }
.fc-rate.easy { color: var(--brand, #3b7bff); background: #eaf1ff; border-color: #bcd4ff; }
</style>
