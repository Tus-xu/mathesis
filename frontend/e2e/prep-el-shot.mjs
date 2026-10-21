/**
 * 备课中心「单元素放大截图」器（开发验收用；不属于产品代码）。
 *
 * usage: node e2e/prep-el-shot.mjs <url-path> <out.png> <css-selector> [waitMs]
 *
 * 行为：登录后打开页面 → 等待渲染 → 把目标元素滚入视野 → 以 DPR=2 对该元素截图
 * （放大看得清封面占位/文字换行细节）。
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
const selector = process.argv[4]
const waitMs = Number(process.argv[5] || 6000)

if (!target || !out || !selector) {
  console.error('usage: node e2e/prep-el-shot.mjs <url> <out.png> <selector> [waitMs]')
  process.exit(2)
}

const url = target.startsWith('http') ? target : `${base}${target}`
const STATE = path.resolve(__dirname, '..', '..', '.tmp', 'pw_state.json')

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1672, height: 941 },
  deviceScaleFactor: 2,
  locale: 'zh-CN',
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const req = ctx.request

async function smsLogin() {
  for (let attempt = 0; attempt < 2; attempt++) {
    const chRes = await req.post(`${base}/api/auth/challenges/sms`, {
      data: { phone, purpose: 'login' },
      timeout: 20000,
    })
    const ch = (await chRes.json())?.data || {}
    if (!ch.demo_code) {
      // 短信通道限流（AUTH_RATE_LIMITED）：等待窗口后重试一次（与 prep-shot.mjs 同口径）
      if (chRes.status() === 429 && attempt === 0) {
        console.warn('[shot] sms rate-limited, wait 62s ...')
        await new Promise((r) => setTimeout(r, 62000))
        continue
      }
      throw new Error(`challenge failed: ${chRes.status()}`)
    }
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
    return
  }
  throw new Error('smsLogin exhausted')
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

  const el = page.locator(selector).first()
  await el.scrollIntoViewIfNeeded({ timeout: 15000 })
  await page.waitForTimeout(600)
  const box = await el.boundingBox()
  const coverBox = await page.evaluate(
    (sel) => {
      const node = document.querySelector(sel)
      const cover = node?.querySelector('.pc-evidence__cover')
      const name = node?.querySelector('.pc-evidence__cover-name')
      if (!cover) return null
      const r = cover.getBoundingClientRect()
      const nr = name?.getBoundingClientRect()
      return {
        cover: { w: Math.round(r.width), h: Math.round(r.height) },
        name: nr ? { w: Math.round(nr.width), h: Math.round(nr.height) } : null,
        coverBg: getComputedStyle(cover).backgroundColor,
        nameFont: name ? getComputedStyle(name).fontSize : null,
        nameLineClamp: name ? getComputedStyle(name).webkitLineClamp : null,
        nameNowrap: name ? getComputedStyle(name).whiteSpace : null,
        nameTextOverflow: name ? getComputedStyle(name).textOverflow : null,
        nameText: name?.textContent ?? null,
      }
    },
    selector,
  )
  console.log('[shot] element box:', JSON.stringify(box))
  console.log('[shot] cover metrics:', JSON.stringify(coverBox))
  const t0 = Date.now()
  await page.screenshot({ path: out, clip: box, animations: 'disabled', timeout: 45000 })
  console.log('[shot] screenshot ms:', Date.now() - t0)
  console.log('[shot] saved:', out)
} catch (e) {
  console.error('[shot] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}