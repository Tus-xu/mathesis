/**
 * RV08 · 真实 RV02/RV03 API + SSE Playwright。
 *
 * 前置：
 * - 8300 已启动
 * - vite 真实 API 代理（/api/research-vnext → 8300）
 * - RESEARCH_GATEWAY_HMAC_SECRET 与 8300 同源（只签名，不打印）
 *
 * Auth：仅 stub 应用侧 researcher 身份以通过路由守卫；
 * research-vnext 请求仍走真实 8300（page.route 注入 AccessContext 签名头）。
 */
import { test, expect, type Page } from '@playwright/test'
import { createHmac, randomUUID, createHash } from 'node:crypto'
import { mkdirSync, appendFileSync } from 'node:fs'

const OUT = 'D:/ppt-rescue-control/20260911-math-ai-platform-vnext/RV08'
const SECRET = process.env.RESEARCH_GATEWAY_HMAC_SECRET || ''

function b64u(raw: Buffer | string): string {
  return Buffer.from(raw).toString('base64url')
}

function accessHeaders(actor: string, tenant: string, project: string) {
  const payload = {
    actor_id: actor,
    tenant_id: tenant,
    session_id: randomUUID(),
    workspace: 'research',
    domain: 'research-vnext',
    capabilities: ['workspace.research.enter', 'research.session.write'],
    resource_scopes: [{ kind: 'project', ids: [project] }],
    security_version: 1,
    policy_version: 'p1',
    trace_id: createHash('md5').update(randomUUID()).digest('hex'),
    authority: 'api',
  }
  const raw = b64u(JSON.stringify(payload))
  const sig = createHmac('sha256', SECRET).update(raw).digest('hex')
  return {
    'X-Research-Access-Context': raw,
    'X-Research-Access-Sig': sig,
  }
}

