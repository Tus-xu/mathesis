<script setup lang="ts">
/**
 * 单页真实渲染器（980×551 / 16:9）：deck spec → 版面要素。
 *
 * 两条渲染路径：
 * 1. **elements 路径**（FROZEN-CONTRACT §A.4）：`slide.elements` 非空 → 由 `role` 派生版式
 *    （定义带/图形/公式胶囊/知识卡/要点列表/步骤/表格），数学图形读 `slide.math_object`（参数化）；
 * 2. **content 路径**（向后兼容）：旧 spec 仅有 `content` 纯文本 → 退化排版，不报错。
 *
 * 页眉（蓝短竖条 + 课题名 + 右上教材 + 底部发丝线）/ 标题行（序号徽标 + 页标题）/ 页脚三态一致。
 *
 * `mini` 模式（黄金图左缩略栏）：隐藏页眉/页脚 —— 缩略图是"干净的微缩"，不得出现页眉小字堆叠。
 */
import { computed, ref } from 'vue'
import { cleanDeckText, isTeachingAnnotation, sanitizeDeckText, stripChapterNo } from '@/features/teacher-prep-center/stores/deckEditor'
import { stripDeckTitleTail } from '@/features/teacher-prep-center/copy/prepCopy'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import MathFigure from './MathFigure.vue'
import SlideElements from './SlideElements.vue'
import type { DeckSlide, MathObject, SlideElement } from '@/features/teacher-prep-center/api/deckApi'

const props = withDefaults(
  defineProps<{
    slide: DeckSlide
    deckTitle?: string
    textbookLine?: string
    index?: number
    /** 非封面页序（1 基）；缺省回退 index+1（旧调用方兼容）。 */
    ordinal?: number
    editable?: boolean
    mathSelected?: boolean
    /** 图形属性编辑草稿（即时联动；缺省回退 spec 权威值）。 */
    mathOverride?: MathObject | null
    /** 缩略微缩态：隐藏页眉/页脚，仅保留真实版面主体。 */
    mini?: boolean
    /**
     * 显式 deckId（学生端老师课堂等非编辑器宿主使用）。
     * 缺省回退 deckEditor store —— 教师端 8 处旧调用方零改动。
     */
    deckId?: string
    /**
     * 预解析好的资产 URL（assetId → data URL / presign URL）。
     * 传入时 SlideElements 直接使用、不再走教师端 presign 取图；
     * 缺省（undefined）保持教师端旧行为。
     */
    assetUrls?: Record<string, string>
    /** 分步演示：已揭示的步骤数（null/undefined=整页静态）。 */
    reveal?: number | null
  }>(),
  {
    deckTitle: '',
    textbookLine: '',
    index: 0,
    ordinal: 0,
    editable: false,
    mathSelected: false,
    mathOverride: null,
    mini: false,
    deckId: '',
    assetUrls: undefined,
    reveal: null,
  },
)

const emit = defineEmits<{
  (e: 'selectMath', selected: boolean): void
  (e: 'contentChange', content: string): void
  (e: 'elementChange', payload: { index: number; next: SlideElement }): void
  (e: 'elementRemove', index: number): void
  (e: 'elementMove', index: number, dir: -1 | 1): void
  (e: 'titleChange', title: string): void
}>()

const CN_NUMS = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十']
const ord = computed(() => (props.ordinal > 0 ? props.ordinal : props.index + 1))
const cnIndex = computed(() => CN_NUMS[ord.value - 1] ?? String(ord.value))

/** 页眉/封面课题名（渲染层唯一口径）：剥离行首章节号（复用 stores/deckEditor.stripChapterNo）
 *  与「· 教学演示」这类无信息量尾巴；只改渲染层文案，不动后端 spec。 */
function courseTitleOf(raw: string | null | undefined): string {
  return stripDeckTitleTail(stripChapterNo(raw))
}
const headerTitle = computed(() => courseTitleOf(props.deckTitle))
const coverTitle = computed(() => courseTitleOf(props.slide.title) || headerTitle.value)

