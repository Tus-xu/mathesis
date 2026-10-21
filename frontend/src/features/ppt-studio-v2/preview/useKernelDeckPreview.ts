/**
 * 备课课件 → PPT 内核（8200/8201）真实生成与逐页渲染（2026-09-14）。
 *
 * 背景（为什么需要这个模块）：
 * 备课本的「课件」此前只把 TeachingDeckSpec 的文本摊成两个文本框渲染，
 * 内核（8200 VPL 12 版式家族 + 8201 layout_resolver/builder/PowerPoint COM 渲染）
 * 虽然完整可用，但浏览器这条路**从未提交生成作业** ——
 * `pptStudioApi.submitDeckGeneration` 在接入前零生产调用点。
 *
 * 本模块按人工裁决实现「复用 8100 导出链」：
 *   1. `POST /decks/ingest`（8200）把服务端 spec 交给内核，拿内核 deck_id；
 *   2. `GET /decks/{id}/pages`（8200）看是否已有可读的逐页渲染图 —— 有就直接用；
 *   3. 没有则 `POST /artifacts/decks/{8100DeckId}/exports`（8100）触发导出；
 *      8100 内部会 ingest → submit generate → 轮询 8200 → 登记产物（既有链路，未新增并行真源）；
 *   4. 轮询 `GET /artifacts/exports/{jobId}` 到终态；成功后再读逐页渲染图。
 *
 * 诚实性约束：
 * - 内核拒收（如 `visual plan QA 未通过: summary-too-thin`）**如实上抛**，不伪造产物、不静默降级；
 * - 浏览器只打 8100 与统一代理的 8200，**不直连 8201**（8201 是私有引擎）；
 * - 页图缺失（老 deck 未归档字节）时明确报「字节缺失」，不显示空白冒充成功。
 */
import { ref, shallowRef, type Ref } from 'vue'

import { pptStudioApi, type PptStudioDeckPage } from '@/features/ppt-studio-v2/api/pptStudioApi'
import { getExportJob, requestDeckExport } from '@/features/teacher-prep-v2/api/prepApi'

export type KernelPreviewStatus = 'ingesting' | 'generating' | 'ready' | 'failed'

export interface KernelPageImage {
  pageNo: number
  slideId: string | null
  url: string
}

export interface KernelDeckPreview {
  status: Ref<KernelPreviewStatus>
  /** 失败原因（内核/导出作业给的原文，不翻译成模糊话术）。 */
  error: Ref<string>
  kernelDeckId: Ref<string | null>
  pages: Ref<KernelPageImage[]>
  /** 内核 PPTX artifact id（可下载/内嵌）。 */
  pptxArtifactId: Ref<string | null>
  /** 生成进度（服务端 job progress 原样透传，无服务端进度就为空）。 */
  progress: Ref<string>
  refresh: () => Promise<void>
}

interface Options {
  /** 8100 侧 TeachingDeckSpec id（导出链要用它）。 */
  deckId: Ref<string | null>
  /** 8100 侧 spec revision（导出要求 expected_revision 乐观并发）。 */
  revision: Ref<number | null>
  /** 完整 TeachingDeckSpec body（交给 8200 ingest）。 */
  spec: Ref<Record<string, unknown> | null>
}

const POLL_INTERVAL_MS = 4000
const POLL_TIMEOUT_MS = 15 * 60 * 1000

function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

/** 从导出作业视图里取「诚实的原因」：服务端给什么就说什么，不编。 */
function exportFailureReason(job: Record<string, unknown>): string {
  const err = asString(job.error_label) || asString(job.error)
  if (err) return err
  const progress = (job.progress ?? null) as Record<string, unknown> | null
  const stage = progress ? asString(progress.stage) : ''
  return stage ? `导出作业失败（stage=${stage}）` : '导出作业失败（服务端未给出原因）'
}

