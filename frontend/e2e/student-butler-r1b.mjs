/** R1 补充验收：打开资源中心 → open_page 确认弹窗 → 前往跳转（开发用）。
 *
 * 真实链路（既有 v1.4 交互）：SSE action 事件 → useChat hooks.onAction →
 * DialogView.handleAction 弹「页面跳转」确认框；MessageBubble 同步渲染按钮卡
 * （历史消息可再次点按）。两条路都可达 /resources，本脚本各验一次。
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = 'http://127.0.0.1:5176'
const phone = '13800009999'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'student-butler-r1')
fs.mkdirSync(outDir, { recursive: true })
const results = []
const record = (s, ok, d) => { results.push({ step: s, ok, detail: d }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${s} | ${d}`) }

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 1500, height: 940 }, locale: 'zh-CN' })
const page = await ctx.newPage()
page.setDefaultTimeout(60000)

await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
await page.getByRole('button', { name: '获取验证码' }).click()
await page.locator('.demo-note').waitFor({ state: 'visible', timeout: 30000 })
const demoCode = ((await page.locator('.demo-note').innerText()).match(/\d{6}/) || [])[0]
await page.locator('#auth-otp').fill(demoCode)
await page.getByRole('button', { name: /安全登录/ }).click()
await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })

// 新开一个干净会话，避免历史按钮干扰 first()
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1200)
const newConv = page.getByText('+ 新话', { exact: false }).or(page.locator('.new-conv-btn')).first()
if (await newConv.isVisible().catch(() => false)) { await newConv.click(); await page.waitForTimeout(1000) }

await page.locator('textarea').fill('打开资源中心')
await page.locator('.ci-send').click()
// 确认弹窗自动弹出（hooks.onAction → handleAction）
await page.locator('.modal-mask').waitFor({ state: 'visible', timeout: 90000 })
const modalText = await page.locator('.modal-mask').innerText()
record('confirm-modal', /资源中心/.test(modalText) && /前往/.test(modalText), modalText.replace(/\n/g, ' | ').slice(0, 80))
await page.screenshot({ path: `${outDir}/12_open_page_confirm_modal.png` })
// 点「前往」→ 跳 /resources
await page.locator('.modal-mask').getByRole('button', { name: '前往' }).click()
await page.waitForTimeout(1500)
record('jump-modal', page.url().includes('/resources'), `确认弹窗前往后 url=${page.url()}`)
await page.screenshot({ path: `${outDir}/13_after_jump_resources.png` })

// 按钮卡路径：回到对话，历史消息里的按钮卡仍在，点击 → 确认弹窗 → 前往
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)
const histBtn = page.locator('.action-card .action-btn').first()
if (await histBtn.isVisible().catch(() => false)) {
  record('history-btn', true, '历史消息按钮卡仍在（envelope.blocks action 块重放）')
  await histBtn.click({ force: true })
  await page.locator('.modal-mask').waitFor({ state: 'visible', timeout: 15000 })
  await page.locator('.modal-mask').getByRole('button', { name: '前往' }).click()
  await page.waitForTimeout(1500)
  record('jump-history-btn', page.url().includes('/resources'), `历史按钮卡跳转 url=${page.url()}`)
} else {
  record('history-btn', false, '历史消息按钮卡未渲染')
}

fs.writeFileSync(`${outDir}/verify-results-b.json`, JSON.stringify(results, null, 2))
await browser.close()
process.exit(results.every((r) => r.ok) ? 0 : 1)
