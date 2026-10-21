<script setup lang="ts">
/**
 * 教案文档纸（黄金图：标题 24px/700 居中 + 元信息条 + 六章）。
 *
 * - 六章内容全部来自真实 LessonPlanSpec（§F 扩展字段缺失时诚实空态，不编造）；
 * - ① 教学目标 有序列表；② 教学重难点 重点/难点胶囊；③ 教学过程 五列表（子组件）；
 *   ④ 板书设计 三栏（子组件）；⑤ 作业布置 有序列表 + ⑥ 教学反思 可点击编辑虚线框（同一行）；
 * - 教学反思为自由文本块，编辑经父组件走块级 patch 持久化（真实落库）；
 * - 工具条格式命令作用于当前编辑块（无编辑态时自动激活反思块），无假按钮。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import PlanProcessTable from './PlanProcessTable.vue'
import PlanBoardDesign from './PlanBoardDesign.vue'
import {
  PLAN_CHAPTERS,
  splitKeyPoints,
  stripPlaceholder,
  stripSectionNo,
  type ChapterDef,
  type ChapterKey,
} from './usePlanEditor'
import type { LessonPlanBody, PlanBoardDesign as BoardDesign, PlanStage } from '@/features/teacher-prep-center/api/lessonPlanApi'

const props = defineProps<{
  spec: LessonPlanBody | null
  /** 学科标签（备课中心固定「高中数学」，由父组件提供）。 */
  subject: string
  textbookTitle: string | null
  className: string | null
  minutes: number
  /** 课时标签（如「第一课时」）：仅来自真实来源（spec.metadata.lesson_scope / intent.lesson_hours），无则 null。 */
  lessonTag: string | null
  saving: boolean
  saveError: string | null
}>()

const emit = defineEmits<{
  (e: 'save-reflection', text: string): void
  (e: 'section-visible', key: ChapterKey): void
}>()

const headChapters: readonly ChapterDef[] = PLAN_CHAPTERS.slice(0, 4)
const homeChapter = PLAN_CHAPTERS[4]
const reflectChapter = PLAN_CHAPTERS[5]

const rootEl = ref<HTMLElement | null>(null)
const editorEl = ref<HTMLElement | null>(null)

/* ── 派生内容（全部防御性剔除占位片段） ─────────────────────────────── */
const displayTitle = computed(() => {
  // 章节号剥离：`3.1.2 椭圆的简单几何性质` → `椭圆的简单几何性质`（黄金图标题 = 课题名）。
  const raw = stripSectionNo(stripPlaceholder(props.spec?.metadata?.title ?? ''))
  if (!raw) return '教案'
  // 黄金图 L1：`{课题}（第N课时）教案`；课时标签由父组件取真实来源，缺省诚实省略（不编造「第一课时」）。
  const tag = props.lessonTag && !/第[一二三四五六七八九十0-9]+课时/.test(raw) ? `（${props.lessonTag}）` : ''
  const stem = raw.replace(/教案\s*$/, '')
  return `${stem}${tag}教案`
})

/**
 * 元信息条（黄金图四项齐全：学科｜教材｜班级｜课时）。
 * 缺真实值时给**诚实占位**（未指定教材/未指定班级），既不编造具体班级名，也不整段省略。
 */
const metaItems = computed(() => {
  const out: { label: string; value: string }[] = [
    { label: '学科', value: props.subject || '高中数学' },
    { label: '教材', value: props.textbookTitle || '未指定教材' },
    { label: '班级', value: props.className || '未指定班级' },
  ]
  if (props.minutes > 0) out.push({ label: '课时', value: `${props.minutes} 分钟` })
  return out
})

