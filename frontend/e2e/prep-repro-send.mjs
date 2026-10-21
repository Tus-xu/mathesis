/**
 * 「连发两条消息」真实链路复现/验收（开发用，非产品代码）。
 *
 * 忠实复现用户路径（2026-09-23 实测缺陷）：
 *   首页输入课题 → 自动建会话并跳工作台 → 教师紧接着在工作台**再发一条**
 *   → 此时服务端首条仍在处理（会话串行化）→ 409
 *   → 旧前端把该消息标为「失败」并弹红色「会话内容已更新，请重试本次操作。」
 *
 * usage:
 *   node e2e/prep-repro-send.mjs --from-home "<msg1>" "<msg2>" [gapMs]
 *   node e2e/prep-repro-send.mjs <sessionId> "<msg1>" "<msg2>" [gapMs]
 * 环境变量：PREP_OUT（截图前缀，默认 D:/math-arena/tmp/repro-send）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const args = process.argv.slice(2)
const fromHome = args[0] === '--from-home'
const sid = fromHome ? null : args[0]
const msg1 = (fromHome ? args[1] : args[1]) || '椭圆'
const msg2 = (fromHome ? args[2] : args[2]) || '椭圆的简单几何性质，第一课时'
const gap = Number((fromHome ? args[3] : args[3]) || 3000)
const out = process.env.PREP_OUT || 'D:/math-arena/tmp/repro-send'
const STATE = 'D:/math-arena/tmp/pw_state.json'

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  locale: 'zh-CN',
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const page = await ctx.newPage()
const posts = []
page.on('response', (r) => {
  if (r.request().method() === 'POST' && r.url().includes('/prep/conversations')) {
    posts.push({
      status: r.status(),
      url: r.url().split('/prep/conversations')[1],
      body: (r.request().postData() || '').slice(0, 140),
    })
  }
})

async function fillAndSend(textIdx, text) {
  const boxes = page.locator('textarea')
  const box = boxes.nth(textIdx)
  await box.waitFor({ state: 'visible', timeout: 20000 })
  await box.click({ timeout: 20000 })
  await box.fill(text)
  await box.press('Enter')
  console.log(`[repro] sent(${textIdx}): ${text}`)
}

/** 同 prep-shot.mjs 口径：演示短信码登录（令牌过期时自动补登，避免脚本假失败）。 */
async function smsLogin() {
  const req = ctx.request
  const phone = process.env.PREP_PHONE || '13900001001'
  for (let attempt = 0; attempt < 2; attempt++) {
    const ch = (await (await req.post(`${base}/api/auth/challenges/sms`, {
      data: { phone, purpose: 'login' }, timeout: 20000,
    })).json())?.data || {}
    if (!ch.demo_code) {
      if (attempt === 0) {
        console.warn('[repro] sms rate-limited, wait 62s ...')
        await new Promise((r) => setTimeout(r, 62000))
        continue
      }
      throw new Error('challenge failed')
    }
    const login = (await (await req.post(`${base}/api/auth/login/sms`, {
      data: { phone, code: ch.demo_code, challenge_id: ch.challenge_id, remember: false }, timeout: 20000,
    })).json())?.data || {}
    if (!login.access_token) throw new Error('login failed')
    await req.post(`${base}/api/auth/role/switch`, {
      data: { role: 'teacher' },
      headers: { Authorization: `Bearer ${login.access_token}` },
      timeout: 20000,
    })
    return
  }
}

async function gotoAuthed(url) {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await page.waitForTimeout(1500)
  if (page.url().includes('/login')) {
    console.log('[repro] not authenticated, performing sms login ...')
    await smsLogin()
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.waitForTimeout(2500)
  }
}

if (fromHome) {
  await gotoAuthed(`${base}/teacher-v3/prep`)
  await page.waitForTimeout(3000)
  await fillAndSend(0, msg1) // 首页输入 → 建会话 + 跳转
  await page.waitForURL(/\/prep\/w\//, { timeout: 60000 })
  console.log('[repro] navigated to', page.url())
  await page.waitForTimeout(gap)
} else {
  await gotoAuthed(`${base}/teacher-v3/prep/w/${sid}/classroom`)
  await page.waitForTimeout(5000)
  await fillAndSend(-1 /* last = 工作台 AI 条 */, msg1)
  await page.waitForTimeout(gap)
}

try {
  await fillAndSend(-1, msg2)
} catch (e) {
  console.log('[repro] 第二条无法输入（输入框被禁用）:', String(e?.message || e).slice(0, 120))
}

await page.waitForTimeout(4000)
await page.screenshot({ path: `${out}-t1.png`, animations: 'disabled' })
await page.waitForTimeout(22000)
await page.screenshot({ path: `${out}-t2.png`, animations: 'disabled' })

const dump = await page.evaluate(() => {
  const rect = (sel) => {
    const el = document.querySelector(sel)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { y: Math.round(r.y), h: Math.round(r.height) }
  }
  const strip = document.querySelector('.pc-conv__failed')
  const composer = document.querySelector('.pc-composer__input')
  return {
    url: location.href,
    conv: rect('.pc-conv'),
    convScroll: rect('.pc-conv__scroll'),
    composerDisabled: composer ? composer.disabled : null,
    messages: [...document.querySelectorAll('.pc-msg')].map((el) => ({
      cls: String(el.className).replace('pc-msg ', '').slice(0, 40),
      text: (el.innerText || '').replace(/\n+/g, ' / ').slice(0, 70),
      h: Math.round(el.getBoundingClientRect().height),
    })),
    failedStrip: strip ? (strip.innerText || '').replace(/\n+/g, ' ').slice(0, 100) : null,
    emptyCard: (() => {
      const el = [...document.querySelectorAll('div')].find((d) =>
        (d.innerText || '').includes('这节课还没有教学流程'),
      )
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { y: Math.round(r.y), h: Math.round(r.height) }
    })(),
  }
})
console.log('[dump]', JSON.stringify(dump, null, 1))
console.log('[net]', JSON.stringify(posts.slice(-6), null, 1))
await ctx.storageState({ path: STATE }).catch(() => {})
await browser.close()