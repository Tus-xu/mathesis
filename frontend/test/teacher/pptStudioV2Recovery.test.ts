import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockPptApi = vi.hoisted(() => ({
  ingestDeck: vi.fn(),
  getDeck: vi.fn(),
  listArtifacts: vi.fn(),
  getJob: vi.fn(),
  getJobPages: vi.fn(),
  getJobEvents: vi.fn(),
  submitDeckGeneration: vi.fn(),
}))

vi.mock('@/features/ppt-studio-v2/api/pptStudioApi', () => ({ pptStudioApi: mockPptApi }))

import { restoreTeachingDeck } from '@/features/ppt-studio-v2/recovery/restoreTeachingDeck'

describe('TeachingDeckSpec → 8200 recovery boundary', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mockPptApi.ingestDeck.mockResolvedValue({ deck_id: '8200-deck-1' })
    mockPptApi.getDeck.mockResolvedValue({ deck_id: '8200-deck-1', title: '课件', page_count: 1 })
    mockPptApi.listArtifacts.mockResolvedValue([])
    mockPptApi.getJob.mockResolvedValue({ id: 'job-1', status: 'COMPLETED' })
    mockPptApi.getJobPages.mockResolvedValue([])
    mockPptApi.getJobEvents.mockResolvedValue([])
  })

  it('ingests the complete spec before using the returned persisted deck id', async () => {
    const spec = { id: 'spec-id-not-a-deck-id', revision: 4, metadata: { title: '函数' }, slides: [] }
    const order: string[] = []
    mockPptApi.ingestDeck.mockImplementation(async (value: unknown) => {
      order.push('ingest')
      expect(value).toEqual(spec)
      return { deck_id: 'persisted-deck-id' }
    })
    mockPptApi.getDeck.mockImplementation(async (id: string) => {
      order.push(`deck:${id}`)
      return { deck_id: id, title: '函数', page_count: 0 }
    })
    mockPptApi.listArtifacts.mockImplementation(async (id: string) => {
      order.push(`artifacts:${id}`)
      return []
    })

    const result = await restoreTeachingDeck(spec)

    expect(order.slice(0, 3)).toEqual(['ingest', 'deck:persisted-deck-id', 'artifacts:persisted-deck-id'])
    expect(result.deck?.deck_id).toBe('persisted-deck-id')
    expect(result.recovery).toContain('deck/artifacts')
  })

  it('keeps spec recovery viable when 8200 deck GET is 404', async () => {
    mockPptApi.getDeck.mockRejectedValue(new Error('404'))
    mockPptApi.listArtifacts.mockResolvedValue([{ id: 'a-1', kind: 'png', object_key: 'x', sha256: 'h', size_bytes: 1, page_no: 1, job_id: 'job-1' }])

    const result = await restoreTeachingDeck({ id: 'spec-1', revision: 1, metadata: { title: '函数' } })

    expect(result.deck).toBeNull()
    expect(result.artifacts).toHaveLength(1)
    expect(result.job?.id).toBe('job-1')
    expect(mockPptApi.getJobPages).toHaveBeenCalledWith('job-1')
    expect(mockPptApi.getJobEvents).toHaveBeenCalledWith('job-1')
    expect(result.recovery).toContain('作业状态')
  })

  it('does not submit a generation job as part of opening a spec', async () => {
    await restoreTeachingDeck({ id: 'spec-2', revision: 1, metadata: { title: '函数' } })
    expect(mockPptApi.submitDeckGeneration).not.toHaveBeenCalled()
  })
})
