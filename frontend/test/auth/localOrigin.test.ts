import { describe, expect, it } from 'vitest'

import { canonicalLocalOrigin } from '../../src/utils/localOrigin'

describe('canonicalLocalOrigin', () => {
  it('redirects localhost requests to the single local auth origin', () => {
    expect(canonicalLocalOrigin('/login?redirect=%2Fdialog', 'localhost:5176', 5176))
      .toBe('http://127.0.0.1:5176/login?redirect=%2Fdialog')
  })

  it('leaves the canonical origin and non-local hosts unchanged', () => {
    expect(canonicalLocalOrigin('/api/auth/me', '127.0.0.1:5176', 5176)).toBe(null)
    expect(canonicalLocalOrigin('/api/auth/me', 'example.test', 5176)).toBe(null)
  })

  it('preserves an explicitly configured development port', () => {
    expect(canonicalLocalOrigin('/login', 'localhost:5186', 5176))
      .toBe('http://127.0.0.1:5186/login')
  })
})
