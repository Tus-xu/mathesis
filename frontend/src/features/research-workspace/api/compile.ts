/**
 * 项目级编译 API（BACKEND-CONTRACT §3.4 / FRONTEND-ARCH §0-2）。
 *
 * 实测后端信封（2026-09-16）：
 * - `POST /projects/{id}/compile`               → `{ compile_run, last_successful_pdf_artifact_id }`（201）
 * - `GET  /projects/{id}/compile-runs/{run_id}` → `{ compile_run, last_successful_pdf_artifact_id }`
 * - `GET  /projects/{id}/compile-runs/latest`   → `{ compile_run|null, last_successful_pdf_artifact_id }`
 * - `GET  /projects/{id}/compile-runs/{run_id}/pdf` → 原始 PDF 字节（`application/pdf`）
 *
 * 后端编译为**同步阻塞**（≈15~25s 后返回已完成的 run），`status ∈ running|success|failed|timeout`。
 * 失败**不得清空 preview**：latest 同时返回 `last_successful_pdf_artifact_id`（宪法 §23）。
 * 本层把后端 payload 归一化为前端 `CompileRun`（`id → run_id`，透传 `last_successful_pdf_artifact_id`）。
 */
import { getAccessToken } from '@/api/authSession'
import { RESEARCH_API_BASE, RvApiError, rwGet, rwPost } from './client'
import type { CompileRun, CompileStatus } from './types'

/**
 * 并发编译被拒的机器可读码（后端 `compile.CompileInProgress`）。
 *
 * 同一项目 + 同 entry 已有编译在跑时，后端返回 409 + 本码，**不新建 run**；
 * 前端据此给人话提示并复用既有 run 继续轮询（不白屏、不报裸错误、不静默丢弃）。
 */
export const COMPILE_IN_PROGRESS = 'COMPILE_IN_PROGRESS'

export const COMPILE_IN_PROGRESS_MESSAGE = '正在编译中，请稍候…'

/** 判定某错误是否为「已有编译在进行中」。 */
export function isCompileInProgress(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false
  return String((err as { code?: unknown }).code) === COMPILE_IN_PROGRESS
}

/** 后端 `run_public_dict` 的原始形状。 */
interface BackendRun {
  id: string
  project_id?: string
  engine?: string
  entry?: string
  status: CompileStatus
  pdf_artifact_id?: string | null
  pdf_available?: boolean
  page_count?: number
  pdf_size_bytes?: number
  duration_ms?: number
  error_count?: number
  warning_count?: number
  exit_code?: number
  log_lines?: CompileRun['log_lines']
  error_code?: string
  error_message?: string
  created_at?: string | null
  finished_at?: string | null
}

interface CompileEnvelope {
  compile_run?: BackendRun | null
  /** 失败时仍返回的上次成功产物 id（宪法 §23）。 */
  last_successful_pdf_artifact_id?: string | null
}

/** 后端 run → 前端 `CompileRun`（补 `run_id` 与 `last_successful_pdf_artifact_id`）。 */
function toCompileRun(env: CompileEnvelope | null | undefined): CompileRun | null {
  const r = env?.compile_run
  if (!r) return null
  return {
    run_id: String(r.id),
    status: r.status,
    engine: r.engine,
    entry: r.entry,
    pdf_artifact_id: r.pdf_artifact_id ?? null,
    pdf_available: Boolean(r.pdf_available ?? r.pdf_artifact_id),
    last_successful_pdf_artifact_id: env?.last_successful_pdf_artifact_id ?? null,
    log_lines: r.log_lines ?? [],
    duration_ms: r.duration_ms ?? null,
    page_count: r.page_count ?? null,
    pdf_size_bytes: r.pdf_size_bytes ?? null,
    error_count: r.error_count ?? 0,
    warning_count: r.warning_count ?? 0,
    error_code: r.error_code ?? '',
    error_message: r.error_message ?? '',
  }
}

/**
 * 触发项目编译。后端同步阻塞至编译完成，返回归一化的 `CompileRun`。
 * 传入 `signal` 可在用户离开时中止等待（后端仍会完成本次编译）。
 */
export async function compile(
  projectId: string,
  entry = 'main.tex',
  engine = 'xelatex',
  signal?: AbortSignal,
): Promise<CompileRun | null> {
  try {
    const env = await rwPost<CompileEnvelope>(
      `/projects/${projectId}/compile`,
      { engine, entry },
      undefined,
      { signal },
    )
    return toCompileRun(env)
  } catch (e) {
    // 已有同入口编译在跑（409 + COMPILE_IN_PROGRESS）：**不是失败**。转成可识别的
    // 友好错误，让 store 显示「正在编译中，请稍候」并复用既有 run 继续轮询——
    // 绝不把裸错误码抛给 UI（宪法 §60/§61）。
    if (isCompileInProgress(e)) {
      throw new RvApiError({
        code: COMPILE_IN_PROGRESS,
        status: 409,
        message: COMPILE_IN_PROGRESS_MESSAGE,
        retryable: true,
      })
    }
    throw e
  }
}

export async function compileRun(projectId: string, runId: string): Promise<CompileRun | null> {
  const env = await rwGet<CompileEnvelope>(`/projects/${projectId}/compile-runs/${runId}`)
  return toCompileRun(env)
}

export async function latestRun(projectId: string): Promise<CompileRun | null> {
  const env = await rwGet<CompileEnvelope>(`/projects/${projectId}/compile-runs/latest`)
  return toCompileRun(env)
}

/**
 * 拉取某次运行的 PDF 产物字节（`GET /compile-runs/{run_id}/pdf`）。
 * 404（无产物）时抛出 `RvApiError`（由 client 统一映射为中文）。
 */
export async function downloadRunPdf(projectId: string, runId: string): Promise<Uint8Array> {
  const url = `${RESEARCH_API_BASE}/projects/${projectId}/compile-runs/${encodeURIComponent(runId)}/pdf`
  const headers: Record<string, string> = { Accept: 'application/pdf' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(url, { headers, credentials: 'include' })
  if (!res.ok) {
    throw Object.assign(new Error('PDF 暂不可用'), { status: res.status, code: res.status })
  }
  const buf = await res.arrayBuffer()
  return new Uint8Array(buf)
}
