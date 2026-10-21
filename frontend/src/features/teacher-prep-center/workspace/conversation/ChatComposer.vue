<script setup lang="ts">
/**
 * 底部 AI 条（W1-C · LEAD-BAR §3.2 / §3.3；全工作台共用）。
 *
 * - 白色胶囊 + 轻阴影，居中悬浮；
 * - 左：AI 图标 + 「正在修改: XX ×」浅蓝上下文胶囊（真实展开节点，可清除）；
 * - 中：多行 textarea（Enter 发送 / Shift+Enter 换行，随内容自动增高）；
 * - 右：麦克风（Web Speech API 真实转写，不支持则不渲染入口）+ 40px 主蓝圆形发送钮；
 * - busy 期间输入框保持可打字；点发送若上一轮仍在途 → 行内提示「上一条还在处理中」
 *   并不发送（不静默丢弃），整轮结束后恢复；上方阶段/失败/断线提示；
 *   行为在三条 Tab 下一致（共享单例状态）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import { useDeckEditorStore } from '@/features/teacher-prep-center/stores/deckEditor'
import { COMPOSER } from '@/features/teacher-prep-center/copy/prepCopy'
import { textbookDisplayName } from '@/features/teacher-prep-center/workspace/classroom/TextbookCover.vue'
import { useSpeechInput } from '../classroom/useSpeechInput'
import { clearEditTarget, submitDraft, useConversation } from './useConversation'

const route = useRoute()
const { state } = useConversation()
const store = usePrepSessionStore()
const deck = useDeckEditorStore()
const textarea = ref<HTMLTextAreaElement | null>(null)

/** 当前 Tab（本输入条只在 对话 / 课堂设计 / PPT / 教材与资料 四页渲染，白名单见 PrepWorkspace）。 */
const activeTab = computed(() => {
  const t = route.params.tab
  return t === 'ppt' || t === 'materials' || t === 'chat' ? t : 'classroom'
})
/** PPT 页：本输入条 = 「修改当前页」（真实 deck regen，落到权威 spec，刷新还原）。 */
const pageMode = computed(() => activeTab.value === 'ppt')
/** 课堂设计页：editTarget（当前展开的教学环节）只在该页有意义。 */
const classroomMode = computed(() => activeTab.value === 'classroom')
/** 对话三栏主面（S2，golden1 composer 三 chips 所在页）。 */
const chatMode = computed(() => activeTab.value === 'chat')

/** placeholder 按 Tab 变化（VISUAL-SPEC-V3 §3-S3.4 / §3-S4.7 / §3-S6；文案集中在 COMPOSER）。 */
const placeholder = computed(() => {
  if (pageMode.value) return COMPOSER.ppt
  if (activeTab.value === 'materials') return COMPOSER.materials
  if (chatMode.value && !state.editTarget) return COMPOSER.chat
  if (classroomMode.value && state.editTarget) return COMPOSER.classroomTarget(state.editTarget.title)
  return COMPOSER.classroom
})
/** 服务端权威信号：本会话有在途生成（available_actions 含 cancel_run）。
 *  生成期间后端拒绝新消息（409）：点发送给行内提示并不发送（不静默丢弃），
 *  但**不再锁死输入框**——busy 期间教师仍可打字（2026-10-04：发送后全程有指示）。 */
const generating = computed(() => (store.current?.available_actions ?? []).some((a) => a.action === 'cancel_run'))

/** 上一轮在途（busy / 在途生成）：点发送时行内提示，不发送（诚实可见，不静默丢弃）。 */
const inFlight = computed(() => !pageMode.value && (state.busy || generating.value))

const canSend = computed(() =>
  pageMode.value ? !!state.draft.trim() && !deck.aiBusy : !!state.draft.trim(),
)

/** 在途点击发送 → 行内提示「上一条还在处理中」；本轮结束后自动恢复（不静默丢弃）。 */
const sendBlocked = ref(false)
watch(inFlight, (busy) => {
  if (!busy) sendBlocked.value = false
})

/** 上方提示条：PPT 页呈现真实的单页修改阶段/失败；其余 Tab 维持会话阶段/失败/断线。 */
const notice = computed<{ kind: 'info' | 'error' | 'warn'; text: string } | null>(() => {
  if (pageMode.value) {
    if (deck.aiBusy) return { kind: 'info', text: deck.aiStageLabel || 'AI 正在修改当前页…' }
    if (deck.aiError) return { kind: 'error', text: deck.aiError }
    return null
  }
  if (sendBlocked.value) return { kind: 'info', text: '上一条还在处理中' }
  if (state.stage) return { kind: 'info', text: state.stage }
  if (generating.value) return { kind: 'info', text: '正在生成，生成完成后可继续对话' }
  if (state.error) return { kind: 'error', text: state.error }
  if (state.streamStatus === 'reconnecting') return { kind: 'warn', text: '连接已断开，正在重连…' }
  return null
})
const mic = useSpeechInput({
  onResult: (text) => {
    state.draft = state.draft ? `${state.draft} ${text}` : text
    void resize()
  },
})

