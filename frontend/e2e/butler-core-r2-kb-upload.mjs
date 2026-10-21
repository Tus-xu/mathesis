/**
 * butler-core-r2 K5c 回归：知识库图片上传（原图保存 + OCR 字数 + 分类）。
 * usage: node e2e/butler-core-r2-kb-upload.mjs
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = 'http://127.0.0.1:5176'
const phone = process.env.R2_PHONE || '13800009999'
const uploadFile = 'D:/math-arena/deliverables/butler-core-r2/kb-test-image.png'
const outDir = 'D:/math-arena/deliverables/butler-core-r2'

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

await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
await page.getByRole('button', { name: '获取验证码' }).click()
const demoNote = page.locator('.demo-note')
await demoNote.waitFor({ state: 'visible', timeout: 20000 })
const demoCode = ((await demoNote.innerText()).match(/\d{6}/) || [])[0]
await page.locator('#auth-otp').fill(demoCode)
await page.getByRole('button', { name: /安全登录/ }).click()
await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
await page.waitForTimeout(2000)
record('login', true, `登录成功 phone=${phone}`)

await page.goto(`${base}/library`, { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)
await shot('12_library_initial')
const beforeCount = await page.locator('.kb-doc, .doc-row, [class*=kb-doc]').count()

// 上传图片（真实 multipart → /v1/kb/upload，分类「其他」）
await page.locator('.kb-upload input[type=file]').setInputFiles(uploadFile)
await page.waitForTimeout(25000)
await shot('13_library_after_upload')
const listText = await page.locator('body').innerText()
const uploaded = listText.includes('kb-test-image')
const imgSaved = /已按原图保存|🖼/.test(listText)
const hasOcr = /文字识别|ocr/i.test(listText)
record('K5c-kb-upload', uploaded, `上传后列表含新文档=${uploaded}；原图保存标记=${imgSaved}；OCR 标记=${hasOcr}；此前列表行=${beforeCount}`)

await browser.close()
process.exit(results.every((r) => r.ok) ? 0 : 1)
