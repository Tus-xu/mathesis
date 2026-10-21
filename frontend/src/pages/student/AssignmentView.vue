<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">
        <span v-if="typeLabel" class="type-badge" :class="detail?.type">{{ typeLabel }}</span>
        {{ detail?.title || '作业作答' }}
      </div>
      <div class="sub">答案与照片会真实提交给老师，教师确认后可在此查看终分和反馈。</div>
    </div>

    <div v-if="loading" class="empty-state"><div class="es-text">作业加载中…</div></div>
    <div v-else-if="error" class="empty-state"><div class="es-text">{{ error }}</div></div>
    <template v-else-if="detail">
      <div v-if="!detail.items || !detail.items.length" class="empty-state card" style="padding:32px">
        <div class="es-icon">📭</div>
        <div class="es-text">这份任务还没有题目（老师可能尚未完成组卷）。请等待老师补充后再作答。</div>
      </div>
      <div v-for="item in detail.items || []" :key="item.item_no" class="card" style="margin-bottom:12px;padding:18px">
        <h3>{{ item.item_no }}. <LatexText :text="item.question_text" /></h3>
        <div v-if="item.image && item.image.length" style="margin:8px 0;">
          <DynamicFigureViewer :items="item.image" :label="'题目配图'" :height="260" />
        </div>
        <div v-if="item.q_type === 'choice'" style="display:grid;gap:8px">
          <label v-for="(text, key) in item.options || {}" :key="key">
            <input v-model="answers[item.item_no]" type="radio" :value="key" :disabled="submitted"> {{ key }}. <LatexText :text="text" />
          </label>
          <p class="auto-tip">· 选择题提交后系统自动判分，即时出结果</p>
        </div>
        <input v-else-if="item.q_type === 'blank'" v-model="answers[item.item_no]" class="input" placeholder="填写答案" :disabled="submitted">
        <template v-else>
          <textarea v-model="answers[item.item_no]" class="input" rows="6" placeholder="写出解题步骤，或拍照识别后修改" :disabled="submitted"></textarea>
        </template>

        <HomeworkPhotos
          v-if="!submitted"
          v-model="photos[item.item_no]"
          :max="3"
          @ocr="(t) => { answers[item.item_no] = t }"
        />
        <div v-if="submitted && resultPhotos[item.item_no]?.length" class="submitted-photos">
          <img
            v-for="(src, i) in resultPhotos[item.item_no]"
            :key="i" :src="src" alt="作答照片"
            @click="openLightbox(src)"
          />
        </div>

        <!-- 提交即判分：选择题/可自动判的填空题即时展示判分结果；主观题/拍照题展示待批阅态 -->
        <div v-if="autoVerdict(item.item_no)" class="auto-verdict" :class="autoVerdict(item.item_no).cls">
          {{ autoVerdict(item.item_no).text }}
        </div>
      </div>

      <button v-if="!submitted && detail.items?.length" class="primary" type="button" :disabled="submitting" @click="submit">
        {{ submitting ? '提交中…' : '提交给老师' }}
      </button>

      <div v-if="result" class="card" style="margin-top:16px;padding:18px">
        <h3>批改结果</h3>
        <p>状态：{{ resultStatusText }}</p>
        <p v-if="result.total_score !== null">总分：{{ result.total_score }}</p>
        <p v-else class="pending-tip">老师确认批阅后，这里会公布终分和反馈。</p>
        <div v-for="item in result.items" :key="item.submission_item_id" style="padding:8px 0;border-top:1px solid var(--line)">
          第 {{ item.item_no }} 题：{{ item.score ?? '待老师确认' }}
          <div v-if="item.answer_text" style="font-size:12px;color:var(--ink2);margin-top:2px;">你的作答：{{ item.answer_text }}</div>
          <div v-if="item.teacher_feedback">老师反馈：<LatexText :text="item.teacher_feedback" /></div>
          <div v-if="item.attachments?.length" class="result-photos">
            <img
              v-for="(src, i) in resultPhotos[item.item_no] || []"
              :key="i" :src="src" alt="作答照片"
              @click="openLightbox(src)"
            />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { studentApi, filesApi } from '@/api'
import HomeworkPhotos from '@/components/student/HomeworkPhotos.vue'
import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'
import LatexText from '@/components/LatexText.vue'
import { openLightbox } from '@/utils/lightbox'
import { useToastStore } from '@/stores/toast'

const route = useRoute()
const toast = useToastStore()
const detail = ref(null)
const result = ref(null)
const answers = reactive({})
const photos = reactive({}) // item_no -> [{file_id}]
const resultPhotos = reactive({}) // item_no -> [presigned urls]
const autoResults = reactive({}) // item_no -> { verdict, score, degraded }（提交响应即时判分）
const loading = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const error = ref('')
/** 提交幂等键：一次作答会话只生成一次（旧实现每次点提交都用 Date.now()，超时重试会重复建 submission）。 */
const submitId = ref('')

