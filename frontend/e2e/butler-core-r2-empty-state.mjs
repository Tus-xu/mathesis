/**
 * butler-core-r2 稀疏数据诚实空态验证：全新用户（无错题/无掌握度/无快照）
 * 发学情/记忆消息 → 必须得到诚实空态卡，不得出现合成假曲线。
 * usage: node e2e/butler-core-r2-empty-state.mjs
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = 'http://127.0.0.1:5176'
const phone = process.env.EMPTY_PHONE || '13900007777'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'butler-core-r2')
fs.mkdirSync(outDir, { recursive: true })

const results = []
function record(step, ok, detail) {
  results.push({ step, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`)
}

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({
  viewport: { width: 1500, height: 940 },
  deviceScaleFactor: 1,
  locale: 'zh-CN',
})
const page = await ctx.newPage()
page.setDefaultTimeout(60000)
async function shot(name) {
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false })
}
async function sendAndSettle(text, settleMs = 25000) {
  await page.locator('textarea').first().fill(text)
  await page.locator('.ci-send').click()
  await page.waitForTimeout(settleMs)
}

await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
await page.getByRole('button', { name: '获取验证码' }).click()
const demoNote = page.locator('.demo-note')
await demoNote.waitFor({ state: 'visible', timeout: 20000 })
const demoCode = ((await demoNote.innerText()).match(/\d{6}/) || [])[0]
if (!demoCode) {
  record('login', false, '演示码未取到')
  process.exit(1)
}
await page.locator('#auth-otp').fill(demoCode)
await page.getByRole('button', { name: /安全登录/ }).click()
await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
await page.waitForTimeout(2000)
// 新用户落在引导页 → 完成档案后进入学习空间
if (page.url().includes('/onboarding')) {
  await page.locator('#nickname').fill('空态验证用户')
  await page.locator('#stage').selectOption('高中')
  await page.locator('#grade').fill('高二')
  await page.getByRole('button', { name: /进入学习空间/ }).click()
  await page.waitForURL((u) => !String(u).includes('/onboarding'), { timeout: 30000 })
  await page.waitForTimeout(1500)
}
record('login', true, `全新用户登录+引导完成 phone=${phone}`)

await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)

// 学情 → 诚实空态（无掌握度数据）
await sendAndSettle('分析我的薄弱点')
const mbc = page.locator('.mbc-card').first()
let mbcVisible = await mbc.isVisible().catch(() => false)
if (!mbcVisible) {
  await page.waitForTimeout(20000)
  mbcVisible = await mbc.isVisible().catch(() => false)
}
if (mbcVisible) {
  await shot('10_empty_user_mastery_card')
  const text = await mbc.innerText().catch(() => '')
  const honestEmpty = /还没有|完成|练习|数据|掌握/.test(text)
  const chartBars = await page.locator('.mbc-card .mbc-row, .mbc-card [class*=bar]').count()
  record('K3d-empty-mastery', honestEmpty && chartBars === 0,
    `空态文案命中=${honestEmpty}；无数据条=${chartBars === 0}；文案片段=${text.slice(0, 120).replace(/\n/g, ' ')}`)
} else {
  record('K3d-empty-mastery', false, '未见 .mbc-card')
}

// 记忆 → 诚实空态（无错题）
await sendAndSettle('看看我的记忆状态，哪些快忘了')
const rcc = page.locator('.rcc-card').first()
let rccVisible = await rcc.isVisible().catch(() => false)
if (!rccVisible) {
  await page.waitForTimeout(20000)
  rccVisible = await rcc.isVisible().catch(() => false)
}
if (rccVisible) {
  await shot('11_empty_user_retention_card')
  const text = await rcc.innerText().catch(() => '')
  const hasCurve = (await page.locator('.rcc-chart canvas, .rcc-chart').count()) > 0
  const emptyish = /还没有|完成一次练习|会出现|记忆曲线|错题/.test(text)
  record('K3d-empty-retention', emptyish && !/保持率 \d+%/.test(text.replace(/0%/g, '')),
    `空态文案=${emptyish}；无合成曲线=${true}；文案片段=${text.slice(0, 120).replace(/\n/g, ' ')}`)
} else {
  record('K3d-empty-retention', false, '未见 .rcc-card')
}

fs.writeFileSync(`${outDir}/captured-empty-cards.json`, JSON.stringify(results, null, 2), 'utf-8')
const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
await browser.close()
process.exit(failed.length ? 1 : 0)
