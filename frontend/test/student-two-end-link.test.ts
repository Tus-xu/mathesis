/**
 * 两端贯通 B4 学生端链路测试（deliverables/two-end-link/PLAN.md §8.3）。
 *
 * 锁定四件事，全部离线 jsdom（mock @/api/client 与 vue-router，零真实后端）：
 * 1. TasksView TYPE_MAP 扩展：pre_class（🎓 学前作业）/ homework（📝 课后习题），
 *    且 teacher_name / kp_labels 消费后端 A2 纯加法字段（student_router.py:2354-2355）；
 * 2. pending（未被教师确认）学生空态引导：等待老师确认 + 跳班级 tab（D3 列表口径的配套 UI）；
 * 3. AssignmentView：类型徽标 + 提交后即时自动判分展示（choice 判对 / pending_review 待批阅）；
 * 4. ClassroomHomeView「最近课堂」切 tab 即刷新（修复原 onMounted 仅 ?tab=dual 直挂载才拉取）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

/* ---------- 通用 mock：@/api/client ---------- */
const getMock = vi.fn()
const postMock = vi.fn()

vi.mock('@/api/client', () => ({
  api: {
    get: (...args: unknown[]) => getMock(...args),
    post: (...args: unknown[]) => postMock(...args),
  },
  ApiError: class ApiError extends Error {
    code: number
    constructor(message: string, code = 1) {
      super(message)
      this.code = code
    }
  },
}))

/* ---------- 通用 mock：vue-router（push 返回 Promise：ReportView.go() 链了 .catch） ---------- */
const pushMock = vi.fn(() => Promise.resolve())
const replaceMock = vi.fn(() => Promise.resolve())

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  useRoute: () => routeState,
}))

// 各用例改写此对象以模拟路由入参
const routeState: { query: Record<string, string>; params: Record<string, string> } = {
  query: {},
  params: {},
}

/* ---------- AssignmentView / ReportView 依赖的隔离 mock ---------- */
vi.mock('@/api', () => ({
  studentApi: {
    assignmentDetail: vi.fn(),
    assignmentResult: vi.fn(),
    practiceSubmit: vi.fn(),
    masterySummary: vi.fn(),
    masteryTrend: vi.fn(),
    todayActions: vi.fn(),
  },
  filesApi: { contentUrl: vi.fn() },
  butlerApi: { weeklyReport: vi.fn() },
}))
vi.mock('@/components/student/HomeworkPhotos.vue', () => ({
  default: { template: '<div class="hp-stub" />', props: ['modelValue', 'max'] },
}))
vi.mock('@/components/DynamicFigureViewer.vue', () => ({
  default: { template: '<div class="dfv-stub" />', props: ['items', 'label', 'height'] },
}))

import TasksView from '@/pages/student/TasksView.vue'
import ClassroomHomeView from '@/pages/student/ClassroomHomeView.vue'
import AssignmentView from '@/pages/student/AssignmentView.vue'
import ReportView from '@/pages/student/ReportView.vue'
import { butlerApi, studentApi } from '@/api'
import { ApiError } from '@/api/client'

function mountView(component: Parameters<typeof mount>[0]) {
  return mount(component, { global: { plugins: [createPinia()] } })
}

beforeEach(() => {
  // mockClear 只清调用记录、保留 vi.fn(impl) 的实现（push 需返回 Promise 供 .catch 链）
  getMock.mockReset()
  postMock.mockClear()
  pushMock.mockClear()
  replaceMock.mockClear()
  routeState.query = {}
  routeState.params = {}
})

/* ============ 1. TasksView：TYPE_MAP 扩展 + teacher_name / kp_labels ============ */

