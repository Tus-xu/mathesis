/**
 * 双师课堂前端纯函数单测：六段式归类 / 验证徽章 / 来源徽章 / 标题推导 / 时间工具。
 * 不依赖 DOM 与后端。
 */
import { describe, expect, it, vi } from 'vitest'
import {
  groupSlideSections,
  splitSolutionSteps,
  practiceAccuracy,
  verificationMeta,
  sourceTypeMeta,
  deriveSourceTitle,
  percent,
  fmtDuration,
  fmtClock,
  fmtTime,
} from '@/utils/dualClassroom'

describe('groupSlideSections 课堂版式归类', () => {
  const slide = {
    key_points: ['掌握导数求法'],
    blocks: [
      { kind: 'text', text: '讲解文本' },
      { kind: 'latex', latex: 'f\'(x)=3x^2' },
      { kind: 'plot2d', expr: 'x^3-3*x', caption: '单调性图像' },
      { kind: 'example', question: '求极值', analysis: '① 求导；② 验符号', answer: '±1' },
      { kind: 'note', text: '易错提醒' },
      { kind: 'theorem', title: '单调性判定', body: '若 $f\'(x)>0$ 则递增' },
      { kind: 'table', headers: ['x', "f'(x)"], rows: [['-1', '0']] },
    ],
  }
  it('把 blocks 归入定理/讲解/例题/表格/图形/公式/结论', () => {
    const s = groupSlideSections(slide)
    expect(s.goals).toEqual(['掌握导数求法'])
    expect(s.lecture).toEqual([{ kind: 'text', text: '讲解文本' }])
    expect(s.formulas).toHaveLength(1)
    expect(s.figures).toHaveLength(1)
    expect(s.examples).toHaveLength(1)
    expect(s.practice).toHaveLength(1)
    expect(s.summary).toHaveLength(1)
    expect(s.theorems).toHaveLength(1)
    expect(s.tables).toHaveLength(1)
  })
  it('教材关联块进 textbook 字段（仅在有真实出处时展示）', () => {
    const s = groupSlideSections({
      blocks: [
        {
          kind: 'textbook_association',
          citations: [{ title: '普通高中数学选择性必修第一册', section: '空间向量与立体几何' }],
        },
      ],
    })

    expect(s.textbook).toEqual(
      expect.objectContaining({ kind: 'textbook_association' }),
    )
  })
  it('过滤旧版管线遗留的工程警告块（学生端零警告）', () => {
    const s = groupSlideSections({
      blocks: [
        { kind: 'text', text: '正常讲解' },
        { kind: 'note', text: '椭圆焦点三角形 MF₁F₂ 的内心 I——未渲染图形，请先补充题目条件或原题图片。' },
        { kind: 'note', text: '此页涉及几何图形，但缺少可验证构造数据，未渲染默认图。' },
        { kind: 'note', text: '缺少经真实题目条件支持的 geometry 内容，未自动补造。' },
        { kind: 'note', text: '结论：内心到三边距离相等' },
      ],
    })
    expect(s.summary).toHaveLength(1)
    expect(s.summary[0].text).toContain('结论')
    expect(s.lecture).toHaveLength(1)
  })
  it('空 slide 返回全空结构', () => {
    const s = groupSlideSections(null)
    expect(s).toEqual({
      goals: [], theorems: [], lecture: [], examples: [], tables: [],
      figures: [], formulas: [], summary: [], textbook: null, practice: [],
    })
  })
})

describe('splitSolutionSteps 例题分步', () => {
  it('按 ①②③ 圆圈编号切分', () => {
    const steps = splitSolutionSteps('① 求导 f\'(x)=3x²−3；② 令 f\'(x)=0 得 x=±1；③ 判符号')
    expect(steps).toHaveLength(3)
    expect(steps[0]).toContain('①')
  })
  it('无编号时按分号/换行切分', () => {
    expect(splitSolutionSteps('求导；令零点；验单调')).toHaveLength(3)
    expect(splitSolutionSteps('第一行\n第二行')).toHaveLength(2)
  })
  it('空解析返回空数组', () => {
    expect(splitSolutionSteps('')).toEqual([])
  })
})

describe('practiceAccuracy 分层练习正确率', () => {
  it('跨档汇总为 0-100 整数', () => {
    expect(practiceAccuracy({ basic: { total: 3, correct: 2 }, advanced: { total: 1, correct: 1 } })).toBe(75)
  })
  it('无作答返回 0', () => {
    expect(practiceAccuracy({})).toBe(0)
    expect(practiceAccuracy(null)).toBe(0)
  })
})

