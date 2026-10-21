/**
 * 教师工作台 V3 API（/teacher-v3/*）
 * 契约来源：src/types/teacherV3.ts（唯一类型来源）
 * 按后端域分节（供四个后端智能体并行实现的边界）：
 *   catalog 基础目录 / recognition 识别 / generation AI 生成(SSE) / figures 图形 / grading 批改
 * decks/plans 为课件与教案内容 CRUD，归属 generation 域的数据面。
 */
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { authHeaders } from '@/api/client'
import { teacherGet, teacherPost, teacherRequest } from './teacher/client'
import type {
  V3ButlerAction, V3ButlerCard, V3ButlerChatInput, V3ButlerContext, V3ClassInfo, V3Deck, V3DeckTemplate, V3DrawRecord, V3ElementDiff, V3FigureLibraryItem, V3FigurePreset, V3FigureRebuildCandidate, V3GradingAssignment,
  V3LessonPlan, V3LessonTemplate, V3PlanGenForm, V3PlanOutline, V3Recipe, V3RecognizePageResult, V3Slide, V3Task, V3TemplateQualityReport, V3TodayData, V3VoiceFormulaInput,
} from '@/types/teacherV3'

/* ============ 试卷/学情/资源 等目录数据的本地形状（catalog 域返回） ============ */

export interface V3QuizQuestion {
  id: string
  kp_name: string
  kp_code: string
  /** 知识点在分类树上的完整路径（P4：分类树定位） */
  kp_path?: string[]
  /** 图片题型：题干为一张图片（拍照/扫描原图），q_type='image' 时必有 stem_image */
  q_type: 'choice' | 'fill' | 'solve' | 'image'
  difficulty: 'easy' | 'medium' | 'hard'
  stem_latex: string
  /** q_type='image' 时的题干图 */
  stem_image?: string
  options?: string[]
  answer: string
  /** 解答过程原图（与题干分开上传，原样保留） */
  solution_image?: string
  source: '校本' | '区库' | '拍照入库' | '自编' | 'AI配题'
  /** V3.1 字段补齐（RESEARCH_QUESTION_BANK §3.3）：年份（选填） */
  year?: string
  /** 解析（题库侧编辑对齐） */
  analysis?: string
  /** 所属专题夹（引用式：题目挂树上，夹子存引用） */
  folder_refs?: string[]
  /** 被挂入教案/试卷/课件的次数（热信号 → 质量分雏形） */
  usage_count?: number
}

/** 题库知识点分类树节点（P4） */
export interface V3KpTreeNode {
  id: string
  name: string
  children?: V3KpTreeNode[]
  /** 题量统计（由服务端汇总） */
  count?: number
}

/** 我的专题夹（V3.1：跨知识点收集，存引用不挪题） */
export interface V3QuestionFolder {
  id: string
  name: string
  desc?: string
  count: number
  updated_at: string
}

/** 教材版本 + 章节（五件套级联数据源；章节由「上传教材 → 知识库目录」驱动，可编辑调整，而非写死） */
export interface V3TextbookChapters {
  /** 目录来源：knowledge_base=由上传教材生成的知识库目录；preset=内置示例（待上传教材替换） */
  provenance?: 'knowledge_base' | 'preset'
  textbooks: { name: string; editable?: boolean; chapters: { id: string; path: string }[] }[]
}

export interface V3ResourceItem {
  id: string
  name: string
  kind: 'deck' | 'plan' | 'figure-recipe' | 'photo-bank'
  subject: string
  /** 章节/课题（资源中心第二分类维度） */
  chapter?: string
  updated_at: string
  owner: string
  shared: boolean
}

export interface V3InsightOverview {
  class_id: string
  class_name: string
  avg: number
  trend: number[]
  kp_heat: { kp: string; name: string; mastery: number; delta: number }[]
  error_tags: { tag: string; count: number; trend: number }[]
  watchlist: { name: string; note: string; weak: string[] }[]
}

export interface V3DeckSummary {
  id: string
  title: string
  template_id: string
  source: V3Deck['source']
  slide_count: number
  class_name: string
  updated_at: string
}

export interface V3PlanSummary {
  id: string
  topic: string
  class_id: string
  lesson_type: string
  section_count: number
  confirmed: boolean
  updated_at: string
}