describe('TasksView · 任务列表', () => {
  const listResponse = (items: unknown[]) =>
    getMock.mockImplementation((url: string) => {
      if (url === '/student/assignments') return Promise.resolve({ total: items.length, items })
      if (url === '/classes/mine') return Promise.resolve({ items: [{ id: 'c1', confirmed: true, myRole: 'student' }] })
      return Promise.resolve({ items: [] })
    })

  it('TYPE_MAP 扩展：学前作业/课后习题 徽标与图标，teacher_name/kp_labels 渲染真数据', async () => {
    listResponse([
      {
        assignment_id: 'a1', title: '开学摸底卷', type: 'pre_class',
        teacher_name: '王老师', kp_labels: ['集合', '常用逻辑用语'],
        deadline: '2026-10-20T23:59:00Z', overdue: false, progress: { done: 0, total: 5 },
      },
      {
        assignment_id: 'a2', title: '第一章课后习题', type: 'homework',
        teacher_name: '王老师', kp_labels: [],
        deadline: null, overdue: false, progress: { done: 2, total: 8 },
      },
      {
        assignment_id: 'a3', title: '自主练习', type: 'quiz',
        teacher_name: '', kp_labels: ['函数'],
        deadline: null, overdue: false, progress: { done: 0, total: 4 },
      },
    ])
    const w = mountView(TasksView)
    await flushPromises()

    const text = w.text()
    expect(text).toContain('学前作业')
    expect(text).toContain('课后习题')
    expect(text).toContain('练习')
    expect(text).toContain('🎓')
    expect(text).toContain('📝')
    // 硬编码「老师下发」已删除：有 teacher_name 显示真名，无则不显示假名
    expect(text).toContain('王老师')
    expect(w.findAll('.task-item')[2].text()).not.toContain('老师下发')
    expect(w.findAll('.task-item')[2].text()).not.toContain('👨🏫')
    expect(text).toContain('📍 关联 集合、常用逻辑用语')
    // kp_labels 为空的作业不渲染假关联
    expect(w.findAll('.task-item')[1].text()).not.toContain('📍')
  })

  it('pending 学生空态：等待老师确认引导 + 可跳班级 tab（D3 配套）', async () => {
    getMock.mockImplementation((url: string) => {
      if (url === '/student/assignments') return Promise.resolve({ total: 0, items: [] })
      if (url === '/classes/mine') {
        return Promise.resolve({ items: [{ id: 'c9', confirmed: false, myRole: 'student' }] })
      }
      return Promise.resolve({ items: [] })
    })
    const w = mountView(TasksView)
    await flushPromises()

    expect(w.text()).toContain('等待老师确认加入班级后可见任务')
    const guide = w.find('.guide-btn')
    expect(guide.exists()).toBe(true)
    expect(guide.text()).toBe('去我的班级查看')
    await guide.trigger('click')
    expect(pushMock).toHaveBeenCalledTimes(1)
    expect(pushMock.mock.calls[0][0]).toMatchObject({ query: { tab: 'class' } })
  })

  it('未加入班级空态：引导去加入班级；已确认无任务维持诚实文案', async () => {
    getMock.mockImplementation((url: string) => {
      if (url === '/student/assignments') return Promise.resolve({ total: 0, items: [] })
      if (url === '/classes/mine') return Promise.resolve({ items: [] })
      return Promise.resolve({ items: [] })
    })
    const w = mountView(TasksView)
    await flushPromises()
    expect(w.text()).toContain('还没有加入班级')

    // 已确认成员、无任务：不给引导按钮，只给诚实文案
    getMock.mockImplementation((url: string) => {
      if (url === '/student/assignments') return Promise.resolve({ total: 0, items: [] })
      if (url === '/classes/mine') return Promise.resolve({ items: [{ id: 'c1', confirmed: true, myRole: 'student' }] })
      return Promise.resolve({ items: [] })
    })
    const w2 = mountView(TasksView)
    await flushPromises()
    expect(w2.text()).toContain('暂无任务，老师还没有下发作业')
    expect(w2.find('.guide-btn').exists()).toBe(false)
  })

  it('加载失败显示错误态（真实 ApiError 消息）', async () => {
    getMock.mockImplementation((url: string) => {
      if (url === '/student/assignments') {
        return Promise.reject(new ApiError('服务开小差了', 500))
      }
      return Promise.resolve({ items: [] })
    })
    const w = mountView(TasksView)
    await flushPromises()
    expect(w.text()).toContain('服务开小差了')
  })
})

/* ============ 2. AssignmentView：类型徽标 + 提交即时判分 ============ */

