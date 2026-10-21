<script setup lang="ts">
/**
 * 语义元素渲染层（FROZEN-CONTRACT §A.4 版式派生）。
 *
 * 由 `role` 派生版式：definition（浅蓝定义带）→ figure + knowledge_card（并排）→
 * formula（公式胶囊）→ bullets（蓝点列表）→ steps（编号步骤）→ table（对比表）。
 * 页脚 note 由 SlideCanvas 统一渲染。
 * 教师双击可在线编辑（真实 emit `element-change`，由上层落库）。
 *
 * R5 编辑体验：
 * - 提交前预校验（与 ppt_v2 spec.py §A.3 同口径）：text≤400 / latex≤2000 / title≤60 /
 *   items 每项≤200 —— 超限**行内红字提示且不提交**（禁止静默丢弃教师输入）；
 * - editable 页面每个元素块 hover 出现 删除 / 同 role 邻居间上移下移 小按钮
 *   （跨 role 换位禁用：渲染层按 role 槽位固定 CSS，跨 role 换位视觉无反馈）；
 * - 公式编辑：常用符号快捷插入 + KaTeX 语法错误中文提示（throwOnError:false 红字保留）。
 */
import { computed, ref } from 'vue'
import {
  isTeachingAnnotation,
  DECK_ELEMENT_LIMITS,
  elementLimitMessage,
  useDeckEditorStore,
} from '@/features/teacher-prep-center/stores/deckEditor'
import { renderMathText } from '@/features/teacher-prep-center/math/katex'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import MathFigure from './MathFigure.vue'
import { useDeckAssetUrls } from './deckAssets'
import type { MathObject, SlideElement } from '@/features/teacher-prep-center/api/deckApi'

const props = withDefaults(
  defineProps<{
    elements: SlideElement[]
    mathObject?: MathObject | null
    mathSelected?: boolean
    editable?: boolean
    /**
     * 本页图片资产 id（MATH_RENDER 通道：直线 / 二次函数 / 幂函数图象）。
     *
     * 与 `mathObject` 是两条并列的图形通道：前者由前端确定性渲染，后者是服务端已渲染
     * 好的 PNG（内容寻址、字节在 MinIO）。两条通道都要能上屏，否则教师会看到「有图页没图」。
     */
    assets?: string[]
    /** 显式 deckId（缺省回退 deckEditor store —— 教师端旧调用方零改动）。 */
    deckId?: string
    /**
     * 预解析好的资产 URL（assetId → data URL / presign URL）。
     * 传入时**直接使用、不再请求**教师端资产接口（学生端老师课堂用后端下发 data URL）；
     * 缺省（undefined）保持教师端旧行为（store.deckId + presign 取图）。
     */
    assetUrls?: Record<string, string>
    /** 分步演示：已揭示的步骤数（null/undefined=整页静态）。 */
    reveal?: number | null
  }>(),
  {
    mathObject: null,
    mathSelected: false,
    editable: false,
    assets: () => [],
    deckId: '',
    assetUrls: undefined,
    reveal: null,
  },
)

const emit = defineEmits<{
  (e: 'selectMath', selected: boolean): void
  (e: 'elementChange', payload: { index: number; next: SlideElement }): void
  (e: 'elementRemove', index: number): void
  (e: 'elementMove', index: number, dir: -1 | 1): void
}>()

const index = (role: string, from = 0): number =>
  props.elements.findIndex((el, i) => i >= from && el.role === role)

const defIdx = computed(() => index('definition'))
const def = computed(() => (defIdx.value >= 0 ? props.elements[defIdx.value] : null))
/** 定义带正文：教研批注（`引导学生…`）不是课件正文 → 不上屏（VISUAL-SPEC-V3 §3-S4.5）。 */
const defText = computed(() => {
  const t = def.value?.text || ''
  return isTeachingAnnotation(t) ? '' : t
})

/** 列表条目渲染层过滤：保留**原始下标**（在线编辑落库必须写回真实位置）。 */
const visibleIndexed = (items?: string[] | null): { it: string; k: number }[] =>
  (items || []).map((it, k) => ({ it, k })).filter((x) => !isTeachingAnnotation(x.it))
const figEl = computed(() => props.elements.find((el) => el.role === 'figure') ?? null)
/**
 * 外部预解析资产（学生端老师课堂）：有值时**直接用**，不调教师端资产接口。
 * `undefined` 表示调用方未接管 → 维持旧行为（deckEditor store + presign）。
 */
const externalUrls = computed<Record<string, string>>(() => props.assetUrls || {})
const usingExternalUrls = computed(() => props.assetUrls !== undefined && props.assetUrls !== null)
/**
 * 本页图片资产 id（每页至多 1 张，与 projector「每页至多 1 张」约束一致）。
 * 学生端 `deck_slide` 不重复携带 assets 数组时，以 `asset_urls` 的 key 兜底（真实存在的资产）。
 */