export const v3Api = {
  /* ==================== catalog 基础目录域 ==================== */
  catalog: {
    today: (signal?: AbortSignal) => teacherGet<V3TodayData>('/teacher-v3/today', undefined, signal),
    classes: () => teacherGet<{ items: V3ClassInfo[] }>('/teacher-v3/classes'),
    tasks: (signal?: AbortSignal) => teacherGet<{ items: V3Task[]; running: number }>('/teacher-v3/tasks', undefined, signal),
    deckTemplates: () => teacherGet<{ items: V3DeckTemplate[] }>('/teacher-v3/deck-templates'),
    lessonTemplates: () => teacherGet<{ items: V3LessonTemplate[] }>('/teacher-v3/lesson-templates'),
    recipes: () => teacherGet<{ items: V3Recipe[] }>('/teacher-v3/recipes'),
    quizQuestions: (query?: { kp?: string; q?: string; difficulty?: string; q_type?: string; source?: string; folder?: string }) =>
      teacherGet<{ items: V3QuizQuestion[]; total: number }>('/teacher-v3/quiz/questions', query as Record<string, unknown>),
    /** P4：知识点分类树 */
    quizKpTree: () => teacherGet<{ tree: V3KpTreeNode[] }>('/teacher-v3/quiz/kp-tree'),
    /** P4：扫描入库存一道题（手写/拍照 → 识别 → 进题库，含图片题型；题干/解答双图分开传） */
    quizScanImport: (body: { src: string; kp_code: string; kp_name: string; as_image?: boolean; kp_path?: string[]; solution_src?: string }) =>
      teacherPost<V3QuizQuestion>('/teacher-v3/quiz/scan-import', body),
    /** V3.2：知识点分类树编辑（个人定制层：加子级/改名/删除） */
    kpTreeAdd: (body: { parent_id: string | null; name: string }) => teacherPost<V3KpTreeNode>('/teacher-v3/quiz/kp-tree', body),
    kpTreeRename: (id: string, name: string) => teacherRequest<{ ok: true }>('PATCH', `/teacher-v3/quiz/kp-tree/${id}`, { body: { name } }),
    kpTreeRemove: (id: string) => teacherRequest<{ ok: true }>('DELETE', `/teacher-v3/quiz/kp-tree/${id}`),
    /** V3.2：AI 识别知识点（拍照/题干 → 建议归属 + 备选） */
    kpSuggest: (body: { src?: string; stem?: string }) =>
      teacherPost<{ suggestion: { code: string; path: string[]; name: string; confidence: number }; alternates: { code: string; path: string[]; name: string }[]; note: string }>(
        '/teacher-v3/quiz/kp-suggest', body),
    /** V3.1：自编题录入（结构化手输，source='自编'） */
    quizCreate: (body: { kp_code: string; kp_name: string; kp_path?: string[]; q_type: 'choice' | 'fill' | 'solve'; difficulty: 'easy' | 'medium' | 'hard'; stem_latex: string; answer: string; analysis?: string }) =>
      teacherPost<V3QuizQuestion>('/teacher-v3/quiz/questions', body),
    /** V3.3：删除一道题（含拍照入库题；会从题库及所有专题夹引用中移除） */
    quizRemove: (id: string) => teacherRequest<{ ok: true }>('DELETE', `/teacher-v3/quiz/questions/${id}`),
    /** V3.1：五件套级联数据源（教材版本 → 章节路径） */
    textbookChapters: () => teacherGet<V3TextbookChapters>('/teacher-v3/textbook-chapters'),
    /** V3.3：上传教材 → AI 重建「知识库目录」（章节由目录驱动，可再编辑/替换，替代写死示例） */
    textbookRebuild: (body: { file: string; version?: string }) =>
      teacherPost<V3TextbookChapters>('/teacher-v3/textbooks/rebuild-index', body),
    /** V3.1：我的专题夹（跨知识点引用式收集） */
    folders: {
      list: () => teacherGet<{ items: V3QuestionFolder[] }>('/teacher-v3/folders'),
      create: (body: { name: string; desc?: string }) => teacherPost<V3QuestionFolder>('/teacher-v3/folders', body),
      remove: (id: string) => teacherRequest<{ ok: true }>('DELETE', `/teacher-v3/folders/${id}`),
      addQuestions: (id: string, body: { question_ids: string[] }) =>
        teacherPost<{ ok: true; count: number }>(`/teacher-v3/folders/${id}/questions`, body),
      removeQuestion: (id: string, qid: string) =>
        teacherRequest<{ ok: true; count: number }>('DELETE', `/teacher-v3/folders/${id}/questions/${qid}`),
    },
    /** V3.1：上传教案/课件 → 质量体检（反套话 + 栏目完整度），帮老师挑出值得当模板的 1-3 份 */
    planTemplatesImport: (body: { files: string[] }) =>
      teacherPost<{ reports: V3TemplateQualityReport[] }>('/teacher-v3/plan-templates/import', body),
    /** V3.1：把通过体检的上传件提炼为「我的模板」（个人知识库档，仅本人可见） */
    planTemplatesExtract: (body: { file: string; name: string }) =>
      teacherPost<V3LessonTemplate>('/teacher-v3/plan-templates/extract', body),
    insights: (classId: string) => teacherGet<V3InsightOverview>('/teacher-v3/insights/overview', { class_id: classId }),
    resources: () => teacherGet<{ items: V3ResourceItem[] }>('/teacher-v3/resources'),
  },

  /* ==================== 课件 decks（generation 域数据面） ==================== */
  decks: {
    list: () => teacherGet<{ items: V3DeckSummary[] }>('/teacher-v3/decks'),
    get: (id: string, signal?: AbortSignal) => teacherGet<V3Deck>(`/teacher-v3/decks/${id}`, undefined, signal),
    patch: (id: string, patch: Partial<Pick<V3Deck, 'title' | 'template_id'>>) =>
      teacherRequest<V3Deck>('PATCH', `/teacher-v3/decks/${id}`, { body: patch }),
    addSlide: (deckId: string, body: { slide: V3Slide; after_id?: string }) =>
      teacherPost<{ deck: V3Deck }>(`/teacher-v3/decks/${deckId}/slides`, body),
    deleteSlide: (deckId: string, slideId: string) =>
      teacherRequest<{ deck: V3Deck }>('DELETE', `/teacher-v3/decks/${deckId}/slides/${slideId}`),
    patchSlide: (deckId: string, slideId: string, patch: Partial<V3Slide>) =>
      teacherRequest<V3Deck>('PATCH', `/teacher-v3/decks/${deckId}/slides/${slideId}`, { body: patch }),
    export: (deckId: string, format: 'pptx' | 'pdf') =>
      teacherPost<{ task_id: string }>(`/teacher-v3/decks/${deckId}/export`, { format }),
  },

  /* ==================== 教案 plans ==================== */
  plans: {
    list: () => teacherGet<{ items: V3PlanSummary[] }>('/teacher-v3/plans'),
    get: (id: string, signal?: AbortSignal) => teacherGet<V3LessonPlan>(`/teacher-v3/plans/${id}`, undefined, signal),
    patch: (id: string, patch: Partial<V3LessonPlan>) =>
      teacherRequest<V3LessonPlan>('PATCH', `/teacher-v3/plans/${id}`, { body: patch }),
    confirm: (id: string) => teacherPost<V3LessonPlan>(`/teacher-v3/plans/${id}/confirm`),
    /** 教案直通课件：以已确认教案为骨架生成 deck */
    pushToDeck: (id: string, body: { template_id: string }) =>
      teacherPost<{ deck_id: string }>(`/teacher-v3/plans/${id}/push-to-deck`, body),
  },

  /* ==================== recognition 识别域 ==================== */
  recognition: {
    /**
     * 拍照出课件（SPEC §5.10/§5.11）：SSE
     * meta → photo(逐张) → block(识别块，含可编辑性) → paginate(分页引擎) → done
     * 返回 { deck, blocks_by_page }
     */
    photoIngest: (
      body: { photos: string[]; question_label: string; config: { scope: 'stem' | 'stem+solution' | 'stem+keypoints'; mode: 'blank-board' | 'full-solution' | 'keypoints'; template_id: string; font_tier: 'compact' | 'standard' | 'large'; margin_notes: boolean } },
      onEvent: (event: string, data: any) => void,
      signal?: AbortSignal,
    ) => v3Sse('POST', '/teacher-v3/recognition/photo-ingest', body, onEvent, signal),

    /** 原图区域 → 图形重建候选（构造校验门：passed_validation=false 不上屏） */
    rebuildCandidates: (body: { region: { x: number; y: number; w: number; h: number }; photo_id: string }) =>
      teacherPost<{ candidates: V3FigureRebuildCandidate[] }>('/teacher-v3/recognition/rebuild', body),

    /** 单张照片 → 公式识别（P2：编辑器拍照"识别为公式"）。返回近似 LaTeX，教师 MathField 审查后落稿。 */
    photoToFormula: (body: { src: string }) =>
      teacherPost<{ latex: string; confidence: number }>('/teacher-v3/recognition/photo-to-formula', body),

    /** 教案板块拍照填充（P5）：拍一页教材/往年教案 → 识别整理成三栏草稿（可编辑，R2）。返回前先扫描增强预览。 */
    planPhotoDraft: (body: { src: string; board_name: string; topic: string }) =>
      teacherPost<{ teacher_activity: string; student_activity: string; design_intent: string; confidence: number }>(
        '/teacher-v3/recognition/plan-photo-draft',
        body,
      ),

    /** 备小研改版（IFC-WS-a）：识别确认步的预览——fixture 演示识别，可编辑文本供教师提前校对；正式识别仍以生成链路为准 */
    preview: (body: { photos: string[] }) =>
      teacherPost<{ items: { photo_id: string; confidence: number; warn?: boolean; text: string; kps: string[] }[]; note: string }>(
        '/teacher-v3/recognition/preview',
        body,
      ),
  },

  /* ==================== generation AI 生成域（SSE） ==================== */
  generation: {
    /** 主题生成课件：meta → outline → slide(逐页草稿) → done */
    /* B3 大纲 Gate 第一段（mock 增量端点；正式契约随 IFC-PRODUCT-01a 评审）
       C1：+chapter（内容源锚定）/ course_type（环节语义大纲结构），随 IFC-C1-a 评审
       C1.1：+requirements（教师自然语言要求，server 词表编译进大纲并返回 reqs 回应台账） */
    deckOutline: (body: { topic: string; class_id?: string; chapter?: string; course_type?: string; requirements?: string[] }) =>
      teacherPost('/teacher-v3/generation/deck-outline', body),
    deck: (body: { topic: string; class_id: string; template_id: string }, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', '/teacher-v3/generation/deck', body, onEvent, signal),
    /**
     * V3.1 两段式第一段：五件套 → 大纲（环节 / 时长预算 / 目标草案 / 例题建议）。
     * 教师确认 / 编辑大纲后才走 generation.plan 成稿（消灭流程性黑盒，RESEARCH_LESSON_PREP §5.2 方案 A）
     */
    planOutline: (body: V3PlanGenForm) =>
      teacherPost<V3PlanOutline>('/teacher-v3/generation/plan/outline', body),
    /** 教案生成：meta → outline → section(逐环节) → done。携带教师确认后的 outline（可增删环节 / 调时长） */
    plan: (body: V3PlanGenForm & { outline?: V3PlanOutline['sections'] }, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', '/teacher-v3/generation/plan', body, onEvent, signal),
    /** 元素级 AI 建议（R5：只出草稿 diff，不覆写）：suggest → diff → done */
    aiElement: (deckId: string, body: { slide_id: string; hint: string }, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', `/teacher-v3/generation/decks/${deckId}/ai-element`, body, onEvent, signal),
  },

  /* ==================== figures 图形域 ==================== */
  figures: {
    presets: () => teacherGet<{ items: V3FigurePreset[] }>('/teacher-v3/figures/presets'),
    saveRecipe: (body: { name: string; preset_id: string; params: Record<string, number>; school_shared: boolean; note: string }) =>
      teacherPost<V3Recipe>('/teacher-v3/figures/recipes', body),
  },

  /* ==================== draw 绘图工作台域（P1） ==================== */
  draw: {
    library: () => teacherGet<{ items: V3FigureLibraryItem[] }>('/teacher-v3/draw/library'),
    saveLibrary: (body: { name: string; kind: 'free' | 'fx'; thumb: string; records?: V3DrawRecord[]; expr?: string; shared?: boolean }) =>
      teacherPost<V3FigureLibraryItem>('/teacher-v3/draw/library', body),
    /**
     * 手写公式识别（SSE）：meta → recognizing → result {latex, confidence} → done
     * 识别结果必须载入 MathField 审查修改后才可入课件（R2：识别不直接定稿）
     */
    handRecognize: (
      body: { strokes: number; hint?: string },
      onEvent: (event: string, data: any) => void,
      signal?: AbortSignal,
    ) => v3Sse('POST', '/teacher-v3/draw/hand-recognize', body, onEvent, signal),
  },

  /* ==================== grading 批改域 ==================== */
  grading: {
    /* B4 发布作业为实例（mock 增量端点；正式契约随 IFC-PRODUCT-03 评审） */
    publish: (body: { title: string; class_id: string; deadline?: string; answer_policy?: string; allow_photo?: boolean; questions: { stem_latex: string; answer?: string; analysis?: string; full_score?: number; kp_name?: string }[] }) =>
      teacherPost<{ ok: true; assignment_id: string; demo: boolean; note: string }>('/teacher-v3/assignments/publish', body),
    /* B4 模拟学生提交（确定性演示数据，明确标注） */
    simulateSubmissions: (id: string) =>
      teacherPost<{ ok: true; demo: boolean }>(`/teacher-v3/grading/assignments/${id}/simulate-submissions`, {}),
    assignments: () => teacherGet<{ items: { id: string; title: string; class_id: string; class_name: string; submitted: number; total: number; graded: number; updated_at: string }[] }>('/teacher-v3/grading/assignments'),
    assignment: (id: string, signal?: AbortSignal) => teacherGet<V3GradingAssignment>(`/teacher-v3/grading/assignments/${id}`, undefined, signal),
    /** 聚类反馈批量确认（AI 起草 → 教师审定） */
    confirmCluster: (assignId: string, clusterId: string, body: { feedback: string }) =>
      teacherPost<{ ok: true }>(`/teacher-v3/grading/assignments/${assignId}/clusters/${clusterId}/confirm`, body),
    /** 讲评生成（SSE）：meta → slide → done */
    reviewPack: (assignId: string, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', `/teacher-v3/grading/assignments/${assignId}/review-pack`, {}, onEvent, signal),
  },

  /* ==================== butler AI 管家域（悬浮球 chat-to-action） ==================== */
  butler: {
    /**
     * 主对话（SSE）：meta → thinking → token* → (tool_call → tool_result)* → card* → action? → citation? → done
     * 意图路由在后端（P0：规则 + 大模型 FC）：数学对话 / 联网搜索 / 图片存题库 / 跳转生成 / 语音公式
     */
    chat: (body: V3ButlerChatInput, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', '/teacher-v3/butler/chat', body, onEvent, signal),
    /**
     * 语音公式专用链（SSE）：asr_partial* → asr_final → card(formula) → done
     * P0 原型用 text 模拟语音输入；P1 挂真 ASR（讯飞/Whisper）
     */
    voiceFormula: (body: V3VoiceFormulaInput, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
      v3Sse('POST', '/teacher-v3/butler/voice-formula', body, onEvent, signal),
    /** 动作确认执行（写操作教师点「执行」后才真正调领域 API，带审计） */
    confirmAction: (actionId: string, body: { params?: Record<string, unknown> }) =>
      teacherPost<{ ok: true; result?: unknown }>(`/teacher-v3/butler/actions/${actionId}/confirm`, body),
    /** 工具目录（文档/调试用） */
    tools: () => teacherGet<{ tools: { name: string; label: string; kind: 'read' | 'write' | 'frontend'; confirm_required: boolean; description: string }[] }>('/teacher-v3/butler/tools'),
  },
}

export type { V3ButlerAction, V3ButlerCard, V3ButlerContext }

/** V3 SSE 通用通道（与 V2 同构）：onEvent(event, data)，abort() 可取消 */
export function v3Sse(method: 'GET' | 'POST', path: string, body?: unknown, onEvent?: (event: string, data: any) => void, signal?: AbortSignal) {
  const ctrl = new AbortController()
  if (signal) {
    if (signal.aborted) ctrl.abort()
    else signal.addEventListener('abort', () => ctrl.abort(), { once: true })
  }
  const finished = fetchEventSource(`/api${path}`, {
    method,
    headers: {
      ...authHeaders(),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      Accept: 'text/event-stream',
    } as Record<string, string>,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    signal: ctrl.signal,
    openWhenHidden: true,
    async onopen(res: Response) {
      if (!res.ok || !(res.headers.get('content-type') || '').includes('text/event-stream')) {
        let detail = `HTTP ${res.status}`
        try { const j = await res.json(); detail = (j as any).message || detail } catch { /* ignore */ }
        throw new Error(detail)
      }
    },
    onmessage(ev: any) {
      if (!ev.event) return
      let data: any
      try { data = JSON.parse(ev.data) } catch { data = { raw: ev.data } }
      onEvent?.(ev.event, data)
    },
    onerror(err: unknown) { throw err },
  })
  return {
    abort: () => ctrl.abort(),
    finished: finished.catch((err: unknown) => {
      if (ctrl.signal.aborted) return { aborted: true } as const
      throw err
    }),
  }
}
