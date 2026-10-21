/**
 * 教材选择卡：选项多时的「搜索 + 按版本分组」。
 *
 * 背景（见 frontend/.impeccable.md）：
 *   目标用户 = 一线高中数学教师；第一优先场景 = 快速出片。
 *   实测教材目录可达 22+ 项且平铺无分组，教师需要在无意义的长列表里逐项扫读。
 *
 * 设计约束：
 *   - 选项少（≤ 8）时**不加控件**，保持原平铺（少即是多）
 *   - 选项多时才出现搜索框，并按 label 首段（版本）分组
 *   - 不露任何内部标识符；组件内不写死色值（走 --ma-* token）
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import StructuredCard from '@/features/teacher-prep-v2/components/cards/StructuredCard.vue'

const HUMAN = [
  ['tb-1', '人教A版2019 · 必修第一册 · 教师用书 · 必修第一册'],
  ['tb-2', '人教A版2019 · 选择性必修第一册 · 教师用书 · 选择性必修第一册'],
  ['tb-3', '人教A版2019 · 选择性必修第二册 · 教师用书 · 选择性必修第二册'],
  ['tb-4', '北师大版 · 必修第一册 · 高一 · 必修第一册'],
  ['tb-5', '北师大版 · 选择性必修第一册 · 高二 · 选择性必修第一册'],
  ['tb-6', '人教A版2019 · 必修第二册 · 高一 · 必修第二册'],
  ['tb-7', '人教A版2019 · 选择性必修第三册 · 高二 · 选择性必修第三册'],
  ['tb-8', '人教A版2019 · 数学人教版 · 通用 · 数学人教版'],
  ['tb-9', '北师大版 · 必修第二册 · 高一 · 必修第二册'],
  ['tb-10', '北师大版 · 选择性必修第二册 · 高二 · 选择性必修第二册'],
].map(([id, label]) => ({ id, label }))

function mountCard(options: Array<{ id: string; label: string }>) {
  return mount(StructuredCard, {
    props: {
      componentType: 'selection_card',
      props: { kind: 'textbook', options },
      requiresConfirmation: false,
    },
  })
}

describe('选择卡 · 少量选项保持平铺', () => {
  it('≤ 8 项时不渲染搜索框，且选项全部平铺可点', () => {
    const few = HUMAN.slice(0, 8)
    const wrapper = mountCard(few)
    expect(wrapper.find('[data-testid="prep-selection-search"]').exists()).toBe(false)
    for (const option of few) {
      expect(wrapper.find(`[data-testid="prep-selection-option-${option.id}"]`).exists()).toBe(true)
    }
  })

  it('点击平铺项 emit select，payload 是 textbook_id', async () => {
    const wrapper = mountCard(HUMAN.slice(0, 3))
    await wrapper.find('[data-testid="prep-selection-option-tb-2"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([{ textbook_id: 'tb-2' }])
  })
})

describe('选择卡 · 选项多时搜索 + 分组', () => {
  it('> 8 项时出现搜索框，且 10 个选项仍全部可达', () => {
    const wrapper = mountCard(HUMAN)
    expect(wrapper.find('[data-testid="prep-selection-search"]').exists()).toBe(true)
    for (const option of HUMAN) {
      expect(wrapper.find(`[data-testid="prep-selection-option-${option.id}"]`).exists()).toBe(true)
    }
  })

  it('按版本分组：人教A版2019 与 北师大版 各自成组，组标题可见', () => {
    const wrapper = mountCard(HUMAN)
    const titles = wrapper.findAll('.structured__selection-group-title').map((n) => n.text())
    expect(titles).toContain('人教A版2019')
    expect(titles).toContain('北师大版')
    // 版本是教师可见的教材名，不是内部 id
    expect(wrapper.text()).not.toContain('tb-')
  })

  it('同一版本不相邻时也不会被拆成多组（Map 累计而非只比相邻项）', () => {
    const wrapper = mountCard(HUMAN)
    const titles = wrapper.findAll('.structured__selection-group-title').map((n) => n.text())
    expect(titles.filter((t) => t === '人教A版2019')).toHaveLength(1)
    expect(titles.filter((t) => t === '北师大版')).toHaveLength(1)
  })

  it('输入关键词只保留匹配项', async () => {
    const wrapper = mountCard(HUMAN)
    await wrapper.find('[data-testid="prep-selection-search"]').setValue('北师大')
    expect(wrapper.find('[data-testid="prep-selection-option-tb-4"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prep-selection-option-tb-1"]').exists()).toBe(false)
    const titles = wrapper.findAll('.structured__selection-group-title').map((n) => n.text())
    expect(titles).toEqual(['北师大版'])
  })

  it('关键词命中跨版本的册次（如「选择性必修」）时两组都在', async () => {
    const wrapper = mountCard(HUMAN)
    await wrapper.find('[data-testid="prep-selection-search"]').setValue('选择性必修')
    const titles = wrapper.findAll('.structured__selection-group-title').map((n) => n.text())
    expect(titles).toEqual(['人教A版2019', '北师大版'])
  })

  it('无匹配时给出人话提示，且不显示任何选项', async () => {
    const wrapper = mountCard(HUMAN)
    await wrapper.find('[data-testid="prep-selection-search"]').setValue('不存在的教材')
    expect(wrapper.findAll('.structured__selection-option')).toHaveLength(0)
    expect(wrapper.text()).toContain('没找到匹配的教材')
  })

  it('分组内点击仍 emit 正确 payload', async () => {
    const wrapper = mountCard(HUMAN)
    await wrapper.find('[data-testid="prep-selection-option-tb-5"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([{ textbook_id: 'tb-5' }])
  })

  it('搜索框有可访问名称（label 关联）', () => {
    const wrapper = mountCard(HUMAN)
    const input = wrapper.find('[data-testid="prep-selection-search"]')
    const id = input.attributes('id')
    expect(id).toBeTruthy()
    expect(wrapper.find(`label[for="${id}"]`).exists()).toBe(true)
  })
})

describe('选择卡 · 设计约束回归', () => {
  it('样式块内不写死色值（全部走 --ma-* token）', () => {
    const raw = readFileSync(
      'src/features/teacher-prep-v2/components/cards/StructuredCard.vue',
      'utf8',
    )
    const styleBlock = raw.slice(raw.indexOf('<style'))
    expect(styleBlock.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([])
  })
})
