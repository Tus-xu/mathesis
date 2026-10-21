/**
 * P · 备课中心整链路状态仓（PROTOTYPE-ONLY，IFC-P-a 登记中）
 * 链路：对话首页 → 从教材开始 / 上传资料 / 模板库 → AI 生成 → 大纲确认 → 备课编辑器
 * 轻量 reactive 模块（同 teacherContext/companion 惯例，不用 Pinia）。
 * 诚实原则：没有的数据就是 undefined——界面显示"未选择"，不冒充。
 */
import { reactive } from 'vue'

export type PrepStage = 'home' | 'textbook' | 'upload' | 'generating' | 'outline' | 'editor'
export type PrepSource = 'input' | 'textbook' | 'upload' | 'template'

export interface PrepBrief {
  topic: string
  textbook?: string
  chapter?: string
  classId?: string
  className?: string
  courseType?: string
  duration?: number
  source: PrepSource
  requirements: string[]
  materials?: { name: string; size: string; pages?: number }[]
  templateId?: string
  templateName?: string
}

export interface PrepRichOutline {
  topic: string
  duration: number
  sections: { id: string; name: string; minutes: number; goal: string; example_suggestion?: string }[]
  total_minutes: number
  notes: string[]
  objectives?: string[]
  keypoints?: { major: string[]; hard: string[] }
  blackboard?: { main: string[]; side: string[] }
  homework?: { tier: string; label: string; items: string[]; minutes: string }[]
}

const state = reactive({
  stage: 'home' as PrepStage,
  brief: null as PrepBrief | null,
  outline: null as PrepRichOutline | null,
  planId: '',
  /** VNext kernel facts are projections only; reload must fetch them again. */
  sessionId: '',
  sessionRevision: null as number | null,
  context: null as Record<string, unknown> | null,
  blueprint: null as Record<string, unknown> | null,
  lessonPlanSpec: null as Record<string, unknown> | null,
  teachingDeckSpec: null as Record<string, unknown> | null,
  availableActions: [] as { action: string; target: string | null; copy_key: string; disabled: boolean }[],
  kernelError: '',
  blockedReason: '',
})

export function usePrepChain() {
  return state
}

export function startBrief(b: Partial<PrepBrief> & { source: PrepSource; topic: string }) {
  state.brief = {
    topic: b.topic,
    textbook: b.textbook,
    chapter: b.chapter,
    classId: b.classId,
    className: b.className,
    courseType: b.courseType,
    duration: b.duration,
    source: b.source,
    requirements: b.requirements || [],
    materials: b.materials,
    templateId: b.templateId,
    templateName: b.templateName,
  }
  state.outline = null
  state.planId = ''
  state.sessionId = ''
  state.sessionRevision = null
  state.context = null
  state.blueprint = null
  state.lessonPlanSpec = null
  state.teachingDeckSpec = null
  state.availableActions = []
  state.kernelError = ''
  state.blockedReason = ''
  state.stage = 'generating'
}

export function applyKernelProjection(projection: {
  sessionId: string | null
  revision: number | null
  brief: Partial<PrepBrief> | null
  outline: PrepRichOutline | null
  context: Record<string, unknown> | null
  blueprint: Record<string, unknown> | null
  lessonPlanSpec: Record<string, unknown> | null
  teachingDeckSpec: Record<string, unknown> | null
  availableActions: { action: string; target: string | null; copy_key: string; disabled: boolean }[]
  blockedReason: string | null
}) {
  state.sessionId = projection.sessionId || ''
  state.sessionRevision = projection.revision
  state.context = projection.context
  state.blueprint = projection.blueprint
  state.lessonPlanSpec = projection.lessonPlanSpec
  state.teachingDeckSpec = projection.teachingDeckSpec
  state.availableActions = projection.availableActions
  state.blockedReason = projection.blockedReason || ''
  state.kernelError = ''
  if (projection.brief?.topic) {
    state.brief = {
      ...(state.brief || { topic: projection.brief.topic, source: 'textbook', requirements: [] }),
      ...projection.brief,
      topic: projection.brief.topic,
      source: projection.brief.source || state.brief?.source || 'textbook',
      requirements: projection.brief.requirements || state.brief?.requirements || [],
    }
  }
  if (projection.lessonPlanSpec?.spec_id && typeof projection.lessonPlanSpec.spec_id === 'string') {
    state.planId = projection.lessonPlanSpec.spec_id
  }
  if (projection.outline) {
    state.outline = projection.outline
    state.stage = 'outline'
  }
}

export function setKernelError(message: string) {
  state.kernelError = message
  state.stage = 'generating'
}

export function setOutline(o: PrepRichOutline) {
  state.outline = o
  state.stage = 'outline'
}

export function enterEditor(planId: string) {
  state.planId = planId
  state.stage = 'editor'
}

export function gotoStage(s: PrepStage) {
  state.stage = s
}

export function resetChain() {
  state.stage = 'home'
  state.brief = null
  state.outline = null
  state.planId = ''
  state.sessionId = ''
  state.sessionRevision = null
  state.context = null
  state.blueprint = null
  state.lessonPlanSpec = null
  state.teachingDeckSpec = null
  state.availableActions = []
  state.kernelError = ''
  state.blockedReason = ''
}

export function __resetPrepChainForTest() {
  resetChain()
}
