<template>
  <section class="rw-log" aria-label="编译日志">
    <div class="rw-log__tabs" role="tablist">
      <button
        type="button"
        role="tab"
        class="rw-log__tab"
        :class="{ 'rw-log__tab--active': tab === 'all' }"
        :aria-selected="tab === 'all'"
        @click="tab = 'all'"
      >
        编译日志
        <span class="rw-log__badge rw-log__badge--primary" :class="{ 'rw-log__badge--zero': !diagTotal }">{{ diagTotal }}</span>
      </button>
      <button
        type="button"
        role="tab"
        class="rw-log__tab"
        :class="{ 'rw-log__tab--active': tab === 'error' }"
        :aria-selected="tab === 'error'"
        @click="tab = 'error'"
      >
        错误
        <span
          class="rw-log__badge"
          :class="diagCounts.error ? 'rw-log__badge--danger' : 'rw-log__badge--zero'"
        >{{ diagCounts.error }}</span>
      </button>
      <button
        type="button"
        role="tab"
        class="rw-log__tab"
        :class="{ 'rw-log__tab--active': tab === 'warning' }"
        :aria-selected="tab === 'warning'"
        @click="tab = 'warning'"
      >
        警告
        <span
          class="rw-log__badge"
          :class="diagCounts.warning ? 'rw-log__badge--warning' : 'rw-log__badge--zero'"
        >{{ diagCounts.warning }}</span>
      </button>
      <button
        type="button"
        role="tab"
        class="rw-log__tab"
        :class="{ 'rw-log__tab--active': tab === 'info' }"
        :aria-selected="tab === 'info'"
        @click="tab = 'info'"
      >
        信息
        <span
          class="rw-log__badge"
          :class="diagCounts.info ? 'rw-log__badge--info' : 'rw-log__badge--zero'"
        >{{ diagCounts.info }}</span>
      </button>

      <span class="rw-log__spacer" />
      <button type="button" class="rw-log__collapse" :aria-label="expanded ? '收起日志' : '展开日志'" @click="emit('toggle')">
        <ChevronDownOutline :class="{ 'rw-log__collapse--rot': !expanded }" aria-hidden="true" />
      </button>
      <button type="button" class="rw-log__clear" @click="emit('clear')">清空日志</button>
    </div>

    <div v-if="expanded" class="rw-log__body" role="log" aria-live="polite">
      <p v-if="!visibleLines.length" class="rw-log__empty">
        {{ running ? '正在编译…' : '尚无编译记录。点击顶栏「重新编译」生成 PDF。' }}
      </p>
      <button
        v-for="(ln, i) in visibleLines"
        :key="i"
        type="button"
        class="rw-log__line"
        :class="[`rw-log__line--${ln.level}`, { 'rw-log__line--loc': ln.clickable }]"
        :disabled="!ln.clickable"
        @click="onLineClick(ln)"
      >
        <span class="rw-log__ts">{{ ln.ts }}</span>
        <span class="rw-log__text">{{ ln.text }}</span>
        <span v-if="ln.clickable && ln.file" class="rw-log__loc">{{ ln.file }}:{{ ln.line }}</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * WRITE 底部编译日志（VISUAL-SPEC §5.4 / 宪法 §24）。
 *
 * - Tab 头：`编译日志 [n]`（选中蓝下划线）+ `错误 [n]` + `警告 [n]`(橙药丸) + `信息 [n]`(蓝药丸) + 右侧 `清空日志`；
 * - 日志行等宽 12px，`时间戳` 用 `--rw-text-3`；warning 橙 / error 红 / 成功绿；
 * - **点击错误（或任何带 file+line 的行）→ `emit('locate', {file,line})` 由 Editor 定位**；
 * - 头部 / 结尾的「Compiling…/Output written…/✅ 编译完成」三行由**真实 run 数据**
 *   （entry/engine/page_count/pdf_size_bytes/duration_ms）派生，不含任何编造数值。
 */
import { computed, ref } from 'vue'
import { ChevronDownOutline } from '@vicons/ionicons5'
import type { CompileLogLine, CompileRun, CompileStatus } from '../api/types'
import { isCompileSuccess } from '../api/types'

const props = withDefaults(
  defineProps<{
    lines?: CompileLogLine[]
    run?: CompileRun | null
    status?: CompileStatus | 'idle'
    expanded?: boolean
    entry?: string
    engineLabel?: string
  }>(),
  {
    lines: () => [],
    run: null,
    status: 'idle',
    expanded: true,
    entry: 'main.tex',
    engineLabel: 'XeLaTeX',
  },
)

