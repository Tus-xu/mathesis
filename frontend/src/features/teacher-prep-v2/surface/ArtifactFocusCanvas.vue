<template>
  <!--
    T22.1 · ARTIFACT_FOCUS（UX-CONTRACT D）：**双区**。
    左（主）：产物编辑区；右：340–420px 可收起对话助手。
    证据 / 版本 / QA / 导出全部走**按需互斥 drawer**，这里不出现第三栏。
  -->
  <div class="focus" data-testid="prep-artifact-focus">
    <header class="focus__bar" data-testid="prep-focus-bar">
      <button
        type="button"
        class="focus__back"
        data-testid="prep-focus-back"
        @click="emit('close')"
      >
        <n-icon :size="15"><ArrowBackOutline /></n-icon>
        {{ copyFor('prep.focus.back_conv') }}
      </button>

      <h1 class="focus__title">{{ artifact.title || typeName }}</h1>
      <span class="focus__rev">{{ copyWith('prep.t24.revision.label', artifact.revision) }}</span>

      <span class="focus__spacer" />

      <button type="button" class="focus__tool" data-testid="prep-open-version" @click="emit('open-overlay', 'version_history')">
        {{ copyFor('prep.overlay.revision') }}
      </button>
      <button type="button" class="focus__tool" data-testid="prep-open-qa" @click="emit('open-overlay', 'qa')">
        {{ copyFor('prep.overlay.qa') }}
      </button>
      <button type="button" class="focus__tool" data-testid="prep-open-evidence" @click="emit('open-overlay', 'evidence')">
        {{ copyFor('prep.overlay.evidence') }}
      </button>
      <button type="button" class="focus__tool" data-testid="prep-open-export" @click="emit('open-overlay', 'export')">
        {{ copyFor('prep.overlay.export') }}
      </button>

      <button
        type="button"
        class="focus__tool focus__tool--icon"
        :aria-expanded="!assistantCollapsed"
        data-testid="prep-toggle-assistant"
        @click="emit('toggle-assistant')"
      >
        <n-icon :size="15"><ChatbubbleOutline /></n-icon>
      </button>
    </header>

    <div class="focus__body">
      <main class="focus__canvas" data-testid="prep-artifact-canvas">
        <LessonPlanFocus
          v-if="artifact.artifact_type === 'lesson_plan_spec'"
          :title="artifact.title || typeName"
          :revision="focusRevision"
          :blocks="lessonBlocks"
          :export-capabilities="exportCapabilities"
          :lineage="lineage"
          :editing="editing"
          :read-only="readOnly"
          :busy="editingBusy"
          @propose="onArtifactPropose"
          @patch="onLessonPatch"
          @propose-operations="onProposeOperations"
          @apply="emit('apply-patch')"
          @reject="emit('reject-patch')"
          @revert="(rev) => emit('revert-revision', rev)"
          @regen="(p) => emit('regen-target', p)"
          @conflict-reload="emit('conflict-reload')"
          @conflict-reapply="emit('conflict-reapply')"
          @conflict-discard="emit('conflict-discard')"
          @cancel-export="emit('cancel-export', $event)"
          @refresh-download="emit('refresh-download', $event)"
          @export="onExport"
          @reload="emit('reload')"
        />
        <TeachingDeckFocus
          v-else-if="artifact.artifact_type === 'teaching_deck_spec'"
          :title="artifact.title || typeName"
          :revision="focusRevision"
          :slides="deckSlides"
          :qa-issues="qaIssues"
          :export-capabilities="exportCapabilities"
          :lineage="lineage"
          :editing="editing"
          :read-only="readOnly"
          :busy="editingBusy"
          @propose="onArtifactPropose"
          @patch="onDeckPatch"
          @propose-operations="onProposeOperations"
          @apply="emit('apply-patch')"
          @reject="emit('reject-patch')"
          @revert="(rev) => emit('revert-revision', rev)"
          @regen="(p) => emit('regen-target', p)"
          @retry-page="(pageNo) => emit('retry-page-qa', pageNo)"
          @conflict-reload="emit('conflict-reload')"
          @conflict-reapply="emit('conflict-reapply')"
          @conflict-discard="emit('conflict-discard')"
          @cancel-export="emit('cancel-export', $event)"
          @refresh-download="emit('refresh-download', $event)"
          @export="onExport"
          @reload="emit('reload')"
        />
        <template v-else>
        <!-- 真实产物内容：只渲染服务端给的结构，unknown 明确标注 -->
        <section v-if="sections.length" class="focus__sections">
          <article
            v-for="(sec, si) in sections"
            :key="`${sec.title}-${si}`"
            class="focus__section"
          >
            <h2 class="focus__section-title">{{ sec.title }}</h2>
            <ul class="focus__list">
              <li
                v-for="(item, ii) in sec.items"
                :key="`${item.text}-${ii}`"
                class="focus__item"
                :class="{ 'focus__item--selected': isSelected(si, ii) }"
                :data-testid="`prep-artifact-item-${si}-${ii}`"
                @click="toggleSelect(si, ii)"
              >
                <span class="focus__item-text">{{ item.text }}</span>
                <span v-if="item.sourceRef" class="focus__item-ref">{{ item.sourceRef }}</span>
              </li>
            </ul>
          </article>
        </section>

        <p v-else class="focus__unknown" data-testid="prep-artifact-unknown">
          {{ copyFor('prep.focus.empty_body') }}
        </p>

        <!-- 局部修改：所选范围 + locator + 指令 一起发送 -->
        <div v-if="selected" class="focus__ask" data-testid="prep-selection-ask">
          <span class="focus__ask-hint">
            {{ copyFor('prep.focus.selected_prefix') }}{{ selected.text.slice(0, 18) }}{{ selected.text.length > 18 ? '…' : '' }}{{ copyFor('prep.focus.selected_suffix') }}
            <code>{{ selected.locator }}</code> · {{ copyFor('prep.focus.current_version_prefix') }}{{ artifact.revision }}{{ copyFor('prep.focus.current_version_suffix') }}
          </span>
          <input
            v-model="instruction"
            class="focus__ask-input"
            :placeholder="copyFor('prep.focus.ask_placeholder')"
            data-testid="prep-selection-instruction"
            @keydown.enter="submitAsk"
          />
          <button
            type="button"
            class="focus__ask-send"
            :disabled="!instruction.trim()"
            data-testid="prep-selection-submit"
            @click="submitAsk"
          >
            {{ copyFor('prep.focus.ask_submit') }}
          </button>
        </div>

        <RevisionDiffPanel
          v-if="diff"
          :diff="diff"
          :busy="busy"
          data-testid="prep-revision-diff"
          @accept="emit('accept-diff')"
          @reject="emit('reject-diff')"
        />
        </template>
      </main>

      <!-- 可收起对话助手（340–420px），不是固定导航区 -->
      <aside
        v-show="!assistantCollapsed"
        class="focus__assistant"
        data-testid="prep-focus-assistant"
      >
        <div class="focus__assistant-head">
          <span>{{ copyFor('prep.focus.assistant') }}</span>
          <span class="focus__assistant-hint">{{ copyFor('prep.focus.assistant_hint') }}</span>
        </div>
        <div class="focus__assistant-scroll">
          <MessageItem
            v-for="m in relatedMessages"
            :key="m.message_id"
            :message="m"
            :actions="[]"
            :busy="busy"
            :active-artifact="null"
            @run="(a, t) => emit('run', a, t)"
            @retry="(id) => emit('retry', id)"
            @open-evidence="emit('open-overlay', 'evidence')"
            @propose-edit="() => {}"
            @open-focus="() => {}"
          />
        </div>
        <ComposerStage
          :model-value="draft"
          :busy="busy"
          @update:model-value="(v) => emit('update:draft', v)"
          @send="(v) => emit('send', v)"
          @stop="emit('stop')"
          @attach="emit('attach')"
        />
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowBackOutline, ChatbubbleOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import ComposerStage from './ComposerStage.vue'
import MessageItem from './MessageItem.vue'
import RevisionDiffPanel from './RevisionDiffPanel.vue'
import LessonPlanFocus, { type LessonBlock } from '../artifacts/lesson-plan/LessonPlanFocus.vue'
import TeachingDeckFocus, { type DeckSlide } from '../artifacts/deck/TeachingDeckFocus.vue'
import type { QaIssue } from '../artifacts/deck/SlideQaPanel.vue'
import type { ExportCapability } from '../artifacts/shared/ExportPanel.vue'
import type { ArtifactEditingView } from './useArtifactEditing'
import { copyFor, copyWith } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'
import type { PatchOperation } from '@/features/teacher-prep-v2/types/t24'
import type { MessageView } from '@/features/teacher-prep-v2/types/workspace'

