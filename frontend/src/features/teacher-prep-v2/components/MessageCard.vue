<template>
  <!--
    T22 · timeline 单条渲染器。
    **allowlist 分发**：只渲染服务端注册的 10 种 component_type；
    未登记类型 → TEXT_SUMMARY 降级（不白屏），并显式标注降级。
    任何 HTML / CSS / JS 在类型层即不可表达（无 v-html、无 style 绑定字符串）。
  -->
  <article class="msg" :class="`msg--${role}`" :data-testid="`prep-msg-${messageId}`">
    <header class="msg__head">
      <span class="msg__role">{{ roleLabel }}</span>
      <span class="msg__seq" aria-hidden="true">#{{ seq }}</span>
      <StatusPill v-if="runStatus" :tone="runStatusTone" :label="runStatusLabel(runStatus)" />
    </header>

    <div class="msg__body">
      <BasicModeCard
        v-if="component?.type === 'basic_mode_card'"
        :error-label="errorLabelCopy"
        :editable="editable"
        :initial-text="text ?? ''"
        @submit="(t) => $emit('apply-edits', messageId, t)"
      />
      <ErrorCard
        v-else-if="component?.type === 'error_card'"
        :label="errorLabelCopy"
        :message="text"
        retryable
        @retry="$emit('retry', messageId)"
      />
      <UserTextCard v-else-if="component?.type === 'user_text'" :text="text ?? ''" />
      <AssistantTextCard v-else-if="component?.type === 'assistant_text'" :text="text ?? ''" />
      <StructuredCard
        v-else-if="component && STRUCTURED_TYPES.includes(component.type)"
        :component-type="component.type"
        :props="component.props ?? {}"
      />
      <p v-else-if="text" class="msg__text">{{ text }}</p>
      <p v-else class="msg__fallback">{{ copyFor('prep.card.unknown') }}</p>
    </div>

    <slot name="actions" />
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import AssistantTextCard from './cards/AssistantTextCard.vue'
import BasicModeCard from './cards/BasicModeCard.vue'
import ErrorCard from './cards/ErrorCard.vue'
import StructuredCard from './cards/StructuredCard.vue'
import UserTextCard from './cards/UserTextCard.vue'
import StatusPill from './primitives/StatusPill.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { errorLabelText, runStatusLabel } from '@/features/teacher-prep-v2/i18n/labels'
import type { ComponentType, MessageView } from '@/features/teacher-prep-v2/types/workspace'

/** 走通用结构化渲染器的类型（数据驱动，无额外交互语义）。 */
const STRUCTURED_TYPES: ComponentType[] = [
  'context_card',
  'blueprint_card',
  'diagnostic_card',
  'selection_card',
  'job_status_card',
  'evidence_rail',
]

const props = defineProps<{ message: MessageView }>()

defineEmits<{
  retry: [messageId: string]
  'apply-edits': [messageId: string, text: string]
}>()

const messageId = computed(() => props.message.message_id)
const seq = computed(() => props.message.seq)
const role = computed(() => props.message.role)
const text = computed(() => props.message.text)
const component = computed(() => props.message.component)
const errorLabelCopy = computed(() => errorLabelText(props.message.error_label))
const editable = computed(() => props.message.editable)
const runStatus = computed(() => props.message.run_status)

const roleLabel = computed(() =>
  props.message.role === 'user' ? '我' : props.message.role === 'assistant' ? 'AI' : '系统',
)

/** 在途/失败用不同基调；未知状态按"在途"处理，绝不是 success。 */
const runStatusTone = computed(() => {
  const s = runStatus.value ?? ''
  if (s === 'SUCCEEDED') return 'success' as const
  if (s === 'FAILED' || s === 'BASIC_MODE') return 'warning' as const
  if (s === 'CANCELLED') return 'neutral' as const
  return 'running' as const
})
</script>

<style scoped>
.msg {
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
  padding: var(--ma-space-3);
  margin-bottom: var(--ma-space-2);
}

.msg--assistant {
  background: var(--ma-surface-subtle);
}

.msg__head {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-2);
}

.msg__role {
  font-weight: 600;
  color: var(--ma-text-primary);
  font-size: var(--ma-font-size-body);
}

.msg__seq {
  color: var(--ma-text-muted);
  font-family: var(--ma-font-family-mono);
  font-size: var(--ma-font-size-body);
}

.msg__text {
  margin: 0;
  white-space: pre-wrap;
  color: var(--ma-text-primary);
}

.msg__fallback {
  margin: 0;
  color: var(--ma-text-muted);
}
</style>
