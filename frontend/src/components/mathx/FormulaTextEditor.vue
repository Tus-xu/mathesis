<template>
  <div class="ftex" :data-testid="testid" :class="{ 'is-empty': !modelValue && editing === null }">
    <!-- 分词渲染：文本段 + 公式段（教师全程不见 $..$） -->
    <div class="ftex__surface" @click="onSurfaceClick">
      <template v-for="(seg, i) in segments" :key="seg.key">
        <!-- 文本段：点击进入行内编辑 -->
        <template v-if="seg.type === 'text'">
          <textarea
            v-if="editing === seg.key"
            :ref="(el) => setTextareaRef(el as HTMLTextAreaElement | null, i)"
            v-model="editBuf"
            class="ftex__ta"
            rows="1"
            :data-testid="`${testid}-seg-${i}`"
            @blur="commitText(i)"
            @input="autoGrow"
            @keydown.enter.exact.prevent="commitText(i)"
            @keyup="trackCaret(i)"
            @click="trackCaret(i)"
          />
          <span
            v-else class="ftex__text"
            :data-testid="`${testid}-text-${i}`"
            @focus="trackCaret(i)"
            @click="startTextEdit(i); trackCaret(i)"
          >{{ seg.value || (segments.length === 1 ? placeholder : '') }}</span>
        </template>
        <!-- 公式段：KaTeX 渲染 + 点击弹 MathField 修改 -->
        <span
          v-else-if="editing !== seg.key"
          class="ftex__formula"
          :data-testid="`${testid}-formula-${i}`"
          title="点击修改这个公式"
          @click.stop="startFormulaEdit(i)"
          v-html="renderLatex(seg.value)"
        />
        <span v-else class="ftex__formula-edit" :data-testid="`${testid}-formula-edit-${i}`" @click.stop>
          <MathField
            :ref="(el) => setFormulaFieldRef(el as InstanceType<typeof MathField> | null)"
            :model-value="editBuf"
            :font-size="20"
            @update:model-value="editBuf = $event"
          />
          <span class="ftex__formula-ops">
            <button class="tv3-btn tv3-btn--sm" type="button" @click="cancelEdit">取消</button>
            <button class="tv3-btn tv3-btn--sm tv3-btn--primary" type="button" :data-testid="`${testid}-formula-ok`" @click="commitFormula(i)">✓ 确定</button>
          </span>
        </span>
      </template>
      <span v-if="!segments.length" class="ftex__text ftex__placeholder" @click="startTextEdit(0)">{{ placeholder }}</span>
    </div>

    <!-- 插入成功后的可撤销提示（一次） -->
    <div v-if="undoTip" class="ftex__undo">
      已插入到光标处
      <button class="tv3-btn tv3-btn--sm" type="button" :data-testid="`${testid}-undo`" @click="undo">↶ 撤销</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * FormulaTextEditor —— 教案正文的双态编辑器（B1 数学内容捕获台的地基）
 * - 展示态：文本 + 公式分词渲染，教师全程不见 $..$（整改提示词 §5.2）
 * - 编辑态：点文本段改文字；点公式段弹 MathField 修改；插入落在最近光标而非段尾
 * - 存储仍是「文本 + $..$」字符串，不改共享契约（映射现有三字符串字段）
 */
import { computed, nextTick, ref } from 'vue'
import MathField from './MathField.vue'
import { renderLatex } from './latex'

const props = defineProps<{
  modelValue: string
  placeholder?: string
  testid?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'focus'): void }>()

interface Seg { type: 'text' | 'formula'; value: string; key: string }
let segSeq = 0
const parse = (src: string): Seg[] => {
  const out: Seg[] = []
  const re = /\$([^$]+)\$/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    if (m.index > last) out.push({ type: 'text', value: src.slice(last, m.index), key: `t${++segSeq}` })
    out.push({ type: 'formula', value: m[1], key: `f${++segSeq}` })
    last = m.index + m[0].length
  }
  if (last < src.length) out.push({ type: 'text', value: src.slice(last), key: `t${++segSeq}` })
  return out
}

