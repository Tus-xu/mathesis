import { describe, expect, it } from 'vitest'
import { allowedActions } from '@/utils/artifactState'

describe('artifact state machine', () => {
  it('draft allows edit/regenerate/confirm/archive', () => {
    expect(allowedActions('draft')).toEqual(['edit', 'regenerate', 'confirm', 'archive'])
  })
  it('confirmed allows derive/publish/archive (no silent publish before confirm)', () => {
    expect(allowedActions('confirmed')).toEqual(['derive', 'publish', 'archive'])
    expect(allowedActions('confirmed')).not.toContain('confirm')
  })
  it('published allows view/copy_draft/archive', () => {
    expect(allowedActions('published')).toEqual(['view', 'copy_draft', 'archive'])
  })
  it('archived allows view/copy_draft only', () => {
    expect(allowedActions('archived')).toEqual(['view', 'copy_draft'])
  })
})