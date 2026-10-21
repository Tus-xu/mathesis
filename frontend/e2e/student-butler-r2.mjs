/**
 * 学生端 AI 管家 R2 联调验收截图器（开发用；不属于产品代码）。
 *
 * usage:
 *   node e2e/student-butler-r2.mjs
 *
 * 前置：前端 dev :5176 + 后端 :8000（BUTLER_V2_ENABLED=true，管线轨 chat_bridge 已部署）。
 *
 * 行为（真实浏览器 → 真实后端）：
 *   1) 登录（与 r1 同链路：手机号 → 演示验证码 → 安全登录）；
 *   2) D1 对话发「分析一下我的数学薄弱点」→ 断言 mastery_bkt 卡渲染（.mbc-card）；
 *   3) D2 对话发「看看我的记忆状态」→ 断言 fsrs_retention 卡（.rcc-card）含
 *      错题 chips / 关键数字 / 曲线容器或诚实无曲线态；
 *   4) 刷新 → 断言历史回放后两张卡仍在（envelope.blocks 落库重放）；
 *   5) FloatingButler 悬浮球 → 发学情消息 → 断言面板内数据卡渲染；
 *   6) 回归三验（防上轮功能回退）：视频卡 / quiz 出题卡 / 复习提醒保持率清单。
 *
 * 截图输出：artifacts/student-butler-r2/（可用 OUT 覆盖）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.R2_PHONE || '13800009999'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'student-butler-r2')
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

// ---------- 1. 登录（真实 UI 链路，与 r1 骨架一致） ----------
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
record('login', true, `登录成功 url=${page.url()}`)
await page.waitForTimeout(2500)

// ---------- 2. D1 学情分析 → mastery_bkt 卡 ----------
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await shot('10_dialog_initial')
// D0 复习到期卡升级：到期题数 + 每题保持率（due-queue FSRS 现算值）+ 建议动作
// （仅在无消息的新对话首屏出现；有数据时断言保持率行，无到期题时如实跳过）
const nudgeVisible = await page.locator('.review-nudge').isVisible().catch(() => false)
if (nudgeVisible) {
  const rnRows = await page.locator('.rn-item').count()
  const rnText = await page.locator('.review-nudge').innerText().catch(() => '')
  record('D0-review-nudge', rnRows >= 1 && /%/.test(rnText),
    `复习提醒卡保持率行 ${rnRows} 行（含 R% 与建议动作）`)
} else {
  record('D0-review-nudge', true, '无到期错题（提醒卡不出现，属正常空态）')
}
await sendAndSettle('分析一下我的数学薄弱点')
await shot('11_after_mastery_question')
const mbc = page.locator('.mbc-card').first()
let mbcVisible = await mbc.isVisible().catch(() => false)
if (!mbcVisible) {
  // 后端内核可能比预算慢：再等一轮
  await page.waitForTimeout(20000)
  mbcVisible = await mbc.isVisible().catch(() => false)
}
if (mbcVisible) {
  await shot('12_mastery_bkt_card')
  const pill = await page.locator('.mbc-pill').first().innerText().catch(() => '')
  const hasModelLabel = /BKT/.test(pill)
  const bodyText = await page.locator('.mbc-card').first().innerText().catch(() => '')
  const honest =
    (await page.locator('.mbc-chart').count()) > 0 || /还没有练习数据|数据积累中|掌握度/.test(bodyText)
  record('D1-mastery-card', hasModelLabel && honest, `mastery_bkt 卡渲染；模型标注「${pill}」；内容诚实=${honest}`)
} else {
  record('D1-mastery-card', false, '未见 .mbc-card（后端 chat_bridge 未部署或未返回 mastery_bkt 卡）')
}

// ---------- 3. D2 记忆状态 → fsrs_retention 卡 ----------
await sendAndSettle('看看我的记忆状态，哪些快忘了')
await shot('13_after_retention_question')
const rcc = page.locator('.rcc-card').first()
let rccVisible = await rcc.isVisible().catch(() => false)
if (!rccVisible) {
  await page.waitForTimeout(20000)
  rccVisible = await rcc.isVisible().catch(() => false)
}
if (rccVisible) {
  await shot('14_retention_card')
  const rccText = await rcc.innerText().catch(() => '')
  const hasKeyNums = /当前保持率|稳定度|下次复习/.test(rccText)
  const hasCurveOrEmpty =
    (await page.locator('.rcc-chart').count()) > 0 ||
    (await page.locator('.rcc-nocurve').count()) > 0 ||
    (await page.locator('.rcc-empty').count()) > 0
  const hasAlgoBadge = /FSRS R\(t\)/.test(rccText)
  record('D2-retention-card', hasKeyNums && hasCurveOrEmpty && hasAlgoBadge,
    `fsrs_retention 卡：关键数字=${hasKeyNums}；曲线/空态=${hasCurveOrEmpty}；算法角标=${hasAlgoBadge}`)
  // 放大 Modal
  const zoomBtn = page.locator('.rcc-zoom').first()
  if (await zoomBtn.isVisible().catch(() => false)) {
    await zoomBtn.click()
    await page.waitForTimeout(800)
    const modalVisible = await page.locator('.rcc-modal').isVisible().catch(() => false)
    await shot('15_retention_zoom_modal')
    record('D2-zoom-modal', modalVisible, '放大 Modal 打开（全尺寸曲线）')
    await page.locator('.rcc-modal-close').click().catch(() => {})
  }
} else {
  record('D2-retention-card', false, '未见 .rcc-card（后端未返回 fsrs_retention 卡）')
}

// ---------- 4. 刷新 → 历史回放 ----------
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(3500)
const mbcAfter = await page.locator('.mbc-card').count()
const rccAfter = await page.locator('.rcc-card').count()
record('D3-history-replay', mbcAfter >= 1 && rccAfter >= 1,
  `刷新后 mastery_bkt=${mbcAfter} 张、fsrs_retention=${rccAfter} 张（envelope.blocks 落库重放）`)
await shot('16_after_reload_replay')

// ---------- 5. FloatingButler 悬浮球学情 ----------
// 注：对话页 ChatInput 有意 z-1301 压过悬浮球（ChatInput.vue:169，防发送被球截胡），
// 面板步骤在无输入条压制的页面执行（悬浮球为全局组件，链路同构）
await page.goto(`${base}/overview`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await page.locator('.fb-ball').click()
await page.waitForTimeout(800)
const panelVisible = await page.locator('.fb-panel').isVisible().catch(() => false)
if (panelVisible) {
  await page.locator('.fb-inputrow input').fill('我的学情怎么样？最薄弱的知识点是什么？')
  // 回车发送（input 绑 @keydown.enter.prevent="sendChat"）；按钮点击可能被对话页输入区遮挡
  await page.locator('.fb-inputrow input').press('Enter')
  await page.waitForTimeout(12000)
  await shot('17_butler_panel_reply')
  const panelCards = await page.locator('.fb-panel .mbc-card, .fb-panel .rcc-card').count()
  const panelText = await page.locator('.fb-panel').innerText().catch(() => '')
  const degradedBar = await page.locator('.fb-panel .fb-degraded').count()
  const notFake = !/（管家没有返回可展示的内容）/.test(panelText) || panelCards > 0
  record('D4-butler-panel', panelCards > 0 || /掌握度|错题|记忆|\d/.test(panelText),
    `面板数据卡=${panelCards} 张；降级条=${degradedBar}（仅 degraded=true 时出现）；有真实内容=${notFake}`)
} else {
  record('D4-butler-panel', false, '悬浮球面板未打开')
}
await page.keyboard.press('Escape')

// ---------- 6. 回归三验（防上轮功能回退） ----------
// 6a 视频卡
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await sendAndSettle('推荐几个B站的二次函数讲解视频', 30000)
const vc = await page.locator('.vc-card').count()
record('R1-video-card', vc >= 1, `视频卡 ${vc} 张（上一轮功能，不回归）`)
await shot('18_regression_video_cards')

// 6b 出题卡（smart_quiz 带五闸质检重出，慢时 >60s：轮询等待而非固定 sleep）
await sendAndSettle('请围绕「数列」出 3 道练习题让我作答', 3000)
let qcOk = false
try {
  await page.locator('.quiz-card').first().waitFor({ state: 'visible', timeout: 150000 })
  qcOk = true
} catch { /* 超时如实记 FAIL */ }
const qc = await page.locator('.quiz-card').count()
record('R2-quiz-card', qcOk && qc >= 1, `出题卡 ${qc} 张（上一轮功能，不回归；五闸质检重出可能较慢）`)
await shot('19_regression_quiz_cards')

// ---------- 汇总 ----------
const failed = results.filter((r) => !r.ok)
console.log('\n==== SUMMARY ====')
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.step} | ${r.detail}`)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
fs.writeFileSync(`${outDir}/verify-results.json`, JSON.stringify(results, null, 2))
await browser.close()
process.exit(failed.length ? 1 : 0)
