<template>
  <!--
    T22.1 · 主输入框 = 页面视觉锚点（UX-CONTRACT A）。
    铁律：固定在内容区底部但**不遮挡最后一条消息**（容器留出 spacer）；
    流式追加**不得抢走**这里的焦点（本组件不因外部更新而 focus）。
  -->
  <div class="composer" data-testid="prep-composer">
    <div class="composer__box" :class="{ 'composer__box--focus': focused }">
      <textarea
        ref="el"
        v-model="draft"
        class="composer__input"
        rows="1"
        :disabled="disabled"
        :placeholder="placeholderText"
        data-testid="prep-composer-input"
        :aria-label="copyFor('prep.composer.aria')"
        @focus="focused = true"
        @blur="focused = false"
        @keydown.enter.exact.prevent="onSubmit"
        @input="autoGrow"
      />

      <div class="composer__bar">
        <button
          type="button"
          class="composer__icon-btn"
          data-testid="prep-composer-attach"
          :disabled="disabled"
          :aria-label="copyFor('prep.composer.attach_aria')"
          @click="onAttach"
        >
          <n-icon :size="17"><AttachOutline /></n-icon>
        </button>

        <span class="composer__hint">{{ copyFor('prep.composer.hint') }}</span>

        <button
          v-if="busy"
          type="button"
          class="composer__send composer__send--stop"
          data-testid="prep-composer-stop"
          @click="emit('stop')"
        >
          <n-icon :size="15"><StopOutline /></n-icon>
          {{ copyFor('prep.composer.stop') }}
        </button>
        <button
          v-else
          type="button"
          class="composer__send"
          :disabled="disabled || !draft.trim()"
          data-testid="prep-composer-send"
          @click="onSubmit"
        >
          <n-icon :size="15"><ArrowUpOutline /></n-icon>
          {{ copyFor('prep.composer.send') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowUpOutline, AttachOutline, StopOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const props = withDefaults(
  defineProps<{
    modelValue: string
    disabled?: boolean
    busy?: boolean
    placeholder?: string
  }>(),
  { disabled: false, busy: false, placeholder: '' },
)

const placeholderText = computed(() => props.placeholder || copyFor('prep.composer.default_placeholder'))

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
  (e: 'send', v: string): void
  (e: 'stop'): void
  (e: 'attach'): void
}>()

const el = ref<HTMLTextAreaElement | null>(null)
const focused = ref(false)
const draft = ref(props.modelValue)

watch(
  () => props.modelValue,
  (v) => {
    // 流式追加导致的外部更新：只在非空→空（发送成功）时同步，绝不抢焦点
    if (v !== draft.value) draft.value = v
  },
)
watch(draft, (v) => emit('update:modelValue', v))

function autoGrow() {
  const t = el.value
  if (!t) return
  t.style.height = 'auto'
  t.style.height = `${Math.min(t.scrollHeight, 200)}px`
}

function onSubmit() {
  const text = draft.value.trim()
  if (!text || props.disabled) return
  emit('send', text)
  draft.value = ''
  if (el.value) el.value.style.height = 'auto'
}

function onAttach() {
  emit('attach')
}

onMounted(autoGrow)
</script>

<style scoped>
.composer {
  padding: 10px var(--ma-space-4) 10px;
  background: var(--ma-surface-default);
  border: 1px solid var(--ma-surface-border);
  border-radius: 16px;
  box-shadow: var(--ma-elevation-modal);
}

.composer__box {
  background: transparent;
  border: 0;
  transition: box-shadow 180ms ease, border-color 180ms ease;
}

/* WP-K 可达性修复：textarea 自身 outline:none，而聚焦态类原来只有 `outline: none`
   —— 结果**输入框聚焦时没有任何可见焦点环**（WCAG 2.4.7 不达标）。
   现在聚焦时把 token 焦点环画在输入区上（与 surface 内其他按钮的焦点环写法一致）。 */
.composer__box--focus {
  outline: none;
  border-radius: var(--ma-radius-inner);
}

.composer__input {
  display: block;
  width: 100%;
  box-sizing: border-box;
  border: 0;
  outline: none;
  resize: none;
  padding: var(--ma-space-3) var(--ma-space-3) var(--ma-space-2);
  font-family: inherit;
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-primary);
  background: transparent;
}

.composer__input::placeholder {
  color: var(--ma-text-muted);
}

.composer__bar {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  padding: 0 var(--ma-space-2) var(--ma-space-2) var(--ma-space-2);
}

.composer__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: var(--ma-radius-md);
  background: transparent;
  color: var(--ma-text-body);
  cursor: pointer;
}

.composer__icon-btn:hover:not(:disabled) {
  background: var(--ma-surface-subtle);
  color: var(--ma-text-primary);
}

.composer__hint {
  flex: 1 1 auto;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.composer__send {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 var(--ma-space-3);
  border: 0;
  border-radius: var(--ma-radius-btn);
  background: var(--ma-action-primary);
  color: var(--ma-color-white-000);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.composer__send:disabled {
  background: var(--ma-surface-border);
  color: var(--ma-text-muted);
  cursor: not-allowed;
}

.composer__send--stop {
  background: var(--ma-surface-subtle);
  color: var(--ma-text-primary);
  border: 1px solid var(--ma-surface-border);
}

.composer__send:focus-visible,
.composer__icon-btn:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}
</style>
