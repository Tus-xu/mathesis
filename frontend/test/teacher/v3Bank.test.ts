// V3.1 · BankView 题库页：分类树筛选 + 专题夹（引用式）+ 拍照原样入库 + 自编结构化录入
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const quizQuestions = vi.fn()
const quizKpTree = vi.fn()
const foldersList = vi.fn()
const foldersCreate = vi.fn()
const foldersRemove = vi.fn()
const foldersAdd = vi.fn()
const foldersRemoveQ = vi.fn()
const quizScanImport = vi.fn()
const quizCreate = vi.fn()

vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    catalog: {
      quizQuestions: (...a: unknown[]) => quizQuestions(...a),
      quizKpTree: (...a: unknown[]) => quizKpTree(...a),
      quizScanImport: (...a: unknown[]) => quizScanImport(...a),
      quizCreate: (...a: unknown[]) => quizCreate(...a),
      folders: {
        list: (...a: unknown[]) => foldersList(...a),
        create: (...a: unknown[]) => foldersCreate(...a),
        remove: (...a: unknown[]) => foldersRemove(...a),
        addQuestions: (...a: unknown[]) => foldersAdd(...a),
        removeQuestion: (...a: unknown[]) => foldersRemoveQ(...a),
      },
    },
  },
}))

import BankView from '@/pages/teacher-v3/BankView.vue'

// 迭代20 门禁同步（2026-10-05 上传契约）：入库必须有对象存储 key——选图/示例图
// 都走 uploadFile（浏览器 → 8100 → MinIO 中转）。mock 供组件拿 key，断言 src=key。
const uploadFn = vi.fn()
vi.mock('@/api/teacherV3Upload', () => ({
  presignUpload: vi.fn(),
  uploadFile: (...a: unknown[]) => uploadFn(...a),
}))

const TREE = [
  { id: 'mod-conic', name: '圆锥曲线', children: [
    { id: 'ch-ellipse', name: '椭圆', children: [
      { id: 'kp-yd1', name: '椭圆标准方程', kp_codes: ['YD-01'] },
      { id: 'kp-yd2', name: '椭圆几何性质', kp_codes: ['YD-02'] },
    ] },
  ] },
  { id: 'mod-deriv', name: '导数', children: [
    { id: 'ch-deriv', name: '导数应用', children: [{ id: 'kp-dr2', name: '导数与单调性', kp_codes: ['DR-02'] }] },
  ] },
]

const QUESTIONS = [
  { id: 'q-ell-1', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'choice', difficulty: 'easy', stem_latex: '椭圆的焦点坐标', answer: 'A', source: '校本', folder_refs: ['fld-1'], usage_count: 14 },
  { id: 'q-ell-2', kp_name: '椭圆几何性质', kp_code: 'YD-02', q_type: 'fill', difficulty: 'medium', stem_latex: '离心率 $e$', answer: 'e=\\frac{\\sqrt{5}}{3}', source: '区库', analysis: '先化标准方程。' },
  { id: 'q-drv-1', kp_name: '导数与单调性', kp_code: 'DR-02', q_type: 'image', difficulty: 'hard', stem_latex: '（图片题）根据原图判断。', stem_image: 'data:image/svg+xml;utf8,<svg/>' as string, answer: '略', source: '拍照入库' },
]

function mountPage() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }] })
  return mount(BankView, { global: { plugins: [router] } })
}

beforeEach(() => {
  vi.clearAllMocks()
  quizQuestions.mockResolvedValue({ data: { items: QUESTIONS, total: 3 } })
  quizKpTree.mockResolvedValue({ data: { tree: TREE } })
  foldersList.mockResolvedValue({ data: { items: [
    { id: 'fld-1', name: '圆锥曲线压轴', desc: '', count: 1, updated_at: '2026-09-02' },
    { id: 'fld-2', name: '易错题集', desc: '', count: 0, updated_at: '2026-08-30' },
  ] } })
})

describe('BankView · 组织：分类树 + 专题夹', () => {
  it('渲染三级分类树（模块▸章▸知识点）与题量统计', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="tv3-bank-tree"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-bank-kp-mod-conic"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-bank-kp-ch-ellipse"]').exists()).toBe(true)
    expect(w.find('[data-testid="tv3-bank-kp-kp-yd1"]').exists()).toBe(true)
    expect(w.text()).toContain('全部知识点')
  })

  it('点分类树知识点 → 按知识点过滤题目', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(3)

    await w.find('[data-testid="tv3-bank-kp-kp-yd2"]').trigger('click')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)
    expect(w.find('[data-testid="tv3-bank-q-q-ell-2"]').exists()).toBe(true)
  })

  it('点专题夹 → 按 folder_refs 过滤（引用式：题目仍挂树上）', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="tv3-bank-folder-fld-1"]').exists()).toBe(true)

    await w.find('[data-testid="tv3-bank-folder-fld-1"]').trigger('click')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)
    expect(w.find('[data-testid="tv3-bank-unfolder-q-ell-1"]').exists()).toBe(true)
    expect(w.text()).toContain('专题夹 · 圆锥曲线压轴')
  })

  it('新建专题夹 → 调 folders.create 并刷新列表', async () => {
    foldersCreate.mockResolvedValue({ data: { id: 'fld-9', name: '期中讲评', desc: '', count: 0, updated_at: '2026-09-03' } })
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-folder-new"]').trigger('click')
    await w.find('[data-testid="tv3-bank-folder-name"]').setValue('期中讲评')
    await w.find('[data-testid="tv3-bank-folder-go"]').trigger('click')
    await flushPromises()

    expect(foldersCreate).toHaveBeenCalledWith({ name: '期中讲评', desc: '' })
    expect(foldersList).toHaveBeenCalledTimes(2)
  })

  it('未选中专题夹时每行可「入夹」→ addQuestions 引用收集', async () => {
    foldersAdd.mockResolvedValue({ data: { ok: true, count: 2 } })
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-addfold-q-ell-2"]').setValue('fld-2')
    await flushPromises()

    expect(foldersAdd).toHaveBeenCalledWith('fld-2', { question_ids: ['q-ell-2'] })
  })

  it('选中夹后可把题目移出 → removeQuestion（不删题）', async () => {
    foldersRemoveQ.mockResolvedValue({ data: { ok: true, count: 0 } })
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-folder-fld-1"]').trigger('click')
    await w.find('[data-testid="tv3-bank-unfolder-q-ell-1"]').trigger('click')
    await flushPromises()

    expect(foldersRemoveQ).toHaveBeenCalledWith('fld-1', 'q-ell-1')
  })
})