const assetIds = computed(() => {
  const declared = (props.assets || []).filter(Boolean)
  if (declared.length) return declared
  return usingExternalUrls.value ? Object.keys(externalUrls.value).filter(Boolean) : []
})
/** deckId 显式传入优先，缺省回退编辑器 store（避免改 8 处 SlideCanvas 调用链）。 */
const deckStore = useDeckEditorStore()
const { urls: fetchedUrls, failed: assetFailedIds } = useDeckAssetUrls(
  computed(() => (usingExternalUrls.value ? null : props.deckId || deckStore.deckId)),
  computed(() => (usingExternalUrls.value ? [] : assetIds.value)),
)
/** 实际生效的 assetId → URL 映射：外部优先，缺省回退教师端 presign 结果。 */
const assetUrls = computed(() => (usingExternalUrls.value ? externalUrls.value : fetchedUrls.value))
/** 首张资产图 URL；未解析完成时为空串（渲染「加载中」而非留白）。 */
const figureUrl = computed(() => assetUrls.value[assetIds.value[0]] || '')
/** 资产失败态：外部映射缺 key 也算失败（禁止永远「加载中」）。 */
const assetFailed = computed(() => {
  if (!assetIds.value.length) return false
  if (usingExternalUrls.value) return assetIds.value.some((id) => !externalUrls.value[id])
  return assetFailedIds.value.length > 0
})
/** 图形区是否需要占位：math_object（前端渲染）或图片资产（服务端渲染）任一存在。 */
const hasFigureArea = computed(() => !!figEl.value || assetIds.value.length > 0)
const cardEl = computed(() => props.elements.find((el) => el.role === 'knowledge_card') ?? null)
const cardIdx = computed(() => index('knowledge_card'))
const formulas = computed(() =>
  props.elements
    .map((el, i) => ({ el, i }))
    .filter((x) => x.el.role === 'formula' && !!x.el.latex),
)
const bullets = computed(() =>
  props.elements
    .map((el, i) => ({ el, i }))
    .filter((x) => x.el.role === 'bullets' && visibleIndexed(x.el.items).length > 0),
)
const steps = computed(() =>
  props.elements
    .map((el, i) => ({ el, i }))
    .filter((x) => x.el.role === 'steps' && visibleIndexed(x.el.items).length > 0),
)

/** 分步演示：当前 steps 元素之前已出现的步骤数（与 PreviewOverlay 的 totalSteps 同口径）。 */
function stepBase(elemIndex: number): number {
  let base = 0
  for (let i = 0; i < elemIndex; i += 1) {
    const el = props.elements[i]
    if (el?.role !== 'steps') continue
    base += (el.items || []).filter((x) => String(x || '').trim()).length
  }
  return base
}
function stepPending(elemIndex: number, k: number): boolean {
  if (props.reveal == null) return false
  return stepBase(elemIndex) + k >= props.reveal
}
const tables = computed(() =>
  props.elements.map((el, i) => ({ el, i })).filter((x) => x.el.role === 'table'),
)
const others = computed(() =>
  props.elements
    .map((el, i) => ({ el, i }))
    .filter(
      (x) =>
        !['definition', 'figure', 'knowledge_card', 'formula', 'bullets', 'steps', 'table', 'note'].includes(x.el.role) &&
        !isTeachingAnnotation(x.el.text),
    ),
)
const hasRow = computed(() => hasFigureArea.value || !!cardEl.value)
/** figure 元素在原始数组中的下标（元素块操作条用）。 */
const figIndex = computed(() => props.elements.findIndex((el) => el.role === 'figure'))
/** 知识卡条目（渲染层过滤，保留原始下标）。 */
const cardItems = computed(() => visibleIndexed(cardEl.value?.items))
const tex = (latex?: string | null): string => (latex ? `$${latex}$` : '')

/* ── 元素操作（元素块 hover 小按钮 → 上层 removeElement/moveElement 落库）──────
 * 同 role 邻居间才可换位：跨 role 换位在渲染层不改变任何视觉（槽位语义错位）。 */
const canMove = (i: number, dir: -1 | 1): boolean => {
  const arr = props.elements
  const j = i + dir
  return j >= 0 && j < arr.length && arr[j].role === arr[i].role
}

/* ── 在线编辑（双击 → 输入 → Enter/失焦提交；真实落库由上层完成） ─────
 * 注意：编辑输入位于 v-for 内，模板 ref 会被 Vue 收集成数组（focus 静默失效/抛错），
 * 统一用根节点 querySelector 聚焦（同一时刻本组件至多一个编辑器）。 */
const rootEl = ref<HTMLElement | null>(null)
const editKey = ref<string>('')
const editText = ref('')
const editLatex = ref('')
const editMode = ref<'text' | 'latex'>('text')
/** 提交前预校验的行内红字提示（空串 = 无提示）。 */
const editHint = ref('')

