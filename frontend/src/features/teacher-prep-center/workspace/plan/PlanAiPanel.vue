<script setup lang="ts">
/**
 * 右智能辅助面板（黄金图 310px）：`✨ 智能辅助` + 当前定位卡 + AI 助手建议卡（真实触发）+ 输入 + 蓝发送钮。
 * 反空壳：建议卡与输入均触发真实 `POST /regen`（由父组件执行）；AI 依据未就绪时诚实禁用并说明原因。
 */
import { computed, ref } from 'vue'
import type { AiSuggestion } from './usePlanEditor'
import { GENERATE } from '@/features/teacher-prep-center/copy/prepCopy'

const props = defineProps<{
  chapterTitle: string | null
  locatedLabel: string | null
  suggestions: readonly AiSuggestion[]
  canUseAi: boolean
  disabledReason: string
  aiBusy: boolean
  aiError: string | null
  aiNotice: string | null
  /** G14：是否有失败块可单块重试（真实 /regen）。 */
  canRetryBlock: boolean
}>()

const emit = defineEmits<{
  (e: 'suggest', card: AiSuggestion): void
  (e: 'ask', text: string): void
  (e: 'retry-block'): void
  (e: 'close'): void
}>()

const input = ref('')

const locationText = computed(() => {
  const c = props.chapterTitle
  const l = props.locatedLabel
  if (c && l) return `${c} › ${l}`
  return c || '教案文档'
})

const canSend = computed(() => props.canUseAi && !props.aiBusy && input.value.trim().length > 0)

function onSuggest(card: AiSuggestion) {
  if (!props.canUseAi || props.aiBusy) return
  emit('suggest', card)
}

function send() {
  const text = input.value.trim()
  if (!canSend.value) return
  emit('ask', text)
  input.value = ''
}

const ICON_PATHS: Record<AiSuggestion['icon'], string> = {
  sparkle: 'M8 1.4 9.5 5.6 13.8 7 9.5 8.4 8 12.6 6.5 8.4 2.2 7 6.5 5.6z',
  megaphone: 'M2.4 6.4 11 2.8v8.4L2.4 7.6V6.4Zm0 0v1.2a1.6 1.6 0 0 0 1.6 1.6h.6l1 3.4 1.5-.4-.9-3h.2',
  bulb: 'M8 1.8a4.2 4.2 0 0 0-2.6 7.5c.4.4.6.7.7 1.2h3.8c.1-.5.3-.8.7-1.2A4.2 4.2 0 0 0 8 1.8ZM5.9 11.8h4.2v1H5.9v-1Z',
  link: 'M9.4 5.2 10.7 3.9a2.6 2.6 0 0 1 3.7 3.7l-1.3 1.3M6.6 10.8 5.3 12.1a2.6 2.6 0 0 1-3.7-3.7l1.3-1.3M6 10l4-4',
}
</script>

<template>
  <aside class="pc-ai" aria-label="智能辅助">
    <div class="pc-ai__head">
      <span class="pc-ai__spark" aria-hidden="true">
        <svg viewBox="0 0 16 16"><path d="M8 1.2 9.6 5.8 14.2 7.4 9.6 9 8 13.6 6.4 9 1.8 7.4 6.4 5.8z" fill="currentColor" /></svg>
      </span>
      <h2 class="pc-ai__title">智能辅助</h2>
      <button type="button" class="pc-ai__close" aria-label="关闭智能辅助" @click="emit('close')">×</button>
    </div>

    <div class="pc-ai__locate">
      <span class="pc-ai__locate-label">当前定位</span>
      <span class="pc-ai__locate-value">{{ locationText }}</span>
    </div>

    <h3 class="pc-ai__section">AI 助手建议</h3>
    <ul class="pc-ai__cards">
      <li v-for="card in suggestions" :key="card.key">
        <button
          type="button"
          class="pc-ai__card"
          :disabled="!canUseAi || aiBusy"
          :title="disabledReason"
          @click="onSuggest(card)"
        >
          <span class="pc-ai__card-icon" aria-hidden="true">
            <svg viewBox="0 0 16 16"><path :d="ICON_PATHS[card.icon]" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
          </span>
          <span class="pc-ai__card-body">
            <span class="pc-ai__card-title">{{ card.title }}</span>
            <span class="pc-ai__card-sub">{{ card.subtitle }}</span>
            <span v-if="card.note" class="pc-ai__card-note">{{ card.note }}</span>
          </span>
          <svg class="pc-ai__card-chevron" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
          </svg>
        </button>
      </li>
    </ul>

    <div class="pc-ai__input-wrap">
      <p v-if="aiNotice" class="pc-ai__notice">{{ aiNotice }}</p>
      <p v-if="aiError" class="pc-ai__error">
        {{ aiError }}
        <!-- G14 单块重试：只重试失败的那一块（真实 lesson-plan /regen），其余块保持已生效内容 -->
        <button
          v-if="canRetryBlock"
          type="button"
          class="pc-ai__retry"
          :disabled="aiBusy"
          @click="emit('retry-block')"
        >
          {{ GENERATE.retryBlock }}
        </button>
      </p>
      <div class="pc-ai__input-box">
        <textarea
          v-model="input"
          class="pc-ai__input"
          rows="2"
          :placeholder="canUseAi ? '告诉 AI 如何修改这一部分…' : '教案的 AI 还没有准备好，稍等一下或刷新…'"
          :disabled="!canUseAi || aiBusy"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button type="button" class="pc-ai__send" :disabled="!canSend" aria-label="发送给 AI" @click="send">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M2.2 8 13.8 2.4 9.8 13.8 7.9 8.9 2.2 8Z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
      <p v-if="aiBusy" class="pc-ai__busy"><span class="pc-ai__dot"></span>正在执行 AI 修改…</p>
    </div>
  </aside>
