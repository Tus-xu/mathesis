<template>
  <div class="h5" data-testid="h5-root">
    <!-- 加入 -->
    <section v-if="!joined" class="h5-join">
      <div class="h5-logo">🎓</div>
      <h1 class="h5-title">课堂互动</h1>
      <p class="h5-sub">输入老师大屏上的 6 位课堂码，无需账号</p>
      <input v-model="joinCode" class="h5-input h5-input--code" inputmode="numeric" maxlength="6" placeholder="课堂码" data-testid="h5-join-code" />
      <input v-model="studentName" class="h5-input" maxlength="12" placeholder="你的姓名" data-testid="h5-join-name" />
      <button class="h5-btn" data-testid="h5-join-go" :disabled="joining" @click="join">{{ joining ? '加入中…' : '加入课堂' }}</button>
      <p v-if="joinError" class="h5-err" data-testid="h5-join-err">{{ joinError }}</p>
    </section>

    <!-- 课堂中 -->
    <section v-else class="h5-room">
      <header class="h5-head">
        <span class="h5-head__class">{{ className }}</span>
        <span class="h5-head__me">{{ studentName }}</span>
      </header>

      <template v-if="sessionEnded">
        <div class="h5-card h5-end" data-testid="h5-ended">
          <div style="font-size: 40px">下课啦</div>
          <p style="margin: 8px 0 0; font-size: 13px; color: #64748b">{{ summaryInsight || '本节课已结束，感谢你的参与。' }}</p>
        </div>
      </template>

      <template v-else-if="!currentActivity">
        <div class="h5-card h5-wait" data-testid="h5-waiting">
          <span class="h5-pulse">●</span> 已加入，等待老师发题…
        </div>
      </template>

      <template v-else>
        <div class="h5-card" data-testid="h5-activity">
          <div class="h5-card__tag">{{ currentActivity.status === 'collecting' ? '● 答题中' : currentActivity.status === 'locked' ? '▪ 已停止' : '◈ 已公布' }}</div>
          <div class="h5-stem" data-testid="h5-stem" v-html="stemHtml" />
          <!-- 选择题作答 -->
          <div v-if="options.length" class="h5-options">
            <button
              v-for="(o, i) in options" :key="i"
              class="h5-opt" :class="{ 'is-picked': myAnswer === String.fromCharCode(65 + i), 'is-right': revealed && currentAnswer === String.fromCharCode(65 + i), 'is-wrong': revealed && myAnswer === String.fromCharCode(65 + i) && myAnswer !== currentAnswer }"
              :data-testid="`h5-opt-${i}`" :disabled="currentActivity.status !== 'collecting' || mySubmitted"
              @click="submit(String.fromCharCode(65 + i))"
            >
              <b>{{ String.fromCharCode(65 + i) }}</b>
            </button>
          </div>
          <!-- 图片投稿作答 -->
          <div v-else-if="currentActivity.kind === 'photo_submit'" class="h5-photo">
            <input type="file" accept="image/*" data-testid="h5-photo-input" :disabled="currentActivity.status !== 'collecting' || mySubmitted" @change="onPhoto" />
            <p class="h5-photo__tip">拍下你的解答过程上传（原图直存）</p>
            <p v-if="photoNote" class="h5-photo__tip" style="color:#b45309">{{ photoNote }}</p>
          </div>
          <div v-if="mySubmitted" class="h5-receipt" data-testid="h5-receipt">✓ 已提交{{ duplicateHint ? '（重复提交已忽略）' : '' }}，等待老师公布</div>
          <template v-if="revealed">
            <div class="h5-reveal" data-testid="h5-reveal">
              <span v-if="currentAnswer" class="h5-reveal__answer">正确答案：{{ currentAnswer }}</span>
              <span class="h5-reveal__mine" :class="myCorrect ? 'is-ok' : 'is-no'">{{ mySubmitted ? (myCorrect ? '你答对了 🎉' : '再想想，看老师讲评') : '你未作答本题' }}</span>
            </div>
            <div v-if="distributionText" class="h5-dist" data-testid="h5-dist">{{ distributionText }}</div>
          </template>
        </div>
        <div class="h5-stats" data-testid="h5-answered">全班已作答 {{ answeredCount }} 人</div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 学生课堂 H5（M2-A · 课堂域唯一新增页，02-ARCHITECTURE §12）
 * 无账号：join_code + 姓名 → 课堂作用域 token → 学生 SSE（学生视角投影）。
 * 状态一律来自服务端 snapshot/事件（applyStudentEvent reducer，G7）；
 * 作答经 submitResponse（课堂 token；服务端幂等，重复提交返回首次结果）。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { v3Api } from '@/api/teacherV3'
