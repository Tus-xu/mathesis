/**
 * 备课工作台「对话面」状态源（W1-C）。
 *
 * 铁律：
 * - 消息唯一来源 = 后端真实接口 `GET /prep/conversations/{id}/messages`（刷新/重进完整还原）；
 *   SSE 增量到达 → 重新拉取权威消息流（不做前端增量拼装，避免断线丢字）。
 * - 发送走 `POST /prep/conversations/{id}/messages`（受理制 202），显式携带
 *   `idempotency_key`（幂等：重试复用同一 key，不产生重复消息）与 `expected_revision`
 *   （乐观并发：409 冲突 → 拉权威 revision 后**用同一 key** 重试一次）。
 * - 乐观占位：点击/发送立即插入教师消息（sending），失败转 failed + 行内重试；
 *   不依赖一闪而过的 toast。
 * - 错误文案产品化：禁止后端错误原文直接上屏（网络/限流/冲突 → 中文可读提示）。
 *
 * 本模块是**工作台内共享单例**：PrepWorkspace 负责 bootstrap + 转发 SSE，
 * ClassroomPane（写「正在修改」目标）与 ChatComposer（读目标 / 发送）共享同一状态。
 */
import { reactive } from 'vue'
import {
  PrepCenterApiError,
  getWorkspace,
  listMessages,
  sendMessage,
  type PrepBlueprintStage,
  type PrepSseFrame,
  type PrepTimelineMessage,
} from '../../api/prepCenterApi'
import { newIdempotencyKey } from '../../api/deckApi'
import type { PrepStreamStatus } from '../../api/eventsClient'
import { usePrepSessionStore } from '../../stores/prepSession'

/** 消息本地发送态（服务端消息恒为 undefined）。
 *  `queued` = 服务端「上一条仍在处理中」拒绝（会话串行化）→ 排队等空闲自动续发，
 *  与 `failed` 严格区分：排队不是失败，教师的字不能丢、也不该看见红色错误。 */
export type PrepMessagePending = 'sending' | 'sent' | 'failed' | 'queued'

/** 对话面消息（服务端 MessageView + 本地乐观层）。 */
export interface PrepChatMessage {
  id: string
  seq: number
  role: 'user' | 'assistant' | 'system' | string
  kind: string
  component: { type: string; props: Record<string, unknown> } | null
  text: string | null
  createdAt: string | null
  /* ── 本地乐观层（服务端消息恒为空）──────────────────────────────── */
  pending?: PrepMessagePending
  errorText?: string
  idempotencyKey?: string
  requestText?: string
  selection?: { textbook_id?: string; section_id?: string } | null
  targetNodeId?: number | null
  /** 进入排队态的时刻（ms epoch）；超时兜底用（见 flushQueue）。 */
  enqueuedAt?: number
}

export interface ConversationState {
  sessionId: string | null
  messages: PrepChatMessage[]
  loading: boolean
  error: string | null
  /** AI 处理中（受理后等待助手回复）。 */
  busy: boolean
  /** 进入 busy 的时刻（ms epoch）——会话条「已用时」真实计时用；非 busy 恒为 null。 */
  busySince: number | null
  /** 阶段文案（来自 ai.stage 事件或本地状态机）。 */
  stage: string
  /** SSE 连接状态（断线重连提示）。 */
  streamStatus: PrepStreamStatus
  /** AI 定向修改目标（课堂设计页展开的节点；三 Tab 共享）。 */
  editTarget: { nodeIndex: number; title: string } | null
  /** 底部输入条草稿。 */
  draft: string
  /** 最近一次点选的选择卡选项 id（用于「已选回显」）。 */
  lastSelectionId: string | null
  /** 是否已至少加载过一次消息（区分「加载中」与「空对话」）。 */
  loaded: boolean
}

const state = reactive<ConversationState>({
  sessionId: null,
  messages: [],
  loading: false,
  error: null,
  busy: false,
  busySince: null,
  stage: '',
  streamStatus: 'idle',
  editTarget: null,
  draft: '',
  lastSelectionId: null,
  loaded: false,
})

