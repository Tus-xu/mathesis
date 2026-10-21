/**
 * T22.1 · 组件级 DOM 断言（阶段 2 的"必须失败则 FAIL"测试）。
 *
 * 钉死 P0 产品形态：
 *  - 默认态固定三栏数 = 0（canvas / evidence rail / job bar 在 DOM 中**不存在**）
 *  - 无 artifact → 无「查看并修改」
 *  - 真实 artifact_ready → 才出现入口；点击后才有画布
 *  - 403 → 聚焦式单状态，无「已就绪」，主错误只出现一次
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

import ImmersivePrepSurface from '@/features/teacher-prep-v2/surface/ImmersivePrepSurface.vue'
import {
  PrepApiError,
  normalizePrepError,
} from '@/features/teacher-prep-v2/api/prepApi'
import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import type { WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

/**
 * 测试环境没有真实 8100。这里**不让网络调用产生假的失败态**：
 * fetch 永远 pending（不 resolve / 不 reject），避免异步 reject 覆盖掉
 * 测试里显式设置的 authoritative snapshot / error。
 * 注意：这不是"生产 mock"——生产路径仍走 `api/prepApi.ts` 的真实 fetch。
 */
vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))

const NAIVE_STUBS = {
  'n-drawer': { props: ['show'], template: '<div v-if="show"><slot /></div>' },
  'n-drawer-content': { template: '<div><slot /></div>' },
  'n-icon': { template: '<i><slot /></i>' },
  'n-button': { template: '<button><slot /></button>' },
}

function snapshot(over: Partial<WorkspaceSnapshot> = {}): WorkspaceSnapshot {
  return {
    session: {
      id: 's-1',
      status: 'active',
      revision: 4,
      last_event_seq: 9,
      class_id: 'c-1',
      title: '二次函数复习',
    },
    timeline: [],
    artifact: { context: null, blueprint: null, diagnostic: null },
    evidence: [],
    available_actions: [],
    job_summary: [],
    basic_mode: false,
    ...over,
  } as WorkspaceSnapshot
}

const CONFIRMED_BLUEPRINT = {
  blueprint_id: 'bp-1',
  session_id: 's-1',
  status: 'confirmed',
  revision: 3,
  title: '二次函数单元蓝图',
  body: {
    objectives: [{ text: '理解顶点式与一般式的互化', source_ref: 'chunk-1' }],
    lesson_flow: [{ title: '导入', items: [{ text: '回顾一次函数图像', source_ref: 'chunk-2' }] }],
  },
}

async function mountSurface(over: Partial<WorkspaceSnapshot> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(ImmersivePrepSurface, {
    props: { sessionId: 's-1' },
    global: { plugins: [pinia], stubs: NAIVE_STUBS },
  })
  const ws = usePrepWorkspaceStore()
  ws.applySnapshot(snapshot(over))
  await nextTick()
  return { wrapper, ws }
}

describe('T22.1 · 默认态：固定三栏 = 0', () => {
  let wrapper: VueWrapper

  beforeEach(async () => {
    ;({ wrapper } = await mountSurface())
  })

  it('无 artifact 时画布 / 证据栏 / Job Bar 在 DOM 中都不存在', () => {
    expect(wrapper.find('[data-testid="prep-artifact-canvas"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-evidence-rail"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-job-bar"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-artifact-ready-card"]').exists()).toBe(false)
  })

  it('默认渲染居中对话舞台', () => {
    expect(wrapper.find('[data-testid="prep-conversation-stage"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prep-composer"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="prep-immersive-surface"]').attributes('data-surface-mode'),
    ).toBe('conversation')
  })

  it('没有 artifact 时不出现「查看并修改」', () => {
    expect(wrapper.find('[data-testid="prep-open-focus"]').exists()).toBe(false)
  })

  it('没有真实上下文时显示诚实空态（不填假班级/教材）', async () => {
    // 移除 class_id → 无任何可确认事实 → 必须显示诚实空态而非 chips
    const ws = usePrepWorkspaceStore()
    ws.applySnapshot(snapshot({ session: { id: 's-1', status: 'active', revision: 1, last_event_seq: 1 } }))
    await nextTick()
    const empty = wrapper.find('[data-testid="prep-context-empty"]')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('还没有确认')
    expect(wrapper.text()).not.toContain('人教版')
  })
})

