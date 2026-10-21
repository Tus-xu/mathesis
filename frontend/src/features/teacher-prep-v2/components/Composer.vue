<template>
  <!--
    T22 · Composer：自然语言入口（conversation-first 的唯一主输入）。
    草稿存 localStorage（**仅**未提交草稿，非业务事实）；发送后清空。
    发送中禁用，但不显示"进度百分比"（后端不给进度，前端不编）。
  -->
  <form class="composer" data-testid="prep-composer" @submit.prevent="submit">
    <label class="composer__label" for="prep-composer-input">{{ copyFor('prep.composer.placeholder') }}</label>
    <div class="composer__row">
      <textarea
        id="prep-composer-input"
        v-model="draft"
        class="composer__input"
        rows="2"
        :placeholder="copyFor('prep.composer.placeholder')"
        :disabled="disabled"
        data-testid="prep-composer-input"
        @keydown.enter.exact.prevent="submit"
      />
      <ActionButton
        variant="primary"
        :disabled="disabled || !draft.trim()"
        :busy="busy"
        test-id="prep-composer-send"
        @click="submit"
      >
        {{ busy ? copyFor('prep.composer.sending') : copyFor('prep.composer.send') }}
      </ActionButton>
    </div>
    <p v-if="restored" class="composer__hint">{{ copyFor('prep.composer.restore_draft') }}</p>
  </form>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import ActionButton from './primitives/ActionButton.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    busy?: boolean
    /** 由 store 持有（localStorage 只落 UI 偏好/草稿）。 */
    modelValue?: string
  }>(),
  { disabled: false, busy: false, modelValue: '' },
)

const emit = defineEmits<{ send: [text: string]; 'update:modelValue': [text: string] }>()

const draft = ref(props.modelValue)
const restored = ref(Boolean(props.modelValue))

watch(draft, (v) => emit('update:modelValue', v))
watch(
  () => props.modelValue,
  (v) => {
    if (v !== draft.value) draft.value = v
  },
)

onMounted(() => {
  restored.value = Boolean(draft.value)
})

function submit() {
  const text = draft.value.trim()
  if (!text || props.disabled || props.busy) return
  emit('send', text)
  draft.value = ''
  restored.value = false
}
</script>

<style scoped>
.composer {
  border-top: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  padding: var(--ma-space-2);
}

.composer__label {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.composer__row {
  display: flex;
  align-items: flex-end;
  gap: var(--ma-space-2);
}

.composer__input {
  flex: 1 1 auto;
  resize: vertical;
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  padding: var(--ma-space-2);
  font-family: var(--ma-font-family-ui);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
  background: var(--ma-surface-default);
}

.composer__input:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: 1px;
}

.composer__hint {
  margin: var(--ma-space-1) 0 0;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}
</style>
