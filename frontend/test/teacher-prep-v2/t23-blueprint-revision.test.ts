import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import BlueprintRevisionCard from '@/features/teacher-prep-v2/components/cards/BlueprintRevisionCard.vue'

describe('T23 BlueprintRevisionCard', () => {
  it('shows before/after lineage, evidence and a recoverable conflict state', async () => {
    const wrapper = mount(BlueprintRevisionCard, {
      props: {
        revision: {
          target: 'lesson_flow', before: '图像观察', after: '图像观察→表达式归纳',
          reason: '补足从观察到表达的过渡。', confidence: 0.86,
          limitations: ['尚未覆盖分层练习'], evidence_links: ['chunk-9'], expected_revision: 8,
        },
      },
    })
    expect(wrapper.find('[data-testid="t23-blueprint-revision"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('图像观察→表达式归纳')
    expect(wrapper.text()).toContain('chunk-9')
    expect(wrapper.find('[data-testid="t23-revision-conflict"]').exists()).toBe(false)
  })
})
