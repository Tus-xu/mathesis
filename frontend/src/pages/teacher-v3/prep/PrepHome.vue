<template>
  <div class="ph">
    <div class="ailp-blob ailp-blob--one"></div>
    <div class="ailp-blob ailp-blob--two"></div>
    <div class="ailp-blob ailp-blob--three"></div>

    <main class="ph-main">
      <!-- 欢迎 -->
      <div class="ph-welcome ailp-fade">
        <p class="ph-welcome__hi">{{ copyFor('prep.home.greeting') }}，{{ teacherName }}</p>
        <h1 class="ph-welcome__title">{{ copyFor('prep.home.title') }}</h1>
        <p class="ph-welcome__sub">{{ copyFor('prep.home.subtitle') }}</p>
      </div>

      <!-- 大输入框 -->
      <div class="ph-inputwrap ailp-fade ph-inputwrap--delayed">
        <div class="ph-input">
          <button class="ph-input__clip" title="添加已授权资料（真实上传）" type="button" data-testid="ailp-home-attach" :disabled="attState.status === 'uploading'" @click="requestAttachment">
            <n-icon :size="18"><AttachOutline /></n-icon>
          </button>
          <input ref="fileInput" type="file" accept=".ppt,.pptx,.pdf,.doc,.docx,.xls,.xlsx,.jpg,.jpeg,.png,.md,.txt" hidden data-testid="ailp-home-file" @change="onFilePicked">
          <textarea
            v-model="draft" rows="1" class="ph-input__ta"
            :placeholder="copyFor('prep.home.placeholder')"
            data-testid="ailp-home-input"
            @keydown.enter.prevent="submit"
          />
          <button class="ph-input__send" data-testid="ailp-home-send" :disabled="!draft.trim() || busy" @click="submit" aria-label="开始备课">
            <n-icon :size="18"><SendOutline /></n-icon>
          </button>
        </div>
        <label v-if="classes.length" class="ph-class-picker">
          <span>{{ copyFor('prep.home.class_label') }}</span>
          <select v-model="selectedClassId" data-testid="prep-home-class">
            <option value="">{{ copyFor('prep.home.class_optional') }}</option>
            <option v-for="item in classes" :key="item.class_id" :value="item.class_id">{{ item.name }}（{{ item.students }} 人）</option>
          </select>
        </label>
        <p v-else-if="classesLoaded && classesLoadError" class="ph-input-error" data-testid="prep-home-class-error">{{ classesLoadError }}</p>
        <p v-else-if="classesLoaded" class="ph-input-hint" data-testid="prep-home-class-empty">{{ copyFor('prep.home.class_optional_hint') }}</p>
        <p v-if="attState.status === 'uploading'" class="ph-input-status" role="status" data-testid="ailp-home-att-uploading">正在上传「{{ attState.name }}」…</p>
        <p v-else-if="attState.status === 'error'" class="ph-input-error" data-testid="ailp-home-att-error">资料上传失败：{{ attState.error }}</p>
        <p v-else-if="attState.status === 'done'" class="ph-input-status" role="status" data-testid="ailp-home-att-chip">📎 {{ attState.name }}（{{ attState.sizeLabel }}）已上传，将随消息一并提交</p>
        <p v-if="busy" class="ph-input-status" role="status">{{ copyFor('prep.home.creating') }}</p>
        <div class="ph-hints">
          <span><n-icon :size="14"><MicOutline /></n-icon> {{ copyFor('prep.home.voice_hint') }}</span><i />
          <span><n-icon :size="14"><AttachOutline /></n-icon> {{ copyFor('prep.home.attachment_hint') }}</span><i />
          <button class="ph-hints__link" @click="fillIntent(copyFor('prep.home.intent.textbook'))"><n-icon :size="14"><BookOutline /></n-icon> {{ copyFor('prep.home.textbook_hint') }}</button>
        </div>
      </div>

      <!-- 快捷四卡 -->
      <div class="ph-cards ailp-fade ph-cards--delayed">
        <button class="ph-card" data-testid="ailp-card-textbook" @click="fillIntent(copyFor('prep.home.intent.textbook'))">
          <div class="ph-card__ic ph-card__ic--primary"><n-icon :size="20"><BookOutline /></n-icon></div>
          <h3>{{ copyFor('prep.home.card.textbook.title') }}</h3><p>{{ copyFor('prep.home.card.textbook.desc') }}</p>
        </button>
        <button class="ph-card" data-testid="ailp-card-upload" @click="requestAttachment">
          <div class="ph-card__ic ph-card__ic--accent"><n-icon :size="20"><CloudUploadOutline /></n-icon></div>
          <h3>{{ copyFor('prep.home.card.attachment.title') }}</h3><p>{{ copyFor('prep.home.card.attachment.desc') }}</p>
        </button>
        <button class="ph-card" data-testid="ailp-card-templates" @click="fillIntent(copyFor('prep.home.intent.template'))">
          <div class="ph-card__ic ph-card__ic--violet"><n-icon :size="20"><GridOutline /></n-icon></div>
          <h3>{{ copyFor('prep.home.card.template.title') }}</h3><p>{{ copyFor('prep.home.card.template.desc') }}</p>
        </button>
        <button class="ph-card" data-testid="ailp-card-continue" @click="fillIntent(copyFor('prep.home.intent.continue'))">
          <div class="ph-card__ic ph-card__ic--amber"><n-icon :size="20"><ArrowUndoOutline /></n-icon></div>
          <h3>{{ copyFor('prep.home.card.continue.title') }}</h3><p>{{ copyFor('prep.home.card.continue.desc') }}</p>
        </button>
      </div>

      <!-- 最近备课：历史教案列表已随「教案预览」一起下线，这里不再提供入口。
           原入口打开的 `PrepEditor.vue`（只读教案外壳）已删除。 -->
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * PrepHome —— AI 对话首页（P，用户定稿 HTML 移植）
 * 大输入框 + 快捷四卡 + 最近备课；输入提交 = 提取课题 + 带要求进「AI 生成」。
 */
