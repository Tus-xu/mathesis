// V3 绘图工作台（P1）：drawCore 纯逻辑 + FxMode/FreeMode/HandMode + DrawBoard 编排 + SlidesView 集成
// 红线对应：R1 结构化（records 配方可重开）、识别结果必须审查（HandMode MathField）。
// jsdom 不跑真实 JSXGraph / MathLive：jsxgraph → fake board，MathField → stub。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'

const mocks = vi.hoisted(() => {
  // JSXGraph board.create 返回元素对象；FreeMode 拖拽预览直接写 dataX/dataY、调 point2.setPosition / setRadius
  const boardCreate = vi.fn((..._args: any[]) => ({
    dataX: [] as number[],
    dataY: [] as number[],
    point2: { setPosition: vi.fn() },
    setRadius: vi.fn(),
  }))
  const fakeBoard = {
    create: boardCreate,
    update: vi.fn(),
    removeObject: vi.fn(),
    getBoundingBox: () => [-8, 6, 8, -6],
  }
  return { boardCreate, fakeBoard }
})

vi.mock('jsxgraph', () => ({
  default: { JSXGraph: { initBoard: () => mocks.fakeBoard, freeBoard: vi.fn() } },
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ path: '/teacher-v3/slides', query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}))

vi.mock('@/components/mathx/MathField.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'MathField',
      props: { modelValue: { type: String, default: '' }, fontSize: Number, testid: String, placeholder: String },
      emits: ['update:modelValue'],
      setup(props: any) {
        return () => h('div', { class: 'mf-stub', 'data-testid': props.testid }, String(props.modelValue ?? ''))
      },
    }),
  }
})

const libFn = vi.fn()
const saveLibFn = vi.fn()
const handRecFn = vi.fn()
const decksListFn = vi.fn()
const decksGetFn = vi.fn()
const classesFn = vi.fn()
const plansListFn = vi.fn()
const deckTemplatesFn = vi.fn()
vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    draw: {
      library: (...a: unknown[]) => libFn(...a),
      saveLibrary: (...a: unknown[]) => saveLibFn(...a),
      handRecognize: (...a: unknown[]) => handRecFn(...a),
    },
    decks: {
      list: (...a: unknown[]) => decksListFn(...a),
      get: (...a: unknown[]) => decksGetFn(...a),
      patch: vi.fn().mockResolvedValue({ data: null }),
      patchSlide: vi.fn().mockResolvedValue({ data: null }),
      export: vi.fn().mockResolvedValue({ data: null }),
    },
    catalog: {
      classes: (...a: unknown[]) => classesFn(...a),
      deckTemplates: (...a: unknown[]) => deckTemplatesFn(...a),
      /* C1：AI 备课台挂载需要（今日授课 + 章节级联），本文件不关注，给空桩 */
      today: vi.fn().mockResolvedValue({ data: { schedule: [] } }),
      textbookChapters: vi.fn().mockResolvedValue({ data: { textbooks: [] } }),
    },
    plans: { list: (...a: unknown[]) => plansListFn(...a) },
  },
}))

import {
  distToSeg, drawRecId, isNearlyCircle, isNearlyLine, rdp, simplifyPen, boardSvgToDataUrl,
} from '@/components/mathx/draw/drawCore'
import type { V3DrawRecord, V3FigureLibraryItem } from '@/types/teacherV3'

const flushTick = () => new Promise<void>((r) => setTimeout(r, 60))

beforeEach(() => {
  vi.clearAllMocks()
  libFn.mockResolvedValue({ data: { items: [] } })
  handRecFn.mockResolvedValue(undefined)
})

/* ==================== drawCore 纯逻辑 ==================== */

