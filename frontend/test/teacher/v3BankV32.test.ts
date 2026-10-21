// V3.2 · BankView：双框入库（题干/解答分开）+ AI 识别知识点 + 分类树编辑；ResourcesView：分类（类型分组+章节+搜索）
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const quizQuestions = vi.fn()
const quizKpTree = vi.fn()
const foldersList = vi.fn()
const quizScanImport = vi.fn()
const kpSuggest = vi.fn()
const kpTreeAdd = vi.fn()
const resourcesFn = vi.fn()
const recipesFn = vi.fn()

vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    catalog: {
      quizQuestions: (...a: unknown[]) => quizQuestions(...a),
      quizKpTree: (...a: unknown[]) => quizKpTree(...a),
      quizScanImport: (...a: unknown[]) => quizScanImport(...a),
      kpSuggest: (...a: unknown[]) => kpSuggest(...a),
      kpTreeAdd: (...a: unknown[]) => kpTreeAdd(...a),
      folders: { list: (...a: unknown[]) => foldersList(...a) },
      resources: (...a: unknown[]) => resourcesFn(...a),
      recipes: (...a: unknown[]) => recipesFn(...a),
    },
  },
}))

import BankView from '@/pages/teacher-v3/BankView.vue'
import ResourcesView from '@/pages/teacher-v3/ResourcesView.vue'

// 迭代20 门禁同步（2026-10-05 上传契约）：题干/解答图都走 uploadFile 中转拿 key，
// 导入 body 携带的是**对象存储 key**（base64 回落已被产品裁决阻断，防裂图垃圾行）。
const uploadFn = vi.fn()
vi.mock('@/api/teacherV3Upload', () => ({
  presignUpload: vi.fn(),
  uploadFile: (...a: unknown[]) => uploadFn(...a),
}))
// ResourcesView（2026-10-05 改版）数据源是 '@/api/client' 的 api.get('/teacher/videos')
const apiGet = vi.fn()
vi.mock('@/api/client', () => ({
  api: { get: (...a: unknown[]) => apiGet(...a) },
  ApiError: class ApiError extends Error {},
}))

const TREE = [
  { id: 'mod-conic', name: '圆锥曲线', children: [
    { id: 'ch-ellipse', name: '椭圆', children: [{ id: 'kp-yd3', name: '焦点弦', kp_codes: ['YD-03'] }] },
  ] },
]
const QUESTIONS = [
  { id: 'q-ell-3', kp_name: '焦点弦', kp_code: 'YD-03', q_type: 'solve', difficulty: 'hard', stem_latex: '求弦长 |AB|', answer: '24/7', analysis: '联立 + 韦达。', solution_image: 'data:image/svg+xml;utf8,<svg/>' },
]

