<template>
  <!--
    T22 · 结构化卡片（context_card / selection_card / job_status_card / diagnostic_card / evidence_rail）。
    完全**数据驱动**：字段与文案来自服务端 props，本文件不含任何业务常量。
    未知字段不渲染（避免把未授权内容带进 UI）。
  -->
  <div class="structured" :data-testid="`prep-card-${componentType}`">
    <DirectChunkLocator v-if="componentType === 'direct_chunk_locator'" :locator="props.props" />
    <DiagnosticProposalCard v-else-if="componentType === 'diagnostic_proposal'" :proposal="props.props" @review="emit('review', $event)" />
    <ClassLearningProfileCard v-else-if="componentType === 'class_learning_profile'" :profile="props.props" />
    <BlueprintRevisionCard v-else-if="componentType === 'blueprint_revision'" :revision="props.props" @conflict="emit('conflict', $event)" />
    <RunContextDisclosure v-else-if="componentType === 'run_context_disclosure'" :context="props.props" />
    <template v-else-if="componentType === 'context_card'">
      <dl v-if="entries.length" class="structured__facts">
        <div v-for="entry in entries" :key="entry.key" class="structured__fact">
          <dt>{{ entry.key }}</dt>
          <dd>{{ entry.value }}</dd>
        </div>
      </dl>
      <dl v-if="contextSourceTitle || contextSectionTitle" class="structured__facts">
        <div v-if="contextSourceTitle" class="structured__fact">
          <dt>{{ copyFor('prep.context.source') }}</dt>
          <dd>{{ contextSourceTitle }}</dd>
        </div>
        <div v-if="contextSectionTitle" class="structured__fact">
          <dt>{{ copyFor('prep.context.section') }}</dt>
          <dd>{{ contextSectionTitle }}</dd>
        </div>
      </dl>
      <ul v-if="contextWarnings.length" class="structured__items">
        <li v-for="warning in contextWarnings" :key="warning" class="structured__item">
          <span class="structured__item-text">{{ warning }}</span>
        </li>
      </ul>
    </template>
    <template v-else-if="componentType === 'selection_card'">
      <p class="structured__selection-hint">{{ selectionHint }}</p>
      <!-- 选项少 → 保持平铺（不打散视线，也不多一个控件） -->
      <div
        v-if="selectionOptions.length && !selectionSearchable"
        class="structured__selection-options"
        role="list"
      >
        <button
          v-for="option in selectionOptions"
          :key="option.id"
          type="button"
          class="structured__selection-option"
          :data-testid="`prep-selection-option-${option.id}`"
          @click="emit('select', selectionPayload(option.id))"
        >
          {{ option.label }}
        </button>
      </div>
      <!-- 选项多 → 搜索 + 按版本分组（教材目录可达数十项） -->
      <template v-else-if="selectionSearchable">
        <div class="structured__selection-search">
          <label class="structured__selection-search-label" :for="selectionSearchId">
            {{ copyFor('prep.selection.search_label') }}
          </label>
          <input
            :id="selectionSearchId"
            v-model="selectionSearch"
            type="search"
            class="structured__selection-search-input"
            :placeholder="copyFor('prep.selection.search_placeholder')"
            data-testid="prep-selection-search"
          />
        </div>
        <div v-if="selectionGroups.length" class="structured__selection-groups">
          <section
            v-for="group in selectionGroups"
            :key="group.title || '__ungrouped__'"
            class="structured__selection-group"
            :data-testid="`prep-selection-group-${group.title || 'other'}`"
          >
            <p v-if="group.title" class="structured__selection-group-title">{{ group.title }}</p>
            <div class="structured__selection-options" role="list">
              <button
                v-for="option in group.options"
                :key="option.id"
                type="button"
                class="structured__selection-option"
                :data-testid="`prep-selection-option-${option.id}`"
                @click="emit('select', selectionPayload(option.id))"
              >
                {{ option.label }}
              </button>
            </div>
          </section>
        </div>
        <p v-else class="structured__empty">{{ copyFor('prep.selection.no_match') }}</p>
      </template>
      <p v-if="!selectionOptions.length" class="structured__empty">{{ copyFor('prep.selection.empty') }}</p>
    </template>
    <template v-else-if="componentType === 'blueprint_card'">
      <!--
        2026-09-14 备课中心简化：这里**不再**渲染大纲正文（教学主线 / 关键环节）。
        原先会把整段模型产出的 markdown（## 标题、![](images/…)）直接倾泻出来，
        既不可读，也把教师拉进一个还需要人工确认的中间环节。
        现在只保留一句进度陈述：AI 生成结束 → 自动投影 → 直达 PPT 编辑工作台。
      -->
      <p class="structured__intent" data-testid="prep-blueprint-progress">
        {{ copyFor('prep.blueprint.auto_preparing') }}
      </p>
    </template>
    <template v-else>
      <h4 v-if="title" class="structured__title">{{ title }}</h4>

    <dl v-if="entries.length" class="structured__facts">
      <div v-for="e in entries" :key="e.key" class="structured__fact">
        <dt>{{ e.key }}</dt>
        <dd>{{ e.value }}</dd>
      </div>
    </dl>

    <ul v-if="items.length" class="structured__items">
      <li v-for="(it, i) in items" :key="i" class="structured__item">
        <span class="structured__item-text">{{ it.text }}</span>
        <SourceLocatorChip v-if="it.sourceRef" :locator-ref="it.sourceRef" kind="fact" :title="it.sourceRef" />
      </li>
    </ul>

      <p v-if="!title && !entries.length && !items.length" class="structured__empty">
        {{ copyFor('prep.card.unknown') }}
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import SourceLocatorChip from '../SourceLocatorChip.vue'
import DirectChunkLocator from '../evidence/DirectChunkLocator.vue'
import DiagnosticProposalCard from './DiagnosticProposalCard.vue'
import ClassLearningProfileCard from './ClassLearningProfileCard.vue'
import BlueprintRevisionCard from './BlueprintRevisionCard.vue'
import RunContextDisclosure from './RunContextDisclosure.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ComponentType } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{
  componentType: ComponentType
  props: Record<string, unknown>
  requiresConfirmation?: boolean
}>()
const emit = defineEmits<{
  (event: 'review', payload: { action: string; target: string; expected_revision: number }): void
  (event: 'conflict', payload: { status: 409; recoverable: true }): void
  (event: 'select', payload: { textbook_id?: string; section_id?: string; class_id?: string }): void
}>()

