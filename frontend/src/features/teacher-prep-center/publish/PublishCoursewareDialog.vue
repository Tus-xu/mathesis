<script setup lang="ts">
/**
 * 发布到班级课堂（Phase D）。
 *
 * 链路：教师确认的 deck 快照 → 8100 `POST /prep/sessions/{id}/publish-classroom`
 * → 8000 创建 `classroom_courseware` artifact（preparing）→ 逐页讲稿/随堂练习后台准备
 * → `published`。学生只在 published 后看到；本对话框轮询 8000 详情（2s，最多 90s）。
 *
 * 反空壳铁律：
 * - 成功只认 8000 详情的 `status === 'published'`，受理（preparing）不冒充已发布；
 * - 90s 未完成 → 诚实提示「后台仍在准备」，可继续等待或先去课堂；
 * - 失败显示服务端真实原因，不生成假 courseware_id，不静默成功；
 * - 班级目录复用既有 8000 `classes/mine`（经 prepSession store），不新建第二套班级 API。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api8000 } from '@/api/api8000'
import type { V3ClassInfo } from '@/types/teacherV3'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import {
  PrepCenterApiError,
  publishClassroom,
  type PublishClassroomResult,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import type { ApiCoursewareDetail } from '@/api/api8000'

const props = defineProps<{
  open: boolean
  sessionId: string
  deckId?: string
  deckTitle?: string
  /** 显式传入班级目录（不传则读 prepSession store 的真实班级）。 */
  classes?: V3ClassInfo[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'published', payload: { coursewareId: string }): void
}>()

const router = useRouter()
const prep = usePrepSessionStore()

type Phase = 'form' | 'submitting' | 'preparing' | 'timeout' | 'published' | 'failed'

const POLL_INTERVAL_MS = 2000
const POLL_MAX_MS = 90_000

const phase = ref<Phase>('form')
const classId = ref('')
const titleText = ref('')
const includePractice = ref(true)
const error = ref('')
const warnings = ref<string[]>([])
const coursewareId = ref('')
const result = ref<PublishClassroomResult | null>(null)
const detail = ref<ApiCoursewareDetail | null>(null)
const pollError = ref('')
const classesLoading = ref(false)

let pollTimer: number | undefined
let pollStartedAt = 0

const classOptions = computed<V3ClassInfo[]>(() =>
  Array.isArray(props.classes) ? props.classes : prep.classes,
)
const selectedClassName = computed(
  () => classOptions.value.find((c) => c.class_id === classId.value)?.name || '',
)
const submitting = computed(() => phase.value === 'submitting')
const canSubmit = computed(
  () =>
    !!props.sessionId &&
    !!classId.value &&
    !submitting.value &&
    phase.value !== 'preparing' &&
    phase.value !== 'published',
)

/** 准备进度（只呈现服务端真实 preparation；取不到就不编造 n/N）。 */
const preparationText = computed(() => {
  if (phase.value === 'timeout') {
    return '已超过 90 秒仍在后台准备讲稿与随堂练习；发布已受理，完成后学生即可看到。'
  }
  const p = detail.value?.preparation
  if (p && typeof p.done === 'number' && typeof p.total === 'number' && p.total > 0) {
    const stage = typeof p.stage === 'string' && p.stage ? `讲稿/练习 ${p.done}/${p.total} 页` : `准备 ${p.done}/${p.total}`
    return `${stage} · 完成后学生端可见`
  }
  if (p && typeof p.stage === 'string' && p.stage) return `${p.stage} · 完成后学生端可见`
  return '正在准备逐页讲稿与随堂练习…'
})

const publishedTitle = computed(() => detail.value?.title || result.value?.title || titleText.value || '课堂课件')

