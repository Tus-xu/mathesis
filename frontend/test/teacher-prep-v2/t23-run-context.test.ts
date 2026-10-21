import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RunContextDisclosure from '@/features/teacher-prep-v2/components/cards/RunContextDisclosure.vue'

describe('T23 RunContextDisclosure', () => {
  it('discloses provider-supplied lineage only and never hidden prompt or secret fields', async () => {
    const wrapper = mount(RunContextDisclosure, {
      props: {
        context: {
          prompt_ref: { id: 'prep-review', version: 'v2', hash: 'sha256:prompt' },
          context_manifest: 'ctx-1', evidence_refs: ['chunk-9'], model: 'model-label',
          tools: ['retrieval'], cost: { unit: 'credits', value: 2 }, trace_id: 'trace-1',
          secret: 'must-not-leak', hidden_prompt: 'must-not-leak',
        },
      },
    })
    await wrapper.find('summary').trigger('click')
    expect(wrapper.text()).toContain('prep-review')
    expect(wrapper.text()).toContain('trace-1')
    expect(wrapper.text()).not.toContain('must-not-leak')
  })
})
