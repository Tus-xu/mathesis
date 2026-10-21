/** 幂等键：同一用户写动作的重试必须复用同一键，禁止每次点击生成新键 */
let fallbackSeq = 0

function makeKey(): string {
  const c = (globalThis as unknown as { crypto?: { randomUUID?: () => string | undefined } }).crypto
  const uuid = c?.randomUUID?.()
  if (uuid) return uuid
  return `idem_${Date.now()}_${++fallbackSeq}_${Math.random().toString(36).slice(2, 10)}`
}

export interface IdempotencyTracker {
  keyFor(actionId: string): string
  clear(actionId: string): void
}

export function createIdempotencyTracker(): IdempotencyTracker {
  const keys = new Map<string, string>()
  return {
    keyFor(actionId: string): string {
      let k = keys.get(actionId)
      if (!k) { k = makeKey(); keys.set(actionId, k) }
      return k
    },
    clear(actionId: string): void { keys.delete(actionId) },
  }
}

export function newIdempotencyKey(): string {
  return makeKey()
}