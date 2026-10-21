/**
 * 阶段 6A.1 最小安全补丁：web_search_opt_in 能力开关 fail-closed
 *
 * 覆盖：
 * - loadFeatures 仅接受严格布尔 true（"false"/1/{}/[] 一律关闭）；
 * - 能力由 true 变 false 时 webSearchOn 自动清零；
 * - catch 分支同时复位能力与 webSearchOn；
 * - 发送快照必须同时满足能力开启 + 用户本次授权；
 * - 仅当前请求携带 true，发送后复位，第二条不携带。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { agentApi } from '@/api'
import { streamChat } from '@/api/sse'
import { useChat } from '@/composables/useChat'

vi.mock('@/api', () => ({
  agentApi: {
    features: vi.fn(),
    conversations: vi.fn(),
    createConversation: vi.fn(),
    conversationMessages: vi.fn(),
    deleteConversation: vi.fn(),
    patchConversation: vi.fn(),
    feedback: vi.fn(),
    stopChat: vi.fn(),
    activateMessage: vi.fn(),
    memories: vi.fn(),
    deleteMemory: vi.fn(),
  },
}))

vi.mock('@/api/sse', () => ({
  streamChat: vi.fn(),
}))

vi.mock('@/stores/toast', () => ({
  useToastStore: () => ({ error: vi.fn(), info: vi.fn(), success: vi.fn(), show: vi.fn() }),
}))

vi.mock('@/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn() }),
}))

vi.mock('@/components/chat/messageModel', () => ({
  uuid: () => 'test-uuid',
  newUserMsg: (o: { clientMsgId: string }) => ({ role: 'user', key: o.clientMsgId, ...o }),
  newAssistantMsg: (id: string) => ({ role: 'assistant', key: id, clientMsgId: id, status: 'streaming' }),
  applySseEvent: vi.fn(),
  fromHistory: (i: unknown) => i,
}))

const featuresMock = vi.mocked(agentApi.features)
const streamChatMock = vi.mocked(streamChat)

function mockFeatures(value: unknown) {
  featuresMock.mockResolvedValue(value)
}

function mockStream() {
  streamChatMock.mockReturnValue({ abort: vi.fn(), finished: Promise.resolve() })
}

describe('useChat web search opt-in (阶段 6A.1 fail-closed)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStream()
  })

  it('true → 能力开启', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: true } })
    const chat = useChat()
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(true)
  })

  it.each([false, null, undefined])('%s → 能力关闭', async (v) => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: v } })
    const chat = useChat()
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(false)
  })

  it('缺字段 → 能力关闭', async () => {
    mockFeatures({ capabilities: {} })
    const chat = useChat()
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(false)
  })

  it.each([
    ['string "false"', 'false'],
    ['number 1', 1],
    ['empty object', {}],
    ['empty array', []],
  ])('%s → 能力关闭（非严格布尔一律关闭）', async (_label, v) => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: v } })
    const chat = useChat()
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(false)
  })

  it('能力 true → false：webSearchOn 自动清零', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: true } })
    const chat = useChat()
    await chat.loadFeatures()
    chat.webSearchOn.value = true
    expect(chat.webSearchOn.value).toBe(true)

    mockFeatures({ capabilities: { web_search_opt_in_enabled: false } })
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(false)
    expect(chat.webSearchOn.value).toBe(false)
  })

  it('catch 分支：能力与 webSearchOn 同时复位', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: true } })
    const chat = useChat()
    await chat.loadFeatures()
    chat.webSearchOn.value = true

    featuresMock.mockRejectedValue(new Error('network down'))
    await chat.loadFeatures()
    expect(chat.webSearchOptInEnabled.value).toBe(false)
    expect(chat.webSearchOn.value).toBe(false)
  })

  it('能力 false、webSearchOn 程序性置 true：payload 不含 web_search_opt_in', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: false } })
    const chat = useChat()
    await chat.loadFeatures()
    chat.webSearchOn.value = true // 程序性置位（正常 UI 不会出现）

    await chat.doSend('hello')
    const payload = streamChatMock.mock.calls[0][0]
    expect(payload.context.web_search_opt_in).toBeUndefined()
  })

  it('能力 true 且用户开启：仅当前请求携带 true，发送后复位', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: true } })
    const chat = useChat()
    await chat.loadFeatures()
    chat.webSearchOn.value = true

    await chat.doSend('hello')
    const payload = streamChatMock.mock.calls[0][0]
    expect(payload.context.web_search_opt_in).toBe(true)
    expect(chat.webSearchOn.value).toBe(false)
  })

  it('第二条消息不携带该字段', async () => {
    mockFeatures({ capabilities: { web_search_opt_in_enabled: true } })
    const chat = useChat()
    await chat.loadFeatures()
    chat.webSearchOn.value = true

    await chat.doSend('first')
    await chat.doSend('second')
    const secondPayload = streamChatMock.mock.calls[1][0]
    expect(secondPayload.context.web_search_opt_in).toBeUndefined()
  })
})
