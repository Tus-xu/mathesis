import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DiagnosticProposalCard from '@/features/teacher-prep-v2/components/cards/DiagnosticProposalCard.vue'

describe('T23 DiagnosticProposalCard', () => {
  it('renders pending review and emits an expected-revision action without claiming success', async () => {
    const wrapper = mount(DiagnosticProposalCard, {
      props: {
        proposal: {
          diagnostic_id: 'diag-1', status: 'proposal', revision: 7,
          title: '学前诊断', rationale: '确认顶点式先备理解。',
          questions: [{ prompt: '请选择图像变化', evidence_refs: ['chunk-9'] }],
          available_actions: ['confirm', 'edit', 'reject', 'skip'],
        },
      },
    })
    expect(wrapper.find('[data-testid="t23-diagnostic-pending"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('第 7 版')
    expect(wrapper.text()).not.toContain('已完成')
    await wrapper.find('[data-testid="t23-diagnostic-confirm"]').trigger('click')
    expect(wrapper.emitted('review')?.[0]?.[0]).toEqual({
      action: 'confirm', target: 'diag-1', expected_revision: 7,
    })
  })
})
