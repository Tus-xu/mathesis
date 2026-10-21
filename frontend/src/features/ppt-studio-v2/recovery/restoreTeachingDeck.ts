import {
  pptStudioApi,
  type PptStudioArtifact,
  type PptStudioDeckView,
  type PptStudioJob,
  type PptStudioPage,
} from '@/features/ppt-studio-v2/api/pptStudioApi'

export interface TeachingDeckRecovery {
  deck: PptStudioDeckView | null
  artifacts: PptStudioArtifact[]
  job: PptStudioJob | null
  pages: PptStudioPage[]
  events: Record<string, unknown>[]
  recovery: string
}

const emptyRecovery = (recovery: string): TeachingDeckRecovery => ({
  deck: null,
  artifacts: [],
  job: null,
  pages: [],
  events: [],
  recovery,
})

/**
 * Restore the independent 8200 view for a 8100 TeachingDeckSpec.
 *
 * The spec id is not a 8200 deck id. Ingest is therefore always the first
 * 8200 operation and returns the only id allowed for subsequent reads. Every
 * read is best-effort: a missing deck/artifact/job must not hide the
 * authoritative 8100 spec that the caller can still render.
 */
export async function restoreTeachingDeck(spec: Record<string, unknown>): Promise<TeachingDeckRecovery> {
  let ingested: Awaited<ReturnType<typeof pptStudioApi.ingestDeck>>
  try {
    // Pass the complete server spec; the API client supplies a stable key.
    ingested = await pptStudioApi.ingestDeck(spec)
  } catch {
    return emptyRecovery('TeachingDeckSpec 已恢复；8200 尚未提供可恢复的 deck/artifact 状态')
  }

  const deckId = ingested.deck_id
  const [deckResult, artifactsResult] = await Promise.allSettled([
    pptStudioApi.getDeck(deckId),
    pptStudioApi.listArtifacts(deckId),
  ])
  const deck = deckResult.status === 'fulfilled' ? deckResult.value : null
  const artifacts = artifactsResult.status === 'fulfilled' ? artifactsResult.value : []
  const jobId = typeof spec.job_id === 'string' && spec.job_id
    ? spec.job_id
    : artifacts.find((item) => typeof item.job_id === 'string' && item.job_id)?.job_id || ''

  let job: PptStudioJob | null = null
  let pages: PptStudioPage[] = []
  let events: Record<string, unknown>[] = []
  if (jobId) {
    const [jobResult, pagesResult, eventsResult] = await Promise.allSettled([
      pptStudioApi.getJob(jobId),
      pptStudioApi.getJobPages(jobId),
      pptStudioApi.getJobEvents(jobId),
    ])
    job = jobResult.status === 'fulfilled' ? jobResult.value : null
    pages = pagesResult.status === 'fulfilled' ? pagesResult.value : []
    events = eventsResult.status === 'fulfilled' ? eventsResult.value : []
  }

  const readCount = Number(Boolean(deck)) + Number(artifactsResult.status === 'fulfilled') + Number(Boolean(job))
  if (job) return { deck, artifacts, job, pages, events, recovery: `8200 作业状态：${job.status}（已载入恢复信息）` }
  if (readCount > 0) return { deck, artifacts, job, pages, events, recovery: '8200 deck/artifacts 已恢复' }
  return { deck, artifacts, job, pages, events, recovery: 'TeachingDeckSpec 已恢复；8200 尚未提供可恢复的 deck/artifact 状态' }
}
