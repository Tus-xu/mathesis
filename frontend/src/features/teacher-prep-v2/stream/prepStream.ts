/**
 * T22 · teacher-prep-v2 SSE 客户端（8100 `GET /prep/conversations/{id}/events`）。
 *
 * 传输契约（8100 `app/core/sse.py`，R11.5-C 冻结）：
 *   首行 `retry:3000` → `event: snapshot` → `event: event`（durable 重放，id=seq）→ live（id=seq）
 *   → 15s 心跳 `: ping` 注释行。断线重连用 `Last-Event-ID` 头（或 `lastEventId` query）。
 *
 * 本模块刻意**不**做以下事（反模式）：
 * - 不用 setTimeout/setInterval 伪造 AI / job 进度；
 * - 不在客户端补齐"缺失的中间态"——发现 seq 缺口一律请求服务端 snapshot 重对账；
 * - 不吞 401/403（交给 store 映射为可见状态）。
 */

import type { StreamEnvelope, WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

export interface RawFrame {
  id?: string
  event?: string
  data?: string
  comment?: string
}

export interface StreamRequest {
  url: string
  headers: Record<string, string>
  signal: AbortSignal
}

export interface StreamHandlers {
  onFrame(frame: RawFrame): void
  onError(err: unknown): void
}

export interface StreamTransport {
  open(req: StreamRequest, handlers: StreamHandlers): unknown
  close(): void
}

export interface PrepStreamOptions {
  sessionId: string
  /** 返回本地已应用的最后 seq（用于 Last-Event-ID 断点续传）。 */
  lastEventId: () => number
  onSnapshot?: (snapshot: WorkspaceSnapshot) => void
  /** seq 缺口 / 不可解析帧 → 请求服务端重对账（唯一"补洞"手段）。 */
  onGap?: (reason: string, detail?: { expected?: number; got?: number }) => void
  onEvent?: (env: StreamEnvelope) => void
  /** 已应用的 seq（含缺口后采纳服务端 seq）—— 供调用方推进 Last-Event-ID 基准。 */
  onAppliedSeq?: (seq: number) => void
  onStateChange?: (state: StreamState) => void
  transport?: StreamTransport
  /** 注入式 sleep（测试可控；生产用真实定时器）。 */
  sleep?: (ms: number) => Promise<void>
  maxRetries?: number
  baseBackoffMs?: number
  maxBackoffMs?: number
  /** 心跳超时（服务端 15s，客户端按 2.5 倍容忍）。 */
  heartbeatTimeoutMs?: number
  eventsUrl?: string
  token?: () => string | null
}

export type StreamState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed'

export interface PrepStream {
  start(): void
  stop(): void
  readonly state: StreamState
  /** 测试/内部：喂入一帧原始 SSE 数据。 */
  __ingest(frame: RawFrame): void
  /** 测试/内部：驱动一次完整连接循环（含退避），直到成功或重试耗尽。 */
  __runUntilStopped(): Promise<void>
}

const DEFAULT_BASE_BACKOFF = 1000
const DEFAULT_MAX_BACKOFF = 30000

/**
 * 建立会话流。
 *
 * 缺口判定（服务端权威优先）：
 * - `seq === last + 1`  → 连续，直接应用；
 * - `seq >  last + 1`   → **缺口**：`onGap()` 请求服务端 snapshot 重对账，并采纳服务端 seq；
 * - `seq <= last`       → 重复/迟到帧，忽略（幂等）。
 */
export function createPrepStream(opts: PrepStreamOptions): PrepStream {
  const transport: StreamTransport =
    opts.transport ??
    createFetchEventSourceTransport({ token: opts.token ?? (() => null) })
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const maxRetries = opts.maxRetries ?? 8
  const base = opts.baseBackoffMs ?? DEFAULT_BASE_BACKOFF
  const cap = opts.maxBackoffMs ?? DEFAULT_MAX_BACKOFF
  const hbTimeout = opts.heartbeatTimeoutMs ?? 37500
  const url = opts.eventsUrl ?? `/api/teacher-v3/prep/conversations/${encodeURIComponent(opts.sessionId)}/events`

  let state: StreamState = 'idle'
  let stopped = true
  let controller: AbortController | null = null
  let heartbeatTimer: ReturnType<typeof setTimeout> | null = null
  let lastSeq = 0

  function setState(next: StreamState) {
    if (state === next) return
    state = next
    opts.onStateChange?.(next)
  }

  function clearHeartbeat() {
    if (heartbeatTimer !== null) {
      clearTimeout(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  /** 心跳看门狗：超时即判定链路僵死，主动重连（不假装还活着）。 */
  function armHeartbeat() {
    clearHeartbeat()
    heartbeatTimer = setTimeout(() => {
      if (stopped) return
      opts.onGap?.('heartbeat_timeout')
      reconnect()
    }, hbTimeout)
  }

  function backoffFor(attempt: number): number {
    const raw = Math.min(cap, base * 2 ** attempt)
    // 有界抖动（≤250ms），避免多标签同时重连造成惊群；不破坏单调性
    return raw + Math.floor(Math.random() * 250)
  }

  function headers(): Record<string, string> {
    const h: Record<string, string> = { Accept: 'text/event-stream' }
    const last = opts.lastEventId()
    if (last > 0) h['Last-Event-ID'] = String(last)
    const t = opts.token?.()
    if (t) h['Authorization'] = `Bearer ${t}`
    return h
  }

  function handleFrame(frame: RawFrame) {
    armHeartbeat()
    if (frame.comment !== undefined) return // 心跳注释行（`: ping`）

    if (frame.event === 'snapshot') {
      try {
        const snap = JSON.parse(frame.data ?? '{}') as WorkspaceSnapshot
        lastSeq = Number(snap?.session?.last_event_seq ?? lastSeq) || lastSeq
        opts.onSnapshot?.(snap)
      } catch {
        opts.onGap?.('unparsable_snapshot')
      }
      return
    }

    const seq = Number.parseInt(frame.id ?? '', 10)
    if (!Number.isFinite(seq)) {
      // 无 id 的帧无法参与对账 → 交给服务端 snapshot 兜底
      opts.onGap?.('frame_without_seq')
      return
    }

    if (seq > lastSeq + 1) {
      opts.onGap?.('seq_gap', { expected: lastSeq + 1, got: seq })
    } else if (seq <= lastSeq) {
      return // 幂等：迟到/重复帧
    }
    lastSeq = seq
    opts.onAppliedSeq?.(seq)

    let parsed: unknown = null
    try {
      parsed = JSON.parse(frame.data ?? 'null')
    } catch {
      opts.onGap?.('unparsable_frame')
      return
    }
    const env = parsed as StreamEnvelope
    if (env && typeof env === 'object' && typeof env.event_type === 'string') {
      opts.onEvent?.({ ...env, seq })
      return
    }
    /**
     * 8100 live 帧当前只下发 `payload`（`sse.py` 对 live 取 `msg["payload"]`），
     * 因此 event_type 在 live 通道不可得 —— 这是**后端契约缺口**，不做客户端猜测：
     * 直接请求一次 snapshot 重对账（服务端权威）。已登记为 SHARED-CHANGE-REQUEST。
     */
    opts.onEvent?.({ seq, payload: (env ?? {}) as unknown as Record<string, unknown> })
    opts.onGap?.('live_frame_without_event_type')
  }

  function reconnect() {
    if (stopped) return
    setState('reconnecting')
    try {
      transport.close()
    } catch {
      /* 关闭失败不影响重连 */
    }
    void runLoop()
  }

  async function runLoop(): Promise<void> {
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      if (stopped) return
      setState(attempt === 0 ? 'connecting' : 'reconnecting')
      controller = new AbortController()
      try {
        transport.open({ url, headers: headers(), signal: controller.signal }, {
          onFrame: handleFrame,
          onError: (err) => {
            throw err
          },
        })
        setState('live')
        return
      } catch {
        clearHeartbeat()
        if (stopped) return
        if (attempt === maxRetries) {
          setState('closed')
          opts.onGap?.('retries_exhausted')
          return
        }
        await sleep(backoffFor(attempt))
      } finally {
        controller = null
      }
    }
  }

  return {
    get state() {
      return state
    },
    start() {
      stopped = false
      lastSeq = opts.lastEventId()
      void runLoop()
    },
    stop() {
      stopped = true
      clearHeartbeat()
      controller?.abort()
      controller = null
      try {
        transport.close()
      } catch {
        /* noop */
      }
      setState('closed')
    },
    __ingest: handleFrame,
    __runUntilStopped: () => {
      stopped = false
      return runLoop()
    },
  }
}

/* ─────────────── 真实传输：@microsoft/fetch-event-source ─────────────── */

export function createFetchEventSourceTransport(opts: {
  token: () => string | null
}): StreamTransport {
  let closeFn: (() => void) | null = null
  return {
    open(req, handlers) {
      const ctrl = new AbortController()
      closeFn = () => ctrl.abort()
      req.signal.addEventListener('abort', () => ctrl.abort())
      // 动态 import：SSR/单测环境下不强制加载
      void import('@microsoft/fetch-event-source').then(({ fetchEventSource }) => {
        void fetchEventSource(req.url, {
          method: 'GET',
          headers: { ...req.headers, ...authOf(opts.token()) },
          signal: ctrl.signal,
          openWhenHidden: true,
          onopen: async (res) => {
            if (!res.ok) {
              const err = new Error(`SSE_OPEN_${res.status}`)
              ;(err as Error & { status?: number }).status = res.status
              throw err
            }
          },
          onmessage: (ev) => {
            handlers.onFrame({ id: ev.id || undefined, event: ev.event || 'event', data: ev.data })
          },
          onerror: (err) => {
            handlers.onError(err)
            throw err // 交给 runLoop 退避重连
          },
        }).catch(() => {
          /* 由 onerror/onopen 抛出，runLoop 负责退避 */
        })
      })
      return { close: () => ctrl.abort() }
    },
    close() {
      closeFn?.()
      closeFn = null
    },
  }
}

function authOf(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {}
}
