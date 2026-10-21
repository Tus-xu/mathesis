/**
 * Research Agent store（FRONTEND-ARCH §5）。
 *
 * 职责边界（关键）：
 *  - **本 store 持有跨工作区的 Research Context**（`context`）与「不丢上下文」的保证：
 *    `context` 同时持 READ（paperId/page/section/selection）与 WRITE（filePath/line），
 *    切 READ/WRITE **只改 `mode`，不清空任何字段**（FRONTEND-ARCH §5 / 宪法 §6）；
 *  - 任务生命周期（轮询 / 结果 / 状态）由 `agent/useAgentTask.ts` 的状态机承载
 *    （模块级单例：关闭 Accordion / 切换工作区都不丢，宪法 §54）。本 store 把该状态机的
 *    响应式状态**透出**为 `task/result/activity/running/streaming/error`，并注册结果回流
 *    到会话线程（`thread`），使 Shell 与面板读到同一份状态；
 *  - 任何修改论文的动作（`applyPatch` / `insertCitation`）**必须由 UI 显式确认后调用**
 *    （宪法 §32/§40）。
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { toRvError } from '../api/client'
import { getChatThread, listTasks, postChatMessage } from '../api/agent'
import { getFile, putFile } from '../api/files'
import { useRwFilesStore } from './files'
import {
  hydrateAgentTask,
  setAgentResultSink,
  startAgentTask,
  useAgentTask,
} from '../agent/useAgentTask'
import type {
  AgentContext,
  AgentPreset,
  ChatMessage,
  ResearchResult,
  ResultPatch,
  WorkspaceMode,
} from '../api/types'

export interface AgentThreadItem {
  id: string
  role: 'user' | 'agent'
  text?: string
  preset?: AgentPreset | null
  result?: ResearchResult
  /** 本条是否为**对话消息**（自由文本闲聊，非能力结果卡）。 */
  chat?: boolean
  at: string
}

