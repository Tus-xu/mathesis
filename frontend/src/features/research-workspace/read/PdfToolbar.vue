<template>
  <div class="rw-ptb-wrap">
    <div class="rw-ptb" role="toolbar" aria-label="PDF 工具条">
      <!-- 文本选择工具（黄金图首个图标：圆角方框内「T」；原 `Aa` 控件已移除） -->
      <button
        type="button"
        class="rw-ptb__btn"
        :class="{ 'rw-ptb__btn--on': tool === 'select' }"
        aria-label="文本选择工具"
        :aria-pressed="tool === 'select'"
        @click="emit('set-tool', 'select')"
      >
        <svg class="rw-ptb__ico" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3.5" y="3.5" width="17" height="17" rx="4" />
          <path d="M8.5 8.5h7M12 8.5v8" />
        </svg>
      </button>

      <span class="rw-ptb__sep" aria-hidden="true" />

      <button
        type="button"
        class="rw-ptb__btn"
        :disabled="!hasDoc || page <= 1"
        aria-label="上一页"
        @click="emit('prev')"
      >
        <ChevronBackOutline aria-hidden="true" />
      </button>
      <span class="rw-ptb__pagebox">
        <input
          class="rw-ptb__pageinput"
          :value="pageText"
          :disabled="!hasDoc"
          inputmode="numeric"
          aria-label="页码"
          @input="onPageInput"
          @keydown.enter="commitPage"
          @blur="commitPage"
        />
      </span>
      <span class="rw-ptb__count">/ {{ pageCount || '—' }}</span>
      <button
        type="button"
        class="rw-ptb__btn"
        :disabled="!hasDoc || page >= pageCount"
        aria-label="下一页"
        @click="emit('next')"
      >
        <ChevronForwardOutline aria-hidden="true" />
      </button>

      <span class="rw-ptb__sep" aria-hidden="true" />

      <button type="button" class="rw-ptb__btn" :disabled="!hasDoc" aria-label="缩小" @click="emit('zoom-out')">
        <RemoveOutline aria-hidden="true" />
      </button>
      <span class="rw-ptb__zoom">{{ zoomPercent }}%</span>
      <button type="button" class="rw-ptb__btn" :disabled="!hasDoc" aria-label="放大" @click="emit('zoom-in')">
        <AddOutline aria-hidden="true" />
      </button>

      <span class="rw-ptb__sep" aria-hidden="true" />

      <!-- 选择工具（光标箭头，黄金图语义） -->
      <button
        type="button"
        class="rw-ptb__btn"
        :class="{ 'rw-ptb__btn--on': tool === 'select' }"
        aria-label="选择工具"
        :aria-pressed="tool === 'select'"
        @click="emit('set-tool', 'select')"
      >
        <svg class="rw-ptb__ico" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 3.2 6 18.4l3.7-3.6 2.5 5.4 2.4-1.1-2.5-5.3 5.2-.5z" />
        </svg>
      </button>
      <!-- 抓手 / 平移 -->
      <button
        type="button"
        class="rw-ptb__btn"
        :class="{ 'rw-ptb__btn--on': tool === 'hand' }"
        aria-label="抓手工具"
        :aria-pressed="tool === 'hand'"
        @click="emit('set-tool', 'hand')"
      >
        <HandLeftOutline aria-hidden="true" />
      </button>
      <!-- 适页（整页轮廓） -->
      <button type="button" class="rw-ptb__btn" aria-label="适页" @click="emit('fit-page')">
        <svg class="rw-ptb__ico" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="5" y="3" width="14" height="18" rx="2.5" />
        </svg>
      </button>
      <!-- 适宽（页宽双向箭头） -->
      <button type="button" class="rw-ptb__btn" aria-label="适宽" @click="emit('fit-width')">
        <svg class="rw-ptb__ico" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="4" y="3.5" width="16" height="17" rx="2.5" />
          <path d="M8 12h8M8 12l2.1-2.1M8 12l2.1 2.1M16 12l-2.1-2.1M16 12l-2.1 2.1" />
        </svg>
      </button>
      <!-- 复制选中文字 -->
      <button type="button" class="rw-ptb__btn" aria-label="复制选中文字" @click="emit('copy')">
        <CopyOutline aria-hidden="true" />
      </button>

      <span class="rw-ptb__sep" aria-hidden="true" />

      <button
        type="button"
        class="rw-ptb__btn rw-ptb__btn--tail"
        :class="{ 'rw-ptb__btn--on': searchOpen }"
        aria-label="全文搜索"
        :aria-pressed="searchOpen"
        @click="emit('toggle-search')"
      >
        <SearchOutline aria-hidden="true" />
      </button>
      <span class="rw-ptb__sep" aria-hidden="true" />
      <button type="button" class="rw-ptb__btn" aria-label="全屏" @click="emit('fullscreen')">
        <ExpandOutline aria-hidden="true" />
      </button>
    </div>

    <!-- 全文搜索：独立行（避免挤压工具条 / 命中数被裁切） -->
    <div v-if="searchOpen" class="rw-ptb__searchbar" role="search">
      <input
        ref="searchRef"
        class="rw-ptb__search-input"
        :value="searchQueryLocal"
        placeholder="搜索当前文献…"
        aria-label="搜索文献"
        @input="onSearchInput"
        @keydown.enter="emit('search-next')"
      />
      <span class="rw-ptb__hits" aria-live="polite">
        {{ searchTotal ? `${searchIndex + 1}/${searchTotal}` : (searchQueryLocal ? '0/0' : '') }}
      </span>
      <button
        type="button"
        class="rw-ptb__btn rw-ptb__btn--sm"
        :disabled="!searchTotal"
        aria-label="上一个匹配"
        @click="emit('search-prev')"
      >
        <ChevronBackOutline aria-hidden="true" />
      </button>
      <button
        type="button"
        class="rw-ptb__btn rw-ptb__btn--sm"
        :disabled="!searchTotal"
        aria-label="下一个匹配"
        @click="emit('search-next')"
      >
        <ChevronForwardOutline aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 中栏紧凑 PDF 工具条（VISUAL-SPEC §4.2）：h 40px、白底、底边框；图标按钮 28×28、图标本体 16px。
 *
 * 图标选型按黄金图语义对齐（`read_crop_center_top.png`）：
 *   文本选择（圆角方框「T」）｜ ‹ [页] / 总 › ｜ − 缩放% + ｜ 选择·抓手·适页·适宽·复制 ｜ 搜索·全屏
 * 已移除黄金图中不存在的 `Aa`（原 `TextOutline` 呈现为字母「Aa」）控件。
 * 全文搜索展开为**独立一行**，保证命中数（`n/total`）始终可见、不被工具条裁切。
 * 全部动作**真实生效**（翻页 / 缩放 / 适宽 / 适页 / 全文搜索 / 复制 / 全屏）。
 */
