/**
 * P01 · 平台级路由守卫（跨 workspace / 401 / 403）。
 *
 * 职责（P00 契约 · ADR-PLAT-09 对象级授权 + frontend AGENTS.md）：
 * - 前端守卫只做**粗粒度导航拦截**（提示/重定向），**不是**授权决策点；
 * - 真正的授权在 8000（对象级）。前端守卫放行 ≠ 有权限，只是"值得发请求试试"；
 * - 目标是防止"越 workspace 乱跳"和"用别人的 workspace 外壳渲染自己的数据"。
 *
 * fail-closed 原则：
 * - 目标 workspace 没有对应 AccessContext → 拒绝；
 * - `activeWorkspace` 与 `targetWorkspace` 不一致 → 拒绝（跨域）；
 * - 任何未知状态 → 拒绝。
 */
import type { AccessContext } from '@/features/platform-contracts/types/platform-contracts'
import type { WorkspaceKey } from './workspaceManifest'

/** 守卫输入。 */
export interface PlatformNavigationInput {
  /** 目标路由路径。 */
  to: string
  /** 目标路由 meta（只读取平台关心的字段）。 */
  toMeta: Record<string, unknown>
  /** 会话状态（与 auth store 的 status 同源）。 */
  status: string
  /** 当前激活的 workspace（服务端 AccessContext 决定）。 */
  activeWorkspace: WorkspaceKey | null
  /** 请求进入的 workspace（由目标路由推导）。 */
  targetWorkspace: WorkspaceKey | null
  /** 服务端下发的全部 AccessContext。 */
  accessContexts: readonly AccessContext[] | null | undefined
  /** 是否为公开路由。 */
  isPublic: boolean
}

/** 守卫结果。 */
export interface PlatformNavigationResult {
  allow: boolean
  redirect?: { path: string; query?: Record<string, string> }
  /** 拒绝原因（机器可读，供测试与埋点）。 */
  reason?:
    | 'ANONYMOUS'
    | 'CROSS_WORKSPACE_DENIED'
    | 'NO_ACCESS_CONTEXT'
    | 'UNKNOWN_WORKSPACE'
}

/** 需要认证但无权限时的统一提示页（403 场景，教师端已有）。 */
const DENIED_PATH = '/teacher-v3/denied'

/** 判定"已登录"的状态集合（与 auth store getters.isLoggedIn 对齐）。 */
const LOGGED_IN_STATES = [
  'authenticated',
  'onboarding',
  'pending_review',
  'needs_more_info',
  'rejected',
  'deletion_pending',
]

function contextsFor(
  contexts: readonly AccessContext[] | null | undefined,
  workspace: WorkspaceKey | null,
): AccessContext | undefined {
  if (!workspace || !Array.isArray(contexts)) return undefined
  return contexts.find((c) => c?.workspace === workspace)
}

/**
 * 解析导航决策。
 *
 * 判定顺序：
 * 1. 公开路由 → 放行（不参与 capability 判定，如登录页/浏览器校验页）；
 * 2. 未登录 → 重定向 `/login` 并带 `redirect`；
 * 3. 目标 workspace 未知 → 拒绝（fail-closed）；
 * 4. 目标 workspace 无 AccessContext → 拒绝（`NO_ACCESS_CONTEXT`）；
 * 5. 目标 workspace ≠ 当前激活 workspace → 拒绝（`CROSS_WORKSPACE_DENIED`，403）；
 * 6. 通过 → 放行。
 */
export function resolvePlatformNavigation(
  input: PlatformNavigationInput,
): PlatformNavigationResult {
  const { to, isPublic, status, accessContexts } = input

  // 1) 公开路由
  if (isPublic) return { allow: true }

  // 2) 未登录（401 场景）
  if (!LOGGED_IN_STATES.includes(status)) {
    return {
      allow: false,
      reason: 'ANONYMOUS',
      redirect: {
        path: '/login',
        ...(to && to !== '/' ? { query: { redirect: to } } : {}),
      },
    }
  }

  // 3) 目标 workspace 未知 → fail-closed
  if (!input.targetWorkspace) {
    return { allow: false, reason: 'UNKNOWN_WORKSPACE' }
  }

  // 4) 无对应 AccessContext → fail-closed
  const access = contextsFor(accessContexts, input.targetWorkspace)
  if (!access) {
    return { allow: false, reason: 'NO_ACCESS_CONTEXT' }
  }

  // 5) 跨 workspace → 403
  if (input.activeWorkspace !== input.targetWorkspace) {
    return { allow: false, reason: 'CROSS_WORKSPACE_DENIED' }
  }

  // 6) 放行
  return { allow: true }
}

/**
 * 由路由 meta 推导目标 workspace。
 *
 * 只认显式登记，不做启发式猜测：meta 里写什么就是什么。
 * 调用方可为路由加 `meta.workspace`；为兼容存量路由，这里保留旧 meta 映射。
 */
export function resolveTargetWorkspace(meta: Record<string, unknown>): WorkspaceKey | null {
  const explicit = meta.workspace
  if (typeof explicit === 'string') {
    return explicit as WorkspaceKey
  }
  // 存量 meta 兼容（不引入新真相，只是读取既有字段）
  if (meta.teacherV3 === true || meta.teacher === true) return 'teacher'
  if (meta.research === true) return 'research'
  if (meta.admin === true) return 'admin'
  return null
}

export { DENIED_PATH }
