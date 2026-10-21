/**
 * Research Agent 任务状态机（FRONTEND-ARCH §1「agent/useAgentTask.ts」）。
 *
 * 职责：
 *  1. **统一任务生命周期**：`POST /agent/tasks`（202）→ 轮询 `GET /agent/tasks/{id}`
 *     → `queued/running/completed/failed/cancelled`（宪法 §54）；长任务关闭 Accordion
 *     后不消失（状态在**模块级单例**里，与组件挂载解耦）。
 *  2. **预设元数据**：READ / WRITE 能力顺序、图标、色调、activity 文案（宪法 §28/§55）。
 *  3. **置信状态中文映射**：`verified/supported/likely/needs_conditions/insufficient/
 *     conflict/failed` → `已验证/证据充分/基本成立/需要补充条件/证据不足/存在冲突`（宪法 §35）。
 *
 * 纪律：
 *  - activity 只显示有意义的过程文案，**禁止 chain-of-thought / 模型参数 / Agent 模式**（宪法 §27/§55）；
 *  - 失败一律经 `toRvError()` 转**中文人话**，禁止裸错误码（宪法 §56/§60）。
 *
 * 说明：后端（2026-09）尚未提供 `/agent/tasks/{id}/stream`，故**默认走轮询**；
 * `subscribeTaskStream()` 保留给后端补齐 SSE 后启用（帧仅驱动 activity）。
 */
import { reactive, toRefs, type Component } from 'vue'
import {
  AddCircleOutline,
  BugOutline,
  BulbOutline,
  CalculatorOutline,
  ChatbubbleEllipsesOutline,
  CheckmarkCircle,
  CloseCircle,
  CodeSlashOutline,
  CreateOutline,
  LanguageOutline,
  LocateOutline,
  ReaderOutline,
  RefreshOutline,
  SparklesOutline,
  WarningOutline,
} from '@vicons/ionicons5'
import { toRvError, type RvError } from '../api/client'
import { getTask, runTask, streamTask } from '../api/agent'
import type {
  AgentContext,
  AgentPreset,
  AgentStreamEvent,
  AgentTask,
  ResearchResult,
  ResultStatus,
  WorkspaceMode,
} from '../api/types'
import type { StatusTone } from '../ui/StatusPill.vue'

/* ───────────────────────── 能力顺序（宪法 §28 / VISUAL-SPEC §4.4）───────────────────────── */

/** READ 能力（顺序固定）。 */
export const READ_PRESETS: AgentPreset[] = [
  'derivation_check',
  'citation',
  'latex_compare',
  'formula_check',
  'translate',
]

/** WRITE 能力（顺序固定）。 */
export const WRITE_PRESETS: AgentPreset[] = [
  'latex_error',
  'suggestion',
  'citation',
  'formula_check',
  'polish',
  'translate',
]

export function presetsFor(mode: WorkspaceMode): AgentPreset[] {
  return mode === 'write' ? WRITE_PRESETS : READ_PRESETS
}

/* ───────────────────────── 预设元数据 ───────────────────────── */

/**
 * 能力卡左侧 28×28 图标色调（VISUAL-SPEC §4.4-3 / 黄金图逐卡比对）。
 *
 * 基础 4 色之外新增 `purple`（翻译 / 润色）。其 soft 底 / ink 字色由
 * `CapabilityAccordion.vue` 用 `color-mix()` 从既有 `--rw-*` token 派生，
 * **未在组件里新增字面色值**（VISUAL-SPEC §7.9）。
 * 若后续希望收敛为命名 token，需主理人批准新增 `--rw-purple*`
 * （本轮未改 `tokens/research-tokens.css`）。
 *
 * 注：`公式检查` 在 READ / WRITE **同为绿色**（WRITE-GOLDEN 图标实测绿），
 * 故不再需要 READ/WRITE 分色，已删除旧的 `toneWrite` 分支。
 */
type PresetTone = 'primary' | 'success' | 'warning' | 'danger' | 'purple'

interface PresetMeta {
  icon: Component
  tone: PresetTone
  /** activity 文案（禁止暴露 chain-of-thought） */
  activity: string
  read?: string
  write?: string
}

