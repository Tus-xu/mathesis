import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = 'http://127.0.0.1:5176'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'research-ux-r1')
fs.mkdirSync(outDir, { recursive: true })
const projectId = fs.readFileSync(path.resolve(__dirname, '..', '..', '.tmp', 'research_project_id.txt'), 'utf8').trim()
const results = []
function rec(step, ok, detail) { results.push({ step, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`) }

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
const page = await ctx.newPage()
page.setDefaultTimeout(45000)
const errors = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 220)) })
page.on('response', (r) => { if (/\/api\//.test(r.url()) && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`) })

try {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.locator('#preferred-role').selectOption('researcher')
  await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill('13900001006')
  await page.getByRole('button', { name: '获取验证码' }).click()
  const note = page.locator('.demo-note'); await note.waitFor({ state: 'visible', timeout: 20000 })
  const code = ((await note.innerText()).match(/\d{6}/) || [])[0]
  await page.locator('#auth-otp').fill(code)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })

  await page.goto(`${base}/research-workspace/${projectId}/read`, { waitUntil: 'networkidle' })
  await page.waitForSelector('.rw-shell', { timeout: 30000 })
  await page.waitForTimeout(1800)
  // 已恢复历史结果时，先点「新对话」验证空态；不掩盖真实持久化能力。
  const newChatBtn = page.locator('.rw-agent-header__new')
  if (await newChatBtn.isVisible().catch(() => false)) {
    await newChatBtn.click()
    await page.waitForTimeout(600)
  }
  await page.screenshot({ path: path.join(outDir, '01-read-empty.png'), fullPage: false })
  const readText = await page.locator('.rw-shell').innerText()
  rec('read-shell', /Research Agent/.test(readText) && /今天想推进什么/.test(readText), 'README + Agent 空态')
  const promptCards = await page.locator('.rw-agent__prompt').count()
  rec('agent-empty-prompts', promptCards >= 3, `Agent 起点卡=${promptCards}`)
  const oldAccordions = await page.locator('.rw-cap').count()
  rec('no-capability-wall', oldAccordions === 0, `旧能力 Accordion=${oldAccordions}`)

  // 切写作，打开 main.tex，选中编辑器文本
  await page.locator('.rw-seg__item', { hasText: '写作' }).click()
  await page.waitForTimeout(2500)
  await page.screenshot({ path: path.join(outDir, '02-write-shell.png'), fullPage: false })
  const mainFile = page.getByText('main.tex', { exact: true }).first()
  await mainFile.waitFor({ state: 'visible', timeout: 20000 })
  await mainFile.click()
  await page.waitForTimeout(1800)
  const editor = page.locator('.cm-content').first()
  await editor.click()
  await page.keyboard.press('Control+A')
  await page.waitForTimeout(1000)
  const hasSelCard = await page.locator('.rw-selctx').count()
  await page.screenshot({ path: path.join(outDir, '03-selection-context.png'), fullPage: false })
  rec('selection-context-card', hasSelCard >= 1, `选中文本卡=${hasSelCard}`)
  if (hasSelCard) {
    const selText = await page.locator('.rw-selctx').innerText()
    rec('selection-actions', /评判/.test(selText) && /解释/.test(selText) && /翻译/.test(selText) && /记笔记/.test(selText), '评判/解释/翻译/记笔记 均可见')
    const selHeight = await page.locator('.rw-agent').evaluate((el) => el.getBoundingClientRect().width)
    rec('agent-width', selHeight > 300 && selHeight < 520, `Agent 栏宽=${Math.round(selHeight)}px`)
    // 触发评判，观察线程/结果状态；不要求 LLM 一定成功，但必须有可见反馈
    await page.locator('.rw-selctx__act--primary').click()
    await page.waitForTimeout(2500)
    const thread = await page.locator('.rw-chat').count()
    const thinking = await page.locator('.rw-chat__thinking').count()
    await page.screenshot({ path: path.join(outDir, '04-judge-running.png'), fullPage: false })
    rec('judge-feedback', thread + thinking >= 1, `对话线程=${thread}, 进行中=${thinking}`)
    for (let i = 0; i < 20; i++) {
      await page.waitForTimeout(1500)
      const resultCards = await page.locator('.rw-chat__result-card').count()
      const error = await page.locator('.rw-chat__error').count()
      if (resultCards || error) {
        await page.screenshot({ path: path.join(outDir, '05-judge-result.png'), fullPage: false })
        rec('judge-result-or-honest-error', true, `结果卡=${resultCards}, 错误态=${error}`)
        break
      }
      if (i === 19) rec('judge-result-or-honest-error', false, '30s 内无结果/错误反馈')
    }
  }
  rec('no-console-errors', errors.length === 0, errors.slice(0, 3).join(' | ') || '无')
} catch (e) {
  rec('script', false, String(e).slice(0, 500))
  try { await page.screenshot({ path: path.join(outDir, '99-failure.png') }) } catch { /* ignore */ }
} finally {
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ results, errors }, null, 2), 'utf8')
  const failed = results.filter((r) => !r.ok).length
  console.log(`\n${failed ? 'FAIL' : 'PASS'} ${results.length - failed}/${results.length}; screenshots -> ${outDir}`)
  await browser.close()
  process.exit(failed ? 1 : 0)
}