describe('BankView · 入库：拍照原样 + 自编录入', () => {
  it('拍照入库：图片题原样（示例图上传拿 key，src=key + as_image=true）→ 刷新题库', async () => {
    quizScanImport.mockResolvedValue({ data: { id: 'q-img-9', kp_name: '拍照入库', kp_code: 'YD-01', q_type: 'image', difficulty: 'medium', stem_latex: '（图片题）', stem_image: 'data:image/svg+xml;utf8,<svg/>', answer: '待补', source: '拍照入库' } })
    // 2026-10-05 契约：入库必须有对象存储 key——示例图也走 uploadFile 中转
    uploadFn.mockResolvedValue({ key: 'mock-uploads/u-1/sample-question.svg', demo: false })
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-scan-open"]').trigger('click')
    expect(w.find('[data-testid="tv3-bank-scan-modal"]').exists()).toBe(true)

    // 载入示例原图（避免依赖真实文件输入）；组件会把示例图上传换 key
    await w.find('[data-testid="tv3-bank-scan-sample"]').trigger('click')
    await flushPromises()
    expect(uploadFn).toHaveBeenCalledWith(expect.any(File))
    // 图片题原样入库（默认 mode=image）；拿到 key 前入库按钮禁用（防 base64 垃圾行）
    expect(w.find('[data-testid="tv3-bank-scan-mode-image"]').exists()).toBe(true)
    expect((w.find('[data-testid="tv3-bank-scan-import"]').element as HTMLButtonElement).disabled).toBe(false)
    await w.find('[data-testid="tv3-bank-scan-import"]').trigger('click')
    await flushPromises()

    expect(quizScanImport).toHaveBeenCalledWith(expect.objectContaining({ src: 'mock-uploads/u-1/sample-question.svg', as_image: true, kp_code: 'YD-01' }))
    expect(quizQuestions).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="tv3-bank-scan-modal"]').exists()).toBe(false)
  })

  it('自编录入：结构化表单（含 $..$ 公式题干）→ quizCreate 落库 source=自编', async () => {
    quizCreate.mockResolvedValue({ data: { id: 'q-self-9', kp_name: '椭圆标准方程', kp_code: 'YD-01', q_type: 'fill', difficulty: 'medium', stem_latex: '自编题干', answer: '略', analysis: '略', source: '自编', folder_refs: [], usage_count: 0 } })
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-create-open"]').trigger('click')
    expect(w.find('[data-testid="tv3-bank-create-modal"]').exists()).toBe(true)

    await w.find('[data-testid="tv3-bank-create-stem"]').setValue('过椭圆 $\\frac{x^2}{4}+\\frac{y^2}{3}=1$ 右焦点的弦 $AB$，求 $|AB|$')
    await w.find('[data-testid="tv3-bank-create-answer"]').setValue('$\\frac{24}{7}$')
    await w.find('[data-testid="tv3-bank-create-go"]').trigger('click')
    await flushPromises()

    expect(quizCreate).toHaveBeenCalledWith(expect.objectContaining({
      q_type: 'solve', difficulty: 'medium',
      stem_latex: '过椭圆 $\\frac{x^2}{4}+\\frac{y^2}{3}=1$ 右焦点的弦 $AB$，求 $|AB|$',
      answer: '$\\frac{24}{7}$',
    }))
    // 落库后即时出现在列表首行
    expect(w.find('[data-testid="tv3-bank-q-q-self-9"]').exists()).toBe(true)
  })
})

describe('BankView · 标签筛选', () => {
  it('题型/难度/来源/搜索多维筛选', async () => {
    const w = mountPage()
    await flushPromises()

    await w.find('[data-testid="tv3-bank-type"]').setValue('image')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)
    expect(w.find('[data-testid="tv3-bank-q-q-drv-1"]').exists()).toBe(true)

    await w.find('[data-testid="tv3-bank-type"]').setValue('')
    await w.find('[data-testid="tv3-bank-diff"]').setValue('easy')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)

    await w.find('[data-testid="tv3-bank-diff"]').setValue('')
    await w.find('[data-testid="tv3-bank-source"]').setValue('拍照入库')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)

    await w.find('[data-testid="tv3-bank-source"]').setValue('')
    await w.find('[data-testid="tv3-bank-search"]').setValue('离心率')
    expect(w.findAll('[data-testid^="tv3-bank-q-"]')).toHaveLength(1)
    expect(w.find('[data-testid="tv3-bank-q-q-ell-2"]').exists()).toBe(true)
  })

  it('答案与解析可折叠展开（不默认占屏）', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[data-testid="tv3-bank-detail-box-q-ell-2"]').exists()).toBe(false)

    await w.find('[data-testid="tv3-bank-detail-q-ell-2"]').trigger('click')
    expect(w.find('[data-testid="tv3-bank-detail-box-q-ell-2"]').exists()).toBe(true)
    expect(w.text()).toContain('先化标准方程')
  })
})
