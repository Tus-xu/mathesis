/**
 * 聊天消息模型 + SSE 事件归约 + 历史信封映射
 * 契约以 .tmp/m2-chat-refactor-spec.md 第 3 节为准：
 *   meta → status* → (thinking|token|card|graph|figure|action)* → citation?/badge? → title? → done
 *   新增：title（标题即时下发）/ edited（edit 流首个事件）/ done 带 message_id+title?
 *   F13：figure 事件（可视化讲解图形，可多次，frames 渐进帧）
 *   未知事件类型一律静默忽略（向前兼容铁律）。
 */

let seq = 0

/**
 * 防御性过滤：剥掉模型回复文本中的 action JSON 片段（v1.2 管家跳转由 action 事件驱动，
 * 文本里的 {"type":"action","data":{...}} 不应作为正文展示）。
 * 覆盖单行完整 JSON 与夹带在句子中的 JSON 片段两种形态。
 */
export function stripActionJson(text) {
  if (!text) return text
  // 1) 完整行：以 { 开头到 } 结尾的 JSON（含 `event: action` 前缀行）
  let t = text.replace(/^event:\s*action\s*\n?/g, '')
  // 2) 行内夹带：{"type":"action" ... } 片段（贪婪到行尾或下一个换行）
  t = t.replace(/\{"type"\s*:\s*"action"[^}]*\}\s*/g, '')
  // 3) 独立 JSON 块行（含中文逗号后的完整 JSON）
  t = t.replace(/^\s*\{.*?"open_page".*?\}\s*$/gm, '')
  return t
}

export { uuid } from '../../utils/uuid.js'

export function newUserMsg({ text, clientMsgId, attachments = [], skillKeys = [], rawText = '', question = null }) {
  return {
    key: `u_${clientMsgId}`,
    role: 'user',
    text,
    rawText: rawText || text, // 真实发送文本（text 可能被 displayText 覆盖成展示文案；regenerate 必须用 rawText）
    status: 'done',
    clientMsgId,
    attachments: normalizeAttachments(attachments), // 发送时快照，随用户气泡展示（name 归一化 + localUrl 透传）
    skillKeys, // 发送时点亮的技能 key 数组（[] 表示自由对话），气泡上显示技能徽标
    createdAt: new Date().toISOString(),
    versions: null,
    question, // {text, options?} 题目白卡（讲解/变式消息把题干单独成白卡展示）
  }
}

export function newAssistantMsg(clientMsgId) {
  return {
    key: `a_${clientMsgId}_${++seq}`,
    role: 'assistant',
    text: '',
    status: 'streaming', // streaming | done | error | aborted
    clientMsgId, // 对应的用户消息 client_msg_id（重新生成复用）
    msgId: '', // 服务端 envelope msg_id（feedback target）
    skill: '',
    confidence: 0,
    cards: [],
    citations: [],
    badge: '',
    graph: null,
    figures: [], // F13：figure 事件累积（可视化讲解图形，可多次）
    clarify: null, // {question, options[]}
    statuses: [], // [{stage, text}] 过程提示
    notices: [],
    unknownBlocks: [],
    usage: null,
    latencyMs: 0,
    doneMeta: null,
    interrupted: false,
    answerConfirm: false, // 防泄题「直接看答案」二次确认条
    confirmDismissed: false,
    hintLevel: 0,
    hintLevelName: '',
    thinking: '', // M2 重构：模型思考过程（socratic_solver 单路流式下发的 <think> 内容）
    feedback: '', // 'up' | 'down'
    feedbackReason: '',
    errorText: '',
    createdAt: new Date().toISOString(),
    versions: null, // {index, count, ids[]} 兄弟版本元数据（count>1 时显示版本导航）
    action: null, // v1.4：open_page 功能直达（消息内跳转按钮卡，不再静默跳页）
    degraded: null, // 阶段 6B：搜索降级提示 {error_code, message}
  }
}

/**
 * 把一条 SSE 事件应用到 assistant 消息对象上。
 * fx 为副作用回调：{ onMeta(data), onLatex(data), onFileParsed(data) }
 */
