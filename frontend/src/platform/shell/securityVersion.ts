/**
 * P01 · security_version 漂移检测。
 *
 * 背景（P00 契约 · AccessContext.security_version）：
 * 8000 是 Session / SecurityVersion 的**唯一权威**。当管理员撤销权限、强制下线、
 * 或改绑身份时，服务端会推进 `security_version`。前端持有的是**快照**，
 * 必须能识别"快照已过期"，并在过期时立即失效本地外壳（不允许继续用旧权限渲染）。
 *
 * 边界：
 * - 前端**不**判断权限内容，只判断"版本是否还对得上"；
 * - 任何拿不到权威值的情况一律 **fail-closed**（INVALID → 必须重新 bootstrap）；
 * - 不在此模块里写 localStorage（业务真相不得落本地）。
 */

/** 前端持有的安全快照。 */
export interface SecuritySnapshot {
  /** 账号标识（服务端权威 actor_id）。 */
  actorId: string
  /** 服务端下发的 security_version。 */
  securityVersion: number
}

/** 漂移判定结果。 */
export type SecurityDriftVerdict =
  /** 快照仍有效。 */
  | 'OK'
  /** 同一账号但 security_version 已推进 → 权限已变更（可能被撤销），必须重新取 AccessContext。 */
  | 'REVOKED'
  /** actor 变了（换账号/会话被替换）→ 整壳必须重建。 */
  | 'SESSION_REPLACED'
  /** 数据结构非法或缺少权威字段 → fail-closed。 */
  | 'INVALID'

function isValidSnapshot(s: unknown): s is SecuritySnapshot {
  if (!s || typeof s !== 'object') return false
  const o = s as Record<string, unknown>
  if (typeof o.actorId !== 'string' || o.actorId.length === 0) return false
  if (typeof o.securityVersion !== 'number' || !Number.isInteger(o.securityVersion)) return false
  if (o.securityVersion < 0) return false
  return true
}

/**
 * 比较前后两个安全快照，判定是否需要让本地外壳失效。
 *
 * 判定顺序（先身份、后版本）：
 * 1. 任一快照结构非法 → `INVALID`（fail-closed）；
 * 2. `actorId` 不同 → `SESSION_REPLACED`（换账号优先于版本比较）；
 * 3. `securityVersion` 不同 → `REVOKED`（含提升与回退，任一变化都视为需重新校验）；
 * 4. 全部一致 → `OK`。
 *
 * 说明：`securityVersion` 变化即判 `REVOKED`，**不做**"> 才失效"的单向判断 ——
 * 版本回退同样意味着服务端状态与本地快照不一致，继续使用旧快照是不安全的。
 */
export function detectSecurityVersionDrift(
  prev: SecuritySnapshot | null | undefined,
  next: SecuritySnapshot | null | undefined,
): SecurityDriftVerdict {
  if (!isValidSnapshot(prev) || !isValidSnapshot(next)) return 'INVALID'
  if (prev.actorId !== next.actorId) return 'SESSION_REPLACED'
  if (prev.securityVersion !== next.securityVersion) return 'REVOKED'
  return 'OK'
}

/** 便捷判定：是否需要让外壳失效（含重新 bootstrap）。 */
export function requiresShellInvalidation(verdict: SecurityDriftVerdict): boolean {
  return verdict !== 'OK'
}
