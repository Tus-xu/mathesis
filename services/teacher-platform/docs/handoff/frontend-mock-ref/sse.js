/**
 * SSE 流式对话封装（基于 @microsoft/fetch-event-source）
 * 事件契约：meta → status* → (thinking|token|card|graph|action)* → citation?/badge? → title? → done
 * M2 新增：file_parsed / latex_rendered / answer_request / title / edited
 * 纪律：不认识的事件类型直接忽略；`: open` / `: ping` 注释行由解析器跳过；
 *       断线不自动重连（onerror 抛出即终止重试）。
 *
 * streamChat(payload, { path }) 默认 POST /api/agent/chat；
 * M2 新端点 regenerate / edit 复用同一 SSE 契约，仅 URL 不同。
 */
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { authHeaders, redirectLogin } from './client'

/** 带 HTTP 状态的错误，便于调用方识别 404 做优雅降级 */
export class SseHttpError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

export function streamChat(payload, { onEvent, signal, path = '/api/agent/chat' } = {}) {
  const ctrl = new AbortController()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true })
  }

  const finished = fetchEventSource(path, {
    method: 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal: ctrl.signal,
    openWhenHidden: true,
    async onopen(res) {
      const ct = res.headers.get('content-type') || ''
      if (res.status === 401) {
        redirectLogin()
        throw new SseHttpError(401, '登录已过期')
      }
      if (!res.ok || !ct.includes('text/event-stream')) {
        let detail = `HTTP ${res.status}`
        try { const j = await res.json(); detail = j.message || detail } catch { /* ignore */ }
        throw new SseHttpError(res.status, detail)
      }
    },
    onmessage(ev) {
      if (!ev.event) return // 心跳/注释行
      let data
      try { data = JSON.parse(ev.data) } catch { data = { raw: ev.data } }
      onEvent?.(ev.event, data)
    },
    onclose() { /* 服务端正常关闭 */ },
    onerror(err) { throw err }, // 抛出以禁止自动重连
  })

  return {
    abort: () => ctrl.abort(),
    finished: finished.catch((err) => {
      if (ctrl.signal.aborted) return { aborted: true }
      throw err
    }),
  }
}

/**
 * 课堂生成进度事件流（GET /api/classroom/sessions/{id}/events）。
 * 事件契约：status / title / outlines / slide / practice → done。
 * 服务端先回放历史事件再直播增量，重连/刷新不丢进度；
 * 断线不自动重连（调用方决定回退轮询）。
 */
export function streamClassroomEvents(sessionId, { onEvent, signal } = {}) {
  const ctrl = new AbortController()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true })
  }

  const finished = fetchEventSource(
    `/api/classroom/sessions/${encodeURIComponent(sessionId)}/events`,
    {
      method: 'GET',
      headers: { ...authHeaders(), Accept: 'text/event-stream' },
      signal: ctrl.signal,
      openWhenHidden: true,
      async onopen(res) {
        const ct = res.headers.get('content-type') || ''
        if (res.status === 401) {
          redirectLogin()
          throw new SseHttpError(401, '登录已过期')
        }
        if (!res.ok || !ct.includes('text/event-stream')) {
          let detail = `HTTP ${res.status}`
          try { const j = await res.json(); detail = j.message || detail } catch { /* ignore */ }
          throw new SseHttpError(res.status, detail)
        }
      },
      onmessage(ev) {
        if (!ev.event) return // 心跳/注释行
        let data
        try { data = JSON.parse(ev.data) } catch { data = { raw: ev.data } }
        onEvent?.(ev.event, data)
      },
      onclose() { /* 服务端发完 done 正常关闭 */ },
      onerror(err) { throw err }, // 抛出以禁止自动重连
    },
  )

  return {
    abort: () => ctrl.abort(),
    finished: finished.catch((err) => {
      if (ctrl.signal.aborted) return { aborted: true }
      throw err
    }),
  }
}