export function useKernelDeckPreview(options: Options): KernelDeckPreview {
  const status = ref<KernelPreviewStatus>('ingesting')
  const error = ref('')
  const kernelDeckId = ref<string | null>(null)
  const pages = shallowRef<KernelPageImage[]>([])
  const pptxArtifactId = ref<string | null>(null)
  const progress = ref('')

  /** 已建好的 object URL，替换/卸载时一并回收，避免内存泄漏。 */
  let objectUrls: string[] = []
  let runToken = 0

  function releaseUrls() {
    for (const url of objectUrls) URL.revokeObjectURL(url)
    objectUrls = []
    pages.value = []
  }

  async function loadPageImages(deckId: string, list: PptStudioDeckPage[]) {
    releaseUrls()
    const ready = list.filter((p) => p.png_available)
    if (!ready.length) return false
    const out: KernelPageImage[] = []
    for (const p of ready) {
      try {
        const blob = await pptStudioApi.fetchDeckPageImage(deckId, p.page_no)
        const url = URL.createObjectURL(blob)
        objectUrls.push(url)
        out.push({ pageNo: p.page_no, slideId: p.slide_id, url })
      } catch {
        // 单页取不到不阻塞其余页；缺页在 UI 上以页码占位如实显示（不塞假图）。
      }
    }
    pages.value = out
    return out.length > 0
  }

  async function loadPptxArtifact(deckId: string) {
    try {
      const artifacts = await pptStudioApi.listArtifacts(deckId)
      const pptx = artifacts.find((a) => a.kind === 'DECK_PPTX')
      pptxArtifactId.value = pptx?.id ?? null
    } catch {
      pptxArtifactId.value = null
    }
  }

  async function refresh() {
    const token = ++runToken
    error.value = ''
    const spec = options.spec.value
    const deckId = options.deckId.value
    const revision = options.revision.value
    if (!spec || !deckId) {
      status.value = 'failed'
      error.value = '课件方案尚未就绪，无法交给 PPT 内核生成。'
      return
    }

    status.value = 'ingesting'
    let ingested: { deck_id: string }
    try {
      // 先 ingest：它同时是内核的 approved/schema/页预算闸门，失败即如实上抛。
      ingested = await pptStudioApi.ingestDeck(spec)
    } catch (e) {
      status.value = 'failed'
      error.value = `PPT 内核拒收该课件方案：${e instanceof Error ? e.message : String(e)}`
      return
    }
    if (token !== runToken) return
    kernelDeckId.value = ingested.deck_id

    // 已有可读页图 → 直接展示（不重复生成，内核产物内容寻址幂等）。
    try {
      const existing = await pptStudioApi.listDeckPages(ingested.deck_id)
      if (token !== runToken) return
      await loadPptxArtifact(ingested.deck_id)
      if (await loadPageImages(ingested.deck_id, existing)) {
        status.value = 'ready'
        return
      }
    } catch {
      /* 清单读不到 → 继续走生成 */
    }

    // 触发 8100 导出链（其内部完成 8200 生成 + 产物登记）。
    status.value = 'generating'
    progress.value = ''
    let jobId = ''
    try {
      const submitted = await requestDeckExport(deckId, {
        format: 'pptx',
        expected_revision: revision ?? 0,
      })
      jobId = asString(submitted.job_id) || asString(submitted.id)
    } catch (e) {
      status.value = 'failed'
      error.value = `提交课件生成失败：${e instanceof Error ? e.message : String(e)}`
      return
    }
    if (!jobId) {
      status.value = 'failed'
      error.value = '提交课件生成失败：服务端未返回作业号。'
      return
    }

    const deadline = Date.now() + POLL_TIMEOUT_MS
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
      if (token !== runToken) return
      let job: Record<string, unknown>
      try {
        job = await getExportJob(jobId)
      } catch {
        continue
      }
      const st = asString(job.status)
      const pg = (job.progress ?? null) as Record<string, unknown> | null
      if (pg) {
        const stage = asString(pg.stage)
        const no = pg.page_no
        const count = pg.page_count
        progress.value = stage
          ? `${stage}${typeof no === 'number' && typeof count === 'number' ? ` ${no}/${count}` : ''}`
          : ''
      }
      if (st === 'done' || st === 'ready' || st === 'completed') break
      if (st === 'failed' || st === 'cancelled' || st === 'expired') {
        status.value = 'failed'
        error.value = exportFailureReason(job)
        return
      }
    }

    if (Date.now() >= deadline) {
      status.value = 'failed'
      error.value = '课件生成超时（未在 15 分钟内完成），可重试。'
      return
    }

    // 生成完成 → 读内核逐页渲染图
    try {
      const list = await pptStudioApi.listDeckPages(ingested.deck_id)
      if (token !== runToken) return
      await loadPptxArtifact(ingested.deck_id)
      if (await loadPageImages(ingested.deck_id, list)) {
        status.value = 'ready'
        progress.value = ''
        return
      }
      status.value = 'failed'
      error.value = '内核已生成课件，但逐页渲染图字节缺失（需要重跑生成以归档页图）。'
    } catch (e) {
      status.value = 'failed'
      error.value = `读取内核渲染结果失败：${e instanceof Error ? e.message : String(e)}`
    }
  }

  return { status, error, kernelDeckId, pages, pptxArtifactId, progress, refresh }
}