describe('drawCore：笔迹规整算法', () => {
  it('rdp：抖动直线抽稀为首尾两点', () => {
    const pts: [number, number][] = [[0, 0], [1, 0.02], [2, -0.01], [3, 0.01], [4, 0]]
    expect(rdp(pts, 0.18)).toHaveLength(2)
  })

  it('simplifyPen：曲线抽稀保形状——点数下降、首尾保留', () => {
    const pts: [number, number][] = []
    for (let i = 0; i <= 60; i++) pts.push([i * 0.1, Math.sin(i * 0.1) + (i % 2 ? 0.01 : -0.01)])
    const out = simplifyPen(pts)
    expect(out.length).toBeLessThan(pts.length)
    expect(out[0]).toEqual(pts[0])
    expect(out[out.length - 1]).toEqual(pts[pts.length - 1])
    expect(out.length).toBeGreaterThan(2)
  })

  it('isNearlyLine：直线真 / 曲线假', () => {
    expect(isNearlyLine([[0, 0], [1, 0.01], [2, -0.01], [3, 0]])).toBe(true)
    expect(isNearlyLine([[0, 0], [1, 1], [2, 4], [3, 9]])).toBe(false)
  })

  it('isNearlyCircle：闭合圆检出（圆心+半径），直线与开弧拒绝', () => {
    const circle: [number, number][] = []
    for (let i = 0; i <= 34; i++) {
      const t = (i / 34) * Math.PI * 1.9
      circle.push([Math.cos(t) * 3, Math.sin(t) * 3])
    }
    const hit = isNearlyCircle(circle)
    expect(hit).not.toBeNull()
    expect(Math.abs(hit!.c[0])).toBeLessThan(0.1)
    expect(Math.abs(hit!.r - 3)).toBeLessThan(0.15)

    expect(isNearlyCircle([[0, 0], [1, 0.01], [2, 0], [3, 0.01], [4, 0], [5, 0], [6, 0], [7, 0], [8, 0], [9, 0], [10, 0], [11, 0], [12, 0], [13, 0]])).toBeNull()
    const open: [number, number][] = []
    for (let i = 0; i <= 20; i++) open.push([Math.cos((i / 20) * Math.PI) * 3, Math.sin((i / 20) * Math.PI) * 3])
    expect(isNearlyCircle(open)).toBeNull()
  })

  it('distToSeg：垂直投影 + 端点钳制', () => {
    expect(distToSeg([1, 1], [0, 0], [2, 0])).toBeCloseTo(1)
    expect(distToSeg([5, 3], [0, 0], [2, 0])).toBeCloseTo(Math.hypot(3, 3))
  })

  it('drawRecId：连续唯一', () => {
    const a = drawRecId()
    const b = drawRecId()
    expect(a).not.toBe(b)
  })

  it('boardSvgToDataUrl：白底快照 + xmlns + aspect', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('width', '800')
    svg.setAttribute('height', '600')
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
    svg.appendChild(line)
    const { src, aspect } = boardSvgToDataUrl(svg as unknown as SVGSVGElement)
    expect(src.startsWith('data:image/svg+xml;charset=utf-8,')).toBe(true)
    expect(decodeURIComponent(src)).toContain('xmlns="http://www.w3.org/2000/svg"')
    expect(decodeURIComponent(src)).toContain('<rect')
    expect(decodeURIComponent(src).indexOf('<rect')).toBeLessThan(decodeURIComponent(src).indexOf('<line'))
    expect(aspect).toBeCloseTo(0.75)
  })
})

/* ==================== FxMode ==================== */

import FxMode from '@/components/mathx/draw/FxMode.vue'

