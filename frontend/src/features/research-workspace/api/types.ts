/**
 * 科研工作台 · 全量 TS 类型（BACKEND-CONTRACT §3 / FRONTEND-ARCH §4）。
 *
 * 命名与后端字段保持一致；后端**可能尚未实现**全部端点，前端类型先行冻结，
 * 端点不可用时由 store 渲染诚实空态（禁止造假数据）。
 */

/** 工作区模式：阅读 / 写作。 */
export type WorkspaceMode = 'read' | 'write'

/* ───────────────────────── 项目 ───────────────────────── */

export interface ResearchProject {
  id: string
  name: string
  description: string | null
  tenant_id?: string
  owner_id?: string
  revision: number
  archived?: boolean
  created_at?: string
  updated_at?: string
  counts?: ProjectCounts | null
}

export interface ProjectCounts {
  sessions?: number
  papers?: number
  drafts?: number
  notes?: number
  data?: number
}

/** AI 项目概览（GET /projects/{id}/overview，BACKEND-CONTRACT §3.7）。 */
export interface ProjectOverview {
  summary: string
  stats: { papers: number; drafts: number; notes: number; data: number }
  updated_at: string
}

/** 历史条目（GET /projects/{id}/history，BACKEND-CONTRACT §3.8 / 宪法 §50）。 */
export interface HistoryEntry {
  kind: 'project' | 'paper' | 'writing' | 'agent' | string
  id: string
  title: string
  updated_at: string
  /** 可选：恢复定位用 */
  paper_id?: string | null
  page?: number | null
  file_path?: string | null
  line?: number | null
}

/** 最近状态（GET /projects/{id}/recent，BACKEND-CONTRACT §3.8）——用于恢复 READ/WRITE。 */
export interface ProjectRecent {
  workspace: WorkspaceMode
  paper_id: string | null
  page: number | null
  file_path: string | null
  line: number | null
  updated_at: string
}

export interface CreateProjectInput {
  name: string
  description?: string
  /** 幂等键（≥8 字符），后端必需 */
  client_request_id: string
}

/**
 * PATCH /projects/{id} 的请求体（BACKEND-CONTRACT §3.1）。
 *
 * 后端 `ResearchProjectUpdate` 要求：`name`(1-120) / `description`(≤500) /
 * `archived` / `expected_revision` / **`client_request_id`（min_length=8，必填）**。
 * 缺 `client_request_id` 会直接 422 —— 这正是「项目无法命名」的根因。
 */
export interface UpdateProjectInput {
  name?: string
  description?: string | null
  archived?: boolean
  /** 乐观并发闸门：必须等于后端当前 revision，否则 409（KERNEL §3）。 */
  expected_revision: number
  /** 幂等键（≥8 字符），后端必需。每次写尝试生成新的。 */
  client_request_id: string
}

/* ───────────────────────── 文献 ───────────────────────── */

export type ParseStatus = 'pending' | 'parsing' | 'ok' | 'failed' | 'skipped'

export interface Paper {
  id: string
  title: string
  authors: string[]
  year: number | null
  venue: string | null
  doi: string | null
  asset_id: string | null
  read_state: string
  page_count: number | null
  parse_status: ParseStatus
  /** 上次阅读页码（后端 `last_page`，用于进入时恢复阅读位置，宪法 §84）。 */
  last_page?: number | null
}

export interface PageBlock {
  text: string
  bbox: [number, number, number, number]
  section?: string
}

export interface PageText {
  page: number
  width: number
  height: number
  blocks: PageBlock[]
}

/** 阅读定位锚点（证据 / 笔记 / Agent 共用，宪法 §14）。 */
export interface Locator {
  paper_id: string
  page: number
  bbox: [number, number, number, number]
  selected_text: string
  section?: string
}

/* ───────────────────────── 笔记 ───────────────────────── */

export type NoteXrefKind = 'paper' | 'agent' | 'manual'

export interface Note {
  id: string
  title: string
  body: string
  paper_id?: string | null
  page?: number | null
  section?: string | null
  selected_text?: string | null
  xref_kind: NoteXrefKind
  xref_id?: string | null
  updated_at: string
}

export interface NoteInput {
  title: string
  body: string
  paper_id?: string | null
  page?: number | null
  section?: string | null
  selected_text?: string | null
  xref_kind?: NoteXrefKind
  xref_id?: string | null
}

/* ───────────────────────── 草稿文件树 ───────────────────────── */

export type FileKind = 'tex' | 'bib' | 'sty' | 'cls' | 'image' | 'pdf' | 'data' | 'dir'

/** 树节点：`type==='dir'` 为目录（后端 `build_tree` 输出），否则为文件。 */
export interface FileNode {
  /** 显示名（末段） */
  name: string
  /** 项目内相对路径（唯一键） */
  path: string
  /** 目录 / 文件 */
  type: 'dir' | 'file'
  /** 文件类型（目录恒为 `dir`） */
  kind: FileKind
  /** 文件当前 revision（目录无） */
  revision?: number
  /** 文件字节数（目录无） */
  size?: number
  updated_at?: string | null
  children?: FileNode[]
}

export interface FileContent {
  path: string
  content: string
  revision: number
  saved_at: string
}

export interface OutlineItem {
  level: number
  title: string
  file: string
  line: number
  kind?: string
}

/* ───────────────────────── 编译 ───────────────────────── */

/**
 * 后端 `research_workspace/models.py` 的编译状态：`running|success|failed|timeout`。
 * 前端另保留 `queued|cancelled` 以兼容契约（异步实现），统一用 `isCompileSuccess()` 判定。
 */