const emit = defineEmits<{
  (e: 'locate', payload: { file: string; line: number }): void
  (e: 'clear'): void
  (e: 'toggle'): void
}>()

type Tab = 'all' | 'error' | 'warning' | 'info'
const tab = ref<Tab>('all')

interface DisplayLine extends CompileLogLine {
  clickable: boolean
}

const running = computed(() => props.status === 'running' || props.status === 'queued')

/** ISO → `HH:MM:SS`（本地时区）。 */
function fmtTs(ts: string): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function tsFrom(ts: string): string {
  return fmtTs(ts) || fmtTs(new Date().toISOString())
}

/** 由真实 run 派生「编译汇总」三行（数值全部来自后端）。 */
const lines = computed<DisplayLine[]>(() => {
  const run = props.run
  const out: DisplayLine[] = []
  const baseTs = run?.log_lines?.[0]?.ts ?? ''

  if (run || running.value) {
    out.push({
      ts: tsFrom(baseTs),
      level: 'info',
      text: `Compiling ${run?.entry || props.entry} (${props.engineLabel})...`,
      clickable: false,
    })
  }

  for (const ln of props.lines ?? []) {
    // 后端只给 "Output written (N pages)"（无文件名/体积）；改用下方由 run 派生的
    // 更完整一行（含 main.pdf / 页数 / MB），故此处过滤后端同名行，避免重复。
    if (/^output written/i.test((ln.text ?? '').trim())) continue
    out.push({ ...ln, ts: fmtTs(ln.ts), clickable: Boolean(ln.file && ln.line) })
  }

  if (run && isCompileSuccess(run.status)) {
    const pages = run.page_count ?? 0
    const mb = run.pdf_size_bytes ? (run.pdf_size_bytes / 1024 / 1024).toFixed(1) : '0.0'
    out.push({
      ts: tsFrom(run.log_lines?.[run.log_lines.length - 1]?.ts ?? ''),
      level: 'info',
      text: `Output written on main.pdf (${pages} pages, ${mb} MB).`,
      clickable: false,
    })
    const secs = run.duration_ms ? (run.duration_ms / 1000).toFixed(1) : '0.0'
    out.push({
      ts: tsFrom(''),
      level: 'success',
      text: `✅ 编译完成，用时 ${secs} 秒。`,
      clickable: false,
    })
  } else if (run && (run.status === 'failed' || run.status === 'timeout')) {
    out.push({
      ts: tsFrom(''),
      level: 'error',
      text: run.status === 'timeout' ? '⏱ 编译超时。' : `❌ 编译失败。${run.error_message || '请检查日志中的错误行。'}`,
      clickable: false,
    })
  }

  return out
})

/**
 * Tab 计数来自**后端真实诊断行**（`props.lines`），不含本地合成的
 * `Compiling… / Output written… / ✅ 编译完成` 状态行 —— 与黄金图一致
 * （黄金图 `编译日志 3 = 错误 0 + 警告 2 + 信息 1`，而正文有 5 行）。
 */
const diagCounts = computed(() => {
  let error = 0
  let warning = 0
  let info = 0
  for (const ln of props.lines ?? []) {
    if (ln.level === 'error') error += 1
    else if (ln.level === 'warning') warning += 1
    else info += 1
  }
  return { error, warning, info }
})
const diagTotal = computed(() => diagCounts.value.error + diagCounts.value.warning + diagCounts.value.info)

const visibleLines = computed(() => {
  if (tab.value === 'all') return lines.value
  return lines.value.filter((l) => l.level === tab.value)
})

function onLineClick(ln: DisplayLine) {
  if (ln.clickable && ln.file && ln.line) emit('locate', { file: ln.file, line: ln.line })
}
</script>

<style scoped>
/*
 * 编译日志卡（黄金图 `WRITE-GOLDEN` 实测）：
 *   白卡 y783~941，上圆角 10 + 1px 描边，左右各内缩 ≈14px（卡左缘 x≈321，中栏原点 x=306）。
 *   Tab 行 h≈35：`编译日志 [3]`（**主蓝文字 + 主蓝下划线**）+ `错误 [0]` + `警告 [2]`(琥珀药丸) +
 *   `信息 [1]`(主蓝药丸) + 右侧 `清空日志`。
 *   日志行：等宽 12px，行距 ≈17.5px（黄金图相邻行基线差实测 17~18px），时间戳 `--rw-text-3`。
 */