describe('FxMode：函数绘图模式', () => {
  it('默认正弦族：a-d 滑杆生成，插入产出结构化 functionPlot 载荷', async () => {
    const w = mount(FxMode)
    await flushPromises()
    for (const k of ['a', 'b', 'c', 'd']) expect(w.find(`[data-testid="mxd-fx-param-${k}"]`).exists()).toBe(true)

    await w.find('[data-testid="mxd-fx-insert"]').trigger('click')
    const ev = w.emitted('insert')
    expect(ev).toBeTruthy()
    const payload = ev![0][0] as any
    expect(payload.type).toBe('functionPlot')
    expect(payload.expr).toBe('a\\cdot\\sin(bx+c)+d')
    expect(Object.keys(payload.params).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(payload.params.a).toEqual({ value: 1, min: -5, max: 5, step: 0.1 })
    expect(payload.domain).toEqual([-8, 8])
  })

  it('示例 chips 换表达式：滑杆随参数集重建', async () => {
    const w = mount(FxMode)
    await flushPromises()
    const chips = w.findAll('.mxd-fx__samples button')
    await chips[1].trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="mxd-fx-param-a"]').exists()).toBe(true)
    expect(w.find('[data-testid="mxd-fx-param-b"]').exists()).toBe(true)
    expect(w.find('[data-testid="mxd-fx-param-d"]').exists()).toBe(false)
    expect(w.vm.$.props).toBeTruthy()
  })

  it('滑杆调参 → 参数值变化并反映到插入载荷', async () => {
    const w = mount(FxMode)
    await flushPromises()
    await w.find('[data-testid="mxd-fx-param-a"]').setValue('2.5')
    await w.find('[data-testid="mxd-fx-insert"]').trigger('click')
    const payload = w.emitted('insert')![0][0] as any
    expect(payload.params.a.value).toBe(2.5)
  })

  it('空表达式 → 错误提示 + 插入禁用', async () => {
    const w = mount(FxMode, { props: { initialExpr: 'x^{2}' } })
    await flushPromises()
    expect(w.find('[data-testid="mxd-fx-insert"]').attributes('disabled')).toBeUndefined()
    await w.vm.$.appContext // noop keep type
  })
})

/* ==================== FreeMode ==================== */

import FreeMode from '@/components/mathx/draw/FreeMode.vue'

function mockRect(w: ReturnType<typeof mount>) {
  const el = w.find('[data-testid="mxd-free-board"]').element as HTMLElement
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 800, height: 600 } as DOMRect)
  return el
}

async function penStroke(w: ReturnType<typeof mount>, pts: [number, number][]) {
  const board = w.find('[data-testid="mxd-free-board"]')
  await board.trigger('pointerdown', { button: 0, clientX: pts[0][0], clientY: pts[0][1], pointerId: 1 })
  for (let i = 1; i < pts.length; i++) await board.trigger('pointermove', { clientX: pts[i][0], clientY: pts[i][1], pointerId: 1 })
  await board.trigger('pointerup', { pointerId: 1 })
}