describe('T22.1 · artifact_ready 才出现入口', () => {
  it('只有 confirmed blueprint → 不再出现任何产物入口（大纲预览已删除）', async () => {
    const { wrapper } = await mountSurface({
      timeline: [
        {
          message_id: 'm-1',
          seq: 1,
          role: 'assistant',
          kind: 'blueprint_proposal',
          component: { type: 'blueprint_card', props: CONFIRMED_BLUEPRINT },
          text: '这是蓝图草案。',
          intent: null,
          context_snapshot_id: null,
          blueprint_id: 'bp-1',
          run_status: null,
          error_label: null,
          error_code: null,
          editable: true,
          created_at: '2026-09-12T00:00:00Z',
        },
      ],
      artifact: { context: null, blueprint: CONFIRMED_BLUEPRINT, diagnostic: null },
    } as never)

    // 2026-09-14 备课中心简化：大纲不再是可打开的产物，也不再要求教师确认。
    expect(wrapper.find('[data-testid="prep-artifact-ready-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-open-focus"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-artifact-dock"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-artifact-canvas"]').exists()).toBe(false)
    // 卡片仍在消息内，但只作为进度陈述（正文与被删掉的确认按钮都不再出现）
    expect(wrapper.find('[data-testid="prep-card-blueprint_card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prep-blueprint-progress"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prep-proposal-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-action-confirm_proposal"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-action-reject_proposal"]').exists()).toBe(false)
  })

  it('课件 spec 存在时页面上不再出现任何「教案」入口', async () => {
    const { wrapper } = await mountSurface({
      session: { id: 's-1', status: 'artifact_ready', revision: 6, last_event_seq: 9, class_id: 'c-1', title: '二次函数复习' },
      artifact: {
        context: null,
        blueprint: CONFIRMED_BLUEPRINT,
        diagnostic: null,
        lesson_plan: { id: 'lp-1', revision: 1, metadata: { title: '二次函数教案' } },
        teaching_deck: { id: 'deck-1', revision: 2, metadata: { title: '二次函数课件' } },
      },
    } as never)

    // 教案 spec 仍是服务端事实（lineage 不变），但页面上不再有任何入口/预览。
    expect(wrapper.text()).not.toContain('教案')
    // 隔离挂载无 router → 不做整页跳转，画布也不会自己冒出来
    expect(wrapper.find('[data-testid="prep-artifact-canvas"]').exists()).toBe(false)
    expect(
      wrapper.find('[data-testid="prep-immersive-surface"]').attributes('data-surface-mode'),
    ).toBe('conversation')
  })

  it('proposal（未确认）不算成熟产物 → 无入口', async () => {
    const { wrapper } = await mountSurface({
      timeline: [
        {
          message_id: 'm-1',
          seq: 1,
          role: 'assistant',
          kind: 'blueprint_proposal',
          component: {
            type: 'blueprint_card',
            props: { ...CONFIRMED_BLUEPRINT, status: 'proposal' },
          },
          text: '草案。',
          intent: null,
          context_snapshot_id: null,
          blueprint_id: 'bp-1',
          run_status: null,
          error_label: null,
          error_code: null,
          editable: true,
          created_at: '2026-09-12T00:00:00Z',
        },
      ],
      artifact: {
        context: null,
        blueprint: { ...CONFIRMED_BLUEPRINT, status: 'proposal' },
        diagnostic: null,
      },
    } as never)

    expect(wrapper.find('[data-testid="prep-artifact-ready-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-open-focus"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-artifact-dock"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-open-evidence"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-propose-edit"]').exists()).toBe(false)
  })
})

