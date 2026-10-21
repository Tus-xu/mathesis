<template>
  <!--
    T22.2 · 沉浸式 Focus 外壳（**全局侧栏完全隐藏**，DESIGN-CORRECTION-R2 §1-1）。

    铁律：
    - 不渲染任何全局模块导航（今日/备课/课堂/评测/资源）；
      App Shell 侧栏在进入 TeachingSession 后**整条消失**，退出后才恢复。
    - 顶部只保留：`← 我的备课` + 课题 + 班级/时长/教材 + 轻量 [进展][产物][更多]。
    - 进展用"N 项已就绪 · M 项待确认"，点击后才展开抽屉 —— 不是 Stepper/Wizard。
  -->
  <div class="focus-shell" data-testid="prep-focus-shell">
    <header class="focus-shell__bar" data-testid="prep-focus-bar">
      <button type="button" class="focus-shell__back" data-testid="prep-back-to-prep" @click="emit('back')">
        <n-icon :size="15"><ArrowBackOutline /></n-icon>
        {{ copyFor('prep.focus.back') }}
      </button>

      <div class="focus-shell__ident">
        <h1 class="focus-shell__title" data-testid="prep-focus-title">{{ title }}</h1>
        <p v-if="meta" class="focus-shell__meta" data-testid="prep-focus-meta">{{ meta }}</p>
      </div>

      <span class="focus-shell__spacer" />

      <span
        class="focus-shell__connection"
        :class="`focus-shell__connection--${streamPhaseTone}`"
        :data-tone="streamPhaseTone"
        data-testid="prep-connection-tone"
        role="status"
      >
        <span class="focus-shell__connection-dot" aria-hidden="true" />
        {{ streamPhaseLabel }}
      </span>

      <button
        v-if="milestoneLabel && milestones.length"
        type="button"
        class="focus-shell__chip"
        data-testid="prep-milestone-chip"
        @click="emit('open-milestones')"
      >
        <n-icon :size="14"><ListOutline /></n-icon>
        {{ milestoneLabel }}
      </button>

      <button
        v-if="artifactCount > 0"
        type="button"
        class="focus-shell__chip"
        data-testid="prep-artifact-chip"
        @click="emit('open-artifacts')"
      >
        <n-icon :size="14"><DocumentsOutline /></n-icon>
        {{ copyWith('prep.focus.artifacts', artifactCount) }}
      </button>

      <button
        type="button"
        class="focus-shell__chip focus-shell__chip--icon"
        data-testid="prep-more"
        :aria-label="copyFor('prep.focus.more')"
      >
        <n-icon :size="15"><EllipsisHorizontalOutline /></n-icon>
      </button>
    </header>

    <main class="focus-shell__body">
      <slot />
    </main>

    <MilestoneDrawer
      v-if="milestonesOpen"
      :items="milestones"
      data-testid="prep-milestone-drawer"
      @close="emit('close-milestones')"
    />
  </div>
</template>

<script setup lang="ts">
import { ArrowBackOutline, DocumentsOutline, EllipsisHorizontalOutline, ListOutline } from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'
import MilestoneDrawer from './MilestoneDrawer.vue'
import { copyFor, copyWith } from '@/features/teacher-prep-v2/i18n/copy'
import type { Milestone } from '@/features/teacher-prep-v2/projection/milestones'
import type { StreamPhase } from '@/features/teacher-prep-v2/projection/types'
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    meta?: string | null
    milestoneLabel: string
    milestones: Milestone[]
    milestonesOpen: boolean
    artifactCount?: number
    streamPhase: StreamPhase
  }>(),
  { meta: null, artifactCount: 0 },
)

const emit = defineEmits<{
  (e: 'back'): void
  (e: 'open-milestones'): void
  (e: 'close-milestones'): void
  (e: 'open-artifacts'): void
}>()
void props

const streamPhaseTone = computed(() => {
  if (props.streamPhase === 'reconnecting' || props.streamPhase === 'stale') return 'warning'
  if (props.streamPhase === 'error') return 'danger'
  if (props.streamPhase === 'live') return 'live'
  return 'neutral'
})

