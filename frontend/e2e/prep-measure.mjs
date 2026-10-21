/**
 * 备课中心首屏版式实测器（开发用；不属于产品代码）。
 *
 * usage: node e2e/prep-measure.mjs <url-path> [--full]
 *
 * 输出：会话条高度 / 标题行与时间轴位置 / 首屏可见环节行数 / 展开卡与右栏卡矩形 /
 *      底部 AI 条矩形 / 全滚动到底后的底部安全区（最后内容最低点 vs 输入条顶边）。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const target = process.argv[2]
if (!target) {
  console.error('usage: node e2e/prep-measure.mjs <url>')
  process.exit(2)
}
const url = target.startsWith('http') ? target : `${base}${target}`

const browser = await chromium.launch({ headless: true })
const STATE = path.resolve(__dirname, '..', '..', '.tmp', 'pw_state.json')
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
    const chRes = await req.post(`${base}/api/auth/challenges/sms`, { data: { phone, purpose: 'login' }, timeout: 20000 })
    const ch = (await chRes.json())?.data || {}
    if (!ch.demo_code) {
      if (chRes.status() === 429 && attempt === 0) {
        console.warn('[measure] sms rate-limited, wait 62s ...')
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
  await page.waitForTimeout(Number(process.env.PREP_WAIT_MS || 6000))
  await ctx.storageState({ path: STATE }).catch(() => {})
  // 首屏口径归一：无论页面是否被自动滚动过，先回到顶部再测
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.waitForTimeout(300)

  const metrics = await page.evaluate(() => {
    const rect = (sel) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height), left: Math.round(r.left), width: Math.round(r.width) }
    }
    const rects = (sel) => Array.from(document.querySelectorAll(sel)).map((el) => {
      const r = el.getBoundingClientRect()
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height), left: Math.round(r.left), width: Math.round(r.width) }
    })
    const fold = window.innerHeight
    const timeline = rects('.pc-timeline__item')
    return {
      fold,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      conv: rect('.pc-conv'),
      convScroll: rect('.pc-conv__scroll'),
      head: rect('.pc-classroom__head'),
      timelineRows: timeline,
      rowsFullyVisible: timeline.filter((r) => r.bottom <= fold).length,
      expand: rect('.pc-expand'),
      expandLeft: rect('.pc-expand__left'),
      expandRight: rect('.pc-expand__right'),
      expandDuration: rect('.pc-expand__duration'),
      groupRows: rects('.pc-expand__left > *'),
      evidenceHead: rect('.pc-evidence__head'),
      evidenceCover: rect('.pc-evidence__cover'),
      evidenceMeta: rect('.pc-evidence__meta'),
      evidenceQuote: rect('.pc-evidence__quote'),
      quoteBox: (() => {
        const el = document.querySelector('.pc-evidence__quote')
        if (!el) return null
        const cs = getComputedStyle(el)
        return {
          boxSizing: cs.boxSizing,
          maxHeight: cs.maxHeight,
          lineHeight: cs.lineHeight,
          fontSize: cs.fontSize,
          padding: cs.padding,
          clientHeight: el.clientHeight,
          scrollHeight: el.scrollHeight,
          inlineLineHeight: (() => {
            const k = document.querySelector('.pc-evidence__quote .pc-katex')
            return k ? getComputedStyle(k).lineHeight : null
          })(),
          inlineFontSize: (() => {
            const k = document.querySelector('.pc-evidence__quote .pc-katex')
            return k ? getComputedStyle(k).fontSize : null
          })(),
          lineRects: (() => {
            const k = document.querySelector('.pc-evidence__quote .pc-katex')
            if (!k || !k.firstChild) return null
            const r = document.createRange()
            r.selectNodeContents(k)
            return Array.from(r.getClientRects()).map((x) => Math.round(x.height)).slice(0, 8)
          })(),
        }
      })(),
      refClip: (() => {
        const el = document.querySelector('.pc-evref__clip')
        if (!el) return null
        const cs = getComputedStyle(el)
        return {
          boxSizing: cs.boxSizing,
          maxHeight: cs.maxHeight,
          lineHeight: el.firstElementChild ? getComputedStyle(el.firstElementChild).lineHeight : null,
          fontSize: el.firstElementChild ? getComputedStyle(el.firstElementChild).fontSize : null,
          clientHeight: el.clientHeight,
          scrollHeight: el.scrollHeight,
        }
      })(),
      evidenceCard: rect('.pc-evidence'),
      hintsCard: rect('.pc-hints'),
      composerPill: rect('.pc-composer__pill'),
      composerBox: rect('.pc-composer'),
      materialsPill: rect('.pc-classroom__materials'),
      docHeight: document.documentElement.scrollHeight,
    }
  })

  // 全滚动到底：测底部安全区（最后一块真实内容最低点 vs 输入条顶边）
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await page.waitForTimeout(400)
  const bottom = await page.evaluate(() => {
    const lastRow = document.querySelector('.pc-timeline__item:last-child')
    const expand = document.querySelector('.pc-expand')
    const pill = document.querySelector('.pc-composer__pill')
    const box = document.querySelector('.pc-composer')
    const bottomOf = (el) => (el ? Math.round(el.getBoundingClientRect().bottom) : null)
    const topOf = (el) => (el ? Math.round(el.getBoundingClientRect().top) : null)
    const candidates = [lastRow, expand].filter(Boolean)
    const lowest = candidates.length ? Math.max(...candidates.map((el) => el.getBoundingClientRect().bottom)) : null
    const pillTop = topOf(pill)
    return {
      scrollY: Math.round(window.scrollY),
      scrollBottom: Math.round(window.scrollY + window.innerHeight),
      docHeight: document.documentElement.scrollHeight,
      lowestContentBottom: lowest === null ? null : Math.round(lowest),
      composerPillTop: pillTop,
      composerBoxTop: topOf(box),
      safeAreaVsPill: lowest === null || pillTop === null ? null : pillTop - Math.round(lowest),
      safeAreaVsBox: lowest === null ? null : topOf(box) - Math.round(lowest),
      viewportBottomGap: lowest === null ? null : window.innerHeight - Math.round(lowest),
    }
  })
  console.log(JSON.stringify({ metrics, bottom }, null, 2))
} catch (e) {
  console.error('[measure] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}