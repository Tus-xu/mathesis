import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ClassLearningProfileCard from '@/features/teacher-prep-v2/components/cards/ClassLearningProfileCard.vue'

describe('T23 ClassLearningProfileCard', () => {
  it('shows aggregate evidence and honest coverage limits without student identity or answers', () => {
    const wrapper = mount(ClassLearningProfileCard, {
      props: {
        profile: {
          sample_size: 18, window: '最近三次作业', coverage: 0.72, confidence: 'medium',
          misconceptions: ['把顶点式与一般式混用'], difficulty: 'medium',
          unknown: ['未覆盖口头解释'], warnings: ['样本量有限'],
          student_names: ['不应显示'], raw_answers: ['不应显示'],
        },
      },
    })
    expect(wrapper.find('[data-testid="t23-learning-profile"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('18')
    expect(wrapper.text()).toContain('72%')
    expect(wrapper.text()).toContain('未覆盖口头解释')
    expect(wrapper.text()).not.toContain('不应显示')
  })
})
