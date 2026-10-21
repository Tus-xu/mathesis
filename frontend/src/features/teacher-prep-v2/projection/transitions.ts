/**
 * T22.1 · 合法状态转换表（纯函数、表驱动）。
 *
 * 铁律（AGENTS.md / 8100 state_machines.py 同构）：
 * - **表驱动**，禁止散落 if/else 业务分支；
 * - 非法转换 → 抛错（测试钉死），不允许前端"猜"流程；
 * - `artifact_ready → artifact_focus` 需要 extra guard：只有教师主动打开才合法。
 */

import type { SessionPhase, SurfaceMode } from './types'

/** UX-CONTRACT §2 允许的核心转换。 */
export const SESSION_TRANSITIONS: Record<SessionPhase, readonly SessionPhase[]> = {
  empty: ['clarifying', 'degraded', 'forbidden', 'not_found'],
  clarifying: ['context_ready', 'proposal_pending', 'degraded', 'forbidden', 'not_found'],
  context_ready: ['clarifying', 'proposal_pending', 'waiting_confirmation', 'degraded', 'forbidden', 'not_found'],
  proposal_pending: ['waiting_confirmation', 'clarifying', 'context_ready', 'degraded', 'forbidden', 'not_found'],
  waiting_confirmation: ['generating', 'clarifying', 'context_ready', 'degraded', 'forbidden', 'not_found'],
  generating: ['artifact_ready', 'degraded', 'proposal_pending', 'forbidden', 'not_found'],
  artifact_ready: ['revising', 'completed', 'clarifying', 'degraded', 'forbidden', 'not_found'],
  revising: ['artifact_ready', 'degraded', 'forbidden', 'not_found'],
  completed: ['clarifying', 'artifact_ready', 'degraded', 'forbidden', 'not_found'],
  degraded: ['clarifying', 'context_ready', 'forbidden', 'not_found'],
  forbidden: [],
  not_found: [],
}

export class IllegalTransitionError extends Error {
  constructor(from: SessionPhase, to: SessionPhase) {
    super(`illegal session phase transition: ${from} -> ${to}`)
    this.name = 'IllegalTransitionError'
  }
}

export function canTransition(from: SessionPhase, to: SessionPhase): boolean {
  if (from === to) return true
  return SESSION_TRANSITIONS[from].includes(to)
}

/** 非法即抛（供 store 与测试共用）。 */
export function assertTransition(from: SessionPhase, to: SessionPhase): void {
  if (!canTransition(from, to)) throw new IllegalTransitionError(from, to)
}

/**
 * surfaceMode 转换守卫。
 * - `conversation → artifact_focus`：**必须** `byTeacher === true` 且当前 phase 允许；
 * - `artifact_focus → conversation`：总是合法（关闭画布是教师自由）。
 */
export function canSwitchSurface(
  from: SurfaceMode,
  to: SurfaceMode,
  opts: { byTeacher: boolean; phase: SessionPhase },
): boolean {
  if (from === to) return true
  if (to === 'artifact_focus') {
    if (!opts.byTeacher) return false
    // 「查看并修改」只在产物已成熟时出现（UX-CONTRACT C/D）
    return opts.phase === 'artifact_ready' || opts.phase === 'revising' || opts.phase === 'completed'
  }
  // to === 'conversation'：关闭画布恒合法
  return true
}

/** 允许出现「查看并修改」入口的 phase。 */
export const FOCUS_ALLOWED_PHASES: readonly SessionPhase[] = [
  'artifact_ready',
  'revising',
  'completed',
]
