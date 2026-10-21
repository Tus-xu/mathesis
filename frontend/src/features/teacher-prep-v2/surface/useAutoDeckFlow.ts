/**
 * 备课中心简化 · AI 生成 → PPT 编辑工作台的自动推进（2026-09-14）。
 *
 * 教师要求：删除「大纲预览」与「教案预览」两个中间环节，
 * 在 AI 生成内容结束后**直接**把课件落到 PPT 编辑工作台。
 *
 * 设计取舍（重要）：
 * - 本模块**不发明业务**：它只按顺序执行 8100 通过 `available_actions` 下发的真实动作
 *   （`confirm_proposal` → `project_specs`），因此服务端仍是唯一事实源，
 *   不可变修订行、projection_hash 幂等、lineage 与审计全部照旧；
 *   "零点击" 只是把原本由教师点的那两下交给前端按同一契约发起。
 * - 每一步都**只在服务端确实下发该动作时**才执行；动作消失即认为该步已完成或不再适用，
 *   模块不会重试、不会兜底猜测，也不会因为没有动作而卡死。
 * - 已推进过的步骤按 `session/blueprint/revision/deck` 记在模块内存里（进程内，
 *   不落 localStorage —— 业务事实不得落盘）。刷新页面后若课件已存在，则直接进入工作台。
 * - 任何失败（网络 / 服务端拒绝）都会停止后续推进并把原因交给调用方展示，
 *   绝不静默吞掉，也绝不假装已经生成好。
 */
import { ref, watch, type Ref } from 'vue'

import type { SessionPhase } from '@/features/teacher-prep-v2/projection/types'
import type { ActionView, WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

/** 与 `ConversationStage.actionPayload` 保持同一契约：一次动作请求双 Spec，共享 lineage。 */
export const PROJECT_SPECS_KINDS = ['lesson_plan', 'deck'] as const

export type AutoDeckStep = 'confirm_proposal' | 'project_specs' | 'open_deck'

/** 进程内已推进记录：`${sessionId}::${step}::${targetId}::${revision}`。 */
const advancedKeys = new Set<string>()
/** 进程内已自动打开过的课件（同一 SPA 会话内不重复把教师弹进工作台）。 */
const openedDecks = new Set<string>()

/** 仅供测试复位。 */
export function __resetAutoDeckFlowForTest() {
  advancedKeys.clear()
  openedDecks.clear()
}

export interface AutoDeckFlowOptions {
  sessionId: Ref<string | null>
  snapshot: Ref<WorkspaceSnapshot | null>
  actions: Ref<ActionView[]>
  phase: Ref<SessionPhase>
  busy: Ref<boolean>
  /** 执行一个服务端动作（与教师点击走同一条 store 通道）。 */
  run: (action: string, target: string | null, payload?: Record<string, unknown>) => unknown
  /** 把课件交给 PPT 编辑器工作台（内部会调 PPT 内核 ingest）。 */
  openDeck: (sessionId: string, deckId: string) => void
}

export interface AutoDeckFlow {
  /** 已自动推进的步骤名（诊断用，不是业务事实）。 */
  advanced: Ref<AutoDeckStep[]>
  /** 自动推进失败原因；非空表示已停止推进。 */
  error: Ref<string>
  /** 教师点击「重试」：清空错误并允许重新推进（不伪造成功）。 */
  reset: () => void
}

export function useAutoDeckFlow(options: AutoDeckFlowOptions): AutoDeckFlow {
  const advanced = ref<AutoDeckStep[]>([])
  const error = ref('')

  function nextStep(): { step: AutoDeckStep; target: string; revision: number } | null {
    const sessionId = options.sessionId.value
    if (!sessionId) return null

    const deck = options.snapshot.value?.artifact?.teaching_deck as Record<string, unknown> | null | undefined
    const deckId = typeof deck?.spec_id === 'string' && deck.spec_id
      ? deck.spec_id
      : typeof deck?.id === 'string' && deck.id
        ? deck.id
        : ''
    if (deckId) return { step: 'open_deck', target: deckId, revision: 0 }

    const usable = (name: string) =>
      options.actions.value.find((a) => a.action === name && !a.disabled && a.target)

    const confirm = usable('confirm_proposal')
    if (confirm?.target) {
      const revision = Number((options.snapshot.value?.artifact?.blueprint as Record<string, unknown> | null)?.revision ?? 0)
      return { step: 'confirm_proposal', target: confirm.target, revision }
    }

    const specs = usable('project_specs')
    if (specs?.target) {
      const revision = Number((options.snapshot.value?.artifact?.blueprint as Record<string, unknown> | null)?.revision ?? 0)
      return { step: 'project_specs', target: specs.target, revision }
    }

    return null
  }

  async function advance() {
    if (error.value) return
    // 生成/命令在途时不动手，避免和 worker 抢时序。
    if (options.busy.value) return
    if (options.phase.value === 'forbidden' || options.phase.value === 'not_found') return

    const sessionId = options.sessionId.value
    const next = nextStep()
    if (!sessionId || !next) return

    const key = `${sessionId}::${next.step}::${next.target}::${next.revision}`
    if (advancedKeys.has(key)) return
    advancedKeys.add(key)

    if (next.step === 'open_deck') {
      const openKey = `${sessionId}::${next.target}`
      if (openedDecks.has(openKey)) return
      openedDecks.add(openKey)
      advanced.value = [...advanced.value, 'open_deck']
      options.openDeck(sessionId, next.target)
      return
    }

    advanced.value = [...advanced.value, next.step]
    try {
      const payload = next.step === 'project_specs' ? { kinds: [...PROJECT_SPECS_KINDS] } : undefined
      await options.run(next.step, next.target, payload)
    } catch (err) {
      // 诚实失败：停止推进并把原因交回页面展示，不假装已生成。
      advancedKeys.delete(key)
      error.value = err instanceof Error ? err.message : String(err)
    }
  }

  watch(
    [options.sessionId, options.snapshot, options.actions, options.phase, options.busy],
    () => {
      void advance()
    },
    { immediate: true },
  )

  function reset() {
    error.value = ''
    void advance()
  }

  return { advanced, error, reset }
}
