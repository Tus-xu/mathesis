/**
 * 备课中心简化 · 自动推进契约（2026-09-14）。
 *
 * 钉死的产品行为：AI 生成内容结束后，前端按服务端下发的真实动作
 * 自动执行 `confirm_proposal` → `project_specs`，然后把课件送进 PPT 编辑工作台。
 * 教师不再经过「大纲预览」「教案预览」两个中间环节，也不需要点任何按钮。
 *
 * 同时钉死诚实性：只在服务端确实下发该动作时才执行；失败即停并暴露原因；
 * forbidden / not_found 绝不动手；同一个课件不会被反复弹出。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import {
  PROJECT_SPECS_KINDS,
  __resetAutoDeckFlowForTest,
  useAutoDeckFlow,
} from '@/features/teacher-prep-v2/surface/useAutoDeckFlow'
import type { SessionPhase } from '@/features/teacher-prep-v2/projection/types'
import type { ActionView, WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

function snapshotWith(over: {
  lesson_plan?: unknown
  teaching_deck?: unknown
  blueprint?: unknown
}) {
  return {
    session: { id: 's-1', status: 'active', revision: 1, last_event_seq: 0 },
    timeline: [],
    artifact: {
      context: null,
      blueprint: over.blueprint ?? null,
      diagnostic: null,
      lesson_plan: over.lesson_plan ?? null,
      teaching_deck: over.teaching_deck ?? null,
    },
    evidence: [],
    available_actions: [],
    job_summary: [],
    basic_mode: false,
  } as never
}

function actions(...items: Array<Partial<ActionView>>): ActionView[] {
  return items.map((item) => ({ action: '', target: null, copy_key: '', disabled: false, ...item })) as ActionView[]
}

const CONFIRM = { action: 'confirm_proposal', target: 'bp-1', copy_key: 'prep.action.confirm_proposal' }
const PROJECT = { action: 'project_specs', target: 'bp-1', copy_key: 'prep.action.project_specs' }

function mountFlow(init: {
  sessionId?: string | null
  snapshot?: unknown
  actions?: ActionView[]
  phase?: SessionPhase
  busy?: boolean
  run?: (...args: unknown[]) => unknown
  openDeck?: (...args: unknown[]) => unknown
}) {
  const sessionId = ref<string | null>(init.sessionId === undefined ? 's-1' : init.sessionId)
  const snapshot = ref<WorkspaceSnapshot | null>((init.snapshot as WorkspaceSnapshot | null) ?? null)
  const actionList = ref<ActionView[]>(init.actions ?? [])
  const phase = ref<SessionPhase>(init.phase ?? 'proposal_pending')
  const busy = ref(init.busy ?? false)
  const run = vi.fn(init.run ?? (() => Promise.resolve()))
  const openDeck = vi.fn(init.openDeck ?? (() => undefined))
  const flow = useAutoDeckFlow({
    sessionId,
    snapshot,
    actions: actionList,
    phase,
    busy,
    run: run as never,
    openDeck: openDeck as never,
  })
  return { flow, sessionId, snapshot, actionList, phase, busy, run, openDeck }
}

describe('备课中心 · AI 生成结束 → 直达 PPT 编辑工作台', () => {
  beforeEach(() => {
    __resetAutoDeckFlowForTest()
  })

  it('教学方案待确认 → 自动下发 confirm_proposal（教师无需点确认）', () => {
    const { run, openDeck } = mountFlow({
      snapshot: snapshotWith({ blueprint: { blueprint_id: 'bp-1', status: 'proposal', revision: 1 } }),
      actions: actions(CONFIRM, { action: 'reject_proposal', target: 'bp-1' }),
    })
    expect(run).toHaveBeenCalledTimes(1)
    expect(run).toHaveBeenCalledWith('confirm_proposal', 'bp-1', undefined)
    expect(openDeck).not.toHaveBeenCalled()
  })

  it('方案已确认且尚无课件 → 自动下发 project_specs，并携带双 Spec kinds', () => {
    const { run, openDeck } = mountFlow({
      snapshot: snapshotWith({ blueprint: { blueprint_id: 'bp-1', status: 'confirmed', revision: 2 } }),
      actions: actions(PROJECT, { action: 'propose_diagnostic', target: 'bp-1' }),
    })
    expect(run).toHaveBeenCalledTimes(1)
    expect(run).toHaveBeenCalledWith('project_specs', 'bp-1', { kinds: [...PROJECT_SPECS_KINDS] })
    expect(openDeck).not.toHaveBeenCalled()
  })

  it('已有课件 → 直接把课件送进 PPT 编辑工作台，且同一课件只打开一次', async () => {
    const { openDeck, run } = mountFlow({
      snapshot: snapshotWith({ teaching_deck: { spec_id: 'deck-1', revision: 1 } }),
      actions: actions(PROJECT),
    })
    expect(openDeck).toHaveBeenCalledTimes(1)
    expect(openDeck).toHaveBeenCalledWith('s-1', 'deck-1')
    // 课件已在，就不再重复投影
    expect(run).not.toHaveBeenCalled()
  })

  it('教案 spec 单独存在时不会触发任何推进（教案不再有界面环节）', () => {
    const { run, openDeck, flow } = mountFlow({
      snapshot: snapshotWith({ lesson_plan: { spec_id: 'lp-1', revision: 1 } }),
      actions: actions(CONFIRM, PROJECT),
      // 注意：这里故意给出动作，模拟服务端事实与动作并存的边界；
      // 教案的存在本身不得改变推进链路。
    })
    // 仍按服务端动作推进（方案待确认 → 确认），但绝不因此打开任何教案界面
    expect(run).toHaveBeenCalledWith('confirm_proposal', 'bp-1', undefined)
    expect(openDeck).not.toHaveBeenCalled()
    expect(flow.advanced.value).not.toContain('open_deck')
  })

  it('生成/命令在途（busy）时不动手，避免与 worker 抢时序', () => {
    const { run, openDeck } = mountFlow({
      busy: true,
      snapshot: snapshotWith({ blueprint: { blueprint_id: 'bp-1', status: 'proposal', revision: 1 } }),
      actions: actions(CONFIRM),
    })
    expect(run).not.toHaveBeenCalled()
    expect(openDeck).not.toHaveBeenCalled()
  })

  it('forbidden / not_found 下绝不自动推进（403 不得出现任何"就绪"暗示）', () => {
    for (const phase of ['forbidden', 'not_found'] as SessionPhase[]) {
      __resetAutoDeckFlowForTest()
      const { run, openDeck } = mountFlow({
        phase,
        snapshot: snapshotWith({ teaching_deck: { spec_id: 'deck-1', revision: 1 } }),
        actions: actions(CONFIRM, PROJECT),
      })
      expect(run).not.toHaveBeenCalled()
      expect(openDeck).not.toHaveBeenCalled()
    }
  })

  it('动作失败 → 记录原因并停止推进，绝不假装已生成完成', async () => {
    const { run, flow } = mountFlow({
      snapshot: snapshotWith({ blueprint: { blueprint_id: 'bp-1', status: 'proposal', revision: 1 } }),
      actions: actions(CONFIRM),
      run: () => Promise.reject(new Error('服务端拒绝：409 冲突')),
    })
    await nextTick()
    await Promise.resolve()
    expect(flow.error.value).toContain('409 冲突')
    // 失败后即便服务端仍在下发同一动作，也不再自动重试（由教师显式重试）
    expect(run).toHaveBeenCalledTimes(1)
  })

  it('教师点「重试」后允许重新推进', async () => {
    let fail = true
    const { flow, run } = mountFlow({
      snapshot: snapshotWith({ blueprint: { blueprint_id: 'bp-1', status: 'proposal', revision: 1 } }),
      actions: actions(CONFIRM),
      run: () => (fail ? Promise.reject(new Error('boom')) : Promise.resolve()),
    })
    await nextTick()
    await Promise.resolve()
    expect(flow.error.value).toBe('boom')
    fail = false
    flow.reset()
    await nextTick()
    expect(flow.error.value).toBe('')
    expect(run).toHaveBeenCalledTimes(2)
  })

  it('没有会话时不做任何事（不猜测）', () => {
    const { run, openDeck } = mountFlow({
      sessionId: null,
      snapshot: snapshotWith({ teaching_deck: { spec_id: 'deck-1', revision: 1 } }),
      actions: actions(CONFIRM, PROJECT),
    })
    expect(run).not.toHaveBeenCalled()
    expect(openDeck).not.toHaveBeenCalled()
  })
})
