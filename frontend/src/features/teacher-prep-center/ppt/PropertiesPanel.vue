<script setup lang="ts">
/**
 * 智能属性面板（VISUAL-SPEC-V2 §4.1 右栏 280px）。
 *
 * - 图形属性：选中参数化图形时展示 a/b 步进器 + 派生 c（只读，c²=a²−b² 微型公式提示）
 *   + 4 显隐开关 + 图形样式 —— 全部经 deck patch 通道真实落库（revision+1，刷新还原）；
 * - 参数改动即时驱动图形/焦点/公式联动（draft 同源 → 600ms 防抖 patch）；
 * - AI 快捷操作 3 项：均为**真实 LLM** 任务（补全推导步骤 / 精简 / 随堂练习）；
 *   名称必须与真实能力一致——推导动作不产出动画，因此不写“动画”。
 */
import { computed, reactive, ref, watch } from 'vue'
import type { MathObject } from '@/features/teacher-prep-center/api/deckApi'
import { useDeckEditorStore } from '@/features/teacher-prep-center/stores/deckEditor'

const store = useDeckEditorStore()

const TABS = [
  { key: 'math', label: '图形属性' },
  { key: 'design', label: '页面信息' },
] as const
const activeTab = ref<'math' | 'design'>('math')

const slide = computed(() => store.currentSlide)
const serverMath = computed<MathObject | null>(() => (slide.value?.math_object ?? null) as MathObject | null)

/* ── 本地草稿（即时重渲 + 防抖落库）─────────────────────────────────── */
const draft = reactive<{ value: MathObject | null }>({ value: null })
let debounceTimer: ReturnType<typeof setTimeout> | null = null

watch(
  draft,
  (v) => {
    store.setDraftMath(v.value ? (JSON.parse(JSON.stringify(v.value)) as MathObject) : null)
  },
  { deep: true },
)
watch(
  () => [slide.value?.id, serverMath.value],
  () => {
    draft.value = serverMath.value ? JSON.parse(JSON.stringify(serverMath.value)) : null
  },
  { immediate: true, deep: true },
)

const cDerived = computed(() => {
  const m = draft.value
  if (!m) return null
  const a = Number(m.params?.a)
  const b = Number(m.params?.b)
  if (!Number.isFinite(a) || !Number.isFinite(b) || a <= b || a <= 0) return null
  return Math.sqrt(a * a - b * b)
})

/** 参数显示统一保留 1 位小数（黄金图 `a = 5.0 / b = 3.0 / c = 4.0`）。数值本身仍来自后端 spec。 */
const fmt1 = (v: unknown): string => {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '—'
}

function schedulePatch() {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!slide.value || !draft.value) return
  const slideId = slide.value.id
  const payload = JSON.parse(JSON.stringify(draft.value)) as MathObject
  debounceTimer = setTimeout(() => {
    void store.updateMathObject(slideId, payload)
  }, 600)
}

/** @returns 是否接受本次改动（false = 违反 a>b>0 被拒绝）。 */
function stepParam(key: 'a' | 'b', delta: number): boolean {
  const m = draft.value
  if (!m) return false
  const params = { ...(m.params || {}) }
  const other = key === 'a' ? Number(params.b ?? 3) : Number(params.a ?? 5)
  const next = Math.round((Number(params[key] ?? (key === 'a' ? 5 : 3)) + delta) * 10) / 10
  // 数学红线 a > b > 0：越界即钳制（非法参数不提交，服务端同样强校验）
  if (key === 'a' && next <= other) return false
  if (key === 'b' && (next <= 0 || next >= other)) return false
  if (next <= 0) return false
  params[key] = next
  draft.value = { ...m, params }
  schedulePatch()
  return true
}

/* P2：步进器拒绝后受控回显 —— 输入框回写模型实际值（不残留键入值）+ 红边轻提示 */
const REJECT_HINT: Record<'a' | 'b', string> = {
  a: '长半轴 a 需大于短半轴 b（a > b > 0）',
  b: '短半轴 b 需小于长半轴 a 且大于 0（a > b > 0）',
}
const rejectFlash = ref<'a' | 'b' | null>(null)
let rejectTimer: ReturnType<typeof setTimeout> | null = null

function flashReject(key: 'a' | 'b') {
  rejectFlash.value = key
  if (rejectTimer) clearTimeout(rejectTimer)
  rejectTimer = setTimeout(() => {
    rejectFlash.value = null
  }, 2500)
}