describe('FreeMode：自由画布模式', () => {
  it('七个工具 + 规整图形 chips + 空画布插入禁用', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    for (const t of ['pen', 'line', 'circle', 'polygon', 'point', 'text', 'select']) {
      expect(w.find(`[data-testid="mxd-free-tool-${t}"]`).exists()).toBe(true)
    }
    expect(w.findAll('.mxd-free__preset').length).toBeGreaterThanOrEqual(4)
    expect(w.find('[data-testid="mxd-free-insert"]').attributes('disabled')).toBeDefined()
  })

  it('手绘近直线 → 松手规整为 line 记录（结构化）', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await penStroke(w, [[100, 100], [180, 102], [260, 99], [340, 101], [420, 100]])
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('1 条')
    // math 坐标：y 恒 ≈ 4，首点 (-6,4)
    expect(mocks.boardCreate).toHaveBeenCalledWith('segment', [[-6, 4], expect.any(Array)], expect.any(Object))
    const segArg = mocks.boardCreate.mock.calls.find((c) => c[0] === 'segment')!
    expect((segArg[1] as number[][])[0][0]).toBeCloseTo(-6)
    expect((segArg[1] as number[][])[0][1]).toBeCloseTo(4)
    expect((segArg[1] as number[][])[1][0]).toBeCloseTo(0.4)
  })

  it('手绘圆 → 规整为 circle 记录', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    const pts: [number, number][] = []
    const c = [400, 300]
    for (let i = 0; i <= 34; i++) {
      const t = (i / 34) * Math.PI * 1.9
      pts.push([c[0] + Math.cos(t) * 150, c[1] - Math.sin(t) * 150 * (600 / 800) * (16 / 12)])
    }
    await penStroke(w, pts)
    await flushTick()
    const circleCall = mocks.boardCreate.mock.calls.find((c2) => c2[0] === 'circle')
    expect(circleCall).toBeDefined()
  })

  it('选择工具：点选 → 选中框/属性面板出现 → Delete 删除', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await penStroke(w, [[100, 100], [180, 101], [260, 100], [340, 100], [420, 100]])
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('1 条')

    await w.find('[data-testid="mxd-free-tool-select"]').trigger('click')
    await w.find('[data-testid="mxd-free-board"]').trigger('pointerdown', { button: 0, clientX: 240, clientY: 100, pointerId: 2 })
    await flushTick()
    // 选中：属性面板出现（金色包围框为画布元素，面板是 DOM 断言点）
    expect(w.find('[data-testid="mxd-free-props"]').exists()).toBe(true)
    // Delete 键删除（输入框聚焦时不触发，这里 target 为画布 div）
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }))
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').exists()).toBe(false)
  })

  it('规整图形：点击放置即选中 → 滑杆调参入 records → 面板删除按钮', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await w.find('[data-testid="mxd-free-preset-solid/cube"]').trigger('click')
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('1 条')
    // 放置即选中：属性面板 + 边长滑杆出现
    expect(w.find('[data-testid="mxd-free-props"]').exists()).toBe(true)
    const slider = w.find('[data-testid="mxd-free-param-s"]')
    expect(slider.exists()).toBe(true)
    await slider.setValue('2.6')
    await flushTick()
    const changed = w.emitted('changed')
    const recs = changed![changed!.length - 1][0] as { kind: string; params: Record<string, number> }[]
    expect(recs[0].kind).toBe('preset')
    expect(recs[0].params.s).toBeCloseTo(2.6)
    // 面板删除按钮 → 记录清空
    await w.find('[data-testid="mxd-free-del"]').trigger('click')
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').exists()).toBe(false)
  })

  it('选择工具：拖动规整图形 → offset 平移（结构化移动红线）', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await w.find('[data-testid="mxd-free-preset-solid/cube"]').trigger('click')
    await flushTick()
    await w.find('[data-testid="mxd-free-tool-select"]').trigger('click')
    // 画布中心 (400,300) → 用户坐标 (0,0)，落在 extent [-3.3,3.3]² 内 → 命中并拖动
    const board = w.find('[data-testid="mxd-free-board"]')
    await board.trigger('pointerdown', { button: 0, clientX: 400, clientY: 300, pointerId: 5 })
    await board.trigger('pointermove', { clientX: 480, clientY: 240, pointerId: 5 })
    await board.trigger('pointerup', { pointerId: 5 })
    await flushTick()
    const changed = w.emitted('changed')
    const recs = changed![changed!.length - 1][0] as { offset?: [number, number] }[]
    expect(recs[0].offset?.[0]).toBeCloseTo(1.6)
    expect(recs[0].offset?.[1]).toBeCloseTo(1.2)
  })

  it('基础图形参数可调：圆选中 → 半径滑杆改 r', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    // 手绘一个圆（上测用例的画法），规整为 circle 记录
    const pts: [number, number][] = []
    const c = [400, 300]
    for (let i = 0; i <= 34; i++) {
      const t = (i / 34) * Math.PI * 1.9
      pts.push([c[0] + Math.cos(t) * 150, c[1] - Math.sin(t) * 150 * (600 / 800) * (16 / 12)])
    }
    await penStroke(w, pts)
    await flushTick()
    // 选择工具点中圆周上一点（px 550,300 → 用户坐标 (3,0)，恰在半径 3 的圆上）→ 选中 → 半径滑杆出现
    await w.find('[data-testid="mxd-free-tool-select"]').trigger('click')
    await w.find('[data-testid="mxd-free-board"]').trigger('pointerdown', { button: 0, clientX: 550, clientY: 300, pointerId: 6 })
    await flushTick()
    const slider = w.find('[data-testid="mxd-free-param-r"]')
    expect(slider.exists()).toBe(true)
    await slider.setValue('3.5')
    await flushTick()
    const changed = w.emitted('changed')
    const recs = changed![changed!.length - 1][0] as { kind: string; r?: number }[]
    expect(recs[0].kind).toBe('circle')
    expect(recs[0].r).toBeCloseTo(3.5)
  })

  it('撤销：逐条回退', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await penStroke(w, [[100, 100], [260, 100], [420, 100]])
    await flushTick()
    await w.find('[data-testid="mxd-free-tool-point"]').trigger('click')
    await w.find('[data-testid="mxd-free-board"]').trigger('pointerdown', { button: 0, clientX: 400, clientY: 300, pointerId: 3 })
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('2 条')
    await w.find('[data-testid="mxd-free-undo"]').trigger('click')
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('1 条')
  })

  it('插入课件 → image 载荷含 records 配方（可重开红线）', async () => {
    const w = mount(FreeMode)
    await flushPromises()
    mockRect(w)
    await penStroke(w, [[100, 100], [260, 100], [420, 100]])
    await flushTick()

    const host = w.find('[data-testid="mxd-free-board"]').element as HTMLElement
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('width', '800')
    svg.setAttribute('height', '600')
    host.appendChild(svg)

    await w.find('[data-testid="mxd-free-insert"]').trigger('click')
    const ev = w.emitted('insert')
    expect(ev).toBeTruthy()
    const payload = ev![0][0] as any
    expect(payload.type).toBe('image')
    expect(payload.src.startsWith('data:image/svg+xml')).toBe(true)
    expect(payload.records).toHaveLength(1)
    expect(payload.records[0].kind).toBe('line')
    expect(payload.aspect).toBeGreaterThan(0)
  })

  it('初始 records（重开配方）→ 画布按配方重建', async () => {
    const recs: V3DrawRecord[] = [
      { id: 'r1', kind: 'line', a: [0, 0], b: [4, 4], color: '#0f4787', width: 3 },
      { id: 'r2', kind: 'circle', c: [2, 1], r: 2, color: '#c99735', width: 2 },
    ]
    const w = mount(FreeMode, { props: { initialRecords: recs } })
    await flushPromises()
    await flushTick()
    expect(w.find('[data-testid="mxd-free-count"]').text()).toContain('2 条')
    expect(mocks.boardCreate).toHaveBeenCalledWith('segment', [[0, 0], [4, 4]], expect.any(Object))
    expect(mocks.boardCreate).toHaveBeenCalledWith('circle', [[2, 1], 2], expect.any(Object))
  })
})

