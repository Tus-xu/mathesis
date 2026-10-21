/**
 * Research Agent API（BACKEND-CONTRACT §3.5 / §3.6）。
 *
 * - 统一任务入口：`POST /projects/{id}/agent/tasks` → 202 `{task_id, status, preset}`；
 *   `GET /agent/tasks/{task_id}` 返回**结构化 Research Result**。
 * - `streamTask`：SSE 订阅（复用 `@microsoft/fetch-event-source`，携带 `Last-Event-ID`、
 *   `openWhenHidden`）；帧**只驱动 activity 状态**（如「正在读取当前段落」），
 *   **禁止 chain-of-thought**（宪法 §55）。
 * - 长任务关闭 Accordion 后不消失（`useAgentTask` 持有 task_id 轮询）。
 *
 * ⚠️ 后端真实返回形态（已实测，2026-09）：`GET /agent/tasks/{id}` 把结果字段
 * **与 `task` 元信息平级**返回：
 *   `{ kind, status, summary, findings, evidence, source_locations, patches, actions,
 *      confidence, task: { id, preset, status, error_code, ... } }`
 * 因此本模块统一用 `normalizeTaskDetail()` 归一到 `{ task, result }`，
 * 上层不再各自解析（宪法 §87 稳定 schema）。
 */
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { getAccessToken } from '@/api/authSession'
import { RESEARCH_API_BASE, rwGet, rwPost } from './client'
import type {
  AgentContext,
  AgentPreset,
  AgentStreamEvent,
  AgentTask,
  AgentTaskState,
  ChatReply,
  ChatThread,
  KnowledgeHit,
  ResearchResult,
  ResultKind,
  ResultStatus,
} from './types'

/** `POST /agent/tasks` 的返回（后端 202）。 */
export interface RunTaskAck {
  task_id: string
  status?: string
  preset?: string
}

/**
 * 发起一次 Agent 任务。
 *
 * `clientRequestId`（宪法 §46 幂等）：同一「用户意图」必须复用同一个键 ——
 * 双击、网络重试、或出错后再点一次，都不应该在后端**重复建任务**。
 * 调用方负责生成与复用（见 `agent/useAgentTask.ts` 的 `pendingRequestId`）。
 * 后端接受 body 的 `client_request_id` 或 `X-Client-Request-Id` 头，这里**两个都发**
 * （同值），走哪条路都能命中同一条幂等记录。
 */
export async function runTask(
  projectId: string,
  preset: AgentPreset,
  context: AgentContext,
  message?: string,
  clientRequestId?: string,
): Promise<RunTaskAck> {
  const body: Record<string, unknown> = { preset, context, message }
  if (clientRequestId) body.client_request_id = clientRequestId
  return rwPost<RunTaskAck>(
    `/projects/${projectId}/agent/tasks`,
    body,
    undefined,
    clientRequestId ? { headers: { 'X-Client-Request-Id': clientRequestId } } : undefined,
  )
}

/**
 * 普通对话（非预设能力）走**既有会话消息端点**（BACKEND-CONTRACT §2「会话/消息」）。
 * 说明：`POST /agent/tasks` 仅接受 8 个能力 preset，**不接受自由对话**；
 * 自由对话由会话子系统承载（Research Agent 集成批次接线）。
 */
export async function postSessionMessage(
  sessionId: string,
  payload: { message: string; client_message_id?: string; expected_revision?: number },
): Promise<{ message_id?: string; revision?: number }> {
  return rwPost<{ message_id?: string; revision?: number }>(
    `/sessions/${encodeURIComponent(sessionId)}/messages`,
    payload,
  )
}

/**
 * 自由文本 → **真实对话**（宪法 §31；项目级便捷入口）。
 *
 * 与「点能力 = preset 直接执行」严格分工：本函数把用户自然语言送到会话子系统
 * （`POST /projects/{id}/agent/chat` → `message_service.send_message` → LLM 网关），
 * 返回**真实 assistant 回答**，而不是把关键词路由成 preset 结果卡。
 *
 * 会话由项目隐式持有（服务端「取或建」，一个用户一个项目一段对话），
 * 因此前端**不需要先建 session**。
 */
export async function postChatMessage(
  projectId: string,
  payload: { message: string; client_message_id?: string; expected_revision?: number },
): Promise<ChatReply> {
  return rwPost<ChatReply>(`/projects/${projectId}/agent/chat`, payload)
}

/**
 * 读取项目对话历史（重进项目时恢复对话线程，宪法 §84）。
 * 尚无对话时返回 `{ session_id: null, timeline: [] }`（诚实空态）。
 */
export async function getChatThread(projectId: string): Promise<ChatThread> {
  return rwGet<ChatThread>(`/projects/${projectId}/agent/chat`)
}

/** 归一后的任务详情：`task` 为任务元信息，`result` 为结构化结果（未完成时为 null）。 */
export interface AgentTaskDetail {
  task: AgentTask
  result: ResearchResult | null
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : v == null ? fallback : String(v)
}

/**
 * 把后端「结果字段与 task 平级」的返回归一到 `{ task, result }`。
 * 无 `kind` 字段视为「尚未产出结果」（任务可能仍在 running/queued）。
 */
