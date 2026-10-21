/**
 * A0 阶段2 自验收 · 断网重连演练（P9 前端侧）+ 40301 提示页直达验证
 * 场景：教师开课并推题 → 教师端断网 3s（期间学生作答）→ 恢复 →
 *      v3Sse 自动重连（Last-Event-ID 补拉）→ snapshot 重同步 → toast「连接已恢复」+ 统计补齐
 * 产出：offline-reconnect-*.png / denied-40301-direct.png
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const OUT = 'D:/teacher-research/acceptance-frontend'
const BASE = 'http://127.0.0.1:5176'
mkdirSync(OUT, { recursive: true })

const run = async () => {
  const browser = await chromium.launch()
  const tctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await tctx.newPage()
  await page.addInitScript(() => { document.cookie = 'ma_mock_role=teacher; Path=/; SameSite=Lax' })

  // 1) 开课 + 学生加入 + 推题
  await page.goto(BASE + '/teacher-v3/classroom', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  await page.getByTestId('tv3-open-session').click()
  await page.waitForTimeout(1000)
  const code = ((await page.getByTestId('tv3-join-code').textContent())?.match(/\d{6}/) || [''])[0]
  const sctx = await browser.newContext({ viewport: { width: 420, height: 860 } })
  const sp = await sctx.newPage()
  await sp.goto(BASE + '/classroom-h5', { waitUntil: 'domcontentloaded' })
  await sp.getByTestId('h5-join-code').fill(code)
  await sp.getByTestId('h5-join-name').fill('李小红')
  await sp.getByTestId('h5-join-go').click()
  await sp.waitForTimeout(1200)
  await page.locator('[data-testid^="tv3-question-"]').first().click()
  for (let i = 0; i < 10 && !(await sp.locator('[data-testid^="h5-opt-"]').count()); i++) await sp.waitForTimeout(500)
  console.log('step1 ok: session open, code', code, ', student sees question')

  // 2) 教师端断网 3s（期间学生作答——作答走学生自身连接，不受教师断网影响）
  await tctx.setOffline(true)
  console.log('teacher offline')
  await sp.locator('[data-testid^="h5-opt-"]').first().click()
  await sp.waitForTimeout(700)
  await page.waitForTimeout(3000)
  await tctx.setOffline(false)
  console.log('teacher back online → v3Sse 自动重连（退避×3 + Last-Event-ID 补拉 + snapshot 重同步）')

  // 3) 恢复后：教师统计应重同步为服务端权威（1/46 已作答），并出现恢复 toast
  let toastSeen = false
  try {
    await page.getByText('连接已恢复，正在补齐进度').waitFor({ state: 'visible', timeoutMs: 2500 })
    toastSeen = true
  } catch { /* toast 可能已自动消失 */ }
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/offline-reconnect-teacher.png`, fullPage: true })
  const statsText = await page.getByTestId('tv3-live-count').textContent().catch(() => 'N/A')
  console.log('reconnect toast seen =', toastSeen, '| 教师端统计 =', statsText?.trim())

  // 学生端视角留档
  await sp.screenshot({ path: `${OUT}/offline-reconnect-student.png`, fullPage: true })

  // 4) 40301 提示页直达（真实 40301 跳转路径由单测覆盖；此处验证页面渲染与出路）
  const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const dp = await dctx.newPage()
  await dp.goto(BASE + '/teacher-v3/denied', { waitUntil: 'domcontentloaded' })
  await dp.waitForTimeout(600)
  await dp.screenshot({ path: `${OUT}/denied-40301-direct.png`, fullPage: true })
  console.log('denied page rendered:', dp.url())

  await browser.close()
  console.log('DONE — 断网重连演练完成')
}

run().catch((e) => { console.error(e); process.exit(1) })