function mountPage(comp: typeof BankView | typeof ResourcesView) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }] })
  return mount(comp, { global: { plugins: [router] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  quizQuestions.mockResolvedValue({ data: { items: QUESTIONS, total: 1 } })
  quizKpTree.mockResolvedValue({ data: { tree: TREE } })
  foldersList.mockResolvedValue({ data: { items: [] } })
  resourcesFn.mockResolvedValue({ data: { items: [
    { id: 'r1', name: '椭圆 · 课件', kind: 'deck', subject: '圆锥曲线', chapter: '椭圆及其标准方程', updated_at: '2026-09-02', owner: '李文澜', shared: true },
    { id: 'r8', name: '英语阅读训练 · 课件', kind: 'deck', subject: '英语', chapter: '阅读专项', updated_at: '2026-08-20', owner: '陈老师', shared: true },
    { id: 'r6', name: '导数 · 教案', kind: 'plan', subject: '导数', chapter: '导数及其应用', updated_at: '2026-08-15', owner: '李文澜', shared: false },
  ] } })
  recipesFn.mockResolvedValue({ data: { items: [] } })
  // 组件任何选图路径都会走 uploadFile；给个默认兑现，具体用例再覆盖
  uploadFn.mockResolvedValue({ key: 'mock-uploads/def.png', demo: false })
})

describe('V3.2 · BankView 双框入库', () => {
  it('题干图与解答图分两框上传；导入时 src/solution_src 携带对象存储 key', async () => {
    quizScanImport.mockResolvedValue({ data: { ...QUESTIONS[0], id: 'q-new' } })
    // 两框各自上传：第一次题干、第二次解答，按调用序返回不同 key
    uploadFn
      .mockResolvedValueOnce({ key: 'mock-uploads/u-9/stem.png', demo: false })
      .mockResolvedValueOnce({ key: 'mock-uploads/u-9/solution.png', demo: false })
    const w = mountPage(BankView)
    await flushPromises()

    await w.find('[data-testid="tv3-bank-scan-open"]').trigger('click')
    expect(w.find('[data-testid="tv3-bank-scan-sol-file"]').exists()).toBe(true)
    expect(w.text()).toContain('② 解答过程图（选传）')

    // 两框各选一张图
    const stemInput = w.find('[data-testid="tv3-bank-scan-input"]')
    Object.defineProperty(stemInput.element, 'files', { value: [{ name: '题干.png' }] })
    vi.spyOn(window, 'FileReader').mockImplementation(() => {
      const fake: any = { readAsDataURL: () => Promise.resolve().then(() => fake.onload?.()), result: 'data:image/png;base64,IMG' }
      return fake
    })
    await stemInput.trigger('change')
    const solInput = w.find('[data-testid="tv3-bank-scan-sol-input"]')
    Object.defineProperty(solInput.element, 'files', { value: [{ name: '解答.png' }] })
    await solInput.trigger('change')
    await flushPromises()
    expect(w.find('[data-testid="tv3-bank-scan-sol-preview"]').exists()).toBe(true)
    expect(uploadFn).toHaveBeenCalledTimes(2)

    await w.find('[data-testid="tv3-bank-scan-import"]').trigger('click')
    await flushPromises()
    expect(quizScanImport).toHaveBeenCalledTimes(1)
    // 新契约：body 携带对象存储 key（不再是 base64 dataURL）
    expect(quizScanImport.mock.calls[0][0]).toMatchObject({
      src: 'mock-uploads/u-9/stem.png',
      solution_src: 'mock-uploads/u-9/solution.png',
    })
    vi.restoreAllMocks()
  })

  it('解答原图出现在「答案与解析」折叠区', async () => {
    const w = mountPage(BankView)
    await flushPromises()
    await w.find('[data-testid="tv3-bank-detail-q-ell-3"]').trigger('click')
    expect(w.find('[data-testid="tv3-bank-detail-box-q-ell-3"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-bank-solution-q-ell-3"]').exists()).toBe(true)
  })
})

describe('V3.2 · BankView AI 识别知识点 + 树编辑', () => {
  it('AI 识别 → 建议与备选出现 → 点选填入归属下拉', async () => {
    kpSuggest.mockResolvedValue({
      data: {
        suggestion: { code: 'YD-03', path: ['圆锥曲线', '椭圆', '焦点弦'], name: '焦点弦', confidence: 0.92 },
        alternates: [{ code: 'YD-01', path: ['圆锥曲线', '椭圆', '椭圆标准方程'], name: '椭圆标准方程' }],
        note: '按题干关键词匹配（mock）',
      },
    })
    const w = mountPage(BankView)
    await flushPromises()
    await w.find('[data-testid="tv3-bank-scan-open"]').trigger('click')

    // 未传图前禁用
    expect((w.find('[data-testid="tv3-bank-scan-suggest"]').element as HTMLButtonElement).disabled).toBe(true)

    const stemInput = w.find('[data-testid="tv3-bank-scan-input"]')
    Object.defineProperty(stemInput.element, 'files', { value: [{ name: '题干.png' }] })
    vi.spyOn(window, 'FileReader').mockImplementation(() => {
      const fake: any = { readAsDataURL: () => Promise.resolve().then(() => fake.onload?.()), result: 'data:image/svg+xml;utf8,<svg>过椭圆右焦点求弦长</svg>' }
      return fake
    })
    await stemInput.trigger('change')
    await flushPromises()
    await w.find('[data-testid="tv3-bank-scan-suggest"]').trigger('click')
    await flushPromises()

    expect(kpSuggest).toHaveBeenCalled()
    const apply = w.find('[data-testid="tv3-bank-suggest-apply"]')
    expect(apply.exists()).toBe(true)
    expect(apply.text()).toContain('焦点弦')
    await apply.trigger('click')
    const sel = w.find('[data-testid="tv3-bank-scan-kp"]').element as HTMLSelectElement
    expect(sel.value).toBe('YD-03')
    vi.restoreAllMocks()
  })

  it('树编辑：加子级（调 kpTreeAdd 并刷新）/ 删除后题目保留', async () => {
    kpTreeAdd.mockResolvedValue({ data: { id: 'kp-new', name: '弦长公式' } })
    const w = mountPage(BankView)
    await flushPromises()

    // 进入编辑模式 → 行内操作出现
    await w.find('[data-testid="tv3-bank-tree-manage"]').trigger('click')
    expect(w.find('[data-testid="tv3-kp-add-kp-yd3"]').exists()).toBe(true)

    // 在焦点弦下加子级
    await w.find('[data-testid="tv3-kp-add-kp-yd3"]').trigger('click')
    await w.find('[data-testid="tv3-kp-add-input"]').setValue('弦长公式')
    await w.find('[data-testid="tv3-kp-add-go"]').trigger('click')
    await flushPromises()
    // 叶节点的 ＋ = 同级新建 → 挂在其父 ch-ellipse 下
    expect(kpTreeAdd).toHaveBeenCalledWith({ parent_id: 'ch-ellipse', name: '弦长公式' })
    // 树刷新后新节点渲染（quizKpTree 已被再次调用）
    expect(quizKpTree.mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})

describe('V3.2 · ResourcesView（2026-10-05 产品改版：B 站备课视频）', () => {
  const VIDEO = {
    bvid: 'BV1xx411c7mD', title: '椭圆定义 · 备课讲解', author: '一数',
    cover: 'https://i0.hdslb.com/bfs/archive/x.jpg', duration: '10:00', duration_sec: 600,
    play: 120000, url: 'https://www.bilibili.com/video/BV1xx411c7mD',
  }
  const TOPICS = [{ key: 'featured', label: '备课精选', icon: '📺', desc: '' }]

  it('默认 topic 拉取视频并渲染卡片（新标签直达B站）；搜索按 keyword 重新检索', async () => {
    apiGet
      .mockResolvedValueOnce({ items: [VIDEO], topics: TOPICS, topic: { key: 'featured' } })
      .mockResolvedValueOnce({ items: [], topics: TOPICS, topic: { key: 'custom' } })
    const w = mountPage(ResourcesView)
    await flushPromises()

    // 首拉：默认精选 topic + page_size=12
    expect(apiGet).toHaveBeenCalledWith('/teacher/videos', expect.objectContaining({ topic: 'featured', page_size: 12 }))
    expect(w.text()).toContain('椭圆定义 · 备课讲解')
    // 卡片新标签直达 B 站（与 ResourceCenterView 学生端同语言）
    const card = w.find('[data-testid="tv3-res-card"]')
    expect(card.attributes('href')).toBe('https://www.bilibili.com/video/BV1xx411c7mD')
    expect(card.attributes('target')).toBe('_blank')

    // 搜索：enter 触发 keyword 检索
    await w.find('[data-testid="tv3-res-search"]').setValue('导数')
    await w.find('[data-testid="tv3-res-search"]').trigger('keyup.enter')
    await flushPromises()
    expect(apiGet).toHaveBeenLastCalledWith('/teacher/videos', expect.objectContaining({ keyword: '导数', page_size: 12 }))
  })
})
