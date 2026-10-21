<template>
  <!--
    T22 · living artifact 区（Teacher 领域的产物 tab）。
    内容**全部**来自服务端 `artifact`（context / blueprint / diagnostic）。
    未返回的 tab 显示"服务端未提供"而不是编造占位内容。
  -->
  <div class="artifact" data-testid="prep-artifact">
    <div class="artifact__tabs" role="tablist" :aria-label="copyFor('prep.region.artifact')">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="artifact__tab"
        :class="{ 'is-active': t.key === active }"
        type="button"
        role="tab"
        :aria-selected="t.key === active"
        :aria-controls="`prep-artifact-panel-${t.key}`"
        :id="`prep-artifact-tab-${t.key}`"
        :data-testid="`prep-artifact-tab-${t.key}`"
        @click="$emit('update:active', t.key)"
      >
        {{ t.label }}
      </button>
    </div>

    <div
      v-for="t in tabs"
      v-show="t.key === active"
      :key="t.key"
      class="artifact__panel"
      role="tabpanel"
      :id="`prep-artifact-panel-${t.key}`"
      :aria-labelledby="`prep-artifact-tab-${t.key}`"
      tabindex="0"
    >
      <StructuredCard
        v-if="t.value"
        :component-type="t.componentType"
        :props="t.value"
      />
      <p v-else class="artifact__empty">{{ copyFor('prep.state.partial') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import StructuredCard from './cards/StructuredCard.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ArtifactSnapshot, ComponentType } from '@/features/teacher-prep-v2/types/workspace'

const props = defineProps<{
  artifact: ArtifactSnapshot
  active: string
}>()

defineEmits<{ 'update:active': [key: string] }>()

interface Tab {
  key: string
  label: string
  componentType: ComponentType
  value: Record<string, unknown> | null
}

/** tab 集合 = 服务端 artifact 的三个键；标签是稳定 UI 文案（i18n）。 */
const tabs = computedTabs()

function computedTabs(): Tab[] {
  const a = props.artifact
  return [
    { key: 'context', label: copyFor('prep.region.artifact') + ' · 上下文', componentType: 'context_card', value: a?.context ?? null },
    { key: 'blueprint', label: copyFor('prep.region.artifact') + ' · 方案', componentType: 'blueprint_card', value: a?.blueprint ?? null },
    { key: 'diagnostic', label: copyFor('prep.region.artifact') + ' · 诊断', componentType: 'diagnostic_card', value: a?.diagnostic ?? null },
  ]
}
</script>

<style scoped>
.artifact {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.artifact__tabs {
  display: flex;
  gap: var(--ma-space-1);
  border-bottom: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  padding: 0 var(--ma-space-2);
}

.artifact__tab {
  appearance: none;
  border: none;
  background: transparent;
  padding: var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  cursor: pointer;
  border-bottom: 2px solid transparent;
}

.artifact__tab.is-active {
  color: var(--ma-action-primary);
  border-bottom-color: var(--ma-action-primary);
  font-weight: 600;
}

.artifact__tab:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: -2px;
}

.artifact__panel {
  flex: 1 1 auto;
  padding: var(--ma-space-3);
  overflow: auto;
}

.artifact__panel:focus-visible {
  outline: var(--ma-focus-ring);
  outline-offset: -2px;
}

.artifact__empty {
  margin: 0;
  color: var(--ma-text-muted);
}
</style>
