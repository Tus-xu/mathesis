/**
 * 备课中心「对话抽屉」截图器（开发验收用；不属于产品代码）。
 *
 * usage: node e2e/prep-conv-drawer-shot.mjs <url-path> <out.png> [waitMs] [--full]
 *
 * 行为：复用 e2e/prep-shot.mjs 的登录/storageState 约定，打开页面后点击顶栏「对话」
 * 入口，等 800ms（抽屉动画 + 消息流滚动到最新）后截图。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const target = process.argv[2]
const out = process.argv[3]
const waitMs = Number(process.argv[4] || 6000)
const fullPage = process.argv.includes('--full')

if (!target || !out) {
  console.error('usage: node e2e/prep-conv-drawer-shot.mjs <url> <out.png> [waitMs] [--full]')
  process.exit(2)
}

const url = target.startsWith('http') ? target : `${base}${target}`
const STATE = path.resolve(__dirname, '..', '..', '.tmp', 'pw_state.json')

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1672, height: 941 },
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const req = ctx.request

async function smsLogin() {
  const chRes = await req.post(`${base}/api/auth/challenges/sms`, {
    data: { phone, purpose: 'login' },
    timeout: 20000,
  })
  const ch = (await chRes.json())?.data || {}
  if (!ch.demo_code) throw new Error(`challenge failed: ${chRes.status()}`)
  const loginRes = await req.post(`${base}/api/auth/login/sms`, {
    data: { phone, code: ch.demo_code, challenge_id: ch.challenge_id, remember: false },
    timeout: 20000,
  })
  const login = (await loginRes.json())?.data || {}
  if (!login.access_token) throw new Error(`login failed: ${loginRes.status()}`)
  await req.post(`${base}/api/auth/role/switch`, {
    data: { role: 'teacher' },
    headers: { Authorization: `Bearer ${login.access_token}` },
    timeout: 20000,
  })
}

try {
  const page = await ctx.newPage()
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(1500)
  if (page.url().includes('/login')) {
    await smsLogin()
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForTimeout(2500)
  }
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(waitMs)

  // 顶栏「对话」入口（与「资料」「历史」同构的 .pc-header__tool）
  const entry = page.locator('.pc-header__tool', { hasText: '对话' }).first()
  await entry.click({ timeout: 15000 })
  await page.waitForTimeout(800)
  await ctx.storageState({ path: STATE }).catch(() => {})

  const drawerText = await page.evaluate(() => (document.querySelector('.pc-convdrawer')?.innerText || '').slice(0, 400))
  // 命中测试（非破坏性）：抽屉中心点的最上层元素必须仍在抽屉内 → 证明消息流未被遮罩盖住、可交互
  const hit = await page.evaluate(() => {
    const drawer = document.querySelector('.pc-convdrawer')
    if (!drawer) return { drawer: false }
    const r = drawer.getBoundingClientRect()
    const top = document.elementFromPoint(Math.round(r.x + r.width / 2), Math.round(r.y + 300))
    const stream = document.querySelector('.pc-convdrawer .pc-conv__scroll')
    const sel = document.querySelector('.pc-convdrawer .pc-selcard__opt')
    return {
      drawer: true,
      drawerRect: { w: Math.round(r.width), h: Math.round(r.height) },
      topInsideDrawer: !!top && drawer.contains(top),
      topCls: top ? String(top.className).slice(0, 60) : null,
      streamScrollable: stream ? stream.scrollHeight > stream.clientHeight : null,
      messages: document.querySelectorAll('.pc-convdrawer .pc-msg').length,
      selectionCards: document.querySelectorAll('.pc-convdrawer .pc-selcard').length,
      selectionOptions: document.querySelectorAll('.pc-convdrawer .pc-selcard__opt').length,
      selectionDisabled: sel ? sel.disabled : null,
    }
  })
  console.log('[shot] drawer hit-test:', JSON.stringify(hit))
  const t0 = Date.now()
  await page.screenshot({ path: out, fullPage, animations: 'disabled', timeout: 45000 })
  console.log('[shot] screenshot ms:', Date.now() - t0)
  console.log('[shot] saved:', out)
  console.log('[shot] drawer text:', JSON.stringify(drawerText))
} catch (e) {
  console.error('[shot] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}