interface DiffView {
  reason: string
  evidenceCount: number
  changes: Array<{ path: string; before: string; after: string }>
}

const props = withDefaults(defineProps<{
  artifact: ActiveArtifact
  body: Record<string, unknown> | null
  messages: MessageView[]
  assistantCollapsed: boolean
  draft: string
  busy: boolean
  diff: DiffView | null
  /**
   * T24 接线后的服务端事实/动作视图（由 `useArtifactEditing` 注入）。
   * 为 null 时退回旧的 body 派生行为，保证隔离挂载（组件单测）仍然成立。
   */
  editing?: ArtifactEditingView | null
}>(), { editing: null })

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-overlay', overlay: 'evidence' | 'version_history' | 'qa' | 'export'): void
  (e: 'toggle-assistant'): void
  (e: 'ask-revision', payload: { locator: string; instruction: string }): void
  (e: 'accept-diff'): void
  (e: 'reject-diff'): void
  (e: 'send', text: string): void
  (e: 'stop'): void
  (e: 'attach'): void
  (e: 'update:draft', v: string): void
  (e: 'run', action: string, target: string | null): void
  (e: 'retry', messageId: string): void
  (e: 'run', action: string, target: string | null, payload?: Record<string, unknown>): void
  (e: 'reload'): void
  /** T24：契约化的 typed action（不再借用通用 `run`）。 */
  (e: 'propose-patch', operations: PatchOperation[], summary?: string): void
  (e: 'apply-patch'): void
  (e: 'reject-patch'): void
  (e: 'revert-revision', revision: number): void
  (e: 'regen-target', payload: { kind: 'slide' | 'block'; targetId: string; instruction: string }): void
  (e: 'retry-page-qa', pageNo: number): void
  (e: 'request-export', format: string): void
  (e: 'cancel-export', format: string): void
  (e: 'refresh-download', format: string): void
  (e: 'conflict-reload'): void
  (e: 'conflict-reapply'): void
  (e: 'conflict-discard'): void
}>()

