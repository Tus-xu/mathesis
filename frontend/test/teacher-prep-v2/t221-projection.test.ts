/**
 * T22.1 · UI projection 契约测试（阶段 2：先写测试）。
 *
 * 钉死 UX-CONTRACT §2 与 UI-STATE-MATRIX 的不变式：
 *  - 无 artifact 不得出现画布 / 「查看并修改」
 *  - 默认 surfaceMode 恒为 conversation（三栏数 = 0）
 *  - 403 不得出现「已就绪」类阶段，且必须聚焦式单状态
 *  - 同一时刻主错误只呈现一次（overlay 强制清空）
 *  - artifact_focus 只能由教师主动打开
 */

import { describe, expect, it } from 'vitest'

import {
  deriveActiveArtifact,
  deriveSessionPhase,
  deriveStreamPhase,
  factsFromSnapshot,
  isArtifactEditable,
  isArtifactFocusable,
  projectWorkspace,
} from '@/features/teacher-prep-v2/projection/project'
import {
  assertTransition,
  canSwitchSurface,
  canTransition,
  IllegalTransitionError,
} from '@/features/teacher-prep-v2/projection/transitions'
import type { ProjectionFacts, ProjectionUi } from '@/features/teacher-prep-v2/projection/types'

const BASE_FACTS: ProjectionFacts = {
  loading: false,
  errorKind: null,
  sessionStatus: 'active',
  sessionRevision: 3,
  pendingActionId: null,
  contextSnapshotId: null,
  blueprintStatus: null,
  blueprintId: null,
  blueprintRevision: null,
  blueprintTitle: null,
  diagnosticStatus: null,
  diagnosticId: null,
  hasRunningJob: false,
  hasFailedJob: false,
  basicMode: false,
  hasAssistantMessage: false,
  timelineLength: 0,
}

const BASE_UI: ProjectionUi = {
  focusRequested: false,
  activeOverlay: 'none',
  dockDismissed: false,
}

const STREAM = { connected: true, reconnecting: false, gapCount: 0 }

describe('T22.1 · 无 artifact 不渲染画布', () => {
  it('无 blueprint / diagnostic 时 hasArtifact=false 且 canOpenFocus=false', () => {
    const p = projectWorkspace(BASE_FACTS, BASE_UI, STREAM)
    expect(p.hasArtifact).toBe(false)
    expect(p.activeArtifact).toBeNull()
    expect(p.canOpenFocus).toBe(false)
  })

  it('教师即便主动请求 focus，无 artifact 时仍不得进入 artifact_focus', () => {
    const p = projectWorkspace(BASE_FACTS, { ...BASE_UI, focusRequested: true }, STREAM)
    expect(p.surfaceMode).toBe('conversation')
  })

  it('blueprint 处于 proposal（未确认）时不算成熟产物', () => {
    const p = projectWorkspace(
      { ...BASE_FACTS, blueprintId: 'bp-1', blueprintStatus: 'proposal', blueprintRevision: 1 },
      { ...BASE_UI, focusRequested: true },
      STREAM,
    )
    expect(p.hasArtifact).toBe(false)
    expect(p.canOpenFocus).toBe(false)
    expect(p.surfaceMode).toBe('conversation')
    expect(p.sessionPhase).toBe('proposal_pending')
  })
})