const isCover = computed(() => props.slide.type === 'title' || props.slide.layout_hint === 'title')
const showHeader = computed(() => !isCover.value && !props.mini)
const showFooter = computed(() => !isCover.value && !props.mini)
/** 页标题（净化后）；空标题不渲染空标题行（内容密度红线：标题不得为空）。
 *  缩略图微缩**保留**标题行：微缩需呈现「标题 + 图形 + 文本块」轮廓（VISUAL-SPEC-V3 §3-S4.2）。 */
const slideTitle = computed(() => sanitizeDeckText(props.slide.title))
const elements = computed<SlideElement[]>(() => (Array.isArray(props.slide.elements) ? props.slide.elements : []))
const useElements = computed(() => elements.value.length > 0)
/** 稀疏页（无图/少元素）：正文组垂直居中，避免画布下半页大片空白。 */
const isSparse = computed(
  () =>
    !isCover.value &&
    !props.mini &&
    elements.value.length > 0 &&
    elements.value.length <= 4 &&
    !mathObject.value &&
    !(props.slide.assets || []).length &&
    !hasExternalAssets.value &&
    !elements.value.some((el) => el.role === 'figure'),
)

const mathObject = computed<MathObject | null>(
  () => (props.mathOverride ?? props.slide.math_object ?? null) as MathObject | null,
)

/** 本页图片资产 id（MATH_RENDER 通道：直线/二次函数/幂函数图象，服务端已渲染好的 PNG）。 */
const slideAssets = computed<string[]>(() =>
  Array.isArray(props.slide.assets) ? props.slide.assets.filter(Boolean) : [],
)
/** 学生端资产走 assetUrls（deck_slide 不重复携带 assets）：有图即不按稀疏页居中。 */
const hasExternalAssets = computed(() => Object.keys(props.assetUrls || {}).length > 0)

/** 页脚金句（黄金图固定设计文案，非数据；note 元素另在正文区下沿呈现，不占用页脚）。 */
const FOOTER_SLOGAN = '用数学的眼光观察世界'

/** `note` 元素（服务端增稠产出的「本页小贴士」）→ 正文区下沿的浅色注脚。 */
const noteText = computed(() => cleanDeckText(elements.value.find((e) => e.role === 'note' && e.text)?.text))
/* ── content 路径（向后兼容）────────────────────────────────────────── */
const lines = computed(() =>
  (props.slide.content || '')
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
    // 教研批注（`引导学生…`）不是课件正文 → 渲染层确定性剔除（VISUAL-SPEC-V3 §3-S4.5）
    .filter((l) => !isTeachingAnnotation(l)),
)
const legacyFigure = computed(() => !!mathObject.value && !isCover.value)
const defLine = computed(() => (legacyFigure.value ? (lines.value[0] ?? '') : ''))
const cardBullets = computed(() => (legacyFigure.value ? lines.value.slice(1, 4) : []))
const extraLines = computed(() => (legacyFigure.value ? lines.value.slice(4) : lines.value))

/* ── 在线编辑（content 路径）──────────────────────────────────────────
 * 编辑输入多在 v-for 内（模板 ref 会被收集为数组）→ 统一根节点 querySelector 聚焦。 */
const slideRoot = ref<HTMLElement | null>(null)
const editingIdx = ref<number | null>(null)
const editingText = ref('')

function focusEditInput(selector: string) {
  requestAnimationFrame(() => {
    slideRoot.value?.querySelector<HTMLInputElement>(selector)?.focus()
  })
}

/* 页标题在线编辑（双击 → Enter/失焦提交 → set_slide.title 落库）。 */
const editingTitle = ref(false)
const titleDraft = ref('')

function startTitleEdit() {
  if (!props.editable || props.mini) return
  editingTitle.value = true
  titleDraft.value = slideTitle.value
  focusEditInput('.pc-slide__title-edit')
}
function commitTitle() {
  if (!editingTitle.value) return
  editingTitle.value = false
  const next = titleDraft.value.trim()
  if (next && next !== slideTitle.value) emit('titleChange', next)
}
function cancelTitle() {
  editingTitle.value = false
}

