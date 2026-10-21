/**
 * 学生端「老师课堂」纯函数契约（Phase C）。
 *
 * 覆盖：来源徽章 / 课件列表字段映射 / 空态文案 / 课件舞台等比缩放。
 * 不依赖 DOM 与后端：所有断言只针对 `src/utils/dualClassroom.js` 的纯函数。
 */
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import fs from 'node:fs'
import path from 'node:path'
import DeckSlideStage from '@/components/dual/DeckSlideStage.vue'
import {
  DECK_STAGE_HEIGHT,
  DECK_STAGE_WIDTH,
  deckStageScale,
  mapTeacherCoursewares,
  sourceTypeMeta,
  teacherCoursewareEmptyText,
} from '@/utils/dualClassroom'

describe('sourceTypeMeta · teacher_deck', () => {
  it('老师课件来源显示「老师课堂」与 🎓 图标', () => {
    expect(sourceTypeMeta('teacher_deck')).toEqual({ label: '老师课堂', icon: '🎓' })
  })

  it('既有主题/拍题/教案来源不回归', () => {
    expect(sourceTypeMeta('topic').label).toBe('主题生成')
    expect(sourceTypeMeta('photo').label).toBe('拍题')
    expect(sourceTypeMeta('file').label).toBe('教案上传')
    expect(sourceTypeMeta(undefined).label).toBe('课堂')
  })
})

describe('deckStageScale · 教科件舞台等比缩放', () => {
  it('设计尺寸 980×551 为基准（1:1）', () => {
    expect(DECK_STAGE_WIDTH).toBe(980)
    expect(DECK_STAGE_HEIGHT).toBe(551)
    expect(deckStageScale(980, 551)).toBe(1)
  })

  it('宽度受限时按宽度等比缩放', () => {
    expect(deckStageScale(490)).toBe(0.5)
    expect(deckStageScale(490, 1000)).toBe(0.5)
  })

  it('高度受限时按高度等比缩放（高度 551*scale）', () => {
    expect(deckStageScale(1200, 275.5)).toBe(0.5)
    expect(deckStageScale(860, 340)).toBeCloseTo(0.6171, 4)
  })

  it('只缩不放：超宽容器封顶 1', () => {
    expect(deckStageScale(1960, 1102)).toBe(1)
    expect(deckStageScale(3000, 2000)).toBe(1)
  })

  it('容器未测量（0/NaN）回退 1，极端窄容器保底 0.1', () => {
    expect(deckStageScale(0, 0)).toBe(1)
    expect(deckStageScale(Number.NaN)).toBe(1)
    expect(deckStageScale(5, 5)).toBe(0.1)
  })
})

describe('mapTeacherCoursewares · 老师课堂列表映射', () => {
  const raw = {
    courseware_id: 'cw-1',
    title: '1.1 集合的概念',
    class_id: 'class-1',
    class_name: '高二2班',
    teacher_name: '张老师',
    page_count: 10,
    published_at: '2026-10-20T08:30:00',
    opened: false,
    progress_pct: 0,
    practice_accuracy: null,
    last_session_id: null,
  }

  it('映射卡片所需字段（老师/班级/页数/进度/正确率/状态）', () => {
    const c = mapTeacherCoursewares([raw])[0]!
    expect(c).toMatchObject({
      id: 'cw-1',
      title: '1.1 集合的概念',
      teacherName: '张老师',
      className: '高二2班',
      pageCount: 10,
      opened: false,
      progressPct: 0,
      practiceAccuracy: null,
      statusText: '未开始',
      tone: 'idle',
      actionText: '开始学习',
    })
    expect(c.publishedText).not.toBe('')
  })

  it('已打开 → 学习中；已学完 → 已学完；百分比夹在 0–100', () => {
    const doing = mapTeacherCoursewares([{ ...raw, opened: true, progress_pct: 40, practice_accuracy: 120 }])[0]!
    expect(doing.statusText).toBe('学习中')
    expect(doing.tone).toBe('doing')
    expect(doing.actionText).toBe('继续学习')
    expect(doing.practiceAccuracy).toBe(100)

    const done = mapTeacherCoursewares([{ ...raw, opened: true, progress_pct: 100 }])[0]!
    expect(done.statusText).toBe('已学完')
    expect(done.tone).toBe('done')

    const negative = mapTeacherCoursewares([{ ...raw, progress_pct: -12 }])[0]!
    expect(negative.progressPct).toBe(0)
  })

  it('缺失老师名/班级时保留空串（视图诚实省略，不伪造）', () => {
    const c = mapTeacherCoursewares([{ courseware_id: 'cw-2', title: '习题课' }])[0]!
    expect(c.teacherName).toBe('')
    expect(c.className).toBe('')
    expect(c.pageCount).toBe(0)
    expect(c.title).toBe('习题课')
  })

  it('丢弃无 courseware_id 的脏条目；非数组返回空数组（空态）', () => {
    expect(mapTeacherCoursewares([{ title: '无 id' }, null, raw])).toHaveLength(1)
    expect(mapTeacherCoursewares(null)).toEqual([])
    expect(mapTeacherCoursewares(undefined)).toEqual([])
    expect(mapTeacherCoursewares({})).toEqual([])
  })
})

