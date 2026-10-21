<template>
  <!--
    T22.1 · 单条消息渲染（UX-CONTRACT A/B/C）。
    铁律：
    - AI 消息**开放内容排版**，不每句包卡（只有确实需要结构化决策时才出卡）；
    - proposal / artifact / 任务状态 / 错误恢复 **都贴在产生它的这条消息附近**，
      不放固定第三栏、不放底部 Job Bar。
  -->
  <article
    class="msg"
    :class="`msg--${message.role}`"
    :data-testid="`prep-message-${message.message_id}`"
    :data-role="message.role"
  >
    <div class="msg__body">
      <header class="msg__header">
        <span class="msg__role-marker" aria-hidden="true">
          <NIcon :size="15"><component :is="roleIcon" /></NIcon>
        </span>
        <span class="msg__role-label">{{ roleLabel }}</span>
        <time v-if="formattedTime" class="msg__time" :datetime="message.created_at">{{ formattedTime }}</time>
      </header>
      <div class="msg__content" :class="[`msg__content--${message.role}`, { 'msg__content--framed': !hasTypedCard }]">
        <p v-if="message.text && message.component?.type !== 'user_text'" class="msg__text">{{ message.text }}</p>

      <!-- user_text：只出气泡正文，不套卡 -->
      <div v-if="message.component?.type === 'user_text'" class="msg__bubble">
        {{ message.text || String(message.component?.props?.text ?? '') }}
      </div>

      <!-- basic_mode_card：可编辑降级（不是「暂不支持」） -->
      <BasicModeCard
        v-else-if="message.component?.type === 'basic_mode_card'"
        :error-label="errorLabelCopy"
        :editable="message.editable"
        :initial-text="''"
        @submit="(t) => emit('apply-edits', message.message_id, t)"
      />

      <!-- allowlisted typed card -->
      <section
        v-else-if="message.component"
        class="msg__card"
        :class="cardAccentClass"
        :data-testid="isProposal ? 'prep-proposal-card' : `prep-card-${message.component.type}`"
        data-card="true"
      >
        <header class="msg__card-head">
          <span v-if="badgeText" class="msg__badge">{{ badgeText }}</span>
          <h3 class="msg__card-title">{{ cardTitle }}</h3>
          <span v-if="cardStatus" class="msg__card-status">{{ cardStatus }}</span>
        </header>
        <p v-if="cardSub" class="msg__card-sub">{{ cardSub }}</p>

        <StructuredCard
          :component-type="message.component.type"
          :props="message.component.props"
          :requires-confirmation="needsConfirmation"
          @review="(payload) => emit('run', payload.action, payload.target, { expected_revision: payload.expected_revision })"
          @conflict="emit('conflict', message.message_id)"
          @select="(payload) => emit('select', payload)"
        />

        <footer v-if="isProposal" class="msg__actions">
          <template v-for="a in proposalActions" :key="a.action + (a.target ?? '')">
            <button
              v-if="!a.disabled"
              type="button"
              class="msg__action"
              :class="{ 'msg__action--primary': a.action.includes('confirm') || a.action.includes('accept') }"
              :disabled="busy"
              :data-testid="`prep-action-${a.action}`"
              @click="emit('run', a.action, a.target)"
            >
              {{ a.copy_key ? copyFor(a.copy_key) : a.action }}
            </button>
          </template>

          <button
            v-if="hasAuthorizedEvidenceAction"
            type="button"
            class="msg__action"
            data-testid="prep-open-evidence"
            @click="emit('open-evidence')"
          >
            {{ copyFor('prep.msg.open_evidence') }}
          </button>

          <button
            v-if="authorizedEditAction"
            type="button"
            class="msg__action"
            data-testid="prep-propose-edit"
            @click="emit('run', authorizedEditAction.action, authorizedEditAction.target)"
          >
            {{ copyFor('prep.msg.propose_edit') }}
          </button>
        </footer>
      </section>

      <!-- 任务/流式状态：留在消息附近（不是底部状态条） -->
      <!-- WP-K：运行状态变化要能被播报（此前无声） -->
      <p v-if="message.run_status" class="msg__status" :data-testid="`prep-run-${message.message_id}`" role="status" aria-live="polite">
        <span class="msg__status-dot" aria-hidden="true" />
        <span>{{ runLabel }}</span>
        <!-- 长耗时阶段（蓝图 LLM 实测 20s–9min）必须给出真实耗时，
             否则教师无法区分「在跑」和「卡死」（2026-09-14 真实反馈）。 -->
        <span v-if="elapsedLabel" class="msg__status-elapsed">已等待 {{ elapsedLabel }}</span>
        <span v-if="isGenerating" class="msg__status-hint">{{ copyFor('prep.run.long_hint') }}</span>
      </p>

      <!-- 消息内恢复卡：仅用于非 401/403/404/409 的可重试错误 -->
      <div v-if="message.error_label" class="msg__error" role="status">
        <span>{{ errorLabelCopy }}</span>
        <button
          type="button"
          class="msg__action"
          data-testid="prep-inline-retry"
          @click="emit('retry', message.message_id)"
        >
          {{ copyFor('prep.msg.retry') }}
        </button>
      </div>

      <!-- ARTIFACT_READY：产物成熟 → 在**产生它的消息下**出现入口 -->
        <ArtifactReadyCard
          v-if="artifactForThisMessage"
          :artifact="artifactForThisMessage"
          data-testid="prep-artifact-ready-card"
          @open="emit('open-focus')"
        />
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { PersonOutline, SparklesOutline } from '@vicons/ionicons5'
import BasicModeCard from '@/features/teacher-prep-v2/components/cards/BasicModeCard.vue'
import StructuredCard from '@/features/teacher-prep-v2/components/cards/StructuredCard.vue'
import ArtifactReadyCard from './ArtifactReadyCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { errorLabelText, runStatusLabel } from '@/features/teacher-prep-v2/i18n/labels'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'
import type { ActionView, MessageView } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{
  message: MessageView
  actions: ActionView[]
  busy?: boolean
  activeArtifact: ActiveArtifact | null
}>()

