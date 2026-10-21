/**
 * P01 App Shell + workspace capability 契约测试（先失败后实现）。
 *
 * 覆盖 P01 指令要求的六类场景：
 *   1. 同一账号的 workspace capability 显示
 *   2. 旧直达 URL 兼容
 *   3. refresh / revoke security_version
 *   4. 401 / 403
 *   5. 跨域 route guard
 *   6. 刷新恢复
 *
 * 边界（P00 契约 + frontend AGENTS.md）：
 * - 前端只**消费** capability，不自行签发/推导权限真相；
 * - security_version 由 8000 权威，前端只做失效检测；
 * - 不允许 localStorage 承载业务真相。
 */
/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest'

import type { AccessContext } from '../../src/features/platform-contracts/types/platform-contracts'

// ── P01 实现目标（当前不存在 → 测试必须先失败） ──────────────────────
import {
  deriveWorkspaceChoices,
  isCapabilityPresent,
  type WorkspaceChoice,
} from '../../src/platform/shell/workspaceManifest'
import {
  detectSecurityVersionDrift,
  type SecuritySnapshot,
} from '../../src/platform/shell/securityVersion'
import {
  resolvePlatformNavigation,
  type PlatformNavigationInput,
} from '../../src/platform/shell/navigation'
import {
  LEGACY_URL_ALIASES,
  resolveLegacyUrl,
} from '../../src/platform/shell/legacyUrls'

const teacherAccess: AccessContext = {
  actor_id: 'u-1',
  tenant_id: 't-1',
  workspace: 'teacher',
  domain: 'teacher-prep',
  capabilities: ['teacher.prep.read', 'teacher.prep.write'],
  security_version: 7,
  policy_version: '2026.09',
  trace_id: 'trace-1',
}

const researchAccess: AccessContext = {
  actor_id: 'u-1',
  tenant_id: 't-1',
  workspace: 'research',
  domain: 'research-vnext',
  capabilities: ['research.paper.read'],
  security_version: 7,
  policy_version: '2026.09',
  trace_id: 'trace-2',
}

// ── 1. 同一账号的 workspace capability 显示 ─────────────────────────
describe('P01 · 同一账号 workspace capability', () => {
  it('同一账号持有 teacher+research 两个 AccessContext 时，chooser 应同时列出两个 workspace', () => {
    const choices: WorkspaceChoice[] = deriveWorkspaceChoices([teacherAccess, researchAccess])
    const keys = choices.map((c) => c.workspace).sort()
    expect(keys).toEqual(['research', 'teacher'])
  })

  it('chooser 条目必须携带 capability 清单，供 UI 显示与锁定判断', () => {
    const choices = deriveWorkspaceChoices([teacherAccess, researchAccess])
    const teacher = choices.find((c) => c.workspace === 'teacher')
    expect(teacher?.capabilities).toContain('teacher.prep.write')
  })

  it('capability 缺失时 chooser 条目标记为不可进入（而非隐藏）', () => {
    const noCap: AccessContext = { ...researchAccess, capabilities: [] }
    const choices = deriveWorkspaceChoices([teacherAccess, noCap])
    const research = choices.find((c) => c.workspace === 'research')
    expect(research?.available).toBe(false)
  })

  it('isCapabilityPresent 对未知 capability 返回 false，不得猜测', () => {
    expect(isCapabilityPresent(teacherAccess, 'teacher.prep.write')).toBe(true)
    expect(isCapabilityPresent(teacherAccess, 'teacher.unknown.thing')).toBe(false)
  })

  it('capabilities 缺席时不得视为“全部允许”（fail-closed）', () => {
    const noCaps: AccessContext = { ...teacherAccess, capabilities: undefined }
    expect(isCapabilityPresent(noCaps, 'teacher.prep.read')).toBe(false)
  })
})

