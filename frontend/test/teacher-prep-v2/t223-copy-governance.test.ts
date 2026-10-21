/**
 * T22.3 阶段 C · 硬编码治理回归。
 * 铁律：禁止 UUID 冒充班级名；禁止前端拼装推荐语冒充 Agent 建议；稳定文案走 copy catalog。
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

import ImmersivePrepSurface from '@/features/teacher-prep-v2/surface/ImmersivePrepSurface.vue'
import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import type { WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

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
      revision: 1,
      last_event_seq: 1,
      class_id: 'c0ffee00-0000-4000-8000-000000000001',
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

describe('T22.3 阶段 C · 班级名只认 classes.name', () => {
  it('有 class_id 但无 class_name → 不得用 UUID 截断冒充班级名', async () => {
    const { wrapper } = await mountSurface()
    const text = wrapper.text()
    expect(text).not.toContain('c0ffee00')
    expect(text).not.toContain('班级 c0ffee')
    // 无上下文事实 → 诚实空态 chips
    expect(wrapper.find('[data-testid="prep-context-empty"]').exists()).toBe(true)
  })

  it('有 class_name（classes.name）→ 显示真实班级名', async () => {
    const { wrapper } = await mountSurface({
      session: {
        id: 's-1',
        status: 'active',
        revision: 1,
        last_event_seq: 1,
        class_id: 'c0ffee00-0000-4000-8000-000000000001',
        class_name: '高二(3)班',
        title: '二次函数复习',
      },
    } as never)
    expect(wrapper.find('[data-testid="prep-chip-class"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('高二(3)班')
  })
})

describe('T22.3 阶段 C · 推荐动作只来自 available_actions', () => {
  it('无 available_actions → 不显示前端拼装的建议 chips，显示诚实空态', async () => {
    const { wrapper } = await mountSurface()
    expect(wrapper.findAll('[data-testid="prep-suggestion"]').length).toBe(0)
    expect(wrapper.find('[data-testid="prep-empty-honest"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('围绕「')
    expect(wrapper.text()).not.toContain('结合这个班最近的作业情况')
    expect(wrapper.text()).not.toContain('帮我设计一节 45 分钟')
  })

  it('有 available_actions → 建议 chip 文案来自 copy_key（非拼装句）', async () => {
    const { wrapper } = await mountSurface({
      available_actions: [
        {
          action: 'confirm_proposal',
          target: 'bp-1',
          copy_key: 'prep.action.confirm_proposal',
          disabled: false,
        },
      ],
    } as never)
    const chips = wrapper.findAll('[data-testid="prep-suggestion"]')
    expect(chips.length).toBe(1)
    expect(chips[0].text()).toBe('采用这份方案')
  })

  it('备课中心简化后：投影双 Spec 不再由教师手动触发，动作栏不出现生成按钮', async () => {
    const { wrapper } = await mountSurface({
      timeline: [],
      available_actions: [
        {
          action: 'propose_diagnostic',
          target: 'bp-1',
          copy_key: 'prep.diagnostic.propose',
          disabled: false,
        },
        {
          action: 'project_specs',
          target: 'bp-1',
          copy_key: 'prep.action.project_specs',
          disabled: false,
        },
      ],
    } as never)
    const actions = wrapper.findAll('[data-testid="prep-quick-action"]')
    // 2026-09-14：`project_specs`（生成教案与课件）与 `confirm_proposal` 由
    // `useAutoDeckFlow` 自动执行，教师不再需要在对话里点一次中间步骤
    // （AI 生成结束 → 直达 PPT 编辑工作台）。
    expect(actions.map((node) => node.text())).toEqual(['生成学前诊断'])
  })
})