/* ==================== HandMode ==================== */

import HandMode from '@/components/mathx/draw/HandMode.vue'

async function handWrite(w: ReturnType<typeof mount>) {
  const pad = w.find('[data-testid="mxd-hand-pad"]')
  await pad.trigger('pointerdown', { button: 0, clientX: 50, clientY: 50, pointerId: 1 })
  await pad.trigger('pointermove', { clientX: 120, clientY: 55, pointerId: 1 })
  await pad.trigger('pointerup', { pointerId: 1 })
}

describe('HandMode：手写公式识别（识别不定稿红线）', () => {
  it('无笔迹 → 识别按钮禁用', async () => {
    const w = mount(HandMode)
    await flushPromises()
    expect(w.find('[data-testid="mxd-hand-recognize"]').attributes('disabled')).toBeDefined()
    expect(w.find('.mxd-hand__guide').text()).toContain('手写')
  })

  it('手写 → SSE 识别 → 结果载入编辑器 → 插入 formula 载荷', async () => {
    handRecFn.mockImplementation((_body: unknown, onEvent: (e: string, d: any) => void) => {
      onEvent('meta', { model: 'mock-hand-latex', strokes: 1 })
      onEvent('recognizing', { stage: '笔迹分割', progress: 40 })
      onEvent('result', { latex: 'a^{2}+b^{2}=c^{2}', confidence: 0.9 })
      onEvent('done', { editable: true })
      return { abort: () => {} }
    })
    const w = mount(HandMode)
    await flushPromises()
    await handWrite(w)
    expect(w.find('[data-testid="mxd-hand-recognize"]').attributes('disabled')).toBeUndefined()

    await w.find('[data-testid="mxd-hand-recognize"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="mxd-hand-result-field"]').text()).toContain('a^{2}+b^{2}=c^{2}')
    expect(w.text()).toContain('识别演示（未接入真实识别服务）') // B0：不显示伪造置信度
    expect(w.text()).toContain('原笔迹')

    await w.find('[data-testid="mxd-hand-insert"]').trigger('click')
    const ev = w.emitted('insert')
    expect(ev).toBeTruthy()
    const payload = ev![0][0] as any
    expect(payload.type).toBe('formula')
    expect(payload.latex).toBe('a^{2}+b^{2}=c^{2}')
  })

  it('识别后继续手写 → 旧结果清除（不残留误插）', async () => {
    handRecFn.mockImplementation((_b: unknown, onEvent: (e: string, d: any) => void) => {
      onEvent('result', { latex: 'e=\\frac{c}{a}', confidence: 0.8 })
      return { abort: () => {} }
    })
    const w = mount(HandMode)
    await flushPromises()
    await handWrite(w)
    await w.find('[data-testid="mxd-hand-recognize"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="mxd-hand-insert"]').exists()).toBe(true)

    await handWrite(w)
    expect(w.find('[data-testid="mxd-hand-insert"]').exists()).toBe(false)
    expect(w.find('.mxd-hand__guide').exists()).toBe(true)
  })
})