import { renderLatex } from '@/components/mathx/latex'
import { applyStudentEvent, studentStateFromSnapshot, type StudentClassroomState } from '@/pages/teacher-v3/classroomReducer'

const joinCode = ref('')
const studentName = ref('')
const joining = ref(false)
const joinError = ref('')
const joined = ref(false)
const token = ref('')
const myAnswer = ref('')
const duplicateHint = ref(false)
const photoNote = ref('')
const uploading = ref(false)

const state = ref<StudentClassroomState | null>(null)
let streamAbort: (() => void) | null = null

const className = computed(() => state.value?.className || '')
const sessionEnded = computed(() => state.value?.sessionStatus === 'ended')
const summaryInsight = computed(() => state.value?.summary?.insight || '')
const currentActivity = computed(() => {
  const acts = state.value?.activities || []
  const live = acts.find((a) => a.status === 'collecting' || a.status === 'locked')
  const latest = [...acts].sort((a, b) => b.ord - a.ord)[0]
  return live || (latest && (latest.status === 'revealed' || latest.status === 'completed') ? latest : null) || null
})
const questionBrief = computed(() => (currentActivity.value?.question_id ? state.value?.questions[currentActivity.value.question_id] : null))
const stemHtml = computed(() => renderLatex(questionBrief.value?.stem_latex || '老师正在准备题目…'))
const options = computed(() => questionBrief.value?.options || [])
const revealed = computed(() => currentActivity.value?.status === 'revealed' || currentActivity.value?.status === 'completed')
const currentAnswer = computed(() => questionBrief.value?.answer || (currentActivity.value?.config as any)?.answer || '')
const answeredCount = computed(() => currentActivity.value?.stats?.answered ?? 0)
const mySubmitted = computed(() => !!currentActivity.value && !!state.value?.myAnsweredActivityIds.includes(currentActivity.value.activity_id))
const myCorrect = computed(() => !!myAnswer.value && myAnswer.value === currentAnswer.value)
const distributionText = computed(() => {
  const d = currentActivity.value?.stats?.distribution
  if (!d || !Object.keys(d).length) return ''
  return '作答分布：' + Object.entries(d).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v} 人`).join(' · ')
})

async function join() {
  joinError.value = ''
  if (!/^\d{6}$/.test(joinCode.value)) { joinError.value = '请输入 6 位数字课堂码'; return }
  if (!studentName.value.trim()) { joinError.value = '请填写姓名'; return }
  joining.value = true
  try {
    const r = await v3Api.classroom.join({ join_code: joinCode.value.trim(), student_name: studentName.value.trim() })
    token.value = r.data.token
    // 占位状态：等学生流首个 snapshot 事件整体重置（§12.4）
    state.value = {
      sessionId: r.data.session_id, participantId: r.data.participant_id, sessionStatus: 'open',
      joinCode: joinCode.value.trim(), className: r.data.session_name || '', activities: [], questions: {},
      myAnsweredActivityIds: [], seq: 0, summary: null,
    }
    joined.value = true
    subscribe(r.data.session_id)
  } catch (e: any) {
    joinError.value = e?.message || '加入失败：请核对课堂码'
  } finally {
    joining.value = false
  }
}

function subscribe(sessionId: string) {
  const { abort, finished } = v3Api.classroom.studentStream(sessionId, token.value, (event, data) => {
    if (state.value) state.value = applyStudentEvent(state.value, { event, data })
  })
  streamAbort = abort
  finished.catch(() => { /* 断线由 fetch-event-source 自动重连（Last-Event-ID 补拉）；多次失败时刷新页面即可 */ })
}

async function submit(answer: string) {
  const s = state.value
  const act = currentActivity.value
  if (!s || !act || act.status !== 'collecting' || mySubmitted.value) return
  myAnswer.value = answer
  try {
    const r = await v3Api.classroom.submitResponse(s.sessionId, { activity_id: act.activity_id, answer: { choice: answer } }, { token: token.value })
    duplicateHint.value = !!r.data.duplicate
  } catch (e: any) {
    duplicateHint.value = false
  }
}

function onPhoto(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const s = state.value
  const act = currentActivity.value
  if (!s || !act || act.status !== 'collecting' || mySubmitted.value) return
  uploading.value = true
  // 拍照投稿需对象存储直传（学生端 presign 通道未建，S16 审计：dataURL 超 image_key 列宽必 500）——
  // 诚实提示而非把 base64 塞给后端炸会话
  uploading.value = false
  photoNote.value = '拍照投稿通道即将开放（需直传改造），本题请等待老师讲解'
}

onBeforeUnmount(() => { streamAbort?.(); streamAbort = null })
</script>

<style scoped>
.h5 {
  min-height: 100vh;
  background: linear-gradient(180deg, #eef3fa 0%, #f8fafc 60%);
  font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: #1a2332;
}
.h5-join {
  max-width: 420px; margin: 0 auto; padding: 64px 24px;
  display: flex; flex-direction: column; align-items: center; gap: 12px; text-align: center;
}
.h5-logo { font-size: 52px; }
.h5-title { margin: 0; font-size: 22px; font-weight: 800; }
.h5-sub { margin: 0 0 10px; font-size: 13px; color: #64748b; }
.h5-input {
  width: 100%; height: 48px; border: 1px solid #d7dee8; border-radius: 12px;
  padding: 0 14px; font-size: 17px; outline: none; background: #fff; box-sizing: border-box;
}
.h5-input:focus { border-color: #0f4787; box-shadow: 0 0 0 3px rgba(15, 71, 135, .1); }
.h5-input--code { letter-spacing: 8px; text-align: center; font-weight: 800; font-size: 22px; }
.h5-btn {
  width: 100%; height: 48px; border: none; border-radius: 12px; cursor: pointer;
  background: linear-gradient(135deg, #0f4787, #1663b0); color: #fff; font-size: 16px; font-weight: 700;
}
.h5-btn:disabled { opacity: .6; }
.h5-err { margin: 0; font-size: 12.5px; color: #c9531f; }

.h5-room { max-width: 520px; margin: 0 auto; padding: 16px 16px 40px; }
.h5-head {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; color: #4a5568; padding: 4px 2px 12px;
}
.h5-head__me { color: #8899aa; }
.h5-card {
  background: #fff; border: 1px solid #e2e8f0; border-radius: 16px; padding: 18px 16px;
  box-shadow: 0 4px 16px rgba(15, 71, 135, .06);
}
.h5-card__tag { display: inline-block; font-size: 11px; color: #0f4787; background: #e8f0fb; border-radius: 999px; padding: 2px 10px; margin-bottom: 10px; }
.h5-wait { text-align: center; font-size: 14px; color: #4a5568; }
.h5-end { text-align: center; font-size: 17px; font-weight: 700; }
.h5-pulse { color: #d4a54a; animation: h5pulse 1.4s infinite; }
@keyframes h5pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
.h5-stem { font-size: 16px; line-height: 1.7; margin-bottom: 14px; word-break: break-all; }
.h5-options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.h5-opt {
  height: 56px; border: 2px solid #d7dee8; border-radius: 12px; background: #fff;
  font-size: 20px; font-weight: 800; color: #334; cursor: pointer; transition: all .15s;
}
.h5-opt:disabled { cursor: default; opacity: .75; }
.h5-opt.is-picked { border-color: #0f4787; background: #e8f0fb; }
.h5-opt.is-right { border-color: #1f8a5f; background: #ecfdf5; color: #1f8a5f; }
.h5-opt.is-wrong { border-color: #c9531f; background: #fdf1ef; color: #c9531f; }
.h5-photo { display: flex; flex-direction: column; gap: 8px; }
.h5-photo__tip { margin: 0; font-size: 12px; color: #8899aa; }
.h5-receipt { margin-top: 12px; font-size: 13px; color: #1f8a5f; font-weight: 600; }
.h5-reveal { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.h5-reveal__answer { font-size: 14px; font-weight: 800; color: #1f8a5f; }
.h5-reveal__mine { font-size: 13px; border-radius: 999px; padding: 3px 12px; }
.h5-reveal__mine.is-ok { background: #ecfdf5; color: #1f8a5f; }
.h5-reveal__mine.is-no { background: #fdf1ef; color: #c9531f; }
.h5-dist { margin-top: 10px; font-size: 12.5px; color: #64748b; }
.h5-stats { margin-top: 12px; text-align: center; font-size: 12px; color: #8899aa; }
</style>