const PRESET_META: Record<AgentPreset, PresetMeta> = {
  derivation_check: {
    icon: CheckmarkCircle,
    tone: 'success',
    activity: '正在检查当前推导……',
    read: '推导校验',
  },
  citation: {
    icon: ChatbubbleEllipsesOutline,
    tone: 'primary',
    activity: '正在查找项目文献与引用……',
    read: '引用补全',
    write: '引用补全',
  },
  latex_compare: {
    icon: CodeSlashOutline,
    tone: 'primary',
    activity: '正在对照文献与当前草稿……',
    read: 'LaTeX 对照',
  },
  formula_check: {
    icon: CalculatorOutline,
    tone: 'success',
    activity: '正在检查当前公式……',
    read: '公式检查',
    write: '公式检查',
  },
  translate: {
    icon: LanguageOutline,
    tone: 'purple',
    activity: '正在翻译当前内容……',
    read: '翻译与改写',
    write: '翻译成英文',
  },
  latex_error: {
    icon: BugOutline,
    tone: 'danger',
    activity: '正在读取编译日志……',
    write: 'LaTeX 错误解释',
  },
  suggestion: {
    icon: BulbOutline,
    tone: 'warning',
    activity: '正在分析项目与草稿……',
    write: '改进建议',
  },
  polish: {
    icon: SparklesOutline,
    tone: 'purple',
    activity: '正在润色当前段落……',
    write: '语言润色',
  },
}

export interface PresetView {
  preset: AgentPreset
  label: string
  icon: Component
  tone: PresetTone
  activity: string
}

export function presetLabel(preset: AgentPreset, mode: WorkspaceMode): string {
  const m = PRESET_META[preset]
  return (mode === 'write' ? m?.write : m?.read) ?? m?.read ?? m?.write ?? preset
}

export function presetMeta(preset: AgentPreset, mode: WorkspaceMode): PresetView {
  const m = PRESET_META[preset]
  return {
    preset,
    label: presetLabel(preset, mode),
    icon: m?.icon ?? SparklesOutline,
    tone: m?.tone ?? 'primary',
    activity: m?.activity ?? '正在处理……',
  }
}

/**
 * @deprecated 语言意图 → preset 的**关键词路由**。**已不再用于自由文本**。
 *
 * 宪法 §31 要求输入框是**真实对话通道**（`store.ask()` → `POST /projects/{id}/agent/chat`
 * → 会话子系统 → LLM）。把「这篇论文用了什么方法？」这类自由文本按关键词硬塞给某个
 * preset，会得到一个能力**结果卡**而不是对话回答，属于「看起来能用」（§71）。
 *
 * 保留此函数仅为**显式、可测**的兼容入口（如需「快捷能力」可在 UI 明示并默认关闭）；
 * 它的任何调用都必须是**有意为之**，不得再出现在 `ask()` 的默认路径里。
 */
export function routeIntent(message: string): AgentPreset {
  const s = (message || '').toLowerCase()
  const has = (...keys: string[]) => keys.some((k) => s.includes(k))
  if (has('引用', 'citation', 'reference', 'cite{', 'bibtex', '参考文献')) return 'citation'
  if (has('编译', '报错', '错误', 'error', 'undefined', '\\cite', 'latex 错')) return 'latex_error'
  if (has('推导', '证明', '推证', 'derive', 'derivation', 'proof', '引理', 'lemma', 'coerciv')) {
    return 'derivation_check'
  }
  if (has('公式', '方程', 'equation', 'formula', '恒等')) return 'formula_check'
  if (has('对照', 'compare', '一致', '与原论文')) return 'latex_compare'
  if (has('翻译', '译为', 'translate', 'english', '英文')) return 'translate'
  if (has('润色', 'polish', 'rewrite', '改写', '表达', '语言')) return 'polish'
  return 'suggestion'
}

/* ───────────────────────── 置信状态（宪法 §35）───────────────────────── */

export interface StatusView {
  label: string
  tone: StatusTone
  icon: Component
}

export const RESULT_STATUS_VIEW: Record<ResultStatus, StatusView> = {
  verified: { label: '已验证', tone: 'success', icon: CheckmarkCircle },
  supported: { label: '证据充分', tone: 'success', icon: CheckmarkCircle },
  // 黄金图状态药丸前置图标是**三角警示**（`⚠`），不是圆环 AlertCircle；
  // 且 `likely` 带概率限定语「（概率较高）」—— 与宪法 §35「避免把 AI 判断表达成绝对真理」一致。
  likely: { label: '基本成立（概率较高）', tone: 'success', icon: WarningOutline },
  needs_conditions: { label: '需要补充条件', tone: 'warning', icon: WarningOutline },
  insufficient: { label: '证据不足', tone: 'warning', icon: WarningOutline },
  conflict: { label: '存在冲突', tone: 'danger', icon: CloseCircle },
  failed: { label: '任务失败', tone: 'danger', icon: CloseCircle },
}