import { computed, onMounted, ref } from 'vue'
import { NIcon } from 'naive-ui'
import { ArrowUndoOutline, AttachOutline, BookOutline, CloudUploadOutline, GridOutline, MicOutline, SendOutline } from '@vicons/ionicons5'
import { v3Api } from '@/api/teacherV3'
import { presignUpload } from '@/api/teacherV3Upload'
import { useAuthStore } from '@/stores/auth'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const props = withDefaults(defineProps<{ busy?: boolean }>(), { busy: false })
const emit = defineEmits<{
  (e: 'submit', payload: { message: string; classId?: string }): void
}>()

const auth = useAuthStore()
const draft = ref('')
const classes = ref<{ class_id: string; name: string; students: number }[]>([])
const selectedClassId = ref('')
const classesLoaded = ref(false)
const classesLoadError = ref('')
// R3：添加已授权资料 = 真实上传（presign → MinIO 直传 → key）；仅消息文本引用，不进 localStorage
const fileInput = ref<HTMLInputElement | null>(null)
type AttState = { status: 'idle' | 'uploading' | 'done' | 'error'; name: string; sizeLabel: string; error: string }
const attState = ref<AttState>({ status: 'idle', name: '', sizeLabel: '', error: '' })
const teacherName = computed(() => {
  const name = typeof auth.nickname === 'string' ? auth.nickname.trim() : ''
  return name && name !== '同学' ? name : '老师'
})

function submit() {
  const t = draft.value.trim()
  if (!t || props.busy) return
  const withAttachment = attState.value.status === 'done' ? `${t}（附带资料：${attState.value.name}）` : t
  emit('submit', { message: withAttachment, classId: selectedClassId.value || undefined })
  attState.value = { status: 'idle', name: '', sizeLabel: '', error: '' }
}

function fillIntent(text: string) {
  draft.value = text
}

function requestAttachment() {
  fileInput.value?.click()
}

function sizeLabelOf(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

async function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  attState.value = { status: 'uploading', name: file.name, sizeLabel: sizeLabelOf(file.size), error: '' }
  try {
    await presignUpload(file)
    attState.value = { status: 'done', name: file.name, sizeLabel: sizeLabelOf(file.size), error: '' }
  } catch (err) {
    attState.value = {
      status: 'error', name: file.name, sizeLabel: '',
      error: err instanceof Error ? err.message : String(err),
    }
  }
}

onMounted(async () => {
  const classesResult = await v3Api.catalog.classes().then(
    (value) => ({ status: 'fulfilled' as const, value }),
    (reason: unknown) => ({ status: 'rejected' as const, reason }),
  )
  if (classesResult.status === 'fulfilled') {
    classes.value = classesResult.value.data.items
    selectedClassId.value = classes.value.length === 1 ? classes.value[0].class_id : ''
  } else {
    const reason = classesResult.reason as { status?: number; code?: number } | undefined
    if (reason?.status === 401) classesLoadError.value = copyFor('prep.error.unauthenticated')
    else if (reason?.status === 403) classesLoadError.value = copyFor('prep.error.forbidden')
    else classesLoadError.value = copyFor('prep.error.authority_unavailable')
  }
  classesLoaded.value = true
})
</script>

<style scoped>
.ph { position: relative; min-height: 100%; overflow: hidden; padding-bottom: 60px; background: var(--ma-surface-page); }
.ailp-blob { position: absolute; border-radius: 50%; pointer-events: none; filter: blur(1px); }
.ailp-blob--one { width: 500px; height: 500px; top: -150px; left: -100px; background: var(--ma-action-primary-soft); opacity: .65; }
.ailp-blob--two { width: 400px; height: 400px; top: 200px; right: -100px; background: var(--ma-status-accent); opacity: .08; }
.ailp-blob--three { width: 600px; height: 600px; bottom: -200px; left: 33%; background: var(--ma-action-primary-soft); opacity: .45; }









