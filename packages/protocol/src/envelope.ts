/**
 * 应答信封类型定义（§4.4）
 *
 * messages.envelope (JSONB) 的 TypeScript 映射
 */

/** Block 类型枚举 */
export type BlockType =
  | 'markdown'
  | 'citation'
  | 'badge'
  | 'notice'
  | 'skill_trace'
  | 'actions'
  | 'quiz_set'  // M2 预留
  | string       // 未知类型占位

/** Markdown Block */
export interface MarkdownBlock {
  type: 'markdown'
  content: string
}

/** 引用 Block */
export interface CitationBlock {
  type: 'citation'
  items: CitationItem[]
}

export interface CitationItem {
  /** n 对应正文【N】锚点，与《M0-M1技术开发手册》§4.4 / API 文档 §4.1 一致 */
  n: number
  source: string
  loc: string
  chunk_id: string
}

/** 证据等级 Block */
export interface BadgeBlock {
  type: 'badge'
  level: string  // L1-验证 / L2-知识库 / L3-模型
}

/** 通知 Block */
export interface NoticeBlock {
  type: 'notice'
  message: string
  notice_type: 'degrade' | 'refuse' | 'info'
}

/** 技能追踪 Block */
export interface SkillTraceBlock {
  type: 'skill_trace'
  steps: string[]
}

/** 操作 Block */
export interface ActionsBlock {
  type: 'actions'
  items: ActionItem[]
}

export interface ActionItem {
  id: string
  label: string
}

/** 信封元数据 */
export interface EnvelopeMeta {
  skill: string
  confidence: number
  provider: 'spark' | 'deepseek'
  latency_ms: number
  ai_generated: boolean
}

/** 完整应答信封 */
export interface Envelope {
  msg_id: string
  role: 'assistant'
  blocks: (
    | MarkdownBlock
    | CitationBlock
    | BadgeBlock
    | NoticeBlock
    | SkillTraceBlock
    | ActionsBlock
  )[]
  meta: EnvelopeMeta
}

/** 通用 Block（用于前端渲染） */
export type Block =
  | MarkdownBlock
  | CitationBlock
  | BadgeBlock
  | NoticeBlock
  | SkillTraceBlock
  | ActionsBlock
  | { type: string; [key: string]: unknown }  // 未知类型
