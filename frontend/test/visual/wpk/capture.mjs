/**
 * WP-K · 视觉验收截图套件（真实浏览器 / 真实后端 / 真实 token）
 *
 * 用法：node test/visual/wpk/capture.mjs <prefix>
 *   产出：$OUT/<prefix><state>-<viewport>.png + $OUT/<prefix>manifest.json
 *
 * 诚实边界（同样写入 WP-K-VISUAL-QA.md §0）：
 * - 业务数据全部真实：`/api/teacher-v3/**`（8100）与 `/api/research-vnext/**`（8300）透传真实后端 + 真实 JWT。
 * - 只有三类东西是测试注入，且都不是业务数据：
 *   1) 身份：`/api/auth/token/refresh` 注入**真实教师 JWT**（来自 8100 dev seed 登录），
 *      `/api/auth/me` 注入身份对象——本机没有统一平台的教师账号密码，无法走真实登录；
 *   2) 外壳噪声：`/api/tasks`、`/api/notifications*`、`/api/student/growth/*`、
 *      `/api/agent/conversations` 返回诚实空响应。原因：这些 8000 端点不认教师平台 JWT 会 401，
 *      而 client.js 的全局 401 处理器会把人踢回 /login，导致 surface 根本看不到；
 *   3) 无。
 * - 任何截图的"状态"都在 manifest 里逐张写明；空态/错误态一律标注为 empty/error，
 *   **绝不当作成功态**。
 */
import { chromium } from 'playwright'
import { writeFileSync, mkdirSync } from 'node:fs'

const PREFIX = process.argv[2] || 'shot-'
const BASE = process.env.WPK_BASE || 'http://127.0.0.1:5176'
const OUT = 'D:/math-arena/deliverables/vnext-autonomous/evidence/WP-K-screenshots'
const FIXTURES = 'D:/frontend/test/visual/wpk/fixtures'

const VIEWPORTS = [
  { name: '1280x800', width: 1280, height: 800 },
  { name: '1440x900', width: 1440, height: 900 },
  { name: '1920x1080', width: 1920, height: 1080 },
]

async function tokenFor(role) {
  const res = await fetch('http://127.0.0.1:8100/api/teacher-v3/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'zhanglaoshi', password: 'teacher123' }),
  })
  if (!res.ok) throw new Error(`login failed ${res.status}`)
  return (await res.json()).data.token
}

const IDENTITIES = {
  teacher: {
    id: '885ad9ac-0c36-5823-88e1-4dd76f6433e5', nickname: '张老师', active_role: 'teacher',
    identity_status: 'approved', roles: [{ role: 'teacher', status: 'approved' }],
  },
  researcher: {
    id: '885ad9ac-0c36-5823-88e1-4dd76f6433e5', nickname: '张老师', active_role: 'researcher',
    identity_status: 'approved', roles: [{ role: 'researcher', status: 'approved' }],
  },
}

const SHELL_PREFIXES = ['/api/tasks', '/api/notifications', '/api/student/growth/', '/api/agent/conversations']
const isShellNoise = (url) => {
  try { return SHELL_PREFIXES.some((p) => new URL(url).pathname.startsWith(p)) } catch { return false }
}

async function makeContext(browser, token, role, viewport) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
  await context.route((url) => url.pathname === '/api/auth/token/refresh', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: { access_token: token } }) }),
  )
  await context.route((url) => url.pathname === '/api/auth/me', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: IDENTITIES[role] }) }),
  )
  await context.route((url) => isShellNoise(url.href), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 0, message: 'ok', data: { items: [], unread: 0 } }) }),
  )
  return context
}

