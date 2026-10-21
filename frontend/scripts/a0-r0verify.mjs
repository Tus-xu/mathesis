/**
 * A0 · R0 巡查 #14 指令板执行后的运行时核查（5176 已带修复重启）
 * 核查项：C4 语音公式（服务端真值）/ C1+C2 课时数一致性（服务端真值）/ C12 种子数据卫生 /
 *         C9 死按钮生效（UI）/ C13 工程词出界面（UI）/ C14 问候语时段（UI）
 * 产出：控制台逐项 PASS/FAIL 清单，供 R0 巡查台账转录
 */
import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:5176'
const results = []
const ok = (id, pass, detail) => { results.push([id, pass ? 'PASS' : 'FAIL', detail]); console.log(`${pass ? 'PASS' : 'FAIL'} ${id} — ${detail}`) }

/** 页面内 fetch SSE 并解析事件（服务端真值检查） */
async function sseCall(page, path, body) {
  return await page.evaluate(async ({ path, body }) => {
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer mock-token-teacher-preview', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
    })
    const text = await res.text()
    const events = []
    for (const block of text.split('\n\n')) {
      const ev = block.match(/^event: (.+)$/m)?.[1]
      const dt = block.match(/^data: (.+)$/m)?.[1]
      if (ev && dt) { try { events.push({ event: ev, data: JSON.parse(dt) }) } catch { /* skip */ } }
    }
    return events
  }, { path, body })
}

const run = async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.addInitScript(() => { document.cookie = 'ma_mock_role=teacher; Path=/; SameSite=Lax' })
  await page.goto(BASE + '/teacher-v3/today', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(600)

  /* ---- C4 · 语音公式服务端真值（根号 + 中文数字） ---- */
  const vEvents = await sseCall(page, '/teacher-v3/butler/voice-formula', { text: '根号下x平方加y平方', context: { route: '/teacher-v3/slides' } })
  const vCard = vEvents.find((e) => e.event === 'card')?.data
  const latexOk = vCard?.latex?.includes('\\sqrt') && !vCard.latex.includes('根号')
  ok('C4-server', !!latexOk, `「根号下x平方加y平方」→ ${vCard?.latex || JSON.stringify(vCard)}`)
  const v2 = await sseCall(page, '/teacher-v3/butler/voice-formula', { text: '根号下x加一', context: { route: '/teacher-v3/slides' } })
  const v2Card = v2.find((e) => e.event === 'card')?.data
  ok('C4-server-2', !v2Card?.latex?.includes('根号'), `「根号下x加一」→ ${v2Card?.latex}`)

  /* ---- C1/C2 · 教案生成课时一致性（服务端真值：meta.boards vs sections 数与分钟加总） ---- */
  const pEvents = await sseCall(page, '/teacher-v3/generation/plan', { topic: '椭圆及其标准方程', class_id: 'c2-03', lesson_type: '新授课', template_id: 'lt-explorer' })
  const meta = pEvents.find((e) => e.event === 'meta')?.data
  const sections = pEvents.filter((e) => e.event === 'section').map((e) => e.data.section)
  const sum = sections.reduce((a, s) => a + (s.minutes || 0), 0)
  const headerSum = Number(meta?.total_minutes ?? meta?.boards_total ?? NaN)
  ok('C1-server', sections.length > 0 && !Number.isNaN(sum) && (Number.isNaN(headerSum) || headerSum === sum),
    `meta=${JSON.stringify(meta)} · sections=${sections.length} 个 · 分钟加总=${sum}`)
  ok('C2-server', meta?.boards === sections.length, `meta.boards=${meta?.boards} vs 实发 section=${sections.length}`)

  /* ---- C12 · 种子数据卫生（fresh server） ---- */
  const decks = await page.evaluate(async () => (await (await fetch('/api/teacher-v3/decks', { headers: { Authorization: 'Bearer mock-token-teacher-preview' } })).json()).data.items)
  const plans = await page.evaluate(async () => (await (await fetch('/api/teacher-v3/plans', { headers: { Authorization: 'Bearer mock-token-teacher-preview' } })).json()).data.items)
  const badTitle = (arr) => arr.filter((x) => !x.title && !x.topic ? true : [x.title, x.topic].some((t) => t && t.trim().length === 1))
  const hb = decks.filter((d) => (d.title || '').includes('双曲线'))
  ok('C12-server', badTitle(decks).length === 0 && badTitle(plans).length === 0 && hb.length <= 1,
    `decks=${decks.length} plans=${plans.length} · 单字标题=${badTitle([...decks, ...plans]).length} · 双曲线 deck=${hb.length}`)

  /* ---- C14 + C9 · 课件工坊 UI ---- */
  await page.goto(BASE + '/teacher-v3/slides', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  const hello = await page.locator('.ws-hello').textContent().catch(() => '')
  const h = new Date().getHours()
  const expectHello = (h < 12 ? '上午好' : h < 18 ? '下午好' : '晚上好') + '，李老师'
  ok('C14-ui', hello?.trim().startsWith(expectHello.slice(0, 3)), `问候语="${hello?.trim()}"（当前时段应 ${expectHello.slice(0, 3)}）`)

  // C9：空文本点击 → 引导 toast（不再是「点了没反应」）
  await page.getByTestId('tv3-entry-topic').click()
  await page.waitForTimeout(400)
  const toastVisible = await page.getByText('先描述这节课怎么上').count()
  ok('C9-ui-empty', toastVisible > 0, `空文本点击 → 引导 toast${toastVisible ? '出现' : '未出现'}`)
  // C9：有文本点击 → 进入大纲流程（screen 离开 home）
  await page.getByTestId('tv3-brief-input').fill('高二(5)班《双曲线及其标准方程》第 1 课时，拉线实验引入')
  await page.getByTestId('tv3-entry-topic').click()
  await page.waitForTimeout(500)
  const leftHome = (await page.locator('.ws-hello').count()) === 0
  ok('C9-ui-with-text', leftHome, `带文本点击 → ${leftHome ? '已离开首页进入流程' : '仍在首页（未生效）'}`)

  /* ---- C13 · 批改页 UI ---- */
  await page.goto(BASE + '/teacher-v3/assign', { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1000)
  // C13 文案在作业详情卡内：先打开第一份作业
  await page.locator('.tv3-row').first().click()
  await page.waitForTimeout(800)
  const assignText = await page.locator('body').innerText()
  const hasNew = assignText.includes('AI 已预标错因供参考')
  const leaked = assignText.includes('（ai-flag）')
  ok('C13-ui', hasNew && !leaked, `新文案${hasNew ? '在' : '不在'}场 · 工程词「（ai-flag）」${leaked ? '仍泄漏' : '已清除'}`)

  console.log('\n=== A0 运行时核查汇总 ===')
  for (const [id, verdict, detail] of results) console.log(`${verdict}  ${id}  ${detail}`)
  await browser.close()
}

run().catch((e) => { console.error(e); process.exit(1) })
