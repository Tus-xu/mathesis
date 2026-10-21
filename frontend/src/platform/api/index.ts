/**
 * P01 · 平台 API 层导出入口。
 */
export {
  resolveRequestContext,
  requestContextHeaders,
  generateTraceId,
  generateSpanId,
  generateClientRequestId,
  isValidTraceparent,
  normalizeError,
  type RequestContext,
  type PlatformErrorShape,
} from './requestContext'