describe('T22.1 · 403 聚焦式单状态', () => {
  it('403 → 不渲染对话舞台、不出现「已就绪」、主错误只有一个', async () => {
    const { wrapper, ws } = await mountSurface({
      artifact: { context: null, blueprint: CONFIRMED_BLUEPRINT, diagnostic: null },
    } as never)

    ws.fail(new PrepApiError(normalizePrepError({ status: 403, detail: 'not a member' })))
    await nextTick()

    expect(wrapper.find('[data-testid="prep-primary-error"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="prep-primary-error"]').length).toBe(1)
    expect(wrapper.find('[data-testid="prep-conversation-stage"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-artifact-canvas"]').exists()).toBe(false)

    const text = wrapper.text()
    expect(text).not.toContain('已就绪')
    expect(text).toContain('没有访问这个备课会话的权限')
    // 403 不提供「已就绪」暗示；提供返回路径
    expect(wrapper.find('[data-testid="prep-focused-back"]').exists()).toBe(true)
  })

  it('404 / 401 / 409 各自给出对应恢复动作', async () => {
    const cases = [
      { status: 404, testId: 'prep-focused-back' },
      { status: 401, testId: 'prep-focused-signin' },
      { status: 409, testId: 'prep-focused-refresh' },
    ]
    for (const c of cases) {
      const { wrapper, ws } = await mountSurface()
      ws.fail(new PrepApiError(normalizePrepError({ status: c.status })))
      await nextTick()
      expect(wrapper.find(`[data-testid="${c.testId}"]`).exists()).toBe(true)
      wrapper.unmount()
    }
  })
})

describe('T22.1 · 会话消息身份与结构化附件', () => {
  it('为教师与智学助教显示角色、服务端时间，并保持左右语法', async () => {
    const { wrapper } = await mountSurface({
      timeline: [
        {
          message_id: 'm-teacher',
          seq: 1,
          role: 'user',
          kind: 'user_text',
          component: { type: 'user_text', props: { text: '请帮我安排这节课。' } },
          text: '请帮我安排这节课。',
          intent: null,
          context_snapshot_id: null,
          blueprint_id: null,
          run_status: null,
          error_label: null,
          error_code: null,
          editable: false,
          created_at: '2026-09-12T00:01:00Z',
        },
        {
          message_id: 'm-assistant',
          seq: 2,
          role: 'assistant',
          kind: 'assistant_text',
          component: { type: 'blueprint_card', props: CONFIRMED_BLUEPRINT },
          text: '我整理了一份教学方案。',
          intent: null,
          context_snapshot_id: null,
          blueprint_id: 'bp-1',
          run_status: null,
          error_label: null,
          error_code: null,
          editable: true,
          created_at: '2026-09-12T00:02:00Z',
        },
      ],
    } as never)

    const teacher = wrapper.find('[data-testid="prep-message-m-teacher"]')
    const assistant = wrapper.find('[data-testid="prep-message-m-assistant"]')
    expect(teacher.classes()).toContain('msg--user')
    expect(teacher.find('.msg__role-label').text()).toBe('教师')
    expect(teacher.find('time').text()).toContain('08:01')
    expect(teacher.find('time').attributes('datetime')).toBe('2026-09-12T00:01:00Z')
    expect(teacher.find('.msg__bubble').classes()).toContain('msg__bubble')
    expect(teacher.find('.msg__content--user').exists()).toBe(true)
    expect(assistant.classes()).toContain('msg--assistant')
    expect(assistant.find('.msg__role-label').text()).toBe('智学助教')
    expect(assistant.find('time').text()).toContain('08:02')
    expect(assistant.find('.msg__content--assistant').exists()).toBe(true)
    // 2026-09-14：blueprint_card 不再是需要教师决策的提案卡（大纲预览已删除），
    // 只作为一句进度陈述存在。
    expect(assistant.find('[data-testid="prep-card-blueprint_card"]').exists()).toBe(true)
    expect(assistant.find('[data-testid="prep-proposal-card"]').exists()).toBe(false)
  })
})
