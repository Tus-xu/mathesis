/**
 * P01 · workspace manifest 与 capability 消费层。
 *
 * 职责边界（P00 契约 · ADR-PLAT-01/09 + frontend AGENTS.md）：
 * - 平台层只**消费** 8000 下发的 AccessContext，**不**自行签发 token、**不**推导权限真相；
 * - capability 为最细粒度授权单位，由服务端权威；
 * - `capabilities` 缺席一律 **fail-closed**（视为无权限），绝不放行；
 * - 本模块**不得** import 任何 Teacher/Research 域代码（保持域无关）。
 */
import type { AccessContext } from '@/features/platform-contracts/types/platform-contracts'

/** 平台已知的 workspace 枚举（与 AccessContext.workspace 同源）。 */
export const PLATFORM_WORKSPACES = ['student', 'teacher', 'research', 'admin'] as const

export type WorkspaceKey = (typeof PLATFORM_WORKSPACES)[number]

/**
 * workspace chooser 的单个条目。
 *
 * 注意：`available` 只表达"当前 AccessContext 是否携带可进入该 workspace 的 capability"，
 * 它是**展示层派生值**，不是权限真相 —— 真相始终在服务端。
 */
export interface WorkspaceChoice {
  workspace: WorkspaceKey
  /** 该 workspace 下服务端下发的 capability 清单（原样透传，不做增删）。 */
  capabilities: string[]
  /** 是否存在可进入的 capability（展示用派生值）。 */
  available: boolean
  /** 服务端下发的安全版本，供失效检测使用。 */
  securityVersion: number
  /** 服务端下发的 domain，供 manifest 解析。 */
  domain: string
  /** 服务端 trace_id，便于前端把 UI 行为关联回后端链路。 */
  traceId: string
}

/**
 * 判断 AccessContext 是否携带指定 capability。
 *
 * fail-closed 规则：
 * - `capabilities` 为 `undefined` / `null` / 非数组 → 返回 `false`；
 * - 数组为空 → 返回 `false`。
 *
 * 这样保证"服务端没给"与"服务端明确不给"产生同一个安全结果。
 */
export function isCapabilityPresent(
  access: AccessContext | null | undefined,
  capability: string,
): boolean {
  if (!access) return false
  const caps = access.capabilities
  if (!Array.isArray(caps)) return false
  if (caps.length === 0) return false
  return caps.includes(capability)
}

/**
 * 由一组 AccessContext 派生 workspace chooser 条目。
 *
 * 规则：
 * - 每个 AccessContext 产出一个条目，按 `PLATFORM_WORKSPACES` 稳定排序（避免 UI 抖动）；
 * - `capabilities` 原样透传（不补、不删，尊重服务端权威）；
 * - 空输入 → 空数组（**不得伪造条目**，刷新后未取到就是未取到）；
 * - 同一 workspace 出现多次时保留最后一个（服务端应为每个 workspace 只下发一份）。
 */
export function deriveWorkspaceChoices(
  contexts: readonly AccessContext[] | null | undefined,
): WorkspaceChoice[] {
  if (!Array.isArray(contexts) || contexts.length === 0) return []

  const byWorkspace = new Map<WorkspaceKey, WorkspaceChoice>()

  for (const access of contexts) {
    if (!access) continue
    const workspace = access.workspace as WorkspaceKey
    if (!PLATFORM_WORKSPACES.includes(workspace)) continue

    const caps = Array.isArray(access.capabilities) ? [...access.capabilities] : []

    byWorkspace.set(workspace, {
      workspace,
      capabilities: caps,
      available: caps.length > 0,
      securityVersion: access.security_version,
      domain: access.domain,
      traceId: access.trace_id,
    })
  }

  return [...byWorkspace.values()].sort(
    (a, b) => PLATFORM_WORKSPACES.indexOf(a.workspace) - PLATFORM_WORKSPACES.indexOf(b.workspace),
  )
}

/**
 * workspace → 默认落地路径。
 *
 * 这是**平台层**的兜底映射，仅用于"进入 workspace 后没有更具体目标"的场景。
 * 域若有自己的默认首页，应通过域 manifest 注册（见 `registerWorkspaceHome`），
 * 平台不硬编码域路由（ADR-PLAT-01）。
 */
export interface WorkspaceHomeRegistration {
  workspace: WorkspaceKey
  path: string
  /** 注册者标识（如 `teacher-prep-v2@1.0.0`），用于审计与冲突检测。 */
  registeredBy: string
}

const workspaceHomes = new Map<WorkspaceKey, WorkspaceHomeRegistration>()

/**
 * 注册某 workspace 的默认首页。域在自己的 manifest 中调用。
 *
 * 冲突策略：后注册者**不覆盖**已存在的注册，并返回 `false`。
 * （静默覆盖会让"哪个域拥有该 workspace 首页"变得不可追溯。）
 */
export function registerWorkspaceHome(reg: WorkspaceHomeRegistration): boolean {
  if (workspaceHomes.has(reg.workspace)) return false
  workspaceHomes.set(reg.workspace, reg)
  return true
}

/** 读取已注册的 workspace 首页；未注册返回 `null`（调用方决定兜底）。 */
export function resolveWorkspaceHome(workspace: WorkspaceKey): string | null {
  return workspaceHomes.get(workspace)?.path ?? null
}

/** 测试/热更新用：清空注册表。生产路径不应调用。 */
export function __resetWorkspaceHomes(): void {
  workspaceHomes.clear()
}
