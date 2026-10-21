/**
 * P01 · 统一请求上下文注入（traceparent / client_request_id）。
 *
 * 职责（P00 契约 · DomainEvent / AIRunRecord 的 trace_id 一致性）：
 * - 为每个出站请求生成/透传 `traceparent`（W3C Trace Context）与
 *   `X-Client-Request-Id`，使前端一次交互可被后端全链路关联；
 * - **不**在此模块做认证（认证由 `src/api/authSession` + `src/api/client` 负责）；
 * - 纯函数 + 极简状态，便于单测。
 *
 * 注意：平台层不直接 fetch（反模式扫描守门），本模块只产出 header 与 ID。
 */

/** W3C traceparent 版本（固定 00）。 */
const TRACE_VERSION = '00'

/** 生成 16 字节 trace-id（32 hex）。 */
export function generateTraceId(): string {
  const bytes = new Uint8Array(16)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 生成 8 字节 span-id（16 hex）。 */
export function generateSpanId(): string {
  const bytes = new Uint8Array(8)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 生成一次交互的 client_request_id（与 trace-id 同源，便于关联与幂等）。 */
export function generateClientRequestId(): string {
  return generateTraceId()
}

const TRACEPARENT_RE = /^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/

/** 校验 traceparent 是否合法（避免把脏值透传给后端）。 */
export function isValidTraceparent(value: unknown): value is string {
  return typeof value === 'string' && TRACEPARENT_RE.test(value)
}

export interface RequestContext {
  traceparent: string
  clientRequestId: string
}

/**
 * 解析请求上下文 header。
 *
 * - 传入合法 `traceparent` → 复用（保持跨请求链路连续）；
 * - 否则新建；
 * - `clientRequestId` 同理。
 */
export function resolveRequestContext(input?: {
  traceparent?: string | null
  clientRequestId?: string | null
}): RequestContext {
  const traceparent = isValidTraceparent(input?.traceparent)
    ? (input!.traceparent as string)
    : `${TRACE_VERSION}-${generateTraceId()}-${generateSpanId()}-01`

  const clientRequestId =
    typeof input?.clientRequestId === 'string' && input.clientRequestId.length > 0
      ? input.clientRequestId
      : generateClientRequestId()

  return { traceparent, clientRequestId }
}

/** 把请求上下文摊平为 HTTP header（纯数据，不含认证头）。 */
export function requestContextHeaders(ctx: RequestContext): Record<string, string> {
  return {
    traceparent: ctx.traceparent,
    'X-Client-Request-Id': ctx.clientRequestId,
  }
}

// ── 统一错误规整 ────────────────────────────────────────────────────

/** 平台统一错误形状（不吞掉域错误，只做结构化包装）。 */
export interface PlatformErrorShape {
  /** HTTP 状态码。 */
  status: number
  /** 业务错误码（若服务端提供）。 */
  code?: string | number
  /** 可展示的消息（**不得**由前端编造业务语义）。 */
  message: string
  /** 服务端错误标识（用于排障）。 */
  errorKey?: string
  /** 是否建议重试（429/503）。 */
  retryable: boolean
  /** 是否需重新登录（401）。 */
  requiresReauth: boolean
  /** 是否需重新获取权限（403）。 */
  requiresReauthz: boolean
}

/**
 * 把 HTTP 状态与响应体规整为统一错误形状。
 *
 * 原则：
 * - **不吞掉**域错误详情（`message` 优先取服务端文案）；
 * - 401/403/409/429/503 有明确语义分类，供上层决定"重登/重授权/重试"；
 * - 未列出的状态按 5xx/4xx 泛化，不猜测业务含义。
 */
export function normalizeError(
  status: number,
  payload?: { code?: string | number; message?: string; error_key?: string; detail?: unknown } | null,
): PlatformErrorShape {
  const detail = payload?.detail
  const detailMessage =
    typeof detail === 'string'
      ? detail
      : detail && typeof detail === 'object' && 'message' in (detail as object)
        ? String((detail as { message?: unknown }).message ?? '')
        : ''

  const message =
    payload?.message ||
    detailMessage ||
    defaultMessageForStatus(status)

  return {
    status,
    code: payload?.code,
    message,
    errorKey: payload?.error_key,
    retryable: status === 429 || status === 503 || status >= 500,
    requiresReauth: status === 401,
    requiresReauthz: status === 403,
  }
}

function defaultMessageForStatus(status: number): string {
  switch (status) {
    case 400:
      return '请求参数有误'
    case 401:
      return '登录状态已失效，请重新登录'
    case 403:
      return '没有访问该资源的权限'
    case 404:
      return '资源不存在'
    case 409:
      return '数据已更新，请刷新后重试'
    case 429:
      return '操作过于频繁，请稍后再试'
    case 503:
      return '服务暂时不可用，请稍后重试'
    default:
      return status >= 500 ? '服务异常，请稍后重试' : `请求失败（${status}）`
  }
}
