// P4 · 题库改造：知识点分类树筛选 + 扫描入库（含图片题型）+ 图片题渲染进试卷
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const quizQuestions = vi.fn()
const quizKpTree = vi.fn()
const quizScanImport = vi.fn()

vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    catalog: {
      quizQuestions: (...a: unknown[]) => quizQuestions(...a),
      quizKpTree: (...a: unknown[]) => quizKpTree(...a),
      quizScanImport: (...a: unknown[]) => quizScanImport(...a),
    },
  },
}))

import QuizView from '@/pages/teacher-v3/QuizView.vue'

const TREE = [
  {
    id: 'mod-conic', name: '圆锥曲线', children: [
      { id: 'ch-ellipse', name: '椭圆', children: [
        { id: 'kp-yd1', name: '椭圆标准方程', kp_codes: ['YD-01'] },
        { id: 'kp-yd2', name: '椭圆几何性质', kp_codes: ['YD-02'] },
      ] },
    ],
  },
]

const QUESTIONS = [
  { id: 'q-ell-1', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'choice', difficulty: 'easy', stem_latex: '椭圆焦点坐标', options: ['A', 'B'], answer: 'A', source: '校本' },
  { id: 'q-ell-2', kp_name: '椭圆几何性质', kp_code: 'YD-02', q_type: 'fill', difficulty: 'medium', stem_latex: '离心率', answer: 'e', source: '区库' },
  { id: 'q-img-1', kp_name: '立体截面（图片题）', kp_code: 'LT-05', q_type: 'image', difficulty: 'medium', stem_latex: '（图片题）判断截面形状。', stem_image: 'data:image/svg+xml;utf8,<svg/>', answer: '梯形', source: '拍照入库' },
]

function mountPage() {
  return mount(QuizView, { attachTo: document.body })
}

beforeEach(() => {
  vi.clearAllMocks()
  quizQuestions.mockResolvedValue({ data: { items: QUESTIONS, total: QUESTIONS.length } })
  quizKpTree.mockResolvedValue({ data: { tree: TREE } })
  quizScanImport.mockResolvedValue({ data: { ...QUESTIONS[2], id: 'q-new', kp_code: 'LT-05' } })
})

describe('QuizView · 分类树筛选', () => {
  it('加载后渲染分类树，选中知识点仅显示归属题目', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="tv3-kp-kp-yd1"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-kp-kp-yd2"]').exists()).toBe(true)

    // 全部：3 题
    expect(w.findAll('[data-testid^="tv3-quiz-q-"]').length).toBe(3)
    // 选中「椭圆标准方程」→ 只剩 q-ell-1
    await w.find('[data-testid="tv3-kp-kp-yd1"]').trigger('click')
    await flushPromises()
    const rows = w.findAll('[data-testid^="tv3-quiz-q-"]')
    expect(rows.length).toBe(1)
    expect(w.find('[data-testid="tv3-quiz-q-q-ell-1"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-quiz-q-q-img-1"]').exists()).toBe(false)
  })
})

describe('QuizView · 图片题型', () => {
  it('图片题显示题干原图，可选中并进入试卷预览（保留原图）', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="tv3-q-stem-image"]').exists()).toBe(true)

    await w.find('[data-testid="tv3-pick-q-img-1"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-paper-stem-image"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-paper-qhead"]').text()).toContain('图片题')
  })
})

describe('QuizView · 扫描入库', () => {
  it('弹层载入示例图 → 选入库方式 → 确认 POST → 新题即时出现在列表', async () => {
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-scan-open"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-scan-modal"]').exists()).toBe(true)

    // 未载图时确认入库禁用
    expect((w.find('[data-testid="tv3-scan-import"]').element as HTMLButtonElement).disabled).toBe(true)

    // 载入示例图（同步 dataURL）
    await w.find('[data-testid="tv3-scan-sample"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-scan-preview"]').exists()).toBe(true)
    expect((w.find('[data-testid="tv3-scan-import"]').element as HTMLButtonElement).disabled).toBe(false)

    // 选「识别成结构题」
    await w.find('[data-testid="tv3-scan-mode-recognize"]').setValue()
    await w.find('[data-testid="tv3-scan-import"]').trigger('click')
    await flushPromises()

    expect(quizScanImport).toHaveBeenCalledTimes(1)
    const arg = quizScanImport.mock.calls[0][0]
    expect(arg.as_image).toBe(false)
    expect(arg.kp_code).toBe('YD-01') // 默认归属第一个叶知识点

    // 弹层关闭 + 新题进列表
    expect(w.find('[data-testid="tv3-scan-modal"]').exists()).toBe(false)
    expect(w.find('[data-testid="tv3-quiz-q-q-new"]').exists()).toBe(true)
    w.unmount()
  })
})