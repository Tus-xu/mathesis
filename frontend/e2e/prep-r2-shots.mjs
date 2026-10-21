/**
 * 备课中心 R2 交付截图器（开发用；不属于产品代码）。
 *
 * usage: node e2e/prep-r2-shots.mjs <sessionId> <outDir> [--gen-out <file>]
 *
 * 口径：1672×941 / DPR=1，与 prep-shot.mjs 一致；每张打印 screenshot ms。
 * 状态：
 *   01 首屏（会话条折叠）  02 会话条折叠特写（滚动到时间轴）  03「查看全部对话」展开
 *   04 节点 02 展开卡       05 全滚动到底（底部安全区）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const sessionId = process.argv[2]
const outDir = process.argv[3]
if (!sessionId || !outDir) {
  console.error('usage: node e2e/prep-r2-shots.mjs <sessionId> <outDir>')
  process.exit(2)
}
fs.mkdirSync(outDir, { recursive: true })

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
  for (let attempt = 0; attempt < 2; attempt++) {
    const chRes = await req.post(`${base}/api/auth/challenges/sms`, { data: { phone, purpose: 'login' }, timeout: 20000 })
    const ch = (await chRes.json())?.data || {}
    if (!ch.demo_code) {
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

async function shot(page, file) {
  const t0 = Date.now()
  await page.screenshot({ path: file, animations: 'disabled', timeout: 45000 })
  console.log(`[shot] ${file.split(/[\\/]/).pop()} ms=${Date.now() - t0}`)
}

try {
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 160))
  })
  page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 160)}`))

  const url = `${base}/teacher-v3/prep/w/${sessionId}`
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(1500)
  if (page.url().includes('/login')) {
    await smsLogin()
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForTimeout(2500)
  }
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(6000)
  await ctx.storageState({ path: STATE }).catch(() => {})

  // 01 首屏（会话条折叠 + 默认展开节点）
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(400)
  await shot(page, `${outDir}/classroom-01.png`)

  // 02「查看全部对话」展开（会话条滚动区 + 紧凑蓝图卡 + 证据卡 2 行摘要）
  await page.getByRole('button', { name: /查看全部对话/ }).first().click()
  await page.waitForTimeout(700)
  const scrollToCards = () =>
    page.evaluate(() => {
      const el = document.querySelector('.pc-conv__scroll')
      const card = Array.from(document.querySelectorAll('.pc-msg')).find((m) => m.innerText.includes('待确认'))
      if (el && card) el.scrollTop = Math.max(0, card.offsetTop - 20)
    })
  await scrollToCards()
  await page.waitForTimeout(400)
  await shot(page, `${outDir}/classroom-02-conv-expanded.png`)

  // 03 证据卡「展开全文」：真实按钮 + 会话条放开折叠高度（内容不被裁切）
  const expandAll = page.getByRole('button', { name: /展开全文/ }).first()
  if (await expandAll.count()) {
    await expandAll.click()
    await page.waitForTimeout(700)
    await scrollToCards()
    await page.waitForTimeout(400)
    await shot(page, `${outDir}/classroom-03-evidence-expanded.png`)
    await page.getByRole('button', { name: '收起', exact: true }).first().click()
    await page.waitForTimeout(300)
  }
  await page.getByRole('button', { name: /收起对话/ }).first().click()
  await page.waitForTimeout(400)

  // 04 展开「02」节点：验证展开卡右栏两卡完整、且不被底部 AI 条遮挡
  await page.locator('[data-node-index="1"] .pc-node').click()
  await page.waitForTimeout(800)
  await page.evaluate(() => {
    const row = document.querySelector('[data-node-index="1"]')
    if (row) window.scrollTo(0, Math.max(0, row.getBoundingClientRect().bottom + window.scrollY - window.innerHeight + 190))
  })
  await page.waitForTimeout(400)
  await shot(page, `${outDir}/classroom-04-node02-expanded.png`)

  // 05 全滚动到底：底部安全区（展开卡最低点 vs 底部 AI 条）
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await page.waitForTimeout(500)
  await shot(page, `${outDir}/classroom-05-scroll-bottom.png`)

  const bottom = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('.pc-expand'))
    const pill = document.querySelector('.pc-composer__pill')
    const low = cards.length ? Math.round(Math.max(...cards.map((el) => el.getBoundingClientRect().bottom))) : null
    const pillTop = pill ? Math.round(pill.getBoundingClientRect().top) : null
    return { expandBottom: low, composerPillTop: pillTop, safeArea: low === null || pillTop === null ? null : pillTop - low }
  })
  console.log('[shot] bottom-safe-area:', JSON.stringify(bottom))
  if (errors.length) console.log('[shot] console-errors:', JSON.stringify(errors.slice(0, 6)))
} catch (e) {
  console.error('[shots] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}