import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMock = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }))
vi.mock('@/api/client', () => ({ api: apiMock }))

import { ingestIdempotencyKey, pptStudioApi } from '@/features/ppt-studio-v2/api/pptStudioApi'

describe('PPT Studio V2 browser boundary', () => {
  beforeEach(() => vi.resetAllMocks())

  it('posts the complete spec to ingest with a stable idempotency key', async () => {
    apiMock.post.mockResolvedValue({ deck_id: 'deck-8200' })
    const spec = { id: 'spec-8100', revision: 7, metadata: { title: '函数' }, slides: [] }

    await pptStudioApi.ingestDeck(spec)

    expect(apiMock.post).toHaveBeenCalledWith(
      '/ppt-studio-v2/v1/decks/ingest',
      { spec },
      undefined,
      { idempotencyKey: ingestIdempotencyKey(spec) },
    )
    expect(ingestIdempotencyKey(spec)).toBe('ppt-studio-v2:ingest:spec-8100:7')
  })

  it('uses only the 8200 generation job contract and persisted deck id', async () => {
    apiMock.post.mockResolvedValue({ id: 'job-1', job_type: 'ppt.deck.generate', status: 'PENDING' })

    await pptStudioApi.submitDeckGeneration('deck-8200')

    expect(apiMock.post).toHaveBeenCalledWith(
      '/ppt-studio-v2/v1/jobs',
      { job_type: 'ppt.deck.generate', payload: { deck_id: 'deck-8200' } },
      undefined,
      { idempotencyKey: 'ppt-studio-v2:generate:deck-8200' },
    )
  })
})