export function applySseEvent(msg, event, data, fx = {}) {
  const d = data && typeof data === 'object' ? data : {}
  switch (event) {
    case 'meta':
      msg.msgId = d.msg_id || msg.msgId
      msg.skill = d.skill || ''
      msg.confidence = d.confidence ?? 0
      fx.onMeta?.(d)
      break
    case 'status':
      msg.statuses.push({ stage: d.stage || '', text: d.text || '' })
      break
    case 'token':
      // 防御性过滤：剥掉模型可能输出的 action JSON 片段（v1.2 管家跳转由 action 事件驱动，
      // 文本里的 {"type":"action"...} 不应作为正文展示）
      msg.text += stripActionJson(d.text || '')
      break
    case 'thinking':
      // M2 重构：模型思考过程流式累积（与正文 token 分离，前端折叠面板展示）
      msg.thinking = (msg.thinking || '') + (d.text || '')
      break
    case 'clarify':
      msg.clarify = {
        question: d.question || '',
        options: Array.isArray(d.options) ? d.options : [],
      }
      break
    case 'card': {
      mergeCardInto(msg, d)
      const t = d.type || d.card_type || ''
      if (t === 'socratic_confirm_answer') msg.answerConfirm = true
      if (t === 'socratic_hint') {
        msg.hintLevel = d.level || 0
        msg.hintLevelName = d.level_name || ''
      }
      break
    }
    case 'graph':
      msg.graph = d
      break
    case 'figure':
      // F13：可视化讲解图形（可多次，一个讲解步骤一张图；后端保证同流内不重复）
      if (!Array.isArray(msg.figures)) msg.figures = []
      msg.figures.push(d)
      break
    case 'action':
      // v1.4：open_page 功能直达挂到消息上（渲染跳转按钮卡，用户主动点击才跳）
      msg.action = d
      break
    case 'citation':
      msg.citations = Array.isArray(d.items) ? d.items : []
      break
    case 'badge':
      msg.badge = d.level || ''
      break
    case 'degraded':
      // 阶段 6B：搜索降级提示（web_search 工具降级结果透传）
      msg.degraded = {
        error_code: d.error_code || '',
        message: d.message || d.refuse_reason || '',
      }
      break
    case 'file_parsed':
      msg.statuses.push({ stage: 'file', text: `文件《${d.filename || ''}》解析完成` })
      fx.onFileParsed?.(d)
      break
    case 'latex_rendered':
      fx.onLatex?.(d)
      break
    case 'answer_request':
      // 预留事件名：与 socratic_confirm_answer 卡同效（以后端实际下发为准，两种都接）
      msg.answerConfirm = true
      break
    case 'done':
      msg.status = 'done'
      msg.usage = d.usage || null
      msg.latencyMs = d.latency_ms || 0
      msg.doneMeta = d.meta || null
      // M2：done 携带 assistant 消息 id（feedback/版本导航锚点）与即时标题
      if (d.message_id) msg.msgId = d.message_id
      if (d.title) fx.onTitle?.(d.title)
      break
    case 'title':
      // M2：标题生成完成即时下发（不等 done 后盲刷）
      if (d.title) fx.onTitle?.(d.title)
      break
    case 'edited':
      // M2 edit 流首个事件：新 user 消息 id（用于本地气泡换锚）
      fx.onEdited?.(d)
      break
    case 'error':
      // 49901 = 用户主动停止：按中断态处理（recoverable，不弹错误）
      if (d.code === 49901) {
        msg.status = 'aborted'
        msg.interrupted = true
      } else {
        msg.status = 'error'
        msg.errorText = d.message || '服务繁忙，请稍后重试'
      }
      break
    default:
      break // 未知事件：忽略，不 throw、不阻断流
  }
}

/** 历史消息（GET /conversations/{id}/messages 返回项）→ 展示模型 */
export function fromHistory(item) {
  const env = (item && item.envelope) || {}
  const meta = env.meta || {}
  const blocks = Array.isArray(env.blocks) ? env.blocks : []
  const msg = {
    key: `h_${item.id}`,
    id: item.id,
    role: item.role,
    clientMsgId: item.clientMsgId || '',
    msgId: env.msg_id || item.id,
    text: '',
    status: 'done',
    skill: meta.skill || '',
    confidence: meta.confidence ?? 0,
    cards: [],
    citations: [],
    badge: meta.badge || '',
    graph: null,
    figures: [], // F13：envelope.blocks 的 figure 块还原（见下方循环）
    clarify: null,
    statuses: [],
    notices: [],
    unknownBlocks: [],
    usage: meta.usage || null,
    latencyMs: meta.latency_ms || 0,
    doneMeta: null,
    interrupted: !!(item.interrupted ?? meta.interrupted),
    answerConfirm: false,
    confirmDismissed: true, // 历史里的确认卡不再弹出
    hintLevel: 0,
    hintLevelName: '',
    // M2：反馈/版本/附件/思考持久化字段（旧后端缺失时给默认，优雅降级）
    feedback: item.feedback || '',
    feedbackReason: item.feedbackReason || '',
    versions: normalizeVersions(item.versions),
    attachments: normalizeAttachments(item.attachments),
    thinking: typeof item.thinking === 'string' ? item.thinking : '',
    createdAt: item.createdAt || '',
    errorText: '',
    skillKeys: [],
    action: null, // v1.4：envelope.blocks 的 action 块还原（见下方循环）
    degraded: null, // 阶段 6B：搜索降级提示（envelope.blocks 的 degraded 块还原）
  }
  for (const b of blocks) {
    if (!b || typeof b !== 'object') continue
    if (b.type === 'markdown') {
      msg.text += (msg.text ? '\n\n' : '') + (b.content || '')
    } else if (b.type === 'card') {
      const card = b.data || {}
      mergeCardInto(msg, card)
      const t = card.type || card.card_type || ''
      if (t === 'socratic_hint') {
        msg.hintLevel = card.level || 0
        msg.hintLevelName = card.level_name || ''
      }
    } else if (b.type === 'graph') {
      msg.graph = b
    } else if (b.type === 'figure') {
      // F13：历史回显还原图形卡（{step_no?, caption?, frames[], figure_params?}）
      msg.figures.push(b)
    } else if (b.type === 'action') {
      // v1.4：envelope.blocks 的 action 块（练题中心拦截/AI 管家）还原为按钮卡
      msg.action = b.data || null
    } else if (b.type === 'notice') {
      msg.notices.push(b.content || '')
    } else if (b.type === 'citation') {
      msg.citations = Array.isArray(b.items) ? b.items : []
    } else if (b.type === 'degraded') {
      // 阶段 6B：搜索降级提示块还原
      msg.degraded = {
        error_code: (b.data && b.data.error_code) || '',
        message: (b.data && (b.data.message || b.data.refuse_reason)) || '',
      }
    } else if (b.type === 'thinking') {
      // M2：envelope.blocks 末尾追加的 thinking 块（老契约通道，与新 item.thinking 并存去重）
      if (!msg.thinking) msg.thinking = b.content || ''
    } else if (item.role === 'assistant') {
      msg.unknownBlocks.push(b) // 未注册 block 渲染占位块，绝不白屏
    }
  }
  return msg
}

