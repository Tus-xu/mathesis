<template>
  <li class="rw-finding" :class="{ 'rw-finding--stacked': stacked }">
    <span v-if="index" class="rw-finding__idx">{{ index }}.</span>
    <span class="rw-finding__body">
      <span class="rw-finding__head" v-html="headHtml" />
      <span v-if="detailHtml" class="rw-finding__detail" v-html="detailHtml" />
    </span>
  </li>
</template>

<script setup lang="ts">
/**
 * 关键信息条目（VISUAL-SPEC §4.4「关键信息块」/ 宪法 §58/§59）。
 *
 * 统一渲染 `ResearchResult.findings[]`：`label` + 内联数学 `math` + `detail`。
 * 数学一律经 KaTeX 渲染（`@/components/mathx/latex`），**绝不把公式当普通
 * Unicode 字符串输出**（宪法 §76）；纯文本经转义后输出，杜绝注入。
 *
 * ⚠️ `math` 必须走 `renderStem`（双格式适配）而**不是** `renderLatex`：
 * 后端 prompt 要求「公式写在 `$...$` 里」，且 `_math_hint_result` /
 * `degraded_math_result` 直接把选中文本写进 `math`，因此该字段实际常带
 * `$...$` 定界符。`renderLatex` 只认裸 LaTeX，遇到 `$` 会由 KaTeX
 * `throwOnError:false` 把整串**渲染成红色报错**（用户实测「公式乱码红色」）。
 * `renderStem` = 含 `$` 走 `renderRich`、否则走 `renderLatex`，两种形态都正确。
 * label / detail 本就是富文本，继续走 `renderRich`。
 *
 * 排版（对齐黄金图）：黄金图的条目是**一句话**（`label` + `math` 同行）。
 * 当后端给出较长的 `detail`（说明句）时，把它降到第二行，避免「标题 + 说明」
 * 挤成一句读不通的长行（黄金图无此形态，属对真实数据的最小必要适配）。
 */
import { computed } from 'vue'
import { renderRich, renderStem } from '@/components/mathx/latex'
import type { Finding } from '../api/types'

const props = withDefaults(
  defineProps<{
    /** 有序列表序号（0 / 省略则不显示） */
    index?: number
    finding: Finding
  }>(),
  { index: 0 },
)

/** detail 超过该长度即视为「说明句」，降到第二行（黄金图条目均为短句）。 */
const STACK_THRESHOLD = 40

const label = computed(() => String(props.finding?.label ?? '').trim())
const detail = computed(() => {
  const d = String(props.finding?.detail ?? '').trim()
  return d && d !== label.value ? d : ''
})
const math = computed(() => String(props.finding?.math ?? '').trim())

const stacked = computed(() => detail.value.length > STACK_THRESHOLD)

const headHtml = computed(() => {
  const parts: string[] = []
  if (label.value) parts.push(renderRich(label.value))
  if (math.value) parts.push(`<span class="rw-finding__math">${renderStem(math.value)}</span>`)
  return parts.join(' ') || renderRich('—')
})

const detailHtml = computed(() => (detail.value ? renderRich(detail.value) : ''))
</script>

<style scoped>
.rw-finding {
  display: flex;
  gap: 6px;
  font-size: var(--rw-fs-h2);
  /* 黄金图「关键信息」条目行距实测 ≈26px（行带 pitch 30 − 列表 gap 4）；
     与结果卡正文统一为 15px / 1.7（原 1.6 偏紧）。复算：tmp/r6_scan3.py。 */
  line-height: 1.7;
  color: var(--rw-text);
}
.rw-finding__idx {
  flex: 0 0 auto;
  color: var(--rw-text-2);
  font-variant-numeric: tabular-nums;
}
.rw-finding__body {
  min-width: 0;
  overflow-wrap: anywhere;
}
.rw-finding__detail {
  margin-left: 6px;
}
/* 长说明句：标题行 + 说明行（避免挤成一句读不通的长行） */
.rw-finding--stacked .rw-finding__head {
  display: block;
  font-weight: var(--rw-fw-medium);
}
.rw-finding--stacked .rw-finding__detail {
  display: block;
  margin-left: 0;
  margin-top: 2px;
}
.rw-finding__math :deep(.katex) {
  font-size: 1em;
}
</style>
