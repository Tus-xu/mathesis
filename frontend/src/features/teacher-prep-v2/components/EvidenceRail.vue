<template>
  <!--
    T22 · Evidence rail（可收起）。
    `collapsed` 是 **client state**（UI 偏好，落 localStorage）；
    证据条目本身是 **server state**（来自 snapshot.evidence，可解析 SourceLocator）。
    收起时保留标题（landmark 仍在），不删除 DOM 语义。
  -->
  <section class="evidence" :aria-label="copyFor('prep.region.evidence')" data-testid="prep-evidence-rail">
    <header class="evidence__head">
      <h2 class="evidence__title">{{ copyFor('prep.region.evidence') }}</h2>
      <ActionButton
        variant="ghost"
        :test-id="collapsed ? 'prep-evidence-expand' : 'prep-evidence-collapse'"
        :aria-expanded="!collapsed"
        @click="$emit('toggle')"
      >
        {{ collapsed ? copyFor('prep.toolbar.rail.expand') : copyFor('prep.toolbar.rail.toggle') }}
      </ActionButton>
    </header>

    <div v-show="!collapsed" class="evidence__body">
      <ul v-if="items.length" class="evidence__list">
        <li v-for="e in items" :key="e.locatorRef" class="evidence__item">
          <SourceLocatorChip :locator-ref="e.locatorRef" :kind="e.kind" :title="e.title" />
          <p v-if="e.snippet" class="evidence__snippet">{{ e.snippet }}</p>
        </li>
      </ul>
      <p v-else class="evidence__empty">{{ copyFor('prep.evidence.empty') }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import ActionButton from './primitives/ActionButton.vue'
import SourceLocatorChip from './SourceLocatorChip.vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

interface EvidenceItem {
  locatorRef: string
  kind: string
  title: string
  snippet: string
}

const props = withDefaults(defineProps<{ evidence: Array<Record<string, unknown>>; collapsed?: boolean }>(), {
  collapsed: false,
})

defineEmits<{ toggle: [] }>()

/** 只映射服务端字段；缺 ref 的条目直接丢弃（不可解析的定位不是证据）。 */
const items = computed<EvidenceItem[]>(() =>
  props.evidence
    .map((e) => ({
      locatorRef: String(e?.ref ?? ''),
      kind: String(e?.kind ?? 'fact'),
      title: String(e?.title ?? ''),
      snippet: String(e?.snippet ?? ''),
    }))
    .filter((e) => e.locatorRef.length > 0),
)
</script>

<style scoped>
.evidence {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--ma-surface-default);
}

.evidence__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2);
  border-bottom: 1px solid var(--ma-surface-border);
}

.evidence__title {
  margin: 0;
  font-size: var(--ma-font-size-title);
  color: var(--ma-text-primary);
}

.evidence__body {
  flex: 1 1 auto;
  overflow: auto;
  padding: var(--ma-space-2);
}

.evidence__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-2);
}

.evidence__snippet {
  margin: var(--ma-space-1) 0 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-muted);
}

.evidence__empty {
  margin: 0;
  color: var(--ma-text-muted);
}
</style>
