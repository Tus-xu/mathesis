<template>
  <div
    ref="host"
    class="mx-field"
    :class="{ 'mx-field--editing': editing, 'mx-field--readonly': readonly }"
    :data-testid="testid"
    @dragover.prevent
    @drop.stop.prevent="onHostDrop"
  >
    <div v-if="readonly" class="mx-field__static" v-html="staticHtml" />
  </div>
</template>

<script setup lang="ts">
/**
 * MathField —— MathLive 公式编辑器封装（SPEC §5.5 六大行为中的组件侧）
 * math-field 是 web component：运行时创建，避免 Vue 模板编译器按组件解析产生告警。
 * 行为由父级组合完成：点按/拖拽插入（MathKeyboard + insert()）、双击进入（focus）、
 * 退格删结构（MathLive 内建）、LaTeX 高级视图（toggleLatexView）。
 * 拖拽包裹：键盘键拖到本组件上 drop，模板首个 #0 替换为当前选区（#@）→ 拖 √ 到选中的 3 上变 √3。
 * 导出：getValue('latex-without-placeholders')，空槽不再产出 \placeholder{}；
 * emit 防抖 120ms，避免逐键触发画布 KaTeX 重建造成闪烁。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import 'mathlive'
import { convertLatexToMarkup } from 'mathlive'
import 'mathlive/static.css'
import { renderLatex } from './latex'

const props = withDefaults(defineProps<{
  modelValue?: string
  readonly?: boolean
  placeholder?: string
  fontSize?: number
  testid?: string
}>(), {
  modelValue: '',
  readonly: false,
  placeholder: '',
  fontSize: 20,
})

const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'focus'): void; (e: 'blur'): void }>()

const host = ref<HTMLElement | null>(null)
const editing = ref(false)
let mf: any = null
let lastEmitted = ''
let emitTimer: number | undefined

const staticHtml = computed(() => {
  if (!props.modelValue) return `<span class="mx-field__empty">（空公式）</span>`
  try { return convertLatexToMarkup(props.modelValue) } catch { return renderLatex(props.modelValue) }
})

function readClean(): string {
  try { return String(mf.getValue('latex-without-placeholders')) } catch { return String(mf.value ?? '') }
}

function scheduleEmit() {
  window.clearTimeout(emitTimer)
  emitTimer = window.setTimeout(() => {
    lastEmitted = readClean()
    emit('update:modelValue', lastEmitted)
  }, 120)
}

function createMf() {
  if (!host.value) return
  mf = document.createElement('math-field')
  mf.value = props.modelValue || ''
  mf.setAttribute('style', `--_font-size:${props.fontSize}px;font-size:${props.fontSize}px;min-width:120px`)
  if (props.placeholder) mf.setAttribute('placeholder', props.placeholder)
  mf.addEventListener('input', scheduleEmit)
  mf.addEventListener('focusin', () => { editing.value = true; emit('focus') })
  mf.addEventListener('focusout', () => { editing.value = false; emit('blur') })
  host.value.appendChild(mf)
}

onMounted(() => { if (!props.readonly) createMf() })

watch(() => props.modelValue, (v) => {
  if (!mf || v === lastEmitted) return
  mf.value = v || ''
})
watch(() => props.readonly, (r) => {
  if (!r && !mf) createMf()
  else if (r && mf) { mf.remove(); mf = null }
})

onBeforeUnmount(() => {
  window.clearTimeout(emitTimer)
  if (mf) { mf.remove(); mf = null }
})

/** 点按 / 拖拽入位：结构模板插入（占位符 #0 光标落位） */
function insert(latex: string) {
  mf?.executeCommand(['insert', latex, { focus: true, feedback: false, selectionMode: 'placeholder', format: 'latex' }])
}
function focus() { mf?.focus() }
function toggleLatexView() { mf?.executeCommand(['toggleLatexMode']) }

/** 拖键盘键到公式上：包裹结构（首槽位吃掉当前选区） */
function onHostDrop(ev: DragEvent) {
  if (props.readonly || !mf) return
  const latex = ev.dataTransfer?.getData('mx/latex')
  if (!latex) return
  mf.focus()
  mf.executeCommand(['insert', latex.replace('#0', '#@'), { focus: true, feedback: false, selectionMode: 'placeholder', format: 'latex' }])
}
defineExpose({ insert, focus, toggleLatexView })
</script>

<style scoped>
.mx-field { display: inline-flex; min-height: 38px; align-items: center; }
.mx-field :deep(math-field) {
  border: 1.5px dashed var(--tv3-line); border-radius: 8px;
  padding: 2px 10px; background: #fff; outline: none;
  transition: border-color 0.15s ease;
}
.mx-field :deep(math-field:focus-within), .mx-field--editing :deep(math-field) { border-color: var(--tv3-gold); }
.mx-field__static { line-height: 1.5; }
.mx-field__empty { color: var(--tv3-ink4); font-size: 13px; }
</style>