/** 步进按钮受控入口：非法参数拒绝并提示（a > b > 0 红线）。 */
function stepOrFlash(key: 'a' | 'b', delta: number) {
  if (!stepParam(key, delta)) flashReject(key)
}

/** 输入框 @change 受控入口：拒绝时把输入框回写为模型实际值并给轻提示。 */
function onParamChange(key: 'a' | 'b', e: Event) {
  const input = e.target as HTMLInputElement
  const m = draft.value
  if (!m) return
  const current = Number(m.params?.[key] ?? (key === 'a' ? 5 : 3))
  const next = Number(input.value)
  const accepted = Number.isFinite(next) && stepParam(key, next - current)
  if (!accepted) {
    input.value = String(current)
    flashReject(key)
  }
}

function setToggle(key: 'show_foci' | 'show_axes' | 'show_aux_lines' | 'show_value_labels', v: boolean) {
  const m = draft.value
  if (!m) return
  draft.value = { ...m, toggles: { ...(m.toggles || {}), [key]: v } }
  schedulePatch()
}

function setStyle(patch: { color?: string; stroke_px?: number; dash?: string }) {
  const m = draft.value
  if (!m) return
  draft.value = { ...m, style: { ...(m.style || {}), ...patch } }
  schedulePatch()
}

const TOGGLE_DEFAULTS: Record<string, boolean> = {
  show_foci: true,
  show_axes: true,
  show_aux_lines: true,
  show_value_labels: false,
}
function toggleValue(key: string): boolean {
  const t = (draft.value?.toggles || {}) as Record<string, boolean>
  return t[key] ?? TOGGLE_DEFAULTS[key] ?? false
}

const TOGGLES = [
  { key: 'show_foci', label: '显示焦点 F₁、F₂' },
  { key: 'show_axes', label: '显示坐标轴' },
  { key: 'show_aux_lines', label: '显示辅助线 (PF₁、PF₂)' },
  { key: 'show_value_labels', label: '显示数值标注' },
] as const

const COLOR_SWATCHES = ['#2563EB', '#DC2626', '#16A34A', '#7C3AED', '#D97706']
const STROKE_OPTIONS = [1, 2, 3, 4]
const DASH_OPTIONS = [
  { value: 'solid', label: '—' },
  { value: 'dashed', label: '– –' },
  { value: 'dotted', label: '···' },
]
const paletteOpen = ref(false)

/* ── AI 快捷操作（全部真实 LLM；无假入口）────────────────────────────── */
function onDerive() {
  const s = slide.value
  if (!s) return
  // 推导动画只对「推导 / 例题」页有意义：在「学习目标 / 小结」页强行生成会覆盖页面职责。
  if (!['derivation', 'worked-example'].includes(String(s.type || ''))) {
    store.aiError = `「生成推导动画」只适用于推导页或例题页；当前页是「${s.title || '本页'}」，请先切换到推导页。`
    return
  }
  void store.aiDeriveAnimation(s.id)
}
function onSimplify() {
  if (slide.value) void store.aiSimplifyPage(slide.value.id)
}
function onPractice() {
  if (slide.value) void store.aiPracticeForPage(slide.value.id)
}

const QUICK_OPS = computed(() => [
  { key: 'derive', title: '生成推导动画', sub: '仅推导/例题页：生成分步推导并逐步播放', run: onDerive },
  { key: 'simplify', title: '让本页更简洁', sub: '精简文字，突出重点', run: onSimplify },
  { key: 'practice', title: '生成相似练习题', sub: '基于本页内容生成随堂练习', run: onPractice },
])
const opsDisabled = computed(() => store.aiBusy || !slide.value)

function closePanel() {
  store.selectMath(false)
}

const aiBusyText = computed(() => {
  if (!store.aiBusy) return ''
  const map: Record<string, string> = {
    derive: 'AI 正在生成分步推导，随后自动播放…',
    simplify: 'AI 正在精简本页…',
    practice: 'AI 正在生成随堂练习…',
    add_page: 'AI 正在生成新页内容…',
    modify_page: 'AI 正在修改本页…',
  }
  return map[store.aiTask ?? ''] || 'AI 处理中…'
})

const noteLines = computed(() => (slide.value?.speaker_note || '').split('\n').filter((s) => s.trim()))
</script>