export function statusView(status: ResultStatus): StatusView {
  return RESULT_STATUS_VIEW[status] ?? RESULT_STATUS_VIEW.insufficient
}

/* ───────────────────────── 动作 → 按钮（统一 Result DS）───────────────────────── */

export interface ActionMeta {
  key: string
  label: string
  variant: 'primary' | 'outline'
  icon?: Component
  /** 是否属于「会修改论文」的动作（必须用户确认，宪法 §32/§40） */
  mutating?: boolean
}

const ACTION_META: Record<string, ActionMeta> = {
  locate: { key: 'locate', label: '定位原文', variant: 'outline', icon: LocateOutline },
  view_evidence: { key: 'view_evidence', label: '查看证据', variant: 'outline', icon: ReaderOutline },
  add_note: { key: 'add_note', label: '加入笔记', variant: 'primary', icon: AddCircleOutline },
  insert_citation: {
    key: 'insert_citation',
    label: '插入引用',
    variant: 'primary',
    icon: ChatbubbleEllipsesOutline,
    mutating: true,
  },
  apply_patch: {
    key: 'apply_patch',
    label: '应用修复',
    variant: 'outline',
    icon: CreateOutline,
    mutating: true,
  },
  recompile: { key: 'recompile', label: '重新编译', variant: 'outline', icon: RefreshOutline },
}

/** 把后端 `actions[]` 映射为按钮描述；未知动作给一个安全的描边按钮。 */
export function actionMeta(key: string): ActionMeta {
  return ACTION_META[key] ?? { key, label: key, variant: 'outline' }
}

/* ───────────────────────── 状态机（模块级单例，与组件挂载解耦）───────────────────────── */

interface AgentTaskState {
  /** 当前执行/最近完成的能力（决定 Accordion 展开项） */
  preset: AgentPreset | null
  task: AgentTask | null
  result: ResearchResult | null
  activity: string
  running: boolean
  streaming: boolean
  startedAt: number
  error: RvError | null
}

const state = reactive<AgentTaskState>({
  preset: null,
  task: null,
  result: null,
  activity: '',
  running: false,
  streaming: false,
  startedAt: 0,
  error: null,
})

const POLL_INTERVAL_MS = 1500
const POLL_MAX_ATTEMPTS = 200

let pollTimer: ReturnType<typeof setInterval> | null = null
let closeStream: (() => void) | null = null

/**
 * §46 幂等键：同一「用户意图」在**成功建任务之前**复用同一个 `client_request_id`。
 * 成功建任务后清空 → 下一次相同请求是新的意图（后端会正常新建任务）。
 * 失败时保留 → 用户重试会命中后端的去重逻辑，不会产生第二个任务。
 */
let pendingRequestId: string | null = null
let pendingFingerprint = ''

