/**
 * T22 · teacher-prep-v2 工作区 store —— **server / stream / client 三层状态边界**。
 *
 * ┌─ SERVER STATE（权威）───────────────────────────────────────────────┐
 * │ snapshot（8100 `views.build_workspace` 投影）：session / timeline /   │
 * │ artifact / evidence / available_actions / job_summary / basic_mode。 │
 * │ 客户端**只读投影**，不推导业务事实、不补齐缺失态。                    │
 * └───────────────────────────────────────────────────────────────────┘
 * ┌─ STREAM STATE（连接生命周期）────────────────────────────────────────┐
 * │ streamState / 心跳 / 重连退避 / 缺口重对账计数。                      │
 * │ 只是"链路是否可信"的元状态，**不是**业务进度。                        │
 * └───────────────────────────────────────────────────────────────────┘
 * ┌─ CLIENT STATE（可重建）──────────────────────────────────────────────┐
 * │ Pinia：面板折叠 / 当前 artifact tab / 选中证据 —— 丢失可重建。        │
 * │ localStorage：**仅** UI 偏好与未提交草稿（不含任何业务事实）。        │
 * └───────────────────────────────────────────────────────────────────┘
 *
 * 铁律：PARTIAL 不得呈现为"完成"（`connectionTone` 永不因部分数据返回 success）。
 */
import { computed, ref } from 'vue'
import { uuid } from '@/utils/uuid'
import { defineStore } from 'pinia'

import {
  PrepApiError,
  createConversation,
  currentToken,
  getWorkspace,
  normalizePrepError,
  postAction,
  postMessage,
} from '@/features/teacher-prep-v2/api/prepApi'
import { createPrepStream, type PrepStream, type StreamState } from '@/features/teacher-prep-v2/stream/prepStream'
import type { JobSummaryView, NormalizedError, WorkspaceSnapshot, WorkspacePhase } from '@/features/teacher-prep-v2/types/workspace'

/** 只允许持久化这些键（UI 偏好 + 未提交草稿）；业务事实一律不落盘。 */
export interface UiPrefs {
  evidenceRailCollapsed: boolean
  activeArtifactTab: string
  composerDraft: string
}

const UI_STORAGE_KEY = 'teacher-prep-v2:ui:v1'
const DEFAULT_UI: UiPrefs = { evidenceRailCollapsed: false, activeArtifactTab: 'context', composerDraft: '' }

export type ConnectionTone = 'neutral' | 'running' | 'partial' | 'reconnecting' | 'forbidden' | 'conflict' | 'error' | 'success'