/**
 * v1.6 变式链逐题发卡合并：后端一道题过好闸就发一张 chain='variant' 卡（同 chain_id），
 * 这里合并成一张链卡（items 渐进增长），QuizSetCard 变式链模式据此渲染进度链与生成中占位。
 * 普通卡不受影响，直接入列。
 */
function mergeCardInto(msg, card) {
  // 兼容历史数据：card 块里装了 action（seed/旧消息）→ 一律还原为按钮卡，不再进 GenericCard
  if (card && (card.type === 'action' || card.card_type === 'action')) {
    msg.action = card
    return
  }
  if (card && card.chain === 'variant' && card.chain_id) {
    const exist = msg.cards.find((c) => c.chain === 'variant' && c.chain_id === card.chain_id)
    if (exist) {
      exist.items = [...(exist.items || []), ...(card.items || [])]
      exist.chain_total = card.chain_total || exist.chain_total
      return
    }
  }
  msg.cards.push(card)
}

/** versions 字段归一化：{index, count, ids[]}；缺字段/旧后端 → null（不显示版本导航） */
function normalizeVersions(v) {
  if (!v || typeof v !== 'object') return null
  const count = Number(v.count) || 0
  const index = Number(v.index) || 1
  const ids = Array.isArray(v.ids) ? v.ids : []
  if (count < 1) return null
  return { index, count, ids }
}

/** attachments 归一化：name/filename 双写兼容（发送快照用 filename，持久化用 name）；localUrl 透传（刚发送图片的本地 objectURL） */
export function normalizeAttachments(list) {
  if (!Array.isArray(list)) return []
  return list
    .filter((a) => a && typeof a === 'object')
    .map((a) => ({
      file_id: a.file_id || a.fileId || '',
      kind: a.kind || (String(a.mime || '').startsWith('image/') ? 'image' : 'doc'),
      name: a.name || a.filename || '附件',
      mime: a.mime || '',
      size: a.size || a.size_bytes || 0,
      localUrl: a.localUrl || '',
    }))
}

/** 时间显示：今天 HH:MM，否则 M/D */
export function fmtTime(iso) {
  if (!iso) return ''
  // 后端 ISO 字符串可能不带时区后缀，按 UTC 处理
  const fixed = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z'
  const d = new Date(fixed)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return hm
  return `${d.getMonth() + 1}/${d.getDate()}`
}

/** 消息时间戳：始终 HH:MM（操作条 hover 显示） */
export function fmtHm(iso) {
  if (!iso) return ''
  const fixed = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z'
  const d = new Date(fixed)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 侧栏分组键：today / week / month / earlier */
export function convGroupKey(iso) {
  if (!iso) return 'earlier'
  const fixed = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : iso + 'Z'
  const d = new Date(fixed)
  if (Number.isNaN(d.getTime())) return 'earlier'
  const now = new Date()
  const sod = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.floor((sod(now) - sod(d)) / 86400000)
  if (days <= 0) return 'today'
  if (days < 7) return 'week'
  if (days < 30) return 'month'
  return 'earlier'
}

export const CONV_GROUPS = [
  { key: 'today', label: '今天' },
  { key: 'week', label: '7 天内' },
  { key: 'month', label: '30 天内' },
  { key: 'earlier', label: '更早' },
]

/** socratic 提示等级中文名（与后端 LEVEL_NAMES_ZH 对齐） */
export const HINT_LEVEL_ZH = { 1: '知识点回顾', 2: '思路方向', 3: '兜底操作' }