/** 生成幂等键（优先 UUID v4，退化到时间戳+随机）。 */
function newClientRequestId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `rw-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

/** 结果落库回调（由 `stores/agent.ts` 注册，用于写入会话线程）。 */
let resultSink: ((result: ResearchResult, preset: AgentPreset | null) => void) | null = null

export function setAgentResultSink(
  fn: ((result: ResearchResult, preset: AgentPreset | null) => void) | null,
): void {
  resultSink = fn
}

/** 订阅 SSE（后端补齐端点后启用）；返回 `close()`。帧仅驱动 activity（宪法 §55）。 */
export function subscribeTaskStream(
  taskId: string,
  onEvent: (ev: AgentStreamEvent) => void,
): () => void {
  return streamTask(taskId, { onEvent })
}

function clearPoll(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopStream(): void {
  if (closeStream) {
    closeStream()
    closeStream = null
  }
  state.streaming = false
}

function finish(result: ResearchResult): void {
  state.result = result
  state.task = state.task
    ? { ...state.task, status: 'completed', result }
    : { task_id: '', status: 'completed', result }
  state.running = false
  state.activity = ''
  stopStream()
  clearPoll()
  resultSink?.(result, state.preset)
}

function settleError(err: RvError): void {
  state.error = err
  state.running = false
  state.activity = ''
  stopStream()
  clearPoll()
}

function pollTask(taskId: string): void {
  clearPoll()
  let attempts = 0
  pollTimer = setInterval(() => {
    attempts += 1
    if (attempts > POLL_MAX_ATTEMPTS) {
      settleError({
        code: 'TASK_TIMEOUT',
        status: 0,
        message: '任务耗时较长，仍在后台执行，可稍后重新查看。',
        retryable: true,
      })
      return
    }
    void (async () => {
      try {
        const detail = await getTask(taskId)
        state.task = detail.task
        if (detail.task.activity) state.activity = detail.task.activity
        // ⚠️ 后端在 running 期也会返回带 `kind` 的**占位结果**（summary=「任务尚未完成」）。
        // 只有任务真正 `completed` 才把结果落成结果卡，否则会把占位文案当成结论渲染（§54/§58）。
        if (detail.result && detail.task.status === 'completed') {
          finish(detail.result)
          return
        }
        if (detail.task.status === 'failed') {
          settleError({ code: 'TASK_FAILED', status: 0, message: '任务执行未成功，请重新尝试。', retryable: true })
        } else if (detail.task.status === 'cancelled') {
          state.running = false
          state.activity = ''
          clearPoll()
        }
      } catch (e) {
        settleError(toRvError(e))
      }
    })()
  }, POLL_INTERVAL_MS)
}

/**
 * 启动一次能力任务（点击能力后直接跑，**禁止参数表单**，宪法 §30）。
 * 返回 task_id（失败为 null）。
 */
export async function startAgentTask(
  projectId: string,
  preset: AgentPreset,
  context: AgentContext,
  message?: string,
): Promise<string | null> {
  clearPoll()
  stopStream()
  state.preset = preset
  state.error = null
  state.result = null
  state.task = null
  state.activity = PRESET_META[preset]?.activity ?? '正在处理……'
  state.running = true
  state.startedAt = Date.now()
  if (!projectId) {
    settleError({ code: 'NO_PROJECT', status: 0, message: '尚未选择科研项目。', retryable: false })
    return null
  }
  // §46 幂等：同一「用户意图」（同 preset + 同 context + 同 message）复用同一个键。
  // 覆盖三种真实场景：① 双击 ② 网络超时后重试 ③ 出错后再点一次 ——
  // 都不该在后端重复建任务（后端按 (tenant, actor, client_request_id) 去重）。
  const fingerprint = JSON.stringify([preset, context, message ?? ''])
  if (!pendingRequestId || pendingFingerprint !== fingerprint) {
    pendingFingerprint = fingerprint
    pendingRequestId = newClientRequestId()
  }
  try {
    const ack = await runTask(projectId, preset, context, message, pendingRequestId)
    state.task = { task_id: ack.task_id, status: 'queued', activity: state.activity }
    // 已成功建任务 → 键的使命结束；下一次相同请求应是**新的**意图
    pendingRequestId = null
    pendingFingerprint = ''
    pollTask(ack.task_id)
    return ack.task_id
  } catch (e) {
    settleError(toRvError(e))
    // 失败时**保留** pendingRequestId：用户重试会复用同一个键，
    // 避免"后端其实已建任务但响应丢了"变成两个任务。
    return null
  }
}

/** 停止本地轮询并标记取消（后端无 cancel 端点：仅停止前端等待，不谎报服务端状态）。 */
export function cancelAgentTask(): void {
  clearPoll()
  stopStream()
  state.running = false
  state.activity = ''
  if (state.task) state.task = { ...state.task, status: 'cancelled' }
}

export function resetAgentTask(): void {
  clearPoll()
  stopStream()
  state.preset = null
  state.task = null
  state.result = null
  state.activity = ''
  state.running = false
  state.error = null
}

/**
 * **恢复**一次已完成的任务到状态机（宪法 §84：重进项目应恢复必要 panel state）。
 *
 * 与 `startAgentTask` 的区别：本函数**不发任何网络请求、不进入 running**，
 * 只是把上次已产出的结果放回面板 —— 用户重进项目时看到的仍是上次那张结果卡，
 * 而不是"又从空态开始"。`running` 时不调用（不覆盖进行中的任务）。
 */
export function hydrateAgentTask(
  preset: AgentPreset | null,
  task: AgentTask | null,
  result: ResearchResult | null,
): void {
  if (state.running) return
  clearPoll()
  stopStream()
  state.preset = preset
  state.task = task
  state.result = result
  state.activity = ''
  state.streaming = false
  state.error = null
}

/** 面板/组件使用的响应式门面。 */
export function useAgentTask() {
  return {
    ...toRefs(state),
    presetsFor,
    presetMeta,
    presetLabel,
    statusView,
    actionMeta,
    start: startAgentTask,
    cancel: cancelAgentTask,
    reset: resetAgentTask,
    hydrate: hydrateAgentTask,
  }
}