</template>

<style scoped>
.pc-ai {
  width: 310px;
  flex: none;
  display: flex;
  flex-direction: column;
  padding: 18px 16px 16px;
  background: #fafdff;
  border-left: 1px solid var(--pc-line);
  overflow-y: auto;
  box-sizing: border-box;
  font-family: var(--pc-font);
}
.pc-ai__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.pc-ai__spark {
  display: inline-flex;
  color: var(--pc-primary);
}
.pc-ai__spark svg {
  width: 17px;
  height: 17px;
}
.pc-ai__title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-ai__close {
  margin-left: auto;
  border: none;
  background: transparent;
  font-size: 20px;
  line-height: 1;
  color: var(--pc-text-muted);
  cursor: pointer;
}
.pc-ai__locate {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 11px 14px;
  background: var(--pc-primary-soft);
  border-radius: var(--pc-radius-card);
  margin-bottom: 18px;
}
.pc-ai__locate-label {
  font-size: 11.5px;
  color: var(--pc-primary);
  opacity: 0.75;
}
.pc-ai__locate-value {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--pc-primary);
}
.pc-ai__section {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-ai__cards {
  list-style: none;
  margin: 0 0 16px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pc-ai__card {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-card);
  background: var(--pc-panel);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.pc-ai__card:hover:not(:disabled) {
  border-color: var(--pc-primary);
  background: #f7faff;
}
.pc-ai__card:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.pc-ai__card-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
}
.pc-ai__card-icon svg {
  width: 16px;
  height: 16px;
}
.pc-ai__card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pc-ai__card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--pc-text);
}
.pc-ai__card-sub {
  font-size: 11px;
  color: var(--pc-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 真实行为说明（11px 灰字；消除「跨页同步」误解） */
.pc-ai__card-note {
  margin-top: 2px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--pc-text-faint);
}
.pc-ai__card-chevron {
  flex: none;
  width: 12px;
  height: 12px;
  color: var(--pc-text-faint);
}
.pc-ai__input-wrap {
  margin-top: auto;
}
.pc-ai__notice {
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: var(--pc-radius-btn);
  background: #f0fdf4;
  color: var(--pc-success);
  font-size: 12px;
}
.pc-ai__error {
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: var(--pc-radius-btn);
  background: #fef2f2;
  color: var(--pc-danger);
  font-size: 12px;
  word-break: break-word;
}
/* G14 单块重试钮（失败块旁的真实重试入口） */
.pc-ai__retry {
  margin-left: 8px;
  padding: 1px 10px;
  border: 1px solid var(--pc-danger);
  border-radius: var(--pc-radius-full);
  background: transparent;
  color: var(--pc-danger);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.pc-ai__retry:hover:not(:disabled) {
  background: var(--pc-danger);
  color: #fff;
}
.pc-ai__retry:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.pc-ai__input-box {
  position: relative;
  border: 1px solid var(--pc-line);
  border-radius: var(--pc-radius-btn);
  background: var(--pc-panel);
  padding: 8px 10px 40px;
}
.pc-ai__input {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  color: var(--pc-text);
}
.pc-ai__input::placeholder {
  color: var(--pc-text-faint);
}
.pc-ai__send {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: var(--pc-primary);
  color: #fff;
  cursor: pointer;
}
.pc-ai__send:disabled {
  background: #bcd0f5;
  cursor: not-allowed;
}
.pc-ai__send svg {
  width: 18px;
  height: 18px;
}
.pc-ai__busy {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--pc-primary);
}
.pc-ai__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pc-primary);
  animation: pc-ai-pulse 1.2s ease-in-out infinite;
}
@keyframes pc-ai-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.3;
  }
}
</style>