describe('T22.1 · artifact_ready 才出现「查看并修改」', () => {
  const confirmed: ProjectionFacts = {
    ...BASE_FACTS,
    blueprintId: 'bp-1',
    blueprintStatus: 'confirmed',
    blueprintRevision: 2,
    blueprintTitle: '二次函数单元蓝图',
    hasAssistantMessage: true,
  }

  it('confirmed blueprint 本身不再是可打开产物（大纲预览已删除）', () => {
    const p = projectWorkspace(confirmed, BASE_UI, STREAM)
    // 2026-09-14 备课中心简化：大纲（teaching_blueprint）不再成为 activeArtifact，
    // 因此没有产物入口、也不会把教师引向已删除的大纲预览界面。
    expect(p.hasArtifact).toBe(false)
    expect(p.canOpenFocus).toBe(false)
    expect(p.activeArtifact).toBeNull()
  })

  it('教师主动打开才进入 artifact_focus', () => {
    const deckFacts: ProjectionFacts = { ...BASE_FACTS, teachingDeckId: 'deck-1', teachingDeckRevision: 3 }
    expect(projectWorkspace(deckFacts, BASE_UI, STREAM).surfaceMode).toBe('conversation')
    expect(
      projectWorkspace(deckFacts, { ...BASE_UI, focusRequested: true }, STREAM).surfaceMode,
    ).toBe('artifact_focus')
  })

  it('诊断 draft 也算可编辑产物', () => {
    const p = projectWorkspace(
      { ...BASE_FACTS, diagnosticId: 'dg-1', diagnosticStatus: 'draft' },
      BASE_UI,
      STREAM,
    )
    expect(p.hasArtifact).toBe(true)
    expect(p.activeArtifact?.artifact_type).toBe('diagnostic_task')
  })

  it('教案 / 课件 Spec 无 8100 端点 → 不可编辑（不造假完成）', () => {
    expect(isArtifactEditable('teaching_blueprint')).toBe(true)
    expect(isArtifactEditable('diagnostic_task')).toBe(true)
    expect(isArtifactEditable('lesson_plan_spec')).toBe(false)
    expect(isArtifactEditable('teaching_deck_spec')).toBe(false)
    // 2026-09-14：大纲/教案不再可聚焦（对应预览界面已删除）；只有课件与诊断可聚焦。
    expect(isArtifactFocusable('lesson_plan_spec')).toBe(false)
    expect(isArtifactFocusable('teaching_blueprint')).toBe(false)
    expect(isArtifactFocusable('teaching_deck_spec')).toBe(true)
    expect(isArtifactFocusable('diagnostic_task')).toBe(true)
  })

  it('TeachingDeckSpec 可进入 T24 focus，但不变成 8100 可编辑端点', () => {
    const p = projectWorkspace(
      { ...BASE_FACTS, teachingDeckId: 'deck-spec-1', teachingDeckRevision: 3 },
      { ...BASE_UI, focusRequested: true },
      STREAM,
    )
    expect(p.sessionPhase).toBe('artifact_ready')
    expect(p.activeArtifact?.artifact_type).toBe('teaching_deck_spec')
    expect(p.canOpenFocus).toBe(true)
    expect(p.surfaceMode).toBe('artifact_focus')
  })

  it('只有教案 spec 时不再产生任何可达产物（教案预览已删除）', () => {
    const p = projectWorkspace(
      { ...BASE_FACTS, lessonPlanId: 'lp-1', lessonPlanRevision: 2 },
      { ...BASE_UI, focusRequested: true },
      STREAM,
    )
    expect(p.sessionPhase).toBe('artifact_ready')
    expect(p.activeArtifact).toBeNull()
    expect(p.canOpenFocus).toBe(false)
    expect(p.surfaceMode).toBe('conversation')
  })
})

describe('T22.1 · 403 聚焦式单状态（禁止「403 + 已就绪」）', () => {
  it('forbidden → sessionPhase=forbidden，不渲染对话舞台，overlay 清空', () => {
    const p = projectWorkspace(
      { ...BASE_FACTS, errorKind: 'forbidden' },
      { ...BASE_UI, activeOverlay: 'evidence', focusRequested: true },
      STREAM,
    )
    expect(p.sessionPhase).toBe('forbidden')
    expect(p.focusedError).toBe('forbidden')
    expect(p.showConversation).toBe(false)
    // 同一时刻主错误只呈现一次 → 辅助面板强制关闭
    expect(p.activeOverlay).toBe('none')
    // 403 阶段不得落入 artifact_ready / completed 等"就绪"语义
    expect(['artifact_ready', 'completed']).not.toContain(p.sessionPhase)
  })

  it('即便服务端同时报了 confirmed blueprint，403 仍优先', () => {
    const p = projectWorkspace(
      {
        ...BASE_FACTS,
        errorKind: 'forbidden',
        blueprintId: 'bp-1',
        blueprintStatus: 'confirmed',
      },
      { ...BASE_UI, focusRequested: true },
      STREAM,
    )
    expect(p.sessionPhase).toBe('forbidden')
    expect(p.surfaceMode).toBe('conversation')
    expect(p.showConversation).toBe(false)
    expect(p.showDock).toBe(false)
  })

  it('404 / 401 / 409 各自归一化为聚焦态', () => {
    expect(projectWorkspace({ ...BASE_FACTS, errorKind: 'not_found' }, BASE_UI, STREAM).focusedError).toBe('not_found')
    expect(projectWorkspace({ ...BASE_FACTS, errorKind: 'unauthenticated' }, BASE_UI, STREAM).focusedError).toBe('unauthenticated')
    expect(projectWorkspace({ ...BASE_FACTS, errorKind: 'conflict' }, BASE_UI, STREAM).focusedError).toBe('conflict')
  })

  it('409 不占用 streamPhase=error（仍可对账），但主错误仍是 conflict', () => {
    const p = projectWorkspace({ ...BASE_FACTS, errorKind: 'conflict' }, BASE_UI, STREAM)
    expect(p.focusedError).toBe('conflict')
    expect(p.streamPhase).not.toBe('error')
  })
})

