<template>
  <!--
    T22 · basic_mode_card：AI 失败后的**可编辑**降级卡片。
    规则（R17 契约）：不沉默、不显示成功、不编造内容 —— 错误标签来自服务端，
    教师可手工编辑后提交 `apply_edits`（确定性路径，无 LLM）。
  -->
  <SurfaceCard class="basic-mode" test-id="prep-card-basic-mode" :title="copyFor('prep.card.basic_mode')">
    <p v-if="errorLabel" class="basic-mode__error">{{ errorLabel }}</p>

    <label class="basic-mode__label" :for="fieldId">{{ copyFor('prep.composer.placeholder') }}</label>
    <textarea
      :id="fieldId"
      v-model="draft"
      class="basic-mode__input"
      rows="4"
      :disabled="disabled"
      data-testid="prep-basic-mode-input"
    />

    <template #footer>
      <ActionButton variant="primary" :disabled="disabled || !draft.trim()" @click="submit">
        {{ copyFor('common.confirm') }}
      </ActionButton>
    </template>
  </SurfaceCard>
</template>

<script setup lang="ts">
import { ref, useId } from 'vue'
import ActionButton from '../primitives/ActionButton.vue'
import SurfaceCard from '../primitives/SurfaceCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const props = withDefaults(
  defineProps<{
    errorLabel?: string | null
    editable?: boolean
    initialText?: string
    disabled?: boolean
  }>(),
  { errorLabel: null, editable: true, initialText: '', disabled: false },
)

const emit = defineEmits<{ submit: [text: string] }>()

const fieldId = `prep-basic-${useId()}`
const draft = ref(props.initialText)

function submit() {
  emit('submit', draft.value)
}
</script>

<style scoped>
.basic-mode__error {
  margin: 0 0 var(--ma-space-2);
  color: var(--ma-status-danger);
  font-size: var(--ma-font-size-body);
}

.basic-mode__label {
  display: block;
  margin-bottom: var(--ma-space-1);
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}

.basic-mode__input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  padding: var(--ma-space-2);
  font-family: var(--ma-font-family-ui);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
  background: var(--ma-surface-default);
}

.basic-mode__input:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: 1px;
}
</style>
