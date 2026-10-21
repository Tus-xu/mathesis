// P2 拍照插入面板：扫描增强 → 三选一（图片素材 / 公式识别 / 手写原样）
// 红线 R2：识别结果必须先进 MathField 审查，不能直接落稿。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mocks = vi.hoisted(() => {
  const enhanceMock = vi.fn(async (..._a: unknown[]) => 'data:image/png;base64,ENHANCED')
  const photoToFormula = vi.fn(async () => ({ data: { latex: 'x^{2}+y^{2}=r^{2}', confidence: 0.9 } }))
  return { enhanceMock, photoToFormula }
})

vi.mock('@/components/mathx/scanEnhance', () => ({
  SCAN_DEFAULTS: { brightness: 6, contrast: 30, whiten: true, strength: 0.55 },
  enhanceSrcToDataUrl: (...args: unknown[]) => mocks.enhanceMock(...args),
}))

// 本组件只用到 v3Api.recognition.photoToFormula
vi.mock('@/api/teacherV3', () => ({
  v3Api: { recognition: { photoToFormula: mocks.photoToFormula } },
}))

vi.mock('@/components/mathx/MathField.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'MathField',
      props: { modelValue: { type: String, default: '' }, testid: String },
      emits: ['update:modelValue'],
      setup(props: any) {
        return () => h('div', { class: 'mf-stub', 'data-testid': props.testid }, String(props.modelValue ?? ''))
      },
    }),
  }
})

import PhotoInsertPanel from '@/components/mathx/PhotoInsertPanel.vue'

function dropPhoto(w: Awaited<ReturnType<typeof mount>>) {
  const input = w.find('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: [new File(['demo'], 'p.png', { type: 'image/png' })], configurable: true })
  return input.trigger('change')
}

/** FileReader.onload 是异步事件，flushPromises 等不到；用真实定时器轮询直到条件成立 */
async function until(fn: () => boolean, tries = 30) {
  for (let i = 0; i < tries; i++) {
    if (fn()) return
    await new Promise((r) => setTimeout(r, 5))
  }
  throw new Error('condition not met')
}
const shown = (w: Awaited<ReturnType<typeof mount>>) => w.find('[data-testid="mxd-photo-choice"]').exists()

beforeEach(() => {
  mocks.enhanceMock.mockReset()
  mocks.enhanceMock.mockResolvedValue('data:image/png;base64,ENHANCED')
  mocks.photoToFormula.mockClear()
})

describe('PhotoInsertPanel', () => {
  it('无照片时只显示上传区，无三选', () => {
    const w = mount(PhotoInsertPanel, { props: { open: true } })
    expect(w.find('[data-testid="mxd-photo-choice"]').exists()).toBe(false)
  })

  it('上传照片后三选出现；点「图片素材」→ 增强图作为 image 插入并关闭', async () => {
    const w = mount(PhotoInsertPanel, { props: { open: true } })
    await dropPhoto(w)
    await until(() => shown(w))
    // 等扫描增强防抖应用完成（160ms）previewSrc 更新为增强图
    await new Promise((r) => setTimeout(r, 200))
    await w.find('[data-testid="mxd-photo-choice"]').trigger('click')
    const arg = w.emitted('insert')![0][0] as { kind: string; src: string }
    expect(arg.kind).toBe('image')
    // 图片素材用增强后的图源
    expect(arg.src).toBe('data:image/png;base64,ENHANCED')
    expect(w.emitted('update:open')![0]).toEqual([false])
  })

  it('点「手写原样」→ 原图作为 anchorPhoto 插入（不改图）', async () => {
    const w = mount(PhotoInsertPanel, { props: { open: true } })
    await dropPhoto(w)
    await until(() => shown(w))
    await w.find('[data-testid="mxd-photo-original"]').trigger('click')
    const arg = w.emitted('insert')![0][0] as { kind: string; src: string }
    expect(arg.kind).toBe('anchorPhoto')
    // 原样必须用原始上传图，而非增强图
    expect(arg.src).not.toBe('data:image/png;base64,ENHANCED')
    expect(arg.src.startsWith('data:')).toBe(true)
  })

  it('「识别为公式」→ 调识别接口 → 进审查 → 确认后才插入（红线 R2）', async () => {
    const w = mount(PhotoInsertPanel, { props: { open: true } })
    await dropPhoto(w)
    await until(() => shown(w))
    expect(w.emitted('insert')).toBeUndefined() // 尚未落稿

    await w.find('[data-testid="mxd-photo-formula"]').trigger('click')
    await until(() => w.find('[data-testid="mxd-photo-insert-formula"]').exists())
    expect(mocks.photoToFormula).toHaveBeenCalledTimes(1)
    // 识别后先进可编辑审查区（MathField 承 latex），仍未落稿
    expect(w.find('[data-testid="mxd-photo-formula"]').exists()).toBe(true)
    expect(w.emitted('insert')).toBeUndefined()
    await w.find('[data-testid="mxd-photo-insert-formula"]').trigger('click')
    const arg = w.emitted('insert')![0][0] as { kind: string; latex: string }
    expect(arg.kind).toBe('formula')
    expect(arg.latex).toContain('x^{2}')
  })
})