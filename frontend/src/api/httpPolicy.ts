/**
 * Web 基础套件 · HTTP 弹性策略
 * 自实现的极简并发闸门（semaphore）+ 可配置策略，零依赖。
 * 仅被 src/api/client.js 使用；测试通过 setHttpPolicy 注入低值以加速。
 */
export interface Semaphore {
  run<T>(fn: () => Promise<T>): Promise<T>
  readonly active: number
}

/** 极简信号量：超过并发上限时入队等待，而非丢弃请求。 */
export function createSemaphore(concurrency: number): Semaphore {
  type Task = { fn: () => Promise<unknown>; resolve: (v: unknown) => void; reject: (e: unknown) => void }
  const queue: Task[] = []
  let active = 0
  const next = () => {
    if (queue.length === 0) return
    const { fn, resolve, reject } = queue.shift() as Task
    active++
    fn().then(resolve, reject).finally(() => { active--; next() })
  }
  const sem: Semaphore = {
    run<T>(fn: () => Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        if (active < concurrency) {
          active++
          fn().then(resolve, reject).finally(() => { active--; next() })
        } else {
          queue.push({ fn, resolve: resolve as (v: unknown) => void, reject })
        }
      })
    },
    get active() { return active },
  }
  return sem
}

export interface HttpPolicy {
  defaultTimeoutMs: number
  globalConcurrency: number
  domainConcurrency: number
  maxRetries: number
  retryableStatuses: number[]
  baseRetryDelayMs: number
  maxRetryDelayMs: number
  maxRetryAfterMs: number
  /** 按 URL 前缀覆盖超时，长耗时生成接口用更长超时。 */
  timeoutByPrefix: Record<string, number>
}

export const defaultHttpPolicy: HttpPolicy = {
  defaultTimeoutMs: 30000,
  globalConcurrency: 8,
  domainConcurrency: 4,
  maxRetries: 2,
  retryableStatuses: [408, 429, 500, 502, 503, 504],
  baseRetryDelayMs: 300,
  maxRetryDelayMs: 8000,
  maxRetryAfterMs: 10000,
  timeoutByPrefix: {},
}

let policy: HttpPolicy = { ...defaultHttpPolicy }
let globalGate: Semaphore | null = null
const domainGates = new Map<string, Semaphore>()

export function getHttpPolicy(): HttpPolicy { return policy }
export function setHttpPolicy(p: Partial<HttpPolicy>): void {
  policy = { ...policy, ...p }
  resetGates()
}
export function resetHttpPolicy(): void {
  policy = { ...defaultHttpPolicy }
  resetGates()
}
function resetGates(): void {
  globalGate = null
  domainGates.clear()
}

/** 按 URL 第一段（跳过 /api）分域，使同域请求受 secondary 闸门约束。 */
function domainOf(path: string): string {
  const seg = path.split('/').filter(Boolean).find((p) => p !== 'api')
  return seg || 'root'
}

/** 全局闸门 + 单域闸门两级限流，等待中的请求排队而非丢弃。 */
export function runWithGate<T>(path: string, fn: () => Promise<T>): Promise<T> {
  const gate = (globalGate ??= createSemaphore(policy.globalConcurrency))
  const dom = domainOf(path)
  let dgate = domainGates.get(dom)
  if (!dgate) { dgate = createSemaphore(policy.domainConcurrency); domainGates.set(dom, dgate) }
  return gate.run(() => dgate!.run(fn))
}