function onKeydown(e: KeyboardEvent): void {
  if (e.key !== 'Enter') return
  if (e.shiftKey || e.isComposing) return // Shift+Enter 换行 / 输入法组合中
  e.preventDefault()
  void send()
}

async function send(): Promise<void> {
  if (!canSend.value) return
  // PPT 页：本输入条承载「修改当前页」（与 placeholder 语义一致，禁止文案与行为不一致）。
  if (pageMode.value) {
    const instruction = state.draft.trim()
    const ok = await deck.aiModifyCurrentPage(instruction)
    if (ok) state.draft = ''
    await nextTick()
    resize()
    return
  }
  // 上一轮仍在途：行内提示（不发送、不静默丢弃、不清空草稿）；整轮结束后恢复。
  if (inFlight.value) {
    sendBlocked.value = true
    return
  }
  await submitDraft()
  await nextTick()
  resize()
}
function resize(): void {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  const line = 22
  el.style.height = `${Math.min(el.scrollHeight, line * 4 + 16)}px`
}

function onClearTarget(): void {
  clearEditTarget()
}

/* ── composer 三 chips（golden1 S2；仅对话页渲染，全部真实行为/诚实禁用）──────
 * 引用教材 → 把当前教材章节的真实引用写入输入框（可编辑，随消息发出）；
 * 数学图形 → 插入可编辑椭圆方程模板（消息正文经 KaTeXText 渲染为行内公式）；
 * 上传文件 → 对话链路无附件后端能力 → 诚实禁用 + 原因（G12：禁止亮而无效的假入口）。 */
const citationText = computed(() => {
  const book = textbookDisplayName(store.currentCourseMeta.textbook)
  const sec = (store.current?.artifact.context?.section?.title || '').trim()
  return [book ? `《${book}》` : '', sec].filter(Boolean).join(' ')
})

function insertToDraft(text: string): void {
  const cur = state.draft.trim()
  state.draft = cur ? `${cur} ${text}` : text
  void nextTick(resize)
}

function insertCitation(): void {
  const cite = citationText.value
  if (!cite) return
  insertToDraft(`（引用教材：${cite}）`)
}

function insertMathTemplate(): void {
  insertToDraft('$\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1$')
}

/** 空态主行动按钮 → 聚焦本输入条（工作台唯一输入入口，不新开输入框）。 */
function focusInput(): void {
  textarea.value?.focus()
}
onMounted(() => window.addEventListener('pc:focus-composer', focusInput))
onBeforeUnmount(() => window.removeEventListener('pc:focus-composer', focusInput))
</script>

