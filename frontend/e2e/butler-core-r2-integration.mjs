/**
 * butler-core-r2 第 1 轮联调验收（K1/K3/K5 浏览器证据采集）。
 * usage: node e2e/butler-core-r2-integration.mjs
 * 前置：前端 dev :5176 + 后端 :8000（BUTLER_V2_ENABLED=true）。
 * 输出：artifacts/butler-core-r2/（截图 + captured-cards.json，可用 OUT 覆盖）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.R2_PHONE || '13800009999'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'butler-core-r2')
fs.mkdirSync(outDir, { recursive: true })

const results = []
function record(step, ok, detail) {
  results.push({ step, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`)
}

const captured = { cards: [] }

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

// ---------- 1. 登录 ----------
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
record('login', true, `登录成功 phone=${phone}`)
await page.waitForTimeout(2500)

// ---------- K3a 复习到期卡（保持率清单） ----------
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1800)
await shot('01_dialog_initial')
const nudgeVisible = await page.locator('.review-nudge').isVisible().catch(() => false)
if (nudgeVisible) {
  const rnRows = await page.locator('.rn-item').count()
  const rnText = await page.locator('.review-nudge').innerText().catch(() => '')
  captured.reviewNudge = rnText
  record('K3a-review-nudge', rnRows >= 1 && /%/.test(rnText),
    `到期卡保持率行 ${rnRows} 行（R% 徽标 + 建议动作）`)
} else {
  record('K3a-review-nudge', false, '复习提醒卡未出现（预期有 31 条到期错题）')
}

// ---------- K1 学情分析（用 ask 原话「分析我的薄弱点」） ----------
await sendAndSettle('分析我的薄弱点')
const mbc = page.locator('.mbc-card').first()
let mbcVisible = await mbc.isVisible().catch(() => false)
if (!mbcVisible) {
  await page.waitForTimeout(20000)
  mbcVisible = await mbc.isVisible().catch(() => false)
}
if (mbcVisible) {
  await shot('02_mastery_bkt_card')
  const pill = await page.locator('.mbc-pill').first().innerText().catch(() => '')
  const text = await page.locator('.mbc-card').first().innerText().catch(() => '')
  captured.masteryCard = text
  const hasModel = /BKT/.test(pill)
  const hasNumbers = /\d+(\.\d+)?%?/.test(text)
  record('K1-mastery-card', hasModel && hasNumbers,
    `mastery_bkt 卡渲染；模型标注「${pill}」；含数字=${hasNumbers}`)
} else {
  record('K1-mastery-card', false, '未见 .mbc-card（桥接未生效或内核失败）')
}
await shot('03_after_mastery_question_full')

// ---------- K3b 记忆状态 → fsrs_retention 卡 ----------
await sendAndSettle('看看我的记忆状态，哪些快忘了')
const rcc = page.locator('.rcc-card').first()
let rccVisible = await rcc.isVisible().catch(() => false)
if (!rccVisible) {
  await page.waitForTimeout(20000)
  rccVisible = await rcc.isVisible().catch(() => false)
}
if (rccVisible) {
  await shot('04_retention_card')
  const rccText = await rcc.innerText().catch(() => '')
  captured.retentionCard = rccText
  const hasKeyNums = /当前保持率|稳定度|下次复习/.test(rccText)
  const hasCurveOrEmpty =
    (await page.locator('.rcc-chart').count()) > 0 ||
    (await page.locator('.rcc-nocurve').count()) > 0 ||
    (await page.locator('.rcc-empty').count()) > 0
  const hasAlgo = /FSRS R\(t\)/.test(rccText)
  record('K3b-retention-card', hasKeyNums && hasCurveOrEmpty && hasAlgo,
    `fsrs_retention 卡：关键数字=${hasKeyNums}；曲线/空态=${hasCurveOrEmpty}；算法角标=${hasAlgo}`)
  const zoomBtn = page.locator('.rcc-zoom').first()
  if (await zoomBtn.isVisible().catch(() => false)) {
    await zoomBtn.click()
    await page.waitForTimeout(800)
    const modalVisible = await page.locator('.rcc-modal').isVisible().catch(() => false)
    await shot('05_retention_zoom_modal')
    record('K3b-zoom-modal', modalVisible, '放大 Modal（全尺寸 R(t) 曲线）')
    await page.locator('.rcc-modal-close').click().catch(() => {})
  }
} else {
  record('K3b-retention-card', false, '未见 .rcc-card')
}

// ---------- K3c 刷新 → 历史回放 ----------
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(3500)
const mbcAfter = await page.locator('.mbc-card').count()
const rccAfter = await page.locator('.rcc-card').count()
record('K3c-history-replay', mbcAfter >= 1 && rccAfter >= 1,
  `刷新后 mastery_bkt=${mbcAfter} 张、fsrs_retention=${rccAfter} 张`)
await shot('06_after_reload_replay')

// ---------- K1b FloatingButler 悬浮球 ----------
await page.goto(`${base}/overview`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await page.locator('.fb-ball').click()
await page.waitForTimeout(800)
const panelVisible = await page.locator('.fb-panel').isVisible().catch(() => false)
if (panelVisible) {
  await page.locator('.fb-inputrow input').fill('分析我的薄弱点')
  await page.locator('.fb-inputrow input').press('Enter')
  await page.waitForTimeout(12000)
  await shot('07_butler_panel_reply')
  const panelCards = await page.locator('.fb-panel .mbc-card, .fb-panel .rcc-card').count()
  const panelText = await page.locator('.fb-panel').innerText().catch(() => '')
  const degradedBar = await page.locator('.fb-panel .fb-degraded').count()
  record('K1b-butler-panel', panelCards > 0,
    `悬浮球面板数据卡=${panelCards} 张；降级条=${degradedBar}`)
} else {
  record('K1b-butler-panel', false, '悬浮球面板未打开')
}
await page.keyboard.press('Escape')

// ---------- K5a 视频卡回归 ----------
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await sendAndSettle('推荐几个B站的二次函数讲解视频', 30000)
const vc = await page.locator('.vc-card').count()
record('K5a-video-card', vc >= 1, `视频卡 ${vc} 张`)
await shot('08_regression_video_cards')

// ---------- K5b 出题卡回归（五闸质检可能慢，轮询 150s） ----------
await sendAndSettle('请围绕「数列」出 3 道练习题让我作答', 3000)
let qcOk = false
try {
  await page.locator('.quiz-card').first().waitFor({ state: 'visible', timeout: 150000 })
  qcOk = true
} catch { /* 超时如实记 FAIL */ }
const qc = await page.locator('.quiz-card').count()
record('K5b-quiz-card', qcOk && qc >= 1, `出题卡 ${qc} 张`)
await shot('09_regression_quiz_cards')

// ---------- 汇总 ----------
fs.writeFileSync(`${outDir}/captured-cards.json`, JSON.stringify(captured, null, 2), 'utf-8')
const failed = results.filter((r) => !r.ok)
console.log('\n==== SUMMARY ====')
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.step} | ${r.detail}`)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
await browser.close()
process.exit(failed.length ? 1 : 0)
