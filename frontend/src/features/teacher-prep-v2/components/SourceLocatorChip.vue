<template>
  <!--
    T22 · SourceLocatorChip：教材来源定位。
    数据来自服务端 `evidence[]`（`textbook_v2/locator/v1/...` 可解析 ref）。
    这里只做**展示**，不解析、不重造页码、不拼回 PDF。
  -->
  <span class="src-chip" :title="locatorRef" :data-testid="testId">
    <span class="src-chip__kind" aria-hidden="true">{{ kindMark }}</span>
    <span class="src-chip__title">{{ title || locatorRef }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

const props = withDefaults(
  defineProps<{
    /** 服务端下发的 SourceLocator ref（唯一权威；前端不得自行拼接）。 */
    locatorRef: string
    kind?: string
    title?: string
    testId?: string
  }>(),
  { kind: 'fact', title: '', testId: undefined },
)

/** 种类标记沿用服务端 bucket 名映射；未知种类回退到通用"来源定位"文案。 */
const KIND_MARK: Record<string, string> = {
  objectives: '目标',
  prior_knowledge: '旧知',
  concepts: '概念',
  theorems: '定理',
  examples: '例题',
  inquiries: '探究',
  design_intent: '意图',
  key_difficult_points: '重难点',
  blueprint: '方案',
  fact: '事实',
}

const kindMark = computed(() => KIND_MARK[props.kind] ?? copyFor('prep.evidence.ref'))
</script>

<style scoped>
.src-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--ma-space-1);
  max-width: 100%;
  padding: 0 var(--ma-space-2);
  border-radius: var(--ma-radius-tag);
  border: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  line-height: 1.9;
}

.src-chip__kind {
  color: var(--ma-text-primary);
  font-weight: 600;
}

.src-chip__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
