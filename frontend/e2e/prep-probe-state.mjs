/**
 * 备课工作台「状态探针」（开发用，非产品代码）：
 * 打开指定会话，输出会话卡/滚动区/空态卡的几何 + 计算样式 + AI 条禁用态 + 失败消息，
 * 用于定位「巨大空白卡」「发送被拒后变幽灵消息」这类版面/状态缺陷。
 *
 * usage: node e2e/prep-probe-state.mjs <sessionId> [tab]
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const sid = process.argv[2]
const tab = process.argv[3] || 'classroom'
const STATE = path.resolve(__dirname, '..', '..', '.tmp', 'pw_state.json')
if (!sid) {
  console.error('usage: node e2e/prep-probe-state.mjs <sessionId> [tab]')
  process.exit(2)
}

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 }, // 用户真机口径
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const page = await ctx.newPage()
const netlog = []
page.on('response', (r) => {
  if (r.url().includes('/prep/conversations') && r.request().method() === 'POST') {
    netlog.push(`${r.status()} POST ${r.url().split('/prep/conversations')[1]}`)
  }
})
page.on('console', (m) => {
  if (m.type() === 'error') netlog.push(`console: ${m.text().slice(0, 120)}`)
})
await page.goto(`${base}/teacher-v3/prep/w/${sid}/${tab}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await page.waitForTimeout(8000)

const dump = await page.evaluate(() => {
  const rect = (sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    return {
      y: Math.round(r.y),
      h: Math.round(r.height),
      w: Math.round(r.width),
      maxH: cs.maxHeight,
      minH: cs.minHeight,
      pad: cs.padding,
      gap: cs.gap,
      justify: cs.justifyContent,
    }
  }
  const msgs = [...document.querySelectorAll('.pc-msg')].map((el) => ({
    cls: String(el.className).slice(0, 70),
    h: Math.round(el.getBoundingClientRect().height),
    text: (el.innerText || '').replace(/\n+/g, ' / ').slice(0, 60),
  }))
  const composer = document.querySelector('.pc-composer__input')
  const sendBtn = document.querySelector('.pc-composer__send')
  const cardTitles = [...document.querySelectorAll('.pc-classroom__head, .pc-empty, [class*="empty"]')].map((e) => ({
    cls: String(e.className).slice(0, 50),
    text: (e.innerText || '').replace(/\n+/g, ' / ').slice(0, 80),
    ...(() => {
      const r = e.getBoundingClientRect()
      return { y: Math.round(r.y), h: Math.round(r.height) }
    })(),
  }))
  return {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    docScrollH: document.scrollingElement.scrollHeight,
    conv: rect('.pc-conv'),
    convScroll: rect('.pc-conv__scroll'),
    convBar: rect('.pc-conv__bar'),
    body: rect('.pc-workspace__body'),
    emptyState: cardTitles,
    messages: msgs,
    failedStrip: (() => {
      const el = document.querySelector('.pc-conv__failed')
      return el ? { text: (el.innerText || '').replace(/\n+/g, ' ').slice(0, 80), ...(() => {
        const r = el.getBoundingClientRect()
        return { y: Math.round(r.y), h: Math.round(r.height) }
      })() } : null
    })(),
    composer: composer ? { disabled: composer.disabled, placeholder: composer.placeholder } : null,
    sendDisabled: sendBtn ? sendBtn.disabled : null,
  }
})
console.log(JSON.stringify(dump, null, 1))
console.log('[net]', JSON.stringify(netlog.slice(-8)))
await browser.close()