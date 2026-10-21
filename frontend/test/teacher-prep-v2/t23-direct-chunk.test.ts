import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DirectChunkLocator from '@/features/teacher-prep-v2/components/evidence/DirectChunkLocator.vue'

describe('T23 DirectChunkLocator', () => {
  it('renders only authorized direct-chunk provenance and never PDF/page fallbacks', () => {
    const wrapper = mount(DirectChunkLocator, {
      props: {
        locator: {
          source_id: 'textbook-1',
          section_path: ['函数', '二次函数'],
          chunk_id: 'chunk-9',
          chunk_ordinal: 9,
          content_hash: 'sha256:abc',
          page: 12,
          page_refs: ['p12'],
        },
      },
    })
    expect(wrapper.text()).toContain('textbook-1')
    expect(wrapper.text()).toContain('chunk-9')
    expect(wrapper.text()).toContain('sha256:abc')
    expect(wrapper.text()).not.toMatch(/page|p12|第\s*12\s*页/i)
  })

  it('stays empty when the server locator lacks an authorized identity field', () => {
    const wrapper = mount(DirectChunkLocator, { props: { locator: { page: 12, page_refs: ['p12'] } } })
    expect(wrapper.find('[data-testid="t23-direct-chunk-locator"]').exists()).toBe(false)
  })
})