function startEdit(i: number, text: string) {
  if (!props.editable || props.mini) return
  editingIdx.value = i
  editingText.value = text
  focusEditInput('input:not(.pc-slide__title-edit)')
}
function commitEdit() {
  if (editingIdx.value === null) return
  const idx = editingIdx.value
  const next = [...lines.value]
  const trimmed = editingText.value.trim()
  if (trimmed) next[idx] = trimmed
  else next.splice(idx, 1)
  editingIdx.value = null
  const content = next.join('\n')
  if (content !== (props.slide.content || '')) emit('contentChange', content)
}
</script>

<template>
  <section
    ref="slideRoot"
    class="pc-slide"
    :class="{
      'pc-slide--cover': isCover,
      'pc-slide--mini': mini,
      'pc-slide--editable': editable && !mini,
      'pc-slide--sparse': isSparse,
    }"
  >
    <header v-if="showHeader" class="pc-slide__header">
      <span class="pc-slide__header-title">{{ headerTitle }}</span>
      <span v-if="textbookLine" class="pc-slide__header-book">{{ textbookLine }}</span>
    </header>

    <!-- 封面版式 -->
    <template v-if="isCover">
      <div class="pc-slide__cover">
        <span class="pc-slide__cover-kicker">高中数学 · 教学课件</span>
        <h1 class="pc-slide__cover-title">{{ coverTitle }}</h1>
        <span v-if="textbookLine" class="pc-slide__cover-book">{{ textbookLine }}</span>
      </div>
    </template>

    <!-- 标准页版式 -->
    <template v-else>
      <div v-if="slideTitle || editingTitle" class="pc-slide__title-row">
        <span class="pc-slide__badge">{{ cnIndex }}</span>
        <h2
          class="pc-slide__title"
          :title="editable && !mini ? '双击编辑页标题' : undefined"
          @dblclick="startTitleEdit"
        >
          <input
            v-if="editingTitle && editable && !mini"
            v-model="titleDraft"
            class="pc-slide__title-edit"
            aria-label="页标题"
            @keydown.enter="commitTitle"
            @keydown.esc="cancelTitle"
            @blur="commitTitle"
          />
          <template v-else>{{ slideTitle }}</template>
        </h2>
      </div>
      <!-- 路径 1：语义元素 -->
      <SlideElements
        v-if="useElements"
        :elements="elements"
        :math-object="mathObject"
        :math-selected="mathSelected"
        :assets="slideAssets"
        :deck-id="deckId"
        :asset-urls="assetUrls"
        :editable="editable && !mini"
        :reveal="reveal"
        @select-math="emit('selectMath', true)"
        @element-change="(p) => emit('elementChange', p)"
        @element-remove="(i) => emit('elementRemove', i)"
        @element-move="(i, d) => emit('elementMove', i, d)"
      />

      <!-- 路径 2：content 退化排版 -->
      <template v-else>
        <div v-if="defLine" class="pc-slide__def" @dblclick="startEdit(0, defLine)">
          <input
            v-if="editingIdx === 0 && editable && !mini"
            v-model="editingText"
            @keydown.enter="commitEdit"
            @blur="commitEdit"
          />
          <KatexText v-else :text="defLine" />
        </div>

        <div v-if="legacyFigure" class="pc-slide__figure-row">
          <div class="pc-slide__figure">
            <MathFigure :math-object="mathObject!" :selected="mathSelected" @select="emit('selectMath', true)" />
          </div>
          <aside class="pc-slide__card">
            <div class="pc-slide__card-head">
              <span class="pc-slide__card-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" width="15" height="15">
                  <path d="M2 3.4A1.4 1.4 0 0 1 3.4 2H8v12H3.4A1.4 1.4 0 0 1 2 12.6V3.4Z" fill="none" stroke="currentColor" stroke-width="1.4" />
                  <path d="M14 3.4A1.4 1.4 0 0 0 12.6 2H8v12h4.6A1.4 1.4 0 0 0 14 12.6V3.4Z" fill="none" stroke="currentColor" stroke-width="1.4" />
                </svg>
              </span>
              <h3 class="pc-slide__card-title">知识要点</h3>
            </div>
            <ul class="pc-slide__list">
              <li v-for="(b, i) in cardBullets" :key="`cb-${i}`">
                <span class="pc-slide__dot" aria-hidden="true"></span>
                <span class="pc-slide__li" @dblclick="startEdit(i + 1, b)">
                  <input
                    v-if="editingIdx === i + 1 && editable && !mini"
                    v-model="editingText"
                    @keydown.enter="commitEdit"
                    @blur="commitEdit"
                  />
                  <KatexText v-else :text="b" />
                </span>
              </li>
            </ul>
          </aside>
        </div>

        <div v-if="extraLines.length" class="pc-slide__body" :class="{ 'pc-slide__body--only': !legacyFigure }">
          <ul class="pc-slide__list">
            <li v-for="(l, i) in extraLines" :key="`xl-${i}`">
              <span class="pc-slide__dot" aria-hidden="true"></span>
              <span class="pc-slide__li" @dblclick="startEdit(legacyFigure ? i + 4 : i, l)">
                <input
                  v-if="editingIdx === (legacyFigure ? i + 4 : i) && editable && !mini"
                  v-model="editingText"
                  @keydown.enter="commitEdit"
                  @blur="commitEdit"
                />
                <KatexText v-else :text="l" />
              </span>
            </li>
          </ul>
        </div>
      </template>
    </template>

    <!-- 本页小贴士（真实 note 元素；缩略态隐藏） -->
    <p v-if="noteText && !mini" class="pc-slide__note"><KatexText :text="noteText" /></p>

    <footer v-if="showFooter" class="pc-slide__footer">{{ FOOTER_SLOGAN }}</footer>
  </section>