<template>
  <aside class="pc-props">
    <header class="pc-props__head">
      <span class="pc-props__title">智能属性</span>
      <button type="button" class="pc-props__close" title="取消选中" @click="closePanel">
        <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
          <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
        </svg>
      </button>
    </header>

    <div class="pc-props__tabs" role="tablist">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        role="tab"
        class="pc-props__tab"
        :class="{ 'is-active': activeTab === t.key }"
        :aria-selected="activeTab === t.key"
        @click="activeTab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="store.aiBusy" class="pc-props__ai-busy">
      <span class="pc-props__spinner" aria-hidden="true"></span>
      <span>{{ aiBusyText }}</span>
    </div>
    <div v-else-if="store.aiError" class="pc-props__ai-error">
      <span>{{ store.aiError }}</span>
      <button type="button" @click="store.clearAiError()">知道了</button>
    </div>

    <template v-if="activeTab === 'math'">
      <template v-if="draft.value && slide">
        <div class="pc-props__obj">
          <span class="pc-props__obj-icon" aria-hidden="true">
            <svg viewBox="0 0 40 26" width="36" height="24">
              <ellipse cx="20" cy="13" rx="16" ry="9" fill="none" :stroke="draft.value?.style?.color || '#2563EB'" stroke-width="2" />
            </svg>
          </span>
          <div class="pc-props__obj-meta">
            <span class="pc-props__obj-name">{{ draft.value?.kind === 'ellipse' ? '椭圆图形' : draft.value?.kind }}</span>
            <span class="pc-props__obj-sub">调整参数，实时更新图形与标注</span>
          </div>
        </div>

        <div class="pc-props__rows">
          <label class="pc-props__row">
            <span class="pc-props__row-name">长半轴 <i class="pc-props__var">a</i></span>
            <span class="pc-props__stepper" :class="{ 'is-reject': rejectFlash === 'a' }" :title="rejectFlash === 'a' ? REJECT_HINT.a : undefined">
              <input
                type="text"
                inputmode="decimal"
                :value="fmt1(draft.value?.params?.a)"
                aria-label="长半轴 a"
                @change="onParamChange('a', $event)"
              />
              <span class="pc-props__step-btns">
                <button type="button" aria-label="增大 a" @click="stepOrFlash('a', 0.5)">
                  <svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 5l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
                </button>
                <button type="button" aria-label="减小 a" @click="stepOrFlash('a', -0.5)">
                  <svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
                </button>
              </span>
            </span>
          </label>
          <label class="pc-props__row">
            <span class="pc-props__row-name">短半轴 <i class="pc-props__var">b</i></span>
            <span class="pc-props__stepper" :class="{ 'is-reject': rejectFlash === 'b' }" :title="rejectFlash === 'b' ? REJECT_HINT.b : undefined">
              <input
                type="text"
                inputmode="decimal"
                :value="fmt1(draft.value?.params?.b)"
                aria-label="短半轴 b"
                @change="onParamChange('b', $event)"
              />
              <span class="pc-props__step-btns">
                <button type="button" aria-label="增大 b" @click="stepOrFlash('b', 0.5)">
                  <svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 5l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
                </button>
                <button type="button" aria-label="减小 b" @click="stepOrFlash('b', -0.5)">
                  <svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg>
                </button>
              </span>
            </span>
          </label>
          <label class="pc-props__row pc-props__row--derived" title="半焦距 c = √(a²−b²)，由 a、b 派生">
            <span class="pc-props__row-name">半焦距 <i class="pc-props__var">c</i></span>
            <span class="pc-props__stepper is-readonly">
              <input type="text" :value="cDerived !== null ? cDerived.toFixed(1) : '—'" readonly />
              <span class="pc-props__step-btns">
                <button type="button" disabled tabindex="-1"><svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 5l4-4 4 4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg></button>
                <button type="button" disabled tabindex="-1"><svg viewBox="0 0 10 6" width="10" height="6"><path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" /></svg></button>
              </span>
            </span>
          </label>
          <span class="pc-props__hint">(c² = a² − b²)</span>
          <!-- 非法参数拒绝提示（a > b > 0 红线；可见提示，不只靠 tooltip） -->
          <span v-if="rejectFlash" class="pc-props__reject-hint" role="alert">{{ REJECT_HINT[rejectFlash] }}</span>
        </div>

        <div class="pc-props__toggles">
          <label v-for="t in TOGGLES" :key="t.key" class="pc-props__toggle-row">
            <span>{{ t.label }}</span>
            <button
              type="button"
              role="switch"
              class="pc-props__switch"
              :class="{ 'is-on': toggleValue(t.key) }"
              :aria-checked="toggleValue(t.key)"
              @click="setToggle(t.key, !toggleValue(t.key))"
            ><span class="pc-props__knob"></span></button>
          </label>
        </div>

        <div class="pc-props__style">
          <span class="pc-props__section">图形样式</span>
          <div class="pc-props__style-row">
            <span class="pc-props__swatch-box">
              <button
                type="button"
                class="pc-props__swatch-btn"
                :style="{ background: draft.value?.style?.color || '#2563EB' }"
                aria-label="线条颜色"
                @click="paletteOpen = !paletteOpen"
              ></button>
              <span v-if="paletteOpen" class="pc-props__palette">
                <button
                  v-for="col in COLOR_SWATCHES"
                  :key="col"
                  type="button"
                  class="pc-props__palette-dot"
                  :class="{ 'is-active': (draft.value?.style?.color || '#2563EB') === col }"
                  :style="{ background: col }"
                  :aria-label="`颜色 ${col}`"
                  @click="setStyle({ color: col }); paletteOpen = false"
                ></button>
              </span>
            </span>
            <select
              class="pc-props__select"
              :value="draft.value?.style?.stroke_px ?? 2"
              aria-label="线宽"
              @change="setStyle({ stroke_px: Number(($event.target as HTMLSelectElement).value) })"
            >
              <option v-for="w in STROKE_OPTIONS" :key="w" :value="w">{{ w }}px</option>
            </select>
            <select
              class="pc-props__select pc-props__select--dash"
              :value="draft.value?.style?.dash ?? 'solid'"
              aria-label="线型"
              @change="setStyle({ dash: ($event.target as HTMLSelectElement).value })"
            >
              <option v-for="d in DASH_OPTIONS" :key="d.value" :value="d.value">{{ d.label }}</option>
            </select>
          </div>
          <span v-if="store.saveState === 'saving'" class="pc-props__saving">保存中…</span>
          <span v-else-if="store.saveState === 'error'" class="pc-props__save-error" :title="store.saveError || ''">保存失败，已回滚</span>
        </div>
      </template>

      <div v-else class="pc-props__empty-math">
        本页暂无可参数化数学图形。带参数化图形的页面（如「椭圆的定义」）可在此调整 a/b、显隐与样式。
      </div>
    </template>

    <template v-else>
      <div v-if="slide" class="pc-props__page-info">
        <span class="pc-props__section">页面信息</span>
        <div class="pc-props__info-grid">
          <span class="k">页码</span><span class="v">{{ store.currentIndex + 1 }} / {{ store.pageCount }}</span>
          <span class="k">页标题</span><span class="v">{{ slide.title || '—' }}</span>
          <span class="k">页类型</span><span class="v">{{ slide.type }}</span>
          <span class="k">版式</span><span class="v">{{ slide.layout_hint || '—' }}</span>
          <span class="k">教学目标</span><span class="v">{{ slide.teaching_goal || '—' }}</span>
          <span class="k">版式元素</span><span class="v">{{ (slide.elements || []).length }} 个</span>
          <span class="k">备注/练习</span><span class="v">{{ noteLines.length ? `${noteLines.length} 条` : '无' }}</span>
          <span class="k">教材溯源</span><span class="v">{{ (slide.source_refs || []).length ? `${(slide.source_refs || []).length} 条引用` : '—' }}</span>
        </div>
      </div>
      <div v-else class="pc-props__empty-math">暂无页面。</div>
    </template>

    <div class="pc-props__ai">
      <span class="pc-props__ai-heading">AI 快捷操作</span>
      <div class="pc-props__ai-list">
        <button
          v-for="op in QUICK_OPS"
          :key="op.key"
          type="button"
          class="pc-props__ai-item"
          :disabled="opsDisabled"
          :title="op.title"
          @click="op.run()"
        >
          <span class="pc-props__ai-icon" aria-hidden="true">
            <!-- 三项差异化图标（黄金图）：推导步骤 / 精简 / 练习 —— 不再复用同一 sparkle -->
            <svg v-if="op.key === 'derive'" viewBox="0 0 24 24" width="19" height="19">
              <path d="M3.5 17.5c3-8 6.8-11 12-11" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
              <circle cx="3.6" cy="17.6" r="1.7" fill="currentColor" />
              <path d="M15 4.2l3.6 2-1.5 3.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
              <path d="M6.4 4.6l.9 1.9 1.9.9-1.9.9-.9 1.9-.9-1.9L3.6 7.4l1.9-.9z" fill="currentColor" opacity="0.9" />
            </svg>
            <svg v-else-if="op.key === 'simplify'" viewBox="0 0 24 24" width="19" height="19">
              <path d="M4 9.4h3.1L11 6.3v11.4l-3.9-3.1H4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" />
              <path d="M14.6 8.6a4.9 4.9 0 0 1 0 6.8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
              <path d="M17.5 6.2a8.4 8.4 0 0 1 0 11.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
            </svg>
            <svg v-else viewBox="0 0 24 24" width="19" height="19">
              <circle cx="12" cy="12" r="7.8" fill="none" stroke="currentColor" stroke-width="1.6" />
              <circle cx="12" cy="12" r="3.5" fill="none" stroke="currentColor" stroke-width="1.6" />
              <circle cx="12" cy="12" r="1.2" fill="currentColor" />
            </svg>
          </span>
          <span class="pc-props__ai-text">
            <span class="pc-props__ai-title">{{ op.title }}</span>
            <span class="pc-props__ai-sub">{{ op.sub }}</span>
          </span>
          <span class="pc-props__ai-chevron" aria-hidden="true">
            <svg viewBox="0 0 8 12" width="8" height="12"><path d="M1 1l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" /></svg>
          </span>
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.pc-props {
  width: 280px;
  flex: none;
  background: var(--pc-panel);
  border-left: 1px solid var(--pc-line);
  padding: 18px 18px 22px;
  overflow-y: auto;
  font-family: var(--pc-font);
  color: var(--pc-navy);
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.pc-props__head { display: flex; align-items: center; justify-content: space-between; }
.pc-props__title { font-size: 16px; font-weight: 700; color: var(--pc-navy); }
.pc-props__close {
  border: none;
  background: transparent;
  color: var(--pc-navy);
  cursor: pointer;
  display: inline-flex;
  padding: 2px;
}

/* 分段 Tab（激活 = 白底 + 蓝字 + 2px 蓝下划线） */
.pc-props__tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  padding: 3px;
  border-radius: 10px;
  background: #eef2f8;
}
.pc-props__tab {
  position: relative;
  border: none;
  background: transparent;
  padding: 7px 0 8px;
  font-size: 13px;
  font-family: inherit;
  color: var(--pc-text-muted);
  border-radius: 8px;
  cursor: pointer;
}
.pc-props__tab.is-active {
  background: #fff;
  color: var(--pc-primary);
  font-weight: 600;
  box-shadow: var(--pc-shadow-card);
}
.pc-props__tab.is-active::after {
  content: '';
  position: absolute;
  left: 22%;
  right: 22%;
  bottom: 3px;
  height: 2px;
  border-radius: 2px;
  background: var(--pc-primary);
}

