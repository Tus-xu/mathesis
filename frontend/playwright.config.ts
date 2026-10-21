import { defineConfig, devices } from '@playwright/test'

const mockMode = process.env.E2E_MOCK !== '0'
// P01：App Shell 集成沙盒（/__platform-harness）只在显式开启时才注册进路由表。
// 生产构建（vite build）不设该变量 → 该路由不存在，沙盒页不可能被发布到线上。
const harness = process.env.E2E_PLATFORM_HARNESS === '1' ? ' VITE_PLATFORM_HARNESS=1' : ''
// P01：沙箱内进程无法 bind 5176/5188（EACCES），故支持挂到已就绪的外部静态服务，
// 此时完全不启动 webServer（复用既有 server）。生产/CI 不设该变量，行为不变。
const externalBase = process.env.E2E_BASE_URL || ''
const rawPort = process.env.E2E_PORT ?? '5176'
const e2ePort = Number(rawPort)

if (!/^\d+$/.test(rawPort) || !Number.isInteger(e2ePort) || e2ePort < 1 || e2ePort > 65_535) {
  throw new Error(`E2E_PORT must be an integer between 1 and 65535; received "${rawPort}"`)
}

const baseURL = externalBase || `http://localhost:${e2ePort}`

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  retries: 0,
  // 单 worker：两个 spec 共享同一 mock dev server，_mock/teacher/reset 会清空共享内存态，
  // 并行 worker 会互踩（2026-09-01 L4 实测），故固化串行。
  workers: 1,
  reporter: [['list', { printSteps: true }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  webServer: externalBase
    ? undefined
    : mockMode
      ? {
          command: `cross-env VITE_USE_MOCK=1 VITE_MOCK_ROLE=teacher${harness} vite --port ${e2ePort} --strictPort`,
          url: baseURL,
          reuseExistingServer: false,
          timeout: 60_000,
        }
      : undefined,
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