const objectives = computed(() =>
  (props.spec?.objectives ?? []).map((it) => stripPlaceholder(it.text)).filter((t) => t.length > 0),
)
const keyPoints = computed(() =>
  splitKeyPoints(props.spec?.key_difficult_points ?? [])
    .map((k) => ({ ...k, text: stripPlaceholder(k.text) }))
    .filter((k) => k.text.length > 0),
)
const homework = computed(() =>
  (props.spec?.homework ?? []).map((it) => stripPlaceholder(it.text)).filter((t) => t.length > 0),
)
const stages = computed<PlanStage[]>(() =>
  (props.spec?.lesson_flow ?? []).map((s) => ({
    ...s,
    title: stripSectionNo(stripPlaceholder(s.title)),
    items: (s.items ?? [])
      .map((it) => ({ ...it, text: stripPlaceholder(it.text) }))
      .filter((it) => it.text.length > 0),
    columns: s.columns
      ? {
          teacher_activity: sanitizeItems(s.columns.teacher_activity),
          student_activity: sanitizeItems(s.columns.student_activity),
          design_intent: sanitizeItems(s.columns.design_intent),
        }
      : null,
  })),
)
const board = computed<BoardDesign | null>(() => {
  const b = props.spec?.board_design
  if (!b) return null
  return {
    text_points: sanitizeItems(b.text_points),
    math_object: b.math_object ?? null,
    formulas: sanitizeItems(b.formulas),
  }
})

function sanitizeItems(items: { text: string }[] | undefined) {
  return (items ?? []).map((it) => ({ ...it, text: stripPlaceholder(it.text) })).filter((it) => it.text.length > 0)
}

/* ── 教学反思：可点击编辑（真实块级持久化） ───────────────────────────── */
const editing = ref(false)
const reflectionText = computed(() =>
  (props.spec?.reflection ?? [])
    .map((it) => stripPlaceholder(it.text))
    .filter((t) => t.trim().length > 0)
    .join('\n'),
)

function startEdit() {
  if (editing.value) return
  editing.value = true
  void nextTick(() => {
    const el = editorEl.value
    if (!el) return
    el.textContent = reflectionText.value
    el.focus()
    // 光标置于末尾
    const range = document.createRange()
    range.selectNodeContents(el)
    range.collapse(false)
    const sel = window.getSelection()
    sel?.removeAllRanges()
    sel?.addRange(range)
  })
}

function cancelEdit() {
  editing.value = false
}

function commit() {
  const el = editorEl.value
  const text = (el?.innerText ?? '').replace(/\u00a0/g, ' ').trim()
  emit('save-reflection', text)
}

/** 由父组件在持久化结束（成功/失败）后回调：退出编辑态。 */
function finishEdit(ok: boolean) {
  if (ok) editing.value = false
}

/** 工具条命令：作用于当前编辑块；无编辑态时自动激活反思块（真实编辑，非假按钮）。 */
function applyFormat(cmd: string, value?: string) {
  if (!editing.value) startEdit()
  void nextTick(() => {
    const el = editorEl.value
    if (!el) return
    el.focus()
    try {
      document.execCommand(cmd, false, value)
    } catch {
      /* 浏览器不支持该命令：静默，不假装成功 */
    }
  })
}

/* ── 章节滚动定位 + 可见性联动 ───────────────────────────────────────── */
const sectionRefs: Partial<Record<ChapterKey, HTMLElement>> = {}
function setSectionRef(key: ChapterKey, el: unknown) {
  if (el instanceof HTMLElement) sectionRefs[key] = el
  else delete sectionRefs[key]
}

let scrollRoot: HTMLElement | null = null
let activeKey: ChapterKey | null = null

function onScroll() {
  if (!scrollRoot) return
  const top = scrollRoot.getBoundingClientRect().top
  let current: ChapterKey = PLAN_CHAPTERS[0].key
  for (const c of PLAN_CHAPTERS) {
    const el = sectionRefs[c.key]
    if (el && el.getBoundingClientRect().top - top <= 16) current = c.key
  }
  if (current !== activeKey) {
    activeKey = current
    emit('section-visible', current)
  }
}

onMounted(() => {
  scrollRoot = (rootEl.value?.closest('.pc-plan__scroll') as HTMLElement | null) ?? null
  scrollRoot?.addEventListener('scroll', onScroll, { passive: true })
  onScroll()
})
onBeforeUnmount(() => {
  scrollRoot?.removeEventListener('scroll', onScroll)
})