/** state 定义：route + 可选 drive()（真实交互） + 说明 */
const STATES = [
  {
    id: 'teacher-today', role: 'teacher', path: '/teacher-v3/today',
    shows: 'TodayView 真实态：真实课表/待办/最近工作（真实 8100 数据），并作为 Q-3 导航验证页',
  },
  {
    id: 'teacher-prep-home', role: 'teacher', path: '/teacher-v3/prep',
    shows: '备课中心首页：真实最近备课列表（来自 8100 /prep 与 /plans）',
  },
  {
    id: 'teacher-prep-templates-blueprint', role: 'teacher', path: '/teacher-v3/prep-templates',
    shows: '教案模板库 →「优质课蓝本」Tab（Q-1 取证点：此处的学校/教师/奖项是否被编造）',
    drive: async (page) => {
      await page.getByTestId('ailp-tpl-tab-blueprint').click()
      await page.locator('[data-testid="ailp-tpl-tab-blueprint"], [data-testid="ailp-blueprints-empty"], [data-testid^="ailp-bp-"]').first().waitFor({ state: 'visible', timeout: 10000 })
      await page.waitForTimeout(900)
    },
  },
  {
    id: 'teacher-prep-templates-refs', role: 'teacher', path: '/teacher-v3/prep-templates',
    shows: '教案模板库 →「资源库引用」Tab（Q-1 取证点：此处的文件名/日期/大小是否被编造）',
    drive: async (page) => {
      await page.getByTestId('ailp-tpl-tab-refs').click()
      await page.locator('[data-testid="ailp-refs-empty"], [data-testid^="ailp-ref-"]').first().waitFor({ state: 'visible', timeout: 10000 })
      await page.waitForTimeout(900)
    },
  },
  {
    id: 'teacher-prep-upload-parse', role: 'teacher', path: '/teacher-v3/prep',
    shows: '上传资料备课：上传一个本地文件后的「AI 解析预览」三 Tab（Q-1 取证点：解析结论/置信度是否被编造）',
    drive: async (page) => {
      await page.getByTestId('ailp-card-upload').click()
      await page.waitForTimeout(800)
      const input = page.locator('input[type="file"]').first()
      await input.setInputFiles(`${FIXTURES}/椭圆优秀教案参考.docx`)
      await page.waitForTimeout(800)
      const tabs = page.locator('.pup-tab')
      if (await tabs.count() >= 2) { await tabs.nth(1).click(); await page.waitForTimeout(300) }
    },
  },
  {
    id: 'teacher-prep-immersive-empty', role: 'teacher', path: '/teacher-v3/prep-immersive',
    shows: '教师沉浸式 surface · 新建会话空态（无 session id）：真实空态 + 服务端动作缺失时的诚实说明',
  },
  {
    id: 'teacher-prep-immersive-missing-session', role: 'teacher',
    path: '/teacher-v3/prep-immersive/00000000-0000-0000-0000-0000000000ff',
    shows: '教师沉浸式 surface · 404 错误态（真实 8100 返回 404），注意 request_id 字段渲染的是什么',
  },
  {
    id: 'research-immersive-empty', role: 'researcher', path: '/research-vnext/session',
    shows: '科研沉浸式 surface · 未选择会话（snapshot=null）空态',
  },
  {
    id: 'research-immersive-missing-session', role: 'researcher',
    path: '/research-vnext/session/00000000-0000-0000-0000-0000000000ff',
    shows: '科研沉浸式 surface · 后续端点 401（真实 8300：缺少网关注入的 AccessContext 签名）→ 能力不可用态',
  },
  {
    id: 'teacher-prep-immersive-narrow', role: 'teacher', path: '/teacher-v3/prep-immersive',
    narrow: { width: 760, height: 800 },
    shows: '响应式折叠验证：760px 窄屏下 conversation 是否仍是主surface（不得退化为纯表单）',
  },
  {
    id: 'research-immersive-narrow', role: 'researcher', path: '/research-vnext/session',
    narrow: { width: 760, height: 800 },
    shows: '响应式折叠验证：760px 窄屏下科研 conversation 是否仍是主surface',
  },
  {
    id: 'teacher-recent-nav', role: 'teacher', path: '/teacher-v3/today',
    shows: 'Q-3 导航取证：点击「最近工作」第一项后**真的跳转**（URL 变成 /teacher-v3/prep?plan=… 或 ?deck=…）',
    drive: async (page) => {
      await page.waitForTimeout(1500)
      const first = page.locator('[data-testid^="tv3-recent-"]').first()
      if (await first.count()) { await first.click(); await page.waitForTimeout(1800) }
    },
  },
  {
    id: 'teacher-slides-migration-notice', role: 'teacher', path: '/teacher-v3/slides',
    shows: '旧课件工坊兼容壳的一次性迁移提示（role=status / aria-live=polite，可关闭）',
    drive: async (page) => { await page.waitForTimeout(1500) },
  },
]

