/**
 * WP-K · 真实浏览器探针（Smoke）
 *
 * 目的：在动手改代码前，先如实看清「真实后端 + 真实 token」下两个沉浸式 surface
 * 到底渲染出什么状态，而不是凭假设写报告。
 *
 * 身份 / 外壳处理（必须如实记录在报告中，见 WP-K-VISUAL-QA.md §0）：
 * - 业务数据 **全部真实**：`/api/teacher-v3/prep/**` 与 `/api/research-vnext/**`
 *   一律透传到真实 8100 / 8300，携带真实 JWT。
 * - 唯一被拦截的是**统一身份平台（8000）的外壳噪声端点**：
 *   `/api/auth/token/refresh`（返回真实教师 JWT）、`/api/auth/me`（返回教师身份），
 *   以及 `/api/tasks`、`/api/notifications*`、`/api/student/*`、`/api/agent/conversations`
 *   —— 这些端点不认识教师平台的 JWT 会返回 401，而 `client.js` 的全局 401 处理器
 *   会把用户踢回 /login，导致**根本看不到 surface**。它们被替换为诚实的空响应，
 *   **不注入任何业务数据**。
 *
 * 运行：node test/visual/wpk/probe.mjs [baseUrl]
 */
import { chromium } from 'playwright'
import { writeFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:5176'
const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-screenshots'

async function teacherToken() {
  const res = await fetch('http://127.0.0.1:8100/api/teacher-v3/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'zhanglaoshi', password: 'teacher123' }),
  })
  if (!res.ok) throw new Error(`teacher login failed: ${res.status}`)
  const body = await res.json()
  if (!body?.data?.token) throw new Error('teacher login: no token in envelope')
  return body.data.token
}

const TEACHER_IDENTITY = {
  id: '885ad9ac-0c36-5823-88e1-4dd76f6433e5',
  nickname: '张老师',
  active_role: 'teacher',
  identity_status: 'approved',
  roles: [{ role: 'teacher', status: 'approved' }],
}

/** 外壳噪声端点 → 诚实空响应（不是业务数据）。按 pathname 精确前缀匹配，
 *  绝不能用 glob 通配符：那会连 Vite 的 `/src/api/*.js` 模块脚本一起吃掉。 */
const SHELL_PREFIXES = ['/api/tasks', '/api/notifications', '/api/student/growth/', '/api/agent/conversations']
const isShellNoise = (url) => {
  try { return SHELL_PREFIXES.some((p) => new URL(url).pathname.startsWith(p)) } catch { return false }
}

const ROUTES = [
  ['today', '/teacher-v3/today'],
  ['prep-home', '/teacher-v3/prep'],
  ['prep-templates', '/teacher-v3/prep-templates'],
  ['prep-immersive-empty', '/teacher-v3/prep-immersive'],
  ['prep-immersive-missing-session', '/teacher-v3/prep-immersive/00000000-0000-0000-0000-0000000000ff'],
  ['research-immersive-empty', '/research-vnext/session'],
  ['research-immersive-missing-session', '/research-vnext/session/00000000-0000-0000-0000-0000000000ff'],
]

async function main() {
  mkdirSync(OUT, { recursive: true })
  const token = await teacherToken()
  const browser = await chromium.launch()
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })

  for (const pattern of [
    (url) => url.pathname === '/api/auth/token/refresh',
  ]) {
    await context.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: { access_token: token } }) }),
    )
  }
  await context.route((url) => url.pathname === '/api/auth/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: TEACHER_IDENTITY }) }),
  )
  await context.route((url) => isShellNoise(url.href), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: { items: [], unread: 0 } }) }),
  )

  const notes = []
  for (const [name, path] of ROUTES) {
    const page = await context.newPage()
    const consoleErrors = []
    const apiCalls = []
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)) })
    page.on('response', (r) => {
      let u
      try { u = new URL(r.url()) } catch { return }
      if (u.pathname.startsWith('/api/')) apiCalls.push(`${r.status()} ${r.request().method()} ${u.pathname}`)
    })
    let gotoError = null
    try {
      await page.goto(`${BASE}${path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    } catch (e) { gotoError = e.message }
    await page.waitForTimeout(3500)
    const info = await page.evaluate(() => {
      const vis = (sel) => { const el = document.querySelector(sel); return !!el && el.getBoundingClientRect().height > 0 }
      const txt = (sel) => { const el = document.querySelector(sel); return el ? (el.textContent || '').trim().slice(0, 500) : null }
      return {
        url: location.pathname + location.search,
        bodyText: (document.body.innerText || '').trim().slice(0, 2500),
        testids: [...document.querySelectorAll('[data-testid]')].map((e) => e.getAttribute('data-testid')).slice(0, 80),
        prepShell: vis('[data-testid="prep-focus-shell"]'),
        prepConversation: vis('[data-testid="prep-conversation-stage"]'),
        prepPrimaryError: txt('[data-testid="prep-primary-error"]'),
        rvSurface: vis('[data-testid="rv08-research-surface"]'),
        rvConversation: vis('[data-testid="rv08-research-conversation"]'),
        scrollH: document.documentElement.scrollHeight,
        viewportH: window.innerHeight,
      }
    })
    await page.screenshot({ path: `${OUT}/probe-${name}.png`, fullPage: true })
    notes.push({ name, path, gotoError, ...info, consoleErrors: [...new Set(consoleErrors)].slice(0, 8), apiCalls: [...new Set(apiCalls)] })
    await page.close()
  }

  await browser.close()
  writeFileSync(`${OUT}/probe.json`, JSON.stringify(notes, null, 2), 'utf8')
  for (const n of notes) {
    console.log(`\n=== ${n.name} (${n.path}) -> ${n.url}  gotoError=${n.gotoError}`)
    console.log(`  prepShell=${n.prepShell} prepConversation=${n.prepConversation} prepErr=${JSON.stringify(n.prepPrimaryError)} rvSurface=${n.rvSurface} rvConversation=${n.rvConversation} scrollH=${n.scrollH} vh=${n.viewportH}`)
    console.log(`  api: ${JSON.stringify(n.apiCalls)}`)
    console.log(`  consoleErrors: ${JSON.stringify(n.consoleErrors)}`)
    console.log(`  body: ${JSON.stringify((n.bodyText || '').slice(0, 700))}`)
  }
}

main().catch((e) => { console.error(e); process.exit(1) })
