/**
 * P01 · 统一请求上下文与错误规整测试。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'

import {
  resolveRequestContext,
  requestContextHeaders,
  generateTraceId,
  generateSpanId,
  isValidTraceparent,
  normalizeError,
} from '../../src/platform/api/requestContext'

describe('P01 · 请求上下文（traceparent / client_request_id）', () => {
  it('generateTraceId 产出 32 位小写 hex', () => {
    const id = generateTraceId()
    expect(id).toMatch(/^[0-9a-f]{32}$/)
  })

  it('generateSpanId 产出 16 位小写 hex', () => {
    expect(generateSpanId()).toMatch(/^[0-9a-f]{16}$/)
  })

  it('两次生成不重复（随机性）', () => {
    expect(generateTraceId()).not.toBe(generateTraceId())
  })

  it('无输入时新建合法 traceparent', () => {
    const ctx = resolveRequestContext()
    expect(isValidTraceparent(ctx.traceparent)).toBe(true)
    expect(ctx.clientRequestId).not.toBe('')
  })

  it('传入合法 traceparent 时复用（保持链路连续）', () => {
    const existing = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'
    expect(resolveRequestContext({ traceparent: existing }).traceparent).toBe(existing)
  })

  it('传入非法 traceparent 时不得透传（重新生成）', () => {
    const bad = 'not-a-traceparent'
    const ctx = resolveRequestContext({ traceparent: bad })
    expect(ctx.traceparent).not.toBe(bad)
    expect(isValidTraceparent(ctx.traceparent)).toBe(true)
  })

  it('header 摊平结果含 traceparent 与 X-Client-Request-Id', () => {
    const headers = requestContextHeaders(resolveRequestContext())
    expect(headers.traceparent).toBeTruthy()
    expect(headers['X-Client-Request-Id']).toBeTruthy()
  })

  it('header 不得包含认证信息（认证由 authSession 负责）', () => {
    const headers = requestContextHeaders(resolveRequestContext())
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain('authorization')
  })
})

describe('P01 · 错误规整（401/403/409/429/503）', () => {
  it('401 标记 requiresReauth', () => {
    const e = normalizeError(401)
    expect(e.requiresReauth).toBe(true)
    expect(e.requiresReauthz).toBe(false)
  })

  it('403 标记 requiresReauthz（不等于重新登录）', () => {
    const e = normalizeError(403)
    expect(e.requiresReauthz).toBe(true)
    expect(e.requiresReauth).toBe(false)
  })

  it('409 不标记可重试（需刷新后再决定）', () => {
    expect(normalizeError(409).retryable).toBe(false)
  })

  it('429 与 503 标记可重试', () => {
    expect(normalizeError(429).retryable).toBe(true)
    expect(normalizeError(503).retryable).toBe(true)
  })

  it('优先采用服务端 message（不编造业务语义）', () => {
    const e = normalizeError(400, { message: '课时标题不能为空' })
    expect(e.message).toBe('课时标题不能为空')
  })

  it('detail 为字符串时作为 message', () => {
    expect(normalizeError(400, { detail: '参数缺失' }).message).toBe('参数缺失')
  })

  it('detail 为对象时取其中的 message（避免渲染 [object Object]）', () => {
    expect(normalizeError(400, { detail: { message: '字段非法' } }).message).toBe('字段非法')
  })

  it('无服务端文案时给出按状态分类的兜底文案', () => {
    expect(normalizeError(429).message).toMatch(/频繁/)
    expect(normalizeError(503).message).toMatch(/稍后/)
  })

  it('保留 error_key 供排障', () => {
    expect(normalizeError(401, { error_key: 'AUTH_REFRESH_INVALID' }).errorKey).toBe(
      'AUTH_REFRESH_INVALID',
    )
  })
})