const roleIcon = computed(() => (props.message.role === 'user' ? PersonOutline : SparklesOutline))
const roleLabel = computed(() =>
  props.message.role === 'user' ? copyFor('prep.msg.role.teacher') : copyFor('prep.msg.role.assistant'),
)
const formattedTime = computed(() => {
  const source = props.message.created_at
  if (!source) return ''
  const parsed = new Date(source)
  if (Number.isNaN(parsed.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(parsed)
})

const emit = defineEmits<{
  (e: 'run', action: string, target: string | null, payload?: Record<string, unknown>): void
  (e: 'retry', messageId: string): void
  (e: 'conflict', messageId: string): void
  (e: 'open-evidence'): void
  (e: 'propose-edit', messageId: string): void
  (e: 'apply-edits', messageId: string, text: string): void
  (e: 'open-focus'): void
  (e: 'select', payload: { textbook_id?: string; section_id?: string; class_id?: string }): void
}>()

/** 本条消息是否带 typed card（决定外层是否再套一层卡框）。 */
const hasTypedCard = computed(() => {
  const t = props.message.component?.type
  return !!t && t !== 'user_text'
})

/**
 * 需要教师做决策的卡片类型（其余为纯展示）。
 *
 * 2026-09-14 备课中心简化：`blueprint_card`（=「大纲预览」）已移除——它此前
 * 既显示整段大纲正文，又要求教师点「采用这份方案 / 不要这份方案」才能继续。
 * 现在教学方案由前端按服务端下发的动作自动确认（见 `useAutoDeckFlow`），
 * 卡片只作为一句进度陈述存在，因此不再属于 proposal 类型。
 */
const PROPOSAL_TYPES = new Set(['diagnostic_card', 'selection_card', 'diagnostic_proposal', 'blueprint_revision'])

const isProposal = computed(
  () => !!props.message.component && PROPOSAL_TYPES.has(props.message.component.type),
)

const cardAccentClass = computed(() => {
  const t = props.message.component?.type
  if (t === 'blueprint_card') return 'msg__card--blueprint'
  if (t === 'diagnostic_card') return 'msg__card--diagnostic'
  return ''
})

const badgeText = computed(() => {
  const t = props.message.component?.type
  if (t === 'blueprint_card') return copyFor('prep.blueprint.badge')
  if (t === 'diagnostic_card') return copyFor('prep.artifact.type.diagnostic_task')
  return null
})

const cardSub = computed(() => {
  return null
})

/**
 * 卡片标题。
 * 服务端给了 `title` 就用服务端的（blueprint 等业务卡自带标题）；
 * 没给时**按组件类型**给准确标题，不再一律兜底成「AI 提案」——
 * `context_card` 只是「教材/章节已锁定 + 质量提示」，把它叫「AI 提案」
 * 会让教师以为这就是备课方案，进而反复点击、反复触发生成（2026-09-14 真实反馈）。
 */
const CARD_TYPE_TITLES: Record<string, string> = {
  context_card: 'prep.msg.card.context_title',
  selection_card: 'prep.msg.card.selection_title',
  error_card: 'prep.msg.card.error_title',
}

const cardTitle = computed(() => {
  const t = props.message.component?.props?.title
  if (typeof t === 'string' && t) {
    return t.replace(/\s*[（(](?:脱敏演示|demo|sanitized)[）)]\s*$/i, '').trim()
  }
  const type = props.message.component?.type
  const byType = type ? CARD_TYPE_TITLES[type] : undefined
  return copyFor(byType ?? 'prep.msg.card.fallback_title')
})

const cardStatus = computed(() => {
  return null
})

/** 只显示与当前提案相关的动作（服务端下发什么就显示什么）。 */
const proposalActions = computed(() =>
  props.actions.filter(
    (a) =>
      a.action.includes('confirm') ||
      a.action.includes('accept') ||
      a.action.includes('reject') ||
      a.action.includes('skip'),
  ),
)
const hasAuthorizedEvidenceAction = computed(() => props.actions.some((a) => !a.disabled && ['open_evidence', 'view_evidence'].includes(a.action)))
const authorizedEditAction = computed(() => props.actions.find((a) => !a.disabled && ['propose_edit', 'propose_revision'].includes(a.action)) ?? null)

const needsConfirmation = computed(() => proposalActions.value.some((a) => a.action.includes('confirm')))

// 内部码一律经 i18n/labels 映射；未收录走通用文案，不回显原码（见 .impeccable.md）。
const runLabel = computed(() => runStatusLabel(props.message.run_status))
const errorLabelCopy = computed(() => errorLabelText(props.message.error_label))

/** 是否处于「生成中」（PENDING/RUNNING/CLAIMED 都算在跑，不是卡死）。 */
const isGenerating = computed(() => {
  const s = String(props.message.run_status ?? '').toUpperCase()
  return s === 'RUNNING' || s === 'PENDING' || s === 'CLAIMED'
})

/**
 * 生成中才走的秒级心跳：给「已等待 N 分 M 秒」提供真实时间基准。
 * 非生成态不启定时器（避免每条历史消息各起一个 interval）。
 */
const nowMs = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | null = null

function stopTicker() {
  if (ticker !== null) {
    clearInterval(ticker)
    ticker = null
  }
}

watch(
  isGenerating,
  (on) => {
    stopTicker()
    if (on) {
      nowMs.value = Date.now()
      ticker = setInterval(() => {
        nowMs.value = Date.now()
      }, 1000)
    }
  },
  { immediate: true },
)
onBeforeUnmount(stopTicker)

/** 已等待时长；< 5 秒不显示（避免刚发出就闪一个「0 秒」）。 */
const elapsedLabel = computed(() => {
  if (!isGenerating.value) return ''
  const started = props.message.created_at ? new Date(props.message.created_at).getTime() : Number.NaN
  if (Number.isNaN(started)) return ''
  const total = Math.max(0, Math.floor((nowMs.value - started) / 1000))
  if (total < 5) return ''
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`
})

/** 本条消息是否产出了当前活跃 artifact（决定 Artifact Ready 卡归属）。 */
const artifactForThisMessage = computed<ActiveArtifact | null>(() => {
  const a = props.activeArtifact
  if (!a) return null
  const m = props.message
  if (a.artifact_type === 'teaching_blueprint' && m.blueprint_id === a.artifact_id) return a
  if (a.artifact_type === 'diagnostic_task' && m.kind?.includes('diagnostic')) return a
  return null
})
</script>

<style scoped>
.msg {
  display: flex;
  flex-direction: column;
  padding: var(--ma-space-2) 0;
  align-items: stretch;
}

.msg--user .msg__body {
  width: min(74%, 660px);
  margin-left: auto;
  text-align: right;
}

.msg--assistant .msg__body {
  width: min(100%, 760px);
  margin-right: auto;
}

/* WP-K 视觉修复（Cardocalypse）：AI 消息原来无条件套一层带边框+背景+内边距的外框，
   而带 typed card 的消息内部又是 `.msg__card`（同样边框+阴影+背景）→ **卡里套卡**，
   层级噪音大且把真卡的边界感冲掉。
   现在只有"纯文本/气泡"消息才画外框；带卡的卡片消息直接让卡自己承担容器语义。
   （对照 FRONTEND-REFERENCE-MATRIX §4.8 "一个卡里不得再套一个卡"） */
.msg--assistant .msg__content--framed {
  padding: var(--ma-space-3) var(--teacher-space-4, 16px);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
}

.msg--user .msg__content { display: contents; }

.msg__header {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  min-height: 28px;
  margin-bottom: var(--ma-space-1);
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
}

.msg--user .msg__header { justify-content: flex-end; }

.msg__role-marker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-full);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
}

.msg--user .msg__role-marker {
  background: var(--ma-action-primary-soft);
  border-color: var(--ma-action-primary);
  color: var(--ma-action-primary);
}

.msg__role-label { font-weight: 600; color: var(--ma-text-body); }
.msg__time { color: var(--ma-text-muted); }

.msg__bubble {
  display: inline-block;
  max-width: 100%;
  padding: 12px var(--teacher-space-4, 16px);
  border-radius: var(--ma-radius-card);
  background: var(--ma-action-primary-soft);
  border: 1px solid var(--ma-action-primary);
  box-shadow: var(--ma-elevation-card);
  font-size: 15px;
  line-height: var(--ma-typography-body);
  color: var(--ma-text-primary);
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
}

.msg__body {
  min-width: 0;
}

.msg__text {
  margin: 0;
  font-size: 15px;
  line-height: 1.7;
  color: var(--ma-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.msg--user .msg__text {
  display: inline-block;
  max-width: 100%;
  margin-left: auto;
  padding: 12px var(--teacher-space-4, 16px);
  border: 1px solid var(--ma-action-primary);
  border-radius: var(--ma-radius-card);
  background: var(--ma-action-primary-soft);
  box-shadow: var(--ma-elevation-card);
  text-align: left;
}

.msg__card {
  margin-top: var(--ma-space-2);
  padding: var(--teacher-space-4, 16px);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
  box-shadow: var(--ma-elevation-card);
  text-align: left;
}
.msg__card--blueprint { border-left: 3px solid var(--ma-action-primary); }
.msg__card--diagnostic { border-left: 3px solid var(--ma-status-accent); }

.msg__badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-action-primary-soft);
  color: var(--ma-action-primary);
  font-size: 12px;
  font-weight: 600;
}

.msg__card-head {
  display: flex;
  align-items: baseline;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-2);
}

.msg__card-title {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.msg__card-status {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.msg__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ma-space-1);
  margin-top: var(--ma-space-3);
  padding-top: var(--ma-space-2);
  border-top: 1px solid var(--ma-surface-border);
}

.msg__action {
  height: 30px;
  padding: 0 var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-btn);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.msg__action:hover:not(:disabled) {
  border-color: var(--ma-action-primary);
  color: var(--ma-text-primary);
}

.msg__action--primary {
  background: var(--ma-action-primary);
  border-color: var(--ma-action-primary);
  color: var(--ma-color-white-000);
}

.msg__action:disabled {
  cursor: not-allowed;
  color: var(--ma-text-muted);
}

.msg__action:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}

.msg__status {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: var(--ma-space-2) 0 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.msg__status-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-status-accent);
}

/* 耗时用等宽数字：秒数跳动时宽度不抖。 */
.msg__status-elapsed {
  font-variant-numeric: tabular-nums;
  color: var(--ma-text-body);
}

.msg__status-hint {
  color: var(--ma-text-muted);
}

.msg__error {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-top: var(--ma-space-2);
  padding: var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  background: var(--ma-surface-subtle);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

@media (prefers-reduced-motion: no-preference) {
  .msg__card {
    animation: card-in 200ms ease-out;
  }
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
</style>
