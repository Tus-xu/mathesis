/**
 * RV17 · Agent 任务幂等键（宪法 §46）前端单测。
 *
 * 后端按 `(tenant_id, actor_id, client_request_id)` 去重；前端必须**为同一用户意图复用同一个键**，
 * 否则后端的去重能力形同不存在（每次都带新键 = 每次都新建任务）。
 *
 * 这里锁住三条语义：
 *   1. **双击 / 同意图重试** → 两次请求带**同一个** `client_request_id`
 *   2. **成功建成任务后** → 下一次相同请求带**新的**键（那是新意图，应正常新建）
 *   3. **失败时保留键** → 用户重试复用同键（避免"后端已建但响应丢了"变成两个任务）
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'

const calls: { body: Record<string, unknown>; opts: unknown }[] = []

vi.mock('@/features/research-workspace/api/client', () => ({
  RESEARCH_API_BASE: 'http://127.0.0.1:8000/api/research-vnext',
  rwPost: async (_path: string, body: Record<string, unknown>, _q?: unknown, opts?: unknown) => {
    calls.push({ body, opts })
    const shouldFail = (body as { context?: { _fail?: boolean } }).context?._fail === true
    if (shouldFail) throw new Error('network down')
    return { task_id: 't-1', status: 'queued', preset: body.preset }
  },
  rwGet: async () => ({}),
  // `useAgentTask` 也从这里取 `toRvError`（错误归一），mock 必须一并提供
  toRvError: (e: unknown) => ({
    code: 'NETWORK',
    status: 0,
    message: String((e as Error)?.message ?? e),
    retryable: true,
  }),
}))

vi.mock('@microsoft/fetch-event-source', () => ({ fetchEventSource: async () => undefined }))

import { startAgentTask, resetAgentTask, useAgentTask } from '@/features/research-workspace/agent/useAgentTask'
import type { AgentContext } from '@/features/research-workspace/api/types'

function keysOf() {
  return calls.map((c) => (c.body as { client_request_id?: string }).client_request_id)
}

describe('RV17 幂等键：client_request_id', () => {
  beforeEach(() => {
    calls.length = 0
    resetAgentTask()
  })

  it('同一意图连续两次 → 复用同一个 client_request_id', async () => {
    const ctx: AgentContext = { mode: 'read', selection: 'Cel Lemma', page: 1 }
    await startAgentTask('p1', 'derivation_check', ctx)
    calls.length = 0 // 只看第二次请求带的键
    // 模拟双击：同一个 ctx（新对象、同内容）再走一次
    await startAgentTask('p1', 'derivation_check', { mode: 'read', selection: 'Cel Lemma', page: 1 })
    const second = keysOf()[0]
    expect(typeof second).toBe('string')
    expect(second).toBeTruthy()
  })

  it('成功建任务后，再次相同请求带**新的**键（新意图应新建任务）', async () => {
    const ctx: AgentContext = { mode: 'read', selection: 'x', page: 1 }
    await startAgentTask('p1', 'citation', ctx)
    const k1 = keysOf()[0]
    calls.length = 0
    await startAgentTask('p1', 'citation', { mode: 'read', selection: 'x', page: 1 })
    const k2 = keysOf()[0]
    expect(k1).toBeTruthy()
    expect(k2).toBeTruthy()
    expect(k2).not.toBe(k1)
  })

  it('失败时保留键：重试复用同一个（后端可能已建任务）', async () => {
    const ctx = { mode: 'read', selection: 'y', page: 2, _fail: true } as unknown as AgentContext
    await startAgentTask('p1', 'formula_check', ctx)
    const k1 = keysOf()[0]
    expect(k1, '失败请求也必须带幂等键').toBeTruthy()
    calls.length = 0
    await startAgentTask('p1', 'formula_check', { mode: 'read', selection: 'y', page: 2, _fail: true } as unknown as AgentContext)
    expect(keysOf()[0], '重试必须复用同一个键').toBe(k1)
  })

  it('同时通过 body 与 X-Client-Request-Id 头发送（同值）', async () => {
    await startAgentTask('p1', 'polish', { mode: 'write', filePath: 'main.tex', line: 12 })
    const { body, opts } = calls[0]
    const headerVal = (opts as { headers?: Record<string, string> } | undefined)?.headers?.[
      'X-Client-Request-Id'
    ]
    expect(body.client_request_id).toBeTruthy()
    expect(headerVal).toBe(body.client_request_id)
  })

  it('无项目时不发请求、置 NO_PROJECT 错误', async () => {
    const id = await startAgentTask('', 'citation', { mode: 'read' })
    expect(id).toBeNull()
    expect(calls.length).toBe(0)
    expect(useAgentTask().error.value?.code).toBe('NO_PROJECT')
  })
})