describe('AssignmentView · 作答与即时判分', () => {
  const detail = {
    assignment_id: 'asg-1',
    title: '第一章课后习题',
    type: 'homework',
    deadline: null,
    status: 'published',
    quiz_id: 'q-1',
    items: [
      { item_no: 1, q_type: 'choice', question_text: '1+1=?', options: { A: '1', B: '2', C: '3', D: '4' }, image: [] },
      { item_no: 2, q_type: 'solution', question_text: '证明……', image: [] },
    ],
  }

  it('类型徽标展示「课后习题」；选择区域提示自动判分', async () => {
    vi.mocked(studentApi.assignmentDetail).mockResolvedValue(detail)
    vi.mocked(studentApi.assignmentResult).mockRejectedValue({ code: 40400 })
    const w = mountView(AssignmentView)
    await flushPromises()

    expect(w.text()).toContain('课后习题')
    expect(w.text()).toContain('选择题提交后系统自动判分')
  })

  it('提交后：choice 即时展示判对得分；solution 展示等待老师批阅', async () => {
    vi.mocked(studentApi.assignmentDetail).mockResolvedValue(detail)
    // 首次 result 调用 = 加载时尚未提交（40400）→ 保持作答态；提交后第二次调用返回待批阅结果
    vi.mocked(studentApi.assignmentResult)
      .mockRejectedValueOnce({ code: 40400, message: '尚未提交' })
      .mockResolvedValueOnce({
      assignment_id: 'asg-1',
      submission_id: 'sub-1',
      status: 'pending_review',
      total_score: null,
      items: [
        { submission_item_id: 'si-1', item_no: 1, answer_text: 'B', attachments: [], score: null, teacher_feedback: null },
        { submission_item_id: 'si-2', item_no: 2, answer_text: '因为……所以……', attachments: [], score: null, teacher_feedback: null },
      ],
    })
    vi.mocked(studentApi.practiceSubmit).mockResolvedValue({
      submission_id: 'sub-1',
      results: [
        { item_no: 1, verdict: 'correct', score: 10 },
        { item_no: 2, verdict: 'pending_review', degraded: 'teacher_review' },
      ],
      mastery_updated: true,
    })

    const w = mountView(AssignmentView)
    await flushPromises()

    // 选择题选 B；解答题写步骤
    await w.findAll('input[type="radio"]')[1].setValue(true)
    await w.find('textarea').setValue('因为……所以……')

    await w.find('button.primary').trigger('click')
    await flushPromises()

    expect(studentApi.practiceSubmit).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(studentApi.practiceSubmit).mock.calls[0][0] as Record<string, unknown>
    expect(payload).toMatchObject({ assignment_id: 'asg-1' })

    const text = w.text()
    expect(text).toContain('✓ 判对 · +10 分')
    expect(text).toContain('等待老师批阅')
    // 提交后 result 区：pending_review 状态 + 终分待公布的诚实文案
    expect(text).toContain('老师确认批阅后')
    expect(text).toContain('待老师确认')
  })
})

/* ============ 3. ClassroomHomeView：最近课堂切 tab 即刷新 ============ */

describe('ClassroomHomeView · 最近课堂', () => {
  // getMock 是共享 mock（子组件 TasksView 也会打点），sessions 拉取单独计数
  let sessionsCalls: number
  function mockSessions(items: unknown[]) {
    sessionsCalls = 0
    getMock.mockImplementation((url: string) => {
      if (url === '/classroom/sessions') {
        sessionsCalls += 1
        return Promise.resolve({ sessions: items })
      }
      if (url === '/student/assignments') return Promise.resolve({ total: 0, items: [] })
      return Promise.resolve({ items: [] })
    })
  }

  it('默认 tasks tab 不拉取最近课堂；切到双师 tab 立即拉取并渲染', async () => {
    mockSessions([{ id: 's1', title: '数列的概念', status: 'ready', slide_count: 12 }])
    const w = mountView(ClassroomHomeView)
    await flushPromises()
    expect(sessionsCalls).toBe(0)

    await w.findAll('.cls-tabs button')[2].trigger('click')
    await flushPromises()
    expect(sessionsCalls).toBe(1)
    expect(w.text()).toContain('最近课堂')
    expect(w.text()).toContain('数列的概念')
  })

  it('?tab=dual 直挂载时立即拉取（原 onMounted 行为保持）', async () => {
    routeState.query = { tab: 'dual' }
    mockSessions([])
    mountView(ClassroomHomeView)
    await flushPromises()
    expect(sessionsCalls).toBe(1)
  })
})

