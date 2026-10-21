/** 幂等键：同一用户写动作的重试必须复用同一键，禁止每次点击生成新键 */
let fallbackSeq = 0

function makeKey(): string {
  const c = (globalThis as unknown as { crypto?: { randomUUID?: () => string | undefined; getRandomValues?: (array: Uint32Array) => Uint32Array } }).crypto
  const uuid = c?.randomUUID?.()
  if (uuid) return uuid
  const entropy = c?.getRandomValues?.(new Uint32Array(2))
  if (entropy) return `idem_${Date.now()}_${++fallbackSeq}_${entropy[0].toString(36)}${entropy[1].toString(36)}`
  // Last-resort runtimes without Web Crypto still get a process-unique key;
  // browser production uses randomUUID/getRandomValues above.
  return `idem_${Date.now()}_${++fallbackSeq}`
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