/* 对象头 */
.pc-props__obj { display: flex; align-items: center; gap: 12px; }
.pc-props__obj-icon {
  width: 54px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--pc-tile);
}
.pc-props__obj-meta { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.pc-props__obj-name { font-size: 14px; font-weight: 700; color: var(--pc-navy); }
.pc-props__obj-sub { font-size: 11px; color: var(--pc-text-muted); }

/* 参数行 */
.pc-props__rows { display: flex; flex-direction: column; gap: 10px; }
.pc-props__row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pc-props__row-name { font-size: 13.5px; color: var(--pc-navy); }
.pc-props__var { font-style: italic; font-family: Georgia, serif; }
.pc-props__stepper {
  display: inline-flex;
  align-items: stretch;
  width: 108px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid var(--pc-line);
  overflow: hidden;
}
.pc-props__stepper input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  text-align: left;
  padding: 0 4px 0 12px;
  font-size: 13.5px;
  color: var(--pc-navy);
  outline: none;
  font-family: inherit;
}
/* P2：拒绝轻提示（红边，短暂后自动消退；不做过渡以保证瞬时可见） */
.pc-props__stepper.is-reject { border-color: var(--pc-danger); }
.pc-props__stepper.is-readonly input { color: var(--pc-text-muted); }
.pc-props__step-btns { display: flex; flex-direction: column; justify-content: center; padding: 0 6px; gap: 1px; }
.pc-props__step-btns button {
  border: none;
  background: transparent;
  color: var(--pc-text-muted);
  cursor: pointer;
  line-height: 0;
  padding: 1px;
}
.pc-props__step-btns button:hover:not(:disabled) { color: var(--pc-primary); }
.pc-props__step-btns button:disabled { cursor: not-allowed; opacity: 0.4; }
.pc-props__hint { font-size: 11.5px; color: var(--pc-text-muted); align-self: flex-end; }
.pc-props__reject-hint {
  align-self: flex-end;
  font-size: 11.5px;
  color: var(--pc-danger);
  line-height: 1.5;
}