function focusEdit() {
  requestAnimationFrame(() => {
    const el = rootEl.value?.querySelector<HTMLInputElement | HTMLTextAreaElement>(
      'input.pc-el__edit, textarea.pc-el__edit-area',
    )
    el?.focus()
    if (el instanceof HTMLTextAreaElement) el.selectionStart = el.value.length
  })
}
function startEdit(key: string, text: string) {
  if (!props.editable) return
  editMode.value = 'text'
  editKey.value = key
  editText.value = text
  editHint.value = ''
  focusEdit()
}
/** 公式编辑（LaTeX）：输入框 + 实时 KaTeX 预览（禁裸源码上屏）。 */
function startLatexEdit(key: string, latex: string | null | undefined) {
  if (!props.editable) return
  editMode.value = 'latex'
  editKey.value = key
  editLatex.value = latex || ''
  editHint.value = ''
  focusEdit()
}
function cancelEdit() {
  editKey.value = ''
  editHint.value = ''
}
/** 超限提示 + 保持编辑态（不提交、不关闭，教师可就地精简）。 */
function rejectWithHint(msg: string) {
  editHint.value = msg
}
function commitText(i: number, el: SlideElement) {
  if (editKey.value !== `t${i}`) return
  const next = editText.value.trim()
  if (!next) {
    rejectWithHint('正文不能为空，请输入内容或按 Esc 取消')
    return
  }
  const over = elementLimitMessage({ text: next })
  if (over) {
    rejectWithHint(over)
    return
  }
  editKey.value = ''
  editHint.value = ''
  if (next !== (el.text ?? '')) emit('elementChange', { index: i, next: { ...el, text: next } })
}
function commitItem(i: number, el: SlideElement, itemIdx: number) {
  if (editKey.value !== `i${i}-${itemIdx}`) return
  const next = editText.value.trim()
  if (!next) {
    rejectWithHint('条目不能为空，请输入内容或按 Esc 取消')
    return
  }
  const over = elementLimitMessage({ items: [next] })
  if (over) {
    rejectWithHint(over)
    return
  }
  editKey.value = ''
  editHint.value = ''
  const items = [...(el.items ?? [])]
  items[itemIdx] = next
  emit('elementChange', { index: i, next: { ...el, items } })
}
/** 卡片标题提交（`ct` 键专属）。 */
function commitTitle(i: number, el: SlideElement) {
  if (editKey.value !== 'ct') return
  const next = editText.value.trim()
  if (!next) {
    rejectWithHint('标题不能为空，请输入内容或按 Esc 取消')
    return
  }
  const over = elementLimitMessage({ title: next })
  if (over) {
    rejectWithHint(over)
    return
  }
  editKey.value = ''
  editHint.value = ''
  if (next !== (el.title ?? '')) emit('elementChange', { index: i, next: { ...el, title: next } })
}
/** 公式提交（LaTeX → 落库；空式/超长**行内红字提示**，不再静默丢弃）。 */
function commitLatex(i: number, el: SlideElement) {
  if (editMode.value !== 'latex') return
  const key = editKey.value
  if (key !== `f${i}` && key !== 'fc') return
  const next = editLatex.value.trim()
  if (!next) {
    rejectWithHint('公式不能为空，请输入 LaTeX 内容或按 Esc 取消')
    return
  }
  if (next.length > DECK_ELEMENT_LIMITS.latex) {
    rejectWithHint(`公式 ${next.length}/${DECK_ELEMENT_LIMITS.latex} 字，超出上限，请拆分后再提交`)
    return
  }
  editKey.value = ''
  editHint.value = ''
  if (next !== (el.latex ?? '')) emit('elementChange', { index: i, next: { ...el, latex: next } })
}
function splitRow(row: string): string[] {
  return row.split('|').map((s) => s.trim())
}

/* ── 公式编辑增强：常用符号快捷插入（光标处插入；{ } 占位自动落光标）───────── */
const LATEX_SYMBOLS: { label: string; insert: string; title: string }[] = [
  { label: 'x⁄y', insert: '\\frac{}{}', title: '分数' },
  { label: 'x²', insert: '^{}', title: '上标' },
  { label: 'x₂', insert: '_{}', title: '下标' },
  { label: '√', insert: '\\sqrt{}', title: '根号' },
  { label: '≥', insert: '\\ge ', title: '大于等于' },
  { label: '≤', insert: '\\le ', title: '小于等于' },
  { label: '≠', insert: '\\ne ', title: '不等于' },
  { label: 'α', insert: '\\alpha ', title: '希腊字母 alpha' },
  { label: 'β', insert: '\\beta ', title: '希腊字母 beta' },
  { label: 'γ', insert: '\\gamma ', title: '希腊字母 gamma' },
  { label: 'θ', insert: '\\theta ', title: '希腊字母 theta' },
  { label: 'λ', insert: '\\lambda ', title: '希腊字母 lambda' },
  { label: 'μ', insert: '\\mu ', title: '希腊字母 mu' },
  { label: 'π', insert: '\\pi ', title: '希腊字母 pi' },
  { label: 'φ', insert: '\\varphi ', title: '希腊字母 phi' },
  { label: 'Δ', insert: '\\Delta ', title: '希腊字母 Delta' },
  { label: 'ω', insert: '\\omega ', title: '希腊字母 omega' },
]
/** 在光标处插入符号；含 `{}` 占位的片段把光标落到第一个 `{` 之后（便于续写）。 */
function insertSymbol(sym: { insert: string }) {
  const area = rootEl.value?.querySelector<HTMLTextAreaElement>('textarea.pc-el__edit-area')
  const s = sym.insert
  if (!area) {
    editLatex.value += s
    return
  }
  const start = area.selectionStart ?? editLatex.value.length
  const end = area.selectionEnd ?? start
  editLatex.value = editLatex.value.slice(0, start) + s + editLatex.value.slice(end)
  const brace = s.indexOf('{}')
  const caret = brace >= 0 ? start + brace + 1 : start + s.length
  requestAnimationFrame(() => {
    area.focus()
    area.setSelectionRange(caret, caret)
  })
}
/** KaTeX 预览是否含语法错误（throwOnError:false 会以 .katex-error 红字兜底渲染）。 */
const latexHasError = computed(() => {
  if (editMode.value !== 'latex' || !editKey.value) return false
  const src = editLatex.value.trim()
  if (!src) return false
  return renderMathText(tex(src)).includes('katex-error')
})
</script>

