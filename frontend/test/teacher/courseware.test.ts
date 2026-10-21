/**
 * Phase D · 教师端「备课课件 → 班级 AI 课堂 → 实时学情」前端契约测试。
 *
 * 覆盖：
 * 1. 8100 发布载荷（publishClassroom 真实签名 / 失败错误可见）；
 * 2. 8000 课件列表 / 详情 / 学情 / 下架 客户端路径与信封；
 * 3. 发布对话框：选班 + 标题 + 随堂练习开关 → preparing 轮询 → 只有 published 才显示成功；
 * 4. 双师课堂监控：班级汇总 / 页级留存 / 每生表格 / 3s 轮询 / 失焦暂停 / 更新时间；
 * 5. 课堂页签默认「双师课堂」、实时互动主体仍在；
 * 6. 侧栏收敛为 5 项且旧深链保留。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import fs from 'node:fs'
import path from 'node:path'

enableAutoUnmount(afterEach)

/* ClassroomView 只依赖 8100 客户端：mock 掉具体目录接口，避免真实网络与 SSE。 */
vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    catalog: {
      classes: vi.fn(async () => ({ data: { items: [] } })),
      quizQuestions: vi.fn(async () => ({ data: { items: [] } })),
      tasks: vi.fn(async () => ({ data: { items: [] } })),
    },
    classroom: {
      createSession: vi.fn(),
      stream: vi.fn(),
      snapshot: vi.fn(),
      pushActivity: vi.fn(),
      lockActivity: vi.fn(),
      revealActivity: vi.fn(),
      endSession: vi.fn(),
      callRandom: vi.fn(),
    },
    decks: { list: vi.fn(async () => ({ data: { items: [] } })), addSlide: vi.fn() },
  },
}))
vi.mock('@/components/mathx/GeoFigure.vue', () => ({
  default: { name: 'GeoFigure', template: '<div class="geo-stub" />' },
}))

import { api8000 } from '@/api/api8000'
import { PREP_CENTER_API_BASE, PrepCenterApiError, publishClassroom } from '@/features/teacher-prep-center/api/prepCenterApi'
import PublishCoursewareDialog from '@/features/teacher-prep-center/publish/PublishCoursewareDialog.vue'
import CoursewareMonitorPane, {
  accuracyPct,
  clockOf,
  coursewareStatusText,
  displayPage,
  progressPct,
  updatedClock,
} from '@/pages/teacher-v3/CoursewareMonitorPane.vue'
import ClassroomView from '@/pages/teacher-v3/ClassroomView.vue'

/* ────────────────────────────── fetch 夹具 ────────────────────────────── */

interface FetchCall {
  url: string
  init: RequestInit & { body?: BodyInit | null }
}
interface RouteResult {
  status?: number
  body: unknown
}
type RouteHandler = (call: FetchCall) => RouteResult | undefined

let calls: FetchCall[] = []

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: new Headers({ 'x-request-id': 'test-request' }),
  }
}

function stubFetch(handler: RouteHandler) {
  calls = []
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init: unknown) => {
    const call: FetchCall = { url: String(url), init: (init || {}) as FetchCall['init'] }
    calls.push(call)
    const r = handler(call)
    if (!r) throw new Error(`unexpected fetch: ${call.init.method || 'GET'} ${call.url}`)
    return jsonResponse(r.status ?? 200, r.body)
  }))
}

function callsTo(fragment: string): FetchCall[] {
  return calls.filter((c) => c.url.includes(fragment))
}

function bodyOf(call: FetchCall): any {
  return call.init.body ? JSON.parse(String(call.init.body)) : null
}