describe('T22.1 · sessionPhase 由服务端事实派生（前端不猜）', () => {
  it('empty → clarifying → context_ready → proposal_pending', () => {
    expect(deriveSessionPhase(BASE_FACTS)).toBe('empty')
    expect(deriveSessionPhase({ ...BASE_FACTS, hasAssistantMessage: true })).toBe('clarifying')
    expect(deriveSessionPhase({ ...BASE_FACTS, contextSnapshotId: 'cs-1' })).toBe('context_ready')
    expect(deriveSessionPhase({ ...BASE_FACTS, blueprintStatus: 'proposal' })).toBe('proposal_pending')
  })

  it('pendingActionId → waiting_confirmation（优先于其它事实）', () => {
    expect(
      deriveSessionPhase({ ...BASE_FACTS, pendingActionId: 'pa-1', contextSnapshotId: 'cs-1' }),
    ).toBe('waiting_confirmation')
  })

  it('running job → generating', () => {
    expect(deriveSessionPhase({ ...BASE_FACTS, hasRunningJob: true })).toBe('generating')
  })
})

describe('T22.1 · streamPhase', () => {
  it('loading → connecting；connected → live', () => {
    expect(deriveStreamPhase({ loading: true, connected: false, reconnecting: false, gapCount: 0, errorKind: null })).toBe('connecting')
    expect(deriveStreamPhase({ loading: false, connected: true, reconnecting: false, gapCount: 0, errorKind: null })).toBe('live')
  })

  it('gap>0 → stale；reconnecting → reconnecting', () => {
    expect(deriveStreamPhase({ loading: false, connected: true, reconnecting: false, gapCount: 3, errorKind: null })).toBe('stale')
    expect(deriveStreamPhase({ loading: false, connected: false, reconnecting: true, gapCount: 0, errorKind: null })).toBe('reconnecting')
  })
})

describe('T22.1 · 合法转换表', () => {
  it('允许的核心链路', () => {
    expect(canTransition('empty', 'clarifying')).toBe(true)
    expect(canTransition('clarifying', 'context_ready')).toBe(true)
    expect(canTransition('context_ready', 'proposal_pending')).toBe(true)
    expect(canTransition('proposal_pending', 'waiting_confirmation')).toBe(true)
    expect(canTransition('waiting_confirmation', 'generating')).toBe(true)
    expect(canTransition('generating', 'artifact_ready')).toBe(true)
    expect(canTransition('artifact_ready', 'revising')).toBe(true)
    expect(canTransition('revising', 'artifact_ready')).toBe(true)
  })

  it('非法转换抛错：不得跳过 confirmation', () => {
    expect(canTransition('proposal_pending', 'generating')).toBe(false)
    expect(() => assertTransition('proposal_pending', 'generating')).toThrow(IllegalTransitionError)
    expect(canTransition('empty', 'artifact_ready')).toBe(false)
  })

  it('forbidden / not_found 为终态', () => {
    expect(canTransition('forbidden', 'clarifying')).toBe(false)
    expect(canTransition('not_found', 'empty')).toBe(false)
  })

  it('surfaceMode：非教师主动不得进 focus', () => {
    expect(canSwitchSurface('conversation', 'artifact_focus', { byTeacher: false, phase: 'artifact_ready' })).toBe(false)
    expect(canSwitchSurface('conversation', 'artifact_focus', { byTeacher: true, phase: 'artifact_ready' })).toBe(true)
    expect(canSwitchSurface('conversation', 'artifact_focus', { byTeacher: true, phase: 'clarifying' })).toBe(false)
    // 关闭画布恒合法
    expect(canSwitchSurface('artifact_focus', 'conversation', { byTeacher: false, phase: 'artifact_ready' })).toBe(true)
  })
})