<template>
  <div ref="rootEl" class="pc-el" :class="{ 'pc-el--center': !hasRow, 'pc-el--editable': editable }">
    <!-- 定义带 -->
    <div v-if="def && defText" class="pc-el__def" @dblclick="startEdit(`t${defIdx}`, defText)">
      <input
        v-if="editKey === `t${defIdx}` && editable"
        v-model="editText"
        class="pc-el__edit"
        @keydown.enter="commitText(defIdx, def)"
        @keydown.esc="cancelEdit"
        @blur="commitText(defIdx, def)"
      />
      <KatexText v-else :text="def.text || ''" />
      <span v-if="editKey === `t${defIdx}` && editable && editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
      <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
        <button type="button" class="pc-el__op" :disabled="!canMove(defIdx, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', defIdx, -1)">↑</button>
        <button type="button" class="pc-el__op" :disabled="!canMove(defIdx, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', defIdx, 1)">↓</button>
        <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', defIdx)">✕</button>
      </span>
    </div>

    <!-- 图形 + 知识卡 并排 -->
    <div v-if="hasRow" class="pc-el__row">
      <div class="pc-el__main">
        <div v-if="hasFigureArea" class="pc-el__figwrap">
          <MathFigure
            v-if="mathObject"
            :math-object="mathObject"
            :selected="mathSelected"
            @select="emit('selectMath', true)"
          />
          <img v-else-if="figureUrl" class="pc-el__fig-img" :src="figureUrl" alt="本页数学图象" />
          <div v-else-if="assetFailed" class="pc-el__fig-empty">图片加载失败，请刷新后重试</div>
          <div v-else-if="assetIds.length" class="pc-el__fig-empty">图片加载中…</div>
          <div v-else class="pc-el__fig-empty">本页图形数据缺失</div>
          <span v-if="editable && figEl" class="pc-el__ops" @click.stop @dblclick.stop>
            <button type="button" class="pc-el__op" :disabled="!canMove(figIndex, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', figIndex, -1)">↑</button>
            <button type="button" class="pc-el__op" :disabled="!canMove(figIndex, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', figIndex, 1)">↓</button>
            <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', figIndex)">✕</button>
          </span>
        </div>
        <div
          v-for="f in formulas"
          :key="`f${f.i}`"
          class="pc-el__capsule"
          :title="editable ? '双击编辑公式' : undefined"
          @dblclick="startLatexEdit(`f${f.i}`, f.el.latex)"
        >
          <div v-if="editKey === `f${f.i}` && editable" class="pc-el__formula-edit">
            <div class="pc-el__symbols" role="toolbar" aria-label="常用数学符号">
              <button
                v-for="sym in LATEX_SYMBOLS"
                :key="sym.insert"
                type="button"
                class="pc-el__symbol"
                :title="sym.title"
                @click.stop="insertSymbol(sym)"
              >{{ sym.label }}</button>
            </div>
            <textarea
              v-model="editLatex"
              class="pc-el__edit-area"
              rows="2"
              aria-label="公式 LaTeX"
              @keydown.enter.prevent="commitLatex(f.i, f.el)"
              @keydown.esc="cancelEdit"
              @blur="commitLatex(f.i, f.el)"
            />
            <div class="pc-el__formula-preview"><KatexText :text="tex(editLatex)" block /></div>
            <span v-if="latexHasError" class="pc-el__formula-error" role="alert">公式语法有误，请检查</span>
            <span v-if="editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
          </div>
          <KatexText v-else :text="tex(f.el.latex)" block />
          <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
            <button type="button" class="pc-el__op" :disabled="!canMove(f.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', f.i, -1)">↑</button>
            <button type="button" class="pc-el__op" :disabled="!canMove(f.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', f.i, 1)">↓</button>
            <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', f.i)">✕</button>
          </span>
        </div>
      </div>

      <aside v-if="cardEl" class="pc-el__card">
        <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
          <button type="button" class="pc-el__op" :disabled="!canMove(cardIdx, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', cardIdx, -1)">↑</button>
          <button type="button" class="pc-el__op" :disabled="!canMove(cardIdx, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', cardIdx, 1)">↓</button>
          <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', cardIdx)">✕</button>
        </span>
        <div class="pc-el__card-head">
          <span class="pc-el__card-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="16" height="16">
              <path d="M2 3.4A1.4 1.4 0 0 1 3.4 2H8v12H3.4A1.4 1.4 0 0 1 2 12.6V3.4Z" fill="none" stroke="currentColor" stroke-width="1.4" />
              <path d="M14 3.4A1.4 1.4 0 0 0 12.6 2H8v12h4.6A1.4 1.4 0 0 0 14 12.6V3.4Z" fill="none" stroke="currentColor" stroke-width="1.4" />
            </svg>
          </span>
          <h3 class="pc-el__card-title" :title="editable ? '双击编辑标题' : undefined" @dblclick="startEdit('ct', cardEl.title || '')">
            <input
              v-if="editKey === 'ct' && editable"
              v-model="editText"
              class="pc-el__edit"
              @keydown.enter="commitTitle(cardIdx, cardEl)"
              @keydown.esc="cancelEdit"
              @blur="commitTitle(cardIdx, cardEl)"
            />
            <template v-else>{{ cardEl.title || '知识要点' }}</template>
          </h3>
        </div>
        <span v-if="editKey === 'ct' && editable && editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
        <div
          v-if="cardEl.latex || editKey === 'fc'"
          class="pc-el__card-eq"
          :title="editable ? '双击编辑公式' : undefined"
          @dblclick="startLatexEdit('fc', cardEl.latex)"
        >
          <div v-if="editKey === 'fc' && editable" class="pc-el__formula-edit">
            <div class="pc-el__symbols" role="toolbar" aria-label="常用数学符号">
              <button
                v-for="sym in LATEX_SYMBOLS"
                :key="sym.insert"
                type="button"
                class="pc-el__symbol"
                :title="sym.title"
                @click.stop="insertSymbol(sym)"
              >{{ sym.label }}</button>
            </div>
            <textarea
              v-model="editLatex"
              class="pc-el__edit-area"
              rows="2"
              aria-label="公式 LaTeX"
              @keydown.enter.prevent="commitLatex(cardIdx, cardEl)"
              @keydown.esc="cancelEdit"
              @blur="commitLatex(cardIdx, cardEl)"
            />
            <div class="pc-el__formula-preview"><KatexText :text="tex(editLatex)" block /></div>
            <span v-if="latexHasError" class="pc-el__formula-error" role="alert">公式语法有误，请检查</span>
            <span v-if="editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
          </div>
          <KatexText v-else :text="tex(cardEl.latex)" block />
        </div>
        <p
          v-if="cardEl.text"
          class="pc-el__card-note"
          :title="editable ? '双击编辑' : undefined"
          @dblclick="startEdit(`t${cardIdx}`, cardEl.text || '')"
        >
          <input
            v-if="editKey === `t${cardIdx}` && editable"
            v-model="editText"
            class="pc-el__edit"
            @keydown.enter="commitText(cardIdx, cardEl)"
            @keydown.esc="cancelEdit"
            @blur="commitText(cardIdx, cardEl)"
          />
          <KatexText v-else :text="cardEl.text" />
          <span v-if="editKey === `t${cardIdx}` && editable && editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
        </p>
        <ul v-if="cardItems.length" class="pc-el__list">
          <li v-for="row in cardItems" :key="`ci${row.k}`">
            <span class="pc-el__dot" aria-hidden="true"></span>
            <span class="pc-el__li" @dblclick="startEdit(`i${cardIdx}-${row.k}`, row.it)">
              <input
                v-if="editKey === `i${cardIdx}-${row.k}` && editable"
                v-model="editText"
                class="pc-el__edit"
                @keydown.enter="commitItem(cardIdx, cardEl, row.k)"
                @keydown.esc="cancelEdit"
                @blur="commitItem(cardIdx, cardEl, row.k)"
              />
              <KatexText v-else :text="row.it" />
            </span>
          </li>
          <li v-if="editKey.startsWith(`i${cardIdx}-`) && editable && editHint" class="pc-el__hint-line"><span class="pc-el__edit-hint" role="alert">{{ editHint }}</span></li>
        </ul>      </aside>
    </div>

    <!-- 无图形/知识卡时的独立公式胶囊 -->
    <template v-else>
      <div
        v-for="f in formulas"
        :key="`fw${f.i}`"
        class="pc-el__capsule pc-el__capsule--wide"
        :title="editable ? '双击编辑公式' : undefined"
        @dblclick="startLatexEdit(`f${f.i}`, f.el.latex)"
      >
        <div v-if="editKey === `f${f.i}` && editable" class="pc-el__formula-edit">
          <div class="pc-el__symbols" role="toolbar" aria-label="常用数学符号">
            <button
              v-for="sym in LATEX_SYMBOLS"
              :key="sym.insert"
              type="button"
              class="pc-el__symbol"
              :title="sym.title"
              @click.stop="insertSymbol(sym)"
            >{{ sym.label }}</button>
          </div>
          <textarea
            v-model="editLatex"
            class="pc-el__edit-area"
            rows="2"
            aria-label="公式 LaTeX"
            @keydown.enter.prevent="commitLatex(f.i, f.el)"
            @keydown.esc="cancelEdit"
            @blur="commitLatex(f.i, f.el)"
          />
          <div class="pc-el__formula-preview"><KatexText :text="tex(editLatex)" block /></div>
          <span v-if="latexHasError" class="pc-el__formula-error" role="alert">公式语法有误，请检查</span>
          <span v-if="editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
        </div>
        <KatexText v-else :text="tex(f.el.latex)" block />
        <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
          <button type="button" class="pc-el__op" :disabled="!canMove(f.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', f.i, -1)">↑</button>
          <button type="button" class="pc-el__op" :disabled="!canMove(f.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', f.i, 1)">↓</button>
          <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', f.i)">✕</button>
        </span>
      </div>
    </template>

    <!-- 要点列表（每个 bullets 元素一个块，块 hover 出现操作小按钮） -->
    <div v-for="b in bullets" :key="`b${b.i}`" class="pc-el__block">
      <ul class="pc-el__bullets">
        <li v-for="row in visibleIndexed(b.el.items)" :key="`bi${b.i}-${row.k}`">
          <span class="pc-el__dot" aria-hidden="true"></span>
          <span class="pc-el__li" @dblclick="startEdit(`i${b.i}-${row.k}`, row.it)">
            <input
              v-if="editKey === `i${b.i}-${row.k}` && editable"
              v-model="editText"
              class="pc-el__edit"
              @keydown.enter="commitItem(b.i, b.el, row.k)"
              @keydown.esc="cancelEdit"
              @blur="commitItem(b.i, b.el, row.k)"
            />
            <KatexText v-else :text="row.it" />
          </span>
        </li>
        <li v-if="editKey.startsWith(`i${b.i}-`) && editable && editHint" class="pc-el__hint-line"><span class="pc-el__edit-hint" role="alert">{{ editHint }}</span></li>
      </ul>
      <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
        <button type="button" class="pc-el__op" :disabled="!canMove(b.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', b.i, -1)">↑</button>
        <button type="button" class="pc-el__op" :disabled="!canMove(b.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', b.i, 1)">↓</button>
        <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', b.i)">✕</button>
      </span>
    </div>

    <!-- 推导步骤（每个 steps 元素一个块） -->
    <div v-for="s in steps" :key="`s${s.i}`" class="pc-el__block">
      <ol class="pc-el__steps">
        <li
          v-for="row in visibleIndexed(s.el.items)" :key="`si${s.i}-${row.k}`"
          class="pc-el__step-item"
          :class="{ 'is-pending': stepPending(s.i, row.k) }"
        >
          <span class="pc-el__step-no">{{ row.k + 1 }}</span>
          <span class="pc-el__li" @dblclick="startEdit(`i${s.i}-${row.k}`, row.it)">
            <input
              v-if="editKey === `i${s.i}-${row.k}` && editable"
              v-model="editText"
              class="pc-el__edit"
              @keydown.enter="commitItem(s.i, s.el, row.k)"
              @keydown.esc="cancelEdit"
              @blur="commitItem(s.i, s.el, row.k)"
            />
            <KatexText v-else :text="row.it" />
          </span>
        </li>
        <li v-if="editKey.startsWith(`i${s.i}-`) && editable && editHint" class="pc-el__hint-line"><span class="pc-el__edit-hint" role="alert">{{ editHint }}</span></li>
      </ol>
      <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
        <button type="button" class="pc-el__op" :disabled="!canMove(s.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', s.i, -1)">↑</button>
        <button type="button" class="pc-el__op" :disabled="!canMove(s.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', s.i, 1)">↓</button>
        <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', s.i)">✕</button>
      </span>
    </div>

    <!-- 对比表 -->
    <div v-for="t in tables" :key="`t${t.i}`" class="pc-el__block">
      <table class="pc-el__table">
        <thead v-if="(t.el.items || []).length">
          <tr>
            <th v-for="(c, k) in splitRow((t.el.items || [])[0] || '')" :key="`th${k}`"><KatexText :text="c" /></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, r) in (t.el.items || []).slice(1)" :key="`tr${r}`">
            <td v-for="(c, k) in splitRow(row)" :key="`td${r}-${k}`"><KatexText :text="c" /></td>
          </tr>
        </tbody>
      </table>
      <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
        <button type="button" class="pc-el__op" :disabled="!canMove(t.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', t.i, -1)">↑</button>
        <button type="button" class="pc-el__op" :disabled="!canMove(t.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', t.i, 1)">↓</button>
        <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', t.i)">✕</button>
      </span>
    </div>

    <!-- 未知 role：诚实退化文本（可双击编辑） -->
    <div v-for="o in others" :key="`o${o.i}`" class="pc-el__block">
      <p
        class="pc-el__fallback"
        :title="editable ? '双击编辑' : undefined"
        @dblclick="startEdit(`t${o.i}`, o.el.text || '')"
      >
        <input
          v-if="editKey === `t${o.i}` && editable"
          v-model="editText"
          class="pc-el__edit"
          @keydown.enter="commitText(o.i, o.el)"
          @keydown.esc="cancelEdit"
          @blur="commitText(o.i, o.el)"
        />
        <KatexText v-else :text="o.el.text || ''" />
      </p>
      <span v-if="editKey === `t${o.i}` && editable && editHint" class="pc-el__edit-hint" role="alert">{{ editHint }}</span>
      <span v-if="editable" class="pc-el__ops" @click.stop @dblclick.stop>
        <button type="button" class="pc-el__op" :disabled="!canMove(o.i, -1)" title="上移此元素块" aria-label="上移此元素块" @click.stop="emit('elementMove', o.i, -1)">↑</button>
        <button type="button" class="pc-el__op" :disabled="!canMove(o.i, 1)" title="下移此元素块" aria-label="下移此元素块" @click.stop="emit('elementMove', o.i, 1)">↓</button>
        <button type="button" class="pc-el__op pc-el__op--danger" title="删除此元素块" aria-label="删除此元素块" @click.stop="emit('elementRemove', o.i)">✕</button>
      </span>
    </div>
  </div>
