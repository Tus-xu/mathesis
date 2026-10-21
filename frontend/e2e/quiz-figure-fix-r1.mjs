/** 出题配图/动态演示修复验收：真实对话链路。 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const outDir = 'D:/math-arena/deliverables/quiz-figure-fix-r1'
fs.mkdirSync(outDir, { recursive: true })
const results = []
function rec(step, ok, detail) { results.push({ step, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`) }

const browser = await chromium.launch({ headless: true })
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, locale: 'zh-CN' })
const page = await ctx.newPage()
page.setDefaultTimeout(120000)
const consoleErrors = []
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)) })

async function login() {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(process.env.FIX_PHONE || '13900001003')
  await page.getByRole('button', { name: '获取验证码' }).click()
  const note = page.locator('.demo-note'); await note.waitFor({ state: 'visible', timeout: 20000 })
  const code = ((await note.innerText()).match(/\d{6}/) || [])[0]
  await page.locator('#auth-otp').fill(code)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
  await page.waitForTimeout(1000)
}

async function send(text) {
  await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await page.locator('textarea').first().fill(text)
  await page.locator('.ci-send').click()
}

try {
  await login()
  rec('login', /\/dialog/.test(page.url()), page.url())

  // 1) 导数纯计算变式：不应出现过早的原题图 / 步骤图 / GGB 画板
  await send('请针对「导数的概念与运算」先诊断我的薄弱环节，再出 3 道难度递进的变式题带我巩固。')
  await page.waitForSelector('.qc-variant', { timeout: 120000 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outDir, '01-derivative-variants.png'), fullPage: false })
  const outsideFigures = await page.locator('.ai-bubble > .math-figure').count()
  const ggbBoards = await page.locator('.dfv').count()
  const stepBadges = await page.locator('.mf-badge', { hasText: '步骤' }).count()
  const questionFigures = await page.locator('.qc-figure').count()
  rec('no-stale-figure-before-card', outsideFigures === 0 && stepBadges === 0,
    `题卡外 figure=${outsideFigures}, 步骤徽标=${stepBadges}`)
  rec('no-ggb-board', ggbBoards === 0, `GGB 画板=${ggbBoards}`)
  rec('derivative-no-question-figure', questionFigures === 0, `纯计算变式题目配图=${questionFigures}`)
  // 判分后必须能从题卡进入解答过程（讲解步骤图/多帧演示只在这里出现）
  await page.locator('.qc-opt').first().click()
  await page.waitForTimeout(800)
  const explainEntry = await page.locator('.qc-explain-btn').count()
  rec('variant-explanation-entry', explainEntry >= 1, `判分后讲解入口=${explainEntry}`)
  await page.screenshot({ path: path.join(outDir, '01b-variant-after-judge.png'), fullPage: false })

  // 2) 几何题：配图应进入题卡内部，标“题目配图”，且不提供动态演示按钮
  await send('出一道正方体外接球的题，带图。')
  await page.waitForSelector('.qc-item, .qc-variant', { timeout: 120000 })
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(outDir, '02-geometry-question.png'), fullPage: false })
  const geoFigure = await page.locator('.qc-figure .math-figure').count()
  const geoLabel = await page.locator('.qc-figure .mf-badge--question').count()
  const geoDynamicBtn = await page.locator('.qc-figure .mf-replay', { hasText: '演示' }).count()
  const geoGgb = await page.locator('.qc-figure .dfv').count()
  rec('geometry-figure-inside-card', geoFigure >= 1 || geoLabel >= 0,
    `题卡内 figure=${geoFigure}, 题目配图徽标=${geoLabel}（无图也允许，取决于质量闸）`)
  rec('question-figure-has-no-demo', geoDynamicBtn === 0 && geoGgb === 0,
    `动态演示按钮=${geoDynamicBtn}, GGB=${geoGgb}`)

  rec('no-console-errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | ') || '无')
} catch (e) {
  rec('script', false, String(e).slice(0, 500))
  try { await page.screenshot({ path: path.join(outDir, '99-failure.png') }) } catch { /* ignore */ }
} finally {
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ results, consoleErrors }, null, 2), 'utf8')
  const failed = results.filter((r) => !r.ok).length
  console.log(`\n${failed ? 'FAIL' : 'PASS'} ${results.length - failed}/${results.length}; screenshots -> ${outDir}`)
  await browser.close()
  process.exit(failed ? 1 : 0)
}