/** 只挑可安全展示的标量字段（不遍历未知结构，避免渲染未授权内容）。 */
// 内部标识符不得出现在教师界面（见 .impeccable.md）：
// context_snapshot_hash / snapshot_hash 是 64 位十六进制内容指纹，教师无法理解也无从使用，
// 曾以「上下文摘要」为标题原样渲染在提案卡上。此处不再作为可展示字段。
const SCALAR_KEYS = ['status', 'title', 'revision', 'revision_no', 'estimated_duration_minutes', 'confidence', 'coverage']
const FIELD_COPY_KEYS: Record<string, string> = {
  status: 'prep.structured.field.status',
  revision: 'prep.structured.field.revision',
  revision_no: 'prep.structured.field.revision',
  estimated_duration_minutes: 'prep.structured.field.estimated_duration_minutes',
  confidence: 'prep.structured.field.confidence',
  coverage: 'prep.structured.field.coverage',
}
const ENUM_COPY_KEYS: Record<string, string> = {
  proposal: 'prep.structured.value.proposal',
  draft: 'prep.structured.value.draft',
  confirmed: 'prep.structured.value.confirmed',
  rejected: 'prep.structured.value.rejected',
  pending: 'prep.structured.value.pending',
  running: 'prep.structured.value.running',
  succeeded: 'prep.structured.value.succeeded',
  failed: 'prep.structured.value.failed',
  cancelled: 'prep.structured.value.cancelled',
  low: 'prep.structured.value.low',
  medium: 'prep.structured.value.medium',
  high: 'prep.structured.value.high',
}

const title = computed(() => {
  const t = props.props?.title
  return typeof t === 'string' ? t : ''
})

/**
 * 2026-09-14 备课中心简化：原先的 `blueprintData` / `blueprintIntent` / `blueprintSections`
 * 已随「大纲预览」一起删除——正文不再渲染，模板只保留一句进度陈述。
 */

interface SelectionOption {
  id: string
  label: string
}

const selectionKind = computed(() => {
  const kind = props.props?.kind
  return typeof kind === 'string' ? kind : ''
})

// 选项数超过该阈值才启用「搜索 + 分组」。少量选项平铺更省事，也少一个控件
// （见 .impeccable.md：宁可少放一个控件，也不要让教师做无谓的选择）。
const SELECTION_SEARCH_THRESHOLD = 8

let selectionSearchSeq = 0
const selectionSearchId = `prep-selection-search-${++selectionSearchSeq}`
const selectionSearch = ref('')

const selectionOptions = computed<SelectionOption[]>(() => {
  const raw = props.props?.options
  if (!Array.isArray(raw)) return []
  return raw
    .map((option) => {
      if (!option || typeof option !== 'object') return null
      const item = option as Record<string, unknown>
      const id = typeof item.id === 'string' ? item.id : ''
      const label = typeof item.label === 'string' ? item.label : ''
      return id && label ? { id, label } : null
    })
    .filter((option): option is SelectionOption => option !== null)
})

