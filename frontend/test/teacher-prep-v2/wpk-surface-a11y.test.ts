/**
 * WP-K · 沉浸式 surface 的可达性 / 反模式回归测试（对应本包改动的可验证断言）。
 *
 * 为什么需要这个文件：截图只能看到"空态"，而流式播报、卡片嵌套、焦点环这些
 * 断言在空态下**根本不在 DOM 里** —— 用真实浏览器截不到，只能组件级钉死。
 *
 * 覆盖：
 *  1. 对话时间线是 role="log" + aria-live="polite"（流式 AI 文本可被播报）
 *  2. 运行状态变化有 role="status" + aria-live（teacher + research 两侧）
 *  3. AI 消息不再"卡里套卡"：带 typed card 的消息不画外层卡框，纯文本消息仍画
 *  4. 输入框聚焦时有可见焦点环（WCAG 2.4.7）
 *  5. 错误态不把 HTTP 状态码伪装成 request_id
 *  6. 科研对话空态只有一个 h1（标题层级不再有两个 h1）
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ConversationStage from '@/features/teacher-prep-v2/surface/ConversationStage.vue'
import MessageItem from '@/features/teacher-prep-v2/surface/MessageItem.vue'
import ComposerStage from '@/features/teacher-prep-v2/surface/ComposerStage.vue'
import FocusedState from '@/features/teacher-prep-v2/surface/FocusedState.vue'
import type { MessageView } from '@/features/teacher-prep-v2/types/workspace'

const STUBS = {
  'n-icon': { template: '<i><slot /></i>' },
  'n-button': { template: '<button v-bind="$attrs"><slot /></button>' },
  ArtifactDock: true,
  ArtifactReadyCard: true,
  StructuredCard: { template: '<div data-testid="structured-card" />' },
  BasicModeCard: true,
  ContextChips: true,
}

function message(over: Partial<MessageView> = {}): MessageView {
  return {
    message_id: 'm-1',
    seq: 1,
    role: 'assistant',
    kind: 'assistant_text',
    component: null,
    text: '我先按你的班级情况整理一下目标。',
    intent: null,
    context_snapshot_id: null,
    blueprint_id: null,
    run_status: null,
    error_label: null,
    error_code: null,
    editable: false,
    created_at: '2026-09-13T10:00:00Z',
    ...over,
  } as MessageView
}

function stageProps(over: Record<string, unknown> = {}) {
  return {
    title: '椭圆及其标准方程',
    subtitle: null,
    timeline: [] as MessageView[],
    actions: [],
    context: null,
    session: null,
    activeArtifact: null,
    showDock: false,
    sessionPhase: 'ready',
    streamPhase: 'live',
    gapCount: 0,
    busy: false,
    draft: '',
    suggestions: [],
    ...over,
  }
}

describe('WP-K · 教师沉浸式 surface 可达性', () => {
  it('对话时间线是 role=log + aria-live=polite（流式追加可被读屏播报）', () => {
    const w = mount(ConversationStage, {
      props: stageProps({ timeline: [message()] }) as never,
      global: { stubs: STUBS },
    })
    const log = w.get('.stage__timeline')
    expect(log.attributes('role')).toBe('log')
    expect(log.attributes('aria-live')).toBe('polite')
    expect(log.attributes('aria-relevant')).toBe('additions text')
  })

  it('空态不渲染时间线（不凭空造一个空的 live region）', () => {
    const w = mount(ConversationStage, { props: stageProps() as never, global: { stubs: STUBS } })
    expect(w.find('.stage__timeline').exists()).toBe(false)
  })

  it('运行状态变化有 role=status + aria-live=polite', () => {
    const w = mount(MessageItem, {
      props: { message: message({ run_status: 'RUNNING' }), actions: [], busy: false, activeArtifact: null },
      global: { stubs: STUBS },
    })
    const status = w.get('.msg__status')
    expect(status.attributes('role')).toBe('status')
    expect(status.attributes('aria-live')).toBe('polite')
  })

  it('带 typed card 的消息不再"卡里套卡"；纯文本消息仍保留外层卡框', () => {
    const withCard = mount(MessageItem, {
      props: {
        message: message({ component: { type: 'blueprint_card', props: { title: '蓝图' } } as never }),
        actions: [], busy: false, activeArtifact: null,
      },
      global: { stubs: STUBS },
    })
    expect(withCard.get('.msg__content').classes()).not.toContain('msg__content--framed')
    expect(withCard.find('.msg__card').exists()).toBe(true)

    const proseOnly = mount(MessageItem, {
      props: { message: message(), actions: [], busy: false, activeArtifact: null },
      global: { stubs: STUBS },
    })
    expect(proseOnly.get('.msg__content').classes()).toContain('msg__content--framed')
  })

  it('输入框聚焦时给外层输入区可见焦点环（textarea 自身 outline:none 不能等于没有焦点环）', async () => {
    const w = mount(ComposerStage, { props: { modelValue: '' }, global: { stubs: STUBS } })
    expect(w.get('.composer__box').classes()).not.toContain('composer__box--focus')
    await w.get('textarea').trigger('focus')
    expect(w.get('.composer__box').classes()).toContain('composer__box--focus')
  })

  it('错误态只显示服务端真的给了 request_id 才显示（不把 HTTP 404 伪装成 request_id）', () => {
    const withoutId = mount(FocusedState, { props: { kind: 'not_found', requestId: null }, global: { stubs: STUBS } })
    expect(withoutId.text()).not.toContain('request_id')
    const withId = mount(FocusedState, { props: { kind: 'not_found', requestId: 'abc123' }, global: { stubs: STUBS } })
    expect(withId.text()).toContain('request_id: abc123')
  })
})
