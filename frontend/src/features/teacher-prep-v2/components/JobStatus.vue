<template>
  <!--
    T22 · 长任务状态区。
    数据来自服务端 `job_summary`（DB 权威）。**不显示百分比进度条** ——
    后端不提供进度，前端就不许编；只能用「状态 + 尝试次数」表达。

    2026-09-14：本组件曾直接渲染 `job.job_type`（内部码 `conversation.run`）与
    `job.last_error`（真实内容形如 `IntegrityError('(sqlalchemy.dialects.postgresql.asyncpg…')`），
    教师看到的是开发者异常栈。按 .impeccable.md「内部标识符一律不得出现在用户可见文本里」
    改为中文映射 + 可行动提示；未收录的码兜底为通用文案，**绝不回显原码**。
  -->
  <div class="job-status" data-testid="prep-job-status">
    <div v-if="!jobs.length && !running" class="job-status__idle">
      <StatusPill tone="neutral" :label="copyFor('prep.state.ready')" />
    </div>

    <ul v-else class="job-status__list">
      <li v-for="job in jobs" :key="job.job_id" class="job-status__item">
        <StatusPill :tone="toneOf(job.status)" :label="jobStatusLabel(job.status)" />
        <span class="job-status__meta">
          {{ jobTypeLabel(job.job_type) }}
          <template v-if="job.max_attempts > 1">· {{ copyFormat('prep.job.attempt', job.attempt, job.max_attempts) }}</template>
        </span>
        <span v-if="job.last_error" class="job-status__error">
          {{ copyFormat('prep.job.failed_hint', shortRef(job.job_id)) }}
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import StatusPill from './primitives/StatusPill.vue'
import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'
import { jobStatusLabel, jobTypeLabel } from '@/features/teacher-prep-v2/i18n/labels'
import type { JobSummaryView } from '@/features/teacher-prep-v2/types/workspace'

defineProps<{ jobs: JobSummaryView[]; running?: boolean }>()

type Tone = 'neutral' | 'running' | 'partial' | 'success' | 'warning' | 'danger'

/** 状态 → 呈现基调：PENDING/RUNNING 绝不为 success（任务书第 7 条）。 */
function toneOf(status: string): Tone {
  switch (status) {
    case 'SUCCEEDED':
      return 'success'
    case 'FAILED':
      return 'danger'
    case 'CANCELLED':
      return 'neutral'
    case 'BASIC_MODE':
      return 'warning'
    default:
      return 'running' // PENDING / CLAIMED / RUNNING / 未知
  }
}

/** 排查用的短编号（UUID 前 8 位），非技术术语，可直接报给支持。 */
function shortRef(id: string): string {
  return id ? id.slice(0, 8) : ''
}
</script>

<style scoped>
.job-status__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ma-space-1);
}

.job-status__item {
  display: flex;
  align-items: center;
  gap: var(--ma-space-2);
  font-size: var(--ma-font-size-body);
  color: var(--ma-text-body);
  flex-wrap: wrap;
}

.job-status__meta {
  color: var(--ma-text-muted);
}

.job-status__error {
  color: var(--ma-status-danger);
}
</style>