import { nextTick, ref, watch } from 'vue'
import {
  AddOutline,
  ChevronBackOutline,
  ChevronForwardOutline,
  CopyOutline,
  ExpandOutline,
  HandLeftOutline,
  RemoveOutline,
  SearchOutline,
} from '@vicons/ionicons5'

const props = withDefaults(
  defineProps<{
    page: number
    pageCount: number
    zoomPercent: number
    hasDoc?: boolean
    tool?: 'select' | 'hand'
    searchOpen?: boolean
    searchQuery?: string
    searchTotal?: number
    searchIndex?: number
  }>(),
  {
    hasDoc: false,
    tool: 'select',
    searchOpen: false,
    searchQuery: '',
    searchTotal: 0,
    searchIndex: 0,
  },
)

const emit = defineEmits<{
  (e: 'prev'): void
  (e: 'next'): void
  (e: 'goto', page: number): void
  (e: 'zoom-in'): void
  (e: 'zoom-out'): void
  (e: 'fit-width'): void
  (e: 'fit-page'): void
  (e: 'set-tool', tool: 'select' | 'hand'): void
  (e: 'copy'): void
  (e: 'toggle-search'): void
  (e: 'update:searchQuery', value: string): void
  (e: 'search-prev'): void
  (e: 'search-next'): void
  (e: 'fullscreen'): void
}>()

const searchRef = ref<HTMLInputElement | null>(null)
const pageText = ref(String(props.page))
const searchQueryLocal = ref(props.searchQuery)

watch(
  () => props.page,
  (p) => {
    pageText.value = String(p)
  },
)
watch(
  () => props.searchQuery,
  (q) => {
    searchQueryLocal.value = q
  },
)
watch(
  () => props.searchOpen,
  (open) => {
    if (open) void nextTick(() => searchRef.value?.focus())
  },
)

