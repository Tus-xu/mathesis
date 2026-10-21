<script setup lang="ts">
/**
 * 历史版本面板（3.7）：真实 blueprint revision 快照列表（时间/来源/摘要）。
 * 支持只读查看任一版本完整内容；回滚能力后端未上线 → 明确「即将支持」（不放假按钮）。
 */
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'
import type { PrepBlueprintRevision } from '@/features/teacher-prep-center/api/prepCenterApi'
import KatexText from '@/features/teacher-prep-center/math/KatexText.vue'

const store = usePrepSessionStore()
const route = useRoute()

const sessionId = computed(() => String(route.params.sessionId || store.currentSessionId || ''))
const viewing = ref<PrepBlueprintRevision | null>(null)

const SOURCE_LABELS: Record<string, string> = {
  teacher: '教师',
  ai_suggestion: 'AI',
  system: '系统',
}
const VERDICT_LABELS: Record<string, string> = {
  proposal: '提案',
  confirmed: '已确认',
  rejected: '已驳回',
}

function sourceLabel(s: string): string {
  return SOURCE_LABELS[s] || s
}
function timeText(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return iso
  }
}

/** 模板助手：避免在 template 表达式里写 TS 断言。 */
interface HistStage {
  title?: unknown
  duration_minutes?: unknown
  teacher_questions?: { text: string }[]
  student_activities?: { text: string }[]
  board_notes?: { text: string }[]
}
function stagesOf(item: PrepBlueprintRevision): HistStage[] {
  const flow = (item.body as { lesson_flow?: unknown } | undefined)?.lesson_flow
  return Array.isArray(flow) ? (flow as HistStage[]) : []
}
function itemsOf(stage: HistStage, key: 'teacher_questions' | 'student_activities' | 'board_notes'): { text: string }[] {
  const v = stage[key]
  return Array.isArray(v) ? v : []
}
function stageTitle(stage: HistStage, i: number): string {
  const t = typeof stage.title === 'string' ? stage.title : ''
  return `${i + 1}. ${t}`
}
function stageMinutes(stage: HistStage): string {
  const d = typeof stage.duration_minutes === 'number' && stage.duration_minutes > 0 ? stage.duration_minutes : null
  return d ? `${d} min` : '—'
}
function bpTitle(item: PrepBlueprintRevision): string {
  const t = (item.body as { title?: unknown } | undefined)?.title
  return (typeof t === 'string' && t) || item.title || '教学蓝图'
}

function summaryOf(item: PrepBlueprintRevision): string {
  if (item.note) return item.note
  const flow = item.body?.lesson_flow
  const stages = Array.isArray(flow) ? flow.length : item.stages
  const minutes = item.total_minutes ? `${item.total_minutes} 分钟` : '时长未设置'
  return `${stages} 个环节 · ${minutes}`
}

function reload() {
  if (sessionId.value) void store.loadHistory(sessionId.value)
}
function close() {
  viewing.value = null
  store.closeHistory()
}
</script>