</template>

<style scoped>
.pc-slide {
  position: relative;
  width: 980px;
  height: 551px;
  padding: 22px 34px 34px;
  background: #fff;
  border-radius: 8px;
  box-shadow: var(--pc-shadow-hover);
  display: flex;
  flex-direction: column;
  font-family: var(--pc-font);
  color: var(--pc-navy);
  overflow: hidden;
  user-select: none;
}
/* 稀疏页：正文组垂直居中，避免画布下半页大片空白（学生端老师课堂实测） */
.pc-slide--sparse :deep(.pc-el) { justify-content: center; gap: 16px; }
.pc-slide--sparse :deep(.pc-el__row) { flex: 0 0 auto; }
.pc-slide--sparse :deep(.pc-el__def) { font-size: 18px; line-height: 1.75; }
.pc-slide--sparse :deep(.pc-el__bullets li) { font-size: 14.5px; line-height: 1.75; }
.pc-slide--sparse :deep(.pc-el__steps li) { font-size: 15.5px; line-height: 1.7; }
.pc-slide--sparse :deep(.pc-el__capsule) { padding: 12px 26px; }
.pc-slide--sparse :deep(.pc-el__capsule .katex) { font-size: 1.12em; }
/* 缩略微缩态：略收内边距，页眉/页脚隐藏（标题行保留），主体更聚焦 */
.pc-slide--mini { padding: 18px 26px 22px; box-shadow: none; border-radius: 0; }
.pc-slide--mini .pc-slide__title-row { margin: 10px 0; gap: 10px; }
.pc-slide--mini .pc-slide__badge { width: 34px; height: 34px; border-radius: 8px; font-size: 17px; }
.pc-slide--mini .pc-slide__title { font-size: 26px; }
/* 页眉（黄金图：蓝短竖条 + 课题名 + 右上教材 + 底部整行发丝线） */
.pc-slide__header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--pc-line);
}
.pc-slide__header::before {
  content: '';
  width: 4px;
  height: 15px;
  border-radius: 2px;
  background: var(--pc-primary);
}
/* 课题名可让位（超长省略），教材行给足宽度不截半句（P0-R3） */
.pc-slide__header-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-primary);
}
.pc-slide__header-book {
  flex: none;
  margin-left: auto;
  padding-left: 12px;
  font-size: 11px;
  color: var(--pc-text-muted);
  white-space: nowrap;
}

/* 封面 */
.pc-slide--cover { justify-content: center; }
.pc-slide__cover { display: flex; flex-direction: column; align-items: center; gap: 18px; text-align: center; }
.pc-slide__cover-kicker { font-size: 14px; letter-spacing: 6px; color: var(--pc-primary); }
.pc-slide__cover-title { margin: 0; font-size: 44px; font-weight: 700; color: var(--pc-navy); }
.pc-slide__cover-book { font-size: 14px; color: var(--pc-text-muted); }