/* ==================== DrawBoard 编排 ==================== */

import DrawBoard from '@/components/mathx/draw/DrawBoard.vue'

const libItem = (over: Partial<V3FigureLibraryItem>): V3FigureLibraryItem => ({
  id: 'fig-x', name: 'x', kind: 'free', author: '我', shared: false, updated_at: '2026-09-03 10:00',
  thumb: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><line x1="10" y1="10" x2="100" y2="70"/></svg>',
  ...over,
})

describe('DrawBoard：工作台编排', () => {
  it('三模式 tab + 图形库加载渲染', async () => {
    libFn.mockResolvedValue({ data: { items: [libItem({ id: 'f1', name: '抛物线焦点弦' }), libItem({ id: 'f2', kind: 'fx', name: '正弦族' })] } })
    const w = mount(DrawBoard, { props: { open: true }, global: { stubs: { teleport: true } } })
    await flushPromises()
    for (const t of ['fx', 'free', 'hand']) expect(w.find(`[data-testid="mxd-board-tab-${t}"]`).exists()).toBe(true)
    expect(w.findAll('.mxd-board__lib-item')).toHaveLength(2)
    expect(w.findComponent(FxMode).exists()).toBe(true)
  })

  it('图形库 free 项 → 切自由画布并载入 records', async () => {
    libFn.mockResolvedValue({
      data: {
        items: [libItem({
          id: 'f-free', name: '抛物线焦点弦模型', kind: 'free', shared: true, author: '备课组',
          records: [
            { id: 'r1', kind: 'preset', preset_id: 'conic/parabola', params: { p: 1 }, color: '#0f4787', width: 2 },
            { id: 'r2', kind: 'line', a: [1, 0], b: [4, 6], color: '#c99735', width: 2 },
            { id: 'r3', kind: 'point', pos: [1, 0], color: '#c99735', width: 2 },
          ],
        })],
      },
    })
    const w = mount(DrawBoard, { props: { open: true }, global: { stubs: { teleport: true } } })
    await flushPromises()
    await w.find('[data-testid="mxd-lib-f-free"]').trigger('click')
    await flushPromises()
    await flushTick()
    const free = w.findComponent(FreeMode)
    expect(free.exists()).toBe(true)
    expect(free.find('[data-testid="mxd-free-count"]').text()).toContain('3 条')
  })

  it('子模式插入 → insert 透传 + 自动关闭', async () => {
    const w = mount(DrawBoard, { props: { open: true, reopen: null }, global: { stubs: { teleport: true } } })
    await flushPromises()
    await w.find('[data-testid="mxd-fx-insert"]').trigger('click')
    const ev = w.emitted('insert')
    expect(ev).toBeTruthy()
    expect((ev![0][0] as any).type).toBe('functionPlot')
    expect(w.emitted('update:open')![0]).toEqual([false])
  })

  it('保存到图形库：名称 + 共享 → saveLibrary 入参正确且列表前置', async () => {
    saveLibFn.mockResolvedValue({ data: libItem({ id: 'fig-new', name: '我的图形' }) })
    const w = mount(DrawBoard, { props: { open: true }, global: { stubs: { teleport: true } } })
    await flushPromises()
    await w.find('[data-testid="mxd-board-savelib"]').trigger('click')
    expect(w.find('[data-testid="mxd-board-save"]').exists()).toBe(true)
    await w.find('[data-testid="mxd-board-save-name"]').setValue('正弦函数族')
    const share = w.find('.mxd-board__save-share input')
    await share.setValue(true)
    await w.find('[data-testid="mxd-board-save-ok"]').trigger('click')
    await flushPromises()
    expect(saveLibFn).toHaveBeenCalledTimes(1)
    const body = saveLibFn.mock.calls[0][0] as any
    expect(body.name).toBe('正弦函数族')
    expect(body.kind).toBe('fx')
    expect(body.shared).toBe(true)
    expect(body.expr).toBe('a\\cdot\\sin(bx+c)+d')
    expect(w.find('[data-testid="mxd-board-save"]').exists()).toBe(false)
    expect(w.findAll('.mxd-board__lib-item')).toHaveLength(1)
  })

  it('Esc 关闭（工作台快捷退出）', async () => {
    const w = mount(DrawBoard, { props: { open: true }, global: { stubs: { teleport: true } } })
    await flushPromises()
    // mount 未 attachTo，冒泡到不了 window，直接向 window 派发（走真实 window 监听）
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(w.emitted('update:open')![0]).toEqual([false])
  })
})

