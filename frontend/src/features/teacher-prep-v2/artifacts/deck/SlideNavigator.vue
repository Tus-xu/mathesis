<template>
  <nav class="navigator" data-testid="t24-slide-navigator" :aria-label="copyFor('prep.t24.aria.slides')">
    <ol>
      <li v-for="(slide, index) in slides" :key="slide.id">
        <button
          type="button"
          :aria-current="index === activeIndex ? 'page' : undefined"
          :class="{ active: index === activeIndex }"
          :data-testid="`t24-slide-${index + 1}`"
          :data-qa="qaOf(slide)"
          @click="emit('select', index)"
        >
          <span class="navigator__number">{{ index + 1 }}</span>
          <span class="navigator__title">{{ slide.title }}</span>
          <!-- 逐页 QA 状态标记：只有服务端给了状态才显示（unknown 也如实显示为待检） -->
          <span
            v-if="qaOf(slide) !== null"
            class="navigator__qa"
            :data-state="qaOf(slide)"
            :data-testid="`t24-slide-qa-${index + 1}`"
            :title="qaLabel(qaOf(slide))"
          >{{ qaMark(qaOf(slide)) }}</span>
        </button>
      </li>
    </ol>
  </nav>
</template>
<script setup lang="ts">
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'

export interface SlideSummary {
  id: string
  title: string
  /** 服务端逐页 QA 状态；未提供时为 undefined（不显示标记，不猜）。 */
  qaStatus?: 'pass' | 'fail' | 'unknown' | null
}

defineProps<{ slides: SlideSummary[]; activeIndex: number }>()
const emit = defineEmits<{ (e: 'select', index: number): void }>()

function qaOf(slide: SlideSummary): 'pass' | 'fail' | 'unknown' | null {
  return slide.qaStatus ?? null
}
function qaMark(state: 'pass' | 'fail' | 'unknown' | null): string {
  return state === 'pass' ? '✓' : state === 'fail' ? '!' : '?'
}
function qaLabel(state: 'pass' | 'fail' | 'unknown' | null): string {
  return copyFor(`prep.t24.qa.page_status.${state ?? 'unknown'}`)
}
</script>
<style scoped>
.navigator { min-width: 190px; padding: var(--ma-space-2); border-right: 1px solid var(--ma-surface-border); background: var(--ma-surface-page); overflow-y: auto; }
.navigator ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--ma-space-2); }
.navigator button { width: 100%; display: grid; grid-template-columns: 28px 1fr auto; gap: var(--ma-space-1); align-items: center; padding: var(--ma-space-2); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-md); background: var(--ma-surface-default); color: var(--ma-text-body); text-align: left; cursor: pointer; }
.navigator button.active { border-color: var(--ma-action-primary); box-shadow: var(--ma-elevation-sm); }
.navigator__number { color: var(--ma-text-muted); }
.navigator__title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.navigator__qa { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border: 1px solid var(--ma-surface-border-strong); border-radius: var(--ma-radius-full); font-size: var(--ma-font-size-body); }
.navigator__qa[data-state='fail'] { border-color: var(--ma-status-danger); color: var(--ma-status-danger); }
.navigator__qa[data-state='pass'] { border-color: var(--ma-status-success); color: var(--ma-status-success); }
.navigator__qa[data-state='unknown'] { color: var(--ma-text-muted); }
.navigator button:focus-visible { outline: 2px solid var(--ma-focus-ring); outline-offset: 1px; }
</style>