describe('verificationMeta 验证徽章', () => {
  it('映射 verified/needs_review/failed 状态', () => {
    expect(verificationMeta('verified').tone).toBe('ok')
    expect(verificationMeta('needs_review').tone).toBe('warn')
    expect(verificationMeta('failed').tone).toBe('err')
    expect(verificationMeta(undefined).tone).toBe('muted')
  })
})

describe('sourceTypeMeta 来源徽章', () => {
  it('topic/photo/file 三类来源', () => {
    expect(sourceTypeMeta('topic').label).toBe('主题生成')
    expect(sourceTypeMeta('photo').label).toBe('拍题')
    expect(sourceTypeMeta('file').label).toBe('教案上传')
    expect(sourceTypeMeta('x').label).toBe('课堂')
  })
})

describe('deriveSourceTitle 标题推导', () => {
  it('取首行去编号作为标题', () => {
    expect(deriveSourceTitle('1. 已知 f(x)=x^3 求导\n第二行')).toContain('已知')
    expect(deriveSourceTitle('   （2）证明三线共面')).toContain('证明')
  })
  it('空文本回退默认标题', () => {
    expect(deriveSourceTitle('   ')).toBe('数学课堂')
  })
  it('超长标题截断', () => {
    const long = '这是一段非常非常非常非常非常非常非常非常非常非常非常长的标题内容用来测试截断'
    expect(deriveSourceTitle(long).length).toBeLessThanOrEqual(22)
  })
})

describe('基础工具', () => {
  it('percent 区间约束', () => {
    expect(percent(1, 10)).toBe(10)
    expect(percent(10, 10)).toBe(100)
    expect(percent(3, 0)).toBe(0)
  })
  it('fmtDuration 分秒格式化', () => {
    expect(fmtDuration(65)).toBe('01:05')
    expect(fmtDuration(0)).toBe('00:00')
  })
  it('fmtClock 课堂计时格式化', () => {
    expect(fmtClock(3727)).toBe('01:02:07')
    expect(fmtClock(59)).toBe('00:00:59')
  })
  it('fmtTime 非法输入返回空串', () => {
    expect(fmtTime('')).toBe('')
    expect(fmtTime('bad-date')).toBe('')
  })
})

/* ===== useClassroomUpload：解析结果文本提取契约（不触发真实网络） ===== */
import {
  useClassroomUpload,
  normalizeClassroomParseResult,
  confirmClassroomPhotoParseQuality,
} from '@/composables/useClassroomUpload'

describe('useClassroomUpload 契约', () => {
  it('返回默认状态与 selectAndParse 函数', () => {
    const toast = { success: vi.fn(), error: vi.fn() }
    const { uploading, stageText, lastFileId, lastFileName, selectAndParse } = useClassroomUpload(toast)
    expect(uploading.value).toBe(false)
    expect(stageText.value).toBe('')
    expect(typeof selectAndParse).toBe('function')
  })

  it('保留拍题的条件与待确认项，供生成课堂前人工核对', () => {
    const result = normalizeClassroomParseResult({
      assets: [{ asset_type: 'markdown', page_no: 1, content: '已知 $f(x)=x^2-1$，求零点。' }],
      parse_quality: {
        provider: 'mimo-v2.5',
        confidence: 0.74,
        conditions: ['定义域为实数集'],
        uncertainties: ['图中横坐标刻度模糊'],
        needs_confirmation: true,
      },
    })

    expect(result.text).toContain('$f(x)=x^2-1$')
    expect(result.parseQuality).toMatchObject({
      provider: 'mimo-v2.5',
      confidence: 0.74,
      needs_confirmation: true,
      uncertainties: ['图中横坐标刻度模糊'],
    })
  })

  it('学生确认题意后，保留原始识别审计信息并显式标记确认', () => {
    const quality = confirmClassroomPhotoParseQuality({
      provider: 'mimo-v2.5',
      confidence: 0.74,
      conditions: ['AE ⟂ CD'],
      uncertainties: ['图中虚线端点需要人工核对'],
      needs_confirmation: true,
    })

    expect(quality).toMatchObject({
      confirmed_by_user: true,
      needs_confirmation: true,
      uncertainties: ['图中虚线端点需要人工核对'],
    })
  })
})
