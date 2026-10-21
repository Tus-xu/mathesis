import { describe, it, expect } from 'vitest'
import { createIdempotencyTracker, newIdempotencyKey } from '@/api/idempotency'

describe('idempotency key tracking', () => {
  it('reuses the same key for repeated writes to the same action', () => {
    const tracker = createIdempotencyTracker()
    const first = tracker.keyFor('confirm:item-1')
    const second = tracker.keyFor('confirm:item-1')
    expect(second).toBe(first)
  })
  it('produces distinct keys across different actions', () => {
    const tracker = createIdempotencyTracker()
    expect(tracker.keyFor('a')).not.toBe(tracker.keyFor('b'))
  })
  it('generates a non-empty idempotency key', () => {
    expect(newIdempotencyKey().length).toBeGreaterThan(0)
  })
  it('clears a memoized key on demand', () => {
    const tracker = createIdempotencyTracker()
    const first = tracker.keyFor('publish')
    tracker.clear('publish')
    const after = tracker.keyFor('publish')
    expect(after).not.toBe(first)
  })
})