export function normalizeTaskDetail(raw: unknown): AgentTaskDetail {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const meta = (o.task && typeof o.task === 'object' ? o.task : {}) as Record<string, unknown>

  const taskId = str(meta.id, str(meta.task_id, ''))
  const status = (str(meta.status, 'running') || 'running') as AgentTaskState
  const activity = str(meta.activity, '') || undefined

  const hasResult = typeof o.kind === 'string' && o.kind.length > 0
  const result: ResearchResult | null = hasResult
    ? {
        kind: o.kind as ResultKind,
        status: (str(o.status, 'insufficient') || 'insufficient') as ResultStatus,
        summary: str(o.summary),
        findings: Array.isArray(o.findings) ? (o.findings as ResearchResult['findings']) : [],
        evidence: Array.isArray(o.evidence) ? (o.evidence as ResearchResult['evidence']) : [],
        source_locations: Array.isArray(o.source_locations)
          ? (o.source_locations as ResearchResult['source_locations'])
          : [],
        patches: Array.isArray(o.patches) ? (o.patches as ResearchResult['patches']) : [],
        actions: Array.isArray(o.actions) ? (o.actions as ResearchResult['actions']) : [],
        confidence: typeof o.confidence === 'number' ? o.confidence : 0,
      }
    : null

  return {
    task: { task_id: taskId, status, activity, result },
    result,
  }
}

export async function getTask(taskId: string): Promise<AgentTaskDetail> {
  const raw = await rwGet<unknown>(`/agent/tasks/${taskId}`)
  return normalizeTaskDetail(raw)
}

/** 恢复用的任务详情：在归一结果上补出 `preset`（面板要靠它决定展开哪张能力卡）。 */
export interface RestoredTask extends AgentTaskDetail {
  preset: AgentPreset | null
}

/**
 * 项目**最近的 Agent 任务**（含结果）。
 *
 * 用途：重新进入项目时把**上次的能力结果**还原到面板，而不是每次都从空态开始 ——
 * 宪法 §84「再次打开 Project 应尽量恢复必要 panel state」、
 * §54「用户关闭 Accordion 后 Task 不神秘消失」。
 *
 * 后端 `GET /projects/{id}/agent/tasks?limit=n` 返回 `{ items: [{ ...result, task }] }`。
 */
export async function listTasks(projectId: string, limit = 1): Promise<RestoredTask[]> {
  const r = await rwGet<{ items?: unknown[] }>(`/projects/${projectId}/agent/tasks?limit=${limit}`)
  const items = Array.isArray(r?.items) ? r.items : []
  return items.map((it) => {
    const o = (it && typeof it === 'object' ? it : {}) as Record<string, unknown>
    const meta = (o.task && typeof o.task === 'object' ? o.task : {}) as Record<string, unknown>
    const preset = str(meta.preset, '')
    return { ...normalizeTaskDetail(it), preset: (preset || null) as AgentPreset | null }
  })
}

/** 拉取任务的结构化结果（部分后端把结果单独挂在 result 端点）。 */
export async function getTaskResult(taskId: string): Promise<ResearchResult> {
  return rwGet<ResearchResult>(`/agent/tasks/${taskId}/result`)
}

export async function searchKnowledge(
  projectId: string,
  query: string,
  limit = 8,
): Promise<KnowledgeHit[]> {
  const r = await rwPost<{ items: KnowledgeHit[] }>(`/projects/${projectId}/knowledge/search`, {
    query,
    limit,
  })
  return r?.items ?? []
}

export interface StreamTaskOptions {
  signal?: AbortSignal
  /** 断线重连起点（fetch-event-source 亦会跨重试自动携带） */
  lastEventId?: string
  /** 覆盖默认 stream 路径 */
  path?: string
  onEvent: (ev: AgentStreamEvent) => void
}

/**
 * 订阅任务 SSE。返回 `close()` 以主动断开（组件卸载时调用）。
 *
 * 设计：`onmessage` 把每帧 JSON 解析为 `AgentStreamEvent`；解析失败时降级为一条
 * 纯文本 activity（仍不承载推理链）。
 *
 * ⚠️ 当前后端（2026-09）**未提供** `/agent/tasks/{id}/stream` 端点，故状态机
 * 默认走 `GET` 轮询；本函数保留供后端补齐 SSE 后启用（见 `useAgentTask`）。
 */
export function streamTask(taskId: string, options: StreamTaskOptions): () => void {
  const ctrl = new AbortController()
  const { signal, onEvent } = options
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true })
  }

  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId

  const path = options.path ?? `${RESEARCH_API_BASE}/agent/tasks/${taskId}/stream`

  void fetchEventSource(path, {
    method: 'GET',
    headers,
    signal: ctrl.signal,
    openWhenHidden: true,
    onmessage(ev: { event?: string; data: string; id?: string }) {
      const raw = ev.data
      if (!raw) return
      let parsed: AgentStreamEvent | null = null
      try {
        parsed = JSON.parse(raw) as AgentStreamEvent
      } catch {
        parsed = { type: 'activity', activity: raw, id: ev.id }
      }
      if (parsed && !parsed.id && ev.id) parsed.id = ev.id
      if (parsed) onEvent(parsed)
    },
    onerror(err: unknown) {
      // 抛出即终止自动重试，交由上层决定（避免无限重连风暴）。
      throw err
    },
  })

  return () => ctrl.abort()
}