describe('T22.1 · factsFromSnapshot 字段映射（可审计）', () => {
  it('从真实 snapshot 结构抽取事实', () => {
    const snapshot = {
      session: {
        id: 's-1',
        status: 'active',
        revision: 7,
        last_event_seq: 12,
        pending_action_id: null,
        context_snapshot_id: 'cs-9',
      },
      timeline: [{ role: 'assistant' }, { role: 'user' }],
      artifact: {
        context: null,
        blueprint: { blueprint_id: 'bp-1', status: 'confirmed', revision: 4, title: 'T' },
        diagnostic: null,
      },
      evidence: [],
      available_actions: [],
      job_summary: [{ job_id: 'j1', job_type: 'x', status: 'running', attempt: 1, max_attempts: 3, updated_at: null, last_error: null }],
      basic_mode: false,
    } as never

    const f = factsFromSnapshot({ snapshot, loading: false, errorKind: null })
    expect(f.sessionRevision).toBe(7)
    expect(f.contextSnapshotId).toBe('cs-9')
    expect(f.blueprintStatus).toBe('confirmed')
    expect(f.blueprintRevision).toBe(4)
    expect(f.hasRunningJob).toBe(true)
    expect(f.hasAssistantMessage).toBe(true)
    // pendingAction 优先 → waiting_confirmation
    expect(deriveSessionPhase({ ...f, pendingActionId: 'pa-1' })).toBe('waiting_confirmation')
    // 无 pending → running job 优先 → generating
    expect(deriveSessionPhase(f)).toBe('generating')
    // 2026-09-14：confirmed blueprint 不再产生可打开产物（大纲预览已删除）
    expect(deriveActiveArtifact(f)).toBeNull()
    expect(
      deriveActiveArtifact({
        ...f,
        teachingDeckId: 'deck-1',
        teachingDeckRevision: 5,
      })?.revision,
    ).toBe(5)
  })

  it('服务端大写作业状态可驱动生成态与失败态', () => {
    const snapshot = {
      session: { id: 's-1', status: 'active', revision: 1, last_event_seq: 0 },
      timeline: [],
      artifact: { context: null, blueprint: null, diagnostic: null },
      evidence: [],
      available_actions: [],
      job_summary: [{ job_id: 'j1', job_type: 'conversation.run', status: 'CLAIMED', attempt: 1, max_attempts: 3, updated_at: null, last_error: null }],
      basic_mode: false,
    } as never

    const running = factsFromSnapshot({ snapshot, loading: false, errorKind: null })
    expect(running.hasRunningJob).toBe(true)
    expect(deriveSessionPhase(running)).toBe('generating')

    // `snapshot` 故意用 never 断言以容纳任意服务端形态；这里显式读作业列表再改写。
    const jobs = (snapshot as unknown as { job_summary: Array<{ status: string }> }).job_summary
    jobs[0].status = 'DLQ'
    const failed = factsFromSnapshot({ snapshot, loading: false, errorKind: null })
    expect(failed.hasRunningJob).toBe(false)
    expect(failed.hasFailedJob).toBe(true)
  })

  it('snapshot 为 null（未加载）→ 全部空事实，不编造', () => {
    const f = factsFromSnapshot({ snapshot: null, loading: true, errorKind: null })
    expect(f.blueprintStatus).toBeNull()
    expect(f.sessionRevision).toBeNull()
    expect(f.hasRunningJob).toBe(false)
    expect(deriveSessionPhase(f)).toBe('empty')
  })
})
