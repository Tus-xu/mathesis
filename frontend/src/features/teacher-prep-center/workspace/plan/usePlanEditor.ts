/**
 * 教案编辑器组合式状态（R5）。唯一数据源 = `GET /artifacts/lesson-plans/{spec_id}`（真实后端）。
 *
 * - 只读渲染所需的全部派生（六章 / 过程表行 / 板书三栏 / 重难点拆分）在组件层，本模块负责：
 *   装载权威 spec、教材元信息（学科）、教学反思的块级持久化、AI 局部重生成的真实调用。
 * - §F 扩展字段全为可选：缺失时派生结果为空数组，渲染层给诚实空态（不编造）。
 * - 数字/状态/时间一律来自后端；禁止前端常量假数据。
 */
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { fetchTextbookView } from '@/features/teacher-prep-center/api/prepCenterApi'
import {
  applyLessonPlanPatch,
  createLessonPlanPatch,
  fetchLessonPlanSpec,
  regenLessonPlanBlock,
  type LessonPlanBody,
  type LessonPlanSpecView,
  type PlanBlock,
  type PlanPatchOp,
  type PlanRegenRefs,
  type PlanStage,
} from '@/features/teacher-prep-center/api/lessonPlanApi'

/** 黄金图六章定义（左大纲顺序 = 文档顺序）。 */
export type ChapterKey = 'objectives' | 'key_points' | 'process' | 'board' | 'homework' | 'reflection'

export interface ChapterDef {
  key: ChapterKey
  /** 蓝色方形序号徽标数字。 */
  no: number
  title: string
}

export const PLAN_CHAPTERS: readonly ChapterDef[] = [
  { key: 'objectives', no: 1, title: '教学目标' },
  { key: 'key_points', no: 2, title: '教学重难点' },
  { key: 'process', no: 3, title: '教学过程' },
  { key: 'board', no: 4, title: '板书设计' },
  { key: 'homework', no: 5, title: '作业布置' },
  { key: 'reflection', no: 6, title: '教学反思' },
] as const

/** 章 → 可定位的块 kind（用于 AI 目标定位）。 */
const CHAPTER_BLOCK_KINDS: Record<ChapterKey, string[]> = {
  objectives: ['objectives'],
  key_points: ['key_difficult_points'],
  process: ['lesson_flow'],
  board: ['board_design'],
  homework: ['homework'],
  reflection: ['reflection'],
}

export interface SplitKeyPoint {
  kind: 'key' | 'difficult'
  text: string
}

export interface AiSuggestion {
  key: string
  icon: 'sparkle' | 'megaphone' | 'bulb' | 'link'
  title: string
  subtitle: string
  /** 真实行为说明（11px 灰字，消除误解，如「非同页同步」）。 */
  note?: string
  instruction: string
}

/** 反空壳：AI 建议卡全部映射到真实 `POST /regen` 指令（无摆设入口）。 */
const SUGGESTIONS: readonly AiSuggestion[] = [
  {
    key: 'condense',
    icon: 'sparkle',
    title: '精简语言',
    subtitle: '让这部分表述更简洁清晰',
    instruction: '请把当前这部分内容改写得更简洁、清晰，保留全部关键教学信息，不要新增未在教材依据中出现的事实。',
  },
  {
    key: 'inquiry',
    icon: 'megaphone',
    title: '改成探究式提问',
    subtitle: '把讲解改为学生探究的问题链',
    instruction: '请把当前的讲授式表述改写为引导学生探究的问题链，问题要层层递进且贴合本节教学目标。',
  },
  {
    key: 'cite',
    icon: 'link',
    title: '强化教材依据',
    subtitle: '为本段补充可追溯依据说明',
    note: '仅实时改写当前教案章节，不会同步到课件页',
    instruction: '请为当前这部分补充可追溯的教材依据说明，明确指出所依据的教材章节内容。',
  },
  {
    key: 'board_points',
    icon: 'bulb',
    title: '补充板书要点',
    subtitle: '基于此内容生成板书设计建议',
    instruction: '请基于当前这部分内容生成板书设计建议：提炼关键结论、术语与公式，条理清晰，不得编造教材事实。',
  },
] as const