describe('teacherCoursewareEmptyText · 空态诚实', () => {
  it('未加载 / 失败 / 真无内容三种文案不混淆', () => {
    expect(teacherCoursewareEmptyText({ loading: true })).toContain('正在加载')
    expect(teacherCoursewareEmptyText({ failed: true })).toContain('加载失败')
    expect(teacherCoursewareEmptyText({})).toContain('老师还没有发布课堂课件')
  })

  it('有内容时不渲染空态', () => {
    expect(teacherCoursewareEmptyText({ count: 2 })).toBe('')
    expect(teacherCoursewareEmptyText({ loading: true, count: 1 })).toBe('')
  })
})

/* ===== DeckSlideStage：学生端舞台真实复用教师端 SlideCanvas（组件级冒烟） ===== */
class FakeResizeObserver {
  observe() { /* noop */ }
  unobserve() { /* noop */ }
  disconnect() { /* noop */ }
}

describe('DeckSlideStage · 渲染老师课件', () => {
  it('用 SlideCanvas 渲染 deck_slide（标题/要点/页序），资产 URL 直接透传', () => {
    const original = globalThis.ResizeObserver
    // jsdom 无 ResizeObserver：注入最小实现（组件只依赖 observe/disconnect）
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    try {
      const assetUrl = 'data:image/png;base64,AAAA'
      const wrapper = mount(DeckSlideStage, {
        global: { plugins: [createPinia()] },
        props: {
          slide: {
            id: 'slide-2',
            type: 'content',
            title: '集合的表示方法',
            // deck_slide 不重复携带 assets 数组：资产 id 由 assetUrls 的 key 兜底
            elements: [{ role: 'figure' }, { role: 'bullets', items: ['列举法', '描述法'] }],
          },
          deckTitle: '1.1 集合的概念',
          textbookLine: '人教A版2019 必修第一册',
          assetUrls: { 'asset-1': assetUrl },
          ordinal: 2,
        },
      })
      expect(wrapper.find('.pc-slide').exists()).toBe(true)
      expect(wrapper.text()).toContain('集合的表示方法')
      expect(wrapper.text()).toContain('列举法')
      // 资产图直接使用后端下发的 data URL，不触发教师端 presign 取图
      expect(wrapper.find('.pc-el__fig-img').attributes('src')).toBe(assetUrl)
      expect(fetchSpy).not.toHaveBeenCalled()
      // 序号徽标用 ordinal（非封面页）
      expect(wrapper.find('.pc-slide__badge').text()).toBe('二')
      wrapper.unmount()
    } finally {
      fetchSpy.mockRestore()
      globalThis.ResizeObserver = original
    }
  })
})

describe('DeckSlideStage · 有资产但元素少的页不得按稀疏页隐藏图', () => {
  it('assetUrls 存在时即使没有 figure 元素也渲染图片', () => {
    const original = globalThis.ResizeObserver
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver
    try {
      const assetUrl = 'data:image/png;base64,BBBB'
      const wrapper = mount(DeckSlideStage, {
        global: { plugins: [createPinia()] },
        props: {
          slide: {
            id: 'slide-3',
            type: 'image-focus',
            title: '新课引入',
            elements: [{ role: 'bullets', items: ['对象是确定的'] }],
          },
          deckTitle: '1.1 集合的概念',
          assetUrls: { 'asset-venn': assetUrl },
          ordinal: 3,
        },
      })
      expect(wrapper.find('.pc-el__fig-img').attributes('src')).toBe(assetUrl)
      wrapper.unmount()
    } finally {
      globalThis.ResizeObserver = original
    }
  })

  it('老师课件一律复用 DeckSlideStage（含稀疏页，禁止第二套版面）', () => {
    const src = fs.readFileSync(path.join(process.cwd(), 'src/pages/student/DualView.vue'), 'utf-8')
    expect(src).toContain('v-if="isTeacherDeck"')
    expect(src).not.toContain('isSparseDeckSlide')
  })
})
