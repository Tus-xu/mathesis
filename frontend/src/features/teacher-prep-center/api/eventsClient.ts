/**
 * 备课会话 SSE 客户端（fetch 流式解析；EventSource 无法带 Authorization 头）。
 *
 * 通道契约（8100 GET /prep/conversations/{id}/events，frame="wrapped"）：
 * - 首帧 `snapshot`：data = 完整 workspace 快照（权威投影）；
 * - 事件帧 `event`：data = {seq,event_type,payload,...}，id = seq（Last-Event-ID 断点续传）；
 * - 心跳 `: ping` 注释行；`retry: 3000` 重连间隔。
 *
 * 断线自动重连（指数退避 ≤3 次），重连带 Last-Event-ID（durable replay 权威补齐）。
 */
import { getAccessToken } from '@/api/authSession'
import { PREP_CENTER_API_BASE, type PrepSseFrame, type PrepWorkspaceSnapshot } from './prepCenterApi'

/** SSE 连接生命周期状态（W1-C 加法式：供对话面渲染「断线重连」提示）。 */
export type PrepStreamStatus = 'connecting' | 'open' | 'reconnecting' | 'closed' | 'idle'

export interface PrepEventStreamHandlers {
  onSnapshot?: (ws: PrepWorkspaceSnapshot) => void
  onEvent?: (frame: PrepSseFrame) => void
  onError?: (err: unknown) => void
  /** 可选：连接状态变化回调（W1-C 加法式参数，不影响既有调用方）。 */
  onStatus?: (status: PrepStreamStatus) => void
}

export interface PrepEventStream {
  readonly sessionId: string
  close(): void
}

function authHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function openPrepEventStream(sessionId: string, handlers: PrepEventStreamHandlers): PrepEventStream {
  let controller: AbortController | null = null
  let closed = false
  let lastSeq = 0
  let attempts = 0
  const MAX_ATTEMPTS = 8

  async function connect(): Promise<void> {
    while (!closed && attempts < MAX_ATTEMPTS) {
      controller = new AbortController()
      handlers.onStatus?.(attempts === 0 ? 'connecting' : 'reconnecting')
      try {
        const res = await fetch(
          `${PREP_CENTER_API_BASE}/conversations/${encodeURIComponent(sessionId)}/events`,
          {
            method: 'GET',
            headers: {
              Accept: 'text/event-stream',
              'Cache-Control': 'no-cache',
              ...(lastSeq > 0 ? { 'Last-Event-ID': String(lastSeq) } : {}),
              ...authHeader(),
            },
            signal: controller.signal,
          },
        )
        if (!res.ok || !res.body) {
          throw new Error(`sse connect failed: ${res.status}`)
        }
        attempts = 0 // 连接成功 → 重新计满重试额度
        handlers.onStatus?.('open')
        const reader = res.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let sep: number
          while ((sep = buffer.indexOf('\n\n')) >= 0) {
            const rawBlock = buffer.slice(0, sep)
            buffer = buffer.slice(sep + 2)
            handleBlock(rawBlock)
          }
        }
        // 服务端正常关闭（如会话切换）→ 视为断线，走重连逻辑
      } catch (err) {
        if (closed || (err as Error)?.name === 'AbortError') return
        handlers.onStatus?.('reconnecting')
        handlers.onError?.(err)
      }
      attempts += 1
      if (!closed && attempts < MAX_ATTEMPTS) {
        await new Promise((r) => setTimeout(r, Math.min(600 * 2 ** attempts, 5000)))
      }
    }
  }

  function handleBlock(rawBlock: string): void {
    let eventName = ''
    const dataLines: string[] = []
    let id: string | null = null
    for (const line of rawBlock.split('\n')) {
      if (!line || line.startsWith(':')) continue // 心跳/注释
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      else if (line.startsWith('id:')) id = line.slice(3).trim()
      // retry: 前端忽略（自动重连由本客户端负责）
    }
    if (id) {
      const n = Number(id)
      if (Number.isFinite(n) && n > lastSeq) lastSeq = n
    }
    if (!dataLines.length) return
    let data: unknown
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      return
    }
    if (closed) return // 已关闭：缓冲中的迟到块不得再回调（防会话切换串台）
    if (eventName === 'snapshot') {
      handlers.onSnapshot?.(data as PrepWorkspaceSnapshot)
      return
    }
    if (eventName === 'event' && data && typeof data === 'object' && 'event_type' in (data as object)) {
      handlers.onEvent?.(data as PrepSseFrame)
    }
  }

  void connect()

  return {
    sessionId,
    close() {
      closed = true
      controller?.abort()
      controller = null
      handlers.onStatus?.('closed')
    },
  }
}
