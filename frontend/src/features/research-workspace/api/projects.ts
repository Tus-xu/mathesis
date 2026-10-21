/**
 * 项目 API（BACKEND-CONTRACT §3.1 / §3.7 / §3.8）。
 * 后端返回裸 JSON（`{items}` / `{project}`），此处适配成强类型返回值。
 * 端点可能尚未实现 → 调用方（store）负责诚实降级。
 */
import { rwGet, rwPatch, rwPost, rwPut } from './client'
import type {
  CreateProjectInput,
  HistoryEntry,
  ProjectOverview,
  ProjectRecent,
  ResearchProject,
  UpdateProjectInput,
} from './types'

export async function listProjects(): Promise<ResearchProject[]> {
  const r = await rwGet<{ items: ResearchProject[] }>('/projects')
  return r?.items ?? []
}

export async function createProject(input: CreateProjectInput): Promise<ResearchProject> {
  const r = await rwPost<{ project: ResearchProject }>('/projects', input)
  return r.project
}

export async function getProject(projectId: string): Promise<ResearchProject> {
  const r = await rwGet<{ project: ResearchProject }>(`/projects/${projectId}`)
  return r.project
}

/**
 * 更新项目（改名 / 改描述 / 归档）。
 *
 * 请求体必须含 `client_request_id`(≥8) 与 `expected_revision` —— 后端
 * `PATCH /projects/{project_id}` 用 `ResearchProjectUpdate` 校验，缺任一即 422。
 */
export async function patchProject(
  projectId: string,
  body: UpdateProjectInput,
): Promise<ResearchProject> {
  const r = await rwPatch<{ project: ResearchProject }>(`/projects/${projectId}`, body)
  return r.project
}

export async function getOverview(projectId: string): Promise<ProjectOverview> {
  // 后端实测（2026-09-16）直接返回 overview 本体（含 project_id/name/summary/stats/source），
  // 不带 `{overview}` 包装；此处对两种形态都做兼容，避免概览卡拿不到真实数据。
  const r = await rwGet<ProjectOverview & { overview?: ProjectOverview }>(
    `/projects/${projectId}/overview`,
  )
  return r?.overview ?? r
}

export async function getRecent(projectId: string): Promise<ProjectRecent | null> {
  const r = await rwGet<{ recent: ProjectRecent | null }>(`/projects/${projectId}/recent`)
  return r?.recent ?? null
}

export async function putRecent(projectId: string, recent: ProjectRecent): Promise<ProjectRecent> {
  const r = await rwPut<{ recent: ProjectRecent }>(`/projects/${projectId}/recent`, recent)
  return r.recent
}

export async function getHistory(projectId: string): Promise<HistoryEntry[]> {
  const r = await rwGet<{ items: HistoryEntry[] }>(`/projects/${projectId}/history`)
  return r?.items ?? []
}