// ── 2. 旧直达 URL 兼容 ──────────────────────────────────────────────
describe('P01 · 旧直达 URL 兼容', () => {
  it('旧 /teacher-v3/* 直达 URL 必须保留可用（不删除旧页）', () => {
    expect(resolveLegacyUrl('/teacher-v3/today')).toBe('/teacher-v3/today')
    expect(resolveLegacyUrl('/teacher-v3/slides')).toBe('/teacher-v3/slides')
  })

  it('旧科研端 URL 必须被解析到新科研路由', () => {
    expect(resolveLegacyUrl('/research/library')).toBe('/research/library')
  })

  it('LEGACY_URL_ALIASES 必须显式登记别名，且不得为空', () => {
    expect(Object.keys(LEGACY_URL_ALIASES).length).toBeGreaterThan(0)
  })

  it('未登记的 URL 原样返回，不得擅自重写', () => {
    expect(resolveLegacyUrl('/some/unknown/path')).toBe('/some/unknown/path')
  })
})

// ── 3. refresh / revoke security_version ───────────────────────────
describe('P01 · security_version 漂移检测', () => {
  it('security_version 提高（权限被撤销）应判定为失效', () => {
    const prev: SecuritySnapshot = { actorId: 'u-1', securityVersion: 7 }
    const next: SecuritySnapshot = { actorId: 'u-1', securityVersion: 8 }
    expect(detectSecurityVersionDrift(prev, next)).toBe('REVOKED')
  })

  it('security_version 不变应判定为未失效', () => {
    const prev: SecuritySnapshot = { actorId: 'u-1', securityVersion: 7 }
    expect(detectSecurityVersionDrift(prev, { actorId: 'u-1', securityVersion: 7 })).toBe('OK')
  })

  it('actor 变更（换账号）应判定为失效，必须重新 bootstrap', () => {
    const prev: SecuritySnapshot = { actorId: 'u-1', securityVersion: 7 }
    expect(detectSecurityVersionDrift(prev, { actorId: 'u-2', securityVersion: 7 })).toBe('SESSION_REPLACED')
  })

  it('缺失 security_version 必须 fail-closed（视为失效）', () => {
    const prev: SecuritySnapshot = { actorId: 'u-1', securityVersion: 7 }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(detectSecurityVersionDrift(prev, { actorId: 'u-1' } as any)).toBe('INVALID')
  })
})

// ── 4 & 5. 401/403 与跨域 route guard ─────────────────────────────
describe('P01 · 路由守卫（401/403 与跨域）', () => {
  const base: PlatformNavigationInput = {
    targetWorkspace: 'teacher',
    to: '/teacher-v3/today',
    toMeta: { requiresRole: 'teacher' },
    status: 'authenticated',
    activeWorkspace: 'teacher',
    accessContexts: [teacherAccess, researchAccess],
    isPublic: false,
  }

  it('公开路由直接放行，不做 capability 判定', () => {
    expect(resolvePlatformNavigation({ ...base, isPublic: true }).allow).toBe(true)
  })

  it('未登录（401 场景）应重定向到登录并携带 redirect', () => {
    const r = resolvePlatformNavigation({ ...base, status: 'anonymous' })
    expect(r.allow).toBe(false)
    expect(r.redirect?.path).toBe('/login')
  })

  it('已登录但 capability 不足（403 场景）应拒绝进入并给出明确理由', () => {
    const r = resolvePlatformNavigation({ ...base, activeWorkspace: 'research' })
    expect(r.allow).toBe(false)
    expect(r.reason).toBe('CROSS_WORKSPACE_DENIED')
  })

  it('同一账号在自己 workspace 内应放行', () => {
    expect(resolvePlatformNavigation(base).allow).toBe(true)
  })

  it('目标 workspace 无 AccessContext 时应 fail-closed', () => {
    const r = resolvePlatformNavigation({ ...base, accessContexts: [researchAccess] })
    expect(r.allow).toBe(false)
  })
})

// ── 6. 刷新恢复 ────────────────────────────────────────────────────
describe('P01 · 刷新恢复', () => {
  it('刷新后应能从服务端 AccessContext 恢复 workspace 选择（不依赖 localStorage）', () => {
    const choices = deriveWorkspaceChoices([teacherAccess])
    expect(choices).toHaveLength(1)
    expect(choices[0].workspace).toBe('teacher')
  })

  it('AccessContext 为空（刷新后尚未取到）应返回空清单，不得伪造条目', () => {
    expect(deriveWorkspaceChoices([])).toEqual([])
  })
})
