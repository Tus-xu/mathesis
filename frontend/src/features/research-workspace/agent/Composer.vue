<template>
  <div class="rw-composer">
    <textarea
      ref="input"
      v-model="text"
      class="rw-composer__input"
      :placeholder="placeholder"
      rows="1"
      :aria-label="placeholder"
      @keydown="onKeydown"
      @input="autoGrow"
    />
    <div class="rw-composer__bar">
      <div class="rw-composer__tools">
        <button type="button" class="rw-composer__tool" title="引用当前选区" aria-label="引用当前选区" @click="tool('attach')">
          <AttachOutline />
        </button>
        <button type="button" class="rw-composer__tool" title="提及（@）" aria-label="提及" @click="tool('mention')">
          <AtOutline />
        </button>
        <button type="button" class="rw-composer__tool" title="引用项目文献" aria-label="引用项目文献" @click="tool('knowledge')">
          <BookOutline />
        </button>
      </div>
      <button
        type="button"
        class="rw-composer__send"
        :disabled="!canSend"
        aria-label="发送"
        title="发送"
        @click="submit"
      >
        <PaperPlaneOutline />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 底部输入框（VISUAL-SPEC §4.4-5 / 宪法 §31）。
 *
 * 始终存在的自然语言输入：用户可直接说「解释这里为什么需要 coercivity」。
 * 占位文案 READ / WRITE 不同。左下 `📎 @ 📖` 为**真实文本操作**（引用选区 / 提及 /
 * 引用项目文献），右下主蓝圆形发送按钮。**不承载任何参数表单**（§30）。
 * `Enter` 发送、`Shift+Enter` 换行（§63 键盘友好）。
 */
import { computed, nextTick, ref } from 'vue'
import { AtOutline, AttachOutline, BookOutline, PaperPlaneOutline } from '@vicons/ionicons5'
import type { WorkspaceMode } from '../api/types'

const props = withDefaults(
  defineProps<{
    mode: WorkspaceMode
    /** 当前选区原文（用于「引用当前选区」真实插入） */
    selection?: string
    /** 当前文献标题（用于「引用项目文献」） */
    paperTitle?: string
    disabled?: boolean
    /** 对话请求进行中：禁用发送，避免重复投递（默认 false，面板可选择性绑定）。 */
    busy?: boolean
  }>(),
  { selection: '', paperTitle: '', disabled: false, busy: false },
)

const emit = defineEmits<{ (e: 'send', text: string): void }>()

const text = ref('')
const input = ref<HTMLTextAreaElement | null>(null)

const placeholder = computed(() =>
  props.mode === 'write'
    ? '有什么可以帮你的吗？'
    : '直接告诉我你想完成什么：解释推导、找证据、写入笔记、生成引用…',
)

const canSend = computed(
  () => !props.disabled && !props.busy && text.value.trim().length > 0,
)

function autoGrow(): void {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(96, el.scrollHeight)}px`
}

function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

function tool(kind: 'attach' | 'mention' | 'knowledge'): void {
  if (kind === 'mention') {
    insert('@')
    return
  }
  if (kind === 'attach') {
    const sel = (props.selection || '').trim()
    insert(sel ? `> ${sel}\n` : '> ')
    return
  }
  const title = (props.paperTitle || '').trim()
  insert(title ? `@${title} ` : '@项目文献 ')
}

function insert(fragment: string): void {
  text.value = text.value ? `${text.value}${fragment}` : fragment
  void nextTick(() => {
    input.value?.focus()
    autoGrow()
  })
}

function submit(): void {
  if (!canSend.value) return
  emit('send', text.value.trim())
  text.value = ''
  void nextTick(autoGrow)
}

defineExpose({ focus: () => input.value?.focus() })
</script>

<style scoped>
.rw-composer {
  flex: 0 0 auto;
  margin: 0 14px 14px;
  padding: 8px 10px 8px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
}
.rw-composer__input {
  width: 100%;
  min-height: 22px;
  max-height: 96px;
  padding: 2px 0;
  border: none;
  outline: none;
  resize: none;
  background: none;
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  line-height: var(--rw-lh-body);
}
.rw-composer__input::placeholder {
  color: var(--rw-text-3);
}
.rw-composer__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
}
.rw-composer__tools {
  display: flex;
  align-items: center;
  gap: 4px;
}
.rw-composer__tool {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--rw-radius);
  background: none;
  color: var(--rw-text-3);
  font-size: 19px;
  cursor: pointer;
}
/* §4.4-5 Composer 底部图标 18px（📎 @ 📖） */
.rw-composer__tool :deep(svg) {
  width: 18px;
  height: 18px;
}
.rw-composer__tool:hover {
  color: var(--rw-primary);
}
.rw-composer__send {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--rw-primary);
  color: var(--rw-surface);
  font-size: 17px;
  cursor: pointer;
}
/* §4.4-5 主蓝圆形发送按钮内纸飞机图标 16px（按钮本体 32px） */
.rw-composer__send :deep(svg) {
  width: 16px;
  height: 16px;
}
.rw-composer__send:disabled {
  background: var(--rw-border-strong);
  cursor: default;
}
.rw-composer__send:not(:disabled):hover {
  background: var(--rw-primary-hover);
}

/* ── WRITE 314px 紧凑态：Composer 压缩（VISUAL-SPEC §1.5）── */
@container rw-agent (max-width: 400px) {
  .rw-composer {
    margin: 0 10px 10px;
    padding: 6px 8px;
  }
  .rw-composer__input {
    font-size: var(--rw-fs-sm);
  }
  .rw-composer__tool {
    width: 26px;
    height: 26px;
  }
}
</style>
