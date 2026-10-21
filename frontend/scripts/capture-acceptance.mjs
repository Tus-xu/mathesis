// PHASE 6 验收截图包：真实 UI 登录 → 8 个核心场景截图 → deliverables/screenshots/
// 用法：node scripts/capture-acceptance.mjs
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT = 'D:/math-arena/deliverables/student-refactor-v4/screenshots'
mkdirSync(OUT, { recursive: true })

const BASE = 'http://127.0.0.1:' + (process.env.CAP_PORT || '3000')
const SHOTS = [
  ['/dialog', 'S1-首页即对话'],
  ['/practice', 'S3-练题中心'],
  ['/errors', 'S5-错题本'],
  ['/kb', 'S15-知识库'],
  ['/graph', 'S8-掌握地图'],
  ['/exam', 'S9-真题模考'],
  ['/dual', 'S11-双师创建页浅色'],
  ['/overview', 'S7-学情概览'],
]

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

// 登录：演示短信流程（验证码显示在登录页提示中）
await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
await page.fill('input[type="tel"], input[placeholder*="手机号"]', '13800000000')
await page.getByText('获取验证码').click()
await page.waitForTimeout(1200)
const bodyText = await page.textContent('body')
const codeMatch = bodyText.match(/验证码[：:]\s*(\d{4,6})/)
if (!codeMatch) {
  console.error('未取到演示验证码，页面提示：', bodyText.slice(0, 200))
  process.exit(1)
}
await page.fill('input[placeholder*="验证码"], input[maxlength="6"]', codeMatch[1])
await page.getByText('安全登录').click()
await page.waitForTimeout(2500)
console.log('登录完成')

for (const [path, name] of SHOTS) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' }).catch(() => {})
  await page.waitForTimeout(2200)
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false })
  console.log('✓', name)
}

// 沉浸式练题 + 悬浮球面板两张补充
await page.goto(BASE + '/practice', { waitUntil: 'networkidle' }).catch(() => {})
await page.waitForTimeout(1500)
const start = await page.$('button:has-text("开始训练")')
if (start) {
  await start.click()
  await page.waitForTimeout(6000)
  await page.screenshot({ path: `${OUT}/S4-沉浸式练题答题卡.png` })
  console.log('✓ S4-沉浸式练题答题卡')
}
await page.keyboard.press('Escape').catch(() => {})
const ball = await page.$('.fb-ball')
if (ball) {
  await ball.click()
  await page.waitForTimeout(1500)
  const studyTab = await page.$('button:has-text("学情")')
  if (studyTab) await studyTab.click()
  await page.waitForTimeout(1200)
  await page.screenshot({ path: `${OUT}/S16-AI管家学情面板.png` })
  console.log('✓ S16-AI管家学情面板')
}

await browser.close()
console.log('截图包完成 →', OUT)
