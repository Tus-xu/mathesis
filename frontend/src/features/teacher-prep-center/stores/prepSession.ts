/**
 * 备课中心会话状态源（pinia）。
 *
 * - `sessions`：`GET /prep/sessions` 真实列表（首页「最近备课」）。
 * - `current`：当前工作台会话的权威快照（`GET /prep/conversations/{id}`）。
 * - `sourceTitles`：source_id → 教材标题缓存（经工作区 context 投影解析，跨卡片复用）。
 * - R3：节点编辑（乐观更新+失败回滚）、AI 定向修改（受理+SSE 进度）、
 *   历史版本、教材查看、资料/历史抽屉开关。
 *
 * 所有数据一律来自真实后端；数字/状态/时间禁止前端编造。
 */
import { defineStore } from 'pinia'
import {
  editBlueprintNode,
  fetchTextbookView,
  getWorkspace,
  listBlueprintRevisions,
  createConversation,
  listSessionEvents,
  listSessions,
  PREP_CENTER_API_BASE,
  sendMessage,
  type PrepBlueprintRevision,
  type PrepBlueprintStage,
  PrepCenterApiError,
  type PrepCenterErrorKind,
  type PrepNodeEditPayload,
  type PrepSessionSummary,
  type PrepTextbookView,
  type PrepWorkspaceSnapshot,
} from '@/features/teacher-prep-center/api/prepCenterApi'
import { openPrepEventStream, type PrepEventStream } from '@/features/teacher-prep-center/api/eventsClient'
import { getAccessToken } from '@/api/authSession'
import { v3Api } from '@/api/teacherV3'
import type { V3ClassInfo } from '@/types/teacherV3'
import { mapErrorText, errorKindOf } from '@/features/teacher-prep-center/copy/prepCopy'

/** 每课时标准时长（分钟）——产品约定 1 课时 = 45 分钟。 */
export const MINUTES_PER_LESSON_HOUR = 45

/**
 * 课时数归一（与后端 `intent.py` 同一确定性规则）。
 *
 * 背景：救援模型曾把「45 分钟」填成 `lesson_hours=45`，顶栏由此渲染出
 * 「第45课时 · 2025分钟」这类荒谬元信息（主编实测截图）。后端已在解析阶段归一，
 * 但**历史会话的 intent 已落库**，故展示层再做一次同样的越界归一：
 * 课时数 > 6 时按 45 分钟/课时折算；正常 1~6 原样返回（不改教师真实意图、不猜）。
 */
function normalizeLessonHours(hours: unknown): number | null {
  if (typeof hours !== 'number' || !Number.isFinite(hours) || hours <= 0) return null
  if (hours <= 6) return hours
  return Math.max(1, Math.round(hours / MINUTES_PER_LESSON_HOUR))
}

interface AiStageState {
  stage: string
  label: string
}

interface NodeNoticeState {
  nodeIndex: number
  title: string
}

interface PrepSessionState {
  sessions: PrepSessionSummary[]
  sessionsLoading: boolean
  sessionsError: string | null
  /** 真实班级目录（`GET /teacher-v3/classes`；首页班级胶囊 + 顶栏默认班级兜底共用）。 */
  classes: V3ClassInfo[]
  /** 班级目录是否已加载（含失败：失败也置 true，避免重复请求）。 */
  classesLoaded: boolean
  /** 最近一次列表错误的语义 kind（供首页无权限态判定；非 API 错误为 null）。 */
  sessionsErrorKind: PrepCenterErrorKind | null
  /** source_id → 教材展示名（null = 已解析但服务端暂无投影，显示占位）。 */
  sourceTitles: Record<string, string | null>
  /** section_id → 章节名（null = 无法解析；首页最近备课的「章节」段据此诚实省略）。 */
  sectionTitles: Record<string, string | null>
  current: PrepWorkspaceSnapshot | null
  currentSessionId: string | null
  currentLoading: boolean
  currentError: string | null
  /** 最近一次结构化业务错误（time_budget_exceeded 等；PrepCenterApiError 实例）。 */
  lastError: PrepCenterApiError | null
  creating: boolean
  createError: string | null
  /** 最近一次创建错误的语义 kind（首页据此区分「无班级」软提示等；非 API 错误为 null）。 */
  createErrorKind: PrepCenterErrorKind | null
  /* ── R3：AI 定向修改（SSE 进度）────────────────────────────────── */
  aiBusy: boolean
  aiStage: AiStageState | null
  aiError: string | null
  /** 最近一次 AI 定向修改成功通知（「AI 已更新 03」；展示后由 FE 清除）。 */
  nodeNotice: NodeNoticeState | null
  /* ── R3：抽屉/面板开关（Shell 与课堂页共用）────────────────────── */
  materialsOpen: boolean
  /* 对话抽屉已移除（R4b：对话为内容区三栏主面 /w/:sid/chat，单实现冻结）。 */
  historyOpen: boolean
  history: { blueprint_id: string | null; current_revision: number; items: PrepBlueprintRevision[] } | null
  historyLoading: boolean
  historyError: string | null
  /** 教材查看抽屉（真实切片原文）。 */
  viewer: { open: boolean; loading: boolean; error: string | null; ref: string | null; data: PrepTextbookView | null }
}