function scrollToSection(key: ChapterKey) {
  const el = sectionRefs[key]
  if (!el) return
  activeKey = key
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

defineExpose({ applyFormat, scrollToSection, finishEdit, isEditing: editing })
</script>

<template>
  <article v-if="spec" ref="rootEl" class="pc-doc">
    <header class="pc-doc__head">
      <h1 class="pc-doc__title">{{ displayTitle }}</h1>
      <div v-if="metaItems.length" class="pc-doc__meta">
        <span v-for="m in metaItems" :key="m.label" class="pc-doc__meta-item">
          <i class="pc-doc__meta-label">{{ m.label }}</i>{{ m.value }}
        </span>
      </div>
    </header>

    <!-- ① 教学目标 / ② 教学重难点 / ③ 教学过程 / ④ 板书设计 -->
    <section v-for="c in headChapters" :key="c.key" :ref="(el) => setSectionRef(c.key, el)" class="pc-sec" :data-section="c.key">
      <div class="pc-sec__head">
        <span class="pc-sec__badge">{{ c.no }}</span>
        <h2 class="pc-sec__title">{{ c.title }}</h2>
      </div>
      <div class="pc-sec__body" :class="{ 'pc-sec__body--panel': c.key !== 'process' }">
        <template v-if="c.key === 'objectives'">
          <ol v-if="objectives.length" class="pc-doc__ol">
            <li v-for="(t, i) in objectives" :key="i" class="pc-doc__li"><KatexText :text="t" /></li>
          </ol>
          <p v-else class="pc-doc__empty">本课教学目标还没有生成：确认教学路径后会自动补全。</p>
        </template>

        <template v-else-if="c.key === 'key_points'">
          <div v-if="keyPoints.length" class="pc-doc__kp">
            <div v-for="(k, i) in keyPoints" :key="i" class="pc-doc__kp-row">
              <span class="pc-doc__pill" :class="k.kind === 'key' ? 'is-key' : 'is-diff'">{{ k.kind === 'key' ? '重点' : '难点' }}</span>
              <span class="pc-doc__kp-text"><KatexText :text="k.text" /></span>
            </div>
          </div>
          <p v-else class="pc-doc__empty">本课教学重难点尚未生成。</p>
        </template>

        <template v-else-if="c.key === 'process'">
          <PlanProcessTable :stages="stages" />
        </template>

        <template v-else-if="c.key === 'board'">
          <PlanBoardDesign :board="board" />
        </template>
      </div>
    </section>

    <!-- ⑤ 作业布置 + ⑥ 教学反思（同一行） -->
    <div class="pc-doc__row">
      <section :ref="(el) => setSectionRef(homeChapter.key, el)" class="pc-sec pc-sec--half" :data-section="homeChapter.key">
        <div class="pc-sec__inline-head">
          <span class="pc-sec__badge">{{ homeChapter.no }}</span>
          <h2 class="pc-sec__title">{{ homeChapter.title }}</h2>
        </div>
        <div class="pc-sec__inline-body">
          <ol v-if="homework.length" class="pc-doc__ol">
            <li v-for="(t, i) in homework" :key="i" class="pc-doc__li"><KatexText :text="t" /></li>
          </ol>
          <p v-else class="pc-doc__empty">作业布置尚未生成。</p>
        </div>
      </section>

      <section :ref="(el) => setSectionRef(reflectChapter.key, el)" class="pc-sec pc-sec--half" :data-section="reflectChapter.key">
        <div class="pc-sec__inline-head">
          <span class="pc-sec__badge">{{ reflectChapter.no }}</span>
          <h2 class="pc-sec__title">{{ reflectChapter.title }}</h2>
        </div>
        <div class="pc-sec__inline-body">
          <div v-if="!editing" class="pc-reflect pc-reflect--view" role="button" tabindex="0" @click="startEdit" @keydown.enter="startEdit">
            <template v-if="reflectionText">
              <div class="pc-reflect__text"><KatexText :text="reflectionText" /></div>
            </template>
            <template v-else>
              <p class="pc-reflect__ph">点击输入本节课的教学反思…</p>
              <p class="pc-reflect__hint">可从学生掌握情况、教学效果、改进建议等方面进行总结。</p>
            </template>
          </div>
          <div v-else class="pc-reflect pc-reflect--edit">
            <div ref="editorEl" class="pc-reflect__input" contenteditable="true" role="textbox" aria-label="教学反思" @keydown.esc.prevent="cancelEdit"></div>
            <div class="pc-reflect__actions">
              <button type="button" class="pc-reflect__btn" @click="cancelEdit">取消</button>
              <button type="button" class="pc-reflect__btn pc-reflect__btn--primary" :disabled="saving" @click="commit">
                {{ saving ? '保存中…' : '保存' }}
              </button>
            </div>
            <p v-if="saveError" class="pc-reflect__err">{{ saveError }}</p>
          </div>
        </div>
      </section>
    </div>
  </article>
</template>

<style scoped>
.pc-doc {
  /* 局部样式变量（黄金图实测：元信息条底 #F0F6FD、发丝分隔 #DBE4F2） */
  --pc-meta-bg: #f0f6fd;
  --pc-meta-sep: #dbe4f2;
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 26px 140px;
  font-family: var(--pc-font);
}
.pc-doc__head {
  margin-bottom: 22px;
}
.pc-doc__title {
  margin: 0 0 14px;
  font-size: 24px;
  font-weight: 700;
  color: var(--pc-navy);
  text-align: center;
  line-height: 1.4;
}
.pc-doc__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  width: fit-content;
  max-width: 100%;
  margin: 0 auto;
  padding: 7px 10px;
  background: var(--pc-meta-bg);
  border-radius: 8px;
  font-size: 13px;
  color: var(--pc-text-2);
}
.pc-doc__meta-item {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  flex: 0 0 auto;
  padding: 0 16px;
  /* 单元内容不换行、不被 flex 压缩裁切（教材全名「苏教版 高中数学 选择性必修第一册」完整显示） */
  white-space: nowrap;
}
.pc-doc__meta-item + .pc-doc__meta-item {
  border-left: 1px solid var(--pc-meta-sep);
}
.pc-doc__meta-label {
  font-style: normal;
  color: var(--pc-text-muted);
}
.pc-doc__meta-label::after {
  content: '：';
}

