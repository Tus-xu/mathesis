/**
 * 「生成中」态真实截图器（开发用；不属于产品代码）。
 *
 * usage: node e2e/prep-live-shot.mjs <sessionId> <outDir>
 *
 * 行为：登录 → 打开会话 → 在底部 AI 条真实输入并发送一条修改消息 →
 *       在受理后立刻连拍若干张（会话条 typing 态 + 底部 AI 条生成提示 + 停止生成按钮），
 *       直到生成结束或超时。口径 1672×941 / DPR=1。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.PREP_PHONE || '13900001001'
const sessionId = process.argv[2]
const outDir = process.argv[3]
if (!sessionId || !outDir) {
  console.error('usage: node e2e/prep-live-shot.mjs <sessionId> <outDir>')
  process.exit(2)
}
fs.mkdirSync(outDir, { recursive: true })

const MSG = process.env.PREP_LIVE_MSG || '把第 2 个环节改成探究式：让学生先动手画椭圆，再总结定义'

const browser = await chromium.launch({ headless: true })
const STATE = 'D:/math-arena/tmp/pw_state.json'
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
  await ctx.storageState({ path: STATE }).catch(() => {})

  // 真实 UI 发送：底部 AI 条输入 + 回车（PREP_LIVE_NO_SEND=1 时只观测已在途生成）
  if (!process.env.PREP_LIVE_NO_SEND) {
    const input = page.locator('.pc-composer__input')
    await input.click()
    await input.fill(MSG)
    await page.waitForTimeout(200)
    await input.press('Enter')
    console.log('[live] sent, capturing busy window ...')
  } else {
    console.log('[live] observe-only, capturing busy window ...')
  }

  let captured = 0
  let capturedCancel = 0
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(1000)
    const st = await page.evaluate(() => ({
      typing: !!document.querySelector('.pc-conv__typing'),
      typingText: document.querySelector('.pc-conv__typing')?.innerText.replace(/\n/g, ' ') || '',
      notice: document.querySelector('.pc-composer__notice')?.innerText || '',
      cancel: Array.from(document.querySelectorAll('.pc-actions__btn')).map((b) => b.innerText),
      genTitle: document.querySelector('.pc-state__title')?.innerText || '',
    }))
    const busy = st.typing || /正在生成|AI 正在处理/.test(st.notice) || st.cancel.some((t) => /停止/.test(t)) || /正在生成/.test(st.genTitle)
    const hasCancel = st.cancel.some((t) => /停止/.test(t))
    console.log(`[live] t=${i + 1}s busy=${busy} cancel=${hasCancel}`, JSON.stringify(st))
    if (busy && i + 1 >= Number(process.env.PREP_LIVE_CAPTURE_AT || 6)) {
      await page.evaluate(() => window.scrollTo(0, 0))
      const t0 = Date.now()
      // 首选「服务端已下发 cancel_run（停止生成可点）」的窗口，作为生成中最强证据
      if (hasCancel && capturedCancel < 2) {
        capturedCancel += 1
        const file = `${outDir}/classroom-06-generating-with-stop${capturedCancel > 1 ? `-${capturedCancel}` : ''}.png`
        await page.screenshot({ path: file, animations: 'disabled', timeout: 45000 })
        console.log(`[shot] ${file.split(/[\\/]/).pop()} ms=${Date.now() - t0}`)
      } else if (!hasCancel && captured < 2) {
        captured += 1
        const file = `${outDir}/classroom-06-generating${captured > 1 ? `-${captured}` : ''}.png`
        await page.screenshot({ path: file, animations: 'disabled', timeout: 45000 })
        console.log(`[shot] ${file.split(/[\\/]/).pop()} ms=${Date.now() - t0}`)
      }
      if (capturedCancel >= 2) break
    }
  }
  console.log(`[live] captured busy=${captured} withStop=${capturedCancel}`)
} catch (e) {
  console.error('[live] FAILED:', e?.message || e)
  process.exitCode = 1
} finally {
  await browser.close()
}