async function createSessionViaApi(
  baseURL: string,
  headers: Record<string, string>,
  actor: string,
  tenant: string,
  project: string,
) {
  const body = {
    schema_version: '1.0.0',
    client_request_id: randomUUID(),
    expected_revision: 0,
    actor_id: actor,
    scope: { kind: 'project', id: project, role: 'owner' },
    traceparent: `00-${createHash('md5').update(randomUUID()).digest('hex').slice(0, 32)}-${createHash('md5').update(randomUUID()).digest('hex').slice(0, 16)}-01`,
    deadline: new Date(Date.now() + 120000).toISOString(),
    idempotency_fingerprint: createHash('sha256').update(randomUUID()).digest('hex'),
    command_type: 'session.create',
    payload_ref: 'inline:e2e',
    tenant_id: tenant,
    payload: {
      title: 'RV08 真实会话',
      mode: 'MODELING',
      project_id: project,
      tenant_id: tenant,
      policy_refs: ['policy:research.default@v1'],
      goal: '验证 Workspace 与 snapshot/SSE 接线',
    },
  }
  const res = await fetch(`${baseURL}/api/research-vnext/sessions:create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...headers },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`create session failed: ${res.status} ${await res.text()}`)
  const json = (await res.json()) as { session?: { id?: string } }
  const id = json.session?.id
  if (!id) throw new Error('missing session id')
  return id
}

async function stubResearcherAuth(page: Page) {
  const user = {
    nickname: '陈研究员',
    id: 'e2e-researcher',
    roles: [{ role: 'researcher', status: 'approved', verified: true }],
    active_role: 'researcher',
    grade: '',
  }
  await page.route('**/api/auth/token/refresh**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: { access_token: 'e2e-token', user, identity_status: 'verified' } }),
    }),
  )
  await page.route('**/api/auth/me**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: user }),
    }),
  )
  await page.route('**/api/auth/role/switch**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: { access_token: 'e2e-token', user } }),
    }),
  )
  // 布局增长面板等非研究请求：返回空成功，避免 401 触发整站登出
  await page.route('**/api/**', async (route) => {
    const url = route.request().url()
    if (url.includes('/research-vnext/')) return route.continue()
    if (url.includes('/auth/')) return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: {} }),
    })
  })
  await page.addInitScript((userJson) => {
    localStorage.setItem('ma_user', userJson)
    document.cookie = 'ma_csrf=e2e-csrf; path=/'
  }, JSON.stringify(user))
}

test.describe('RV08 real research workspace', () => {
  test.skip(!SECRET, 'RESEARCH_GATEWAY_HMAC_SECRET not set')

  test('loads workspace from real snapshot and opens SSE stream', async ({ page, baseURL }) => {
    const actor = randomUUID()
    const tenant = randomUUID()
    const project = randomUUID()
    const headers = accessHeaders(actor, tenant, project)
    const sessionId = await createSessionViaApi(baseURL!, headers, actor, tenant, project)

    await stubResearcherAuth(page)
    await page.addInitScript(
      ([actor, tenant, project, headerJson]) => {
        ;(globalThis as any).__MA_RESEARCH_CONTEXT__ = {
          actor_id: actor,
          tenant_id: tenant,
          project_id: project,
        }
        ;(globalThis as any).__MA_RESEARCH_ACCESS_HEADERS__ = JSON.parse(headerJson)
      },
      [actor, tenant, project, JSON.stringify(headers)],
    )
    await page.route('**/api/research-vnext/**', async (route) => {
      const request = route.request()
      await route.continue({ headers: { ...request.headers(), ...headers } })
    })

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/research-vnext/session/${sessionId}`)
    await expect(page.getByTestId('rv08-research-workspace')).toBeVisible({ timeout: 15000 })
    await expect(page.getByTestId('ma-region-left')).toBeVisible()
    await expect(page.getByTestId('ma-region-center')).toBeVisible()
    await expect(page.getByTestId('ma-region-status')).toBeVisible()
    await expect(page.getByTestId('rv08-composer')).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toContainText('RV08 真实会话')
    await expect(page.getByTestId('rv08-stream-status')).toBeVisible()
    await page.screenshot({ path: `${OUT}/screenshots/rv08-real-1440x900.png`, fullPage: true })
    mkdirSync(`${OUT}/evidence`, { recursive: true })
    appendFileSync(`${OUT}/evidence/e2e-real-api-sse.log`, `PASS real snapshot+workspace sessionId=${sessionId}\n`)
  })

  test('forbidden path stays in-surface honest error', async ({ page }) => {
    await stubResearcherAuth(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.route('**/api/research-vnext/**', (route) =>
      route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'FORBIDDEN', message: 'no access' } }),
      }),
    )
    await page.goto('/research-vnext/session/00000000-0000-0000-0000-000000000001')
    await expect(page.getByTestId('rv08-research-workspace')).toBeVisible({ timeout: 15000 })
    await expect(page.getByTestId('rv08-error-state')).toBeVisible()
    expect(page.url()).not.toContain('/login')
    await page.screenshot({ path: `${OUT}/screenshots/rv08-real-forbidden-1440x900.png`, fullPage: true })
  })
})

test('RV08 workspace layout @ 1440x900', async ({ page }) => {
  await stubResearcherAuth(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/research-vnext/session')
  await expect(page.getByTestId('rv08-research-workspace')).toBeVisible({ timeout: 15000 })
  await expect(page.getByTestId('ma-region-left')).toBeVisible()
  await expect(page.getByTestId('ma-region-center')).toBeVisible()
  await expect(page.getByTestId('ma-region-status')).toBeVisible()
  await page.screenshot({ path: `${OUT}/screenshots/rv08-workspace-1440x900.png`, fullPage: true })
})

test('RV08 workspace layout @ 1920x1080', async ({ page }) => {
  await stubResearcherAuth(page)
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.goto('/research-vnext/session')
  await expect(page.getByTestId('rv08-research-workspace')).toBeVisible({ timeout: 15000 })
  await expect(page.getByTestId('ma-region-left')).toBeVisible()
  await expect(page.getByTestId('ma-region-center')).toBeVisible()
  await page.screenshot({ path: `${OUT}/screenshots/rv08-workspace-1920x1080.png`, fullPage: true })
})
