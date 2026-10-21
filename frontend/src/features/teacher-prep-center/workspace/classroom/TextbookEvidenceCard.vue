<script setup lang="ts">
/**
 * 展开卡右栏「教材依据」卡（黄金图 §2）。
 *
 * - 头部：书本图标 + 组名 + 右侧「查看教材 >」蓝链（打开真实切片原文抽屉）；
 * - 封面：真实封面资产缺位（切片零图片资产，禁止造假）→ **共用封面占位组件**
 *   （TextbookCover：3:4 浅蓝底 + 学科短名，如「数学」）；教材全名放右侧文字区，
 *   不塞进封面（62px 宽会被压成细碎多行、观感像渲染故障）；
 * - 书名/章节/页码全部来自真实教材查看端点（SourceLocator 解析）；无锚点 → 诚实占位。
 */
import { computed, watch } from 'vue'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'
import { cleanInlineSlice, slicePreviewText } from '@/features/teacher-prep-center/math/sliceMarkdown'
import TextbookCover, { textbookDisplayName } from './TextbookCover.vue'

const props = defineProps<{
  sessionId: string
  /** 章节级 SourceLocator（context 投影）；空 = 无教材锚点（诚实占位）。 */
  locator: string | null
}>()

const store = usePrepSessionStore()

const view = computed(() => {
  const v = store.viewer
  return v.ref === props.locator ? v.data : null
})

const loading = computed(() => store.viewer.loading && store.viewer.ref === props.locator)

/** 书名：消费全局权威展示名（裁定 3.3：`context.source.title` 去重字段，= 顶栏/PPT 页眉同一字符串）；
 * `view.source_title` 为教材查看端点的旧变体（含年级、缺学科）仅作兜底 —— 保证同一本书全屏同名。 */
const bookTitle = computed(() => store.currentCourseMeta.textbook || view.value?.source_title || null)
/** 展示口径（DESIGN §3.1）：与 PPT 页眉一致，`·` 分隔符转空格、去年份（如 2019）—— 全屏同书同形。 */
const bookTitleDisplay = computed(() => textbookDisplayName(bookTitle.value) || null)

const sectionLine = computed(() => {
  const ctx = store.current?.artifact.context
  const v = view.value
  const title = v?.section.title || ctx?.section.title
  if (!title) return null
  const path = v?.section.section_path || ctx?.section.section_path
  return path ? `${path} ${title}` : title
})

const pageLine = computed(() => {
  const range = view.value?.page_range
  if (range && Number.isFinite(range[0]) && Number.isFinite(range[1])) {
    return `P${range[0]}${range[1] !== range[0] ? `–${range[1]}` : ''}`
  }
  return null
})

/** 引文块：真实切片原文首个非标题正文行（含 LaTeX → KaTeX 渲染）。
 *  优先取该行首个完整短句（≤80 字，止于 `。`/`；`）——黄金图引文是完整短句，
 *  不是半句截断；超长段落才落到整行并由 CSS 行数钳制兜底。
 *  G22 反泄漏：候选行经 sliceMarkdown 清洗（图片/`\-`/孤立 `$$` 不上屏），
 *  图片行与公式行不当引文。 */