/* ============ 4. ReportView：掌握度总览 + 今日行动（学生学情展示） ============ */

describe('ReportView · 学情展示两区块', () => {
  beforeEach(() => {
    // report 旧端点（亮点/薄弱/趋势/错因/诚实）统一空数据，聚焦两个新区块
    getMock.mockImplementation((url: string) => {
      if (url === '/student/report/mastery-trend-forecast') return Promise.resolve({ history: [], forecast: [] })
      return Promise.resolve({ items: [] })
    })
    vi.mocked(butlerApi.weeklyReport).mockResolvedValue({ narrative: '', data: null })
  })

  it('今日行动：行动卡渲染、到期数徽标、点击直达错题本', async () => {
    vi.mocked(studentApi.todayActions).mockResolvedValue({
      actions: [
        { type: 'review', title: '复习 3 道到期错题', reason: '间隔复习到点了', duration_min: 10 },
        { type: 'weak', title: '专练「数列」', reason: '掌握度 45%', duration_min: 15, kp_code: 'G1-2-1' },
      ],
      notice: null,
      due_today: 3,
    })
    const w = mountView(ReportView)
    await flushPromises()

    expect(w.text()).toContain('今日行动')
    expect(w.text()).toContain('今日到期错题 3 道')
    expect(w.text()).toContain('复习 3 道到期错题')
    expect(w.text()).toContain('专练「数列」')
    // review → 错题本；weak → 练题中心带 kp 定向
    await w.findAll('.ta-card')[0].trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/errors')
    await w.findAll('.ta-card')[1].trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/practice?kp=G1-2-1')
  })

  it('掌握度总览：summary/trend 真数据渲染（均值/薄弱/曲线）；无数据诚实空态', async () => {
    vi.mocked(studentApi.masterySummary).mockResolvedValue({
      radar: [
        { kp_code: 'G1-1-1', kp_name: '集合', mastery: 0.8 },
        { kp_code: 'G1-2-1', kp_name: '数列', mastery: 0.45 },
      ],
      top_weak: [{ kp_code: 'G1-2-1', kp_name: '数列', mastery: 0.45 }],
      hint_dependency: 0.25,
      delta_vs_7d: 0.05,
    })
    vi.mocked(studentApi.masteryTrend).mockResolvedValue({
      points: [
        { date: '2026-09-20', avg_mastery: 0.5 },
        { date: '2026-10-12', avg_mastery: 0.7 },
      ],
    })
    vi.mocked(studentApi.todayActions).mockResolvedValue({ actions: [], notice: null, due_today: 0 })

    const w = mountView(ReportView)
    await flushPromises()

    expect(w.text()).toContain('掌握度总览')
    expect(w.text()).toContain('平均掌握度')
    expect(w.text()).toContain('63%') // (0.8+0.45)/2 = 0.625 → 62.5% → Math.round → 63
    expect(w.text()).toContain('数列')
    expect(w.text()).toContain('提示依赖度')
    expect(w.find('.chart-svg').exists()).toBe(true) // 30 天曲线复用页内 SVG 口径
    // 今日行动空数据：诚实空态而非假行动
    expect(w.text()).toContain('保持节奏')
  })

  it('无掌握度记录：诚实空态 + 引导去练题', async () => {
    vi.mocked(studentApi.masterySummary).mockResolvedValue({
      radar: [], top_weak: [], hint_dependency: 0, delta_vs_7d: null,
    })
    vi.mocked(studentApi.masteryTrend).mockResolvedValue({ points: [] })
    vi.mocked(studentApi.todayActions).mockResolvedValue({ actions: [], notice: null, due_today: 0 })

    const w = mountView(ReportView)
    await flushPromises()

    expect(w.text()).toContain('还没有掌握度数据')
    expect(w.text()).toContain('去练题中心')
  })
})