.ph-main { position: relative; z-index: 5; max-width: 900px; margin: 0 auto; padding: 36px 24px 80px; }
.ph-welcome { text-align: center; margin-bottom: 36px; }
.ph-welcome__hi { color: var(--ailp-gray-500); margin-bottom: 10px; }
.ph-welcome__title { font-size: 42px; font-weight: 800; letter-spacing: -0.5px; margin-bottom: 12px; }
.ph-welcome__sub { color: var(--ailp-gray-500); font-size: 16px; }
.ph-inputwrap { margin-bottom: 44px; }
.ph-input {
  display: flex; align-items: flex-start; gap: 14px; background: var(--ma-surface-default);
  border-radius: var(--ailp-radius-2xl); padding: 18px 22px;
  border: 1px solid var(--ma-surface-border);
  box-shadow: var(--ma-elevation-card);
}
.ph-input__clip { flex-shrink: 0; width: 42px; height: 42px; border-radius: 12px; border: none; background: var(--ailp-gray-100); cursor: pointer; font-size: 17px; }
.ph-input__clip:hover { background: var(--ailp-primary-50); }
.ph-input__ta { flex: 1; min-height: 44px; border: none; outline: none; resize: none; font-size: 15px; font-family: inherit; color: var(--ailp-gray-900); background: transparent; padding: 9px 0; }
.ph-input__ta::placeholder { color: var(--ailp-gray-400); }
.ph-input__send { flex-shrink: 0; width: 42px; height: 42px; border-radius: var(--ma-radius-control); border: none; color: var(--ma-color-white-000); font-size: 16px; cursor: pointer; background: var(--ma-action-primary); transition: transform 0.15s ease; }
.ph-input__send:hover:not(:disabled) { transform: scale(1.06); }
.ph-input__send:disabled { opacity: 0.5; cursor: not-allowed; }
.ph-hints { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 14px; font-size: 13px; color: var(--ailp-gray-500); }
.ph-hints i { width: 4px; height: 4px; border-radius: 50%; background: var(--ailp-gray-300); }
.ph-hints__link { border: none; background: none; color: var(--ailp-gray-500); font-size: 13px; cursor: pointer; padding: 0; }
.ph-hints__link:hover { color: var(--ailp-primary-600); }
.ph-class-picker { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 14px; color: var(--ma-text-secondary); font-size: 13px; }
.ph-class-picker select { min-width: 220px; border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-control); background: var(--ma-surface-default); color: var(--ma-text-primary); padding: 7px 10px; font: inherit; }
.ph-class-picker select:focus { outline: 2px solid var(--ma-focus-ring); outline-offset: 2px; }
.ph-input-error, .ph-input-status { margin: 12px 0 0; text-align: center; color: var(--ma-text-secondary); font-size: 13px; }
.ph-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 48px; }
.ph-card { text-align: left; background: var(--ma-surface-default); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); padding: 18px; cursor: pointer; transition: transform 0.25s ease, box-shadow 0.25s ease; }
.ph-card:hover { transform: translateY(-4px); box-shadow: var(--ma-elevation-popover); }
.ph-card__ic { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center; font-size: 20px; margin-bottom: 14px; }
.ph-card__ic--primary { background: var(--ma-action-primary-soft); color: var(--ma-action-primary); }
.ph-card__ic--accent { background: var(--ma-surface-subtle); color: var(--ma-status-accent); }
.ph-card__ic--violet { background: var(--ma-surface-subtle); color: var(--ma-status-ai); }
.ph-card__ic--amber { background: var(--ma-surface-subtle); color: var(--ma-status-warning); }
.ph-card h3 { font-size: 14px; font-weight: 700; color: var(--ailp-gray-900); margin-bottom: 5px; }
.ph-card p { font-size: 12.5px; color: var(--ailp-gray-500); line-height: 1.6; }
.ph-recent__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.ph-recent__head h2 { font-size: 18px; font-weight: 700; }
.ph-recent__more { border: none; background: none; color: var(--ailp-primary-600); font-size: 13px; font-weight: 600; cursor: pointer; }
.ph-recent { display: flex; flex-direction: column; gap: 10px; }
.ph-plan { display: flex; align-items: center; gap: 14px; text-align: left; background: var(--ma-surface-default); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); padding: 16px 20px; cursor: pointer; transition: all 0.2s ease; width: 100%; }
.ph-plan:hover { border-color: var(--ma-action-primary); box-shadow: var(--ma-elevation-card); }
.ph-plan__ic { width: 44px; height: 44px; border-radius: var(--ma-radius-inner); display: grid; place-items: center; font-size: 19px; background: var(--ma-action-primary-soft); color: var(--ma-action-primary); flex-shrink: 0; }
.ph-plan__meta { flex: 1; min-width: 0; }
.ph-plan__meta h3 { font-size: 14px; font-weight: 700; color: var(--ailp-gray-900); margin-bottom: 4px; }
.ph-plan__tags { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ailp-gray-400); }
.ph-plan__chev { color: var(--ailp-gray-300); font-size: 20px; }
@media (max-width: 1100px) { .ph-cards { grid-template-columns: repeat(2, 1fr); } }
</style>