export const usePrepWorkspaceStore = defineStore('prepWorkspaceV2', () => {
  // ── SERVER STATE ────────────────────────────────────────────────────────
  const snapshot = ref<WorkspaceSnapshot | null>(null)
  const jobs = ref<JobSummaryView[]>([])
  const error = ref<NormalizedError | null>(null)
  const phase = ref<WorkspacePhase>('loading')

  // ── STREAM STATE ────────────────────────────────────────────────────────
  const streamState = ref<StreamState>('idle')
  /** 流侧已应用的最大 seq（推进 Last-Event-ID 基准；snapshot 到达时以服务端为准）。 */
  const streamSeq = ref(0)
  const gapCount = ref(0)
  const lastGapReason = ref<string | null>(null)
  let stream: PrepStream | null = null
  let inFlight: AbortController | null = null
  let resyncTimer: ReturnType<typeof setTimeout> | null = null

  // ── CLIENT STATE ────────────────────────────────────────────────────────
  const ui = ref<UiPrefs>({ ...DEFAULT_UI })
  const sessionId = ref<string | null>(null)

  // ── 派生 ────────────────────────────────────────────────────────────────
  const revision = computed(() => snapshot.value?.session?.revision ?? 0)
  const snapshotSeq = computed(() => Number(snapshot.value?.session?.last_event_seq ?? 0) || 0)
  /** Last-Event-ID 基准 = max(服务端快照 seq, 流侧已应用 seq)，单调不回退。 */
  const lastEventSeq = computed(() => Math.max(snapshotSeq.value, streamSeq.value))
  const timeline = computed(() => snapshot.value?.timeline ?? [])
  const artifact = computed(() => snapshot.value?.artifact ?? { context: null, blueprint: null, diagnostic: null })
  const evidence = computed(() => snapshot.value?.evidence ?? [])
  const availableActions = computed(() => snapshot.value?.available_actions ?? [])
  const basicMode = computed(() => snapshot.value?.basic_mode ?? false)

  const hasRunningJob = computed(() =>
    jobs.value.some((j) => j.status === 'PENDING' || j.status === 'RUNNING' || j.status === 'CLAIMED'),
  )
  const hasFailedJob = computed(() => jobs.value.some((j) => j.status === 'FAILED' || j.status === 'BASIC_MODE'))

  /** PARTIAL 永不为 success：这是任务书第 7 条的机器保证。 */
  const connectionTone = computed<ConnectionTone>(() => {
    if (error.value?.kind === 'forbidden') return 'forbidden'
    if (error.value?.kind === 'conflict') return 'conflict'
    if (error.value && error.value.kind !== 'unauthenticated') return 'error'
    if (streamState.value === 'reconnecting') return 'reconnecting'
    if (gapCount.value > 0 && streamState.value !== 'live') return 'partial'
    if (hasFailedJob.value) return 'partial'
    if (hasRunningJob.value) return 'running'
    if (snapshot.value) return 'success'
    return 'neutral'
  })

  // ── SERVER STATE 写入（唯一入口）────────────────────────────────────────
  function applySnapshot(next: WorkspaceSnapshot) {
    snapshot.value = next
    jobs.value = next.job_summary ?? []
    error.value = null
    gapCount.value = 0
    phase.value = next.timeline.length === 0 ? 'empty' : 'ready'
  }

  function setJobSummary(next: JobSummaryView[]) {
    jobs.value = next
  }

  function fail(err: unknown) {
    const info =
      err instanceof PrepApiError
        ? err.info
        : normalizePrepError({ status: null, detail: err instanceof Error ? err.message : String(err) })
    error.value = info
    phase.value =
      info.kind === 'forbidden' ? 'forbidden' : info.kind === 'conflict' ? 'conflict' : 'error'
  }

  /** 流侧推进 Last-Event-ID 基准（由 stream 回调驱动，单调不回退）。 */
  function noteAppliedSeq(seq: number) {
    if (Number.isFinite(seq) && seq > streamSeq.value) streamSeq.value = seq
  }

  // ── CLIENT STATE ────────────────────────────────────────────────────────
  function setUiPref<K extends keyof UiPrefs>(key: K, value: UiPrefs[K]) {
    ui.value[key] = value
    persistUi()
  }

  function persistUi() {
    try {
      // 只写 UI 偏好/草稿；不含任何业务事实
      localStorage.setItem(UI_STORAGE_KEY, JSON.stringify(ui.value))
    } catch {
      /* 隐私模式下 localStorage 不可用：UI 偏好丢失可接受 */
    }
  }

  function restoreUiPrefs() {
    try {
      const raw = localStorage.getItem(UI_STORAGE_KEY)
      if (raw) ui.value = { ...DEFAULT_UI, ...(JSON.parse(raw) as Partial<UiPrefs>) }
    } catch {
      ui.value = { ...DEFAULT_UI }
    }
  }

  // ── 远端操作 ────────────────────────────────────────────────────────────
  /** 缺口/僵死 → 以服务端 snapshot 重对账（debounce，单飞）。 */
  function requestResync(reason: string) {
    lastGapReason.value = reason
    gapCount.value += 1
    if (resyncTimer !== null) clearTimeout(resyncTimer)
    resyncTimer = setTimeout(() => {
      resyncTimer = null
      void load({ silent: true })
    }, 120)
  }

  async function load(opts: { silent?: boolean } = {}) {
    if (!sessionId.value) return
    if (!opts.silent) {
      phase.value = 'loading'
      inFlight?.abort()
    }
    const ctrl = new AbortController()
    inFlight = ctrl
    try {
      const snap = await getWorkspace(sessionId.value, ctrl.signal)
      if (ctrl.signal.aborted) return
      applySnapshot(snap)
    } catch (e) {
      if (ctrl.signal.aborted) return
      fail(e)
    }
  }

  /**
   * 带退避的 409 收敛提交。
   *
   * 根因：会话运行（LLM 编排）在后台连续推进 revision，教师点击时携带的
   * `expected_revision` 极易在一次往返内过期 → 409。单次重试不够（运行期间
   * revision 可能连续跳多格）。这里每次冲突都：静默重拉快照 → 取最新 revision
   * → 退避后重发（同一操作、新幂等键）。被 409 拒绝的请求未落库，重发安全。
   * 超过 attempts 次仍冲突才交给失败态（此时说明是持续演进的会话，交给用户刷新）。
   */
  async function submitWithConflictRetry<T>(submit: () => Promise<T>, attempts = 5): Promise<T> {
    let lastErr: unknown
    for (let i = 0; i < attempts; i += 1) {
      try {
        return await submit()
      } catch (e) {
        lastErr = e
        if (!(e instanceof PrepApiError) || e.info.kind !== 'conflict') throw e
        await load({ silent: true })
        if (error.value) throw e
        await new Promise((resolve) => setTimeout(resolve, 200 * (i + 1)))
      }
    }
    throw lastErr
  }

  async function send(
    text: string,
    idempotencyKey?: string,
    selection?: { textbook_id?: string; section_id?: string; class_id?: string },
  ) {
    if (!text.trim()) return
    if (!sessionId.value) {
      const idem0 = idempotencyKey ?? `fe-${uuid()}`
      const created = await createConversation({ message: text, idempotency_key: idem0 })
      applySnapshot(created)
      sessionId.value = created.session.id
      startStream()
      return
    }
    // 迭代20 门禁修复：ref 的 .value 收窄不穿透 submitWithConflictRetry 的回调，
    // 在闭包外捕获非空字面量，消除 string|null → string 的 TS2345。
    const sid: string = sessionId.value
    try {
      const snap = await submitWithConflictRetry(() =>
        postMessage(sid, {
          message: text,
          idempotency_key: `fe-${uuid()}`,
          expected_revision: revision.value,
          selection,
        }),
      )
      applySnapshot(snap)
      setUiPref('composerDraft', '')
    } catch (e) {
      fail(e)
    }
  }

  async function runAction(
    action: string,
    target?: string | null,
    payload?: Record<string, unknown> | null,
  ) {
    if (!sessionId.value) return
    const sid: string = sessionId.value
    try {
      const snap = await submitWithConflictRetry(() =>
        postAction(sid, {
          action,
          target: target ?? null,
          expected_revision: revision.value,
          idempotency_key: `fe-${action}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
          payload: payload ?? null,
        }),
      )
      applySnapshot(snap)
    } catch (e) {
      fail(e)
    }
  }

  // ── STREAM 生命周期 ─────────────────────────────────────────────────────
  function startStream() {
    if (!sessionId.value) return
    stopStream()
    stream = createPrepStream({
      sessionId: sessionId.value,
      // SSE 与 REST 共用同一令牌入口（不新增第二套鉴权来源）
      token: currentToken,
      lastEventId: () => lastEventSeq.value,
      onSnapshot: applySnapshot,
      onGap: (reason) => requestResync(reason),
      onAppliedSeq: noteAppliedSeq,
      onStateChange: (s) => {
        streamState.value = s
        if (s === 'live') phase.value = snapshot.value ? (snapshot.value.timeline.length ? 'ready' : 'empty') : 'loading'
      },
    })
    stream.start()
  }

  function stopStream() {
    stream?.stop()
    stream = null
    streamState.value = 'closed'
  }

  function attach(nextSessionId: string | null) {
    sessionId.value = nextSessionId
    restoreUiPrefs()
    if (nextSessionId) {
      void load()
      startStream()
    } else {
      stopStream()
      // 无会话 = 可开始新备课（conversation-first 的起点），不是加载中
      phase.value = 'empty'
    }
  }

  function detach() {
    inFlight?.abort()
    inFlight = null
    if (resyncTimer !== null) clearTimeout(resyncTimer)
    resyncTimer = null
    stopStream()
    sessionId.value = null
    snapshot.value = null
    jobs.value = []
    error.value = null
    phase.value = 'loading'
    gapCount.value = 0
    streamSeq.value = 0
  }

  return {
    // server
    snapshot, jobs, error, phase, revision, lastEventSeq,
    timeline, artifact, evidence, availableActions, basicMode,
    // stream
    streamState, streamSeq, gapCount, lastGapReason,
    // client
    ui, sessionId,
    // derived
    connectionTone, hasRunningJob, hasFailedJob,
    // actions
    applySnapshot, setJobSummary, fail,
    setUiPref, restoreUiPrefs, persistUi,
    load, send, runAction, noteAppliedSeq,
    startStream, stopStream, attach, detach, requestResync,
  }
})
