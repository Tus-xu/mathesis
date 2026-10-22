/** 两台部署机的公网烟测（真实浏览器 + 真实登录）。输出截图到 deliverables/deploy-verify-r1/ */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'deploy-verify-r1')
fs.mkdirSync(outDir, { recursive: true })
const targets = [
  { host: process.env.DEPLOY_HOST_1 || 'http://host1.example.com', student: '13900001002', researcher: '13900002023', tag: 'host1' },
  { host: process.env.DEPLOY_HOST_2 || 'http://host2.example.com', student: '13800138000', researcher: '13900002024', tag: 'host2' },
]
const results = []
const errors = []
function rec(step, ok, detail) { results.push({ step, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`) }

async function login(page, host, phone, role) {
  await page.goto(`${host}/login`, { waitUntil: 'networkidle' })
  await page.locator('#preferred-role').selectOption(role)
  await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
  await page.getByRole('button', { name: '获取验证码' }).click()
  const note = page.locator('.demo-note')
  await note.waitFor({ state: 'visible', timeout: 20000 })
  const code = ((await note.innerText()).match(/\d{6}/) || [])[0]
  if (!code) throw new Error(`demo code missing on ${host}`)
  await page.locator('#auth-otp').fill(code)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
  await page.waitForTimeout(1800)
  return page.url()
}

const browser = await chromium.launch({ headless: true })
for (const t of targets) {
  console.log(`\n=== ${t.host} ===`)
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
  const page = await ctx.newPage()
  page.setDefaultTimeout(45000)
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${t.tag}] ${m.text().slice(0, 200)}`) })
  page.on('response', (r) => {
    if (/\/api\//.test(r.url()) && r.status() >= 500) errors.push(`[${t.tag}] ${r.status()} ${r.url()}`)
  })
  try {
    const studentUrl = await login(page, t.host, t.student, 'student')
    rec(`${t.tag}-student-login`, /\/dialog/.test(studentUrl), studentUrl)
    await page.waitForTimeout(1800)
    await page.screenshot({ path: path.join(outDir, `${t.tag}-student-home.png`), fullPage: false })
    const homeText = await page.locator('body').innerText()
    rec(`${t.tag}-student-home`, /学情脉搏|今天想搞定什么/.test(homeText), '首页渲染')
    await page.goto(`${t.host}/library`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    await page.screenshot({ path: path.join(outDir, `${t.tag}-library.png`), fullPage: false })
    const libText = await page.locator('body').innerText()
    rec(`${t.tag}-library`, /知识库/.test(libText) && /我的资料/.test(libText), '知识库渲染')
    await ctx.close()
  } catch (e) {
    rec(`${t.tag}-student`, false, String(e).slice(0, 300))
    try { await page.screenshot({ path: path.join(outDir, `${t.tag}-student-fail.png`) }) } catch { /* ignore */ }
    await ctx.close()
  }

  const rctx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, locale: 'zh-CN' })
  const rp = await rctx.newPage()
  rp.setDefaultTimeout(45000)
  rp.on('console', (m) => { if (m.type() === 'error') errors.push(`[${t.tag}-research] ${m.text().slice(0, 200)}`) })
  rp.on('response', (r) => {
    if (/\/api\//.test(r.url()) && r.status() >= 500) errors.push(`[${t.tag}-research] ${r.status()} ${r.url()}`)
  })
  try {
    const rUrl = await login(rp, t.host, t.researcher, 'researcher')
    rec(`${t.tag}-research-landing`, /\/research-workspace\/?$/.test(rUrl), rUrl)
    await rp.waitForSelector('.rl-page', { timeout: 30000 })
    await rp.waitForTimeout(1200)
    await rp.screenshot({ path: path.join(outDir, `${t.tag}-research-landing.png`), fullPage: true })
    await rp.goto(`${t.host}/research-workspace/account`, { waitUntil: 'networkidle' })
    await rp.waitForSelector('.ra-profile', { timeout: 30000 })
    await rp.waitForTimeout(1000)
    await rp.screenshot({ path: path.join(outDir, `${t.tag}-research-account.png`), fullPage: true })
    rec(`${t.tag}-research-account`, true, '个人中心渲染')
  } catch (e) {
    rec(`${t.tag}-research`, false, String(e).slice(0, 300))
    try { await rp.screenshot({ path: path.join(outDir, `${t.tag}-research-fail.png`) }) } catch { /* ignore */ }
  }
  await rctx.close()
}
fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ results, errors }, null, 2), 'utf8')
const failed = results.filter((r) => !r.ok).length
console.log(`\n${failed ? 'FAIL' : 'PASS'} ${results.length - failed}/${results.length}; errors=${errors.length}`)
await browser.close()
process.exit(failed ? 1 : 0)
