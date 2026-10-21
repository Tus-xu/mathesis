<script setup lang="ts">
/**
 * 教案工具条（黄金图：撤销/重做｜正文▾｜16▾｜B I U｜对齐×2｜列表×2｜图片 表格 链接；按钮 32px）。
 *
 * 反空壳：所有按钮都是真实富文本命令（作用于当前聚焦的可编辑块），
 * 无可编辑目标时诚实禁用（不弹出假 Toast）。命令由父组件转交编辑器执行。
 */
import { reactive } from 'vue'

const props = defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ (e: 'command', cmd: string, value?: string): void }>()

const menu = reactive<{ open: 'format' | 'size' | null }>({ open: null })

const FORMATS: { label: string; cmd: string; value: string }[] = [
  { label: '正文', cmd: 'formatBlock', value: 'p' },
  { label: '小标题', cmd: 'formatBlock', value: 'h3' },
  { label: '章节标题', cmd: 'formatBlock', value: 'h2' },
]
const SIZES: { label: string; cmd: string; value: string }[] = [
  { label: '14', cmd: 'fontSize', value: '2' },
  { label: '16', cmd: 'fontSize', value: '3' },
  { label: '18', cmd: 'fontSize', value: '4' },
  { label: '24', cmd: 'fontSize', value: '5' },
]

/** 图片/表格/链接：真实插入（父组件执行），仅在有可编辑目标时可用。 */
function promptImage() {
  if (!props.enabled) return
  const url = window.prompt('图片地址（URL）')
  if (url && url.trim()) emit('command', 'insertImage', url.trim())
}
function promptLink() {
  if (!props.enabled) return
  const url = window.prompt('链接地址（URL）')
  if (url && url.trim()) emit('command', 'createLink', url.trim())
}
function insertTable() {
  if (!props.enabled) return
  emit(
    'command',
    'insertHTML',
    '<table><tbody><tr><td>列1</td><td>列2</td></tr><tr><td></td><td></td></tr></tbody></table>',
  )
}
function run(cmd: string, value?: string) {
  if (!props.enabled) return
  menu.open = null
  emit('command', cmd, value)
}
</script>