export type CompileStatus =
  | 'queued'
  | 'running'
  | 'success'
  | 'failed'
  | 'timeout'
  | 'cancelled'
  /** 兼容旧契约值（等价于 success）。 */
  | 'completed'

export type CompileLogLevel = 'error' | 'warning' | 'info' | 'success'

export interface CompileLogLine {
  ts: string
  level: CompileLogLevel
  text: string
  file?: string | null
  line?: number | null
}

/** 编译运行（前端归一化后的形状；后端 `run_public_dict` → `.compile_run`）。 */
export interface CompileRun {
  run_id: string
  status: CompileStatus
  engine?: string
  entry?: string
  pdf_artifact_id?: string | null
  pdf_available?: boolean
  /** 失败时保留的上次成功产物（宪法 §23）。 */
  last_successful_pdf_artifact_id?: string | null
  log_lines: CompileLogLine[]
  duration_ms?: number | null
  page_count?: number | null
  pdf_size_bytes?: number | null
  error_count: number
  warning_count: number
  error_code?: string | null
  error_message?: string | null
}

/** 编译是否已结束（成功或失败都算结束）。 */
export function isCompileDone(status: CompileStatus | 'idle'): boolean {
  return ['success', 'completed', 'failed', 'timeout', 'cancelled'].includes(status)
}

/** 编译是否成功产出（`success` 与兼容值 `completed`）。 */
export function isCompileSuccess(status: CompileStatus | 'idle'): boolean {
  return status === 'success' || status === 'completed'
}

/* ───────────────────────── Research Agent ───────────────────────── */

export type ResultKind =
  | 'derivation_check'
  | 'citation'
  | 'latex_compare'
  | 'formula_check'
  | 'translate'
  | 'polish'
  | 'latex_error'
  | 'suggestion'
  | 'chat'

export type ResultStatus =
  | 'verified'
  | 'supported'
  | 'likely'
  | 'needs_conditions'
  | 'insufficient'
  | 'conflict'
  | 'failed'

export interface Finding {
  label: string
  detail: string
  math?: string
}

export interface EvidenceSource {
  paper_id: string
  title: string
  authors: string
  year: number
  page: number
  section: string
  locator?: Locator | null
}

export interface SourceLocation {
  file: string
  line: number
}

export interface ResultPatch {
  file: string
  line: number
  before: string
  after: string
  reason: string
}

export interface ResearchResult {
  kind: ResultKind
  status: ResultStatus
  summary: string
  findings: Finding[]
  evidence: EvidenceSource[]
  source_locations: SourceLocation[]
  patches: ResultPatch[]
  actions: string[]
  confidence: number
}

export type AgentPreset =
  | 'derivation_check'
  | 'citation'
  | 'latex_compare'
  | 'formula_check'
  | 'translate'
  | 'polish'
  | 'latex_error'
  | 'suggestion'

/** Agent 上下文（READ + WRITE 共存，切换工作区**不清理**，FRONTEND-ARCH §5）。 */
export interface AgentContext {
  mode: WorkspaceMode
  paperId?: string | null
  page?: number | null
  section?: string | null
  selection?: string | null
  filePath?: string | null
  line?: number | null
}

export type AgentTaskState = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface AgentTask {
  task_id: string
  status: AgentTaskState
  /** 面向用户的过程活动（如「正在读取当前段落」），**禁止 chain-of-thought**。 */
  activity?: string
  result?: ResearchResult | null
}

export interface KnowledgeHit {
  paper_id: string
  title: string
  page: number
  section: string
  snippet: string
  score: number
}

/** Agent SSE 帧（只驱动 activity，不承载推理链）。 */
export interface AgentStreamEvent {
  type: 'activity' | 'status' | 'result' | 'error' | 'done'
  id?: string
  task_id?: string
  activity?: string
  status?: AgentTaskState
  result?: ResearchResult
  message?: string
}

/* ───────────────────────── 科研对话（宪法 §31）───────────────────────── */

/**
 * 一条会话消息（后端 `domain/snapshot.timeline_item` 的投影）。
 *
 * **自由文本 → 真实对话**：输入框里的话经 `POST /projects/{id}/agent/chat`
 * 落到会话子系统，产出真实 `assistant` 消息；`text` 是 LLM 的回答正文。
 * `component` 为后端**白名单**投影（如 `answer` 类不产生卡片，保持纯文本）。
 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  createdAt?: string
  evidenceRefs?: string[]
  component?: { type: string; props: Record<string, unknown> }
  attachments?: Array<{
    assetId: string
    kind: string
    filename: string
    mediaType: string
    sizeBytes: number
  }>
}

/** `POST /projects/{id}/agent/chat` 的返回。 */
export interface ChatReply {
  /** 承载本段对话的会话 id（同一项目同一用户稳定复用）。 */
  session_id: string | null
  /** 最新一条 assistant 回答（无则 null）。 */
  reply: ChatMessage | null
  /** 本段对话的完整时间线（user + assistant 交替）。 */
  timeline: ChatMessage[]
  /** 后端能力表（`research-message` 的 state 决定输入框可用性）。 */
  capabilities?: Array<{ key: string; label: string; state: string; reason?: string }>
}

/** `GET /projects/{id}/agent/chat` 的返回（重进项目时恢复对话线程）。 */
export interface ChatThread {
  session_id: string | null
  timeline: ChatMessage[]
  capabilities?: ChatReply['capabilities']
}
