<template>
  <div class="view">
    <div class="greeting">
      <div class="hello">📝 体验反馈</div>
      <div class="sub">你的每次使用都已在后台真实留痕——填写反馈帮我们把产品做得更好。</div>
    </div>

    <div class="card metrics">
      <div class="card-title">我的使用档案（后台真实留痕）</div>
      <div v-if="loading" class="hint">加载中…</div>
      <template v-else-if="metrics">
        <div class="grid">
          <div class="cell"><div class="val">{{ metrics.conversations ?? 0 }}</div><div class="lab">学习会话</div></div>
          <div class="cell"><div class="val">{{ metrics.messages_from_user ?? 0 }}</div><div class="lab">我发送的消息</div></div>
          <div class="cell"><div class="val">{{ metrics.tutor_sessions_total ?? 0 }}</div><div class="lab">引导解题</div></div>
          <div class="cell"><div class="val">{{ metrics.error_records ?? 0 }}</div><div class="lab">错题收录</div></div>
        </div>
        <div class="hint" style="margin-top:10px;">
          最近活跃：{{ shortTime(metrics.last_message_at) }}；学习事件 {{ eventCount }} 次；注册于 {{ shortTime(metrics.registered_at) }}
        </div>
      </template>
      <div v-else class="hint">档案加载失败，请稍后重试。</div>
    </div>

    <form class="card form" @submit.prevent="submit">
      <div class="card-title">试用反馈（标注身份，用于作品验收留痕）</div>
      <div class="row2">
        <label>姓名<input v-model="f.name" required maxlength="20" placeholder="真实姓名" /></label>
        <label>身份标注<input v-model="f.identity_label" required maxlength="60" placeholder="如：XX中学 高二 学生" /></label>
      </div>
      <div class="row2">
        <label>学校<input v-model="f.school" maxlength="60" placeholder="选填" /></label>
        <label>年级<input v-model="f.grade" maxlength="20" placeholder="选填" /></label>
      </div>
      <div class="rates">
        <label v-for="r in rateKeys" :key="r.key" class="rate">
          <span>{{ r.label }}</span>
          <select v-model.number="f.ratings[r.key]">
            <option v-for="n in 5" :key="n" :value="n">{{ n }} 分</option>
          </select>
        </label>
        <label class="rate">
          <span>推荐意愿 (0-10)</span>
          <select v-model.number="f.nps">
            <option v-for="n in 11" :key="n - 1" :value="n - 1">{{ n - 1 }}</option>
          </select>
        </label>
        <label class="rate">
          <span>试用时长（分钟）</span>
          <input v-model.number="f.duration_minutes" type="number" min="0" max="100000" placeholder="选填" />
        </label>
      </div>
      <label class="full">使用感受<textarea v-model="f.comment" rows="3" maxlength="2000" placeholder="哪里帮到了你？哪里还别扭？"></textarea></label>
      <label class="chk"><input v-model="f.allow_showcase" type="checkbox" /> 允许将我的反馈作为作品验收案例展示（匿名可选）</label>
      <div class="actions">
        <button class="btn-primary" type="submit" :disabled="submitting">{{ submitting ? '提交中…' : submitted ? '✓ 已留痕，可更新重交' : '提交反馈' }}</button>
      </div>
      <div v-if="err" class="err">{{ err }}</div>
    </form>
  </div>
</template>

<script setup>
import { computed, reactive, ref, onMounted } from 'vue'
import { trialApi } from '@/api/trial'

const rateKeys = [
  { key: 'usability', label: '易用性' },
  { key: 'usefulness', label: '学习帮助' },
  { key: 'speed', label: '响应速度' },
  { key: 'accuracy', label: '讲解准确性' },
  { key: 'overall', label: '总体满意度' },
]

const metrics = ref(null)
const loading = ref(true)
const submitting = ref(false)
const submitted = ref(false)
const err = ref('')

const f = reactive({
  name: '',
  identity_label: '',
  school: '',
  grade: '',
  ratings: { usability: 5, usefulness: 5, speed: 4, accuracy: 5, overall: 5 },
  nps: 9,
  comment: '',
  duration_minutes: null,
  allow_showcase: true,
})

const eventCount = computed(() =>
  Object.values(metrics.value?.learning_events || {}).reduce((a, b) => a + b, 0)
)

function shortTime(iso) {
  if (!iso) return '—'
  return String(iso).slice(0, 16).replace('T', ' ')
}

onMounted(async () => {
  try {
    const res = await trialApi.getMetrics()
    metrics.value = res?.data ?? res
  } catch {
    metrics.value = null
  } finally {
    loading.value = false
  }
})

async function submit() {
  if (!f.name.trim() || !f.identity_label.trim()) { err.value = '请填写姓名与身份标注'; return }
  submitting.value = true
  err.value = ''
  try {
    await trialApi.submitFeedback({ ...f })
    submitted.value = true
  } catch (e) {
    err.value = e?.message || '提交失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.card {
  background: var(--card); border: none; border-radius: var(--radius-xl);
  box-shadow: var(--shadow-card); padding: 18px 20px; margin-bottom: 14px;
}
.card-title { font-weight: 700; font-size: 15px; color: var(--ink); margin-bottom: 12px; }
.grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.cell { background: var(--brand-faint, #f5f7ff); border-radius: var(--radius-lg, 12px); padding: 12px; text-align: center; }
.val { font-size: 22px; font-weight: 800; color: var(--brand-deep); }
.lab { font-size: 12px; color: var(--ink3); margin-top: 2px; }
.hint { font-size: 12.5px; color: var(--ink3); }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: var(--ink2); }
input, select, textarea {
  font: inherit; font-size: 13.5px; padding: 8px 10px; border-radius: var(--radius-md, 10px);
  border: 1px solid var(--line); background: #fff; color: var(--ink);
}
.rates { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 10px; }
.full { margin-bottom: 10px; }
textarea { resize: vertical; }
.chk { flex-direction: row; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink2); margin-bottom: 8px; }
.actions { display: flex; justify-content: flex-end; }
.btn-primary {
  padding: 10px 26px; border: none; border-radius: 999px; cursor: pointer;
  font: inherit; font-weight: 700; color: #fff;
  background: linear-gradient(135deg, var(--brand), var(--brand2));
}
.btn-primary:disabled { opacity: .6; cursor: default; }
.err { margin-top: 8px; font-size: 12.5px; color: var(--err); }
</style>
