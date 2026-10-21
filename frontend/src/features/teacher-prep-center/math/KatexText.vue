<script setup lang="ts">
/** 行内/块级数学文本渲染：$...$ / $$...$$ / \(..\) / \[..\] → KaTeX；纯文本转义。 */
import { computed } from 'vue'
import { renderMathText } from './katex'

const props = withDefaults(
  defineProps<{
    text: string | null | undefined
    block?: boolean
  }>(),
  { block: false },
)

const html = computed(() => renderMathText(props.text ?? ''))
</script>

<template>
  <span v-if="!block" class="pc-katex" v-html="html"></span>
  <div v-else class="pc-katex pc-katex--block" v-html="html"></div>
</template>

<style scoped>
.pc-katex {
  line-height: 1.7;
  word-break: break-word;
}
.pc-katex--block {
  display: block;
}
.pc-katex :deep(.katex-display) {
  margin: 6px 0;
}
</style>