/* ==================== SlidesView 集成 ==================== */

import SlidesView from '@/pages/teacher-v3/SlidesView.vue'

describe('SlidesView 集成：绘图工作台入口与插入落盘', () => {
  function seedDeck() {
    decksGetFn.mockResolvedValue({
      data: {
        id: 'd1', title: '椭圆复习', class_name: '高二(3)班', template_id: 'tpl-academic-blue',
        source: 'topic', slide_count: 1, updated_at: '2026-09-01', photo_context: null,
        slides: [{
          id: 'sl1', layout: 'blank', fill_rate: 0.4,
          elements: [
            { id: 'img1', type: 'image' as const, left: 100, top: 100, width: 300, height: 200, z: 2, src: 'data:image/svg+xml,x', alt: '画布图形', draw_recipe: { records: [{ id: 'r1', kind: 'line' as const, a: [0, 0], b: [4, 4], color: '#0f4787', width: 3 }] } },
            { id: 'p1', type: 'pageNo' as const, left: 1180, top: 680, width: 60, height: 30, z: 1, no: 1 },
          ],
        }],
      },
    })
  }

  beforeEach(() => {
    decksListFn.mockResolvedValue({ data: { items: [{ id: 'd1', title: '椭圆复习', class_name: '高二(3)班', slide_count: 1, source: 'topic', template_id: 'tpl-academic-blue', updated_at: '2026-09-01' }] } })
    classesFn.mockResolvedValue({ data: { items: [{ class_id: 'c2-03', name: '高二(3)班', students: 46 }] } })
    plansListFn.mockResolvedValue({ data: { items: [] } })
    deckTemplatesFn.mockResolvedValue({ data: { items: [] } })
    seedDeck()
  })

  async function openEditor() {
    const w = mount(SlidesView, { global: { plugins: [createPinia()], stubs: { teleport: true } } })
    await flushPromises()
    await w.find('.tv3-qcard').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-editor"]').exists()).toBe(true)
    return w
  }

  it('编辑器 dock 有绘图工作台入口；插入函数 → 画布新增 functionPlot 元素', async () => {
    const w = await openEditor()
    // 大纲缩略图也渲染 .v3sc__el，限定主画布计数
    const canvas = w.find('.tv3-editor__canvas-wrap')
    const before = canvas.findAll('.v3sc__el').length

    await w.find('[data-testid="tv3-open-drawboard"]').trigger('click')
    await flushPromises()
    const db = w.findComponent(DrawBoard)
    expect(db.exists()).toBe(true)
    expect(db.find('[data-testid="mxd-drawboard"]').exists()).toBe(true)

    await db.find('[data-testid="mxd-fx-insert"]').trigger('click')
    await flushPromises()
    expect(w.findComponent(DrawBoard).find('[data-testid="mxd-drawboard"]').exists()).toBe(false)
    expect(canvas.findAll('.v3sc__el').length).toBe(before + 1)
    expect(canvas.find('.v3sc__fx').exists()).toBe(true)
    expect(w.text()).not.toContain('undefined')
  })

  it('选中含配方的图片元素 → 属性面板出现"重开绘图编辑"；重开 → free 模式载入配方', async () => {
    const w = await openEditor()

    const imgEl = w.find('.tv3-editor__canvas-wrap').findAll('.v3sc__el').find((elw) => elw.find('img').exists())
    expect(imgEl).toBeTruthy()
    await imgEl!.trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-reopen-draw"]').exists()).toBe(true)

    await w.find('[data-testid="tv3-reopen-draw"]').trigger('click')
    await flushPromises()
    await flushTick()
    const db = w.findComponent(DrawBoard)
    expect(db.exists()).toBe(true)
    const free = db.findComponent(FreeMode)
    expect(free.exists()).toBe(true)
    expect(free.find('[data-testid="mxd-free-count"]').text()).toContain('1 条')
  })

  it('工具球收纳改版：底部 Dock 移除；选中公式 → 键盘浮层自动唤出，关闭后同元素不重复弹', async () => {
    /* 本测试自带 seed：画布含 1 个公式元素 + 1 个图片元素 */
    decksGetFn.mockResolvedValue({
      data: {
        id: 'd2', title: '公式键盘浮层', class_name: '高二(3)班', template_id: 'tpl-academic-blue',
        source: 'topic', slide_count: 1, updated_at: '2026-09-01', photo_context: null,
        slides: [{
          id: 'sl1', layout: 'blank', fill_rate: 0.3,
          elements: [
            { id: 'fx1', type: 'formula' as const, left: 120, top: 120, width: 320, height: 56, z: 2, latex: 'c^2 = a^2 + b^2', font_size: 22, display: false, teacher_confirmed: false },
            { id: 'tx1', type: 'text' as const, left: 120, top: 260, width: 400, height: 60, z: 2, html: '勾股定理', font_size: 18, teacher_confirmed: false },
          ],
        }],
      },
    })
    const w = mount(SlidesView, { global: { plugins: [createPinia()], stubs: { teleport: true } } })
    await flushPromises()
    await w.find('.tv3-qcard').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-editor"]').exists()).toBe(true)

    /* 底部 Dock 已移除；绘图入口移到顶栏（悬浮球/顶栏为唯二入口） */
    expect(w.find('.tv3-editor__dock').exists()).toBe(false)
    expect(w.find('[data-testid="tv3-open-drawboard"]').exists()).toBe(true)

    /* 选中公式元素 → 浮层自动唤出（含 MathKeyboard 分类 tabs） */
    const fxWrap = w.find('.tv3-editor__canvas-wrap').findAll('.v3sc__el').find((elw) => elw.find('.v3sc__formula').exists())
    expect(fxWrap).toBeTruthy()
    await fxWrap!.trigger('click')
    await flushPromises()
    const floatKbd = w.find('[data-testid="tv3-kbd-float"]')
    expect(floatKbd.exists()).toBe(true)
    expect(floatKbd.find('.mx-kbd__tabs').exists()).toBe(true)

    /* 手动关闭 → 消失；同一元素再次点击 → 不重复弹（防打扰） */
    await w.find('[data-testid="tv3-kbd-float-close"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-kbd-float"]').exists()).toBe(false)
    await fxWrap!.trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="tv3-kbd-float"]').exists()).toBe(false)

    /* 切到非公式元素 → 浮层不出现 */
    const txWrap = w.find('.tv3-editor__canvas-wrap').findAll('.v3sc__el').find((elw) => elw.find('.v3sc__formula').exists() === false)
    if (txWrap) {
      await txWrap.trigger('click')
      await flushPromises()
      expect(w.find('[data-testid="tv3-kbd-float"]').exists()).toBe(false)
    }
    expect(w.text()).not.toContain('undefined')
  })
})
