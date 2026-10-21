import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import StructuredCard from '@/features/teacher-prep-v2/components/cards/StructuredCard.vue'

const fixtures = [
  ['direct_chunk_locator', { source_id: 'source-1', section_path: ['函数'], chunk_id: 'chunk-1', chunk_ordinal: 1, content_hash: 'sha256:x' }, 't23-direct-chunk-locator'],
  ['diagnostic_proposal', { diagnostic_id: 'diag-1', status: 'proposal', revision: 3, title: '诊断', available_actions: ['confirm'] }, 't23-diagnostic-proposal'],
  ['class_learning_profile', { sample_size: 12, coverage: 0.5, confidence: 0.7, difficulty: 'medium' }, 't23-learning-profile'],
  ['blueprint_revision', { target: 'lesson_flow', before: 'A', after: 'B', reason: 'C', evidence_links: ['chunk-1'] }, 't23-blueprint-revision'],
  ['run_context_disclosure', { prompt_ref: { id: 'p', version: '1' }, model: 'm' }, 't23-run-context'],
] as const

describe('T23 StructuredCard production renderer selection', () => {
  it.each(fixtures)('renders %s through StructuredCard', (componentType, props, testId) => {
    const wrapper = mount(StructuredCard, {
      props: { componentType, props, requiresConfirmation: false },
    })
    expect(wrapper.find(`[data-testid="${testId}"]`).exists()).toBe(true)
  })

  it('keeps stable field and enum labels in the copy catalog', () => {
    const wrapper = mount(StructuredCard, {
      props: {
        componentType: 'context_card',
        props: { status: 'confirmed', revision: 4, coverage: 0.75 },
        requiresConfirmation: false,
      },
    })
    expect(wrapper.text()).toContain('状态')
    expect(wrapper.text()).toContain('已确认')
    expect(wrapper.text()).toContain('75%')

    const source = readFileSync('src/features/teacher-prep-v2/components/cards/StructuredCard.vue', 'utf8')
    expect(source).not.toContain('FIELD_LABELS')
    expect(source).not.toContain('ENUM_LABELS')
    expect(source).not.toContain("status: '状态'")
  })

  it('renders the server context card instead of the unknown-content fallback', () => {
    const wrapper = mount(StructuredCard, {
      props: {
        componentType: 'context_card',
        props: {
          source_title: '人教A版2019 · 选择性必修第一册',
          section_title: '3.1.2 椭圆的简单几何性质',
          snapshot_hash: 'abc123',
          warnings: ['no_units'],
        },
        requiresConfirmation: false,
      },
    })
    expect(wrapper.text()).toContain('人教A版2019 · 选择性必修第一册')
    expect(wrapper.text()).toContain('3.1.2 椭圆的简单几何性质')
    expect(wrapper.text()).not.toContain('暂不支持的内容类型')
  })
})