const selectionSearchable = computed(() => selectionOptions.value.length > SELECTION_SEARCH_THRESHOLD)

const matchedSelectionOptions = computed<SelectionOption[]>(() => {
  const query = selectionSearch.value.trim().toLowerCase()
  if (!query) return selectionOptions.value
  return selectionOptions.value.filter((option) => option.label.toLowerCase().includes(query))
})

// 按 label 首段分组（教材 label 形如「人教A版2019 · 选择性必修第一册 · 教师用书 · …」，
// 首段即版本）。用 Map 累计而非只比相邻项，避免未排序时同一版本被拆成多组。
const selectionGroups = computed<Array<{ title: string; options: SelectionOption[] }>>(() => {
  const buckets = new Map<string, SelectionOption[]>()
  for (const option of matchedSelectionOptions.value) {
    const title = option.label.split('·')[0].trim()
    const bucket = buckets.get(title)
    if (bucket) bucket.push(option)
    else buckets.set(title, [option])
  }
  return [...buckets.entries()].map(([title, options]) => ({ title, options }))
})

const selectionHint = computed(() => {
  const key = selectionKind.value === 'section'
    ? 'prep.selection.choose_section'
    : selectionKind.value === 'class'
      ? 'prep.selection.choose_class'
      : 'prep.selection.choose_textbook'
  return copyFor(key)
})

const contextSourceTitle = computed(() => {
  const source = props.props?.source_title
  if (typeof source === 'string' && source.trim()) return source
  const nested = props.props?.source
  return nested && typeof nested === 'object' && typeof (nested as Record<string, unknown>).title === 'string'
    ? String((nested as Record<string, unknown>).title)
    : ''
})

const contextSectionTitle = computed(() => {
  const section = props.props?.section_title
  if (typeof section === 'string' && section.trim()) return section
  const nested = props.props?.section
  return nested && typeof nested === 'object' && typeof (nested as Record<string, unknown>).title === 'string'
    ? String((nested as Record<string, unknown>).title)
    : ''
})

// 上下文复核提示：后端给的是内部码，不得直接露给教师（见 .impeccable.md）。
// 未收录的码原样兜底，避免新增码时静默丢失信息。
const WARNING_COPY_KEYS: Record<string, string> = {
  incomplete_chapter: 'prep.warning.incomplete_chapter',
  no_units: 'prep.warning.no_units',
  rule_extraction_fallback: 'prep.warning.rule_extraction_fallback',
  no_prior_knowledge: 'prep.warning.no_prior_knowledge',
  no_objectives_evidence: 'prep.warning.no_objectives_evidence',
  no_key_difficult_evidence: 'prep.warning.no_key_difficult_evidence',
  no_design_intent: 'prep.warning.no_design_intent',
  no_data: 'prep.warning.no_data',
  no_answer: 'prep.warning.no_answer',
  no_authorized_content: 'prep.warning.no_authorized_content',
  conflicting_evidence: 'prep.warning.conflicting_evidence',
}

const contextWarnings = computed(() => {
  const raw = props.props?.warnings
  if (!Array.isArray(raw)) return []
  const labels = raw
    .map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object' && typeof (item as Record<string, unknown>).message === 'string') {
        return String((item as Record<string, unknown>).message)
      }
      return ''
    })
    .filter(Boolean)
    .map((code) => {
      const copyKey = WARNING_COPY_KEYS[code]
      return copyKey ? copyFor(copyKey) : code
    })
  // 后端同一码可能按不同 ref 重复下发（实测 no_units ×10），逐条平铺会让卡片变成
  // 复读机且淹没真正不同的提示 → 去重保序。
  return [...new Set(labels)]
})

function selectionPayload(id: string) {
  if (selectionKind.value === 'section') return { section_id: id }
  if (selectionKind.value === 'class') return { class_id: id }
  return { textbook_id: id }
}

const entries = computed(() =>
  SCALAR_KEYS.filter((k) => props.props?.[k] !== undefined && props.props?.[k] !== null && k !== 'title').map(
    (k) => ({ key: copyFor(FIELD_COPY_KEYS[k]), value: formatValue(props.props[k], k) }),
  ),
)

function formatValue(value: unknown, key: string): string {
  if (key === 'coverage' && typeof value === 'number') return `${Math.round(value * 100)}%`
  if (typeof value === 'string') {
    const copyKey = ENUM_COPY_KEYS[value.toLowerCase()]
    return copyKey ? copyFor(copyKey) : value
  }
  return String(value)
}