</template>

<style scoped>
.pc-el {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
/* 无图形/知识卡行时正文在画布内垂直居中分布（黄金图版式，禁止「上密下空」的重心偏移） */
.pc-el--center { justify-content: center; }
/* 定义带（黄金图：浅蓝圆角面板 14px + 行内 KaTeX） */
.pc-el__def {
  flex: none;
  padding: 16px 22px;
  border-radius: 12px;
  background: var(--pc-def-bg);
  font-size: 16px;
  line-height: 1.8;
  color: #1e3a8a;
}.pc-el__def :deep(.katex) { font-size: 1.02em; }

/* 图形 + 知识卡 */
.pc-el__row {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 20px;
  align-items: stretch;
}
.pc-el__main {
  flex: 1.3;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 6px;
}
/* figure 元素块容器（承载 hover 操作条；图形撑满块内） */
.pc-el__figwrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
}
.pc-el__figwrap :deep(.pc-mathfig) { flex: 1; min-height: 0; }
/* 资产图（MATH_RENDER）：等比缩放并居中撑满图形区，不裁切、不拉伸变形 */
.pc-el__fig-img {
  flex: 1;
  min-height: 0;
  width: 100%;
  object-fit: contain;
  border-radius: 8px;
}
.pc-el__fig-empty { font-size: 12px; color: var(--pc-text-faint); text-align: center; }

