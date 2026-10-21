<template>
  <!--
    T22.3-C · 备课进展抽屉（Session State Inspector，DESIGN-CORRECTION-R2 §1-2）。

    铁律：
    - **不是 Stepper**：不编号、不强制顺序、不画流程条；每一项都能直接点（非线性可跳）。
    - 只列"本节课已启用"的里程碑；可选未启用项不进分母。
    - 数据全部来自服务端事实，没有就说"尚未开始"，不造假。
  -->
  <n-drawer :show="true" :width="380" placement="right" :auto-focus="true" @update:show="onClose">
    <n-drawer-content :title="copyFor('prep.focus.milestone')" closable @close="onClose">
      <p class="msd__hint">{{ copyFor('prep.focus.milestone.hint') }}</p>

      <ul class="msd__list" data-testid="prep-milestone-list">
        <li v-for="m in items" :key="m.key" class="msd__item" :data-state="m.state" :data-testid="`prep-milestone-${m.key}`">
          <span class="msd__dot" aria-hidden="true" />
          <div class="msd__body">
            <div class="msd__row">
              <span class="msd__label">{{ m.label }}</span>
              <span class="msd__state">{{ stateText(m.state) }}</span>
            </div>
            <p v-if="m.detail" class="msd__detail">{{ m.detail }}</p>
            <p v-else class="msd__detail msd__detail--empty">{{ copyFor('prep.focus.milestone.empty') }}</p>
          </div>
        </li>
      </ul>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup lang="ts">
import { NDrawer, NDrawerContent } from 'naive-ui'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import type { Milestone, MilestoneState } from '@/features/teacher-prep-v2/projection/milestones'

const props = defineProps<{ items: Milestone[] }>()
const emit = defineEmits<{ (e: 'close'): void }>()

function stateText(s: MilestoneState): string {
  switch (s) {
    case 'ready':
      return copyFor('prep.focus.state.ready')
    case 'confirming':
      return copyFor('prep.focus.state.confirming')
    case 'generating':
      return copyFor('prep.focus.state.generating')
    default:
      return copyFor('prep.focus.state.idle')
  }
}

function onClose() {
  emit('close')
}
void props
</script>

<style scoped>
.msd__hint {
  margin: 0 0 var(--ma-space-3);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.msd__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-2);
}

.msd__item {
  display: flex;
  gap: var(--ma-space-2);
  padding: var(--ma-space-2);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-md);
  background: var(--ma-surface-default);
}

.msd__dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-text-muted);
}

.msd__item[data-state='ready'] .msd__dot {
  background: var(--ma-status-success);
}

.msd__item[data-state='confirming'] .msd__dot {
  background: var(--ma-status-warning);
}

.msd__item[data-state='generating'] .msd__dot {
  background: var(--ma-status-accent);
}

.msd__body {
  min-width: 0;
}

.msd__row {
  display: flex;
  align-items: baseline;
  gap: var(--ma-space-2);
}

.msd__label {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-primary);
}

.msd__state {
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.msd__detail {
  margin: 2px 0 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
}

.msd__detail--empty {
  color: var(--ma-text-body);
}
</style>
