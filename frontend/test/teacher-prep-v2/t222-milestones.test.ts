/**
 * T22.2 · 动态 Milestone Registry 契约测试。
 *
 * 钉死 DESIGN-CORRECTION-R2 §1-2：
 *  - 不用固定 "4/6"；未启用的里程碑**不进分母**；
 *  - 后端无端点的里程碑（profile / deck）**不进 registry**（不造假）；
 *  - lesson_plan（L-221-2）在蓝图 confirmed 后进入；
 *  - 状态由服务端事实派生。
 */

import { describe, expect, it } from 'vitest'
import {
  buildMilestones,
  enabledMilestoneKeys,
  MILESTONE_KEYS,
} from '@/features/teacher-prep-v2/projection/milestones'
import type { ProjectionFacts } from '@/features/teacher-prep-v2/projection/types'

const BASE: ProjectionFacts = {
  loading: false,
  errorKind: null,
  sessionStatus: 'active',
  sessionRevision: 1,
  pendingActionId: null,
  contextSnapshotId: null,
  blueprintStatus: null,
  blueprintId: null,
  blueprintRevision: null,
  blueprintTitle: null,
  diagnosticStatus: null,
  diagnosticId: null,
  lessonPlanId: null,
  lessonPlanRevision: null,
  hasRunningJob: false,
  hasFailedJob: false,
  basicMode: false,
  hasAssistantMessage: false,
  timelineLength: 0,
}

describe('T22.2 · 动态 milestone：未启用项不进分母', () => {
  it('全新会话：只有教材上下文进 registry（分母 = 1，不是 6）', () => {
    const s = buildMilestones(BASE)
    expect(s.items.map((i) => i.key)).toEqual(['textbook_context'])
    expect(s.readyCount).toBe(0)
    expect(s.pendingCount).toBe(1)
    expect(s.label).toBe('0 项已就绪 · 1 项待确认')
  })

  it('L-221-2：lesson_plan 在蓝图 confirmed 后进入 registry；deck/profile 仍不进', () => {
    const s = buildMilestones({
      ...BASE,
      contextSnapshotId: 'cs-1',
      blueprintId: 'bp-1',
      blueprintStatus: 'confirmed',
      diagnosticId: 'dg-1',
      diagnosticStatus: 'published',
    })
    const keys = s.items.map((i) => i.key)
    expect(keys).toContain('lesson_plan')
    expect(keys).not.toContain('profile')
    expect(keys).not.toContain('deck')
    // context + blueprint + diagnostic + lesson_plan（未投影时 pending）
    expect(s.items.length).toBe(4)
    expect(s.label).toBe('3 项已就绪 · 1 项待确认')
  })

  it('诊断未启用时不进分母（分母随本节课实际内容变化）', () => {
    const s = buildMilestones({ ...BASE, contextSnapshotId: 'cs-1' })
    expect(s.items.length).toBe(1)
    expect(s.label).toBe('1 项已就绪 · 0 项待确认')
  })
})

describe('T22.2 · milestone 状态由服务端事实派生', () => {
  it('教材上下文：有 snapshot → ready；无且在跑任务 → generating', () => {
    expect(buildMilestones({ ...BASE, contextSnapshotId: 'cs-9' }).items[0].state).toBe('ready')
    expect(buildMilestones({ ...BASE, hasRunningJob: true }).items[0].state).toBe('generating')
    expect(buildMilestones(BASE).items[0].state).toBe('pending')
  })

  it('蓝图：confirmed → ready；proposal → confirming；rejected → pending', () => {
    const mk = (st: string, pa: string | null = null) =>
      buildMilestones({
        ...BASE,
        blueprintId: 'bp-1',
        blueprintStatus: st,
        pendingActionId: pa,
      }).items.find((i) => i.key === 'blueprint')!.state
    expect(mk('confirmed')).toBe('ready')
    expect(mk('proposal', 'pa-1')).toBe('confirming')
    expect(mk('rejected')).toBe('pending')
  })

  it('蓝图 revision 作为 detail 展示（来自服务端 DTO）', () => {
    const item = buildMilestones({
      ...BASE,
      blueprintId: 'bp-1',
      blueprintStatus: 'confirmed',
      blueprintRevision: 3,
    }).items.find((i) => i.key === 'blueprint')!
    expect(item.detail).toBe('revision 3')
  })

  it('诊断：published/draft → ready；proposal → confirming', () => {
    const mk = (st: string) =>
      buildMilestones({ ...BASE, diagnosticId: 'dg-1', diagnosticStatus: st })
        .items.find((i) => i.key === 'diagnostic')!.state
    expect(mk('published')).toBe('ready')
    expect(mk('draft')).toBe('ready')
    expect(mk('proposal')).toBe('confirming')
  })
})

describe('T22.2 · registry 完整性', () => {
  it('所有 key 都有定义且顺序稳定（教材 → 诊断 → 学情 → 蓝图 → 教案 → PPT）', () => {
    expect([...MILESTONE_KEYS]).toEqual([
      'textbook_context',
      'diagnostic',
      'profile',
      'blueprint',
      'lesson_plan',
      'deck',
    ])
  })

  it('enabledMilestoneKeys 与 buildMilestones 一致', () => {
    const f = { ...BASE, contextSnapshotId: 'cs-1', blueprintId: 'bp-1', blueprintStatus: 'proposal' }
    expect(enabledMilestoneKeys(f)).toEqual(buildMilestones(f).items.map((i) => i.key))
  })

  it('label 不含 "N/6" 这类固定分母（避免像 Wizard）', () => {
    expect(buildMilestones(BASE).label).not.toMatch(/\d\s*\/\s*\d/)
  })
})