<template>
  <div class="pc-toolbar" role="toolbar" aria-label="教案格式工具条">
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="撤销" @click="run('undo')">
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path d="M6 4 3.2 6.8 6 9.6M3.4 6.8H10a4 4 0 0 1 0 8H7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="重做" @click="run('redo')">
      <svg viewBox="0 0 18 18" aria-hidden="true">
        <path d="M12 4 14.8 6.8 12 9.6M14.6 6.8H8a4 4 0 0 0 0 8h3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <div class="pc-toolbar__menu-wrap">
      <button type="button" class="pc-toolbar__menu" :disabled="!enabled" @click="menu.open = menu.open === 'format' ? null : 'format'">
        正文
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
      </button>
      <ul v-if="menu.open === 'format'" class="pc-toolbar__dropdown">
        <li v-for="f in FORMATS" :key="f.value">
          <button type="button" @click="run(f.cmd, f.value)">{{ f.label }}</button>
        </li>
      </ul>
    </div>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <div class="pc-toolbar__menu-wrap">
      <button type="button" class="pc-toolbar__menu pc-toolbar__menu--size" :disabled="!enabled" @click="menu.open = menu.open === 'size' ? null : 'size'">
        16
        <svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
      </button>
      <ul v-if="menu.open === 'size'" class="pc-toolbar__dropdown pc-toolbar__dropdown--size">
        <li v-for="s in SIZES" :key="s.value">
          <button type="button" @click="run(s.cmd, s.value)">{{ s.label }}</button>
        </li>
      </ul>
    </div>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <button type="button" class="pc-toolbar__btn pc-toolbar__btn--text" :disabled="!enabled" title="加粗" @click="run('bold')"><strong>B</strong></button>
    <button type="button" class="pc-toolbar__btn pc-toolbar__btn--text" :disabled="!enabled" title="斜体" @click="run('italic')"><em>I</em></button>
    <button type="button" class="pc-toolbar__btn pc-toolbar__btn--text" :disabled="!enabled" title="下划线" @click="run('underline')"><span style="text-decoration: underline">U</span></button>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="左对齐" @click="run('justifyLeft')">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M3 4.5h12M3 7.5h8M3 10.5h12M3 13.5h8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
    </button>
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="居中" @click="run('justifyCenter')">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M3 4.5h12M5 7.5h8M3 10.5h12M5 13.5h8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
    </button>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="无序列表" @click="run('insertUnorderedList')">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M7 5h8M7 9h8M7 13h8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><circle cx="4" cy="5" r="1" fill="currentColor" /><circle cx="4" cy="9" r="1" fill="currentColor" /><circle cx="4" cy="13" r="1" fill="currentColor" /></svg>
    </button>
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="有序列表" @click="run('insertOrderedList')">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M7 5h8M7 9h8M7 13h8" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /><text x="2.4" y="6.2" font-size="4.6" fill="currentColor">1</text><text x="2.4" y="10.2" font-size="4.6" fill="currentColor">2</text><text x="2.4" y="14.2" font-size="4.6" fill="currentColor">3</text></svg>
    </button>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <span class="pc-toolbar__sep" aria-hidden="true"></span>

    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="插入图片" @click="promptImage">
      <svg viewBox="0 0 18 18" aria-hidden="true"><rect x="3" y="3.5" width="12" height="11" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3" /><circle cx="6.6" cy="7" r="1.2" fill="currentColor" /><path d="M3.6 12.5 7.4 9l2.6 2.4 2-1.7 2.4 2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" /></svg>
    </button>
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="插入表格" @click="insertTable">
      <svg viewBox="0 0 18 18" aria-hidden="true"><rect x="3" y="3.5" width="12" height="11" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.3" /><path d="M3 7.2h12M3 10.9h12M7.6 3.5v11M11.2 3.5v11" fill="none" stroke="currentColor" stroke-width="1.1" /></svg>
    </button>
    <button type="button" class="pc-toolbar__btn" :disabled="!enabled" title="插入链接" @click="promptLink">
      <svg viewBox="0 0 18 18" aria-hidden="true"><path d="M7.4 9.9 10.6 6.7M6.6 6.4l1.3-1.3a2.7 2.7 0 0 1 3.8 3.8l-1.3 1.3M11.4 11.6l-1.3 1.3a2.7 2.7 0 0 1-3.8-3.8l1.3-1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
    </button>
  </div>
</template>

<style scoped>
.pc-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 2px;
  height: 56px;
  padding: 0 22px;
  background: var(--pc-panel);
  border-bottom: 1px solid var(--pc-line);
  box-sizing: border-box;
}
.pc-toolbar__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-toolbar__btn svg {
  width: 17px;
  height: 17px;
}
.pc-toolbar__btn--text {
  font-size: 15px;
  font-family: Georgia, 'Times New Roman', serif;
  line-height: 1;
}
.pc-toolbar__btn:hover:not(:disabled) {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-toolbar__btn:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}
.pc-toolbar__sep {
  width: 1px;
  height: 20px;
  margin: 0 8px;
  background: var(--pc-line);
}
.pc-toolbar__menu-wrap {
  position: relative;
}
.pc-toolbar__menu {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-toolbar__menu--size {
  width: 46px;
  justify-content: center;
}
.pc-toolbar__menu svg {
  width: 11px;
  height: 11px;
  color: var(--pc-text-muted);
}
.pc-toolbar__menu:hover:not(:disabled) {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-toolbar__menu:disabled {
  color: #cbd5e1;
  cursor: not-allowed;
}
.pc-toolbar__dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 20;
  min-width: 120px;
  margin: 0;
  padding: 5px;
  list-style: none;
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  box-shadow: var(--pc-shadow-hover);
}
.pc-toolbar__dropdown--size {
  min-width: 72px;
}
.pc-toolbar__dropdown button {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-toolbar__dropdown button:hover {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
</style>
