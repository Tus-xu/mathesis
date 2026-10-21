/**
 * R3 生成链（G11 空态出路 / G13 真实进度 / G14 单页重试）· 单例状态机。
 *
 * 链路（PROMPT §5）：老师确认 → PPT/教案同源投影 → 逐页内容生成 → 老师编辑 → 导出。
 * - 触发只用既有 `available_actions`（confirm_proposal / project_specs），不发明动作与目标；
 * - 进度全部来自真实后端：动作请求在途态 + enrich 任务的 durable `progress` 事件 n/N；
 *   禁止前端造数、禁止假进度条；
 * - 失败保留已完成部分：投影/充实失败时已落库产物不回滚，失败页/块只走
 *   ppt_v2 /regen、lesson-plan /regen 单页/单块重试，不整份重生成。
 *
 * 单例：S4（DeckPane）与 S5（LessonPlanPane）共享同一份生成状态（一次点击 → 两产物）。
 */
import { computed, ref } from 'vue'

import { PrepCenterApiError, runPrepAction, type PrepWorkspaceSnapshot } from '@/features/teacher-prep-center/api/prepCenterApi'
import { newIdempotencyKey } from '@/features/teacher-prep-center/api/deckApi'
import { GENERATE, mapErrorText } from '@/features/teacher-prep-center/copy/prepCopy'
import { useDeckEditorStore } from '@/features/teacher-prep-center/stores/deckEditor'
import { usePrepSessionStore } from '@/features/teacher-prep-center/stores/prepSession'

export type GeneratePhase = 'idle' | 'confirming' | 'projecting' | 'enriching' | 'done' | 'error'

export interface GenerateViewState {
  phase: GeneratePhase
  /** 真实阶段文案（自陈服务端动作/作业，不含编造数字）。 */
  stage: string
  /** 失败原因（已归一中文；成功后清空）。 */
  error: string | null
  /** 已完成产物（失败时保留：已完成部分不回滚）。 */
  producedPlan: boolean
  producedDeck: boolean
  /** enrich 作业真实失败页（G14：只对这些页提供单页重试）。 */
  failedSlideIds: string[]
}

const phase = ref<GeneratePhase>('idle')
const stage = ref('')
const error = ref<string | null>(null)
const producedPlan = ref(false)
const producedDeck = ref(false)
const failedSlideIds = ref<string[]>([])
const busy = ref(false)

function reset(): void {
  phase.value = 'idle'
  stage.value = ''
  error.value = null
  producedPlan.value = false
  producedDeck.value = false
  failedSlideIds.value = []
}

/** 找一个可用动作（只认服务端下发的动作；disabled 的不认）。 */
function actionOf(
  ws: PrepWorkspaceSnapshot,
  name: string,
): { action: string; target: string | null; disabled: boolean } | null {
  const hit = (ws.available_actions ?? []).find((a) => a.action === name && a.disabled !== true)
  return hit ? { action: hit.action, target: hit.target, disabled: hit.disabled === true } : null
}

export function useGenerate() {
  const prep = usePrepSessionStore()
  const deckStore = useDeckEditorStore()

  const active = computed(() => phase.value !== 'idle' && phase.value !== 'done')

  /** 当前缺失的产物 → 按需投影（不覆盖教师已有产物，G14「不整份重生成」同源纪律）。 */
  function missingKinds(ws: PrepWorkspaceSnapshot): string[] {
    const kinds: string[] = []
    if (!ws.artifact?.lesson_plan?.spec_id) kinds.push('lesson_plan')
    const deck = ws.artifact?.teaching_deck as { deck_id?: string } | null
    if (!deck?.deck_id) kinds.push('deck')
    return kinds
  }

  async function execAction(
    sessionId: string,
    ws: PrepWorkspaceSnapshot,
    name: string,
    payload?: Record<string, unknown>,
  ): Promise<PrepWorkspaceSnapshot> {
    const act = actionOf(ws, name)
    if (!act) throw new Error('缺少可用动作')
    return runPrepAction(sessionId, {
      action: act.action,
      target: act.target,
      expectedRevision: ws.session.revision,
      payload,
      idempotencyKey: newIdempotencyKey(),
    })
  }

  /**
   * 一键生成（S4/S5 空态按钮）：确认教学路径（如需）→ 同源投影缺失产物 → 逐页充实课件。
   * 每一步都是真实服务端动作/作业；任一步失败即停并保留已完成部分。
   */
  async function generate(): Promise<void> {
    if (busy.value) return
    const sessionId = prep.currentSessionId
    let ws = prep.current
    if (!sessionId || !ws) {
      phase.value = 'error'
      error.value = '备课还没有准备好，请刷新后再试'
      return
    }
    busy.value = true
    error.value = null
    failedSlideIds.value = []
    try {
      // ── ① 老师确认教学路径（仅当服务端下发 confirm_proposal；点击即确认）──
      if (actionOf(ws, 'confirm_proposal')) {
        phase.value = 'confirming'
        stage.value = GENERATE.phaseConfirm
        ws = await execAction(sessionId, ws, 'confirm_proposal')
        if (prep.currentSessionId === sessionId) prep.current = ws
      }

      // ── ② 同源投影（project_specs：只投缺失产物，不覆盖已有）──
      const kinds = missingKinds(ws)
      if (kinds.length > 0) {
        if (!actionOf(ws, 'project_specs')) {
          throw new Error('教学路径尚未确认，无法生成课件与教案')
        }
        phase.value = 'projecting'
        stage.value = GENERATE.phaseProject
        ws = await execAction(sessionId, ws, 'project_specs', { kinds })
        if (prep.currentSessionId === sessionId) prep.current = ws
      }
      producedPlan.value = !!ws.artifact?.lesson_plan?.spec_id
      const deck = ws.artifact?.teaching_deck as { deck_id?: string } | null
      producedDeck.value = !!deck?.deck_id

      // ── ③ 逐页生成课件内容（enrich_deck_v2：真实 taskiq 作业 + 每页 progress 事件）──
      if (producedDeck.value) {
        deckStore.syncFromWorkspace(ws.artifact?.teaching_deck ?? null)
        if (deckStore.deckId && deckStore.deckThin) {
          phase.value = 'enriching'
          stage.value = GENERATE.phaseEnrich
          const ok = await deckStore.startEnrich()
          failedSlideIds.value = deckStore.enrichFailedPages.map((f) => f.slideId)
          if (!ok) {
            // 作业失败但投影产物保留；失败页进入单页重试清单
            throw new Error(deckStore.enrichError || '课件内容生成未完成')
          }
        }
      }

      phase.value = 'done'
      stage.value = GENERATE.genDone
      // 成果以权威快照为准（runPrepAction 已回写；deck 编辑器已同步）
    } catch (e) {
      phase.value = 'error'
      error.value =
        e instanceof PrepCenterApiError
          ? mapErrorText(e)
          : e instanceof Error && e.message
            ? e.message
            : '生成未完成，请重试'
      stage.value = GENERATE.genFailed
    } finally {
      busy.value = false
    }
  }

  /** 失败后重试整个链路（已完成产物会被跳过：missingKinds 只投缺失项）。 */
  async function retryAll(): Promise<void> {
    if (phase.value === 'error') {
      phase.value = 'idle'
      await generate()
    }
  }

  return {
    phase,
    stage,
    error,
    producedPlan,
    producedDeck,
    failedSlideIds,
    busy,
    active,
    generate,
    retryAll,
    reset,
    missingKinds,
  }
}