const instruction = ref('')
const selected = ref<{ section: number; index: number; text: string; locator: string } | null>(null)

/** 展示用 revision：服务端事实优先（body 里的 revision 只是快照副本）。 */
const focusRevision = computed(() =>
  props.editing?.revision ?? (typeof props.body?.revision === 'number' ? props.body.revision : props.artifact.revision),
)
const readOnly = computed(() => props.editing === null)
const editingBusy = computed(() => props.busy || props.editing?.busy === true)

const lineage = computed(() => props.editing?.lineage ?? {
  blueprintRevision: typeof props.body?.blueprint_revision === 'number' ? props.body.blueprint_revision : null,
  artifactRevision: props.artifact.revision,
  sourceLocator: typeof props.body?.source_locator === 'string' ? props.body.source_locator : null,
  runId: typeof props.body?.run_id === 'string' ? props.body.run_id : null,
  hash: typeof props.body?.content_hash === 'string' ? props.body.content_hash : null,
})

const exportCapabilities = computed<ExportCapability[]>(() => {
  // 接线后一律以服务端作业/票据状态为准（不伪造进度、不长期降级为 capability-disabled）
  if (props.editing) return props.editing.exportCapabilities

  const raw = props.body?.export_capabilities
  if (Array.isArray(raw)) {
    return raw.flatMap((entry) => {
      if (!entry || typeof entry !== 'object') return []
      const value = entry as Record<string, unknown>
      const format = typeof value.format === 'string' && value.format.trim() ? value.format.trim() : ''
      if (!format) return []
      const allowedStates = ['available', 'capability-disabled', 'pending', 'running', 'ready', 'done', 'failed', 'cancelled', 'expired'] as const
      if (!allowedStates.includes(value.state as (typeof allowedStates)[number])) return []
      const label = typeof value.label === 'string' && value.label.trim()
        ? value.label
        : copyFor(`prep.t24.export.format.${format.toLowerCase()}`)
      return [{
        format,
        label,
        state: value.state as ExportCapability['state'],
        downloadUrl: typeof value.download_url === 'string' ? value.download_url : null,
        fileName: typeof value.file_name === 'string' ? value.file_name : null,
      }]
    })
  }

  // 未接线的隔离挂载：8100 未声明任何能力 → 每个已知格式显式禁用（不伪造授权/进度）。
  const formats = props.artifact.artifact_type === 'lesson_plan_spec'
    ? ['markdown', 'pdf']
    : ['pptx', 'pdf']
  return formats.map((format) => ({
    format,
    label: copyFor(`prep.t24.export.format.${format}`),
    state: 'capability-disabled' as const,
  }))
})

