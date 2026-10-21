<template>
  <article class="block" :class="{ 'block--selected': selected }" data-testid="t24-lesson-block">
    <header class="block__header">
      <h3>{{ title }}</h3>
      <span class="block__kind">{{ kindLabel }}</span>
    </header>
    <textarea
      v-model="draft"
      :readonly="readOnly"
      class="block__input"
      :aria-label="title"
      data-testid="t24-block-editor"
      @focus="emit('select')"
    />
    <footer>
      <span v-if="sourceLocator" class="block__source">{{ sourceLocator }}</span>
      <button
        type="button"
        :disabled="readOnly || draft === original"
        data-testid="t24-block-propose"
        @click="onPropose"
      >
        {{ copyFor('prep.t24.block.propose') }}
      </button>
    </footer>
  </article>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { PatchOperation } from '@/features/teacher-prep-v2/types/t24'

/** 契约 S2.4：lesson plan 的块级操作形如 `set_block {target:<block_id>, field, value}`。 */
const OP_SET_BLOCK = 'set_block'

const props = withDefaults(defineProps<{ blockId: string; title: string; kind?: string; content: string; sourceLocator?: string | null; selected?: boolean; readOnly?: boolean }>(), { kind: 'content', sourceLocator: null, selected: false, readOnly: false })
const emit = defineEmits<{
  (e: 'select'): void
  /** 兼容既有调用方的形状（保持 T24 上一轮契约不变）。 */
  (e: 'propose', payload: { blockId: string; before: string; after: string }): void
  /** T24 接线：带 `before` 的契约化 operations（服务端据此做 stale 校验）。 */
  (e: 'propose-operations', payload: { operations: PatchOperation[]; blockId: string }): void
}>()
const original = ref(props.content)
const draft = ref(props.content)
watch(() => props.content, (value) => { original.value = value; draft.value = value })
const kindLabel = computed(() => copyFor(`prep.t24.block.kind.${props.kind}`))

function onPropose() {
  emit('propose', { blockId: props.blockId, before: original.value, after: draft.value })
  emit('propose-operations', {
    blockId: props.blockId,
    operations: [{
      op: OP_SET_BLOCK,
      target: props.blockId,
      field: 'content',
      value: draft.value,
      // 服务端 stale 校验的依据：客户端提交时看到的旧值
      before: original.value,
    }],
  })
}
</script>
<style scoped>
.block { padding: var(--ma-space-3); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-default); }.block--selected { border-color: var(--ma-action-primary); }
.block__header, .block footer { display: flex; align-items: baseline; justify-content: space-between; gap: var(--ma-space-2); }.block h3 { margin: 0; font-size: var(--ma-font-size-title); color: var(--ma-text-primary); }.block__kind, .block__source { color: var(--ma-text-muted); font-size: var(--ma-font-size-body); }
.block__input { width: 100%; min-height: 96px; margin-top: var(--ma-space-2); box-sizing: border-box; padding: var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-md); background: var(--ma-surface-subtle); color: var(--ma-text-primary); font: inherit; line-height: var(--ma-typography-body); resize: vertical; }.block__input:focus { outline: 2px solid var(--ma-focus-ring); outline-offset: 1px; }
.block footer { margin-top: var(--ma-space-2); }.block button { min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-action-primary); color: var(--ma-color-white-000); cursor: pointer; }.block button:disabled { cursor: not-allowed; opacity: .55; }
</style>
