<template>
  <!--
    T22.3-C · ARTIFACT_READY 卡（UX-CONTRACT C）。
    只在**真实 artifact 存在**时由 MessageItem 挂载；无 artifact 时 DOM 中不存在。
    全部字段来自服务端投影，**不编造** QA / 时间 / revision。
  -->
  <section class="ready" data-testid="prep-artifact-ready-card">
    <div class="ready__head">
      <span class="ready__badge">{{ copyFor('prep.artifact.ready_badge') }}</span>
      <h4 class="ready__title">{{ artifact.title || artifactTypeName }}</h4>
    </div>

    <dl class="ready__meta">
      <div class="ready__meta-item">
        <dt>{{ copyFor('prep.artifact.label.version') }}</dt>
        <dd>{{ copyFor('prep.artifact.version_prefix') }} {{ artifact.revision }} {{ copyFor('prep.artifact.version_suffix') }}</dd>
      </div>
      <div v-if="artifact.updated_at" class="ready__meta-item">
        <dt>{{ copyFor('prep.artifact.label.updated') }}</dt>
        <dd>{{ artifact.updated_at }}</dd>
      </div>
      <div v-if="qaSummary" class="ready__meta-item">
        <dt>{{ copyFor('prep.artifact.label.qa') }}</dt>
        <dd>{{ qaSummary }}</dd>
      </div>
    </dl>

    <footer class="ready__actions">
      <button
        type="button"
        class="ready__primary"
        data-testid="prep-open-focus"
        @click="emit('open')"
      >
        <n-icon :size="15"><CreateOutline /></n-icon>
        {{ copyFor('prep.artifact.open_focus') }}
      </button>
      <button
        type="button"
        class="ready__ghost"
        data-testid="prep-open-qa"
        @click="emit('open-qa')"
      >
        {{ copyFor('prep.artifact.open_qa') }}
      </button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CreateOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'

const props = defineProps<{ artifact: ActiveArtifact }>()
const emit = defineEmits<{ (e: 'open'): void; (e: 'open-qa'): void }>()

const artifactTypeName = computed(() => {
  switch (props.artifact.artifact_type) {
    case 'teaching_blueprint':
      return copyFor('prep.artifact.type.teaching_blueprint')
    case 'diagnostic_task':
      return copyFor('prep.artifact.type.diagnostic_task')
    case 'lesson_plan_spec':
      return copyFor('prep.artifact.type.lesson_plan_spec')
    case 'teaching_deck_spec':
      return copyFor('prep.artifact.type.teaching_deck_spec')
    default:
      return copyFor('prep.artifact.type.fallback')
  }
})

/** QA 摘要：只有服务端给了才显示（不伪造"全部通过"）。 */
const qaSummary = computed(() => null)
</script>

<style scoped>
.ready {
  margin-top: var(--ma-space-3);
  padding: var(--ma-space-3);
  border: 1px solid var(--ma-surface-border-strong);
  border-radius: var(--ma-radius-card);
  background: var(--ma-surface-default);
  box-shadow: var(--ma-elevation-sm);
}

.ready__head {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  margin-bottom: var(--ma-space-2);
}

.ready__badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 var(--ma-space-2);
  border-radius: var(--ma-radius-tag);
  background: var(--ma-action-primary-soft);
  color: var(--ma-action-primary);
  font-size: var(--ma-font-size-body);
}

.ready__title {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ready__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ma-space-1) var(--ma-space-4);
  margin: 0 0 var(--ma-space-3);
  font-size: var(--ma-font-size-body);
}

.ready__meta-item {
  display: flex;
  gap: 6px;
}

.ready__meta-item dt {
  color: var(--ma-text-body);
}

.ready__meta-item dd {
  margin: 0;
  color: var(--ma-text-primary);
}

.ready__actions {
  display: flex;
  gap: var(--ma-space-2);
}

.ready__primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 var(--ma-space-3);
  border: 0;
  border-radius: var(--ma-radius-btn);
  background: var(--ma-action-primary);
  color: var(--ma-color-white-000);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.ready__ghost {
  height: 34px;
  padding: 0 var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-btn);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
}

.ready__primary:focus-visible,
.ready__ghost:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}
</style>