let pollTimer: ReturnType<typeof setInterval> | null = null
/**
 * 权威消息流在途请求所属会话 id（null = 无在途）。
 *
 * 按**会话维度**去重：仅同一会话的并发刷新合并；**不同会话必须放行**。
 * 2026-09-24 修（P0 空白竞态定位）：原实现用全局布尔 `reloadInFlight`，
 * 旧会话在途请求会把新会话的首帧加载**静默丢弃**（提前 return 且未置 loading），
 * 结果是 `loading=false && loaded=false && messages=[]` → 对话面整片空白，
 * 且无任何通道重新拉取，只有 F5 才能恢复。
 */
let reloadInFlightSid: string | null = null
/** 兜底轮询：SSE 在开发代理下可能被缓冲，首条 AI 回复/后台续写需靠轮询兜底。 */
let watchTimer: ReturnType<typeof setInterval> | null = null
let watchStartedAt = 0
/** 生成态轮询：会话有在途生成（available_actions 含 cancel_run）时刷新权威快照，
 *  待生成结束（cancel_run 消失）即时解除输入条禁用，避免「永远发送不了」。 */
let genTimer: ReturnType<typeof setInterval> | null = null
let genStartedAt = 0
const REPLY_TIMEOUT_MS = 120_000
const POLL_INTERVAL_MS = 1500
const WATCH_POLL_MS = 3000
const WATCH_MAX_MS = 300_000
const GEN_POLL_MS = 3000
const GEN_MAX_MS = 600_000
/** 排队消息的最长等待（服务端持续忙碌 → 转可重试失败，不无限等待）。 */
const QUEUE_MAX_MS = 300_000

/**
 * 等待中的本轮回复（发送受理后登记；终态收口的唯一依据）。
 *
 * 2026-10-04 修（发送后全程有指示，不锁死输入）：120s 未等到回复**不再解除 busy**——
 * 阶段胶囊保持、stage 降级为「本轮处理较慢，仍在进行…」，由兜底轮询继续驱动直至
 * 真实终态（回复到达 / 失败卡落库 / 服务端在途信号全部消失），此后才恢复输入。
 */
interface PendingReply {
  sid: string
  baselineSeq: number
  startedAt: number
  /** 是否已进入「处理较慢」降级态（120s 超时后）。 */
  degraded: boolean
}
let pendingReply: PendingReply | null = null

/** baseline 之后是否已出现服务端 assistant 消息（回复 / 失败卡均算）。 */
function hasReplySince(baselineSeq: number): boolean {
  return state.messages.some(
    (m) => m.role === 'assistant' && !m.pending && m.seq > baselineSeq,
  )
}

/** 本轮终态收口：解除 busy / 阶段胶囊；无回复时诚实提示（不假完成）。
 *  幂等：以 pendingReply 身份校验收口，过期调用直接忽略。 */
async function settleReplyRound(round: PendingReply): Promise<void> {
  if (pendingReply !== round) return
  pendingReply = null
  stopPolling()
  state.busy = false
  state.busySince = null
  state.stage = ''
  if (!hasReplySince(round.baselineSeq)) state.error = 'AI 回复耗时较长，可稍后刷新查看结果。'
  // 回复到达/本轮结束后同步权威快照（蓝图/上下文/动作条随之更新）
  await usePrepSessionStore().refreshCurrent(round.sid)
  syncGenerationPoll()
  syncWatchPoll()
}

/** 降级等待期收口检查（reloadMessages / 各轮询 tick 统一调用）：
 *  - 回复到达 → 正常收口；
 *  - 降级后服务端在途信号全部消失（无在途生成且会话不在处理中）→ 诚实收口；
 *  - 降级超过生成轮询上限（10min）→ 视为卡死，诚实收口（不永久锁住输入）。 */
function settlePendingReply(): void {
  const round = pendingReply
  if (!round || round.sid !== state.sessionId) return
  if (hasReplySince(round.baselineSeq)) {
    void settleReplyRound(round)
    return
  }
  if (!round.degraded) return
  const ws = usePrepSessionStore().current
  const processing = ws?.session?.status === 'processing'
  if ((!isGenerating() && !processing) || Date.now() - round.startedAt > GEN_MAX_MS) {
    void settleReplyRound(round)
  }
}