<template>
  <div class="pc-composer">
    <div v-if="notice" class="pc-composer__notice" :class="`pc-composer__notice--${notice.kind}`">
      <span class="pc-composer__notice-dot" aria-hidden="true"></span>{{ notice.text }}
    </div>
    <div class="pc-composer__pill">
      <span class="pc-composer__spark" aria-hidden="true">
        <svg viewBox="0 0 16 16"><path d="M8 1.6 9.6 6 14 7.6 9.6 9.2 8 13.6 6.4 9.2 2 7.6 6.4 6z" fill="currentColor" /></svg>
      </span>

      <!-- 对话页三 chips（golden1）：上传文件=诚实禁用（无附件后端能力）；引用教材/数学图形=真实写入输入框 -->
      <span v-if="chatMode" class="pc-composer__chips">
        <button
          type="button"
          class="pc-composer__chip"
          disabled
          title="对话暂不支持上传文件"
        >
          上传文件
        </button>
        <button
          type="button"
          class="pc-composer__chip"
          title="把当前教材章节引用写入输入框"
          :disabled="!citationText"
          @click="insertCitation"
        >
          引用教材
        </button>
        <button
          type="button"
          class="pc-composer__chip"
          title="插入椭圆方程模板（可编辑）"
          @click="insertMathTemplate"
        >
          数学图形
        </button>
      </span>

      <span v-if="state.editTarget && classroomMode" class="pc-composer__target">
        正在修改：{{ String(state.editTarget.nodeIndex + 1).padStart(2, '0') }} {{ state.editTarget.title }}
        <button type="button" class="pc-composer__target-x" aria-label="清除修改目标" @click="onClearTarget">×</button>
      </span>

      <textarea
        ref="textarea"
        v-model="state.draft"
        class="pc-composer__input"
        rows="1"
        :disabled="pageMode && deck.aiBusy"
        :placeholder="placeholder"
        @keydown="onKeydown"
        @input="resize"
      ></textarea>

      <button
        v-if="mic.supported.value"
        type="button"
        class="pc-composer__mic"
        :class="{ 'is-recording': mic.listening.value }"
        :title="mic.error.value || (mic.listening.value ? '正在听取语音，点击结束' : '语音输入')"
        :aria-label="mic.listening.value ? '结束语音输入' : '开始语音输入'"
        @click="mic.toggle()"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <rect x="6.2" y="1.8" width="3.6" height="7.4" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.3" />
          <path d="M3.8 7.8a4.2 4.2 0 0 0 8.4 0M8 12v2.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </svg>
      </button>

      <button
        type="button"
        class="pc-composer__send"
        :class="{ 'pc-composer__send--sq': classroomMode }"
        :disabled="!canSend"
        aria-label="发送给 AI"
        @click="send"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M2.2 8 13.8 2.4 9.8 13.8 7.9 8.9 2.2 8Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.pc-composer {
  position: fixed;
  /* --pc-center-offset：嵌入外框后内容区相对视口的中心补偿（R1 G1；默认 0） */
  left: calc(50% + var(--pc-center-offset, 0px));
  transform: translateX(-50%);
  bottom: 26px;
  /* z-index 95：高于抽屉遮罩（90）、低于导出对话框（100）。
   对话抽屉内不放第二个输入框，发送唯一入口是本条 → 抽屉打开时它必须仍可见可点。 */
  z-index: 95;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 680px;
  max-width: calc(100vw - 48px);
  font-family: var(--pc-font);
}
.pc-composer__notice {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 14px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--pc-panel, #fff);
  color: var(--pc-primary, #2563eb);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}
.pc-composer__notice--error {
  color: var(--pc-danger, #dc2626);
}
.pc-composer__notice--warn {
  color: var(--pc-warning, #d97706);
}
.pc-composer__notice-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pc-primary, #2563eb);
  animation: pc-composer-pulse 1.2s ease-in-out infinite;
}
@keyframes pc-composer-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
.pc-composer__pill {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  width: 100%;
  padding: 9px 10px 9px 12px;
  background: var(--pc-panel, #fff);
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: 999px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.14);
}
.pc-composer__spark {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
  margin-bottom: 2px;
}
.pc-composer__spark svg {
  width: 15px;
  height: 15px;
}
/* 对话页三 chips（golden1 composer；仅对话页渲染） */
.pc-composer__chips {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: center;
}
.pc-composer__chip {
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--pc-line, #e5e9f2);
  border-radius: 999px;
  background: var(--pc-panel, #fff);
  color: var(--pc-text-2, #1f2937);
  font-size: 11.5px;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
}
.pc-composer__chip:hover:not(:disabled) {
  border-color: var(--pc-primary, #2563eb);
  color: var(--pc-primary, #2563eb);
  background: var(--pc-primary-soft, #eff4ff);
}
.pc-composer__chip:disabled {
  color: var(--pc-text-faint, #9ca3af);
  cursor: not-allowed;
}
.pc-composer__target {
  flex: none;
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 240px;
  padding: 4px 8px 4px 10px;
  border-radius: 999px;
  background: var(--pc-primary-soft, #eff4ff);
  color: var(--pc-primary, #2563eb);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pc-composer__target-x {
  border: none;
  background: transparent;
  color: var(--pc-primary, #2563eb);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.pc-composer__input {
  flex: 1;
  min-width: 0;
  align-self: center;
  max-height: 104px;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-size: 14px;
  font-family: inherit;
  line-height: 22px;
  color: var(--pc-text, #0f172a);
  overflow-y: auto;
}
.pc-composer__input::placeholder {
  color: var(--pc-text-faint, #9ca3af);
}
.pc-composer__mic {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--pc-text-muted, #6b7280);
  cursor: pointer;
  align-self: center;
}
.pc-composer__mic:hover {
  color: var(--pc-primary, #2563eb);
}
.pc-composer__mic.is-recording {
  color: var(--pc-danger, #dc2626);
  animation: pc-composer-pulse 1.2s ease-in-out infinite;
}
.pc-composer__mic svg {
  width: 16px;
  height: 16px;
}
.pc-composer__send {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: var(--pc-primary, #2563eb);
  color: #fff;
  cursor: pointer;
  align-self: center;
}
.pc-composer__send:hover:not(:disabled) {
  background: var(--pc-primary-hover, #1d4fd8);
}
.pc-composer__send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.pc-composer__send svg {
  width: 16px;
  height: 16px;
}
/* 课堂设计页发送钮（R6-S3fix · 黄金图 G23：实心蓝圆角方 ≈41×38，主蓝亮 #2F71FE）。
   其它 Tab 维持圆形（各屏黄金图口径不同；首页 LEAD-BAR 圆形规则不受影响）。
   禁用态保持实心底（黄金图默认态即实心蓝），以图标降透明度表达不可发送。 */
.pc-composer__send--sq {
  width: 41px;
  height: 38px;
  border-radius: 10px;
  background: var(--pc-primary-bright);
}
.pc-composer__send--sq:hover:not(:disabled) {
  background: var(--pc-primary-hover);
}
.pc-composer__send--sq:disabled {
  opacity: 1;
}
.pc-composer__send--sq:disabled svg {
  opacity: 0.5;
}
</style>