/* 章节 */
.pc-sec {
  margin-bottom: 22px;
}
.pc-sec__head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 10px;
}
.pc-sec__badge {
  flex: none;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--pc-primary);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
}
.pc-sec__title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-sec__body {
  padding-left: 0;
}
/* ①②④ 章节正文：浅蓝圆角面板（黄金图实测 #F7FBFE ≈ --pc-section-bg） */
.pc-sec__body--panel {
  padding: 10px 16px 12px;
  background: var(--pc-section-bg);
  border-radius: 10px;
}

/* 有序列表（教学目标 / 作业布置） */
.pc-doc__ol {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pc-doc__li {
  font-size: 14px;
  line-height: 1.7;
  color: var(--pc-text-2);
}

/* 重难点胶囊 */
.pc-doc__kp {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-doc__kp-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.pc-doc__pill {
  flex: none;
  padding: 2px 10px;
  border-radius: var(--pc-radius-full);
  font-size: 12px;
  font-weight: 600;
}
.pc-doc__pill.is-key,
.pc-doc__pill.is-diff {
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-doc__kp-text {
  font-size: 14px;
  line-height: 1.7;
  color: var(--pc-text-2);
}

/* ⑤ + ⑥ 同行 */
.pc-doc__row {
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  gap: 20px;
}
.pc-sec--half {
  margin-bottom: 22px;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 12px;
  align-items: start;
}
.pc-sec__inline-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 1px;
}
.pc-sec__inline-body {
  min-width: 0;
}

.pc-doc__empty {
  margin: 0;
  font-size: 13px;
  color: var(--pc-text-faint);
}

/* 教学反思 */
.pc-reflect {
  border: 1px dashed #cdd8ea;
  border-radius: var(--pc-radius-btn);
  padding: 12px 14px;
}
.pc-reflect--view {
  cursor: text;
  min-height: 84px;
  background: #fbfdff;
}
.pc-reflect--view:hover {
  border-color: var(--pc-primary);
}
.pc-reflect--edit {
  border-style: solid;
  border-color: var(--pc-primary);
  background: var(--pc-panel);
}
.pc-reflect__ph {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--pc-text-faint);
}
.pc-reflect__hint {
  margin: 0;
  font-size: 12px;
  color: var(--pc-text-faint);
}
.pc-reflect__text {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pc-text-2);
}
.pc-reflect__input {
  min-height: 76px;
  outline: none;
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--pc-text-2);
  white-space: pre-wrap;
}
.pc-reflect__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
.pc-reflect__btn {
  padding: 4px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  font-family: inherit;
  font-size: 12.5px;
  color: var(--pc-text-2);
  cursor: pointer;
}
.pc-reflect__btn--primary {
  border-color: var(--pc-primary);
  background: var(--pc-primary);
  color: #fff;
}
.pc-reflect__btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.pc-reflect__err {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--pc-danger);
}
</style>
