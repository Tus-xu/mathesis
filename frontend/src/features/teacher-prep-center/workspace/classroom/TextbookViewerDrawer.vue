<script setup lang="ts">
/**
 * 教材查看抽屉（3.6）：真实切片原文（章节名/页码/含 LaTeX 的 chunk 原文）。
 * 数据来自 GET /textbook/view（SourceLocator 解析；禁止编造页码）。
 * 渲染与资料页同管线（sliceMarkdown → SliceBlocks）：图真实渲染、公式 KaTeX、
 * markdown 源码零上屏（G22：`![](`/`\-`/`$$`/`\frac` 均由解析层消化）。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import SliceBlocks from '@/features/teacher-prep-center/math/SliceBlocks.vue'
import { cleanInlineSlice, parseSliceBlocks } from '@/features/teacher-prep-center/math/sliceMarkdown'

const store = usePrepSessionStore()

/** 切片原文 → 结构化渲染块（标题标记剥离、转义清洗、图/公式真实渲染）。 */
function toBlocks(text: string): ReturnType<typeof parseSliceBlocks> {
  return parseSliceBlocks(text || '')
}
/** 头部元信息（来源名/章节路径/章节名）同口径清洗：行首 `#` 标记、转义源码不上屏。 */
function cleanMeta(text: string | null | undefined): string {
  return cleanInlineSlice(text || '')
}
const route = useRoute()

const sessionId = computed(() => String(route.params.sessionId || store.currentSessionId || ''))
const view = computed(() => store.viewer.data)

function close() {
  store.closeTextbookViewer()
}

function retry() {
  if (store.viewer.ref) void store.openTextbookViewer(sessionId.value, store.viewer.ref)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="store.viewer.open" class="pc-viewer-mask" @click.self="close">
      <aside class="pc-viewer" role="dialog" aria-label="教材原文查看">
        <header class="pc-viewer__head">
          <div>
            <h3 class="pc-viewer__title">教材原文</h3>
            <p v-if="view" class="pc-viewer__sub">
              {{ cleanMeta(view.source_title) }}
              <template v-if="view.section.section_path"> · {{ cleanMeta(view.section.section_path) }} {{ cleanMeta(view.section.title) }}</template>
              <template v-if="view.page_range"> · P{{ view.page_range[0] }}<template v-if="view.page_range[1] !== view.page_range[0]">–{{ view.page_range[1] }}</template></template>
            </p>
          </div>
          <button type="button" class="pc-viewer__close" aria-label="关闭" @click="close">×</button>
        </header>

        <div v-if="store.viewer.loading" class="pc-viewer__state">正在读取切片原文…</div>
        <div v-else-if="store.viewer.error" class="pc-viewer__state pc-viewer__state--error">
          {{ store.viewer.error }}
          <button type="button" class="pc-viewer__retry" @click="retry">重试</button>
        </div>

        <div v-else-if="view" class="pc-viewer__body">
          <article v-for="chunk in view.chunks" :key="chunk.id" class="pc-viewer__chunk">
            <span v-if="chunk.page_range" class="pc-viewer__page">P{{ chunk.page_range[0] }}</span>
            <SliceBlocks :blocks="toBlocks(chunk.text)" />
          </article>
          <p v-if="!view.chunks.length" class="pc-viewer__state">该定位无切片内容。</p>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.pc-viewer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.24);
  z-index: 95;
}
.pc-viewer {
  position: absolute;
  top: 0;
  right: 0;
  width: 520px;
  max-width: 92vw;
  height: 100%;
  background: var(--pc-panel);
  box-shadow: var(--pc-shadow-hover);
  display: flex;
  flex-direction: column;
  font-family: var(--pc-font);
}
.pc-viewer__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--pc-line);
}
.pc-viewer__title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-viewer__sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-viewer__close {
  margin-left: auto;
  border: none;
  background: transparent;
  font-size: 20px;
  color: var(--pc-text-muted);
  cursor: pointer;
  line-height: 1;
}
.pc-viewer__state {
  padding: 24px 18px;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-viewer__state--error {
  color: var(--pc-danger);
}
.pc-viewer__retry {
  margin-left: 8px;
  border: 1px solid var(--pc-line);
  background: transparent;
  border-radius: var(--pc-radius-btn);
  padding: 2px 10px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  color: var(--pc-text-2);
}
.pc-viewer__body {
  flex: 1;
  overflow: auto;
  padding: 14px 18px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.pc-viewer__chunk {
  position: relative;
  padding: 12px 14px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  font-size: 14px;
  line-height: 1.9;
  color: var(--pc-text-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.pc-viewer__page {
  position: absolute;
  top: 8px;
  right: 10px;
  font-size: 11px;
  color: var(--pc-text-faint);
  background: var(--pc-bg);
  border-radius: 4px;
  padding: 1px 6px;
}
.pc-viewer__chunk :deep(.katex-display) {
  overflow-x: auto;
}
</style>