const typeName = computed(() =>
  props.artifact.artifact_type === 'teaching_blueprint'
    ? copyFor('prep.artifact.type.teaching_blueprint')
    : props.artifact.artifact_type === 'diagnostic_task'
      ? copyFor('prep.artifact.type.diagnostic_task')
      : props.artifact.artifact_type === 'lesson_plan_spec'
        ? copyFor('prep.artifact.type.lesson_plan_spec')
        : copyFor('prep.artifact.type.teaching_deck_spec'),
)

interface Section {
  title: string
  items: Array<{ text: string; sourceRef?: string }>
}

/** 从服务端 body 抽取可展示结构；**没有就不渲染**（不填占位示例）。 */
const sections = computed<Section[]>(() => {
  const body = props.body
  if (!body) return []
  const out: Section[] = []

  const toItem = (v: unknown): { text: string; sourceRef?: string } | null => {
    if (typeof v === 'string') return { text: v }
    if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      const text = typeof o.text === 'string' ? o.text : typeof o.title === 'string' ? o.title : ''
      if (!text) return null
      const src = typeof o.source_ref === 'string' ? o.source_ref : undefined
      return { text, sourceRef: src }
    }
    return null
  }

  const objectives = body.objectives
  if (Array.isArray(objectives)) {
    const items = objectives.map(toItem).filter(Boolean) as Section['items']
    if (items.length) out.push({ title: copyFor('prep.focus.section.objectives'), items })
  }

  const flow = body.lesson_flow
  if (Array.isArray(flow)) {
    for (const stage of flow) {
      if (!stage || typeof stage !== 'object') continue
      const s = stage as Record<string, unknown>
      const title = typeof s.title === 'string' ? s.title : copyFor('prep.focus.section.flow')
      const items = Array.isArray(s.items)
        ? (s.items.map(toItem).filter(Boolean) as Section['items'])
        : []
      if (items.length) out.push({ title, items })
    }
  }
  return out
})