/* 标题行（黄金图：蓝色方形序号徽标 40px 白字 + 标题 28/700） */
.pc-slide__title-row { display: flex; align-items: center; gap: 14px; margin: 16px 0 16px; flex: none; }
.pc-slide__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--pc-primary); /* 黄金图：纯色序号徽标，无渐变 */
  color: #fff;
  font-size: 20px;
  font-weight: 700;
}
.pc-slide__title { margin: 0; font-size: 28px; font-weight: 700; color: var(--pc-navy); }
.pc-slide__title-edit {
  width: 100%;
  border: none;
  outline: 1.5px solid var(--pc-primary);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 26px;
  font-weight: 700;
  font-family: inherit;
  background: #fff;
  color: var(--pc-navy);
}

/* content 路径：定义带 */
/* 定义带（黄金图实证：浅蓝圆角 12 面板，正文 16/1.8） */
.pc-slide__def {
  flex: none;
  margin: 0 0 12px;
  padding: 16px 22px;
  border-radius: 12px;
  background: var(--pc-def-bg);
  font-size: 16px;
  line-height: 1.8;
  color: #1e3a8a;
}.pc-slide__def :deep(.katex) { font-size: 1.02em; }
.pc-slide__def input {
  width: 100%;
  border: none;
  outline: 1.5px solid var(--pc-primary);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 17px;
  background: #fff;
}

/* content 路径：图形 + 知识卡 */
.pc-slide__figure-row { flex: 1; min-height: 0; display: flex; gap: 20px; align-items: stretch; }
.pc-slide__figure { flex: 1.3; min-width: 0; min-height: 0; display: flex; flex-direction: column; justify-content: center; }
.pc-slide__figure :deep(.pc-mathfig) { flex: 1; min-height: 0; }
.pc-slide__card {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--pc-line);
  border-radius: 12px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fff;
  overflow: hidden;
}
.pc-slide__card-head { display: flex; align-items: center; gap: 8px; color: var(--pc-primary); }
.pc-slide__card-title { margin: 0; font-size: 16px; font-weight: 700; color: var(--pc-navy); }

.pc-slide__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.pc-slide__list li { display: flex; gap: 8px; font-size: 12.5px; line-height: 1.6; color: var(--pc-text-2); }
.pc-slide__dot { flex: none; width: 6px; height: 6px; margin-top: 8px; border-radius: 50%; background: var(--pc-primary); }
.pc-slide__li { min-width: 0; }
.pc-slide__li input {
  width: 100%;
  border: none;
  outline: 1.5px solid var(--pc-primary);
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 13.5px;
}

/* content 路径：正文带 */
.pc-slide__body {
  flex: none;
  margin-top: 10px;
  padding: 12px 20px;
  border-radius: 10px;
  background: var(--pc-capsule-bg);
  max-height: 150px;
  overflow: hidden;
}
/* 无图形页：正文带在画布内垂直居中分布（禁止「上密下空」的重心偏移） */
.pc-slide__body--only {
  flex: 1;
  max-height: none;
  margin-top: 0;
  padding: 16px 22px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.pc-slide__body--only li { font-size: 15px; }

/* 本页小贴士（note 元素）：正文下沿，浅色小字，最多 2 行（不抢版面） */
.pc-slide__note {
  flex: none;
  margin: 6px 0 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: #8a9bb5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 编辑可供性（editable 页面）：可编辑元素 hover 虚线描边 + 文本光标（双击进入编辑） */
.pc-slide--editable .pc-slide__title:hover,
.pc-slide--editable .pc-slide__def:hover,
.pc-slide--editable .pc-slide__li:hover {
  outline: 1.5px dashed var(--pc-primary);
  outline-offset: 3px;
  border-radius: 4px;
  cursor: text;
}

/* 页脚金句（黄金图：右下斜体灰蓝 12px） */
.pc-slide__footer {
  position: absolute;
  right: 34px;
  bottom: 12px;
  font-size: 12px;
  font-style: italic;
  color: #7c93b8;
  letter-spacing: 1px;
}
</style>
