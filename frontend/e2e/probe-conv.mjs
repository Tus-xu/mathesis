import { chromium } from '@playwright/test'
import fs from 'node:fs'
const base = 'http://127.0.0.1:5176'
const STATE = 'D:/math-arena/tmp/pw_state.json'
const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1672, height: 941 },
  deviceScaleFactor: 1,
  ...(fs.existsSync(STATE) ? { storageState: STATE } : {}),
})
const page = await ctx.newPage()
await page.goto(`${base}/teacher-v3/prep/w/101781c5-777e-43ab-9e88-69fbca19e21b/classroom`, { waitUntil: 'domcontentloaded' })
await page.waitForTimeout(2500)
if (page.url().includes('/login')) {
  const req = ctx.request
  const ch = (await (await req.post(`${base}/api/auth/challenges/sms`, { data: { phone: '13900001001', purpose: 'login' } })).json()).data
  const login = (await (await req.post(`${base}/api/auth/login/sms`, { data: { phone: '13900001001', code: ch.demo_code, challenge_id: ch.challenge_id, remember: false } })).json()).data
  await req.post(`${base}/api/auth/role/switch`, { data: { role: 'teacher' }, headers: { Authorization: `Bearer ${login.access_token}` } })
  await ctx.storageState({ path: STATE })
  await page.goto(`${base}/teacher-v3/prep/w/101781c5-777e-43ab-9e88-69fbca19e21b/classroom`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(3000)
}
await page.waitForTimeout(4000)
const info = await page.evaluate(() => {
  const root = document.querySelector('.pc-conv')
  const scroll = document.querySelector('.pc-conv__scroll')
  const box = (el) => (el ? { h: Math.round(el.getBoundingClientRect().height), cls: el.className } : null)
  const kids = scroll ? Array.from(scroll.children).map((c) => ({ tag: c.tagName, cls: c.className, h: Math.round(c.getBoundingClientRect().height) })) : []
  return {
    rootH: box(root)?.h, rootCls: root?.className,
    scrollH: box(scroll)?.h, scrollCls: scroll?.className,
    scrollStyle: scroll ? { minH: getComputedStyle(scroll).minHeight, maxH: getComputedStyle(scroll).maxHeight, jc: getComputedStyle(scroll).justifyContent } : null,
    kids,
  }
})
console.log(JSON.stringify(info, null, 2))
await browser.close()