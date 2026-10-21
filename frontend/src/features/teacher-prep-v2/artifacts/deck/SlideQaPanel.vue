<template>
  <section class="qa" data-testid="t24-slide-qa" aria-labelledby="qa-title">
    <header>
      <h2 id="qa-title">{{ copyFor('prep.t24.qa.title') }}</h2>
      <span>{{ report ? copyFor('prep.t24.qa.scope.deck') : copyFor('prep.t24.qa.scope.current') }}</span>
    </header>

    <!-- 服务端逐页 QA 报告（S2.5）：维度中文文案 + 失败页缺陷 + **单页局部重试** -->
    <template v-if="report">
      <p class="qa__summary" data-testid="t24-qa-summary">
        {{ copyFor('prep.t24.qa.summary_prefix') }}
        <span data-testid="t24-qa-p0">{{ report.p0Count }}</span> P0 ·
        <span data-testid="t24-qa-p1">{{ report.p1Count }}</span> P1 ·
        {{ copyFor('prep.t24.qa.failed_pages') }}
        <span data-testid="t24-qa-failed-count">{{ report.failedPages.length }}</span>
      </p>
      <p v-if="report.checks.length" class="qa__checks" data-testid="t24-qa-checks">
        {{ copyFor('prep.t24.qa.checks') }}{{ report.checks.map(checkLabel).join(' · ') }}
      </p>

      <p v-if="!failed.length" class="qa__empty" data-testid="t24-qa-all-pass">{{ copyFor('prep.t24.qa.all_pass') }}</p>
      <ul v-else class="qa__pages">
        <li
          v-for="page in failed"
          :key="page.pageNo"
          class="qa__page"
          :class="{ 'qa__page--current': page.pageNo === activePageNo }"
          :data-page="page.pageNo"
          :data-testid="`t24-qa-page-${page.pageNo}`"
        >
          <div class="qa__page-head">
            <button type="button" class="qa__locate" :data-testid="`t24-qa-locate-${page.pageNo}`" @click="emit('select-page', page.pageNo)">
              {{ copyFormat('prep.t24.qa.page_prefix', page.pageNo) }}
            </button>
            <span class="qa__attempts">{{ copyFormat('prep.t24.qa.attempts', page.attempts ?? 0) }}</span>
            <button
              type="button"
              class="qa__retry"
              :disabled="busy || page.pageNo !== activePageNo"
              :title="page.pageNo !== activePageNo ? copyFor('prep.t24.qa.retry_needs_current') : undefined"
              :data-testid="`t24-qa-retry-${page.pageNo}`"
              @click="emit('retry-page', page.pageNo)"
            >
              {{ copyFor('prep.t24.qa.retry_page') }}
            </button>
          </div>
          <ul class="qa__defects">
            <li v-for="(defect, index) in page.defects" :key="`${defect.kind}-${index}`" :data-severity="defect.severity" data-testid="t24-qa-defect">
              <span class="qa__mark" aria-hidden="true">{{ defect.severity === 'p0' ? '!' : '△' }}</span>
              <div>
                <strong>{{ kindLabel(defect.kind) }}</strong>
                <span class="qa__severity">{{ severityLabel(defect.severity) }}</span>
                <p>{{ defect.message }}</p>
                <span v-if="defect.shapeIds.length" class="qa__shapes">{{ defect.shapeIds.join(', ') }}</span>
              </div>
            </li>
          </ul>
        </li>
      </ul>
    </template>

    <!-- 兼容旧调用：只有扁平 issues 时保持原样 -->
    <template v-else>
      <p v-if="!issues.length" class="qa__empty">{{ copyFor('prep.t24.qa.empty') }}</p>
      <ul v-else class="qa__issues">
        <li v-for="(issue, index) in issues" :key="`${issue.code}-${index}`" :data-severity="issue.severity">
          <span class="qa__mark" aria-hidden="true">{{ mark(issue.severity) }}</span>
          <div>
            <strong>{{ severityLabel(issue.severity) }}</strong>
            <p>{{ issue.message }}</p>
            <button v-if="issue.action" type="button" @click="emit('fix', issue)">{{ issue.action }}</button>
          </div>
        </li>
      </ul>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'
import type { QaDefect, QaReport } from '@/features/teacher-prep-v2/types/t24'

