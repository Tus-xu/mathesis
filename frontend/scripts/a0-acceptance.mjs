/**
 * A0 阶段2 自验收 · 用户链路截图（mock 演示模式）
 * 用法：node scripts/a0-acceptance.mjs
 * 产出：D:/teacher-research/acceptance-frontend/*.png（全页截图，带 URL 水印标注在文件名）
 * 覆盖：九视图 + 课堂双端（教师开课 → 学生 H5 输码加入 → 推题 → 作答）+ 40301 提示页
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const OUT = 'D:/teacher-research/acceptance-frontend'
const BASE = 'http://127.0.0.1:5176'
mkdirSync(OUT, { recursive: true })

const run = async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  // mock 教师身份（mock 服务按 ma_mock_role 签发 token）
  await page.addInitScript(() => { document.cookie = 'ma_mock_role=teacher; Path=/; SameSite=Lax' })

  const shots = []
  const shot = async (name, url, { before } = {}) => {
    await page.goto(BASE + url, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1200)
    if (before) await before()
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true })
    shots.push([name, page.url()])
    console.log('shot', name)
  }

  /* ---- 九视图 ---- */
  await shot('L1-today', '/teacher-v3/today')
  await shot('L3-prep-desk', '/teacher-v3/prep')
  await shot('L4-slides-home', '/teacher-v3/slides')
  await shot('L8-bank', '/teacher-v3/bank')
  await shot('L8-quiz', '/teacher-v3/quiz')
  await shot('L6-assign', '/teacher-v3/assign')
  await shot('insights', '/teacher-v3/insights')
  await shot('resources', '/teacher-v3/resources')
  await shot('L9-classroom-idle', '/teacher-v3/classroom')

  /* ---- L9 课堂双端 ---- */
  await page.goto(BASE + '/teacher-v3/classroom', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  await page.getByTestId('tv3-open-session').click()
  await page.waitForTimeout(1200)
  const joinCode = await page.getByTestId('tv3-join-code').textContent()
  const code = (joinCode.match(/\d{6}/) || [''])[0]
  await page.screenshot({ path: `${OUT}/L9-classroom-session.png`, fullPage: true })
  shots.push(['L9-classroom-session', page.url()])
  console.log('shot L9-classroom-session, join_code =', code)

  // 学生 H5（独立上下文，无教师身份）
  const sctx = await browser.newContext({ viewport: { width: 420, height: 860 } })
  const sp = await sctx.newPage()
  await sp.goto(BASE + '/classroom-h5', { waitUntil: 'domcontentloaded' })
  await sp.getByTestId('h5-join-code').fill(code)
  await sp.getByTestId('h5-join-name').fill('王小明')
  await sp.getByTestId('h5-join-go').click()
  await sp.waitForTimeout(1500)
  await sp.screenshot({ path: `${OUT}/L9-h5-joined.png`, fullPage: true })
  shots.push(['L9-h5-joined', sp.url()])
  console.log('shot L9-h5-joined')

  // 教师推一道题（若题库可点）
  const q = page.locator('[data-testid^="tv3-question-"]').first()
  if (await q.count()) {
    await q.click()
    await page.waitForTimeout(800)
    await page.screenshot({ path: `${OUT}/L9-classroom-push.png`, fullPage: true })
    shots.push(['L9-classroom-push', page.url()])
    console.log('shot L9-classroom-push')

    // 学生端作答（等学生流投影出题——live 事件即达，留足投影与渲染时间）
    let answered = false
    for (let i = 0; i < 10 && !answered; i++) {
      await sp.waitForTimeout(600)
      answered = (await sp.locator('[data-testid^="h5-opt-"]').count()) > 0
    }
    await sp.screenshot({ path: `${OUT}/L9-h5-question.png`, fullPage: true })
    shots.push(['L9-h5-question', sp.url()])
    console.log('shot L9-h5-question, options =', answered)
    if (answered) {
      await sp.locator('[data-testid^="h5-opt-"]').first().click()
      await sp.waitForTimeout(600)
      await sp.screenshot({ path: `${OUT}/L9-h5-answered.png`, fullPage: true })
      shots.push(['L9-h5-answered', sp.url()])
      console.log('shot L9-h5-answered')
      // 教师端统计应来自服务端事件（≥1 已作答）
      await page.waitForTimeout(800)
      await page.screenshot({ path: `${OUT}/L9-classroom-stats.png`, fullPage: true })
      shots.push(['L9-classroom-stats', page.url()])
      console.log('shot L9-classroom-stats')
    } else {
      console.log('H5 未见作答选项（投影状态见 L9-h5-question.png）')
    }
  }

  /* ---- 40301 提示页（学生身份访问教师端 → 统一落点） ---- */
  const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const dp = await dctx.newPage()
  await dp.addInitScript(() => { document.cookie = 'ma_mock_role=student; Path=/; SameSite=Lax' })
  await dp.goto(BASE + '/teacher-v3/today', { waitUntil: 'domcontentloaded' })
  await dp.waitForTimeout(2500)
  await dp.screenshot({ path: `${OUT}/denied-40301.png`, fullPage: true })
  shots.push(['denied-40301', dp.url()])
  console.log('shot denied-40301, landed at', dp.url())

  console.log('\n=== 产出清单 ===')
  for (const [n, u] of shots) console.log(`${n}.png  <-  ${u}`)
  await browser.close()
}

run().catch((e) => { console.error(e); process.exit(1) })
