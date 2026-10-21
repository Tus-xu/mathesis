<script setup lang="ts">
/**
 * 教材切片结构化块渲染器（R4a）：节号徽章标题 / 定义 callout / 公式块（KaTeX+编号）/
 * 插图（真实 <img>，失败降级）/ 列表 / 正文段落（行内公式 KaTeX）。
 *
 * 上屏文本必须来自 `sliceMarkdown` 的清洗产物（parseSliceBlocks / cleanInlineSlice），
 * 保证 `![](`、`\-`、`$$`、`\frac` 等源码零泄漏（G22）。
 */
import type { PropType } from 'vue'
import KatexText from './KatexText.vue'
import SliceFigure from './SliceFigure.vue'
import type { SliceBlock } from './sliceMarkdown'

defineProps({
  blocks: { type: Array as PropType<SliceBlock[]>, required: true },
  /** 锚点前缀（定位原文/相关图片跳转用）；缺省不带锚点属性。 */
  anchorPrefix: { type: String, default: '' },
})
</script>

<template>
  <template v-for="(b, i) in blocks" :key="i">
    <h4
      v-if="b.kind === 'heading' && b.level === 1"
      class="pc-slice-h1"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <span v-if="b.no" class="pc-slice-h1__no">{{ b.no }}</span>
      <KatexText :text="b.text" />
    </h4>
    <h5
      v-else-if="b.kind === 'heading'"
      class="pc-slice-h2"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <KatexText :text="b.text" />
    </h5>
    <div
      v-else-if="b.kind === 'definition'"
      class="pc-slice-def"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <span class="pc-slice-def__icon" aria-hidden="true">
        <svg viewBox="0 0 16 16">
          <path d="M3 2.6h7.2L13 5.4v8H3v-10.8Z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
          <path d="M5.4 7.2h5M5.4 9.6h3.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
        </svg>
      </span>
      <div class="pc-slice-def__body">
        <span class="pc-slice-def__label">定义</span>
        <KatexText class="pc-slice-def__text" :text="b.text" />
      </div>
    </div>
    <div
      v-else-if="b.kind === 'formula'"
      class="pc-slice-formula"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <KatexText class="pc-slice-formula__tex" :text="`$$${b.tex}$$`" block />
      <span v-if="b.tag" class="pc-slice-formula__tag">({{ b.tag }})</span>
    </div>
    <SliceFigure
      v-else-if="b.kind === 'figure'"
      :src="b.src"
      :caption="b.caption"
      :alt="b.alt"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    />
    <p
      v-else-if="b.kind === 'list'"
      class="pc-slice-li"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <span class="pc-slice-li__dot" aria-hidden="true"></span>
      <KatexText :text="b.text" />
    </p>
    <p
      v-else
      class="pc-slice-p"
      v-bind="anchorPrefix ? { 'data-block': `${anchorPrefix}-b${i}` } : {}"
    >
      <KatexText :text="b.text" />
    </p>
  </template>
</template>

<style scoped>
.pc-slice-h1 {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0 12px;
  font-size: 1.45em;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.35;
}
.pc-slice-h1__no {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.9em;
  height: 1.9em;
  padding: 0 6px;
  border-radius: 7px;
  background: var(--pc-primary);
  color: #fff;
  font-size: 0.72em;
  font-weight: 700;
}
.pc-slice-h2 {
  margin: 14px 0 8px;
  font-size: 1.08em;
  font-weight: 700;
  color: var(--pc-text);
  line-height: 1.5;
}
.pc-slice-p {
  margin: 0 0 10px;
  line-height: 1.85;
  color: var(--pc-text-2);
}
.pc-slice-li {
  display: flex;
  gap: 8px;
  margin: 0 0 8px;
  line-height: 1.8;
  color: var(--pc-text-2);
}
.pc-slice-li__dot {
  flex: none;
  width: 5px;
  height: 5px;
  margin-top: 0.72em;
  border-radius: 50%;
  background: var(--pc-primary);
}
.pc-slice-def {
  display: flex;
  gap: 10px;
  margin: 10px 0 14px;
  padding: 12px 14px;
  border-left: 3px solid var(--pc-primary);
  border-radius: 0 var(--pc-radius-btn) var(--pc-radius-btn) 0;
  background: var(--pc-primary-softer);
}
.pc-slice-def__icon {
  flex: none;
  width: 17px;
  height: 17px;
  color: var(--pc-primary);
}
.pc-slice-def__icon svg {
  width: 100%;
  height: 100%;
}
.pc-slice-def__body {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: 8px;
}
.pc-slice-def__label {
  flex: none;
  font-size: 0.95em;
  font-weight: 700;
  color: var(--pc-primary);
}
.pc-slice-def__text {
  flex: 1;
  min-width: 0;
  font-size: 0.95em;
  line-height: 1.8;
  color: var(--pc-text-2);
}
.pc-slice-formula {
  position: relative;
  margin: 12px 0 14px;
  padding: 4px 56px 4px 12px;
  text-align: center;
}
.pc-slice-formula__tex {
  display: block;
}
.pc-slice-formula__tag {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 12px;
  color: var(--pc-text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