function errText(e: unknown): string {
  if (e && typeof e === 'object' && 'detail' in e && typeof (e as { detail?: unknown }).detail === 'string') {
    return (e as { detail: string }).detail
  }
  if (e instanceof Error) return e.message
  return String(e)
}

/**
 * 编辑内部标记（**禁止上屏**）：投影 / 编辑链路可能残留内部修订类括注（词表见下方正则），
 * 必须在**渲染层**统一净化（不依赖后端投影是否清理，
 * LEAD-BAR 铁律 3 / VISUAL-RUBRIC §1.7）。
 *
 * 覆盖：括号包裹的标记段 + 行尾裸标记（标记词表见下方两条正则）。
 * 仅匹配已知标记词，避免误伤「（如天文轨道…）」
 * 「（a>b>0）」「（焦点在 x 轴上）」等正文括号。
 */
const EDIT_MARKER_RE = /[（(]\s*(?:教师修订|教师新增|教师补充|教师备注|教师[A-Za-z]*\d*编辑验证|待确认|AI\s*生成|自动生成|占位|待补充|待完善)[^）)]*[）)]/g
const EDIT_MARKER_TAIL_RE = /[，,、;；]?\s*(?:教师修订|教师新增|教师补充|教师[A-Za-z]*\d*编辑验证|待确认|AI\s*生成|自动生成|待补充|待完善)\s*$/g

/**
 * 防御性展示守卫：剔除 EDIT_MARKER_* 命中的内部标记片段（渲染层最后一道防线，
 * 正常路径下投影器已丢弃占位内容）。
 */