const CLASS_ITEM = {
  class_id: 'c1',
  name: '高二(1)班',
  students: 42,
}
const COURSEWARE = {
  courseware_id: 'cw-1',
  title: '1.1 集合的概念',
  class_id: 'c1',
  class_name: '高二(1)班',
  teacher_name: '张老师',
  page_count: 10,
  status: 'published',
  opened: 2,
  students_total: 42,
  avg_progress_pct: 37,
  practice_accuracy: 75,
  published_at: '2026-10-20T02:00:00Z',
  updated_at: '2026-10-20T02:10:00Z',
}
const MONITOR = {
  students_total: 42,
  opened: 2,
  completed: 1,
  avg_progress_pct: 37,
  practice_accuracy: 75,
  page_dropoff: [
    { page: 0, count: 2 },
    { page: 1, count: 2 },
    { page: 2, count: 1 },
  ],
  students: [
    {
      user_id: 'u1', name: '王小明', opened: true, current_page: 3, pages_seen: 4,
      self_check_ok: 3, self_check_again: 1, practice_answered: 4, practice_correct: 3,
      last_active: '2026-10-20T02:10:00Z',
    },
    {
      user_id: 'u2', name: '李小红', opened: true, current_page: 1, pages_seen: 2,
      self_check_ok: 1, self_check_again: 0, practice_answered: 2, practice_correct: 1,
      last_active: '2026-10-20T02:09:00Z',
    },
    {
      user_id: 'u3', name: '赵小刚', opened: false, current_page: null, pages_seen: 0,
      self_check_ok: 0, self_check_again: 0, practice_answered: 0, practice_correct: 0,
      last_active: null,
    },
  ],
}

function classListBody() {
  return {
    code: 0,
    data: {
      items: [{
        id: CLASS_ITEM.class_id, name: CLASS_ITEM.name, grade: '高二', myRole: 'teacher',
        confirmed: true, inviteCode: null, createdAt: null, students: CLASS_ITEM.students,
      }],
      total: 1,
    },
  }
}

function coursewareListBody() {
  return { code: 0, data: { items: [COURSEWARE] } }
}

function monitorBody() {
  return { code: 0, data: MONITOR }
}

