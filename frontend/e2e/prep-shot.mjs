/**
 * 备课中心视觉验收截图器（开发用；不属于产品代码）。
 *
 * usage:
 *   node e2e/prep-shot.mjs <url-path-or-absolute> <out.png> [waitMs] [--full]
 *
 * 行为：
 *   1) 用同源 API 完成教师登录（短信演示码 + 角色切换为 teacher），cookie 自动进 context；
 *   2) 以黄金图口径 1672×941 / DPR=1 打开目标页；
 *   3) 等待 networkidle + 额外等待（默认 6s，SSE/生成类页面可调大）；
 *   4) 截图到 out.png，并打印页面标题/URL 与关键文本片段（便于快速自查）。
 *
 * 环境变量：E2E_BASE_URL（默认 http://127.0.0.1:5176）、PREP_PHONE（默认 13900001001）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const target = process.argv[2]
const out = process.argv[3]
const waitMs = Number(process.argv[4] || 6000)
const fullPage = process.argv.includes('--full')

if (!target || !out) {
  console.error('usage: node e2e/prep-shot.mjs <url> <out.png> [waitMs] [--full]')
  process.exit(2)
}

const url = target.startsWith('http') ? target : `${base}${target}`

const browser = await chromium.launch({ headless: true })
const STATE = 'D:/math-arena/tmp/pw_state.json'
const hasState = fs.existsSync(STATE)
const ctx = await browser.newContext({
  viewport: { width: 1672, height: 941 },
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  ...(hasState ? { storageState: STATE } : {}),
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
      if (chRes.status() === 429 && attempt === 0) {
        console.warn('[shot] sms rate-limited, wait 62s ...')
        await new Promise((r) => setTimeout(r, 62000))
        continue
      }
      throw new Error(`challenge failed: ${chRes.status()} ${JSON.stringify(ch)}`)
    }
    const loginRes = await req.post(`${base}/api/auth/login/sms`, {
      data: { phone, code: ch.demo_code, challenge_id: ch.challenge_id, remember: false },
      timeout: 20000,
    })
    const login = (await loginRes.json())?.data || {}
    if (!login.access_token) throw new Error(`login failed: ${loginRes.status()}`)
    const swRes = await req.post(`${base}/api/auth/role/switch`, {
      data: { role: 'teacher' },
      headers: { Authorization: `Bearer ${login.access_token}` },
      timeout: 20000,
    })
    if (swRes.status() >= 400) console.warn('[shot] role switch failed', swRes.status())
    return
  }
  throw new Error('smsLogin exhausted')
}

try {
  const page = await ctx.newPage()
  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 200))
  })
  page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 200)}`))

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(1500)
  if (page.url().includes('/login')) {
    console.log('[shot] not authenticated, performing sms login ...')
    await smsLogin()
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForTimeout(2500)
  }
  await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
  await page.waitForTimeout(waitMs)

  // PREP_CLICK_TEXT / PREP_WAIT_DONE_MS：真实点击某个入口并等待其作业收敛（如「充实课件内容」）。
  // 用于验收「点击 → 真实进度 → 真实结果」链路，而不是只看静态首屏。
  if (process.env.PREP_CLICK_TEXT) {
    const label = process.env.PREP_CLICK_TEXT
    try {
      await page.getByText(label, { exact: false }).first().click({ timeout: 15000 })
      console.log('[shot] clicked:', label)
    } catch (e) {
      console.log('[shot] click FAILED:', label, String(e?.message || e).slice(0, 160))
    }
    const extra = Number(process.env.PREP_WAIT_DONE_MS || 60000)
    await page.waitForTimeout(extra)
  }

  // PREP_SCROLL_BOTTOM=1：滚到页面底部后截图，用于验收「底部安全区」——
  // 最后一屏内容（最后一个教学环节 / 教案表格末行）是否被浮动 AI 条遮挡（真实可达性）。
  // PREP_GEO=1：额外打印几何（文档高度/滚动条/末行/AI 条包围盒），作为遮挡的数值证据。
  if (process.env.PREP_SCROLL_BOTTOM || process.env.PREP_GEO) {
    const geo = await page.evaluate(async () => {
      const rect = (el) => {
        if (!el) return null
        const r = el.getBoundingClientRect()
        return { y: Math.round(r.y), h: Math.round(r.height), bottom: Math.round(r.bottom), x: Math.round(r.x), w: Math.round(r.width) }
      }
      const ta = document.querySelector('textarea')
      let bar = ta
      for (let i = 0; i < 5 && bar?.parentElement; i += 1) bar = bar.parentElement
      const rows = [...document.querySelectorAll('tr, li, div')].filter((el) => /^\d{2}\s*\S/.test(el.innerText || ''))
      const tableRows = [...document.querySelectorAll('tr')]
      window.scrollTo(0, document.scrollingElement.scrollHeight)
      // 有些页（教案）的滚动发生在内层容器：一并滚到底，保证「末行可达」的验收是真的。
      const inner = [...document.querySelectorAll('div')]
        .filter((el) => el.scrollHeight > el.clientHeight + 40 && /auto|scroll/.test(getComputedStyle(el).overflowY))
        .sort((a, b) => b.scrollHeight - a.scrollHeight)[0]
      if (inner) inner.scrollTop = inner.scrollHeight
      await new Promise((r) => setTimeout(r, 400))
      return {
        viewportH: window.innerHeight,
        docScrollH: document.scrollingElement.scrollHeight,
        scrolledTo: Math.round(window.scrollY),
        innerScroller: inner ? { cls: String(inner.className).slice(0, 50), sh: inner.scrollHeight, ch: inner.clientHeight, top: Math.round(inner.scrollTop) } : null,
        lastTimelineRow: rect(rows.at(-1) || null),
        lastTimelineText: (rows.at(-1)?.innerText || '').slice(0, 40),
        lastTableRow: rect(tableRows.at(-1) || null),
        aiBarCls: String(bar?.className || '').slice(0, 60),
        aiBar: rect(bar?.parentElement || bar),
        // 全部 composer 实例（排查重复渲染 / 重叠）
        composers: [...document.querySelectorAll('[class*="pc-composer"]')]
          .filter((el) => /pc-composer(__pill)?$/.test(el.className.toString().split(' ')[0] || ''))
          .map((el) => ({ cls: el.className.toString().slice(0, 40), ...rect(el), fixed: getComputedStyle(el).position })),
      }
    })
    console.log('[shot] geometry:', JSON.stringify(geo))
    await page.waitForTimeout(500)
  }

  await ctx.storageState({ path: STATE }).catch(() => {})
  const t0 = Date.now()
  await page.screenshot({ path: out, fullPage, animations: 'disabled', timeout: 45000 })
  console.log('[shot] screenshot ms:', Date.now() - t0)
  const info = await page.evaluate(() => ({
    url: location.href,
    title: document.title,
    text: document.body.innerText.replace(/\n{2,}/g, '\n').slice(0, 1200),
  }))
  console.log('[shot] saved:', out)
  console.log('[shot] url:', info.url)
  console.log('[shot] text:', JSON.stringify(info.text))
  if (errors.length) console.log('[shot] console-errors:', JSON.stringify(errors.slice(0, 6)))

  fs.mkdirSync('D:/math-arena/tmp', { recursive: true })
  await ctx.storageState({ path: 'D:/math-arena/tmp/pw_state.json' }).catch(() => {})
} catch (e) {
  console.error('[shot] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}