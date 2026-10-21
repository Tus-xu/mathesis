/**
 * useChat —— 聊天状态与 SSE 流控 composable（从 ChatView 500 行组件内状态抽取）
 *
 * 职责：会话 CRUD+搜索+置顶+分页、消息加载（向上分页）、发送/停止/重新生成/
 *       编辑重发/版本切换/反馈，SSE 事件归约与新端点优雅降级。
 * 消费方：ChatView（完整会话管理）、ClassroomView（manageConversations=false 纯对话）。
 *
 * 视图通过 hooks 订阅副作用（滚动/路由同步/技能提示等），composable 不碰 DOM：
 *   hooks = {
 *     onEvent(event, data, aiMsg)   每个 SSE 事件后调用（滚动用）
 *     onMeta(d)                     meta 事件（技能自动更正提示等）
 *     onConversationId(id)          meta 拿到 conversation_id（路由 replaceState）
 *     onTitle(title)                title 事件 / done 携带标题
 *     onAction(d)                   action 事件（AI 管家跳转确认）
 *     onLatex(d) / onFileParsed(d)  业务联动
 *   }
 */
import { ref, watch } from 'vue'
import { agentApi } from '@/api'
import { streamChat } from '@/api/sse'
import { useToastStore } from '@/stores/toast'
import { useConfirm } from '@/composables/useConfirm'
import {
  applySseEvent, fromHistory, newAssistantMsg, newUserMsg, uuid,
} from '@/components/chat/messageModel'

const CONV_PAGE = 30
const MSG_PAGE = 20
/** 分支类操作（edit/regenerate/activate）完成后重拉消息的窗口大小 */
const BRANCH_REFRESH_LIMIT = 50

