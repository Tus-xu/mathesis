<template>
  <!-- 轻量行内 LaTeX 渲染：只处理 $...$/$$...$$，纯展示无交互；DOMPurify 消毒后输出 -->
  <span class="latex-text" v-html="html"></span>
</template>

<script setup>
import { computed } from 'vue'
import katex from 'katex'
import DOMPurify from 'dompurify'
import { mergeNestedMath } from '@/utils/latex'

const props = defineProps({
  text: { type: String, default: '' },
})

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escapeHtml = (s) => s.replace(/[&<>"]/g, (ch) => ESCAPES[ch])

const BARE_TEX_REPLACEMENTS = [
  // 嵌套一层花括号也能匹配（\frac{\eta^2}{...}），否则会残留 frac(...){...} 伪记号
  [/\\(?:d?frac)\{((?:[^{}]|\{[^{}]*\})*)\}\{((?:[^{}]|\{[^{}]*\})*)\}/g, '($1)/($2)'],
  [/\\sqrt\{((?:[^{}]|\{[^{}]*\})*)\}/g, '√($1)'],
  [/\\(?:rightarrow|to)\b/g, '→'],
  [/\\(?:leq|le)\b/g, '≤'],
  [/\\(?:geq|ge)\b/g, '≥'],
  [/\\neq\b/g, '≠'],
  [/\\infty\b/g, '∞'],
  [/\\pm\b/g, '±'],
  [/\\mp\b/g, '∓'],
  [/\\cup\b/g, '∪'],
  [/\\cap\b/g, '∩'],
  [/\\times\b/g, '×'],
  [/\\cdot\b/g, '·'],
  [/\\(?:left|right)\b/g, ''],
  // S3（V2 文档）：老题库残留的间距/句点命令——直接转空白，不落成可见源码
  [/\\qquad|\\quad/g, '  '],
  [/\\;/g, ' '],
  [/\\\./g, ''],
]

function renderPlainText(value) {
  // S11：课堂分层练习等场景用 \( \) 定界——归一为可读形式（先转 $..$ 走 KaTeX 的链路不需要，此管线直接剥壳）
  let result = escapeHtml(String(value || '').replace(/\\\((.+?)\\\)/g, '$1'))
  for (const [pattern, replacement] of BARE_TEX_REPLACEMENTS) result = result.replace(pattern, replacement)
  // Keep a malformed or uncommon command readable without exposing source syntax.
  return result.replace(/\\([A-Za-z]+)/g, '$1')
}

/**
 * 轻量渲染内核：不走 marked 全管线，仅把 $...$/$$...$$ 段交给 KaTeX，
 * 其余文本转义直出。嵌套/碎片化 $ 先经 mergeNestedMath 归一化（与 MarkdownView 同规则）。
 * 任何失败都回退为转义源码，绝不白屏。
 */
function renderLatex(src) {
  const s = mergeNestedMath(String(src ?? ''))
  let out = ''
  let i = 0
  const n = s.length
  while (i < n) {
    if (s[i] === '\\' && s[i + 1] === '$') { out += '$'; i += 2; continue } // \$ 按字面美元符
    if (s[i] !== '$') {
      const next = s.indexOf('$', i)
      const end = next === -1 ? n : next
      out += renderPlainText(s.slice(i, end))
      i = end
      continue
    }
    const block = s[i + 1] === '$'
    const close = block ? s.indexOf('$$', i + 2) : s.indexOf('$', i + 1)
    if (close === -1) { out += '$'; i += 1; continue } // 未闭合：$ 按字面输出
    try {
      out += katex.renderToString(s.slice(i + (block ? 2 : 1), close), {
        throwOnError: false,
        displayMode: block,
      })
    } catch {
      out += escapeHtml(s.slice(i, close + (block ? 2 : 1)))
    }
    i = close + (block ? 2 : 1)
  }
  return DOMPurify.sanitize(out, { USE_PROFILES: { html: true, mathMl: true, svg: true } })
}

const html = computed(() => renderLatex(props.text))
</script>