/* 开关 */
.pc-props__toggles { display: flex; flex-direction: column; gap: 14px; }
.pc-props__toggle-row { display: flex; align-items: center; justify-content: space-between; font-size: 13.5px; color: var(--pc-navy); }
.pc-props__switch {
  position: relative;
  width: 42px;
  height: 24px;
  border: none;
  border-radius: var(--pc-radius-full);
  background: var(--pc-track);
  cursor: pointer;
  transition: background 0.18s ease;
}
.pc-props__switch.is-on { background: var(--pc-primary); }
.pc-props__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  transition: transform 0.18s ease;
}
.pc-props__switch.is-on .pc-props__knob { transform: translateX(18px); }

/* 样式 */
.pc-props__section { font-size: 13.5px; font-weight: 700; color: var(--pc-navy); display: block; }
.pc-props__style { display: flex; flex-direction: column; gap: 10px; }
.pc-props__style-row { display: flex; align-items: stretch; gap: 10px; }
.pc-props__swatch-box {
  position: relative;
  width: 74px;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  box-sizing: border-box;
}
.pc-props__swatch-btn { width: 100%; height: 16px; border: none; border-radius: 4px; cursor: pointer; }
.pc-props__palette {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 5;
  display: flex;
  gap: 6px;
  padding: 6px;
  background: #fff;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
  box-shadow: var(--pc-shadow-hover);
}
.pc-props__palette-dot { width: 18px; height: 18px; border: 2px solid transparent; border-radius: 5px; cursor: pointer; }
.pc-props__palette-dot.is-active { border-color: var(--pc-navy); }
.pc-props__select {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--pc-line);
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  color: var(--pc-navy);
  padding: 6px 8px;
  font-family: inherit;
}
.pc-props__saving { font-size: 11.5px; color: var(--pc-warning); }
.pc-props__save-error { font-size: 11.5px; color: var(--pc-danger); }

