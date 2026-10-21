<template>
  <!--
    T22.1 · 「本会话产物」dock（UX-CONTRACT C）：小型、可关闭、靠近输入框。
    与 Artifact Canvas 的区别：dock 只是**入口**，不是画布；
    没有 artifact 时本组件不会被挂载（由 `showDock` 控制）。
  -->
  <aside class="dock" data-testid="prep-artifact-dock">
    <span class="dock__label">{{ copyFor('prep.dock.label') }}</span>

    <button
      v-for="(item, index) in artifacts"
      :key="item.artifact_type + item.artifact_id"
      type="button"
      class="dock__item"
      :data-testid="index === 0 ? 'prep-dock-open' : `prep-dock-open-${item.artifact_type}`"
      @click="emit('open', item)"
    >
      <n-icon :size="14"><DocumentTextOutline /></n-icon>
      <span class="dock__name">{{ item.title || typeName(item) }}</span>
      <span class="dock__rev">{{ copyFor('prep.dock.current') }}</span>
    </button>

    <span class="dock__note">{{ copyFor('prep.dock.capability_note') }}</span>

    <button
      type="button"
      class="dock__close"
      data-testid="prep-dock-dismiss"
      :aria-label="copyFor('prep.dock.close')"
      @click="emit('dismiss')"
    >
      <n-icon :size="14"><CloseOutline /></n-icon>
    </button>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CloseOutline, DocumentTextOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'

const props = defineProps<{ artifact: ActiveArtifact; artifacts?: ActiveArtifact[] }>()
const emit = defineEmits<{ (e: 'open', artifact: ActiveArtifact): void; (e: 'dismiss'): void }>()

const artifacts = computed(() => props.artifacts?.length ? props.artifacts : [props.artifact])

const typeName = (artifact: ActiveArtifact) =>
  artifact.artifact_type === 'teaching_blueprint'
    ? copyFor('prep.artifact.type.teaching_blueprint')
    : artifact.artifact_type === 'diagnostic_task'
      ? copyFor('prep.artifact.type.diagnostic_task')
      : artifact.artifact_type === 'lesson_plan_spec'
        ? copyFor('prep.artifact.type.lesson_plan_spec')
        : copyFor('prep.artifact.type.teaching_deck_spec')

</script>

<style scoped>
.dock {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin: 0 var(--ma-space-4) var(--ma-space-2);
  padding: var(--ma-space-1) var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  background: var(--ma-surface-default);
  font-size: var(--ma-font-size-body);
}

.dock__label {
  color: var(--ma-text-muted);
}

.dock__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-tag);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
  cursor: pointer;
}

.dock__item--disabled {
  cursor: not-allowed;
  color: var(--ma-text-muted);
  background: var(--ma-surface-default);
  border-style: dashed;
}

.dock__name {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dock__rev {
  color: var(--ma-text-muted);
}

.dock__note {
  max-width: 280px;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
}

.dock__close {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: var(--ma-radius-sm);
  background: transparent;
  color: var(--ma-text-muted);
  cursor: pointer;
}

.dock__close:hover {
  background: var(--ma-surface-subtle);
  color: var(--ma-text-primary);
}

.dock__item:focus-visible,
.dock__close:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}
</style>
