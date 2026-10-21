<template>
  <section class="conflict" data-testid="t24-conflict-resolver" role="alert">
    <h2>{{ copyFor('prep.t24.conflict.title') }}</h2>
    <p>{{ copyFor('prep.t24.conflict.body') }}</p>
    <p class="conflict__versions">
      {{ copyFor('prep.t24.conflict.expected') }} {{ expectedRevision }} ·
      {{ copyFor('prep.t24.conflict.current') }} {{ currentRevision }}
    </p>
    <p v-if="summaryZh" class="conflict__summary" data-testid="t24-conflict-summary">{{ summaryZh }}</p>

    <!-- 字段级 diff：target / field / before / after —— 全部由服务端 409 载荷提供 -->
    <table v-if="fields.length" class="conflict__fields" data-testid="t24-conflict-fields">
      <caption class="conflict__caption">{{ copyFor('prep.t24.conflict.diff_caption') }}</caption>
      <thead>
        <tr>
          <th scope="col">{{ copyFor('prep.t24.conflict.col.target') }}</th>
          <th scope="col">{{ copyFor('prep.t24.conflict.col.field') }}</th>
          <th scope="col">{{ copyFor('prep.t24.conflict.col.before') }}</th>
          <th scope="col">{{ copyFor('prep.t24.conflict.col.after') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(field, index) in fields" :key="`${field.target}-${field.field}-${index}`" :data-target="field.target">
          <td class="conflict__cell-target">{{ field.target }}</td>
          <td>{{ field.field }}</td>
          <td class="conflict__cell-before">{{ field.before || copyFor('prep.t24.conflict.empty_value') }}</td>
          <td class="conflict__cell-after">{{ field.after || copyFor('prep.t24.conflict.empty_value') }}</td>
        </tr>
      </tbody>
    </table>

    <ul v-if="operations.length" class="conflict__ops" data-testid="t24-conflict-operations">
      <li v-for="op in operations" :key="op.index" :data-status="op.status">
        <code>#{{ op.index }}</code> {{ opStatusLabel(op.status) }}
        <span v-if="op.message"> · {{ op.message }}</span>
      </li>
    </ul>

    <div class="conflict__actions">
      <button type="button" data-testid="t24-conflict-reload" :disabled="busy" @click="emit('reload')">
        {{ copyFor('prep.t24.conflict.reload') }}
      </button>
      <button type="button" class="conflict__primary" data-testid="t24-conflict-reapply" :disabled="busy" @click="emit('reapply')">
        {{ copyFor('prep.t24.conflict.reapply') }}
      </button>
      <button type="button" class="conflict__quiet" data-testid="t24-conflict-discard" :disabled="busy" @click="emit('discard')">
        {{ copyFor('prep.t24.conflict.discard') }}
      </button>
      <button v-if="showCompare" type="button" class="conflict__quiet" data-testid="t24-conflict-compare" :disabled="busy" @click="emit('compare')">
        {{ copyFor('prep.t24.conflict.compare') }}
      </button>
    </div>

    <p v-if="reloaded" class="conflict__hint" data-testid="t24-conflict-reloaded">
      {{ copyFor('prep.t24.conflict.reloaded_hint') }}
    </p>
    <p v-else class="conflict__hint">{{ copyFor('prep.t24.conflict.reapply_hint') }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ConflictField, ConflictOperation } from '@/features/teacher-prep-v2/types/t24'

/**
 * 409 冲突解决器（INV-1）。
 *
 * - 展示的是**服务端**给的 expected/current + 字段级 diff + 逐操作状态；
 * - 三个明确出口：重新加载 / 按最新 revision 重提 / 放弃本地草稿；
 * - 本组件**不做**任何自动覆盖（last-write-wins 被禁止）。
 */
const props = withDefaults(defineProps<{
  expectedRevision: number
  currentRevision: number
  fields?: ConflictField[]
  operations?: ConflictOperation[]
  summaryZh?: string | null
  reloaded?: boolean
  busy?: boolean
  /** 兼容旧调用：仍可只传两个 revision。 */
  showCompare?: boolean
}>(), {
  fields: () => [],
  operations: () => [],
  summaryZh: null,
  reloaded: false,
  busy: false,
  showCompare: true,
})

const emit = defineEmits<{
  (e: 'reload'): void
  (e: 'reapply'): void
  (e: 'discard'): void
  (e: 'compare'): void
}>()

const fields = computed(() => props.fields)
const operations = computed(() => props.operations)

function opStatusLabel(status: ConflictOperation['status']): string {
  return copyFor(`prep.t24.conflict.op.${status}`)
}
</script>
<style scoped>
.conflict { margin-top: var(--ma-space-3); padding: var(--ma-space-3); border: 1px solid var(--ma-status-warning); border-radius: var(--ma-radius-card); background: var(--ma-surface-subtle); color: var(--ma-text-body); }
.conflict h2 { margin: 0 0 var(--ma-space-1); font-size: var(--ma-font-size-title); color: var(--ma-text-primary); }
.conflict p { margin: var(--ma-space-1) 0; line-height: var(--ma-typography-body); }
.conflict__versions, .conflict__hint { color: var(--ma-text-muted); }
.conflict__summary { color: var(--ma-text-primary); }
.conflict__fields { width: 100%; margin-top: var(--ma-space-2); border-collapse: collapse; }
.conflict__caption { text-align: left; color: var(--ma-text-muted); padding-bottom: var(--ma-space-1); }
.conflict__fields th, .conflict__fields td { padding: var(--ma-space-1) var(--ma-space-2); border: 1px solid var(--ma-surface-border); text-align: left; vertical-align: top; overflow-wrap: anywhere; }
.conflict__fields th { color: var(--ma-text-muted); font-weight: 500; }
.conflict__cell-target { font-family: var(--ma-font-family-mono); }
.conflict__cell-before { color: var(--ma-text-muted); text-decoration: line-through; }
.conflict__cell-after { color: var(--ma-text-primary); }
.conflict__ops { list-style: none; margin: var(--ma-space-2) 0 0; padding: 0; }
.conflict__ops li { padding: var(--ma-space-1) 0; }
.conflict__ops li[data-status='conflict'] { color: var(--ma-status-danger); }
.conflict__ops code { font-family: var(--ma-font-family-mono); color: var(--ma-text-muted); }
.conflict__actions { display: flex; gap: var(--ma-space-2); margin-top: var(--ma-space-2); flex-wrap: wrap; }
.conflict button { min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-text-body); cursor: pointer; }
.conflict__primary { border-color: var(--ma-action-primary); background: var(--ma-action-primary); color: var(--ma-color-white-000); }
.conflict button:disabled { cursor: not-allowed; opacity: .55; }
</style>