export function useChat({
  workspace = 'student',
  scene = '',
  manageConversations = true,
  sendThinking = true,
  hooks = {},
} = {}) {
  const toast = useToastStore()
  const { confirm } = useConfirm()

  /* ===== 状态 ===== */
  const conversations = ref([])
  const convLoading = ref(false)
  const convHasMore = ref(false)
  const convQuery = ref('')
  const activeConvId = ref('')
  const messages = ref([])
  const msgLoading = ref(false)
  const historyHasMore = ref(false)
  const historyLoading = ref(false)
  const streaming = ref(false)
  // 思考模式开关：默认开（深度推理+思考面板）；localStorage 跨会话记忆
  // 迭代15 B7a 延迟治理：思考模式默认关（实测 socratic 37s→~10s），学生可手动开
  const thinkingOn = ref(localStorage.getItem('ma_thinking') === 'on')
  watch(thinkingOn, (v) => localStorage.setItem('ma_thinking', v ? 'on' : 'off'))

  // 联网搜索授权（阶段 6A 预接线）：
  // - webSearchOn：单条请求授权，默认关，发送后自动复位，不持久化；
  // - webSearchOptInEnabled：能力开关（来自 /api/agent/features），
  //   v2 未切流期间恒为 false，前端据此隐藏按钮；读取失败/缺字段回退 false。
  const webSearchOn = ref(false)
  const webSearchOptInEnabled = ref(false)
  async function loadFeatures() {
    try {
      const d = await agentApi.features()
      // 仅接受严格布尔 true；"false"/1/{}/[]/null/undefined/缺字段一律关闭
      const enabled = d?.capabilities?.web_search_opt_in_enabled === true
      webSearchOptInEnabled.value = enabled
      if (!enabled) webSearchOn.value = false
    } catch {
      webSearchOptInEnabled.value = false // fail-closed
      webSearchOn.value = false
    }
  }

  let currentStream = null // { abort, cmid, convId }
  let loadSeq = 0
  let titleTimer = null

  /* ===== 内部工具 ===== */
  function isOfflineNewEndpoint(e) {
    return e?.status === 404 || e?.status === 405 || e?.code === 404 || e?.code === 405
  }

  /** SSE 事件统一归约（chat / regenerate / edit 三端点同一契约） */
  function reduceEvent(aiLive, event, data) {
    if (event === 'action') {
      // v1.4：action 挂到消息上渲染跳转按钮卡（用户主动点击才跳页，不再静默直达）；
      // hooks.onAction 仍调（AI 管家类 action 的确认弹窗由 ChatView 决定是否保留）
      aiLive.action = data
      hooks.onAction?.(data)
      return
    }
    applySseEvent(aiLive, event, data, {
      onMeta: (d) => {
        if (d.conversation_id && d.conversation_id !== activeConvId.value) {
          activeConvId.value = d.conversation_id
          hooks.onConversationId?.(d.conversation_id)
        }
        hooks.onMeta?.(d)
      },
      onLatex: (d) => hooks.onLatex?.(d),
      onFileParsed: (d) => hooks.onFileParsed?.(d),
      onTitle: (t) => {
        applyConversationTitle(t)
        hooks.onTitle?.(t)
      },
      onEdited: (d) => hooks.onEdited?.(d),
    })
  }

  /** title 事件即时更新侧栏标题（M2 §4.3） */
  function applyConversationTitle(title) {
    if (!title) return
    const c = conversations.value.find((x) => x.id === activeConvId.value)
    if (c) c.title = title
  }

  function scheduleTitleRefresh() {
    // 兜底：老后端无 title 事件，done 后 2.5s 等异步标题生成再刷列表
    clearTimeout(titleTimer)
    titleTimer = setTimeout(() => { loadConversations().catch(() => {}) }, 2500)
  }

  /** 流收尾公共逻辑：aborted/异常态标记 + 状态复位 */
  async function settleStream(aiLive, finished, terminatedRef) {
    try {
      const res = await finished
      if (res?.aborted || !terminatedRef.value) {
        aiLive.status = 'aborted'
        aiLive.interrupted = true
      }
      return true
    } catch (e) {
      if (!terminatedRef.value) {
        aiLive.status = 'error'
        aiLive.errorText = e?.message || '连接中断'
      }
      if (isOfflineNewEndpoint(e)) return false // 404/405：新端点未上线，调用方走降级
      if (!terminatedRef.value) toast.error(aiLive.errorText)
      return true
    } finally {
      streaming.value = false
      currentStream = null
    }
  }

  /** 分支操作后重拉活动线程（拿真实 id / versions / 截断结果） */
  async function refreshThread() {
    if (!activeConvId.value) return
    try {
      const d = await agentApi.conversationMessages(activeConvId.value, { limit: BRANCH_REFRESH_LIMIT })
      const items = (d?.items || []).slice().reverse().map(fromHistory)
      if (items.length) {
        messages.value = items
        historyHasMore.value = !!d?.hasMore
      }
    } catch { /* 静默：流已完成，本地状态可用 */ }
  }

  /* ===== 会话管理（manageConversations 时由视图驱动调用） ===== */
  async function loadConversations({ append = false } = {}) {
    if (!manageConversations) return
    if (convLoading.value) return
    convLoading.value = true
    try {
      const params = { limit: CONV_PAGE }
      if (convQuery.value) params.q = convQuery.value
      if (append && conversations.value.length) {
        const last = conversations.value[conversations.value.length - 1]
        params.before = last.updatedAt || last.updated_at || ''
      }
      const d = await agentApi.conversations(params)
      let items = d?.items || (Array.isArray(d) ? d : [])
      // 旧后端忽略 q → 客户端标题过滤兜底
      if (convQuery.value) {
        const q = convQuery.value.toLowerCase()
        items = items.filter((c) => (c.title || '').toLowerCase().includes(q))
      }
      // hasMore：新契约显式给；旧契约用「拿满一页」启发式
      const more = typeof d?.hasMore === 'boolean' ? d.hasMore : items.length >= CONV_PAGE
      if (append) {
        const seen = new Set(conversations.value.map((c) => c.id))
        const fresh = items.filter((c) => !seen.has(c.id))
        conversations.value = [...conversations.value, ...fresh]
        // 旧后端忽略 before 返回同页 → 去重为空即止（防无限滚动死循环）
        convHasMore.value = fresh.length ? more : false
      } else {
        conversations.value = items
        convHasMore.value = more
      }
    } catch (e) {
      toast.error(e?.message || '会话列表加载失败')
    } finally {
      convLoading.value = false
    }
  }

  /** 搜索（侧栏输入防抖后调用） */
  function searchConversations(q) {
    convQuery.value = q || ''
    return loadConversations()
  }

  function loadMoreConversations() {
    if (!convHasMore.value || convLoading.value) return Promise.resolve()
    return loadConversations({ append: true })
  }

  async function openConversation(id) {
    const seqNo = ++loadSeq
    activeConvId.value = id
    messages.value = []
    historyHasMore.value = false
    msgLoading.value = true
    try {
      const d = await agentApi.conversationMessages(id, { limit: MSG_PAGE })
      if (seqNo !== loadSeq) return // 已切走，丢弃过期响应
      messages.value = (d?.items || []).slice().reverse().map(fromHistory)
      historyHasMore.value = !!d?.hasMore
    } catch (e) {
      if (seqNo === loadSeq) toast.error(e?.message || '历史消息加载失败')
    } finally {
      if (seqNo === loadSeq) msgLoading.value = false
    }
  }

  /** 历史向上分页（before 游标）；返回新增条数（视图据此保持滚动位置） */
  async function loadOlderMessages() {
    if (!activeConvId.value || !historyHasMore.value || historyLoading.value) return 0
    historyLoading.value = true
    try {
      const oldest = messages.value.find((m) => m.id)
      const d = await agentApi.conversationMessages(activeConvId.value, {
        limit: MSG_PAGE,
        ...(oldest?.id ? { before: oldest.id } : {}),
      })
      const items = (d?.items || []).slice().reverse()
      const seen = new Set(messages.value.map((m) => m.id).filter(Boolean))
      const fresh = items.map(fromHistory).filter((m) => !m.id || !seen.has(m.id))
      if (!fresh.length) {
        historyHasMore.value = false // 旧后端忽略 before → 同页去重为空即止
        return 0
      }
      messages.value = [...fresh, ...messages.value]
      historyHasMore.value = typeof d?.hasMore === 'boolean' ? d.hasMore : items.length >= MSG_PAGE
      return fresh.length
    } catch (e) {
      toast.error(e?.message || '更早消息加载失败')
      return 0
    } finally {
      historyLoading.value = false
    }
  }

  async function createConversation() {
    const c = await agentApi.createConversation(workspace)
    conversations.value = [c, ...conversations.value]
    ++loadSeq
    activeConvId.value = c.id
    messages.value = []
    historyHasMore.value = false
    return c
  }

  async function deleteConversation(id) {
    const ok = await confirm({
      title: '删除会话',
      message: '确定删除该会话吗？删除后不可恢复。',
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return false
    try {
      await agentApi.deleteConversation(id)
      conversations.value = conversations.value.filter((c) => c.id !== id)
      toast.success('会话已删除')
      if (activeConvId.value === id) {
        ++loadSeq
        activeConvId.value = ''
        messages.value = []
        historyHasMore.value = false
        return { deleted: true, wasActive: true }
      }
      return { deleted: true, wasActive: false }
    } catch (e) {
      toast.error(e?.message || '删除失败')
      return { deleted: false }
    }
  }

  /** 重命名（M2 §2.6；旧后端 405 → 提示未上线，不做本地假改） */
  async function renameConversation(id, title) {
    const t = (title || '').trim()
    if (!t) return false
    try {
      const updated = await agentApi.patchConversation(id, { title: t })
      const c = conversations.value.find((x) => x.id === id)
      if (c) c.title = updated?.title || t
      toast.success('已重命名')
      return true
    } catch (e) {
      if (isOfflineNewEndpoint(e)) toast.info('重命名功能后端尚未上线')
      else toast.error(e?.message || '重命名失败')
      return false
    }
  }

  /** 置顶/取消置顶（乐观更新，失败回滚） */
  async function togglePinConversation(conv) {
    const target = !conv.pinned
    conv.pinned = target
    try {
      await agentApi.patchConversation(conv.id, { pinned: target })
    } catch (e) {
      conv.pinned = !target // 回滚
      if (isOfflineNewEndpoint(e)) toast.info('置顶功能后端尚未上线')
      else toast.error(e?.message || '操作失败')
    }
  }

  /* ===== 发送与 SSE ===== */
  async function doSend(text, {
    attachments = [],
    attachmentTexts = [],
    clientMsgId = '',
    tutorAction = '',
    tutorSessionId = '',
    tutorStepNo = null,
    questionText = '',
    kbDocIds = [],
    replaceKey = '',
    displayText = '',
    skillKeys = [],
    skills = null,
    question = null,
  } = {}) {
    if (streaming.value) return
    const cmid = clientMsgId || uuid()
    if (!replaceKey) {
      // rawText = 真实发送文本（displayText 仅展示；修 regenerate displayText bug 的关键）
      messages.value.push(newUserMsg({ text: displayText || text, clientMsgId: cmid, attachments, skillKeys, rawText: text, question: question || null }))
    }
    const aiMsg = newAssistantMsg(cmid)
    if (replaceKey) {
      const i = messages.value.findIndex((m) => m.key === replaceKey)
      if (i >= 0) messages.value.splice(i, 1, aiMsg)
      else messages.value.push(aiMsg)
    } else {
      messages.value.push(aiMsg)
    }
    // 关键：SSE 事件必须落在响应式代理上（直接改原始对象不触发视图更新）
    const aiLive = messages.value.find((m) => m.key === aiMsg.key) || aiMsg
    streaming.value = true

    // skills = 已解析的技能 id 数组；未显式给时经 hooks.resolveSkills 做 key→id 映射
    // （composable 不依赖技能配置，映射由视图侧注入）
    const skillIds = skills || hooks.resolveSkills?.(skillKeys) || []
    // 联网授权先拍快照再复位：必须同时满足能力开启 + 用户本次授权；
    // 能力关闭时即便 webSearchOn 被程序性置 true 也不携带该字段
    const optedIn = webSearchOptInEnabled.value === true && webSearchOn.value === true
    webSearchOn.value = false
    const payload = {
      message: text,
      context: {
        workspace,
        client_msg_id: cmid,
        ...(scene ? { scene } : {}),
        ...(tutorAction ? { tutor_action: tutorAction } : {}),
        ...(tutorSessionId ? { tutor_session_id: tutorSessionId } : {}),
        ...(tutorStepNo ? { tutor_step_no: tutorStepNo } : {}),
        ...(questionText ? { question_text: questionText } : {}),
        ...(kbDocIds.length ? { kb_doc_ids: kbDocIds } : {}),
        ...(skillIds.length ? { skills: skillIds } : {}),
        ...(sendThinking ? { thinking: thinkingOn.value } : {}),
        ...(optedIn ? { web_search_opt_in: true } : {}),
        ...(attachmentTexts.length ? { attachment_texts: attachmentTexts } : {}),
      },
      ...(activeConvId.value ? { conversation_id: activeConvId.value } : {}),
      ...(attachments.length ? { attachments: attachments.map(({ file_id, kind }) => ({ file_id, kind })) } : {}),
    }

    let terminated = false
    const terminatedRef = { get value() { return terminated } }
    const { abort, finished } = streamChat(payload, {
      onEvent: (event, data) => {
        if (event === 'done' || event === 'error') terminated = true
        reduceEvent(aiLive, event, data)
        if (event === 'done') scheduleTitleRefresh()
        if (event === 'error' && data?.code !== 49901) toast.error(data?.message || '服务繁忙，请稍后重试')
        hooks.onEvent?.(event, data, aiLive)
      },
    })
    currentStream = { abort, cmid, convId: activeConvId.value }
    await settleStream(aiLive, finished, terminatedRef)
    hooks.onEvent?.('stream_end', null, aiLive)
  }

  /** 停止生成：后端置位取消事件（M2 §2.4）+ 本地 abort 双保险 */
  async function stopStreaming() {
    const cur = currentStream
    if (!cur) return
    cur.abort()
    currentStream = null
    if (cur.convId) {
      try { await agentApi.stopChat(cur.convId, cur.cmid) } catch { /* 旧后端 404 静默，本地 abort 已生效 */ }
    }
  }

  /* ===== 重新生成（M2 §2.2，流式替换该气泡；404 → 兼容模式） ===== */
  async function regenerate(aiMsg) {
    if (streaming.value) return
    const msgId = aiMsg.msgId || aiMsg.id
    const convId = activeConvId.value
    if (!msgId || !convId) {
      legacyRegenerate(aiMsg)
      return
    }
    // 原位替换为新的流式气泡
    const idx = messages.value.findIndex((m) => m.key === aiMsg.key)
    if (idx < 0) return
    const aiNew = newAssistantMsg(aiMsg.clientMsgId)
    messages.value.splice(idx, 1, aiNew)
    const aiLive = messages.value.find((m) => m.key === aiNew.key) || aiNew
    streaming.value = true
    hooks.onEvent?.('stream_start', null, aiLive)

    let terminated = false
    const terminatedRef = { get value() { return terminated } }
    const { abort, finished } = streamChat(
      { conversation_id: convId, message_id: msgId },
      {
        path: '/api/agent/chat/regenerate',
        onEvent: (event, data) => {
          if (event === 'done' || event === 'error') terminated = true
          reduceEvent(aiLive, event, data)
          if (event === 'error' && data?.code !== 49901) toast.error(data?.message || '服务繁忙，请稍后重试')
          hooks.onEvent?.(event, data, aiLive)
        },
      },
    )
    currentStream = { abort, cmid: aiMsg.clientMsgId, convId }
    const ok = await settleStream(aiLive, finished, terminatedRef)
    hooks.onEvent?.('stream_end', null, aiLive)

    if (!ok) {
      // 新端点未上线：恢复原气泡，走旧版重发（client_msg_id 幂等）
      messages.value.splice(idx, 1, aiMsg)
      toast.info('后端新版重新生成未上线，已切换兼容模式')
      legacyRegenerate(aiMsg)
      return
    }
    if (terminated && aiLive.status === 'done') await refreshThread() // 成功后拿版本导航数据
  }

  /** 旧版重新生成：复用同一 client_msg_id 重发（幂等重放）；rawText 修复 displayText bug */
  function legacyRegenerate(aiMsg) {
    const userMsg = messages.value.find((m) => m.role === 'user' && m.clientMsgId === aiMsg.clientMsgId)
    if (!userMsg) {
      toast.error('找不到对应的原始提问，无法重新生成')
      return
    }
    doSend(userMsg.rawText || userMsg.text, {
      clientMsgId: aiMsg.clientMsgId,
      replaceKey: aiMsg.key,
      skillKeys: userMsg.skillKeys || [],
    })
  }

  /* ===== 用户消息编辑重发（M2 §2.3，分支截断；404 → 回滚+提示） ===== */
  async function editUserMessage(userMsg, newText) {
    if (streaming.value) return false
    const trimmed = (newText || '').trim()
    if (!trimmed || trimmed === userMsg.text) return false
    const msgId = userMsg.id || userMsg.msgId
    const convId = activeConvId.value
    if (!msgId || !convId) {
      toast.error('该消息暂不支持编辑（刷新历史后可编辑）')
      return false
    }
    const idx = messages.value.findIndex((m) => m.key === userMsg.key)
    if (idx < 0) return false

    // 乐观分支截断：该消息之后全部移除，编辑文本 + 新流式气泡
    const removedTail = messages.value.slice(idx)
    const cmid = uuid()
    const branchUser = newUserMsg({
      text: trimmed,
      rawText: trimmed,
      clientMsgId: cmid,
      attachments: userMsg.attachments || [],
      skillKeys: userMsg.skillKeys || [],
    })
    messages.value.splice(idx, messages.value.length - idx, branchUser)
    const aiMsg = newAssistantMsg(cmid)
    messages.value.push(aiMsg)
    const aiLive = messages.value.find((m) => m.key === aiMsg.key) || aiMsg
    streaming.value = true
    hooks.onEvent?.('stream_start', null, aiLive)

    let terminated = false
    const terminatedRef = { get value() { return terminated } }
    const { abort, finished } = streamChat(
      { conversation_id: convId, message_id: msgId, message: trimmed },
      {
        path: '/api/agent/chat/edit',
        onEvent: (event, data) => {
          if (event === 'done' || event === 'error') terminated = true
          reduceEvent(aiLive, event, data)
          if (event === 'error' && data?.code !== 49901) toast.error(data?.message || '服务繁忙，请稍后重试')
          hooks.onEvent?.(event, data, aiLive)
        },
      },
    )
    currentStream = { abort, cmid, convId }
    const ok = await settleStream(aiLive, finished, terminatedRef)
    hooks.onEvent?.('stream_end', null, aiLive)

    if (!ok) {
      // 端点未上线：回滚本地分支，恢复原消息列表
      messages.value.splice(idx, messages.value.length - idx, ...removedTail)
      toast.info('编辑重发功能后端尚未上线')
      return false
    }
    if (terminated && aiLive.status === 'done') await refreshThread()
    return true
  }

  /* ===== 版本切换（M2 §2.5；activate 后重拉活动线程） ===== */
  async function activateVersion(msg, targetId) {
    if (!targetId || !activeConvId.value) return
    try {
      await agentApi.activateMessage(targetId)
      await refreshThread()
    } catch (e) {
      if (isOfflineNewEndpoint(e)) toast.info('版本切换功能后端尚未上线')
      else toast.error(e?.message || '版本切换失败')
    }
  }

  /* ===== 反馈（M2 §2.9 持久化；value='' 为取消） ===== */
  async function submitFeedback(msg, value, reason = '') {
    const id = msg.msgId || msg.id
    if (!id) {
      toast.error('消息尚未就绪，暂不能反馈')
      return
    }
    const newVal = msg.feedback === value ? '' : value
    try {
      await agentApi.feedback(id, newVal, newVal === 'down' ? reason : '')
      msg.feedback = newVal
      msg.feedbackReason = newVal === 'down' ? reason : ''
      if (newVal) toast.success('感谢反馈')
    } catch (e) {
      toast.error(e?.message || '反馈提交失败')
    }
  }

  /* ===== 清理 ===== */
  function dispose() {
    if (currentStream) {
      currentStream.abort()
      currentStream = null
    }
    clearTimeout(titleTimer)
    ++loadSeq
  }

  return {
    // 状态
    conversations, convLoading, convHasMore, convQuery,
    activeConvId, messages, msgLoading,
    historyHasMore, historyLoading, streaming, thinkingOn,
    // 联网搜索授权（阶段 6A 预接线）
    webSearchOn, webSearchOptInEnabled, loadFeatures,
    // 会话
    loadConversations, searchConversations, loadMoreConversations,
    openConversation, loadOlderMessages,
    createConversation, deleteConversation, renameConversation, togglePinConversation,
    // 消息流
    doSend, stopStreaming, regenerate, editUserMessage, activateVersion, submitFeedback,
    refreshThread, dispose,
  }
}