const lessonBlocks = computed<LessonBlock[]>(() => {
  // 接线后：服务端 spec 视图是唯一事实（快照 body 只是副本，可能滞后）
  if (props.editing) return props.editing.lessonBlocks

  const raw = props.body?.blocks
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return []
    const value = item as Record<string, unknown>
    const content = typeof value.content === 'string' ? value.content : typeof value.text === 'string' ? value.text : ''
    if (!content) return []
    return [{ id: typeof value.id === 'string' ? value.id : `block-${index}`, title: typeof value.title === 'string' ? value.title : copyFor('prep.t24.block.kind.content'), kind: typeof value.kind === 'string' ? value.kind : 'content', content, sourceLocator: typeof value.source_locator === 'string' ? value.source_locator : null }]
  })
})

const deckSlides = computed<DeckSlide[]>(() => {
  if (props.editing) {
    return props.editing.deckSlides.map((slide) => ({
      id: slide.id,
      title: slide.title,
      content: slide.content,
      notes: slide.notes,
      qaStatus: slide.qaStatus,
    }))
  }

  const raw = props.body?.slides
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return []
    const value = item as Record<string, unknown>
    return [{ id: typeof value.id === 'string' ? value.id : `slide-${index}`, title: typeof value.title === 'string' ? value.title : `#${index + 1}`, content: typeof value.content === 'string' ? value.content : '', notes: typeof value.notes === 'string' ? value.notes : '' }]
  })
})

const qaIssues = computed<QaIssue[]>(() => {
  const raw = props.body?.qa
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const value = item as Record<string, unknown>
    const severity = value.severity === 'blocking' || value.severity === 'warning' || value.severity === 'info' ? value.severity : 'info'
    return typeof value.message === 'string' ? [{ code: typeof value.code === 'string' ? value.code : 'qa', severity, message: value.message, action: typeof value.action === 'string' ? value.action : null }] : []
  })
})

/** 助手只保留与当前产物相关的消息（避免把整段历史搬进来）。 */
const relatedMessages = computed(() => {
  const a = props.artifact
  const rel = props.messages.filter(
    (m) =>
      (a.artifact_type === 'teaching_blueprint' && m.blueprint_id === a.artifact_id) ||
      m.kind?.includes('diagnostic'),
  )
  return (rel.length ? rel : props.messages).slice(-6)
})

function isSelected(si: number, ii: number) {
  return selected.value?.section === si && selected.value?.index === ii
}

function toggleSelect(si: number, ii: number) {
  const item = sections.value[si]?.items[ii]
  if (!item) return
  if (isSelected(si, ii)) {
    selected.value = null
    return
  }
  selected.value = {
    section: si,
    index: ii,
    text: item.text,
    // locator 必须随指令一起发送（服务端据此定位，不做全文重写）
    locator: `${sections.value[si].title}/${si}/${ii}`,
  }
}

function submitAsk() {
  if (!selected.value || !instruction.value.trim()) return
  emit('ask-revision', { locator: selected.value.locator, instruction: instruction.value.trim() })
  instruction.value = ''
}

/**
 * 块/页级 patch 的接线。
 *
 * 接线后（editing !== null）走**契约化 typed action**：`propose-patch` 携带
 * `operations`（含 `before`），由 store 打真实 `POST .../patches`，并带 expected_revision。
 * 未接线时保留旧的通用 `run` 通道（不再有生产调用方，仅为隔离挂载兼容）。
 */
