/**
 * T22 · Conversation-first Teacher Workspace —— **红灯测试**（先于实现编写）。
 *
 * 覆盖任务书要求的 7 类失败场景：
 *   1. 路由加载新 Workspace
 *   2. 真实 snapshot（服务端权威，客户端不得自造业务事实）
 *   3. SSE 事件对账（seq 全序 / gap → snapshot 重对账）
 *   4. 刷新恢复（离开后返回、双标签）
 *   5. 401 / 403 / 409 状态分发
 *   6. 断线重连（Last-Event-ID + backoff）
 *   7. 离开后返回（不重复订阅、不丢状态）
 *
 * 纪律：
 * - 本文件**不**断言任何硬编码班级 / 知识点 / 推荐文案 —— 业务事实一律来自 fixture 中的
 *   **服务端形状**载荷（这里用录制的真实 8100 契约形状，不手写业务值）。
 * - 不使用 setTimeout/setInterval 伪造 AI / job 进度（时间由注入的 clock 控制）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import { defineComponent, h, nextTick } from 'vue'

import PrepWorkspace from '@/features/teacher-prep-v2/components/PrepWorkspace.vue'
import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import { createPrepStream } from '@/features/teacher-prep-v2/stream/prepStream'
import { normalizePrepError } from '@/features/teacher-prep-v2/api/prepApi'
import type { WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

// ── 服务端形状 fixture（字段名与 8100 views.build_workspace 一致；值一律为占位符）────
function serverSnapshot(overrides: Partial<WorkspaceSnapshot> = {}): WorkspaceSnapshot {
  return {
    session: { id: 's-1', status: 'active', revision: 7, last_event_seq: 12 },
    timeline: [
      { message_id: 'm-1', seq: 1, role: 'user', kind: 'text', component: { type: 'user_text', props: { text: '__SERVER__' } }, text: '__SERVER__', intent: null, context_snapshot_id: null, blueprint_id: null, run_status: null, error_label: null, error_code: null, editable: false, created_at: '2026-09-12T00:00:00' },
    ],
    artifact: { context: null, blueprint: null, diagnostic: null },
    evidence: [],
    available_actions: [{ action: 'cancel_session', target: null, copy_key: 'common.cancel', disabled: false }],
    job_summary: [],
    basic_mode: false,
    ...overrides,
  }
}

describe('T22 · Conversation-first Teacher Workspace（红灯）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  // ── 1. 路由加载新 Workspace ────────────────────────────────────────────────
  it('1 · 路由加载后三核心区 + 状态区恒定在场，且不是 Stepper/Wizard', async () => {
    const router = createRouter({
      history: createWebHistory(),
      routes: [{ path: '/teacher-v3/prep-v2/:sessionId?', component: PrepWorkspace }],
    })
    router.push('/teacher-v3/prep-v2/s-1')
    await router.isReady()

    const wrapper = mount(
      defineComponent({ render: () => h(PrepWorkspace) }),
      { global: { plugins: [router] } },
    )
    await nextTick()

    for (const id of ['ma-region-left', 'ma-region-center', 'ma-region-right', 'ma-region-status']) {
      expect(wrapper.find(`[data-testid="${id}"]`).exists(), `缺少 region ${id}`).toBe(true)
    }
    // 反 attractor：不得出现步骤条
    expect(wrapper.find('[data-testid="prep-stepper"]').exists()).toBe(false)
    expect(wrapper.html()).not.toMatch(/current[_-]step|wizard/i)
  })

  // ── 2. 真实 snapshot 是唯一权威 ────────────────────────────────────────────
  it('2 · snapshot 落地后 store 不注入任何客户端自造业务事实', async () => {
    const store = usePrepWorkspaceStore()
    const snap = serverSnapshot()
    store.applySnapshot(snap)

    expect(store.snapshot).toEqual(snap)
    expect(store.revision).toBe(7)
    expect(store.lastEventSeq).toBe(12)
    // 客户端不得凭空生成班级 / 知识点 / 推荐
    expect(store.snapshot!.timeline[0]!.text).toBe('__SERVER__')
    expect(store.phase).toBe('ready')
  })

  // ── 3. SSE 事件对账（seq 全序 + gap 触发重对账）────────────────────────────
  it('3 · 事件乱序/缺口时以服务端 snapshot 重对账，不猜中间态', async () => {
    const store = usePrepWorkspaceStore()
    store.applySnapshot(serverSnapshot({ session: { id: 's-1', status: 'active', revision: 7, last_event_seq: 10 } }))

    const resync = vi.fn()
    const stream = createPrepStream({
      sessionId: 's-1',
      transport: {
        open: vi.fn(),
        close: vi.fn(),
      },
      onSnapshot: (s) => store.applySnapshot(s),
      onGap: resync,
      // 与 store.startStream() 的生产接线一致：流侧 seq 回写 Last-Event-ID 基准
      onAppliedSeq: (seq) => store.noteAppliedSeq(seq),
      lastEventId: () => store.lastEventSeq,
    })

    // live 帧 seq=12，而本地 last=10 → 缺口（缺 11）
    stream.__ingest({ id: '12', event: 'event', data: JSON.stringify({ seq: 12, event_type: 'conversation.message.created' }) })
    expect(resync).toHaveBeenCalledTimes(1)

    // seq 连续 → 不触发重对账
    stream.__ingest({ id: '11', event: 'event', data: JSON.stringify({ seq: 11, event_type: 'conversation.message.created' }) })
    expect(resync).toHaveBeenCalledTimes(1)
    expect(store.lastEventSeq).toBe(12)
  })

  // ── 4. 刷新恢复 / 双标签 ──────────────────────────────────────────────────
  it('4 · 刷新后从 snapshot 恢复，并以持久化的 UI 偏好还原面板折叠态', async () => {
    const first = usePrepWorkspaceStore()
    first.setUiPref('evidenceRailCollapsed', true)
    first.applySnapshot(serverSnapshot())

    // 模拟刷新：Pinia 重建 + localStorage 保留 UI 偏好
    setActivePinia(createPinia())
    const second = usePrepWorkspaceStore()
    second.restoreUiPrefs()

    expect(second.ui.evidenceRailCollapsed).toBe(true)
    // 业务事实不得从 localStorage 恢复（必须重新拉服务端）
    expect(second.snapshot).toBeNull()
    expect(second.phase).toBe('loading')
  })

  // ── 5. 401 / 403 / 409 状态分发 ───────────────────────────────────────────
  it('5 · 401/403/409 分别映射为 unauthenticated / forbidden / conflict', () => {
    expect(normalizePrepError({ status: 401, code: 40100 }).kind).toBe('unauthenticated')
    expect(normalizePrepError({ status: 403, code: 40301 }).kind).toBe('forbidden')
    expect(normalizePrepError({ status: 409, code: 40900 }).kind).toBe('conflict')
    expect(normalizePrepError({ status: 500, code: 50000 }).kind).toBe('error')
  })

  it('5b · PARTIAL 不得显示绿色完成', async () => {
    const store = usePrepWorkspaceStore()
    store.applySnapshot(serverSnapshot())
    store.setJobSummary([{ job_id: 'j-1', job_type: 'conversation.run', status: 'PENDING', attempt: 0, max_attempts: 5, updated_at: null, last_error: null }])
    expect(store.connectionTone).not.toBe('success')
    expect(['pending', 'running', 'partial', 'reconnecting']).toContain(store.connectionTone)
  })

  // ── 6. 断线重连 ──────────────────────────────────────────────────────────
  it('6 · 断线后按 backoff 重连，并携带 Last-Event-ID', async () => {
    const seen: Array<string | undefined> = []
    const clock = { sleeps: [] as number[] }
    const stream = createPrepStream({
      sessionId: 's-1',
      lastEventId: () => 42,
      transport: {
        open: vi.fn((req: { headers: Record<string, string> }) => {
          seen.push(req.headers['Last-Event-ID'])
          throw new Error('boom') // 立即失败，触发 backoff
        }),
        close: vi.fn(),
      },
      sleep: async (ms: number) => { clock.sleeps.push(ms) },
      maxRetries: 3,
    })
    await stream.__runUntilStopped()
    expect(seen.every((v) => v === '42')).toBe(true)
    expect(clock.sleeps.length).toBeGreaterThan(0)
    // 指数退避：后续间隔不递减
    expect(clock.sleeps[1]).toBeGreaterThan(clock.sleeps[0])
  })

  // ── 7. 离开后返回 ────────────────────────────────────────────────────────
  it('7 · 离开后返回不重复订阅、不丢已确认状态', async () => {
    const open = vi.fn(() => ({ close: () => {} }))
    const close = vi.fn()
    const stream = createPrepStream({
      sessionId: 's-1',
      transport: { open: open as never, close },
      lastEventId: () => 0,
      sleep: async () => {},
    })
    stream.start()
    stream.stop()
    stream.start()
    stream.stop()
    expect(open.mock.calls.length).toBe(2)
    expect(close.mock.calls.length).toBe(2)
  })
})
