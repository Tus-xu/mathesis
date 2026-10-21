<template>
  <div v-if="items.length || running || error" ref="rootRef" class="rw-chat" data-rw-chat>
    <template v-for="m in items" :key="m.id">
      <!-- 用户消息 -->
      <div v-if="m.chat && m.role === 'user'" class="rw-chat__row rw-chat__row--user">
        <div class="rw-chat__user-bubble">
          <MarkdownView :text="m.text || ''" />
        </div>
      </div>

      <!-- Agent 普通对话：像学生端 AI 消息一样，头像 + 正文，不做成一张张工具卡 -->
      <div v-else-if="m.chat" class="rw-chat__row rw-chat__row--agent">
        <span class="rw-chat__avatar" aria-hidden="true">π</span>
        <div class="rw-chat__agent-body">
          <span class="rw-chat__label">Research Agent</span>
          <div class="rw-chat__text">
            <MarkdownView :text="m.text || ''" />
          </div>
        </div>
      </div>

      <!-- 结构化结果：回答只保留一层容器，证据/动作由 ResultCard 统一承载 -->
      <div v-else-if="m.result" class="rw-chat__row rw-chat__row--agent">
        <span class="rw-chat__avatar" aria-hidden="true">π</span>
        <div class="rw-chat__agent-body">
          <div class="rw-chat__result-head">
            <span class="rw-chat__result-ico" :class="`tone-${metaFor(m).tone}`">
              <component :is="metaFor(m).icon" />
            </span>
            <span class="rw-chat__result-title">{{ metaFor(m).label }}</span>
            <StatusPill
              v-if="statusFor(m)"
              :tone="statusFor(m)!.tone"
              :text="statusFor(m)!.label"
              :icon="statusFor(m)!.icon"
            />
            <span class="rw-chat__time">{{ hm(m.at) }}</span>
          </div>
          <div class="rw-chat__result-card">
            <ResultCard
              :result="m.result"
              :mode="mode"
              :preset="m.preset || null"
              :line-resolver="lineResolver"
              :busy="busy"
              :feedback="feedback"
              @action="(key) => emit('action', key, m)"
              @source="(ev) => emit('source', ev)"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- 执行中 -->
    <div v-if="running" class="rw-chat__row rw-chat__row--agent rw-chat__row--thinking" role="status" aria-live="polite">
      <span class="rw-chat__avatar" aria-hidden="true">π</span>
      <div class="rw-chat__agent-body">
        <span class="rw-chat__label">{{ runningLabel }}</span>
        <div class="rw-chat__thinking">
          <span class="rw-chat__spinner" aria-hidden="true"></span>
          <span>{{ activity || '正在处理…' }}</span>
        </div>
      </div>
    </div>

    <!-- 错误：中文人话 + 重试，不藏任务 -->
    <div v-if="error && !running" class="rw-chat__error" role="alert">
      <span>{{ error.message }}</span>
      <button type="button" @click="emit('retry')">重新尝试</button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 科研对话线程（2026-10-21 重构）：
 * - 自由对话与能力结果统一进入**同一条时间线**，能力不再以五张 Accordion 占据首屏；
 * - 用户消息右对齐、Agent 消息左对齐；结构化结果只保留一层容器（ResultCard）；
 * - 证据/定位/写笔记等动作仍由 ResultCard 的真实链路承载。
 */
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownView from '@/components/MarkdownView.vue'
import ResultCard from './ResultCard.vue'
import StatusPill from '../ui/StatusPill.vue'
import { presetMeta, statusView, type PresetView } from './useAgentTask'
import type { RvError } from '../api/client'
import type { AgentThreadItem } from '../stores/agent'
import type { AgentPreset, EvidenceSource, WorkspaceMode } from '../api/types'

const props = withDefaults(
  defineProps<{
    items: AgentThreadItem[]
    mode: WorkspaceMode
    running?: boolean
    activity?: string
    preset?: AgentPreset | null
    error?: RvError | null
    lineResolver?: (file: string, line: number) => string
    busy?: boolean
    feedback?: string
  }>(),
  {
    running: false,
    activity: '',
    preset: null,
    error: null,
    lineResolver: undefined,
    busy: false,
    feedback: '',
  },
)

const emit = defineEmits<{
  (e: 'action', key: string, item: AgentThreadItem): void
  (e: 'source', ev: EvidenceSource): void
  (e: 'retry'): void
}>()

const rootRef = ref<HTMLElement | null>(null)
const runningLabel = computed(() => {
  const p = props.preset
  return p ? presetMeta(p, props.mode).label : 'Research Agent'
})