function errText(e: unknown): string {
  if (e && typeof e === 'object' && 'detail' in e && typeof (e as { detail?: unknown }).detail === 'string') {
    return (e as { detail: string }).detail
  }
  if (e instanceof Error) return e.message
  return String(e)
}

export const usePrepSessionStore = defineStore('prepCenterSession', {
  state: (): PrepSessionState => ({
    sessions: [],
    sessionsLoading: false,
    sessionsError: null,
    sessionsErrorKind: null,
    classes: [],
    classesLoaded: false,
    sourceTitles: {},
    sectionTitles: {},
    current: null,
    currentSessionId: null,
    currentLoading: false,
    currentError: null,
    lastError: null,
    creating: false,
    createError: null,
    createErrorKind: null,
    /* ── R3：AI 定向修改（SSE 进度）────────────────────────────────── */
    aiBusy: false,
    aiStage: null,
    aiError: null,
    nodeNotice: null,
    /* ── R3：抽屉/面板开关（Shell 与课堂页共用）────────────────────── */
    materialsOpen: false,
    historyOpen: false,
    history: null,
    historyLoading: false,
    historyError: null,
    viewer: { open: false, loading: false, error: null, ref: null, data: null },
  }),

  getters: {
    /** 顶栏课程标题：章节题 > 意图范围 > 会话标题（全部真实字段，缺省诚实占位）。 */
    currentCourseTitle(state): string {
      const ws = state.current
      if (!ws) return ''
      return (
        ws.artifact.context?.section?.title ||
        ws.session.intent?.lesson_scope ||
        ws.session.title ||
        ws.artifact.blueprint?.body?.title ||
        '备课记录'
      )
    },

    /** 当前会话课时数（intent.lesson_hours，缺省 1）。 */
    currentLessonHours(state): number {
      return normalizeLessonHours(state.current?.session.intent?.lesson_hours) ?? 1
    },

    /**
     * 顶栏展示用课时数：**仅真实字段**（intent.lesson_hours 缺失 → null）。
     * 与 currentLessonHours（预算计算用，缺省 1）分开，避免把「缺省 1」渲染成
     * 假的「第1课时」（LEAD-BAR §3.4 禁止空值拼接）。
     */
    lessonHoursDisplay(state): number | null {
      return normalizeLessonHours(state.current?.session.intent?.lesson_hours)
    },

    /** 课时数归一（暴露给首页会话列表等消费方）：同一规则只允许一处实现，
     * 否则顶栏已修好、列表仍显示「第45课时」（口径漂移）。 */
    normalizeLessonHours(): (hours: unknown) => number | null {
      return normalizeLessonHours
    },

    /** 顶栏元信息段：教材版本 / 班级 / 时长（真实字段，缺省段省略不造数）。 */
    currentCourseMeta(state): { textbook: string | null; className: string | null; minutes: number } {
      const ws = state.current
      const textbook = ws?.artifact.context?.source?.title || null
      const className = this.resolveClassName(ws?.session) || null
      return {
        textbook,
        className,
        minutes: this.currentLessonHours * MINUTES_PER_LESSON_HOUR,
      }
    },

    /** 顶栏 Tab 右侧数据源齐备性（教材上下文是否已定位）。 */
    hasContext(state): boolean {
      return !!state.current?.artifact.context
    },

    /* ── R3 课堂设计页派生数据（全部来自权威快照）────────────────── */

    /** 教学流程节点（服务端权威顺序；空安全）。 */
    lessonNodes(state): PrepBlueprintStage[] {
      const flow = state.current?.artifact.blueprint?.body?.lesson_flow
      return Array.isArray(flow) ? flow : []
    },

    /** 节点时长合计（null 计 0 —— 与后端预算校验同口径）。 */
    sumNodeMinutes(state): number {
      return this.lessonNodes.reduce((sum, s) => sum + (typeof s.duration_minutes === 'number' && s.duration_minutes > 0 ? s.duration_minutes : 0), 0)
    },

    /** 全部节点已有时长 → Σ；否则为 null（诚实显示课时预算而非伪合计）。 */
    totalNodeMinutes(state): number | null {
      const nodes = this.lessonNodes
      if (!nodes.length) return null
      const allSet = nodes.every((s) => typeof s.duration_minutes === 'number' && s.duration_minutes > 0)
      return allSet ? this.sumNodeMinutes : null
    },

    /** 课时预算（分钟）：lesson_hours × 45（缺省 1 课时；与后端 lesson_budget_minutes 同源）。 */
    budgetMinutes(state): number {
      return this.currentLessonHours * MINUTES_PER_LESSON_HOUR
    },

    /** 已规划时长（有值的节点合计）→ 剩余可分配预算。 */
    remainingBudget(state): number {
      return this.budgetMinutes - this.sumNodeMinutes
    },

    /** 「已考虑」胶囊：优先真实学情画像摘要。
     * 裁定 2（RULINGS-R14）：禁止用 session.class_name 兜底 —— 那会渲染出无画像支撑的
     * 「已考虑：高二(1)班」臆造学情。
     * R6-S3fix（黄金图顶行右 chip）：未绑定班级 → 无学情画像（实测 DB tp_sessions.class_id
     * 为空、artifact.class_profile_summary 为 null）→ 诚实回退到**真实存在**的教学上下文：
     * 课时范围（context.selection.lesson_scope）+ 教材版本（context.source.title 版本段）；
     * 两者皆缺 → 仍返回 null（消费方 v-if 诚实隐藏），绝不编造。 */
    considerCapsule(state): string | null {
      const ws = state.current
      if (!ws) return null
      if (ws.artifact.class_profile_summary) return ws.artifact.class_profile_summary
      const ctx = ws.artifact.context
      const selection = (ctx?.selection ?? null) as Record<string, unknown> | null
      const scope = typeof selection?.lesson_scope === 'string' ? selection.lesson_scope.trim() : ''
      const sourceTitle = typeof ctx?.source?.title === 'string' ? ctx.source.title.trim() : ''
      /* 「苏教版 · 高中数学 · 选择性必修第一册」→ 版本段「苏教版」（胶囊宽 ≈195px，只取首段） */
      const version = sourceTitle.split('·')[0]?.trim() ?? ''
      const parts = [scope, version].filter((p) => p.length > 0)
      return parts.length ? parts.join(' · ') : null
    },

    /** 教师默认班级名（真实班级目录首项；无班级 → null，消费方诚实省略/占位）。 */
    defaultClassName(state): string | null {
      return (state.classes[0]?.name ?? '').trim() || null
    },

    /** 班级目录 id → 名称（由 `fetchClasses()` 结果构建；供会话按 class_id 解析班级名）。 */
    classNamesById(state): Record<string, string> {
      const map: Record<string, string> = {}
      for (const c of state.classes) {
        const name = (c.name ?? '').trim()
        if (c.class_id && name) map[c.class_id] = name
      }
      return map
    },

    /**
     * 会话班级名解析（唯一口径；顶栏 / 首页列表 / 历史列表 / overview 头卡共用）：
     * `classNamesById[session.class_id]` → `session.class_name` → 教师默认班级 → ''（消费方经 formatSessionMeta 诚实省略班级段）。
     *
     * 背景：教学服务（8100）返回的 `class_name` 由其自身 classes 表按 class_id 反查，
     * 与身份服务（8000）的 id 空间不同，故多为 null —— 必须优先按会话 class_id 在
     * 8000 班级目录里解析；解析不到再退回后端给的 class_name，仍无则退回教师默认班级。
     */
    resolveClassName(state): (session: Pick<PrepSessionSummary, 'class_id' | 'class_name'> | null | undefined) => string {
      const byId = this.classNamesById
      const fallback = this.defaultClassName ?? ''
      return (session) => {
        if (!session) return ''
        const name = session.class_id ? byId[session.class_id] : ''
        if (name) return name
        const backend = (session.class_name ?? '').trim()
        if (backend) return backend
        return fallback
      }
    },
  },

  actions: {
    /** 首页「最近备课」列表（真实接口条数）。 */
    async fetchSessions(signal?: AbortSignal) {
      this.sessionsLoading = true
      this.sessionsError = null
      this.sessionsErrorKind = null
      try {
        const data = await listSessions(signal)
        // archived = 已归档（终态）：不进「最近备课」列表（测试污染会话已由主编置为 archived）
        this.sessions = (data.items || []).filter((s) => s.status !== 'archived')
        this.syncSessionTitles()
        void this.resolveSourceTitles(signal)
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return
        // 禁止后端错误原文上屏：统一归一为产品化中文文案 + 语义 kind（供无权限态判定）
        this.sessionsErrorKind = errorKindOf(e)
        this.sessionsError = mapErrorText(e, '最近备课加载失败，请稍后重试。')
      } finally {
        this.sessionsLoading = false
      }
    },

    /** 真实班级目录（`GET /teacher-v3/classes`；失败不阻塞：置空 + 置已加载，消费方诚实降级）。 */
    async fetchClasses(): Promise<void> {
      if (this.classesLoaded) return
      try {
        const res = await v3Api.catalog.classes()
        this.classes = res.data.items ?? []
      } catch {
        this.classes = []
      } finally {
        this.classesLoaded = true
      }
    },

    /**
     * 解析列表中出现教材（source_id）与章节（section_id）的展示名：
     * 对每个未缓存的 key，取该教材/章节的任一会话拉工作区（context 投影含
     * source.title / section.title），去重后**受控并发**填充两套缓存。
     * - 结果缓存，重复进首页不重复请求；
     * - 上限（key≤12 / 并发≤6）防止长列表拖慢首屏；
     * - 单点失败诚实降级（该段留空 → 消费方省略），绝不阻塞首页。
     */
    async resolveSourceTitles(signal?: AbortSignal) {
      const pendingSources = new Set<string>()
      const pendingSections = new Set<string>()
      for (const s of this.sessions) {
        const sid = s.intent?.source_id
        const sec = s.intent?.section_id
        if (sid && !(sid in this.sourceTitles)) pendingSources.add(sid)
        if (sec && !(sec in this.sectionTitles)) pendingSections.add(sec)
      }
      if (!pendingSources.size && !pendingSections.size) {
        this.syncSessionTitles()
        return
      }

      // 每 key 选一个代表作会话（去重并发）。
      // 教材代表优先用「已定位章节」的会话：其 workspace context 更可能含真实 source.title，
      // 否则会解析成 null，导致列表/顶栏元信息丢失教材段（只剩「班级 · 时长」）。
      const repBySource = new Map<string, string>()
      const srcRepLocated = new Set<string>()
      const repBySection = new Map<string, string>()
      for (const s of this.sessions) {
        const sid = s.intent?.source_id
        const sec = s.intent?.section_id
        if (sid && pendingSources.has(sid) && (!repBySource.has(sid) || (sec && !srcRepLocated.has(sid)))) {
          repBySource.set(sid, s.session_id)
          if (sec) srcRepLocated.add(sid)
        }
        if (sec && pendingSections.has(sec) && !repBySection.has(sec)) repBySection.set(sec, s.session_id)
      }

      // 章节优先（首页「章节」段），再补教材
      const tasks: { sessionId: string }[] = [
        ...[...repBySection.values()].map((sessionId) => ({ sessionId })),
        ...[...repBySource.values()].map((sessionId) => ({ sessionId })),
      ].slice(0, 12)
      if (!tasks.length) return

      let cursor = 0
      const worker = async () => {
        while (cursor < tasks.length) {
          const task = tasks[cursor++]
          try {
            const ws = await getWorkspace(task.sessionId, signal)
            const ctx = ws.artifact.context
            const srcId = ws.session.intent?.source_id ?? null
            const secId = ctx?.section?.id ?? ws.session.intent?.section_id ?? null
            if (srcId) this.sourceTitles = { ...this.sourceTitles, [srcId]: ctx?.source?.title ?? null }
            if (secId) this.sectionTitles = { ...this.sectionTitles, [secId]: ctx?.section?.title ?? null }
          } catch {
            /* 列表展示名解析失败诚实降级为省略，不阻塞首页 */
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(6, tasks.length) }, () => worker()))
      this.syncSessionTitles()
    },

    /**
     * 裁决5（VISUAL-BASELINE §3 冲突 5）标题同步：会话标题与列表标题统一口径。
     * 工作台顶栏标题 = `context.section.title`（currentCourseTitle 首选）；列表行 / 最近备课 /
     * 备课历史读 `summary.title`。历史 tp_sessions.title 未随章节改选回写的会话会漂移
     * （如列表「椭圆的标准方程 · 第一课时」vs 工作台「1.2.1 直线的点斜式方程」）。
     * 此处按「老师最新确认的章节」同源改写展示用 summary.title：
     * 章节真名解析不到时保持原值（诚实保留，不编造标题）。
     */
    syncSessionTitles(): void {
      for (const s of this.sessions) {
        const t = this.sectionTitleOf(s.intent?.section_id)
        if (t && t.trim() && t !== s.title) s.title = t.trim()
      }
    },

    /** 教材展示名（未解析出真实投影时返回 null，由调用方显示占位）。 */
    sourceTitleOf(sourceId: string | null | undefined): string | null {
      if (!sourceId) return null
      return this.sourceTitles[sourceId] ?? null
    },

    /** 章节展示名（未解析出真实投影时返回 null，由调用方诚实省略该段）。 */
    sectionTitleOf(sectionId: string | null | undefined): string | null {
      if (!sectionId) return null
      return this.sectionTitles[sectionId] ?? null
    },

    /** 进入工作台：加载权威快照（重进/刷新均由此还原）。 */
    async loadWorkspace(sessionId: string, signal?: AbortSignal) {
      this.currentLoading = true
      this.currentError = null
      this.currentSessionId = sessionId
      try {
        this.current = await getWorkspace(sessionId, signal)
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return
        this.current = null
        this.currentError = errText(e)
      } finally {
        this.currentLoading = false
      }
    },

    /**
     * NL 新建：POST /conversations（受理制，服务端异步处理）。
     * 成功返回新 session_id；失败置**产品化中文文案** createError（禁后端原文上屏）+ kind。
     */
    async createFromMessage(message: string, classId?: string | null): Promise<string | null> {
      this.creating = true
      this.createError = null
      this.createErrorKind = null
      try {
        const ws = await createConversation({
          idempotency_key: (crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`),
          message,
          class_id: classId ?? null,
        })
        this.current = ws
        this.currentSessionId = ws.session.session_id
        // 新会话入列（真实现置顶语义由后端 updated_at 决定，这里重新拉取）
        void this.fetchSessions()
        return ws.session.session_id
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return null
        this.createErrorKind = errorKindOf(e)
        this.createError = mapErrorText(e, '暂时无法创建备课会话，请稍后重试。')
        return null
      } finally {
        this.creating = false
      }
    },

    /** 选择卡点击（结构化直传，不经 LLM）；完成后刷新工作区快照。 */
    async pickSection(
      sessionId: string,
      selection: { textbook_id?: string; section_id: string },
      displayText: string,
    ): Promise<boolean> {
      const base = this.current
      if (!base) return false
      this.currentLoading = true
      this.currentError = null
      try {
        // expected_revision 为服务端强校验字段（ge=1，缺失即 422）：以快照 revision 为准
        this.current = await sendMessage(sessionId, {
          message: displayText,
          idempotency_key: (crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`),
          expected_revision: base.session.revision,
          selection,
        })
        return true
      } catch (e) {
        if ((e as Error)?.name === 'AbortError') return false
        this.currentError = errText(e)
        return false
      } finally {
        this.currentLoading = false
      }
    },

    /**
     * 静默刷新当前会话（后台 job / 等待 AI 回复的轮询通道；不拨 currentLoading，避免闪烁）。
     * 会话已切换/组件已卸载时丢弃过期响应。
     * - 默认失败静默（下一次 tick 重试）；调用方传 `onError` 时上报失败，但同样不中断轮询。
     * - 返回本次是否成功，供调用方在恢复后清除自己写入的错误态。
     */
    async refreshCurrent(sessionId: string, onError?: (e: unknown) => void): Promise<boolean> {
      try {
        const ws = await getWorkspace(sessionId)
        if (this.currentSessionId === sessionId) this.current = ws
        return true
      } catch (e) {
        /* 轮询失败：默认静默、下一次 tick 重试（终止态由 loadWorkspace/错误通道负责） */
        onError?.(e)
        return false
      }
    },


    /* ════════════════════════════════════════════════════════════════
     * R3 课堂设计页：在线编辑 / AI 定向修改 / 历史 / 教材查看 / SSE
     * ════════════════════════════════════════════════════════════════ */

    /**
     * 教师在线编辑单节点（真实落库：新 revision，edited_by=teacher）。
     * 乐观更新：先改本地快照 → PATCH；失败回滚本地并抛错给调用方展示
     * （time_budget_exceeded 等业务错误由 PrepCenterApiError.data 携带）。
     */
    async editNode(sessionId: string, payload: PrepNodeEditPayload): Promise<boolean> {
      const base = this.current
      if (!base) return false
      const snapshot = JSON.parse(JSON.stringify(base)) as PrepWorkspaceSnapshot
      this.lastError = null
      const flow = base.artifact.blueprint?.body?.lesson_flow
      const node = Array.isArray(flow) ? flow[payload.node_index] : undefined
      if (node) {
        if (payload.title !== undefined && payload.title !== null) node.title = payload.title
        if (payload.duration_minutes !== undefined) node.duration_minutes = payload.duration_minutes
        for (const key of ['teacher_questions', 'student_activities', 'board_notes', 'hints'] as const) {
          if (payload[key] !== undefined) (node as Record<string, unknown>)[key] = payload[key]
        }
        node.edited_by = 'teacher'
        node.edited_at = new Date().toISOString()
      }
      try {
        this.current = await editBlueprintNode(sessionId, payload)
        return true
      } catch (e) {
        this.current = snapshot // 失败回滚（重进/刷新仍以服务端为准）
        this.lastError = e instanceof PrepCenterApiError ? e : null
        this.currentError = errText(e)
        return false
      }
    },

    /**
     * AI 定向修改（受理制 + SSE）：先连事件流 → POST /messages(target_node_id) →
     * ai.stage 阶段文案实时上屏 → blueprint.node.updated → 刷新权威快照 → done 收口。
     * 失败（error 事件）→ aiError 产品化文案；5 分钟无终态按超时收口；全程无前端假进度。
     */
    async sendNodeRevision(sessionId: string, message: string, nodeIndex: number): Promise<boolean> {
      return this.sendAiMessage(sessionId, message, nodeIndex)
    },

    /**
     * 通用续备消息（无「正在修改」目标）：走标准意图解析/蓝图链路（受理制 + SSE 阶段进度）。
     * 终态 = ai.stage(done)（assistant.message.completed / error 亦收口）。
     */
    async sendGeneral(sessionId: string, message: string): Promise<boolean> {
      return this.sendAiMessage(sessionId, message, null)
    },

    /** AI 消息共用实现：nodeIndex=null 时走标准链路；否则附 target_node_id 定向修改。 */
    async sendAiMessage(sessionId: string, message: string, nodeIndex: number | null): Promise<boolean> {
      if (this.aiBusy) return false
      const base = this.current
      if (!base) return false
      this.aiBusy = true
      this.aiError = null
      this.aiStage = null
      this.nodeNotice = null

      let nodeUpdated = false
      let failed = false
      let terminal = false
      let stream: PrepEventStream | null = null
      // 终态判定基线：只认「发送之后」的事件（重连 replay 的历史 done/error 不得误判）
      const baselineSeq = Number(base.session.last_event_seq ?? 0)

      const finish = (ok: boolean, timeoutHit = false): boolean => {
        stream?.close()
        stream = null
        this.aiBusy = false
        this.aiStage = null
        if (timeoutHit && !ok) {
          this.aiError = 'AI 修改耗时过长，已停止等待；可稍后查看历史版本确认结果。'
        }
        return ok
      }

      // 终态兜底（双通道）：
      // 1) durable 事件轮询（代理/网络无关，服务端权威事实）
      // 2) SSE live（生产网关实时推；开发代理可能缓冲）
      // 两者都认 seq > baseline 的事件；300s 无终态按超时收口（诚实提示，不假完成）
      const terminalPromise = new Promise<boolean>((resolve) => {
        const started = Date.now()
        const timer = setInterval(() => {
          void (async () => {
            try {
              const data = await listSessionEvents(sessionId)
              for (const ev of data.items || []) {
                if (Number(ev.seq ?? 0) <= baselineSeq) continue
                const p = (ev.payload ?? {}) as Record<string, unknown>
                if (ev.event_type === 'ai.stage') {
                  this.aiStage = { stage: String(p.stage ?? ''), label: String(p.stage_label ?? '') }
                  if (p.stage === 'done') terminal = true
                } else if (ev.event_type === 'blueprint.node.updated') {
                  nodeUpdated = nodeIndex !== null && Number(p.node_index) === nodeIndex
                } else if (ev.event_type === 'error') {
                  failed = true
                  this.aiError = String(p.message ?? '生成失败，请重试。')
                }
              }
              if (terminal && nodeUpdated) void this.refreshCurrent(sessionId)
            } catch {
              /* 轮询失败下一次重试 */
            }
            if (terminal || failed || Date.now() - started > 300_000) {
              clearInterval(timer)
              resolve(terminal && !failed)
            }
          })()
        }, 3500)
      })

      stream = openPrepEventStream(sessionId, {
        onSnapshot: (ws) => {
          // 快照权威投影：仅在非编辑临界时直接采用（受理处理中字段落库前后一致）
          if (this.currentSessionId === sessionId) this.current = ws
        },
        onEvent: (frame) => {
          const type = frame.event_type
          const p = (frame.payload ?? {}) as Record<string, unknown>
          if (Number(frame.seq ?? 0) <= baselineSeq) return // replay 的历史事件：只驱动刷新，不参与终态
          if (type === 'ai.stage') {
            this.aiStage = { stage: String(p.stage ?? ''), label: String(p.stage_label ?? '') }
            if (p.stage === 'done') terminal = true
          } else if (type === 'blueprint.node.updated') {
            nodeUpdated = nodeIndex !== null && Number(p.node_index) === nodeIndex
            void this.refreshCurrent(sessionId)
          } else if (type === 'error') {
            failed = true
            this.aiError = String(p.message ?? '生成失败，请重试。')
          } else if (
            type === 'conversation.updated' ||
            type === 'blueprint.proposed' ||
            type === 'assistant.message.completed'
          ) {
            void this.refreshCurrent(sessionId)
          }
        },
        onError: () => {
          /* 断线重连由事件流内部处理；终态由 terminalPromise 兜底 */
        },
      })

      try {
        await sendMessage(sessionId, {
          message,
          idempotency_key: (crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`),
          expected_revision: base.session.revision,
          ...(nodeIndex !== null ? { target_node_id: String(nodeIndex) } : {}),
        })
      } catch (e) {
        failed = true
        this.aiError = errText(e)
      }
      if (!failed) {
        const ok = await terminalPromise
        if (!ok && !this.aiError) {
          this.aiError = 'AI 修改耗时过长，已停止等待；可稍后查看历史版本确认结果。'
        }
        if (ok) {
          await this.refreshCurrent(sessionId)
          if (nodeIndex !== null) {
            const node: PrepBlueprintStage | undefined = this.lessonNodes[nodeIndex]
            this.nodeNotice = { nodeIndex, title: String(node?.title ?? `环节 ${nodeIndex + 1}`) }
          }
        }
        return finish(ok)
      }
      return finish(false)
    },

    clearNodeNotice() {
      this.nodeNotice = null
    },

    /** 蓝图历史版本（真实 revision 快照列表）。 */
    async loadHistory(sessionId: string): Promise<void> {
      this.historyLoading = true
      this.historyError = null
      try {
        this.history = await listBlueprintRevisions(sessionId)
      } catch (e) {
        this.historyError = errText(e)
      } finally {
        this.historyLoading = false
      }
    },

    /** 打开历史面板（懒加载：首次打开才拉取）。 */
    async openHistory(sessionId: string): Promise<void> {
      this.historyOpen = true
      if (!this.history) await this.loadHistory(sessionId)
    },

    closeHistory() {
      this.historyOpen = false
    },

    /** 教材查看（真实切片原文；按 ref 拉取并缓存当前视图）。 */
    async openTextbookViewer(sessionId: string, ref: string): Promise<void> {
      this.viewer = { open: true, loading: true, error: null, ref, data: null }
      try {
        const data = await fetchTextbookView(sessionId, ref)
        if (this.viewer.ref === ref) this.viewer.data = data
      } catch (e) {
        if (this.viewer.ref === ref) this.viewer.error = errText(e)
      } finally {
        if (this.viewer.ref === ref) this.viewer.loading = false
      }
    },

    closeTextbookViewer() {
      this.viewer = { open: false, loading: false, error: null, ref: null, data: null }
    },

    /** 资料抽屉（黄金图左下角胶囊与顶栏「资料」共用开关）。 */
    toggleMaterials(open?: boolean) {
      this.materialsOpen = open ?? !this.materialsOpen
    },

    /**
     * 归档会话（真实落库：`POST /prep/sessions/archive`；后端状态机 draft/active → archived 终态）。
     * 首页「删除备课」菜单的真实动作：成功后从列表移除该会话（服务端归档，不物理删除）。
     * 失败返回产品化中文软提示（禁后端原文上屏），列表数据保持不变。
     */
    async archiveSession(sessionId: string, expectedRevision: number): Promise<string | null> {
      try {
        const token = getAccessToken()
        const res = await fetch(`${PREP_CENTER_API_BASE}/sessions/archive`, {
          method: 'POST',
          headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            session_id: sessionId,
            expected_revision: expectedRevision,
            idempotency_key: crypto?.randomUUID?.() ?? `pc-archive-${Date.now()}`,
          }),
        })
        if (!res.ok) {
          const kind: PrepCenterErrorKind =
            res.status === 401 ? 'unauthenticated'
              : res.status === 403 ? 'forbidden'
                : res.status === 409 ? 'conflict'
                  : res.status === 404 ? 'not_found'
                    : res.status === 429 ? 'rate_limited'
                      : 'error'
          return mapErrorText(new PrepCenterApiError({ kind, status: res.status, code: null }))
        }
        this.sessions = this.sessions.filter((s) => s.session_id !== sessionId)
        return null
      } catch {
        return mapErrorText(new PrepCenterApiError({ kind: 'network', status: null, code: null }))
      }
    },
    /** 离开工作台时清理（防跨会话串数据）。 */
    resetCurrent() {
      this.current = null
      this.currentSessionId = null
      this.currentError = null
    },
  },
})