function onLessonPatch(payload: { blockId: string; before: string; after: string }) {
  if (props.editing) return
  emit('run', 'propose_artifact_patch', props.artifact.artifact_id, { expected_revision: props.artifact.revision, patch: { block_id: payload.blockId, before: payload.before, after: payload.after } })
}
function onDeckPatch(payload: { slideId: string; target: 'content' | 'notes'; before: string; after: string }) {
  if (props.editing) return
  emit('run', 'propose_artifact_patch', props.artifact.artifact_id, { expected_revision: props.artifact.revision, patch: { slide_id: payload.slideId, target: payload.target, before: payload.before, after: payload.after } })
}
function onProposeOperations(operations: PatchOperation[]) {
  emit('propose-patch', operations)
}
function onExport(format: string) {
  if (props.editing) {
    emit('request-export', format)
    return
  }
  emit('run', 'request_export', props.artifact.artifact_id, { expected_revision: props.artifact.revision, format })
}
function onArtifactPropose() {
  emit('run', 'propose_artifact_patch', props.artifact.artifact_id, { expected_revision: focusRevision.value, patch: null })
}

watch(
  () => props.artifact.artifact_id,
  () => {
    selected.value = null
    instruction.value = ''
  },
)
</script>

<style scoped>
.focus {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--ma-surface-page);
}

.focus__bar {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2) var(--ma-space-3);
  border-bottom: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
}

.focus__back,
.focus__tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-btn);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.focus__back:hover,
.focus__tool:hover {
  border-color: var(--ma-action-primary);
  color: var(--ma-text-primary);
}

.focus__tool:focus-visible,
.focus__back:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}

.focus__title {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.focus__rev {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.focus__spacer {
  flex: 1 1 auto;
}

.focus__tool--icon {
  width: 30px;
  justify-content: center;
  padding: 0;
}

.focus__body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
}

.focus__canvas {
  min-width: 0;
  overflow-y: auto;
  padding: var(--ma-space-4) var(--ma-space-6);
  background: var(--ma-surface-default);
  border-right: 1px solid var(--ma-surface-border);
}

.focus__section {
  margin-bottom: var(--ma-space-4);
}

.focus__section-title {
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.focus__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-1);
}

.focus__item {
  display: flex;
  align-items: baseline;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2);
  border: 1px solid transparent;
  border-radius: var(--ma-radius-md);
  cursor: pointer;
  font-size: var(--ma-font-size-body);
  line-height: var(--ma-typography-body);
  color: var(--ma-text-body);
}

.focus__item:hover {
  background: var(--ma-surface-subtle);
}

.focus__item--selected {
  border-color: var(--ma-action-primary);
  background: var(--ma-action-primary-soft);
  color: var(--ma-text-primary);
}

.focus__item-ref {
  font-family: var(--ma-font-family-mono);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.focus__unknown {
  margin: 0;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}

.focus__ask {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-top: var(--ma-space-4);
  padding: var(--ma-space-2);
  border: 1px solid var(--ma-action-primary);
  border-radius: var(--ma-radius-md);
  background: var(--ma-action-primary-soft);
  flex-wrap: wrap;
}

.focus__ask-hint {
  flex: 1 1 100%;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.focus__ask-input {
  flex: 1 1 260px;
  height: 32px;
  padding: 0 var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-btn);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
  background: var(--ma-surface-default);
}

.focus__ask-send {
  height: 32px;
  padding: 0 var(--ma-space-3);
  border: 0;
  border-radius: var(--ma-radius-btn);
  background: var(--ma-action-primary);
  color: var(--ma-color-white-000);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.focus__ask-send:disabled {
  background: var(--ma-surface-border);
  color: var(--ma-text-muted);
  cursor: not-allowed;
}

.focus__assistant {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--ma-surface-page);
}

.focus__assistant-head {
  display: flex;
  align-items: baseline;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2) var(--ma-space-3);
  border-bottom: 1px solid var(--ma-surface-border);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.focus__assistant-hint {
  color: var(--ma-text-muted);
}

.focus__assistant-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--ma-space-3);
}

/* 较窄桌面：助手收窄但画布不被压到不可编辑 */
@media (max-width: 1280px) {
  .focus__body {
    grid-template-columns: minmax(0, 1fr) 340px;
  }
  .focus__canvas {
    padding: var(--ma-space-3) var(--ma-space-4);
  }
}
</style>