const quote = computed(() => {
  const isHeading = (l: string) => /^#{1,6}\s/.test(l) || /^\d+(\.\d+)*\s/.test(l)
  const chunks = view.value?.chunks || []
  for (const c of chunks) {
    const lines = (c.text || '')
      .split('\n')
      .map((l) => l.replace(/^#+\s*/, '').trim())
      .filter((l) => l.length > 12 && !isHeading(l))
      .filter((l) => !/!\[[^\]]*\]\([^)]*\)/.test(l) && !/^\$\$|^\\\[/.test(l))
    for (const raw of lines) {
      const line = cleanInlineSlice(raw)
      if (slicePreviewText(raw).length <= 12) continue
      const sentence = line.match(/^.{8,80}?[。；]/)
      return sentence ? sentence[0] : line
    }
  }
  return null
})

watch(
  () => [props.sessionId, props.locator] as const,
  ([sid, ref], prev) => {
    if (ref && (!view.value || prev?.[1] !== ref)) {
      void store.openTextbookViewer(sid, ref)
      // 依据卡只需数据，不保留抽屉打开态
      store.viewer.open = false
    }
  },
  { immediate: true },
)

function openViewer() {
  if (props.locator) void store.openTextbookViewer(props.sessionId, props.locator)
}
</script>

<template>
  <div class="pc-evidence">
    <div class="pc-evidence__head">
      <span class="pc-evidence__icon" aria-hidden="true">
        <svg viewBox="0 0 16 16">
          <path d="M8 3.2C6.8 2.3 5 2 2.6 2v10.4C5 12.4 6.8 12.7 8 13.6c1.2-.9 3-1.2 5.4-1.2V2C11 2 9.2 2.3 8 3.2Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
          <path d="M8 3.2v10.4" stroke="currentColor" stroke-width="1.1" />
        </svg>
      </span>
      <span class="pc-evidence__name">教材依据</span>
      <span class="pc-evidence__spacer"></span>
      <button type="button" class="pc-evidence__link" :disabled="!locator" @click="openViewer">
        查看教材 <span aria-hidden="true">›</span>
      </button>
    </div>

    <div v-if="locator" class="pc-evidence__body">
      <!-- 封面：无真实封面资产 → 共用占位组件（lg 变体 88×118，黄金图 G23 硬数字）；
           教材全名在右侧文字区，不塞进封面。 -->
      <TextbookCover :name="bookTitle" size="lg" />
      <!-- 右列：书名/章节/页码 + 引文块（黄金图：引文块与封面并排，整卡高度 = 封面高） -->
      <div class="pc-evidence__detail">
        <p class="pc-evidence__book">{{ bookTitleDisplay || '—' }}</p>
        <p class="pc-evidence__line">{{ sectionLine || '—' }}</p>
        <p v-if="pageLine" class="pc-evidence__line">{{ pageLine }}</p>
        <p v-if="loading" class="pc-evidence__line pc-evidence__loading">正在读取切片原文…</p>

        <blockquote v-if="quote" class="pc-evidence__quote">
          <span class="pc-evidence__quote-mark" aria-hidden="true">“</span>
          <KatexText class="pc-evidence__quote-text" :text="quote" />
        </blockquote>
      </div>
    </div>
    <p v-else class="pc-evidence__empty">教材定位后展示章节依据。</p>
  </div>
</template>

<style scoped>
/* 展开体改白底后按黄金图补发丝线边界（黄金图右栏卡片带极浅灰蓝描边） */
.pc-evidence {
  background: var(--pc-panel);
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  padding: 14px 16px;
}
.pc-evidence__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pc-evidence__icon {
  display: inline-flex;
  width: 17px;
  height: 17px;
  color: var(--pc-primary);
}
.pc-evidence__icon svg {
  width: 17px;
  height: 17px;
}
.pc-evidence__name {
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-evidence__spacer {
  flex: 1;
}
.pc-evidence__link {
  border: none;
  background: transparent;
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.pc-evidence__link:disabled {
  color: var(--pc-text-faint);
  cursor: not-allowed;
}
.pc-evidence__body {
  display: flex;
  gap: 12px;
  margin-top: 12px;
}
.pc-evidence__detail {
  min-width: 0;
  flex: 1;
}
.pc-evidence__book {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.5;
}
.pc-evidence__line {
  margin: 0 0 2px;
  font-size: 13px;
  color: var(--pc-text-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-evidence__loading {
  color: var(--pc-text-faint);
}
.pc-evidence__empty {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--pc-text-faint);
}
.pc-evidence__quote {
  position: relative;
  margin: 10px 0 0;
  padding: 8px 10px 8px 30px;
  background: var(--pc-primary-softer);
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--pc-text-2);
}
/* 引文块 ≤2 行（黄金图：浅底 + 大引号 + 2–3 行短引文）。
   切片正文此处为纯文本 + 行内 KaTeX（行内式不含块级子元素），
   故可用 -webkit-line-clamp 精确按行收尾（带省略号，不会拦腰截断半行）。 */
.pc-evidence__quote-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.pc-evidence__quote-mark {
  position: absolute;
  left: 8px;
  top: 1px;
  font-size: 24px;
  line-height: 1;
  color: var(--pc-primary);
  opacity: 0.7;
  font-family: Georgia, serif;
}
</style>