/** 服务端错误 → 教师可读中文（禁止后端原文 / 对象上屏）。 */
function productError(e: unknown): string {
  if (e instanceof PrepCenterApiError) {
    switch (e.kind) {
      case 'network':
        return '网络连接异常，消息未发送成功，请检查网络后重试。'
      case 'rate_limited':
        return '操作过于频繁，请稍等片刻后重试。'
      case 'conflict':
        // 会话串行化（上一条仍在处理）已由 deliver 分诊为「排队」而非失败；
        // 能走到这里的 conflict 是真实并发冲突且自动重同步也没成功 → 措辞必须诚实说明「未发出」。
        return '会话状态刚刚更新，本条未发出，请重试。'
      case 'unauthenticated':
        return '登录状态已失效，请重新登录后再试。'
      case 'forbidden':
        return '当前会话无权限操作。'
      case 'not_found':
        return '会话不存在或已被删除。'
      default:
        return '发送失败，请重试。'
    }
  }
  if ((e as Error)?.name === 'AbortError') return ''
  return '发送失败，请重试。'
}

/** 最后一次服务端消息 seq（乐观消息 seq 恒为最大，不计入）。 */
function lastServerSeq(): number {
  let max = 0
  for (const m of state.messages) {
    if (!m.pending && Number.isFinite(m.seq) && m.seq > max) max = m.seq
  }
  return max
}

/** 服务端消息 → 前端消息（**防御式**：服务端偶发 null 元素 / 缺字段时不得抛错，
 *  否则整次权威刷新失败 → loaded 永不置位 → 对话面空白）。 */
function normalize(m: PrepTimelineMessage | null | undefined): PrepChatMessage | null {
  if (!m || typeof m !== 'object') return null
  const seq = Number.isFinite(m.seq) ? m.seq : 0
  return {
    id: typeof m.message_id === 'string' && m.message_id ? m.message_id : `srv-${seq}`,
    seq,
    role: typeof m.role === 'string' && m.role ? m.role : 'assistant',
    kind: typeof m.kind === 'string' && m.kind ? m.kind : 'text',
    component: m.component ?? null,
    text: m.text ?? null,
    createdAt: m.created_at ?? null,
  }
}

/** 以服务端消息流为权威重建列表，保留尚未落库的本地乐观消息（sending/queued/failed）。
 *  2026-09-23 修（主编实测）：原实现无条件保留 `failed` 本地消息 → 若服务端其实已落库
 *  （同文本消息已存在），页面会留下**永不消失的幽灵气泡 + 红色错误条**。改为：只要服务端
 *  已出现同角色同文本消息，本地占位（含 failed）一律丢弃 —— 服务端才是权威。 */
function mergeServerMessages(items: PrepTimelineMessage[]): void {
  const server = (Array.isArray(items) ? items : [])
    .map(normalize)
    .filter((m): m is PrepChatMessage => m !== null)
    .sort((a, b) => a.seq - b.seq)
  const kept: PrepChatMessage[] = []
  for (const local of state.messages) {
    if (!local.pending) continue
    const reflected = server.some(
      (s) => s.role === local.role && (s.text ?? '').trim() === (local.text ?? '').trim(),
    )
    if (!reflected) kept.push(local)
  }
  state.messages = [...server, ...kept]
}

/** 拉取权威消息流（刷新/重进/SSE 增量后统一走此函数）。 */
export async function reloadMessages(): Promise<void> {
  const sid = state.sessionId
  if (!sid) return
  // 同一会话并发刷新去重；不同会话必须放行（旧请求不得吞掉新会话首帧加载）
  if (reloadInFlightSid === sid) return
  reloadInFlightSid = sid
  if (!state.messages.length) state.loading = true
  try {
    const items = await listMessages(sid)
    if (state.sessionId === sid) {
      mergeServerMessages(items)
      state.error = null
      state.loaded = true
    }
  } catch (e) {
    if ((e as Error)?.name !== 'AbortError' && state.sessionId === sid) {
      state.error = productError(e)
    }
  } finally {
    // 仅当本次请求仍是该会话的持有者时才清标记（避免旧请求清掉新会话的标记）
    if (reloadInFlightSid === sid) reloadInFlightSid = null
    // 会话已切走时不得收敛 loading（那是新会话的状态）
    if (state.sessionId === sid) state.loading = false
    // 本轮回复可能已随本次权威刷新落库 → 先做终态收口，再同步轮询/续发排队
    settlePendingReply()
    syncWatchPoll()
    syncGenerationPoll()
    // 空闲后自动续发排队消息（不依赖用户再点一次）
    void flushQueue()
  }
}