const segsSignal = ref(0)
const segments = computed(() => {
  void segsSignal.value
  return parse(props.modelValue)
})

const editing = ref<string | null>(null)
const editBuf = ref('')
const caret = ref<{ segIndex: number; offset: number } | null>(null)
const undoTip = ref(false)
const history = ref<string[]>([])
let taEl: HTMLTextAreaElement | null = null
let mfEl: InstanceType<typeof MathField> | null = null
const setTextareaRef = (el: HTMLTextAreaElement | null, _i: number) => { taEl = el; if (el) { autoGrow(); nextTick(() => el.focus()) } }
const setFormulaFieldRef = (el: InstanceType<typeof MathField> | null) => { mfEl = el }

function serialize(segs: Seg[]): string {
  return segs.map((s) => (s.type === 'formula' ? `$${s.value}$` : s.value)).join('')
}

function startTextEdit(i: number) {
  editing.value = segments.value[i]?.key ?? null
  editBuf.value = segments.value[i]?.value ?? ''
  caret.value = { segIndex: i, offset: editBuf.value.length }
  emit('focus')
}
function trackCaret(i: number) {
  const pos = taEl ? taEl.selectionStart : editBuf.value.length
  caret.value = { segIndex: i, offset: pos ?? editBuf.value.length }
}
function autoGrow() {
  if (!taEl) return
  taEl.style.height = 'auto'
  taEl.style.height = `${Math.max(28, taEl.scrollHeight)}px`
}
function commitText(i: number) {
  if (editing.value === null) return
  const segs = parse(props.modelValue)
  if (segs[i]) segs[i].value = editBuf.value
  emit('update:modelValue', serialize(segs))
  editing.value = null
  segsSignal.value++
}
function startFormulaEdit(i: number) {
  editing.value = segments.value[i]?.key ?? null
  editBuf.value = segments.value[i]?.value ?? ''
  emit('focus')
}
function commitFormula(i: number) {
  if (editing.value === null) return
  const segs = parse(props.modelValue)
  if (segs[i] && editBuf.value.trim()) segs[i].value = editBuf.value.trim()
  emit('update:modelValue', serialize(segs))
  editing.value = null
  segsSignal.value++
}
function cancelEdit() {
  editing.value = null
  segsSignal.value++
}

/** 捕获台调用：把 latex 作为公式段插到最近光标（文本段 offset 处；无光标则追加末尾） */
function insertAtCaret(latex: string) {
  if (!latex.trim()) return
  history.value.push(props.modelValue)
  const segs = parse(props.modelValue)
  const c = caret.value
  if (c && segs[c.segIndex]?.type === 'text') {
    const seg = segs[c.segIndex]
    const off = Math.min(c.offset ?? seg.value.length, seg.value.length)
    const before = seg.value.slice(0, off)
    const after = seg.value.slice(off)
    const insert = `$${latex}$`
    seg.value = before + insert + after
    // 插入后光标落在公式之后的文本段
    if (after) {
      segs.splice(c.segIndex + 1, 0, { type: 'text', value: after, key: `t${++segSeq}` })
      seg.value = before + insert
      caret.value = { segIndex: c.segIndex + 1, offset: 0 }
    } else {
      caret.value = { segIndex: c.segIndex, offset: off + insert.length }
    }
  } else {
    segs.push({ type: 'formula', value: latex, key: `f${++segSeq}` })
    caret.value = { segIndex: segs.length - 1, offset: 0 }
  }
  emit('update:modelValue', serialize(segs))
  segsSignal.value++
  undoTip.value = true
  window.setTimeout(() => { undoTip.value = false }, 6000)
}