export interface QaIssue {
  code: string
  severity: 'blocking' | 'warning' | 'info'
  message: string
  action?: string | null
}

/**
 * 逐页 QA 面板。
 *
 * - 失败页列表 + 缺陷维度中文文案（`prep.t24.qa.kind.*`）全部来自服务端报告；
 * - 单页重试只针对**当前页**（`POST /qa/retry`），并声明"其它页未变"由服务端背书；
 * - 本组件不发请求：只 emit，接线在 store。
 */
const props = withDefaults(defineProps<{
  issues?: QaIssue[]
  report?: QaReport | null
  /** 当前打开的页号（1-based，与契约 page_no 对齐）。 */
  activePageNo?: number | null
  busy?: boolean
}>(), {
  issues: () => [],
  report: null,
  activePageNo: null,
  busy: false,
})

const emit = defineEmits<{
  (e: 'fix', issue: QaIssue): void
  (e: 'retry-page', pageNo: number): void
  (e: 'select-page', pageNo: number): void
}>()

const issues = computed(() => props.issues)
const report = computed(() => props.report)
const failed = computed(() => (props.report?.pages ?? []).filter((page) => page.status === 'fail'))

function kindLabel(kind: string): string {
  return copyFor(`prep.t24.qa.kind.${kind}`)
}
function checkLabel(kind: string): string {
  return copyFor(`prep.t24.qa.check.${kind}`)
}
function severityLabel(severity: QaDefect['severity'] | QaIssue['severity']): string {
  if (severity === 'p0' || severity === 'blocking') return copyFor('prep.t24.qa.severity.blocking')
  if (severity === 'p1' || severity === 'warning') return copyFor('prep.t24.qa.severity.warning')
  return copyFor('prep.t24.qa.severity.info')
}
function mark(severity: QaIssue['severity']): string {
  return { blocking: '!', warning: '△', info: 'i' }[severity]
}
</script>
<style scoped>
.qa { padding: var(--ma-space-3); border-top: 1px solid var(--ma-surface-border); background: var(--ma-surface-default); }
.qa header { display: flex; justify-content: space-between; align-items: baseline; }
.qa h2 { margin: 0; font-size: var(--ma-font-size-title); }
.qa header span, .qa__empty, .qa__summary, .qa__checks, .qa__attempts, .qa__shapes { color: var(--ma-text-muted); }
.qa ul { list-style: none; margin: var(--ma-space-2) 0 0; padding: 0; }
.qa__issues li, .qa__defects li { display: flex; gap: var(--ma-space-2); padding: var(--ma-space-2) 0; border-top: 1px solid var(--ma-surface-border); }
.qa__pages { display: flex; flex-direction: column; gap: var(--ma-space-2); }
.qa__page { border: 1px solid var(--ma-surface-border); border-radius: var(--ma-radius-card); padding: var(--ma-space-2); }
.qa__page--current { border-color: var(--ma-action-primary); }
.qa__page-head { display: flex; align-items: baseline; gap: var(--ma-space-2); flex-wrap: wrap; }
.qa__mark { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border: 1px solid var(--ma-surface-border-strong); border-radius: var(--ma-radius-full); font-weight: 700; }
.qa__defects { margin-top: var(--ma-space-1); }
.qa strong { color: var(--ma-text-primary); }
.qa__severity { margin-left: var(--ma-space-1); color: var(--ma-status-warning); }
.qa p { margin: var(--ma-space-1) 0; color: var(--ma-text-body); line-height: var(--ma-typography-body); }
.qa__locate { padding: 0; border: 0; background: transparent; color: var(--ma-text-primary); cursor: pointer; text-decoration: underline; }
.qa__retry { margin-left: auto; min-height: 28px; padding: 0 var(--ma-space-3); border: 1px solid var(--ma-action-primary); border-radius: var(--ma-radius-btn); background: var(--ma-surface-default); color: var(--ma-action-primary); cursor: pointer; }
.qa__retry:disabled { cursor: not-allowed; opacity: .55; }
.qa__issues button { padding: 0; border: 0; background: transparent; color: var(--ma-action-primary); cursor: pointer; text-decoration: underline; }
.qa__defects li[data-severity='p0'] .qa__mark { border-color: var(--ma-status-danger); color: var(--ma-status-danger); }
</style>