const streamPhaseLabel = computed(() => {
  if (props.streamPhase === 'reconnecting') return copyFor('prep.stream.reconnecting')
  if (props.streamPhase === 'error') return copyFor('prep.stream.error')
  if (props.streamPhase === 'connecting') return copyFor('prep.stream.connecting')
  if (props.streamPhase === 'stale') return copyFor('prep.stream.stale')
  return copyFor('prep.stream.live')
})
</script>

<style scoped>
.focus-shell {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--ma-surface-page);
  /* WP-K 视觉修复：下面两层装饰性氛层（::before/::after 光斑）定位在 right/bottom 的负偏移上，
     在 1440px 视口实测把文档 scrollWidth 撑到 1560px → 整页出现横向滚动条。
     装饰不得影响布局，故在此裁掉溢出（滚动仍由 .focus-shell__body 承担）。 */
  overflow: hidden;
}

.focus-shell::before,
.focus-shell::after {
  position: absolute;
  z-index: -1;
  width: 420px;
  height: 320px;
  border-radius: var(--ma-radius-full);
  content: '';
  opacity: .08;
  pointer-events: none;
  filter: blur(28px);
}

.focus-shell::before {
  top: -140px;
  left: -120px;
  background: var(--ma-status-ai);
}

.focus-shell::after {
  right: -120px;
  bottom: -140px;
  background: var(--ma-status-accent);
}

.focus-shell__bar {
  display: flex;
  align-items: center;
  gap: var(--ma-space-3);
  padding: 8px var(--ma-space-4);
  border-bottom: 1px solid var(--ma-surface-border);
  background: var(--ma-surface-default);
  min-height: 56px;
}

.focus-shell__back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 var(--ma-space-2);
  border: 0;
  border-radius: var(--ma-radius-btn);
  background: transparent;
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
  flex: 0 0 auto;
}

.focus-shell__back:hover {
  background: var(--ma-surface-subtle);
  color: var(--ma-text-primary);
}

.focus-shell__ident {
  min-width: 0;
}

.focus-shell__title {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 600;
  color: var(--ma-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.focus-shell__meta {
  margin: 0;
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.focus-shell__spacer {
  flex: 1 1 auto;
}

.focus-shell__brand {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-action-primary-soft);
  border: 1px solid var(--ma-surface-border-strong);
  color: var(--ma-action-primary);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  flex: 0 0 auto;
}
.focus-shell__brand-mark {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  background: var(--ma-status-ai);
  color: var(--ma-color-white-000);
  font-size: 9px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.focus-shell__connection {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 var(--ma-space-2);
  border: 1px solid transparent;
  border-radius: var(--ma-radius-tag);
  background: transparent;
  color: var(--ma-text-muted);
  font-size: var(--ma-font-size-body);
  white-space: nowrap;
  flex: 0 0 auto;
}

.focus-shell__connection--warning,
.focus-shell__connection--danger {
  border-color: var(--ma-surface-border);
  background: var(--ma-surface-subtle);
  color: var(--ma-text-body);
}

.focus-shell__connection-dot {
  width: 7px;
  height: 7px;
  border-radius: var(--ma-radius-full);
  background: var(--ma-text-muted);
}

.focus-shell__connection--live .focus-shell__connection-dot {
  background: var(--ma-status-success);
}

.focus-shell__connection--warning .focus-shell__connection-dot {
  background: var(--ma-status-warning);
}

.focus-shell__connection--danger .focus-shell__connection-dot {
  background: var(--ma-status-danger);
}

.focus-shell__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 var(--ma-space-3);
  border: 1px solid var(--ma-surface-border);
  border-radius: var(--ma-radius-tag);
  background: var(--ma-surface-default);
  color: var(--ma-text-body);
  font-size: var(--ma-font-size-body);
  cursor: pointer;
  flex: 0 0 auto;
}

.focus-shell__chip:hover {
  border-color: var(--ma-action-primary);
  color: var(--ma-text-primary);
}

.focus-shell__chip--icon {
  width: 30px;
  padding: 0;
  justify-content: center;
}

.focus-shell__chip:focus-visible,
.focus-shell__back:focus-visible {
  outline: none;
  outline: 2px solid var(--ma-focus-ring);
  outline-offset: 1px;
}

.focus-shell__body {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