<template>
  <Teleport to="body">
    <div v-if="store.historyOpen" class="pc-history-mask" @click.self="close">
      <aside class="pc-history" role="dialog" aria-label="历史版本">
        <header class="pc-history__head">
          <div>
            <h3 class="pc-history__title">历史版本</h3>
            <p class="pc-history__sub">
              教学蓝图共 {{ store.history?.current_revision ?? '—' }} 个版本（新 → 旧）；查看任意版本内容
              <span class="pc-history__soon">· 回滚即将支持</span>
            </p>
          </div>
          <button type="button" class="pc-history__close" aria-label="关闭" @click="close">×</button>
        </header>

        <div v-if="store.historyLoading" class="pc-history__state">正在加载版本列表…</div>
        <div v-else-if="store.historyError" class="pc-history__state pc-history__state--error">
          {{ store.historyError }}
          <button type="button" class="pc-history__retry" @click="reload">重试</button>
        </div>

        <ul v-else-if="store.history?.items?.length" class="pc-history__list">
          <li v-for="item in store.history.items" :key="item.revision_no" class="pc-history__item">
            <div class="pc-history__row">
              <span class="pc-history__no">V{{ item.revision_no }}</span>
              <span class="pc-history__source" :class="{ 'is-teacher': item.source === 'teacher' }">
                {{ sourceLabel(item.source) }}
              </span>
              <span class="pc-history__verdict">{{ VERDICT_LABELS[item.verdict] || item.verdict }}</span>
              <span class="pc-history__summary">{{ summaryOf(item) }}</span>
              <span class="pc-history__time">{{ timeText(item.created_at) }}</span>
              <button type="button" class="pc-history__view" @click="viewing = viewing?.revision_no === item.revision_no ? null : item">
                {{ viewing?.revision_no === item.revision_no ? '收起' : '查看' }}
              </button>
            </div>
            <!-- 只读版本内容（完整 body 投影） -->
            <div v-if="viewing?.revision_no === item.revision_no" class="pc-history__body">
              <p class="pc-history__bp-title">{{ bpTitle(item) }}</p>
              <div v-for="(stage, i) in stagesOf(item)" :key="i" class="pc-history__stage">
                <p class="pc-history__stage-title">
                  {{ stageTitle(stage, i) }}
                  <span class="pc-history__stage-min">{{ stageMinutes(stage) }}</span>
                </p>
                <ol class="pc-history__stage-items">
                  <li v-for="(g, gi) in itemsOf(stage, 'teacher_questions')" :key="`q${gi}`">
                    <KatexText :text="g.text" />
                  </li>
                </ol>
                <ol class="pc-history__stage-items">
                  <li v-for="(g, gi) in itemsOf(stage, 'student_activities')" :key="`a${gi}`">
                    <KatexText :text="g.text" />
                  </li>
                </ol>
                <ol class="pc-history__stage-items is-board">
                  <li v-for="(g, gi) in itemsOf(stage, 'board_notes')" :key="`b${gi}`">
                    <KatexText :text="g.text" />
                  </li>
                </ol>
              </div>
            </div>
          </li>
        </ul>
        <div v-else class="pc-history__state">暂无历史版本。</div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.pc-history-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.24);
  z-index: 95;
}
.pc-history {
  position: absolute;
  top: 0;
  right: 0;
  width: 560px;
  max-width: 94vw;
  height: 100%;
  background: var(--pc-panel);
  box-shadow: var(--pc-shadow-hover);
  display: flex;
  flex-direction: column;
  font-family: var(--pc-font);
}
.pc-history__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--pc-line);
}
.pc-history__title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-history__sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--pc-text-muted);
}
.pc-history__soon {
  color: var(--pc-text-faint);
}
.pc-history__close {
  margin-left: auto;
  border: none;
  background: transparent;
  font-size: 20px;
  color: var(--pc-text-muted);
  cursor: pointer;
  line-height: 1;
}
.pc-history__state {
  padding: 24px 18px;
  font-size: 13px;
  color: var(--pc-text-muted);
}
.pc-history__state--error {
  color: var(--pc-danger);
}
.pc-history__retry {
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
.pc-history__list {
  list-style: none;
  margin: 0;
  padding: 8px 12px 20px;
  overflow: auto;
  flex: 1;
}
.pc-history__item {
  padding: 10px 6px;
  border-bottom: 1px solid var(--pc-line);
}
.pc-history__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pc-history__no {
  flex: none;
  min-width: 34px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-history__source {
  flex: none;
  padding: 1px 8px;
  border-radius: var(--pc-radius-full);
  background: var(--pc-primary-soft);
  color: var(--pc-primary);
  font-size: 11px;
}
.pc-history__source.is-teacher {
  background: #ecfdf3;
  color: var(--pc-success);
}
.pc-history__verdict {
  flex: none;
  font-size: 11px;
  color: var(--pc-text-faint);
}
.pc-history__summary {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--pc-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-history__time {
  flex: none;
  font-size: 12px;
  color: var(--pc-text-faint);
}
.pc-history__view {
  flex: none;
  border: none;
  background: transparent;
  color: var(--pc-primary);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.pc-history__body {
  margin-top: 10px;
  padding: 12px 14px;
  background: var(--pc-bg);
  border-radius: var(--pc-radius-btn);
  max-height: 320px;
  overflow: auto;
}
.pc-history__bp-title {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 700;
  color: var(--pc-text);
}
.pc-history__stage {
  padding: 6px 0;
  border-top: 1px dashed var(--pc-line);
}
.pc-history__stage-title {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pc-text-2);
}
.pc-history__stage-min {
  font-weight: 400;
  color: var(--pc-text-faint);
  font-size: 12px;
  margin-left: 6px;
}
.pc-history__stage-items {
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: pc-hist-item;
}
.pc-history__stage-items li {
  position: relative;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--pc-text-2);
}
.pc-history__stage-items li::before {
  counter-increment: pc-hist-item;
  content: counter(pc-hist-item) '.';
  position: absolute;
  left: 2px;
  color: var(--pc-text-muted);
}
.pc-history__stage-items.is-board li::before {
  content: '•';
  left: 8px;
}
</style>
