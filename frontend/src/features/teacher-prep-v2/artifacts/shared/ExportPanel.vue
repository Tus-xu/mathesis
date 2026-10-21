<template>
  <section class="export" data-testid="t24-export-panel">
    <header>
      <h2>{{ copyFor('prep.t24.export.title') }}</h2>
      <span>{{ copyFor('prep.t24.export.server_job') }}</span>
    </header>

    <p v-if="!capabilities.length" class="export__empty">{{ copyFor('prep.t24.export.none') }}</p>
    <ul v-else>
      <li v-for="item in capabilities" :key="item.format" :data-format="item.format" :data-state="item.state">
        <div class="export__format">
          <span class="export__label">{{ item.label }}</span>
          <span class="export__state" :data-state="item.state">{{ stateLabel(item.state) }}</span>
          <!-- 服务端真实作业阶段（不是本地定时器伪造的进度） -->
          <span v-if="progressLabel(item)" class="export__progress" data-testid="t24-export-progress">{{ progressLabel(item) }}</span>
          <span v-if="item.jobId" class="export__job" data-testid="t24-export-job">{{ copyFormat('prep.t24.export.job_prefix', item.jobId) }}</span>
        </div>

        <div class="export__actions">
          <button
            v-if="item.state === 'available'"
            type="button"
            :data-testid="`t24-export-${item.format}`"
            @click="emit('request', item.format)"
          >
            {{ copyFor('prep.t24.export.start') }}
          </button>

          <button
            v-else-if="item.state === 'pending' || item.state === 'running'"
            type="button"
            class="export__quiet"
            :data-testid="`t24-export-cancel-${item.format}`"
            @click="emit('cancel', item.format)"
          >
            {{ copyFor('prep.t24.export.cancel') }}
          </button>

          <a
            v-else-if="item.state === 'ready' && item.downloadUrl"
            :href="item.downloadUrl"
            :download="item.fileName ?? undefined"
            :data-testid="`t24-export-download-${item.format}`"
          >
            {{ copyFor('prep.t24.export.download') }}
          </a>

          <!-- 短时链接过期：显式"重新获取"，不伪造一个看起来还能用的链接 -->
          <template v-else-if="item.state === 'expired'">
            <span class="export__expired" data-testid="t24-export-expired">{{ copyFor('prep.t24.export.expired') }}</span>
            <button
              type="button"
              class="export__quiet"
              :data-testid="`t24-export-refresh-${item.format}`"
              @click="emit('refresh-download', item.format)"
            >
              {{ copyFor('prep.t24.export.refresh') }}
            </button>
          </template>

          <button
            v-else-if="item.state === 'failed' || item.state === 'cancelled'"
            type="button"
            class="export__quiet"
            :data-testid="`t24-export-retry-${item.format}`"
            @click="emit('request', item.format)"
          >
            {{ copyFor('prep.t24.export.retry') }}
          </button>

          <span v-else class="export__disabled">{{ copyFor('prep.t24.export.disabled') }}</span>
        </div>

        <p v-if="item.error" class="export__error" :data-testid="`t24-export-error-${item.format}`">{{ item.error }}</p>
      </li>
    </ul>

    <p v-if="message" class="export__message" :data-state="state">{{ message }}</p>
  </section>
</template>
<script setup lang="ts">
import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'

/**
 * 导出面板只呈现**服务端事实**。
 *
 * - `state` 来自真实作业状态机（pending/running/done/failed/cancelled）+ 票据生命周期（expired）；
 * - `capability-disabled` 只在契约里确实没有该格式能力时出现，**不得**因为没有后端就长期降级；
 * - 进度文案取自服务端 `progress`，本组件没有任何定时器。
 */
export interface ExportCapability {
  format: string
  label: string
  state: 'available' | 'capability-disabled' | 'pending' | 'running' | 'ready' | 'done' | 'failed' | 'cancelled' | 'expired'
  downloadUrl?: string | null
  fileName?: string | null
  jobId?: string | null
  artifactId?: string | null
  error?: string | null
  progressStage?: string | null
  progressPageNo?: number | null
  progressPageCount?: number | null
}

withDefaults(defineProps<{ capabilities: ExportCapability[]; message?: string | null; state?: string }>(), {
  message: null,
  state: 'idle',
})

const emit = defineEmits<{
  (e: 'request', format: string): void
  (e: 'cancel', format: string): void
  (e: 'refresh-download', format: string): void
}>()

function stateLabel(state: ExportCapability['state']): string {
  return copyFor(`prep.t24.export.state.${state}`)
}

function progressLabel(item: ExportCapability): string | null {
  if (item.state !== 'running' && item.state !== 'pending') return null
  const parts: string[] = []
  if (item.progressStage) parts.push(item.progressStage)
  if (typeof item.progressPageNo === 'number' && typeof item.progressPageCount === 'number') {
    parts.push(`${item.progressPageNo}/${item.progressPageCount}`)
  }
  return parts.length ? parts.join(' · ') : null
}
</script>
<style scoped>
.export { padding: var(--ma-space-3); border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); background: var(--ma-surface-default); }
.export header { display: flex; align-items: baseline; justify-content: space-between; gap: var(--ma-space-2); }
.export h2 { margin: 0; font-size: var(--ma-font-size-title); }
.export header span, .export__disabled, .export__expired, .export__state { color: var(--ma-text-muted); }
.export ul { list-style: none; margin: var(--ma-space-2) 0 0; padding: 0; }
.export li { display: flex; align-items: center; gap: var(--ma-space-2); padding: var(--ma-space-2) 0; border-top: 1px solid var(--ma-surface-border); flex-wrap: wrap; }
.export__format { display: flex; align-items: baseline; gap: var(--ma-space-2); min-width: 0; flex: 1 1 220px; flex-wrap: wrap; }
.export__label { color: var(--ma-text-primary); }
.export__state[data-state='failed'] { color: var(--ma-status-danger); }
.export__state[data-state='expired'], .export__expired { color: var(--ma-status-warning); }
.export__job { font-family: var(--ma-font-family-mono); color: var(--ma-text-muted); }
.export__actions { display: flex; align-items: center; gap: var(--ma-space-2); margin-left: auto; }
.export button { min-height: 30px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-action-primary); cursor: pointer; }
.export button:disabled { cursor: not-allowed; opacity: .55; }
.export a { color: var(--ma-action-primary); }
.export__empty, .export__message, .export__progress { color: var(--ma-text-muted); line-height: var(--ma-typography-body); }
.export__error { flex: 1 1 100%; margin: 0; color: var(--ma-status-danger); line-height: var(--ma-typography-body); }
</style>