function fakeTimers() {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/* ─────────────────────── 1. 8100 发布载荷 ─────────────────────── */

describe('Phase D · 8100 发布载荷', () => {
  it('publishClassroom POST 到会话发布端点，body 与契约一致', async () => {
    stubFetch((call) => {
      if (call.url === `${PREP_CENTER_API_BASE}/sessions/sess-1/publish-classroom` && call.init.method === 'POST') {
        return {
          body: {
            code: 0,
            data: { courseware_id: 'cw-1', status: 'preparing', title: '1.1 集合的概念', class_id: 'c1', warnings: [] },
          },
        }
      }
      return undefined
    })

    const r = await publishClassroom('sess-1', {
      class_id: 'c1',
      deck_id: 'deck-1',
      title: '1.1 集合的概念',
      include_practice: true,
    })
    expect(r.courseware_id).toBe('cw-1')
    expect(r.status).toBe('preparing')

    const post = callsTo('/publish-classroom')[0]
    expect(post).toBeTruthy()
    expect(post.init.method).toBe('POST')
    expect(bodyOf(post)).toEqual({
      class_id: 'c1',
      deck_id: 'deck-1',
      title: '1.1 集合的概念',
      include_practice: true,
    })
  })

  it('失败信封（409）抛 PrepCenterApiError，保留服务端真实原因', async () => {
    stubFetch(() => ({
      status: 409,
      body: { code: 40901, message: '该课件不属于当前班级，禁止发布' },
    }))
    await expect(publishClassroom('sess-1', { class_id: 'c2' })).rejects.toMatchObject({
      name: 'PrepCenterApiError',
      kind: 'conflict',
      detail: '该课件不属于当前班级，禁止发布',
    })
  })
})

/* ─────────────────── 2. 8000 课件客户端（路径/信封/监控映射） ─────────────────── */

describe('Phase D · 8000 课堂课件客户端', () => {
  it('list 带 class_id/deck_id query 并解信封；monitor 保留真实每生/页级数据', async () => {
    stubFetch((call) => {
      if (call.url === '/api/teacher/classroom-coursewares?class_id=c1&deck_id=deck-1') {
        return { body: coursewareListBody() }
      }
      if (call.url === '/api/teacher/classroom-coursewares/cw-1/monitor') {
        return { body: monitorBody() }
      }
      return undefined
    })

    const list = await api8000.coursewares.list({ class_id: 'c1', deck_id: 'deck-1' })
    expect(list.items[0]).toMatchObject({ courseware_id: 'cw-1', opened: 2, students_total: 42 })

    const m = await api8000.coursewares.monitor('cw-1')
    expect(m.students.map((s) => s.name)).toEqual(['王小明', '李小红', '赵小刚'])
    expect(m.page_dropoff.map((d) => d.page)).toEqual([0, 1, 2])
    expect(m.students[0]).toMatchObject({ current_page: 3, practice_answered: 4, practice_correct: 3 })
  })

  it('get / archive 走真实路径；detail 保留 preparation/warnings', async () => {
    stubFetch((call) => {
      if (call.url === '/api/teacher/classroom-coursewares/cw-1' && (call.init.method || 'GET') === 'GET') {
        return {
          body: {
            code: 0,
            data: {
              courseware_id: 'cw-1', title: '1.1 集合的概念', class_id: 'c1', status: 'draft',
              warnings: ['资产超过 4MB，已跳过 1 张图'], preparation: { done: 2, total: 10, stage: '逐页讲稿' },
            },
          },
        }
      }
      if (call.url === '/api/teacher/classroom-coursewares/cw-1/archive') {
        return { body: { code: 0, data: { courseware_id: 'cw-1', status: 'archived' } } }
      }
      return undefined
    })

    const d = await api8000.coursewares.get('cw-1')
    expect(d.status).toBe('draft')
    expect(d.preparation?.done).toBe(2)
    expect(d.warnings[0]).toContain('4MB')

    const a = await api8000.coursewares.archive('cw-1')
    expect(a.status).toBe('archived')
    expect(callsTo('/archive')[0].init.method).toBe('POST')
  })
})

/* ─────────────────────── 3. 发布对话框 ─────────────────────── */

describe('Phase D · 发布对话框', () => {
  function mountDialog(fetchHandler: RouteHandler) {
    stubFetch(fetchHandler)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/classroom', component: { template: '<div />' } },
        { path: '/teacher-v3/prep', component: { template: '<div />' } },
        { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
      ],
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(PublishCoursewareDialog, {
      props: {
        open: true,
        sessionId: 'sess-1',
        deckId: 'deck-1',
        deckTitle: '1.1 集合的概念',
        classes: [{ class_id: 'c1', name: '高二(1)班', students: 40 }],
      },
      global: { plugins: [router, pinia], stubs: { teleport: true } },
    })
    return { wrapper, router }
  }

  it('默认标题/班级来自 props；提交载荷包含选班、标题与随堂练习开关', async () => {
    fakeTimers()
    const { wrapper } = mountDialog((call) => {
      if (call.url.endsWith('/publish-classroom')) {
        return {
          body: {
            code: 0,
            data: { courseware_id: 'cw-1', status: 'preparing', title: '1.1 集合的概念', class_id: 'c1', warnings: [] },
          },
        }
      }
      if (call.url === '/api/teacher/classroom-coursewares/cw-1') {
        return { body: { code: 0, data: { ...COURSEWARE, status: 'draft', preparation: { done: 2, total: 10 } } } }
      }
      return undefined
    })
    await flushPromises()

    expect((wrapper.find('[data-testid="publish-title"]').element as HTMLInputElement).value).toBe('1.1 集合的概念')
    expect((wrapper.find('[data-testid="publish-class"]').element as HTMLSelectElement).value).toBe('c1')

    await wrapper.find('[data-testid="publish-submit"]').trigger('click')
    await flushPromises()

    const post = callsTo('/publish-classroom')[0]
    expect(bodyOf(post)).toEqual({
      class_id: 'c1',
      deck_id: 'deck-1',
      title: '1.1 集合的概念',
      include_practice: true,
    })
    expect(wrapper.find('[data-testid="publish-preparing"]').exists()).toBe(true)
  })

  it('preparing 不冒充成功；只有详情 status=published 才出现「已发布 · 查看课堂」', async () => {
    fakeTimers()
    let detailCalls = 0
    const { wrapper, router } = mountDialog((call) => {
      if (call.url.endsWith('/publish-classroom')) {
        return {
          body: {
            code: 0,
            data: { courseware_id: 'cw-1', status: 'preparing', title: '1.1 集合的概念', class_id: 'c1', warnings: [] },
          },
        }
      }
      if (call.url === '/api/teacher/classroom-coursewares/cw-1') {
        detailCalls += 1
        if (detailCalls === 1) {
          return { body: { code: 0, data: { ...COURSEWARE, status: 'draft', preparation: { done: 2, total: 10 } } } }
        }
        return { body: { code: 0, data: { ...COURSEWARE, status: 'published', warnings: [] } } }
      }
      return undefined
    })
    await flushPromises()
    await wrapper.find('[data-testid="publish-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="publish-success"]').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.find('[data-testid="publish-success"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('2/10')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.find('[data-testid="publish-success"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('已发布')

    await wrapper.find('[data-testid="publish-goto-classroom"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('close')).toBeTruthy()
    expect(router.currentRoute.value.path).toBe('/teacher-v3/classroom')
  })

  it('发布失败显示服务端真实错误，不出现成功态', async () => {
    fakeTimers()
    const { wrapper } = mountDialog(() => ({
      status: 500,
      body: { code: 50001, message: '班级不存在或无权限' },
    }))
    await flushPromises()
    await wrapper.find('[data-testid="publish-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="publish-success"]').exists()).toBe(false)
    const err = wrapper.find('[data-testid="publish-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('班级不存在或无权限')
  })
})

/* ─────────────────────── 4. 双师课堂监控 ─────────────────────── */

describe('Phase D · 双师课堂监控', () => {
  function mountPane(handler: RouteHandler) {
    stubFetch(handler)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/prep', component: { template: '<div />' } },
        { path: '/teacher-v3/classes', component: { template: '<div />' } },
        { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
      ],
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(CoursewareMonitorPane, {
      global: { plugins: [router, pinia], stubs: { teleport: true } },
    })
    return { wrapper, router }
  }

  const handler: RouteHandler = (call) => {
    if (call.url === '/api/classes/mine') return { body: classListBody() }
    if (call.url === '/api/teacher/classroom-coursewares?class_id=c1') return { body: coursewareListBody() }
    if (call.url === '/api/teacher/classroom-coursewares/cw-1/monitor') return { body: monitorBody() }
    return undefined
  }

  it('列表卡展示真实汇总；抽屉展示 4 数字 / 页级留存 / 每生表格，并标注「更新于」', async () => {
    fakeTimers()
    const { wrapper } = mountPane(handler)
    await flushPromises()

    const card = wrapper.find('[data-testid="cwm-card-cw-1"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('2/42')
    expect(card.text()).toContain('37%')
    expect(card.text()).toContain('75%')

    await wrapper.find('[data-testid="cwm-view-cw-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="cwm-drawer"]').exists()).toBe(true)
    const summary = wrapper.find('[data-testid="cwm-summary"]').text()
    expect(summary).toContain('2/42')
    expect(summary).toContain('37%')
    expect(summary).toContain('75%')

    // 页级留存：0 基 slide_index → 第 1/2/3 页，人数来自真实接口
    const drop = wrapper.find('[data-testid="cwm-dropoff"]').text()
    expect(drop).toContain('第 1 页')
    expect(drop).toContain('第 3 页')

    const students = wrapper.find('[data-testid="cwm-students"]').text()
    expect(students).toContain('王小明')
    expect(students).toContain('第 4 页')
    expect(students).toContain('4 题 · 对 3')
    expect(students).toContain('未打开')

    expect(wrapper.find('[data-testid="cwm-updated"]').text()).toMatch(/更新于 \d{2}:\d{2}:\d{2}/)
  })

  it('每 3 秒轮询 monitor；页面失焦暂停，回到前台立即补一次', async () => {
    fakeTimers()
    const { wrapper } = mountPane(handler)
    await flushPromises()
    await wrapper.find('[data-testid="cwm-view-cw-1"]').trigger('click')
    await flushPromises()

    expect(callsTo('/monitor').length).toBe(1)
    await vi.advanceTimersByTimeAsync(3000)
    expect(callsTo('/monitor').length).toBe(2)

    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(3000)
    expect(callsTo('/monitor').length).toBe(2)

    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(callsTo('/monitor').length).toBe(3)

    await wrapper.find('.cwm-drawer__x').trigger('click')
    await vi.advanceTimersByTimeAsync(9000)
    expect(callsTo('/monitor').length).toBe(3)
  })

  it('准备中的课件每 5s 静默刷新，published 后「查看学情」才可用', async () => {
    fakeTimers()
    let listCalls = 0
    const { wrapper } = mountPane((call) => {
      if (call.url === '/api/classes/mine') return { body: classListBody() }
      if (call.url === '/api/teacher/classroom-coursewares?class_id=c1') {
        listCalls += 1
        return {
          body: {
            code: 0,
            data: { items: [{ ...COURSEWARE, status: listCalls === 1 ? 'draft' : 'published' }] },
          },
        }
      }
      return undefined
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="cwm-card-cw-1"]').text()).toContain('准备中')
    expect(wrapper.find('[data-testid="cwm-view-cw-1"]').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.find('[data-testid="cwm-card-cw-1"]').text()).toContain('已发布')
    expect(wrapper.find('[data-testid="cwm-view-cw-1"]').attributes('disabled')).toBeUndefined()
  })

  it('空态：没有发布 → 引导去备课中心；有课件但没人打开 → 诚实提示', async () => {
    fakeTimers()
    const { wrapper } = mountPane((call) => {
      if (call.url === '/api/classes/mine') return { body: classListBody() }
      if (call.url === '/api/teacher/classroom-coursewares?class_id=c1') return { body: { code: 0, data: { items: [] } } }
      return undefined
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="cwm-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="cwm-goto-prep"]').exists()).toBe(true)

    // 有课件但 0 人打开
    const { wrapper: w2 } = mountPane((call) => {
      if (call.url === '/api/classes/mine') return { body: classListBody() }
      if (call.url === '/api/teacher/classroom-coursewares?class_id=c1') {
        return { body: { code: 0, data: { items: [{ ...COURSEWARE, opened: 0 }] } } }
      }
      if (call.url === '/api/teacher/classroom-coursewares/cw-1/monitor') {
        return {
          body: {
            code: 0,
            data: { ...MONITOR, opened: 0, completed: 0, avg_progress_pct: 0, practice_accuracy: null, page_dropoff: [], students: [] },
          },
        }
      }
      return undefined
    })
    await flushPromises()
    expect(w2.find('[data-testid="cwm-card-cw-1"]').text()).toContain('还没有学生打开')
    await w2.find('[data-testid="cwm-view-cw-1"]').trigger('click')
    await flushPromises()
    expect(w2.find('[data-testid="cwm-no-students"]').exists()).toBe(true)
    expect(w2.find('[data-testid="cwm-updated"]').text()).toMatch(/\d{2}:\d{2}:\d{2}/)
  })
})

/* ─────────────────────── 5. 展示映射纯函数 ─────────────────────── */

describe('Phase D · 监控展示映射', () => {
  it('正确率按 0–100 百分数展示；进度/页码/时钟不越界', () => {
    expect(accuracyPct(75.6)).toBe('76%')
    expect(accuracyPct(82)).toBe('82%')
    expect(accuracyPct(null)).toBe('—')
    expect(progressPct(37.4)).toBe('37%')
    expect(progressPct(180)).toBe('100%')
    expect(displayPage(0)).toBe('第 1 页')
    expect(displayPage(3)).toBe('第 4 页')
    expect(displayPage(null)).toBe('—')
    expect(coursewareStatusText('draft')).toBe('准备中')
    expect(coursewareStatusText('published')).toBe('已发布')
    expect(clockOf('2026-10-20T02:10:00Z')).toMatch(/^\d{2}:\d{2}$/)
    expect(updatedClock(new Date(2026, 9, 20, 8, 3, 4))).toBe('08:03:04')
  })
})

/* ─────────────────────── 6. 课堂页签 + 侧栏 6 项 ─────────────────────── */

describe('Phase D · 课堂页签与教师 IA', () => {
  it('ClassroomView 默认「双师课堂」，实时互动主体保留且可切换（?tab=live）', async () => {
    stubFetch((call) => {
      if (call.url === '/api/classes/mine') return { body: classListBody() }
      if (call.url === '/api/teacher/classroom-coursewares?class_id=c1') return { body: { code: 0, data: { items: [] } } }
      return undefined
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/classroom', component: { template: '<div />' } },
        { path: '/teacher-v3/classes', component: { template: '<div />' } },
        { path: '/teacher-v3/prep', component: { template: '<div />' } },
        { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
      ],
    })
    await router.push('/teacher-v3/classroom')
    await router.isReady()
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(ClassroomView, { global: { plugins: [router, pinia] } })
    await flushPromises()

    expect(wrapper.find('[data-testid="tv3-tab-dual"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-testid="tv3-dual-pane"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="tv3-live-pane"]').isVisible()).toBe(false)
    expect(wrapper.find('[data-testid="cwm-empty"]').exists()).toBe(true)

    await wrapper.find('[data-testid="tv3-tab-live"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.tab).toBe('live')
    expect(wrapper.find('[data-testid="tv3-tab-live"]').classes()).toContain('is-active')
    expect(wrapper.find('[data-testid="tv3-live-pane"]').isVisible()).toBe(true)
    // 实时互动原主体仍在（无班级 → 真实引导态，而非 mock 默认班）
    expect(wrapper.find('[data-testid="tv3-classroom-no-class"]').exists()).toBe(true)
  })

  it('侧栏 6 项：恢复学情洞察一级入口（grading/classes 仍保留深链）', () => {
    const root = path.resolve(process.cwd())
    const layout = fs.readFileSync(path.join(root, 'src/layouts/TeacherV3Layout.vue'), 'utf-8')
    const daily = layout.slice(layout.indexOf('const dailyMenus'), layout.indexOf('const assessMenus'))
    const assess = layout.slice(layout.indexOf('const assessMenus'), layout.indexOf('const teacherName'))
    const nav = daily + assess
    const paths = [...nav.matchAll(/path: '([^']+)'/g)].map((m) => m[1])
    expect(paths).toEqual([
      '/teacher-v3/prep',
      '/teacher-v3/classroom',
      '/teacher-v3/bank',
      '/teacher-v3/assign',
      '/teacher-v3/insights',
      '/teacher-v3/resources',
    ])
    expect(nav).not.toContain("path: '/teacher-v3/grading'")
    expect(nav).not.toContain("path: '/teacher-v3/classes'")
    expect(nav).toContain("label: '课堂'")
    expect(nav).toContain("label: '学情洞察'")

    const subs = layout.slice(layout.indexOf('const subTitles'), layout.indexOf('async function refreshTasks'))
    expect(subs).toContain('/teacher-v3/classroom')
    expect(subs).toContain('双师课堂 · 实时互动 · 班级')

    // 旧深链不删：/grading、/classes 仍在正式路由表
    const routerSource = fs.readFileSync(path.join(root, 'src/router/index.js'), 'utf-8')
    expect(routerSource).toContain("path: 'grading'")
    expect(routerSource).toContain("path: 'classes'")
  })

  it('备课 PPT 两个入口都挂了「发布到班级课堂」，导出卡未被替换', () => {
    const root = path.resolve(process.cwd())
    const deck = fs.readFileSync(path.join(root, 'src/features/teacher-prep-center/workspace/DeckPane.vue'), 'utf-8')
    const overview = fs.readFileSync(path.join(root, 'src/features/teacher-prep-center/workspace/OverviewPane.vue'), 'utf-8')
    expect(deck).toContain('PublishCoursewareDialog')
    expect(deck).toContain('data-testid="pc-deck-publish"')
    expect(deck).toContain('发布到班级课堂')
    expect(overview).toContain('PublishCoursewareDialog')
    expect(overview).toContain('data-testid="ov-publish"')
    expect(overview).toContain('data-testid="ov-consistency"')
    expect(overview).toContain('EXPORT_CARDS')
  })
})

describe('G2 · 教师端班级角色过滤', () => {
  it('课堂监控使用 teachingClasses 过滤，不再直用 8000 原始班级目录', () => {
    const root = path.resolve(process.cwd())
    const src = fs.readFileSync(path.join(root, 'src/pages/teacher-v3/CoursewareMonitorPane.vue'), 'utf-8')
    expect(src).toContain("from '@/utils/teachingClasses'")
    expect(src).toContain('teachingClasses(r.items')
    expect(src).not.toContain("filter((c) => c.confirmed")
  })

  it('作业/学情/发布流程共用同一教学班口径', () => {
    const root = path.resolve(process.cwd())
    for (const rel of [
      'src/pages/teacher-v3/AssignView.vue',
      'src/pages/teacher-v3/InsightsView.vue',
      'src/pages/teacher-v3/GradingWorkspaceView.vue',
      'src/pages/teacher-v3/publishFlow.ts',
    ]) {
      const src = fs.readFileSync(path.join(root, rel), 'utf-8')
      expect(src.includes('teachingClasses') || src.includes('isTeachingClass'), rel).toBe(true)
    }
  })
})
