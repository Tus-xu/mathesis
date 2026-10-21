<template>
  <!--
    AI 生成内容标识
    显式标识：紧贴 AI 输出末尾的可见文字提示
    机器可读标识：data-aigc* 属性（供抓取/审计/后续水印链路对接）
    依据：
      · 中国《人工智能生成合成内容标识办法》(2025-09-01 施行) + GB 45438-2025：
        文本内容需在适当位置（此处取「末尾」）添加文字提示；文件类另需元数据隐式标识
      · EU AI Act Art.50(1)(2)：告知用户正在与 AI 交互；AI 生成内容应带机器可读标记
      · 国际同行惯例（Gemini/Copilot 等）：显式标注 + 提醒核实
  -->
  <div
    class="aigc-mark"
    role="note"
    :aria-label="ariaLabel"
    :title="title"
    data-aigc="ai-generated"
    data-aigc-label="AI 生成"
    data-aigc-standard="GB45438-2025;CN-AIGC-LABELING;EU-AI-ACT-ART50"
    :data-aigc-provider="provider || ''"
    :data-aigc-content-id="contentId || ''"
  >
    <svg class="aigc-ico" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3.2 13.7 9l5.8 1.7-5.8 1.7L12 18.2 10.3 12.4 4.5 10.7 10.3 9z"
        stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"
      />
    </svg>
    <span class="aigc-txt">AI 生成</span>
    <span v-if="hint" class="aigc-hint">· {{ hint }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  /** 生成方（技能名/模型名），进 title 与 data-aigc-provider */
  provider: { type: String, default: '' },
  /** 内容编号，进 data-aigc-content-id */
  contentId: { type: String, default: '' },
  /** 核实提示；传空字符串可只保留「AI 生成」四字标识 */
  hint: { type: String, default: '仅供参考，请核实' },
})

const ariaLabel = computed(
  () => `本内容由 AI 生成${props.provider ? `（${props.provider}）` : ''}${props.hint ? `，${props.hint}` : ''}`,
)

const title = computed(() => {
  const parts = ['AI 生成内容']
  if (props.provider) parts.push(`生成方：${props.provider}`)
  if (props.contentId) parts.push(`编号：${props.contentId}`)
  parts.push('标识依据：《人工智能生成合成内容标识办法》/ GB 45438-2025 / EU AI Act Art.50')
  return parts.join(' ｜ ')
})
</script>

<style scoped>
/* 静默的细体标识：出现在每一条 AI 回复末尾，故克制为主、可读为先 */
.aigc-mark {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--line, #e2e8f0);
  font-size: 11.5px;
  line-height: 1.6;
  /* 对比度：#475569 在白底 7.3:1、在 --bg2(#f1f5f9) 底 6.6:1，均高于 WCAG AA 4.5:1 */
  color: #475569;
}
.aigc-ico {
  width: 12px;
  height: 12px;
  flex: 0 0 auto;
  color: var(--brand, #4f46e5);
}
.aigc-txt {
  font-weight: 600;
  color: #334155;
}
.aigc-hint {
  color: #475569;
}
@media (max-width: 480px) {
  .aigc-mark { font-size: 11px; gap: 4px; }
}
</style>
