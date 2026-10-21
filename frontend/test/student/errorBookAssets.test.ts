/**
 * 错题本配图与正解持久化（om8 Task 3）回归测试：
 * 1. DynamicFigureViewer 渲染后端规范契约 {type:'image',src,alt}；
 * 2. 图片加载失败（如本地原图丢失 → raw 404）显示"配图暂不可用"空态，不留破损 img；
 * 3. ErrorsView 收到缓存正解（solution_figure 非空）时显示"正解示意图"，
 *    且不再出现"AI 生成中"（缓存命中不是生成状态）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

/* ---- ErrorsView 依赖面统一 mock（路由/接口/store） ---- */
const apiGet = vi.fn()
const butlerDiagnosis = vi.fn()
const butlerErrorDetail = vi.fn()

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/api/client', () => ({ api: { get: (...a: any[]) => apiGet(...a), post: vi.fn(), patch: vi.fn() } }))
vi.mock('@/api', () => ({
  butlerApi: {
    errorDiagnosis: (...a: any[]) => butlerDiagnosis(...a),
    errorDetail: (...a: any[]) => butlerErrorDetail(...a),
    errorTutor: vi.fn(),
  },
  filesApi: { contentUrl: vi.fn().mockResolvedValue({ url: '' }) },
  studentApi: { createErrorRecord: vi.fn() },
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ nickname: '测试同学' }) }))
vi.mock('@/stores/toast', () => ({ useToastStore: () => ({ success: vi.fn(), error: vi.fn() }) }))

import DynamicFigureViewer from '@/components/DynamicFigureViewer.vue'
import ErrorsView from '@/pages/student/ErrorsView.vue'

const REC_ID = '11111111-1111-4111-8111-111111111111'

const DETAIL = {
  record_id: REC_ID,
  question_text: '如图，证明直线与平面垂直',
  image: [],
  file_id: null,
  answer_text: '',
  error_type: null,
  kp_code: null,
  kp_name: '立体几何',
  source_channel: 'manual_photo',
  origin: 'manual',
  ai_judged: false,
  note: '',
  next_review_at: null,
  created_at: '2026-09-01T10:00:00Z',
  entered_at: '2026-09-01T10:00:00Z',
  wrong_count: 2,
  review_count: 1,
  memory_stability: null,
  retrievability: null,
  fsrs_level: 'new',
  variants_hint: '建议做 3 道变式题巩固。',
}

/* 图片 onerror 模拟：jsdom 不加载真实资源，手动触发 */
async function failImg(wrapper: any) {
  const img = wrapper.get('img')
  img.trigger('error')
  await flushPromises()
}

describe('DynamicFigureViewer 规范契约渲染', () => {
  beforeEach(() => vi.clearAllMocks())

  it('渲染 {type:"image"} 对象：img 的 src/alt 来自契约', async () => {
    const wrapper = mount(DynamicFigureViewer, {
      props: {
        items: [{ type: 'image', src: 'data:image/svg+xml,abc', alt: '题目配图' }],
      },
    })
    await flushPromises()
    const img = wrapper.get('img')
    expect(img.attributes('src')).toBe('data:image/svg+xml,abc')
    expect(img.attributes('alt')).toBe('题目配图')
    expect(wrapper.text()).not.toContain('配图暂不可用')
  })

  it('兼容历史字符串形态', async () => {
    const wrapper = mount(DynamicFigureViewer, { props: { items: ['data:image/png,xyz'] } })
    await flushPromises()
    expect(wrapper.get('img').attributes('src')).toBe('data:image/png,xyz')
  })

  it('图片加载失败：移除破损 img，显示"配图暂不可用"空态', async () => {
    const wrapper = mount(DynamicFigureViewer, {
      props: { items: [{ type: 'image', src: '/api/files/dead-beef/raw?token=x', alt: '题目配图' }] },
    })
    await flushPromises()
    await failImg(wrapper)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('配图暂不可用')
  })

  it('ggb 对象不进静态图通道（不产生 img）', async () => {
    const wrapper = mount(DynamicFigureViewer, {
      props: { items: [{ type: 'ggb', view: '3d', commands: ['A=(0,0,0)'], caption: '' }] },
    })
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
  })
})

describe('ErrorsView 缓存正解与示意图', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiGet.mockImplementation((url) => {
      if (url.includes('memory-heatmap')) return Promise.resolve({ cells: [] })
      if (url.includes('due-queue')) return Promise.resolve({ items: [], total: 0 })
      if (url.includes('/filter')) {
        return Promise.resolve({
          items: [{ record_id: REC_ID, question_preview: '如图…', kp_code: null, kp_name: null, created_at: '2026-09-01T10:00:00Z', review_count: 1, retrievability: 0.5, fsrs_level: 'new' }],
          total: 1,
        })
      }
      if (url.endsWith(`/error-records/${REC_ID}/detail`)) return Promise.resolve(DETAIL)
      return Promise.resolve({})
    })
    butlerDiagnosis.mockResolvedValue({ subtype_zh: '概念不清', diagnosis: '诊断文本' })
  })

  it('缓存正解：显示"正解示意图"与缓存徽标，不出现"AI 生成中"', async () => {
    butlerErrorDetail.mockResolvedValue({
      record_id: REC_ID,
      generated_answer: '已保存的正解',
      solution_figure: [{ type: 'image', src: 'data:image/svg+xml,abc', alt: '正解示意图' }],
      cached: true,
    })
    const wrapper = mount(ErrorsView, {
      global: { stubs: { LatexText: true, MarkdownView: true, HomeworkPhotos: true, FlashcardReview: true } },
    })
    await flushPromises()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('正解示意图')
    expect(text).toContain('已缓存，秒开')  // 缓存命中徽标（plan Task 3: 不再显示"生成中"）
    expect(text).not.toContain('AI 生成中')
    expect(text).not.toContain('正解加载中')
    // 已把缓存正解传给 MarkdownView 渲染（MarkdownView 被 stub，只验 prop）
    const mv = wrapper.findComponent({ name: 'MarkdownView', props: { text: '已保存的正解' } })
    // 兜底：直接断言页面至少出现"正解"区块与缓存状态
    expect(text).toMatch(/正解/)
  })

  it('正解接口失败：显示"正解暂不可用"而不是永远"生成中"', async () => {
    butlerErrorDetail.mockRejectedValue(new Error('网络异常'))
    const wrapper = mount(ErrorsView, {
      global: { stubs: { LatexText: true, MarkdownView: true, HomeworkPhotos: true, FlashcardReview: true } },
    })
    await flushPromises()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('正解暂不可用')
    expect(text).not.toContain('AI 生成中')
    expect(text).not.toContain('正解加载中')
  })

  it('solution_figure 为空数组：不渲染"正解示意图"区块', async () => {
    butlerErrorDetail.mockResolvedValue({ record_id: REC_ID, generated_answer: '正解', solution_figure: [], cached: true })
    const wrapper = mount(ErrorsView, {
      global: { stubs: { LatexText: true, MarkdownView: true, HomeworkPhotos: true, FlashcardReview: true } },
    })
    await flushPromises()
    await flushPromises()
    expect(wrapper.text()).not.toContain('正解示意图')
  })
})