interface Item {
  text: string
  sourceRef?: string
}

/** 从常见数组桶提取可展示条目（objectives / items / knowledge_points / key_points / lesson_flow…）。 */
const items = computed<Item[]>(() => {
  const out: Item[] = []
  const keys = [
    'objectives',
    'items',
    'knowledge_points',
    'key_points',
    'difficult_points',
    'lesson_flow',
    'facts',
    'selection',
    'refs',
  ]
  for (const key of keys) {
    const raw = props.props?.[key]
    if (Array.isArray(raw)) {
      for (const v of raw) {
        if (typeof v === 'string') out.push({ text: v })
        else if (v && typeof v === 'object') {
          const o = v as Record<string, unknown>
          let text = typeof o.text === 'string' ? o.text : typeof o.title === 'string' ? o.title : ''
          if (!text && Array.isArray(o.items)) {
            const inner = o.items
              .map((x) => (typeof x === 'string' ? x : ((x as Record<string, unknown>)?.text as string) || ''))
              .filter(Boolean)
              .join(' · ')
            if (inner) text = `${typeof o.title === 'string' ? o.title + '：' : ''}${inner}`
          }
          if (!text) continue
          const src = typeof o.source_ref === 'string' ? o.source_ref : typeof o.chunk_ref === 'string' ? o.chunk_ref : ''
          out.push({ text, sourceRef: src || undefined })
        }
      }
    } else if (raw && typeof raw === 'object') {
      const o = raw as Record<string, unknown>
      const kps = o.knowledge_points
      if (Array.isArray(kps)) for (const v of kps) if (typeof v === 'string') out.push({ text: v })
    }
  }
  return out.slice(0, 50)
})
</script>

<style scoped>
.structured__title {
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-title);
  color: var(--ma-text-primary);
}

.structured__title--proposal {
  font-size: var(--ma-font-size-display);
}

.structured__intent {
  margin: 0 0 var(--ma-space-3);
  color: var(--ma-text-body);
  line-height: var(--ma-typography-body);
}

.structured__section {
  margin-top: var(--ma-space-3);
}

.structured__section-title {
  margin: 0 0 var(--ma-space-1);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.structured__section-list {
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-1);
  margin: 0;
  padding-left: var(--ma-space-4);
  color: var(--ma-text-body);
}

.structured__section-item {
  line-height: var(--ma-typography-body);
}

.structured__confirm {
  margin: var(--ma-space-4) 0 0;
  padding: var(--ma-space-2) var(--ma-space-3);
  border-left: 2px solid var(--ma-action-primary);
  background: var(--ma-action-primary-soft);
  color: var(--ma-text-primary);
  line-height: var(--ma-typography-body);
}

.structured__facts {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--ma-space-1) var(--ma-space-3);
  margin: 0 0 var(--ma-space-2);
  font-size: var(--ma-font-size-body);
}

.structured__fact dt {
  color: var(--ma-text-muted);
}

.structured__fact dd {
  margin: 0;
  color: var(--ma-text-body);
}

.structured__items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-1);
}

.structured__item {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  flex-wrap: wrap;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.structured__empty {
  margin: 0;
  color: var(--ma-text-muted);
}

.structured__selection-hint {
  margin: 0 0 var(--ma-space-2);
  color: var(--ma-text-body);
  line-height: var(--ma-typography-body);
}

.structured__selection-options {
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-2);
}

.structured__selection-option {
  width: 100%;
  padding: var(--ma-space-2) var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-control);
  background: var(--ma-surface-default);
  color: var(--ma-text-primary);
  text-align: left;
  cursor: pointer;
}

.structured__selection-option:hover,
.structured__selection-option:focus-visible {
  border-color: var(--ma-action-primary);
  background: var(--ma-action-primary-soft);
}

.structured__selection-search {
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-1);
  margin-bottom: var(--ma-space-3);
}

.structured__selection-search-label {
  color: var(--ma-text-muted);
  line-height: var(--ma-typography-body);
}

.structured__selection-search-input {
  width: 100%;
  padding: var(--ma-space-2) var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-control);
  background: var(--ma-surface-default);
  color: var(--ma-text-primary);
  font-family: inherit;
  font-size: var(--ma-font-size-body);
}

.structured__selection-search-input:focus-visible {
  border-color: var(--ma-action-primary);
  outline: none;
}

.structured__selection-groups {
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-4);
}

.structured__selection-group-title {
  margin: 0 0 var(--ma-space-2);
  color: var(--ma-text-muted);
  line-height: var(--ma-typography-body);
}
</style>