/** 共备桌内联入口（清单3）：光标处落一个空公式占位并直接进入行内 MathField——不弹模态、不打断写作流 */
function insertFormulaInline() {
  const token = '$?$'
  const segs = parse(props.modelValue)
  if (editing.value === null) {
    let idx = segs.findIndex((s) => s.type === 'text')
    let off = idx >= 0 ? segs[idx].value.length : 0
    const c = caret.value
    if (c && segs[c.segIndex]?.type === 'text') {
      idx = c.segIndex
      off = Math.min(c.offset ?? 0, segs[idx].value.length)
    }
    if (idx < 0) {
      segs.push({ type: 'formula', value: '?', key: `f${++segSeq}` })
    } else {
      const seg = segs[idx]
      const before = seg.value.slice(0, off)
      const after = seg.value.slice(off)
      seg.value = before + token + after
      if (after) segs.splice(idx + 1, 0, { type: 'text', value: after, key: `t${++segSeq}` })
    }
    emit('update:modelValue', serialize(segs))
    segsSignal.value++
    // 占位公式写回后等父组件完成重渲染（props 更新、segments 重算稳定），再进入行内编辑
    void nextTick(() => {
      const ni = segments.value.findIndex((s) => s.type === 'formula' && s.value === '?')
      if (ni >= 0) startFormulaEdit(ni)
    })
  } else {
    const i = segments.value.findIndex((s) => s.key === editing.value)
    if (i >= 0 && segments.value[i].type === 'text') {
      const off = Math.min(caret.value?.segIndex === i ? (caret.value?.offset ?? editBuf.value.length) : editBuf.value.length, editBuf.value.length)
      editBuf.value = editBuf.value.slice(0, off) + token + editBuf.value.slice(off)
      caret.value = { segIndex: i, offset: off + token.length }
    } else {
      editBuf.value += token
    }
  }
}

function undo() {
  const prev = history.value.pop()
  if (prev !== undefined) {
    emit('update:modelValue', prev)
    segsSignal.value++
  }
  undoTip.value = false
}

function onSurfaceClick() {
  if (editing.value === null && !segments.value.length) startTextEdit(0)
}

/* 供宿主调用：光标处插入（捕获台）/ 内联插公式（共备桌，清单3）/ 撤销 */
defineExpose({ insertAtCaret, insertFormulaInline, undo })
</script>

<style scoped>
.ftex { position: relative; }
.ftex__surface {
  min-height: 64px; padding: 8px 10px; border-radius: 10px;
  border: 1px solid var(--tv3-line); background: #fff; cursor: text;
  font-size: 13px; line-height: 1.8; color: var(--tv3-ink);
}
.ftex.is-empty .ftex__surface { color: var(--tv3-ink4); }
.ftex__text { white-space: pre-wrap; word-break: break-word; outline: none; }
.ftex__placeholder { color: var(--tv3-ink4); }
.ftex__formula {
  display: inline-block; padding: 0 6px; margin: 0 2px; border-radius: 6px; cursor: pointer;
  background: rgba(15, 71, 135, 0.06); border: 1px dashed rgba(15, 71, 135, 0.35);
  vertical-align: middle;
}
.ftex__formula:hover { border-color: var(--tv3-gold, #c99735); background: rgba(201, 151, 53, 0.08); }
.ftex__formula-edit {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin: 2px 0;
  border: 1px solid var(--tv3-gold, #c99735); border-radius: 10px; background: #fffdf6;
}
.ftex__formula-ops { display: flex; gap: 6px; flex-shrink: 0; }
.ftex__ta {
  width: 100%; border: 1px dashed var(--tv3-gold, #c99735); border-radius: 6px;
  font: inherit; padding: 2px 4px; resize: none; overflow: hidden; background: #fffdf6;
}
.ftex__undo {
  position: absolute; right: 8px; bottom: -26px; z-index: 5;
  display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--tv3-ink2);
  background: var(--tv3-gold-soft, #fdf6e3); border: 1px solid var(--tv3-gold-border, #e5c96a);
  border-radius: 8px; padding: 3px 8px;
}
</style>