/** bootstrap：进入 / 切换会话时初始化（会话 id 变化才重置）。 */
export function bootstrapConversation(sessionId: string): void {
  if (!sessionId) return
  if (state.sessionId === sessionId) {
    void reloadMessages()
    return
  }
  stopPolling()
  stopWatchPoll()
  stopGenerationPoll()
  pendingReply = null
  // 会话切换：清掉旧会话的在途标记，保证新会话首帧加载不被吞（旧请求 resolve 时
  // 因 state.sessionId 已变而不会写入，其 finally 也不会清掉新会话的标记）
  reloadInFlightSid = null
  state.sessionId = sessionId
  state.messages = []
  state.error = null
  state.busy = false
  state.busySince = null
  state.stage = ''
  state.editTarget = null
  state.draft = ''
  state.lastSelectionId = null
  state.loaded = false
  void reloadMessages()
}

/** 卸载 / 离开工作台：清理轮询。 */
export function disposeConversation(): void {
  stopPolling()
  stopWatchPoll()
  stopGenerationPoll()
  pendingReply = null
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** 最近一条服务端消息是否为教师消息（= 仍在等待 AI 回复）。 */
function awaitingAssistant(): boolean {
  const server = state.messages.filter((m) => !m.pending)
  if (!server.length) return false
  return server[server.length - 1]?.role === 'user'
}

/**
 * 权威快照是否显示「教师已发言、AI 尚未回复」——独立于本地消息流的自愈信号。
 * 用于兜底：首帧权威消息流若在服务端落库前返回空（`loaded=true && messages=[]`），
 * 本地 `awaitingAssistant()` 为 false，轮询不会启动 → 对话面停在空态且无人重拉。
 * 此处按**服务端权威快照**判定，真实待回复期间持续自愈（非假进度，信号完全来自服务端）。
 */
function snapshotAwaitingReply(): boolean {
  const ws = usePrepSessionStore().current
  if (!ws) return false
  if (ws.session?.status === 'processing') return true
  const tl = Array.isArray(ws.timeline) ? ws.timeline : []
  const last = tl[tl.length - 1]
  return !!last && last.role === 'user'
}

/** 兜底轮询控制：等待 AI 回复 / 有排队消息期间每 3s 拉一次权威消息流（SSE 缓冲时的自愈通道）。 */
function syncWatchPoll(): void {
  const need = !!state.sessionId && (state.busy || awaitingAssistant() || snapshotAwaitingReply() || hasQueued())
  if (need) {
    if (!watchTimer) {
      watchStartedAt = Date.now()
      watchTimer = setInterval(() => {
        // 降级等待（120s 未回复、胶囊保持期）与生成轮询同寿命：上限放宽到 GEN_MAX_MS，
        // 保证「本轮处理较慢」期间消息流仍每 3s 自愈刷新直至真实终态。
        const maxMs = pendingReply?.degraded ? GEN_MAX_MS : WATCH_MAX_MS
        if (
          !state.sessionId ||
          Date.now() - watchStartedAt > maxMs ||
          (!state.busy && !awaitingAssistant() && !snapshotAwaitingReply() && !hasQueued())
        ) {
          stopWatchPoll()
          return
        }
        void reloadMessages()
      }, WATCH_POLL_MS)
    }
  } else {
    stopWatchPoll()
  }
}

function stopWatchPoll(): void {
  if (watchTimer) {
    clearInterval(watchTimer)
    watchTimer = null
  }
  watchStartedAt = 0
}

/** 会话是否处于「在途生成」态：服务端权威信号 = available_actions 含 cancel_run。 */
function isGenerating(): boolean {
  const store = usePrepSessionStore()
  return (store.current?.available_actions ?? []).some((a) => a.action === 'cancel_run')
}

/**
 * 生成态轮询控制：只要会话仍在生成，就周期性刷新权威快照（含 available_actions），
 * 生成结束（cancel_run 消失）即时收口 —— 输入条据此解除禁用。
 */
function syncGenerationPoll(): void {
  const need = !!state.sessionId && isGenerating()
  if (need) {
    if (!genTimer) {
      genStartedAt = Date.now()
      genTimer = setInterval(() => {
        const sid = state.sessionId
        if (!sid || Date.now() - genStartedAt > GEN_MAX_MS || !isGenerating()) {
          stopGenerationPoll()
          // 快照驱动结束（生成结束 / 上限到）→ 给降级等待一次终态收口机会
          settlePendingReply()
          return
        }
        void usePrepSessionStore().refreshCurrent(sid).then(() => settlePendingReply())
      }, GEN_POLL_MS)
    }
  } else {
    stopGenerationPoll()
  }
}

function stopGenerationPoll(): void {
  if (genTimer) {
    clearInterval(genTimer)
    genTimer = null
  }
  genStartedAt = 0
}

/** 是否存在排队待发的消息（服务端「上一条仍在处理中」被拒 → 等空闲续发）。 */
function hasQueued(): boolean {
  return state.messages.some((m) => m.pending === 'queued')
}

/**
 * 空闲后自动续发排队消息（FIFO，一次一条）。
 *
 * 触发点：每次权威消息流刷新后（watch 轮询 / 等待回复结束）。
 * 空闲判定 = 无本地发送、服务端无待回复的教师消息、无在途生成 —— 三个信号都来自真实状态。
 */
async function flushQueue(): Promise<void> {
  const sid = state.sessionId
  if (!sid || state.busy || awaitingAssistant() || isGenerating()) return
  // 超时兜底：排队超过 5 分钟仍未发出（服务端一直忙碌）→ 诚实转为可重试失败，不无限等待
  for (const m of state.messages) {
    if (m.pending === 'queued' && m.enqueuedAt && Date.now() - m.enqueuedAt > QUEUE_MAX_MS) {
      m.pending = 'failed'
      m.enqueuedAt = undefined
      m.errorText = '上一条消息处理时间过长，本条未发出，请重试。'
    }
  }
  const next = state.messages.find((m) => m.pending === 'queued')
  if (!next) return
  await deliver(sid, next)
}

/** AI 定向修改目标（课堂设计页展开节点同步到此；ChatComposer 消费）。 */
export function setEditTarget(nodeIndex: number | null, title?: string): void {
  if (nodeIndex === null) {
    state.editTarget = null
    return
  }
  state.editTarget = { nodeIndex, title: String(title ?? `环节 ${nodeIndex + 1}`) }
}

export function clearEditTarget(): void {
  state.editTarget = null
}

/** SSE 连接状态（PrepWorkspace 转发）。 */
export function setStreamStatus(status: PrepStreamStatus): void {
  state.streamStatus = status
}

/** SSE 事件转发：ai.stage → 阶段文案；终态事件 → 重新拉取消息流。 */
export function onConversationEvent(frame: PrepSseFrame): void {
  const type = frame.event_type
  const p = (frame.payload ?? {}) as Record<string, unknown>
  if (type === 'ai.stage') {
    const label = String(p.stage_label ?? '')
    if (label) state.stage = label
    return
  }
  if (
    type === 'assistant.message.completed' ||
    type === 'conversation.updated' ||
    type === 'conversation.selection.requested' ||
    type === 'blueprint.proposed' ||
    type === 'blueprint.confirmed' ||
    type === 'blueprint.node.updated'
  ) {
    void reloadMessages()
  }
}

/**
 * 发送消息（受理制）。乐观占位 + 幂等重试 + 权威刷新 + 等待助手回复。
 *
 * @param text 文本（选择卡点选时 = 选项 label）
 * @param opts.selection 结构化选择（section_id/textbook_id；点击选择卡走此路径，不经 LLM）
 * @param opts.targetNodeId AI 定向修改目标节点（课堂设计页「正在修改」上下文）
 * @param opts.idempotencyKey 复用幂等键（重试路径）：同键重发不产生重复消息
 */
export async function sendConversationMessage(
  text: string,
  opts?: {
    selection?: { textbook_id?: string; section_id?: string }
    targetNodeId?: number | null
    idempotencyKey?: string
  },
): Promise<void> {
  const sid = state.sessionId
  const message = text.trim()
  if (!sid || !message || state.busy) return

  const key = opts?.idempotencyKey ?? newIdempotencyKey()
  const localId = `local-${key}`

  state.messages.push({
    id: localId,
    seq: Number.MAX_SAFE_INTEGER,
    role: 'user',
    kind: 'text',
    component: { type: 'user_text', props: { text: message } },
    text: message,
    createdAt: new Date().toISOString(),
    pending: 'sending',
    idempotencyKey: key,
    requestText: message,
    selection: opts?.selection ?? null,
    targetNodeId: opts?.targetNodeId ?? null,
  })
  // 最近一次点选的选项 id（章节卡 = 章节 id；教材卡 = 教材源 id）：
  // 非活跃历史卡据此回显真实选中项（对勾 + 名称），教材卡点选同样要能回显
  const pickedId = opts?.selection?.section_id ?? opts?.selection?.textbook_id
  if (pickedId) state.lastSelectionId = pickedId

  const local = state.messages.find((m) => m.id === localId)
  if (local) await deliver(sid, local)
}

/** 权威 revision：直接 GET workspace（不依赖可能滞后的 store 快照）；顺带回填 store。 */
async function authoritativeRevision(sid: string): Promise<number | null> {
  const store = usePrepSessionStore()
  try {
    const ws = await getWorkspace(sid)
    if (store.currentSessionId === sid) store.current = ws
    const rev = ws?.session?.revision
    if (typeof rev === 'number') return rev
  } catch {
    /* 网络失败 → 回退 store 快照 */
  }
  const rev = store.current?.session?.revision
  return typeof rev === 'number' ? rev : null
}

/** 投递单条本地消息（供首发与重试共用；同一幂等键保证不重复）。 */
async function deliver(sid: string, local: PrepChatMessage): Promise<void> {
  const baselineSeq = lastServerSeq()
  const key = local.idempotencyKey as string
  const message = local.requestText as string
  const selection = local.selection
  const targetNodeId = local.targetNodeId ?? null

  local.pending = 'sending'
  local.errorText = undefined
  state.busy = true
  state.busySince = Date.now()
  state.stage = '正在发送…'
  state.error = null

  const buildPayload = (revision: number) => ({
    message,
    idempotency_key: key,
    expected_revision: revision,
    ...(selection ? { selection } : {}),
    ...(targetNodeId !== null ? { target_node_id: String(targetNodeId) } : {}),
  })

  try {
    const revision = await authoritativeRevision(sid)
    if (revision === null) throw new PrepCenterApiError({ kind: 'error', status: null, code: null })

    try {
      await sendMessage(sid, buildPayload(revision))
    } catch (e) {
      // 409：快照过期 → 重新取权威 revision，用**同一幂等键**重试一次
      // （不死循环；同键重放由服务端幂等收口，不产生重复消息）
      if (e instanceof PrepCenterApiError && e.kind === 'conflict') {
        const fresh = await authoritativeRevision(sid)
        if (fresh !== null && fresh !== revision) {
          await sendMessage(sid, buildPayload(fresh))
        } else {
          throw e
        }
      } else {
        throw e
      }
    }

    local.pending = 'sent'
    // 受理成功（202）：全程有指示——阶段胶囊即刻给出真实状态（等待助手回复由下函数驱动）
    state.stage = '已受理·正在理解你的要求'
    await waitForAssistantReply(sid, baselineSeq)
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    // 服务端 409 有三种语义（orchestrator.accept_message）：真实 revision 冲突 /
    // 「上一条消息仍在处理中」/「上一条消息仍在生成中」。后两者是**排队**，不是失败——
    // 2026-09-23 实测缺陷：教师首条未回复时再发一条 → 被拒 → 旧前端标红「会话内容已更新」
    // 并留下幽灵消息，教师以为发送不了。此处按会话真实忙碌态分诊。
    const conflict = e instanceof PrepCenterApiError && e.kind === 'conflict'
    if (conflict && (awaitingAssistant() || isGenerating())) {
      local.pending = 'queued'
      local.enqueuedAt = Date.now()
      local.errorText = undefined
      pendingReply = null
      state.stage = '上一条消息还在处理中，等 AI 回复后自动发送'
      state.busy = false
      state.busySince = null
      state.error = null
      syncWatchPoll()
      return
    }
    local.pending = 'failed'
    local.errorText = productError(e)
    pendingReply = null
    state.stage = ''
    state.busy = false
    state.busySince = null
  }
}

/** 等待助手回复出现（轮询权威消息流）；超时不解锁——保持阶段胶囊降级提示，由兜底轮询驱动至终态。 */
function waitForAssistantReply(sid: string, baselineSeq: number): Promise<void> {
  return new Promise((resolve) => {
    const round: PendingReply = { sid, baselineSeq, startedAt: Date.now(), degraded: false }
    pendingReply = round
    const started = round.startedAt
    const finish = async () => {
      if (pendingReply === round) pendingReply = null
      stopPolling()
      state.busy = false
      state.busySince = null
      state.stage = ''
      const hasReply = hasReplySince(baselineSeq)
      if (!hasReply) state.error = 'AI 回复耗时较长，可稍后刷新查看结果。'
      // 回复到达后同步权威快照（蓝图/上下文/动作条随之更新）
      await usePrepSessionStore().refreshCurrent(sid)
      syncGenerationPoll()
      resolve()
    }
    stopPolling()
    pollTimer = setInterval(() => {
      void (async () => {
        await reloadMessages()
        if (hasReplySince(baselineSeq)) {
          await finish()
          return
        }
        if (Date.now() - started > REPLY_TIMEOUT_MS) {
          // 120s 超时降级：不置 busy=false、不撤胶囊（输入不在此处解锁）——
          // stage 降级为诚实提示，快照/消息双轮询继续直至真实终态（settlePendingReply 收口）。
          if (pendingReply === round) {
            round.degraded = true
            state.stage = '本轮处理较慢，仍在进行…'
            stopPolling()
            void usePrepSessionStore().refreshCurrent(sid)
            syncGenerationPoll()
            syncWatchPoll()
          }
          resolve()
        }
      })()
    }, POLL_INTERVAL_MS)
    // 立即探一次（服务端可能已同步落库）
    void (async () => {
      await reloadMessages()
      if (pendingReply === round && hasReplySince(baselineSeq)) await finish()
    })()
  })
}

/** 重试失败消息（复用同一幂等键 → 服务端不产生重复消息）。 */
export async function retryMessage(id: string): Promise<void> {
  const local = state.messages.find((m) => m.id === id)
  const sid = state.sessionId
  if (!local || local.pending !== 'failed' || !local.requestText || !sid || state.busy) return
  await deliver(sid, local)
}

/** 选择卡点选（结构化回传；不经 LLM）。
 *
 * 2026-10-04 修（选择卡无限重复根因）：卡类型不同，回传的 selection 结构不同——
 * - 教材卡（kind=textbook）：选项 id 是**教材源 id**，只回传 `textbook_id`；
 *   此前把教材 UUID 当 section_id 回传 → 后端 section_id 存脏值、source_id 恒空，
 *   selection_complete 永为 False → 选择卡无限重复。
 * - 其余卡：保持原行为（textbook_id 取 intent.source_id，缺失时用卡上透传的
 *   source_id 兜底），section_id = 选项 id。
 */
export interface SelectionCardInfo {
  /** 卡类型（后端 card props.kind：class / section / textbook / knowledge_points；存量卡缺省 → null）。 */
  kind: string | null
  /** 卡所属教材源 id（后端 card props.source_id；存量卡缺省 → null）。 */
  sourceId: string | null
}

export async function pickSection(
  option: { id: string; label: string },
  card?: SelectionCardInfo | null,
): Promise<void> {
  const store = usePrepSessionStore()
  const selection =
    card?.kind === 'textbook'
      ? { textbook_id: option.id }
      : {
          textbook_id:
            store.current?.session?.intent?.source_id ?? card?.sourceId ?? undefined,
          section_id: option.id,
        }
  await sendConversationMessage(option.label, { selection })
}

/** 底部输入条提交（进入对话面的通用续备消息；带「正在修改」目标时走节点修订）。 */
export async function submitDraft(): Promise<void> {
  const text = state.draft.trim()
  if (!text || state.busy) return
  state.draft = ''
  const target = state.editTarget
  await sendConversationMessage(text, {
    targetNodeId: target ? target.nodeIndex : null,
  })
}

/** 单例访问（模块级共享状态）。 */
export function useConversation() {
  return {
    state,
    bootstrapConversation,
    disposeConversation,
    reloadMessages,
    setEditTarget,
    clearEditTarget,
    setStreamStatus,
    onConversationEvent,
    sendConversationMessage,
    retryMessage,
    pickSection,
    submitDraft,
  }
}

/** 供课堂设计页读取「展开节点」显示名（AI 条上下文胶囊）。 */
export function stageTitle(flow: PrepBlueprintStage[], index: number | null): string {
  if (index === null || index < 0 || index >= flow.length) return ''
  return String(flow[index]?.title ?? `环节 ${index + 1}`)
}

export type { PrepBlueprintStage }