function errText(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    if (typeof e.detail === 'string' && e.detail.trim()) return e.detail
    if (e.kind === 'network') return '网络连接失败，请确认后端已启动后重试'
    if (e.kind === 'forbidden') return '没有权限发布到该班级（仅该班任课教师可发布）'
    if (e.kind === 'conflict') return e.message || '发布状态冲突，请刷新后重试'
    return e.message || '发布失败，请稍后重试'
  }
  if (e instanceof Error && e.message) return e.message
  return '发布失败，请稍后重试'
}

function stopPolling() {
  if (pollTimer) {
    window.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function preferredClassId(list: V3ClassInfo[]): string {
  const sessionClass = prep.current?.session.class_id || ''
  if (sessionClass && list.some((c) => c.class_id === sessionClass)) return sessionClass
  return list[0]?.class_id || ''
}

async function ensureClasses() {
  if (Array.isArray(props.classes)) return
  classesLoading.value = true
  try {
    // fetchClasses 内部已做失败降级（置空 + 已加载），不阻塞表单；失败时选项为空即不让提交。
    await prep.fetchClasses()
  } finally {
    classesLoading.value = false
  }
}

function init() {
  stopPolling()
  phase.value = 'form'
  error.value = ''
  warnings.value = []
  coursewareId.value = ''
  result.value = null
  detail.value = null
  pollError.value = ''
  includePractice.value = true
  titleText.value = (props.deckTitle || prep.current?.session.title || '').trim()
  classId.value = preferredClassId(classOptions.value)
  void ensureClasses()
}

watch(
  () => props.open,
  (open) => {
    if (open) init()
    else stopPolling()
  },
  { immediate: true },
)
watch(classOptions, (list) => {
  if (!props.open) return
  if (!classId.value && list.length) classId.value = preferredClassId(list)
})

async function submit() {
  if (!canSubmit.value) return
  phase.value = 'submitting'
  error.value = ''
  warnings.value = []
  pollError.value = ''
  try {
    const body = {
      class_id: classId.value,
      ...(props.deckId ? { deck_id: props.deckId } : {}),
      ...(titleText.value.trim() ? { title: titleText.value.trim() } : {}),
      include_practice: includePractice.value,
    }
    const r = await publishClassroom(props.sessionId, body)
    result.value = r
    warnings.value = Array.isArray(r.warnings) ? r.warnings : []
    coursewareId.value = r.courseware_id
    if (!r.courseware_id) {
      // 没有 courseware_id 就不是成功受理：如实报错，不伪造。
      phase.value = 'failed'
      error.value = '服务端未返回课件编号（发布未受理），请重试或联系管理员'
      return
    }
    if (r.status === 'published') {
      phase.value = 'published'
      emit('published', { coursewareId: r.courseware_id })
      return
    }
    phase.value = 'preparing'
    pollStartedAt = Date.now()
    pollTimer = window.setTimeout(() => void pollOnce(), POLL_INTERVAL_MS)
  } catch (e) {
    phase.value = 'failed'
    error.value = errText(e)
  }
}

async function pollOnce() {
  if (!props.open || !coursewareId.value) return
  if (Date.now() - pollStartedAt >= POLL_MAX_MS) {
    phase.value = 'timeout'
    stopPolling()
    return
  }
  try {
    const d = await api8000.coursewares.get(coursewareId.value)
    pollError.value = ''
    detail.value = d
    if (Array.isArray(d.warnings) && d.warnings.length) warnings.value = d.warnings
    if (d.status === 'published') {
      phase.value = 'published'
      stopPolling()
      emit('published', { coursewareId: coursewareId.value })
      return
    }
    if (d.status === 'failed' || d.status === 'archived') {
      phase.value = 'failed'
      error.value =
        d.status === 'archived'
          ? '该课件已被下架，学生不再可见。'
          : `后台准备失败${d.warnings?.length ? `：${d.warnings.join('；')}` : '，请稍后重试'}`
      stopPolling()
      return
    }
  } catch (e) {
    // 瞬时读取失败不冒充发布失败：提示正在重试，继续轮询到 90s 上限。
    pollError.value = `准备状态暂时读取失败，正在重试…（${errText(e)}）`
  }
  pollTimer = window.setTimeout(() => void pollOnce(), POLL_INTERVAL_MS)
}

function resumePolling() {
  if (!coursewareId.value) return
  phase.value = 'preparing'
  pollStartedAt = Date.now()
  pollError.value = ''
  stopPolling()
  pollTimer = window.setTimeout(() => void pollOnce(), 0)
}

function requestClose() {
  if (submitting.value) return // 提交中不关闭：避免请求在途时误以为未发布
  stopPolling()
  emit('close')
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) requestClose()
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
)

function gotoClassroom() {
  stopPolling()
  emit('close')
  void router.push('/teacher-v3/classroom')
}

onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="pc-pub__mask" @click.self="requestClose">
      <div
        class="pc-pub"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pc-pub-title"
        data-testid="publish-dialog"
        :data-phase="phase"
      >
        <header class="pc-pub__head">
          <div class="pc-pub__head-text">
            <h3 id="pc-pub-title" class="pc-pub__title">发布到班级课堂</h3>
            <p class="pc-pub__sub">
              {{ deckTitle || '当前课件' }} · 学生将在「课堂 · 老师课堂」跟 AI 主讲逐页学习
            </p>
          </div>
          <button type="button" class="pc-pub__x" aria-label="关闭" @click="requestClose">×</button>
        </header>

        <!-- 已发布（只认 8000 详情 published） -->
        <div v-if="phase === 'published'" class="pc-pub__body" data-testid="publish-success">
          <div class="pc-pub__success">
            <span class="pc-pub__success-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26">
                <circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="1.6" />
                <path d="M7.6 12.3 10.7 15.4 16.4 9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <div>
              <div class="pc-pub__success-title">已发布 · {{ publishedTitle }}</div>
              <p class="pc-pub__success-sub">
                {{ selectedClassName || detail?.class_name || '目标班级' }} 的学生现在可以在「课堂」看到这份课件。
              </p>
            </div>
          </div>
          <p v-if="detail?.degraded" class="pc-pub__warn">
            部分讲稿/练习由确定性内容兜底（AI 生成未全部完成），学生仍可正常学习。
          </p>
          <ul v-if="warnings.length" class="pc-pub__warnings">
            <li v-for="(w, i) in warnings" :key="`w-${i}`">{{ w }}</li>
          </ul>
          <footer class="pc-pub__foot">
            <span class="pc-pub__note">发布是快照：重发会创建新课件，不会覆盖学生已学进度。</span>
            <div class="pc-pub__spacer" />
            <button type="button" class="pc-pub__btn" @click="requestClose">留在本页</button>
            <button type="button" class="pc-pub__btn is-primary" data-testid="publish-goto-classroom" @click="gotoClassroom">
              查看课堂
            </button>
          </footer>
        </div>

        <!-- 准备中 / 超时（不冒充已完成） -->
        <div v-else-if="phase === 'preparing' || phase === 'timeout'" class="pc-pub__body">
          <div class="pc-pub__state" data-testid="publish-preparing">
            <span v-if="phase === 'preparing'" class="pc-pub__spinner" aria-hidden="true" />
            <span v-else class="pc-pub__state-dot" aria-hidden="true">…</span>
            <div>
              <b>{{ phase === 'timeout' ? '发布已受理，后台仍在准备' : '已提交，正在准备课堂…' }}</b>
              <p class="pc-pub__state-sub">{{ preparationText }}</p>
              <p v-if="pollError" class="pc-pub__hint">{{ pollError }}</p>
            </div>
          </div>
          <ul v-if="warnings.length" class="pc-pub__warnings">
            <li v-for="(w, i) in warnings" :key="`w-${i}`">{{ w }}</li>
          </ul>
          <footer class="pc-pub__foot">
            <span class="pc-pub__note">准备期间可关闭：后台继续生成，学生只会在发布完成后看到。</span>
            <div class="pc-pub__spacer" />
            <button type="button" class="pc-pub__btn" @click="requestClose">关闭</button>
            <button
              v-if="phase === 'timeout'"
              type="button"
              class="pc-pub__btn"
              data-testid="publish-timeout-classroom"
              @click="gotoClassroom"
            >先去课堂</button>
            <button
              v-if="phase === 'timeout'"
              type="button"
              class="pc-pub__btn is-primary"
              @click="resumePolling"
            >继续等待</button>
          </footer>
        </div>

        <!-- 表单 / 失败（失败回表单，可改参数重试） -->
        <div v-else class="pc-pub__body">
          <p v-if="error" class="pc-pub__error" role="alert" data-testid="publish-error">{{ error }}</p>

          <label class="pc-pub__field">
            <span class="pc-pub__label">选择班级</span>
            <select v-model="classId" class="pc-pub__input" data-testid="publish-class" :disabled="submitting">
              <option value="" disabled>{{ classesLoading ? '正在加载班级…' : '请选择班级' }}</option>
              <option v-for="c in classOptions" :key="c.class_id" :value="c.class_id">
                {{ c.name }}<template v-if="c.students"> · {{ c.students }} 人</template>
              </option>
            </select>
          </label>
          <p v-if="!classesLoading && !classOptions.length" class="pc-pub__hint" data-testid="publish-no-class">
            还没有可发布的班级：先到「班级管理」建班并确认学生加入。
          </p>

          <label class="pc-pub__field">
            <span class="pc-pub__label">课件标题</span>
            <input
              v-model="titleText"
              class="pc-pub__input"
              type="text"
              maxlength="80"
              placeholder="学生端显示的课堂标题"
              data-testid="publish-title"
              :disabled="submitting"
            />
          </label>

          <label class="pc-pub__check">
            <input v-model="includePractice" type="checkbox" data-testid="publish-include-practice" :disabled="submitting" />
            <span>
              <b>包含随堂练习</b>
              <em>学生每看一页后可作答；正确率实时回流到「课堂 · 双师课堂」。</em>
            </span>
          </label>

          <p v-if="!sessionId" class="pc-pub__error">缺少备课会话，无法发布。请从备课工作台重新进入。</p>

          <footer class="pc-pub__foot">
            <span class="pc-pub__note">发布的是你当前确认的课件快照；不会改动备课内容。</span>
            <div class="pc-pub__spacer" />
            <button type="button" class="pc-pub__btn" :disabled="submitting" @click="requestClose">取消</button>
            <button
              type="button"
              class="pc-pub__btn is-primary"
              :disabled="!canSubmit"
              data-testid="publish-submit"
              @click="submit"
            >{{ submitting ? '提交中…' : '发布到班级' }}</button>
          </footer>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pc-pub__mask {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.38);
  backdrop-filter: blur(2px);
}
.pc-pub {
  width: min(560px, 100%);
  max-height: calc(100vh - 48px);
  overflow: auto;
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-panel, 14px);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
  font-family: var(--pc-font, -apple-system, 'Microsoft YaHei', sans-serif);
  color: var(--pc-text, #111827);
}
.pc-pub__head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--pc-line, #e5e9f2);
}
.pc-pub__head-text { flex: 1; min-width: 0; }
.pc-pub__title { margin: 0; font-size: 17px; font-weight: 700; }
.pc-pub__sub { margin: 4px 0 0; font-size: 12.5px; line-height: 1.55; color: var(--pc-text-muted, #6b7280); }
.pc-pub__x {
  flex: none;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--pc-text-muted, #6b7280);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
}
.pc-pub__x:hover { background: rgba(15, 23, 42, 0.06); }
.pc-pub__body { padding: 16px 20px 18px; }
.pc-pub__field { display: block; margin-bottom: 14px; }
.pc-pub__label { display: block; margin-bottom: 6px; font-size: 12.5px; font-weight: 600; color: var(--pc-text-2, #1f2937); }
.pc-pub__input {
  width: 100%;
  box-sizing: border-box;
  height: 38px;
  padding: 0 12px;
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-btn, 8px);
  background: #fff;
  font-size: 13.5px;
  font-family: inherit;
  color: var(--pc-text, #111827);
  outline: none;
}
.pc-pub__input:focus { border-color: var(--pc-primary, #2563eb); box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
.pc-pub__input:disabled { background: var(--pc-bg, #f6f8fb); color: var(--pc-text-muted, #6b7280); }
.pc-pub__check {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-card, 12px);
  background: var(--pc-bg, #f6f8fb);
  font-size: 13px;
  cursor: pointer;
}
.pc-pub__check input { margin-top: 2px; accent-color: var(--pc-primary, #2563eb); }
.pc-pub__check b { display: block; font-weight: 600; }
.pc-pub__check em { display: block; margin-top: 2px; font-style: normal; font-size: 12px; line-height: 1.55; color: var(--pc-text-muted, #6b7280); }
.pc-pub__hint { margin: 8px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--pc-warning, #d97706); }
.pc-pub__error {
  margin: 0 0 14px;
  padding: 9px 12px;
  border: 1px solid #f3c1c1;
  border-radius: 8px;
  background: #fdeeee;
  font-size: 12.5px;
  line-height: 1.6;
  color: #b42323;
}
.pc-pub__foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--pc-line, #e5e9f2);
}
.pc-pub__note { flex: 1; min-width: 0; font-size: 11.5px; line-height: 1.5; color: var(--pc-text-faint, #9ca3af); }
.pc-pub__spacer { flex: none; }
.pc-pub__btn {
  flex: none;
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: var(--pc-radius-btn, 8px);
  background: #fff;
  color: var(--pc-text-2, #1f2937);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-pub__btn:hover:not(:disabled) { border-color: var(--pc-primary, #2563eb); color: var(--pc-primary, #2563eb); }
.pc-pub__btn.is-primary {
  border-color: transparent;
  background: var(--pc-primary, #2563eb);
  color: #fff;
}
.pc-pub__btn.is-primary:hover:not(:disabled) { background: var(--pc-primary-hover, #1d4fd8); color: #fff; }
.pc-pub__btn:disabled { opacity: 0.55; cursor: not-allowed; }
.pc-pub__state {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  border: 1px solid #dbe6fb;
  border-radius: var(--pc-radius-card, 12px);
  background: #f2f7ff;
}
.pc-pub__state b { font-size: 14px; }
.pc-pub__state-sub { margin: 4px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--pc-text-2, #1f2937); }
.pc-pub__state-dot { flex: none; width: 18px; text-align: center; color: var(--pc-warning, #d97706); font-weight: 700; }
.pc-pub__spinner {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border: 2px solid var(--pc-primary, #2563eb);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-pub-spin 0.8s linear infinite;
}
@keyframes pc-pub-spin { to { transform: rotate(360deg); } }
.pc-pub__success { display: flex; align-items: flex-start; gap: 12px; padding: 14px; border: 1px solid #c7ebd3; border-radius: var(--pc-radius-card, 12px); background: #f0faf3; }
.pc-pub__success-icon { flex: none; color: var(--pc-success, #16a34a); }
.pc-pub__success-title { font-size: 14.5px; font-weight: 700; }
.pc-pub__success-sub { margin: 4px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--pc-text-2, #1f2937); }
.pc-pub__warn { margin: 10px 0 0; font-size: 12.5px; line-height: 1.6; color: var(--pc-warning, #d97706); }
.pc-pub__warnings { margin: 10px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.6; color: var(--pc-text-muted, #6b7280); }
</style>