function makeSubmitId(assignmentId) {
  const key = `ma_submit_${assignmentId}`
  try {
    const cached = sessionStorage.getItem(key)
    if (cached) return cached
  } catch { /* 隐私模式 */ }
  let id
  try {
    id = (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function')
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  } catch {
    id = `${Date.now()}`
  }
  // submissions.client_submit_id 是 varchar(64)：只保留短前缀 + 36 位 UUID。
  id = `sub-${id}`.slice(0, 64)
  try { sessionStorage.setItem(key, id) } catch { /* 隐私模式 */ }
  return id
}

// 类型徽标（tel D2：学前作业/课后习题与 quiz/watch 同域展示）
const TYPE_LABELS = { pre_class: '学前作业', homework: '课后习题', quiz: '练习', watch: '视频' }
const typeLabel = computed(() => TYPE_LABELS[detail.value?.type] || '')

const resultStatusText = computed(() => {
  if (!result.value) return ''
  if (result.value.status === 'graded') return '老师已批阅'
  return result.value.status === 'pending_review' ? '等待老师批阅' : '已提交'
})

/** 提交即时判分展示：correct/wrong 出分数；pending_review 出待批阅态（拍照/解答题） */
function autoVerdict(itemNo) {
  const r = autoResults[itemNo]
  if (!r) return null
  if (r.verdict === 'correct') {
    return { cls: 'ok', text: `✓ 判对${r.score != null ? ` · +${r.score} 分` : ''}` }
  }
  if (r.verdict === 'wrong') {
    return { cls: 'bad', text: '✗ 判错 · 0 分（已收录错题本）' }
  }
  const photoHint = r.degraded === 'photo_attached' || r.degraded === 'ocr_pending'
    ? '照片已随答案提交'
    : ''
  return { cls: 'wait', text: `⏳ 已提交，等待老师批阅${photoHint ? `（${photoHint}）` : ''}` }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    detail.value = await studentApi.assignmentDetail(route.params.id)
    submitId.value = makeSubmitId(detail.value.assignment_id || route.params.id)
    try { result.value = await studentApi.assignmentResult(route.params.id) } catch { result.value = null }
    if (result.value) {
      submitted.value = true
      // 已交作业必须回显学生自己的作答（旧实现只显示分数，刷新后输入框全空，像“作答丢了”）。
      for (const item of result.value.items || []) {
        if (item.answer_text) answers[item.item_no] = item.answer_text
      }
      await resolveResultPhotos(result.value)
    }
  } catch (e) { error.value = e?.message || '作业加载失败' }
  finally { loading.value = false }
}

/** 预签名缩略图：学生提交的照片附件（含 OCR 文本兜底展示） */
async function resolveResultPhotos(submission) {
  for (const item of submission.items || []) {
    const ids = (item.attachments || []).map((a) => a.file_id).filter(Boolean)
    if (!ids.length) continue
    resultPhotos[item.item_no] = []
    for (const fid of ids) {
      try {
        const d = await filesApi.contentUrl(fid)
        if (d?.url) resultPhotos[item.item_no].push(d.url)
      } catch { /* 图片暂不可用：跳过缩略图 */ }
    }
  }
}

async function submit() {
  if (!detail.value || submitting.value) return
  const items = detail.value.items.map((item) => ({
    item_no: item.item_no,
    q_type: item.q_type,
    answer_text: answers[item.item_no] || null,
    attachments: photos[item.item_no] || [],
  }))
  if (items.some((item) => !item.answer_text && !item.attachments.length)) {
    toast.error('请完成全部题目后再提交（可填写答案或上传解答照片）')
    return
  }
  submitting.value = true
  try {
    const data = await studentApi.practiceSubmit({
      assignment_id: detail.value.assignment_id,
      client_submit_id: submitId.value || makeSubmitId(detail.value.assignment_id),
      items,
    })
    // 提交响应自带逐题判分（student_router.py practice/submit → results[]）：
    // 选择题即时展示判对/判错；pending_review 题项展示「等待老师批阅」。
    for (const r of data?.results || []) {
      autoResults[r.item_no] = { verdict: r.verdict, score: r.score, degraded: r.degraded || '' }
    }
    const hasPending = Object.values(autoResults).some((r) => r.verdict === 'pending_review')
    toast.success(hasPending ? '已提交，主观题等待老师批阅' : '已提交，判分结果已出')
    submitted.value = true
    result.value = await studentApi.assignmentResult(detail.value.assignment_id)
    if (result.value) {
      for (const item of result.value.items || []) {
        if (item.answer_text) answers[item.item_no] = item.answer_text
      }
      await resolveResultPhotos(result.value)
    }
  } catch (e) { toast.error(e?.message || '提交失败') }
  finally { submitting.value = false }
}

onMounted(load)
</script>

<style scoped>
.type-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
  padding: 5px 10px;
  border-radius: 999px;
  margin-right: 10px;
  vertical-align: 3px;
  background: var(--bg2, #f1f5f9);
  color: var(--ink2, #475569);
}
.type-badge.pre_class { background: var(--purple-soft, #f3e8ff); color: var(--purple, #7c3aed); }
.type-badge.homework { background: var(--warn-soft, #fef3c7); color: var(--warn-deep, #b45309); }
.auto-tip { font-size: 11.5px; color: var(--ink3, #94a3b8); }
.auto-verdict { margin-top: 10px; padding: 8px 12px; border-radius: 8px; font-size: 12.5px; font-weight: 700; }
.auto-verdict.ok { background: var(--ok-bg, #ecfdf5); color: var(--ok-deep, #16a34a); }
.auto-verdict.bad { background: var(--err-bg, #fef2f2); color: var(--err-deep, #dc2626); }
.auto-verdict.wait { background: var(--warn-bg, #fffbeb); color: var(--warn-deep, #b45309); }
.pending-tip { font-size: 12px; color: var(--ink3, #94a3b8); }
</style>

<style scoped>
.submitted-photos, .result-photos { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.submitted-photos img, .result-photos img {
  width: 96px; height: 72px; object-fit: cover; border-radius: 8px;
  border: 1px solid var(--line); cursor: zoom-in;
}
</style>
