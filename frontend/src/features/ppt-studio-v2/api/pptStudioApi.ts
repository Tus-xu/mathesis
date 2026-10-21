/**
 * Browser boundary for the independent 8200 PPT Studio V2 service.
 *
 * This client deliberately exposes only the published read/recovery surface.
 * VNext editors are read-only until 8200 publishes a browser patch/export
 * contract; legacy `v3Api.generation.*` is never used by this module.
 */
import { api } from '@/api/client'

const BASE = '/ppt-studio-v2/v1'

export interface PptStudioDeckView {
  deck_id: string
  spec_sha256: string
  schema_version: string
  revision: number
  title: string
  page_count: number
  projection: Record<string, unknown> | null
  created_at: string | null
}

export interface PptStudioArtifact {
  id: string
  kind: string
  object_key: string
  sha256: string
  size_bytes: number
  page_no: number | null
  job_id: string | null
  artifact_meta: Record<string, unknown> | null
  created_at: string | null
}

export interface PptStudioJob {
  id: string
  job_type: string
  status: string
  attempt?: number
  max_attempts?: number
  last_error?: string | null
  result?: Record<string, unknown> | null
}

export interface PptStudioSubmitJob {
  id: string
  job_type: string
  status: string
  dedupe_key?: string | null
}

export interface PptStudioIngestResult {
  deck_id: string
  spec_sha256: string
  deduped: boolean
  page_count: number
  projection: Record<string, unknown>
}

export interface PptStudioPage {
  page_no: number
  slide_id: string
  status: string
  content_sha256: string | null
  fragment_ref: string | null
  png_ref: string | null
  cache_hit: boolean
  elapsed_ms: number | null
  qa: Record<string, unknown> | null
  last_error: string | null
}

/**
 * deck 作用域的逐页渲染清单（2026-09-14 新增）。
 *
 * 与 `PptStudioArtifact` 的区别：artifact 的 `object_key` 是全局内容寻址的
 * （同内容只登记一次、行归属首次渲染它的 deck），所以 `/decks/{id}/artifacts`
 * 拿不到本 deck 的逐页产物；本结构与后端 `GET /decks/{id}/pages` 对齐，
 * 是**内核渲染页图**的权威清单（`png_available` 为真才代表字节可取）。
 */
export interface PptStudioDeckPage {
  page_no: number
  slide_id: string | null
  status: string
  png_ref: string | null
  fragment_ref: string | null
  png_available: boolean
  png_content_url: string | null
  last_error: string | null
}

export const pptStudioApi = {
  ingestDeck(
    spec: Record<string, unknown>,
    assetsInline?: Record<string, string>,
    idempotencyKey = ingestIdempotencyKey(spec),
  ) {
    const body: Record<string, unknown> = { spec }
    if (assetsInline && Object.keys(assetsInline).length) body.assets_inline = assetsInline
    return api.post(`${BASE}/decks/ingest`, body, undefined, { idempotencyKey }) as Promise<PptStudioIngestResult>
  },
  /**
   * Submit only an authorized 8200 generation action. Opening a VNext spec
   * never calls this method implicitly; the caller must have a server action
   * allowing generation and must pass the persisted deck_id returned by ingest.
   */
  submitDeckGeneration(deckId: string, idempotencyKey = `ppt-studio-v2:generate:${deckId}`) {
    return api.post(`${BASE}/jobs`, {
      job_type: 'ppt.deck.generate',
      payload: { deck_id: deckId },
    }, undefined, { idempotencyKey }) as Promise<PptStudioSubmitJob>
  },
  getDeck(deckId: string) {
    return api.get(`${BASE}/decks/${encodeURIComponent(deckId)}`) as Promise<PptStudioDeckView>
  },
  listArtifacts(deckId: string) {
    return api.get(`${BASE}/decks/${encodeURIComponent(deckId)}/artifacts`) as Promise<PptStudioArtifact[]>
  },
  getJob(jobId: string) {
    return api.get(`${BASE}/jobs/${encodeURIComponent(jobId)}`) as Promise<PptStudioJob>
  },
  getJobPages(jobId: string) {
    return api.get(`${BASE}/jobs/${encodeURIComponent(jobId)}/pages`) as Promise<PptStudioPage[]>
  },
  getJobEvents(jobId: string) {
    return api.get(`${BASE}/jobs/${encodeURIComponent(jobId)}/events`) as Promise<Record<string, unknown>[]>
  },
  /** deck 逐页渲染清单（内核 PowerPoint COM 逐页导出；含字节是否已归档的真实状态）。 */
  listDeckPages(deckId: string) {
    return api.get(`${BASE}/decks/${encodeURIComponent(deckId)}/pages`) as Promise<PptStudioDeckPage[]>
  },
  /**
   * 取单页渲染图字节。走 `api.download` 是为了带 Authorization 且 401 自动续期一次
   * —— `<img src>` 不会带 Bearer 头，直接绑 URL 会 401。
   */
  async fetchDeckPageImage(deckId: string, pageNo: number): Promise<Blob> {
    const { blob } = await api.download(
      `${BASE}/decks/${encodeURIComponent(deckId)}/pages/${pageNo}/content`,
    )
    return blob
  },
  /** 取内核产出的真 PPTX 字节（用于工作台内嵌预览 / 下载）。 */
  async fetchDeckPptx(artifactId: string): Promise<Blob> {
    const { blob } = await api.download(`${BASE}/artifacts/${encodeURIComponent(artifactId)}/content`)
    return blob
  },
}

/** Stable per-spec key: retries of one unchanged spec are idempotent. */
export function ingestIdempotencyKey(spec: Record<string, unknown>): string {
  const id = typeof spec.id === 'string' && spec.id ? spec.id : 'anonymous'
  const revision = typeof spec.revision === 'number' && Number.isFinite(spec.revision) ? spec.revision : 0
  return `ppt-studio-v2:ingest:${id}:${revision}`
}