function onPageInput(e: Event) {
  pageText.value = (e.target as HTMLInputElement).value.replace(/[^\d]/g, '')
}

function commitPage() {
  const n = Number(pageText.value)
  if (Number.isFinite(n) && n >= 1) emit('goto', Math.min(n, props.pageCount || n))
  else pageText.value = String(props.page)
}

function onSearchInput(e: Event) {
  const v = (e.target as HTMLInputElement).value
  searchQueryLocal.value = v
  emit('update:searchQuery', v)
}
</script>

<style scoped>
.rw-ptb-wrap {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}
/*
 * PDF 工具条（黄金图 `feC_bands.py read` 实测）：
 *   白底条带 y165~218（h≈54），内容带 y184~202（图标/文字高 ≈19px，垂直居中）。
 *   分组：`[T] ｜ ‹ [页] / N › ｜ − N% + ｜ 光标·手·页·图·复制 ｜ 搜索 ｜ 全屏`
 *   组间 1px 竖分隔线（h20），图标按钮 34×34（黄金图抓手按钮软底实测 x745~778），图标本体 20px。
 *   搜索组由 `margin-left:auto` 右靠（黄金图复制图标 x905 → 搜索图标 x983 之间为弹性留白）。
 */
.rw-ptb {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 54px;
  padding: 0 14px;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  flex: 0 0 auto;
}
.rw-ptb__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  border: none;
  border-radius: var(--rw-radius-md);
  background: transparent;
  color: var(--rw-text);
  cursor: pointer;
  font-size: 20px;
}
.rw-ptb__btn:hover:not(:disabled) {
  background: var(--rw-surface-2);
  color: var(--rw-primary);
}
.rw-ptb__btn:disabled {
  opacity: .32;
  cursor: default;
}
.rw-ptb__btn--on {
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
}
/* 图标本体 20px（按钮 34×34） */
.rw-ptb__btn :deep(svg),
.rw-ptb__ico {
  width: 20px;
  height: 20px;
}
/* 内联 SVG：描边风格与 @vicons 一致 */
.rw-ptb__ico {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.rw-ptb__btn--sm {
  width: 26px;
  height: 26px;
  font-size: 15px;
}
.rw-ptb__btn--sm :deep(svg) {
  width: 15px;
  height: 15px;
}
.rw-ptb__sep {
  /* 分组竖分隔线：黄金图里清晰可见（组间 1px 竖线）。
     原用 `--rw-border` 太浅，在白底工具条上几乎看不见 → 提到 `--rw-border-strong`。 */
  width: 1px;
  height: 20px;
  margin: 0 10px;
  flex: 0 0 auto;
  background: var(--rw-border-strong);
}
.rw-ptb__pagebox {
  display: inline-flex;
  align-items: center;
}
/* 页码输入：黄金图 x423~475 / y176~209 → 52×32，圆角 10 */
.rw-ptb__pageinput {
  width: 52px;
  height: 32px;
  padding: 0 6px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-lg);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-h2);
  font-weight: var(--rw-fw-medium);
  text-align: center;
  outline: none;
}
.rw-ptb__pageinput:focus {
  border-color: var(--rw-primary);
}
.rw-ptb__count,
.rw-ptb__zoom {
  font-size: var(--rw-fs-h2);
  color: var(--rw-text);
  min-width: 40px;
  text-align: center;
  white-space: nowrap;
}
/* 搜索组右靠（黄金图弹性留白） */
.rw-ptb__btn--tail {
  margin-left: auto;
}
/* 搜索独立行：h 36、白底、底边框；输入框自适应，命中数恒可见 */
.rw-ptb__searchbar {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  background: var(--rw-surface);
  border-bottom: var(--rw-border-w) solid var(--rw-border);
  flex: 0 0 auto;
}
.rw-ptb__search-input {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 320px;
  height: 26px;
  padding: 0 8px;
  border: var(--rw-border-w) solid var(--rw-border-strong);
  border-radius: var(--rw-radius-sm);
  background: var(--rw-surface);
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-sm);
  outline: none;
}
.rw-ptb__search-input:focus {
  border-color: var(--rw-primary);
}
.rw-ptb__hits {
  min-width: 44px;
  text-align: center;
  font-size: var(--rw-fs-xs);
  color: var(--rw-text-3);
}
</style>