.rw-log {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  margin: 0 14px;
  overflow: hidden;
  background: var(--rw-surface);
  border: var(--rw-border-w) solid var(--rw-border);
  border-bottom: none;
  border-radius: var(--rw-radius-lg) var(--rw-radius-lg) 0 0;
}

/* Tab 头 */
.rw-log__tabs {
  display: flex;
  align-items: center;
  gap: 14px;
  height: 35px;
  padding: 0 10px 0 14px;
  flex: 0 0 auto;
  border-bottom: var(--rw-border-w) solid var(--rw-border);
}
.rw-log__tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 35px;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--rw-text);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-body);
  cursor: pointer;
  box-shadow: inset 0 -2px 0 transparent;
}
.rw-log__tab--active {
  color: var(--rw-primary);
  font-weight: var(--rw-fw-semibold);
  box-shadow: inset 0 -2px 0 var(--rw-primary);
}
.rw-log__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  font-size: var(--rw-fs-xs);
  font-weight: var(--rw-fw-semibold);
  line-height: 1;
}
.rw-log__badge--neutral {
  background: var(--rw-inset);
  color: var(--rw-text-2);
}
/* 非零计数：实心药丸 + 白字（黄金图 `编译日志 ③ / 警告 ② / 信息 ①` 均为实心） */
.rw-log__badge--primary,
.rw-log__badge--info {
  background: var(--rw-primary);
  color: var(--rw-surface);
}
.rw-log__badge--warning {
  background: var(--rw-warning);
  color: var(--rw-surface);
}
.rw-log__badge--danger {
  background: var(--rw-danger);
  color: var(--rw-surface);
}
/* 计数为 0：黄金图为纯文本（无药丸底），避免「橙色 0」的视觉噪声。 */
.rw-log__badge--zero {
  background: transparent;
  color: var(--rw-text-3);
  padding: 0 2px;
}
.rw-log__spacer {
  flex: 1 1 auto;
}
.rw-log__collapse {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--rw-radius-sm);
  background: transparent;
  color: var(--rw-text-2);
  cursor: pointer;
}
.rw-log__collapse:hover {
  background: var(--rw-surface-2);
}
.rw-log__collapse :deep(svg) {
  width: 15px;
  height: 15px;
  transition: transform .14s ease;
}
.rw-log__collapse--rot :deep(svg) {
  transform: rotate(180deg);
}
/* 清空日志：黄金图为浅底圆角按钮 */
.rw-log__clear {
  height: 26px;
  padding: 0 12px;
  border: none;
  border-radius: var(--rw-radius-md);
  background: var(--rw-inset);
  color: var(--rw-text-2);
  font-family: var(--rw-font-sans);
  font-size: var(--rw-fs-sm);
  cursor: pointer;
}
.rw-log__clear:hover {
  background: var(--rw-primary-soft);
  color: var(--rw-primary);
}

/* 日志体 */
.rw-log__body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 8px 0 0;
}
.rw-log__empty {
  margin: 0;
  padding: 6px 14px;
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-log);
  color: var(--rw-text-3);
}
.rw-log__line {
  display: flex;
  align-items: baseline;
  gap: 12px;
  width: 100%;
  padding: 0 14px;
  border: none;
  background: transparent;
  text-align: left;
  font-family: var(--rw-font-mono);
  font-size: var(--rw-fs-log);
  line-height: 1.46;
  color: var(--rw-text);
}
.rw-log__line--loc {
  cursor: pointer;
}
.rw-log__line--loc:hover {
  background: var(--rw-surface-2);
}
.rw-log__line:disabled {
  cursor: default;
}
.rw-log__ts {
  flex: 0 0 auto;
  color: var(--rw-text-3);
}
.rw-log__text {
  flex: 1 1 auto;
  min-width: 0;
  word-break: break-word;
}
.rw-log__loc {
  flex: 0 0 auto;
  color: var(--rw-primary);
  font-size: var(--rw-fs-xs);
}
.rw-log__line--warning .rw-log__text {
  color: var(--rw-warning);
}
.rw-log__line--error .rw-log__text {
  color: var(--rw-danger);
}
.rw-log__line--success .rw-log__text {
  color: var(--rw-success);
}
</style>