function metaFor(m: AgentThreadItem): PresetView {
  const preset = (m.preset || m.result?.kind || 'suggestion') as AgentPreset
  return presetMeta(preset, props.mode)
}
function statusFor(m: AgentThreadItem) {
  return m.result ? statusView(m.result.status) : null
}
function hm(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function maybeScroll() {
  const el = rootRef.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160
  if (nearBottom) el.scrollTop = el.scrollHeight
}
watch(
  () => [props.items.length, props.running, props.activity],
  () => nextTick(maybeScroll),
)
</script>

<style scoped>
.rw-chat {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 14px 14px 18px;
}
.rw-chat__row { display: flex; gap: 9px; min-width: 0; }
.rw-chat__row--agent { align-items: flex-start; }
.rw-chat__row--user { justify-content: flex-end; }
.rw-chat__avatar {
  width: 28px; height: 28px; flex: 0 0 auto; display: grid; place-items: center;
  border-radius: 50%; background: var(--gradient-brand); color: #fff;
  font: italic 900 14px Georgia, "Times New Roman", serif;
  box-shadow: 0 4px 12px -8px rgba(79, 70, 229, .6);
}
.rw-chat__agent-body { flex: 1; min-width: 0; }
.rw-chat__label { display: block; margin-bottom: 4px; font-size: 10px; font-weight: 800; color: var(--rw-primary); letter-spacing: .02em; }
.rw-chat__text {
  font-size: 12.5px; line-height: 1.7; color: var(--rw-text);
  background: var(--rw-surface-2); border-radius: 3px 12px 12px 12px; padding: 8px 11px;
}
.rw-chat__text :deep(.md-body) { font-size: 12.5px; line-height: 1.7; }
.rw-chat__text :deep(.md-body > :first-child) { margin-top: 0; }
.rw-chat__text :deep(.md-body > :last-child) { margin-bottom: 0; }
.rw-chat__text :deep(.md-body p) { margin: 5px 0; }
.rw-chat__text :deep(.md-body ul), .rw-chat__text :deep(.md-body ol) { margin: 5px 0; padding-left: 18px; }
.rw-chat__text :deep(.md-body code) { padding: 1px 4px; border-radius: 5px; background: var(--rw-surface); border: 1px solid var(--rw-border); font-family: var(--rw-font-mono); font-size: 11px; }
.rw-chat__text :deep(.katex) { font-size: 1em; }
.rw-chat__user-bubble {
  max-width: 88%; padding: 9px 12px; border-radius: 14px 14px 4px 14px;
  background: var(--rw-primary-soft); border: 1px solid var(--rw-primary-border, #c7d2fe);
  color: var(--rw-text); font-size: 12.5px; line-height: 1.65;
}
.rw-chat__user-bubble :deep(.md-body) { font-size: 12.5px; line-height: 1.65; color: var(--rw-text); }
.rw-chat__user-bubble :deep(.md-body > :first-child) { margin-top: 0; }
.rw-chat__user-bubble :deep(.md-body > :last-child) { margin-bottom: 0; }

.rw-chat__result-head { display: flex; align-items: center; gap: 7px; margin-bottom: 6px; flex-wrap: wrap; }
.rw-chat__result-ico {
  width: 24px; height: 24px; display: grid; place-items: center; border-radius: 7px;
  background: var(--rw-primary-soft); color: var(--rw-primary); flex: none;
}
.rw-chat__result-ico.tone-success { background: var(--rw-success-soft); color: var(--rw-success); }
.rw-chat__result-ico.tone-warning { background: var(--rw-warning-soft); color: var(--rw-warning); }
.rw-chat__result-ico.tone-danger { background: var(--rw-danger-soft); color: var(--rw-danger); }
.rw-chat__result-ico.tone-purple { background: #f5f3ff; color: #7c3aed; }
.rw-chat__result-ico :deep(svg) { width: 14px; height: 14px; }
.rw-chat__result-title { font-size: 12px; font-weight: 800; color: var(--rw-text); }
.rw-chat__time { margin-left: auto; font-size: 9.5px; color: var(--rw-text-3); }
.rw-chat__result-card { border: 1px solid var(--rw-border); border-radius: 14px; background: var(--rw-surface); overflow: hidden; }
.rw-chat__result-card :deep(.rw-result) { padding: 12px 13px; }

.rw-chat__thinking { display: flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: 10px; background: var(--rw-surface-2); color: var(--rw-text-2); font-size: 11.5px; }
.rw-chat__spinner { width: 11px; height: 11px; border: 2px solid var(--rw-border-strong); border-top-color: var(--rw-primary); border-radius: 50%; animation: rw-chat-spin .8s linear infinite; }
@keyframes rw-chat-spin { to { transform: rotate(360deg); } }
.rw-chat__error { padding: 10px 12px; border-radius: 10px; border: 1px solid #fecaca; background: var(--rw-danger-soft); color: var(--rw-danger); font-size: 11.5px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rw-chat__error button { border: 0; background: none; color: inherit; font: inherit; font-weight: 800; cursor: pointer; }

@container rw-agent (max-width: 400px) {
  .rw-chat { padding: 12px 10px 14px; gap: 13px; }
  .rw-chat__text, .rw-chat__user-bubble { font-size: 12px; }
  .rw-chat__text :deep(.md-body), .rw-chat__user-bubble :deep(.md-body) { font-size: 12px; }
}
</style>
