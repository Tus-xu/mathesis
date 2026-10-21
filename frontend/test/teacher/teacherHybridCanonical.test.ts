import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

import PrepSurfaceRoute from '@/features/teacher-prep-v2/surface/PrepSurfaceRoute.vue'
import PrepCenterView from '@/pages/teacher-v3/prep/PrepCenterView.vue'
import { usePrepWorkspaceStore } from '@/features/teacher-prep-v2/stores/prepWorkspace'
import { __resetAutoDeckFlowForTest } from '@/features/teacher-prep-v2/surface/useAutoDeckFlow'
import type { WorkspaceSnapshot } from '@/features/teacher-prep-v2/types/workspace'

vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {})))

const STUBS = {
  'n-icon': { template: '<i><slot /></i>' },
  'n-drawer': { props: ['show'], template: '<div v-if="show"><slot /></div>' },
  'n-drawer-content': { template: '<div><slot /></div>' },
  'n-button': { template: '<button><slot /></button>' },
}

const snapshot: WorkspaceSnapshot = {
  session: { id: 'session-canonical', status: 'artifact_ready', revision: 9, last_event_seq: 5, class_name: '高二一班', title: '二次函数复习' },
  timeline: [
    {
      message_id: 't23-direct', seq: 1, role: 'assistant', kind: 'assistant_text',
      component: { type: 'direct_chunk_locator', props: { source_id: 'book-1', section_path: ['函数'], chunk_id: 'chunk-2', chunk_ordinal: 2, content_hash: 'sha256:demo' } },
      text: '以下是本次方案使用的教材依据。', intent: null, context_snapshot_id: null, blueprint_id: null,
      run_status: null, error_label: null, error_code: null, editable: false, created_at: '2026-09-13T01:00:00Z',
    },
    {
      message_id: 't23-profile', seq: 2, role: 'assistant', kind: 'assistant_text',
      component: { type: 'class_learning_profile', props: { sample_size: 12, coverage: 0.5, confidence: 'medium' } },
      text: '这是班级学习画像。', intent: null, context_snapshot_id: null, blueprint_id: null,
      run_status: null, error_label: null, error_code: null, editable: false, created_at: '2026-09-13T01:01:00Z',
    },
  ],
  artifact: {
    context: { source: { title: '已授权教材' }, section: { title: '二次函数' } },
    blueprint: null, diagnostic: null,
    lesson_plan: { id: 'lesson-spec-1', revision: 2, metadata: { title: '二次函数教案' }, lesson_flow: [] },
    teaching_deck: null,
  },
  evidence: [], available_actions: [], job_summary: [], basic_mode: false,
}

describe('Teacher Hybrid canonical session integration', () => {
  async function mountCanonical(nextSnapshot: WorkspaceSnapshot) {
    // 自动推进按「session::deck」在模块内存里去重；每个用例都必须从干净状态开始，
    // 否则上一个用例已打开的课件会让本用例跳过 open_deck。
    __resetAutoDeckFlowForTest()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/prep/session/:sessionId', name: 'canonical-session', component: PrepSurfaceRoute },
        { path: '/teacher-v3/prep', name: 'tv3-prep', component: { template: '<div data-testid="prep-editor-shell" />' } },
        { path: '/teacher-v3/slides', name: 'tv3-slides', component: { template: '<div />' } },
      ],
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    await router.push('/teacher-v3/prep/session/session-canonical')
    await router.isReady()
    const wrapper = mount({ template: '<router-view />' }, { global: { plugins: [pinia, router], stubs: STUBS } })
    const ws = usePrepWorkspaceStore(pinia)
    ws.applySnapshot(nextSnapshot)
    ws.streamState = 'live'
    await flushPromises()
    return { router, wrapper }
  }

  it('renders T23 typed cards from the canonical route and no longer exposes a lesson-plan shell', async () => {
    const { wrapper } = await mountCanonical(snapshot)

    expect(wrapper.find('[data-testid="t23-direct-chunk-locator"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="t23-learning-profile"]').exists()).toBe(true)
    // 2026-09-14 备课中心简化：教案（LessonPlanSpec）不再有可达的预览界面，
    // 因此产物 dock 既不存在，也不可能路由到已删除的 PrepEditor。
    expect(wrapper.find('[data-testid="prep-artifact-dock"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('AI 生成结束后自动把课件送进 PPT 编辑工作台（无需教师再点中间环节）', async () => {
    const { router, wrapper } = await mountCanonical({
      ...snapshot,
      artifact: {
        ...snapshot.artifact,
        lesson_plan: null,
        teaching_deck: { id: 'deck-spec-1', revision: 4, metadata: { title: '二次函数课件' }, slides: [] },
      },
    })
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('tv3-slides')
    expect(router.currentRoute.value.query).toMatchObject({ session: 'session-canonical', deck: 'deck-spec-1', mode: 'vnext' })
    wrapper.unmount()
  })

  it('双 Spec 同时存在时也只送课件——教案不再有任何界面入口', async () => {
    const { router, wrapper } = await mountCanonical({
      ...snapshot,
      artifact: {
        ...snapshot.artifact,
        lesson_plan: { id: 'lesson-spec-1', revision: 2, metadata: { title: '二次函数教案' }, lesson_flow: [] },
        teaching_deck: { id: 'deck-spec-1', revision: 4, metadata: { title: '二次函数课件' }, slides: [] },
      },
    })
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('tv3-slides')
    expect(router.currentRoute.value.query).toMatchObject({ session: 'session-canonical', deck: 'deck-spec-1', mode: 'vnext' })
    wrapper.unmount()
  })

  it('legacy focus=lesson-plan / mode=legacy query can no longer reach a lesson-plan editor', async () => {
    const responseFor = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/teacher-v3/prep/conversations/session-canonical')) {
        return new Response(JSON.stringify({ data: snapshot }), { status: 200, headers: { 'content-type': 'application/json' } })
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', responseFor)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/prep', name: 'tv3-prep', component: PrepCenterView },
        { path: '/teacher-v3/prep/session/:sessionId', name: 'canonical-session', component: { template: '<div />' } },
      ],
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    await router.push('/teacher-v3/prep?session=session-canonical&focus=lesson-plan&spec=lesson-spec-1')
    await router.isReady()
    const wrapper = mount({ template: '<router-view />' }, { global: { plugins: [pinia, router], stubs: STUBS } })
    await flushPromises()
    // 备课中心现在只渲染首页；旧编辑器壳（`prep-editor-vnext-readonly`）已随文件删除。
    expect(wrapper.find('[data-testid="tv3-prep-center"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prep-editor-vnext-readonly"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prep-home-error"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