/* 公式胶囊（VISUAL-SPEC-V2 §4.4「白底描边圆角 + KaTeX 显示式」）。
 * 2026-09-23 修复：原为 radius 999 胶囊 + 7px 纵内边距 → 显示式分式（\frac 上下叠放）
 * 比胶囊高度还高，被圆角裁掉分子/分母，公式看起来"散架"（实测椭圆标准方程）。
 * 改为 8px 圆角 + 10px 纵内边距，容纳显示式高度；数学排版不再被裁切。 */
.pc-el__capsule {
  align-self: center;
  position: relative;
  padding: 10px 22px;
  border: 1px solid var(--pc-line);
  border-radius: 10px;
  background: var(--pc-def-bg); /* 黄金图实证：浅蓝底公式胶囊 */
  color: var(--pc-navy);
}.pc-el__capsule--wide { margin: 4px auto; }
.pc-el__capsule :deep(.katex) { font-size: 1.02em; }
.pc-el__capsule :deep(.katex-display) { margin: 0; }
.pc-el__capsule :deep(.katex-display > .katex) { white-space: nowrap; }

/* 知识卡 */
.pc-el__card {
  flex: 1;
  min-width: 0;
  position: relative;
  border: 1px solid var(--pc-line);
  border-radius: 12px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fff;
  overflow: hidden;
}
.pc-el__card-head { display: flex; align-items: center; gap: 8px; color: var(--pc-primary); }
.pc-el__card-title { margin: 0; font-size: 16px; font-weight: 700; color: var(--pc-navy); }
.pc-el__card-eq {
  padding: 10px 8px;
  border-radius: 8px;
  background: var(--pc-def-bg);
  text-align: center;
  color: var(--pc-navy);
}
.pc-el__card-eq :deep(.katex) { font-size: 1.15em; }
.pc-el__card-eq :deep(.katex-display) { margin: 0; }
.pc-el__card-note { margin: 0; font-size: 12px; color: var(--pc-text-2); line-height: 1.7; }