async function run() {
  mkdirSync(OUT, { recursive: true })
  const token = await tokenFor('teacher')
  const browser = await chromium.launch()
  const manifest = []

  for (const state of STATES) {
    const viewports = state.narrow ? [{ name: `${state.narrow.width}x${state.narrow.height}`, ...state.narrow }] : VIEWPORTS
    for (const vp of viewports) {
      const context = await makeContext(browser, token, state.role, vp)
      const page = await context.newPage()
      const consoleErrors = []
      const api = []
      page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)) })
      page.on('response', (r) => { try { const u = new URL(r.url()); if (u.pathname.startsWith('/api/')) api.push(`${r.status()} ${u.pathname}`) } catch { /* ignore */ } })
      let driveError = null
      try {
        await page.goto(`${BASE}${state.path}`, { waitUntil: 'domcontentloaded', timeout: 30000 })
        await page.waitForTimeout(3000)
        if (state.drive) await state.drive(page)
      } catch (e) { driveError = e.message }
      await page.waitForTimeout(600)

      const probe = await page.evaluate(() => {
        const root = document.querySelector('[data-testid="prep-focus-shell"]') || document.querySelector('[data-testid="rv08-research-surface"]') || document.body
        const cs = root ? getComputedStyle(root) : null
        // 主列宽度（阅读宽度测量）
        const col = document.querySelector('.stage__column') || document.querySelector('.conversation')
        let colWidth = null, colChars = null
        if (col) {
          const r = col.getBoundingClientRect()
          colWidth = Math.round(r.width)
          const fs = parseFloat(getComputedStyle(col).fontSize) || 16
          colChars = Math.round(r.width / (fs * 0.55))
        }
        const shellVisible = !!document.querySelector('[data-testid="prep-focus-shell"]')
        const globalNav = !!document.querySelector('nav[aria-label="教师工作台导航"], .tv3-nav, [data-testid="tv3-global-nav"]')
        return {
          url: location.pathname + location.search,
          bodyText: (document.body.innerText || '').trim().slice(0, 2200),
          shellVisible,
          globalNavVisible: globalNav,
          colWidth, colChars,
          scrollH: document.documentElement.scrollHeight,
          bodyBg: document.body ? getComputedStyle(document.body).backgroundColor : null,
          surfaceBg: cs ? cs.backgroundColor : null,
          focusableCount: document.querySelectorAll('a[href],button,input,textarea,select,[tabindex]:not([tabindex="-1"])').length,
        }
      })
      const file = `${OUT}/${PREFIX}${state.id}-${vp.name}.png`
      await page.screenshot({ path: file, fullPage: true })
      manifest.push({
        id: state.id, viewport: vp.name, role: state.role, route: state.path,
        finalUrl: probe.url, shows: state.shows,
        colWidth: probe.colWidth, colChars: probe.colChars,
        shellVisible: probe.shellVisible, globalNavVisible: probe.globalNavVisible,
        scrollH: probe.scrollH, focusableCount: probe.focusableCount,
        api: [...new Set(api)], consoleErrors: [...new Set(consoleErrors)].slice(0, 6),
        driveError, file, bodyText: probe.bodyText,
      })
      await context.close()
    }
  }
  await browser.close()
  writeFileSync(`${OUT}/${PREFIX}manifest.json`, JSON.stringify(manifest, null, 2), 'utf8')
  console.log(`captured ${manifest.length} screenshots -> ${OUT}`)
  for (const m of manifest) {
    console.log(`- ${m.id} @${m.viewport} url=${m.finalUrl} col=${m.colWidth}px~${m.colChars}ch nav=${m.globalNavVisible} err=${m.driveError || 'none'}`)
  }
}

run().catch((e) => { console.error(e); process.exit(1) })