export function stripPlaceholder(text: string): string {
  return String(text ?? '')
    .replace(EDIT_MARKER_RE, '')
    .replace(EDIT_MARKER_TAIL_RE, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

/**
 * 教材章节号前缀剥离（确定性）：`3.1.2 椭圆的简单几何性质` → `椭圆的简单几何性质`。
 *
 * 黄金图教案标题是**课题名**；章节号（`3.1.2`/`2.3.1`）只是教材目录定位符，不是教学内容。
 * 正则要求**至少一个点号段**（`^\d+(?:\.\d+)+\s*`）：裸序号「1. 掌握…」不命中
 * （否则会残留一个孤立的 `.`）；统一用于文档标题、右面板「当前定位」与教学过程环节名。
 */
const SECTION_NO_RE = /^\d+(?:\.\d+)+\s*/
export function stripSectionNo(text: string): string {
  return String(text ?? '').replace(SECTION_NO_RE, '').trim()
}

/**
 * 教学重难点拆分：`kind` 存在即按其分栏；全为 null 时按顺序智能拆分（前段重点 / 后段难点），
 * 仅用于显示、不写库（FROZEN-CONTRACT §F.1）。
 */
export function splitKeyPoints(items: { text: string; kind?: 'key' | 'difficult' | null }[]): SplitKeyPoint[] {
  const list = items.filter((it) => String(it.text ?? '').trim().length > 0)
  if (!list.length) return []
  const anyKind = list.some((it) => it.kind === 'key' || it.kind === 'difficult')
  if (anyKind) {
    return list.map((it) => ({ kind: it.kind === 'difficult' ? 'difficult' : 'key', text: it.text }))
  }
  const mid = Math.ceil(list.length / 2)
  return list.map((it, i) => ({ kind: i < mid ? 'key' : 'difficult', text: it.text }))
}

export interface UsePlanEditor {
  view: Ref<LessonPlanSpecView | null>
  spec: ComputedRef<LessonPlanBody | null>
  loading: Ref<boolean>
  error: Ref<string | null>
  revision: ComputedRef<number | null>
  /** 教材元信息（学科 / 教材名，来自真实教材视图；缺省诚实为空）。 */
  subject: Ref<string | null>
  textbookTitle: Ref<string | null>
  load: (specId: string) => Promise<void>
  /** 解析教材元信息（学科 / 教材名）；失败诚实置空，不阻塞教案渲染。 */
  loadTextbookMeta: (sessionId: string, locator: string | null | undefined) => Promise<void>
  /* 过程表行（与教学路径节点一一对应） */
  stages: ComputedRef<PlanStage[]>
  /* 教学反思持久化 */
  reflectionText: ComputedRef<string>
  saving: Ref<boolean>
  saveError: Ref<string | null>
  saveReflection: (text: string) => Promise<boolean>
  /* AI 局部重生成 */
  aiBusy: Ref<boolean>
  aiError: Ref<string | null>
  aiNotice: Ref<string | null>
  /** G14 单块重试：最近一次失败的块；成功后为 null。 */
  aiFailedBlock: Ref<{ blockId: string; instruction: string } | null>
  suggestions: readonly AiSuggestion[]
  blockForChapter: (key: ChapterKey) => PlanBlock | null
  canUseAi: ComputedRef<boolean>
  runInstruction: (blockId: string, instruction: string) => Promise<boolean>
  /** G14：只重试失败的那一块（lesson-plan /regen），其余块不动。 */
  retryFailedBlock: () => Promise<boolean>
  clearAiNotice: () => void
}

export function usePlanEditor(): UsePlanEditor {
  const view = ref<LessonPlanSpecView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const subject = ref<string | null>(null)
  const textbookTitle = ref<string | null>(null)

  const saving = ref(false)
  const saveError = ref<string | null>(null)

  const aiBusy = ref(false)
  const aiError = ref<string | null>(null)
  const aiNotice = ref<string | null>(null)
  /** G14 单块重试：最近一次失败的块（blockId + 原指令）；成功后清空。 */
  const aiFailedBlock = ref<{ blockId: string; instruction: string } | null>(null)

  let aborter: AbortController | null = null

  const spec = computed(() => view.value?.spec ?? null)
  const revision = computed(() => (view.value ? view.value.revision : null))

  const stages = computed<PlanStage[]>(() => {
    const flow = spec.value?.lesson_flow
    if (!Array.isArray(flow)) return []
    // 渲染层净化环节标题（剥内部修订括注，如「课堂导入（…）」→「课堂导入」）+ 剥教材章节号前缀。
    return flow.map((s) => ({ ...s, title: stripSectionNo(stripPlaceholder(s.title)) }))
  })

  const reflectionText = computed(() => {
    const items = spec.value?.reflection
    if (!Array.isArray(items) || !items.length) return ''
    return items
      .map((it) => stripPlaceholder(it.text))
      .filter((t) => t.trim().length > 0)
      .join('\n')
  })

  /** AI 依据（真实版本锚点）：缺任一必填项则 AI 入口诚实禁用，不伪造 prompt_ref。 */
  const regenRefs = computed<PlanRegenRefs | null>(() => {
    const v = view.value
    const s = spec.value
    if (!v || !s) return null
    const bid = s.blueprint_ref?.blueprint_id ?? v.lineage?.approved_blueprint_id ?? null
    const brev = s.blueprint_ref?.revision ?? v.lineage?.blueprint_revision ?? null
    const promptId = v.lineage?.prompt_ref?.prompt_id ?? s.projection?.prompt_release_id ?? null
    if (!bid || !brev || !promptId) return null
    return {
      approved_blueprint_id: String(bid),
      blueprint_revision: Number(brev),
      spec_revision: Number(v.revision),
      prompt_ref: { prompt_id: String(promptId) },
      source_refs: (s.source_refs ?? []).slice(0, 20),
    }
  })

  const canUseAi = computed(() => !!view.value && !!regenRefs.value)

  function blockForChapter(key: ChapterKey): PlanBlock | null {
    const v = view.value
    if (!v) return null
    const kinds = CHAPTER_BLOCK_KINDS[key]
    return v.blocks.find((b) => kinds.includes(b.kind)) ?? null
  }

  async function loadTextbookMeta(sessionId: string, locator: string | null | undefined) {
    subject.value = null
    textbookTitle.value = null
    if (!sessionId || !locator) return
    try {
      const tb = await fetchTextbookView(sessionId, locator)
      subject.value = tb.subject?.trim() || null
      textbookTitle.value = tb.source_title?.trim() || tb.textbook?.trim() || null
    } catch {
      /* 教材元信息解析失败：诚实地不显示学科，不阻塞教案渲染 */
    }
  }

  async function load(specId: string) {
    if (!specId) {
      view.value = null
      return
    }
    aborter?.abort()
    aborter = new AbortController()
    loading.value = true
    error.value = null
    saveError.value = null
    aiError.value = null
    try {
      view.value = await fetchLessonPlanSpec(specId, aborter.signal)
    } catch (e) {
      if ((e as Error)?.name === 'AbortError') return
      view.value = null
      error.value = errText(e)
    } finally {
      loading.value = false
    }
  }

  /** 教学反思持久化：块级 patch（真实落库，revision+1）。 */
  async function saveReflection(text: string): Promise<boolean> {
    const v = view.value
    if (!v) {
      saveError.value = '教案还没有准备好，稍等一下或刷新后再试'
      return false
    }
    const value = text.trim()
    const existing = v.blocks.find((b) => b.kind === 'reflection')
    let operations: PlanPatchOp[]
    if (!value && existing) {
      operations = [{ op: 'remove_block', target: existing.block_id }]
    } else if (!value) {
      return true // 空值且无可删块：无操作
    } else if (existing) {
      operations = [{ op: 'set_block', target: existing.block_id, field: 'text', value }]
    } else {
      operations = [{ op: 'add_block', target: 'reflection.0', value: { text: value } }]
    }

    saving.value = true
    saveError.value = null
    try {
      const proposal = await createLessonPlanPatch(v.spec_id, {
        expected_revision: v.revision,
        summary: '编辑教学反思',
        source: 'human',
        operations,
      })
      await applyLessonPlanPatch(v.spec_id, proposal.patch_id, v.revision)
      await load(v.spec_id)
      return true
    } catch (e) {
      saveError.value = errText(e)
      return false
    } finally {
      saving.value = false
    }
  }

  /** AI 局部重生成：真实 LLM → 提案 → 应用 → 重载（新 revision，可回滚）。 */
  async function runInstruction(blockId: string, instruction: string): Promise<boolean> {
    const v = view.value
    const refs = regenRefs.value
    if (!v || !refs) {
      aiError.value = '教案的 AI 还没有准备好，稍等一下或刷新后再试。'
      return false
    }
    const text = instruction.trim()
    if (!text) return false
    aiBusy.value = true
    aiError.value = null
    aiNotice.value = null
    try {
      const proposal = await regenLessonPlanBlock(v.spec_id, {
        expected_revision: v.revision,
        target: { kind: 'block', block_id: blockId },
        instruction: text,
        refs,
      })
      const applied = await applyLessonPlanPatch(v.spec_id, proposal.patch_id, v.revision)
      await load(v.spec_id)
      aiNotice.value = `AI 已更新内容（v${applied.revision}）`
      aiFailedBlock.value = null
      return true
    } catch (e) {
      aiError.value = errText(e)
      // G14 单块重试：只重试失败的那一块（lesson-plan /regen），其余块保持已生效内容
      aiFailedBlock.value = { blockId, instruction: text }
      return false
    } finally {
      aiBusy.value = false
    }
  }

  /** G14：重试上一次失败的那一个块（真实 /regen，不整份重生成）。 */
  async function retryFailedBlock(): Promise<boolean> {
    const failed = aiFailedBlock.value
    if (!failed) return false
    return runInstruction(failed.blockId, failed.instruction)
  }

  return {
    view,
    spec,
    loading,
    error,
    revision,
    subject,
    textbookTitle,
    load,
    loadTextbookMeta,
    stages,
    reflectionText,
    saving,
    saveError,
    saveReflection,
    aiBusy,
    aiError,
    aiNotice,
    aiFailedBlock,
    suggestions: SUGGESTIONS,
    blockForChapter,
    canUseAi,
    runInstruction,
    retryFailedBlock,
    clearAiNotice: () => {
      aiNotice.value = null
    },
  }
}