/* 列表 */
.pc-el__list,
.pc-el__bullets {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pc-el__list li,
.pc-el__bullets li { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.6; color: var(--pc-text-2); }
.pc-el__dot {
  flex: none;
  width: 6px;
  height: 6px;
  margin-top: 8px;
  border-radius: 50%;
  background: var(--pc-primary);
}
.pc-el__li { min-width: 0; }

/* 步骤 */
.pc-el__steps { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.pc-el__steps li { display: flex; gap: 8px; align-items: baseline; font-size: 13px; color: var(--pc-text-2); }
/* 分步演示（预览层）：未揭示的步骤淡出并轻微下移，揭示时逐条上浮。 */
.pc-el__step-item {
  transition: opacity .28s ease, transform .28s ease, filter .28s ease;
}
.pc-el__step-item.is-pending {
  opacity: 0;
  transform: translateY(7px);
  filter: blur(1.5px);
  pointer-events: none;
}
.pc-el__step-no {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* 元素块容器（bullets/steps/table/fallback 逐元素成块：hover 操作条的定位锚点） */
.pc-el__block { position: relative; }
.pc-el__block + .pc-el__block { margin-top: 6px; }
.pc-el__hint-line { list-style: none; }

/* 表格 */
.pc-el__table { width: 100%; border-collapse: collapse; font-size: 12px; }
.pc-el__table th,
.pc-el__table td { border: 1px solid var(--pc-line); padding: 6px 10px; text-align: left; color: var(--pc-text-2); }
.pc-el__table th { background: var(--pc-primary-soft); font-weight: 700; }

.pc-el__fallback { margin: 0; font-size: 13px; color: var(--pc-text-2); }

/* ── 元素操作小按钮（hover 显现；删除/同 role 邻居上移下移）──────────────── */
.pc-el__ops {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 3;
  display: inline-flex;
  gap: 3px;
  opacity: 0;
  transition: opacity 0.12s ease;
  pointer-events: none;
}
.pc-el__def > .pc-el__ops,
.pc-el__block > .pc-el__ops,
.pc-el__capsule > .pc-el__ops { top: 4px; right: 4px; }
.pc-el__card > .pc-el__ops { top: 8px; right: 8px; }
*:hover > .pc-el__ops,
.pc-el__ops:focus-within {
  opacity: 1;
  pointer-events: auto;
}
.pc-el__op {
  width: 20px;
  height: 20px;
  border: 1px solid var(--pc-line);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.92);
  color: var(--pc-navy);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  font-family: inherit;
}
.pc-el__op:hover:not(:disabled) { border-color: var(--pc-primary); color: var(--pc-primary); }
.pc-el__op:disabled { opacity: 0.4; cursor: not-allowed; }
.pc-el__op--danger:hover:not(:disabled) { border-color: var(--pc-danger); color: var(--pc-danger); }

/* ── 编辑可供性（editable 页面）：可编辑元素 hover 虚线描边 + 文本光标 ────── */
.pc-el--editable .pc-el__def:hover,
.pc-el--editable .pc-el__capsule:hover,
.pc-el--editable .pc-el__li:hover,
.pc-el--editable .pc-el__fallback:hover,
.pc-el--editable .pc-el__card-title:hover,
.pc-el--editable .pc-el__card-note:hover,
.pc-el--editable .pc-el__card-eq:hover {
  outline: 1.5px dashed var(--pc-primary);
  outline-offset: 2px;
  border-radius: 4px;
  cursor: text;
}

/* ── 提交前预校验：行内红字提示（超限不提交，就地精简）────────────────────── */
.pc-el__edit-hint {
  display: block;
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--pc-danger);
}

/* 公式编辑（LaTeX 输入 + 符号快捷插入 + 实时 KaTeX 预览；禁裸源码上屏） */
.pc-el__formula-edit { display: flex; flex-direction: column; gap: 6px; text-align: left; }
.pc-el__symbols {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.pc-el__symbol {
  min-width: 24px;
  height: 22px;
  padding: 0 4px;
  border: 1px solid var(--pc-line);
  border-radius: 5px;
  background: #fff;
  color: var(--pc-navy);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  font-family: inherit;
}
.pc-el__symbol:hover { border-color: var(--pc-primary); color: var(--pc-primary); background: var(--pc-primary-soft); }
.pc-el__edit-area {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid var(--pc-primary);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12.5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: #fff;
  color: var(--pc-navy);
  resize: none;
}
.pc-el__formula-preview {
  padding: 4px 8px;
  border-radius: 6px;
  background: #fff;
  border: 1px dashed var(--pc-line);
  min-height: 22px;
}
/* KaTeX 语法错误中文提示行（throwOnError:false 红字保留，此处补中文说明） */
.pc-el__formula-error {
  font-size: 11.5px;
  color: var(--pc-danger);
}
</style>