/* AI 快捷操作（黄金图：3 卡较紧凑，图标 + 13/700 标题 + 11 灰副标题 + chevron） */
.pc-props__ai { display: flex; flex-direction: column; gap: 10px; margin-top: 4px; }
.pc-props__ai-heading { font-size: 16px; font-weight: 700; color: var(--pc-navy); }
.pc-props__ai-list { display: flex; flex-direction: column; gap: 8px; }
.pc-props__ai-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 1px solid var(--pc-line);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
}
.pc-props__ai-item:disabled { cursor: not-allowed; opacity: 0.5; }
.pc-props__ai-item:not(:disabled):hover { background: var(--pc-primary-soft); border-color: #cddcff; }
.pc-props__ai-icon {
  width: 32px;
  height: 32px;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-props__ai-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.pc-props__ai-title { font-size: 13px; font-weight: 700; color: var(--pc-navy); }
.pc-props__ai-sub { font-size: 11px; color: var(--pc-text-muted); }
.pc-props__ai-chevron { margin-left: auto; color: #9db3d6; display: inline-flex; }

/* 状态与空态 */
.pc-props__ai-busy {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 12.5px;
}
.pc-props__spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--pc-primary);
  border-top-color: transparent;
  border-radius: 50%;
  animation: pc-props-spin 0.8s linear infinite;
}
@keyframes pc-props-spin { to { transform: rotate(360deg); } }
.pc-props__ai-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fef2f2;
  color: var(--pc-danger);
  font-size: 12px;
}
.pc-props__ai-error button {
  margin-left: auto;
  border: none;
  background: transparent;
  color: var(--pc-danger);
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
}
.pc-props__empty-math { font-size: 12.5px; color: var(--pc-text-muted); line-height: 1.8; }
.pc-props__empty-why { margin: 0; }
.pc-props__empty-how { margin: 6px 0 0; padding-left: 18px; }
.pc-props__empty-how li { margin-bottom: 4px; }

/* 页面信息 */
.pc-props__page-info { display: flex; flex-direction: column; gap: 10px; }
.pc-props__info-grid { display: grid; grid-template-columns: auto 1fr; gap: 8px 12px; font-size: 12.5px; }
.pc-props__info-grid .k { color: var(--pc-text-faint); }
.pc-props__info-grid .v { color: var(--pc-text-2); word-break: break-all; }
</style>
