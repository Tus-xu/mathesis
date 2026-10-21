/**
 * 首屏可见环节行探针（开发用；不属于产品代码）。
 * usage: node e2e/prep-rows.mjs <sessionId>
 * 输出：逐个展开节点后，首屏内「至少 50% 可见」的环节行数量与展开卡高度。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const sessionId = process.argv[2]

const browser = await chromium.launch({ headless: true })
const STATE = path.resolve(__dirname, '..', '..', '.tmp', 'pw_state.json')
const ctx = await browser.newContext({
  viewport: { width: 1672, height: 941 },
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const req = ctx.request

async function smsLogin() {
  const chRes = await req.post(`${base}/api/auth/challenges/sms`, { data: { phone, purpose: 'login' }, timeout: 20000 })
  const ch = (await chRes.json())?.data || {}
  const loginRes = await req.post(`${base}/api/auth/login/sms`, {
    data: { phone, code: ch.demo_code, challenge_id: ch.challenge_id, remember: false },
    timeout: 20000,
  })
  const login = (await loginRes.json())?.data || {}
  await req.post(`${base}/api/auth/role/switch`, { data: { role: 'teacher' }, headers: { Authorization: `Bearer ${login.access_token}` }, timeout: 20000 })
}

const page = await ctx.newPage()
const url = `${base}/teacher-v3/prep/w/${sessionId}`
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
await page.waitForTimeout(1500)
if (page.url().includes('/login')) {
  await smsLogin()
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(2500)
}
await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
await page.waitForTimeout(5000)

const count = await page.locator('.pc-timeline__item').count()
for (let i = 0; i < count; i++) {
  await page.evaluate(() => window.scrollTo(0, 0))
  const already = await page.locator(`[data-node-index="${i}"] .pc-expand`).count()
  if (!already) {
    await page.locator(`[data-node-index="${i}"] .pc-node`).click()
    await page.waitForTimeout(700)
  }
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(300)
  const r = await page.evaluate(() => {
    const fold = window.innerHeight
    const rows = Array.from(document.querySelectorAll('.pc-timeline__item')).map((el) => {
      const b = el.getBoundingClientRect()
      const visible = Math.max(0, Math.min(b.bottom, fold) - Math.max(b.top, 0))
      return { top: Math.round(b.top), bottom: Math.round(b.bottom), height: Math.round(b.height), ratio: +(visible / b.height).toFixed(2) }
    })
    const exp = document.querySelector('.pc-expand')
    return {
      rows,
      half: rows.filter((x) => x.ratio >= 0.5).length,
      full: rows.filter((x) => x.ratio >= 0.98).length,
      expandHeight: exp ? Math.round(exp.getBoundingClientRect().height) : null,
      expandBottom: exp ? Math.round(exp.getBoundingClientRect().bottom) : null,
    }
  })
  console.log(`node ${i + 1} expanded → 半可见行=${r.half} 全可见行=${r.full} 展开卡高=${r.expandHeight} 底=${r.expandBottom}`)
  console.log('   rows:', r.rows.map((x) => `${x.top}-${x.bottom}(${x.ratio})`).join(' '))
}
await browser.close()