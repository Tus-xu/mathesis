/**
 * B1 · FormulaTextEditor：分词双态编辑器
 * - 展示态不暴露 $..$；公式以 KaTeX 渲染
 * - insertAtCaret 落在光标处（非段尾）；undo 可恢复
 * - 存储契约不变：仍是「文本 + $..$」字符串
 */
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'

// MathField 内部创建 math-field web component，jsdom 下 mock 其生命周期
vi.mock('@/components/mathx/MathField.vue', () => ({
  default: {
    name: 'MathField',
    props: ['modelValue', 'fontSize', 'testid'],
    emits: ['update:modelValue'],
    template: '<input class="mf-mock" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

import FormulaTextEditor from '@/components/mathx/FormulaTextEditor.vue'

async function typeAt(wrapper: VueWrapper, offset: number) {
  // 进入文本编辑态并设置光标
  await wrapper.find('[data-testid="fte-text-0"]').trigger('click')
  await flushPromises()
  const ta = wrapper.find('textarea')
  const el = ta.element as HTMLTextAreaElement
  el.setSelectionRange(offset, offset)
  await ta.trigger('keyup')
}

describe('FormulaTextEditor · 光标插入与撤销', () => {
  it('插入落在光标处（非段尾），且渲染态不暴露 $..$', async () => {
    const w = mount(FormulaTextEditor, { props: { modelValue: '已知抛物线 y 的焦点为 F，求准线。', placeholder: 'x', testid: 'fte' } })
    expect(w.text()).not.toContain('$') // 展示态无 $ 符号
    await typeAt(w, 4) // 光标放在「抛物线」之后
    ;(w.vm as unknown as { insertAtCaret: (l: string) => void }).insertAtCaret('y^{2}=2px')
    await flushPromises()
    const emitted = w.emitted('update:modelValue')!
    const v = emitted[emitted.length - 1][0] as string
    expect(v).toBe('已知抛物$y^{2}=2px$线 y 的焦点为 F，求准线。')
    // 渲染态继续不暴露 $..$
    expect(w.text()).not.toContain('$')
  })

  it('无光标记录时追加末尾；undo 恢复插入前内容', async () => {
    const onUpdate = vi.fn()
    const w = mount(FormulaTextEditor, { props: { modelValue: '结论：成立。', placeholder: 'x', testid: 'fte', 'onUpdate:modelValue': onUpdate } })
    ;(w.vm as unknown as { insertAtCaret: (l: string) => void }).insertAtCaret('a>b')
    await flushPromises()
    let v = onUpdate.mock.calls[0][0] as string
    expect(v.endsWith('$a>b$')).toBe(true)
    // undo 恢复
    await w.find('[data-testid="fte-undo"]').trigger('click')
    await flushPromises()
    v = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0] as string
    expect(v).toBe('结论：成立。')
    expect(w.find('[data-testid="fte-undo"]').exists()).toBe(false)
  })

  it('解析既有 $..$ 内容为分词：公式段可见、文本段可编辑', async () => {
    const w = mount(FormulaTextEditor, { props: { modelValue: '定义 $|MF_1|+|MF_2|=2a$ 是椭圆。', placeholder: 'x', testid: 'fte' } })
    expect(w.find('[data-testid="fte-formula-1"]').exists()).toBe(true)
    expect(w.text()).toContain('是椭圆。')
    expect(w.text()).not.toContain('$|MF_1')
  })

  it('文本段编辑提交后回写（保留相邻公式段）', async () => {
    const onUpdate = vi.fn()
    const w = mount(FormulaTextEditor, { props: { modelValue: '前文 $e=c/a$ 后文', placeholder: 'x', testid: 'fte', 'onUpdate:modelValue': onUpdate } })
    await w.find('[data-testid="fte-text-0"]').trigger('click')
    await flushPromises()
    await w.find('textarea').setValue('前文改写 ')
    await w.find('textarea').trigger('blur')
    await flushPromises()
    const v = onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0] as string
    expect(v).toBe('前文改写 $e=c/a$ 后文')
  })
})
