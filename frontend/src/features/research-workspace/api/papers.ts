/**
 * 文献 API（BACKEND-CONTRACT §3.1）。
 * 元数据解析失败**不得**导致文献不可用：`parse_status=failed` 仍可读 PDF（宪法 §10）。
 */
import { rwDel, rwGet, rwPatch, rwPost } from './client'
import type { Paper, PageText } from './types'

export async function listPapers(projectId: string): Promise<Paper[]> {
  const r = await rwGet<{ items: Paper[] }>(`/projects/${projectId}/papers`)
  return r?.items ?? []
}

/** multipart 上传（file）或引用既有 asset（asset_id）。 */
export async function uploadPaper(projectId: string, file: File): Promise<Paper> {
  const fd = new FormData()
  fd.append('file', file)
  const r = await rwPost<{ paper: Paper }>(`/projects/${projectId}/papers`, fd)
  return r.paper
}

export async function attachPaperAsset(projectId: string, assetId: string): Promise<Paper> {
  const r = await rwPost<{ paper: Paper }>(`/projects/${projectId}/papers`, { asset_id: assetId })
  return r.paper
}

export async function deletePaper(projectId: string, paperId: string): Promise<void> {
  await rwDel<void>(`/projects/${projectId}/papers/${paperId}`)
}

export async function patchPaper(
  projectId: string,
  paperId: string,
  body: { read_state?: string; last_page?: number },
): Promise<Paper> {
  const r = await rwPatch<{ paper: Paper }>(`/projects/${projectId}/papers/${paperId}`, body)
  return r.paper
}

export async function getPageText(projectId: string, paperId: string, page: number): Promise<PageText> {
  return rwGet<PageText>(`/projects/${projectId}/papers/${paperId}/text`, { page })
}