function uid(): string {
  return `rw-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

/** 后端会话消息 → 面板线程项（对话消息 `chat: true`，区别于能力结果卡）。 */
function chatToThreadItem(m: ChatMessage): AgentThreadItem {
  return {
    id: m.id || uid(),
    role: m.role === 'user' ? 'user' : 'agent',
    chat: true,
    text: m.text,
    at: m.createdAt || new Date().toISOString(),
  }
}

export const useRwAgentStore = defineStore('rw.agent', () => {
  // 任务状态机（模块级单例）——透出其响应式状态，Shell 与面板共用同一份。
  const taskApi = useAgentTask()
  const preset = taskApi.preset
  const task = taskApi.task
  const result = taskApi.result
  const activity = taskApi.activity
  const running = taskApi.running
  const streaming = taskApi.streaming
  const error = taskApi.error

  /** 跨工作区上下文（READ+WRITE 共存；切 mode 不清理其它字段，宪法 §6）。 */
  const context = ref<AgentContext>({ mode: 'read' })
  /** 研究会话 id（自由对话经会话子系统承载）。 */
  const sessionId = ref<string>('')
  /** 对话请求进行中（输入框据此禁用发送，避免重复投递）。 */
  const chatPending = ref<boolean>(false)
  /** 当前展开的能力卡（决定 Accordion 展开项；切换工作区保留）。 */
  const expandedPreset = ref<AgentPreset | null>(null)
  /** 会话线程（用户请求 + Agent 结果）。 */
  const thread = ref<AgentThreadItem[]>([])

  // 任务结果回流：状态机完成时回调（宪法 §54：任务不因 Accordion 收起而消失）。
  setAgentResultSink((res, presetName) => {
    thread.value = [
      ...thread.value,
      { id: uid(), role: 'agent', preset: presetName, result: res, at: new Date().toISOString() },
    ]
  })

  const mode = computed<WorkspaceMode>(() => context.value.mode)

  /** 合并式更新上下文（切换工作区只改 mode，其余字段保留）。 */
  function setContext(patch: Partial<AgentContext>): void {
    context.value = { ...context.value, ...patch }
  }

  /** 切换工作区：**只改 mode，不清空 READ/WRITE 锚点**（宪法 §6）。 */
  function setMode(next: WorkspaceMode): void {
    if (context.value.mode !== next) context.value = { ...context.value, mode: next }
  }

  function setSession(id: string): void {
    sessionId.value = id || ''
  }

  function setExpanded(next: AgentPreset | null): void {
    expandedPreset.value = next
  }

  /** 聚焦某能力（展开但**不自动执行**，宪法 §78/§79）。 */
  function focusPreset(next: AgentPreset): void {
    expandedPreset.value = next
  }

  /**
   * 运行一个能力（点一下直接跑，**禁止参数表单**，宪法 §30）。
   * 会写入用户请求到会话线程；结果由状态机完成后经 sink 回流。
   */
  async function runPreset(
    projectId: string,
    presetName: AgentPreset,
    extraContext: Record<string, unknown> = {},
    message?: string,
  ): Promise<string | null> {
    expandedPreset.value = presetName
    thread.value = [
      ...thread.value,
      { id: uid(), role: 'user', text: message, preset: presetName, at: new Date().toISOString() },
    ]
    // 后端 `context` 是一袋字段（paper_id/file/query/text/line…）：合并进去，
    // 但**不改 mode**（切工作区只由 setContext({mode}) 负责，宪法 §6）。
    const merged = { ...context.value, ...extraContext, mode: context.value.mode } as AgentContext
    context.value = merged
    return startAgentTask(projectId, presetName, merged, message)
  }

  /**
   * 自由对话（用户直接说话，宪法 §31）——**真实 LLM 对话通道**。
   *
   * 行为：把用户自然语言经 `POST /projects/{id}/agent/chat` 送到**会话子系统**
   * （`message_service.send_message` → LLM 网关），把返回的**真实 assistant 回答**
   * 作为一条 `chat` 消息追加到会话线程。
   *
   * 与「点能力」的分工（关键）：
   *  - 点能力（`runPreset`）→ `POST /agent/tasks` → **结构化结果卡**（本函数不碰）；
   *  - 输入框自由文本（本函数）→ 会话子系统 → **对话回答**。
   *
   * 旧实现把自由文本按关键词 `routeIntent` 到某个 preset，得到的是**能力结果卡**
   * 而非对话回答（宪法 §71「看起来能用」）。该行为已移除，默认路径**一律进对话**。
   */
  async function ask(projectId: string, message: string): Promise<string | null> {
    const text = (message || '').trim()
    if (!text) return null
    if (!projectId) {
      error.value = { code: 'NO_PROJECT', status: 0, message: '尚未选择科研项目。', retryable: false }
      return null
    }
    thread.value = [
      ...thread.value,
      { id: uid(), role: 'user', chat: true, text, at: new Date().toISOString() },
    ]
    error.value = null
    chatPending.value = true
    try {
      const res = await postChatMessage(projectId, { message: text, client_message_id: uid() })
      if (res?.session_id) sessionId.value = res.session_id
      const reply = res?.reply
      if (reply?.text) {
        thread.value = [...thread.value, chatToThreadItem(reply)]
      }
      return res?.session_id ?? (sessionId.value || null)
    } catch (e) {
      error.value = toRvError(e)
      return null
    } finally {
      chatPending.value = false
    }
  }

  /** 应用一条 patch（**必须由用户在 UI 显式确认后调用**）。 */
  async function applyPatch(projectId: string, patch: ResultPatch): Promise<boolean> {
    if (!patch.before && !patch.after) {
      error.value = {
        code: 'PATCH_EMPTY',
        status: 0,
        message: '该修改没有可应用的具体替换内容。',
        retryable: false,
      }
      return false
    }
    try {
      const fc = await getFile(projectId, patch.file)
      const lines = fc.content.split('\n')
      const idx = Math.min(lines.length - 1, Math.max(0, patch.line - 1))
      const current = lines[idx] ?? ''
      if (patch.before && current.includes(patch.before)) {
        lines[idx] = current.replace(patch.before, patch.after)
      } else if (patch.after) {
        lines[idx] = patch.after
      }
      await putFile(projectId, patch.file, lines.join('\n'), fc.revision)
      // 应用修复后**必须同步前端打开的文档**：否则编辑器仍显示旧内容，
      // 用户看到的正是「补丁只进了重编译的 PDF，main.tex 没变」；且本地
      // `revisions[path]` 已过期 → 下一次自动保存必 409（宪法 §51 保存态）。
      await useRwFilesStore().reload(projectId, patch.file)
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  /**
   * 插入引用 `\cite{key}`。复用已有 key：若文件已含该 key 则直接成功，
   * **不重复造条目**（宪法 §37 / ACCEPTANCE C11）。
   */
  async function insertCitation(
    projectId: string,
    filePath: string,
    line: number,
    key: string,
  ): Promise<boolean> {
    try {
      const fc = await getFile(projectId, filePath)
      if (fc.content.includes(`\\cite{${key}}`)) return true
      const lines = fc.content.split('\n')
      const idx = Math.min(lines.length - 1, Math.max(0, line - 1))
      const base = (lines[idx] ?? '').replace(/\s+$/, '')
      lines[idx] = `${base} \\cite{${key}}`
      await putFile(projectId, filePath, lines.join('\n'), fc.revision)
      // 与 applyPatch 同理：同步前端打开的文档与 revision（否则编辑器不更新 + 下次保存 409）。
      await useRwFilesStore().reload(projectId, filePath)
      return true
    } catch (e) {
      error.value = toRvError(e)
      return false
    }
  }

  function clearThread(): void {
    thread.value = []
  }

  /** 只清空结构化结果，保留自由对话。 */
  function clearResults(): void {
    thread.value = thread.value.filter((it) => !it.result)
  }

  /** 已尝试恢复过的项目 id（同一项目只恢复一次，避免重复请求）。 */
  const restoredFor = ref<string>('')

  /**
   * 恢复项目**对话历史**到会话线程（宪法 §84）。
   *
   * 与 `restoreLatest`（恢复能力结果卡）互补：桌面刷新/重进项目后，输入框里
   * 的对话线程不应凭空消失。无历史时静默保持空态（不伪造）。
   */
  async function restoreChat(projectId: string): Promise<void> {
    if (!projectId || running.value) return
    try {
      const res = await getChatThread(projectId)
      if (res?.session_id) sessionId.value = res.session_id
      const timeline = Array.isArray(res?.timeline) ? res.timeline : []
      if (timeline.length) thread.value = timeline.map(chatToThreadItem)
    } catch {
      /* 恢复失败不打扰用户 */
    }
  }

  /**
   * 恢复项目**上次的 Agent 结果**（宪法 §84 状态恢复）。
   *
   * 行为：先恢复**对话线程**，再拉最近 1 条已完成任务 → 放回面板（展开对应能力卡 + 还原结果）。
   * 边界：
   *  - 有任务在跑时不覆盖（`hydrateAgentTask` 内部再挡一层）；
   *  - 同一项目只恢复一次；
   *  - **失败静默**：恢复只是体验优化，失败就回到空态，不打扰用户。
   */
  async function restoreLatest(projectId: string): Promise<void> {
    if (!projectId || running.value) return
    if (restoredFor.value === projectId) return
    restoredFor.value = projectId
    await restoreChat(projectId)
    try {
      const items = await listTasks(projectId, 1)
      const first = items[0]
      if (!first?.result || !first.preset) return
      hydrateAgentTask(first.preset, first.task, first.result)
      expandedPreset.value = first.preset
      // 对话优先面板不再有 Accordion 承载“上次结果”：把它放回同一条时间线。
      thread.value = [
        ...thread.value,
        { id: uid(), role: 'agent', preset: first.preset, result: first.result, at: new Date().toISOString() },
      ]
    } catch {
      /* 恢复失败不打扰用户：面板回到空态即可 */
    }
  }

  function reset(): void {
    context.value = { mode: 'read' }
    sessionId.value = ''
    chatPending.value = false
    expandedPreset.value = null
    thread.value = []
    error.value = null
    restoredFor.value = ''
  }

  return {
    // 任务状态（来自 useAgentTask 状态机）
    preset,
    task,
    result,
    activity,
    running,
    streaming,
    error,
    // 上下文 / 线程
    context,
    mode,
    sessionId,
    chatPending,
    expandedPreset,
    thread,
    setContext,
    setMode,
    setSession,
    setExpanded,
    focusPreset,
    runPreset,
    ask,
    applyPatch,
    insertCitation,
    clearThread,
    clearResults,
    restoreLatest,
    restoreChat,
    reset,
  }
})
