/**
 * SSE 事件类型定义（§4.3）
 *
 * 事件顺序链：meta → status* → (token* | clarify) → card* → graph? → citation? → badge? → done
 * 事件类型全集（M1 9 种 + M2 新增 file_parsed/latex_rendered/graph 共 12 种，ADR-010/SSOT §5.4）
 * 前端遇到未知事件类型必须忽略而不是报错——向前兼容铁律
 */

/** meta 事件 - 永远第一个 */
export interface MetaEvent {
  type: 'meta'
  data: {
    conversation_id: string
    msg_id: string
    skill: string
    confidence: number
    provider: 'spark' | 'deepseek'
  }
}

/** status 事件 - 过程提示 */
export interface StatusEvent {
  type: 'status'
  data: {
    stage: string
    text: string  // 如 "正在检索知识库…"
  }
}

/** clarify 事件 - 低置信反问 */
export interface ClarifyEvent {
  type: 'clarify'
  data: {
    question: string
    options: string[]
  }
}

/** token 事件 - LLM 流式增量文本 */
export interface TokenEvent {
  type: 'token'
  data: {
    text: string
  }
}

/** card 事件 - 结构化卡片（M2 启用） */
export interface CardEvent {
  type: 'card'
  data: {
    card_type: string
    payload: unknown
  }
}

/** graph 事件（M2 新增，F11）- 动态几何/函数图像契约；渲染由前端 KaTeX+JSXGraph 完成 */
export interface GraphEvent {
  type: 'graph'
  data: {
    engine: 'jsxgraph'
    schema: Record<string, unknown>
  }
}

/** citation 事件 - 引用来源 */
export interface CitationEvent {
  type: 'citation'
  data: {
    items: Array<{
      /** n 对应正文【N】锚点，与《M0-M1技术开发手册》§4.4 / API 文档 §4.1 一致 */
      n: number
      source: string
      loc: string
      chunk_id: string
    }>
  }
}

/** badge 事件 - 证据等级 */
export interface BadgeEvent {
  type: 'badge'
  data: {
    level: string
  }
}

/** file_parsed 事件（M2 新增，status 段）- 附件解析完成通知（SSOT §5.4 / ADR-018） */
export interface FileParsedEvent {
  type: 'file_parsed'
  data: {
    file_id: string
    filename: string
    status: 'uploaded' | 'parsing' | 'parsed' | 'failed'
    parse_engine: string | null
    summary: string // ≤100 字解析摘要
  }
}

/** latex_rendered 事件（M2 新增，token 段之前）- 语音转 LaTeX 结果注入（SSOT §5.4） */
export interface LatexRenderedEvent {
  type: 'latex_rendered'
  data: {
    latex: string
    source: 'speech'
    ambiguous: boolean // true 时前端须用户确认
  }
}

/** error 事件 - 失败（出现则无 done） */
export interface ErrorEvent {
  type: 'error'
  data: {
    code: number
    message: string
    recoverable: boolean
  }
}

/** done 事件 - 正常结束（正常路径永远最后一个） */
export interface DoneEvent {
  type: 'done'
  data: {
    usage: {
      tokens_in: number
      tokens_out: number
    }
    latency_ms: number
  }
}

/** 所有 SSE 事件的联合类型 */
export type SSEEvent =
  | MetaEvent
  | StatusEvent
  | ClarifyEvent
  | TokenEvent
  | CardEvent
  | GraphEvent
  | CitationEvent
  | BadgeEvent
  | FileParsedEvent
  | LatexRenderedEvent
  | ErrorEvent
  | DoneEvent

/** SSE 事件类型枚举 */
export const SSE_EVENT_TYPES = [
  'meta',
  'status',
  'clarify',
  'token',
  'card',
  'graph',
  'citation',
  'badge',
  'file_parsed',
  'latex_rendered',
  'error',
  'done',
] as const

export type SSEEventType = (typeof SSE_EVENT_TYPES)[number]
