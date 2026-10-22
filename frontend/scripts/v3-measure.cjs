// V3 编辑器主画布公式测量（选最大 .v3sc）+ 404 资源明细
const { chromium } = require('playwright')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')

;(async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 900 } })
  await ctx.addCookies([{ name: 'ma_mock_role', value: 'teacher', url: 'http://127.0.0.1:5177' }])
  const page = await ctx.newPage()
  const errors = []
  const failed = []
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', (e) => errors.push('PAGEERROR: ' + e.message))
  page.on('requestfailed', (r) => failed.push(r.url() + ' :: ' + (r.failure()?.errorText || '')))
  page.on('response', (r) => { if (r.status() === 404) failed.push('404: ' + r.url()) })

  await page.goto('http://127.0.0.1:5177/teacher-v3/slides', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  await page.locator('.tv3-qcard').first().click()
  await page.waitForTimeout(2500)

  const data = await page.evaluate(() => {
    const rect = (x) => { const r = x.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
    const canvases = [...document.querySelectorAll('.v3sc')]
    const canvas = canvases.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0]
    const f = canvas.querySelector('.v3sc__formula')
    const el = f ? f.parentElement : null
    const kd = f ? f.querySelector('.katex-display') : null
    const inner = f ? (f.querySelector('.katex-html') || f.querySelector('.katex')) : null
    const texts = [...canvas.querySelectorAll('.v3sc__text')]
    const katexEls = f ? [...f.querySelectorAll('.katex-html .mord, .katex-html .mbin, .katex-html .mrel')].length : 0
    return {
      canvasCount: canvases.length,
      mainCanvas: rect(canvas),
      canvasOverflow: getComputedStyle(canvas).overflow,
      formulaEl: el ? rect(el) : null,
      formulaBox: f ? rect(f) : null,
      formulaScrollW: f ? f.scrollWidth : null,
      katexDisplay: kd ? rect(kd) : null,
      katexInner: inner ? rect(inner) : null,
      katexInnerText: inner ? inner.textContent : null,
      latexHasX2: f ? f.innerHTML.includes('x^{2}') : null,
      texts: texts.slice(0, 2).map((t) => ({ rect: rect(t), scrollW: t.scrollWidth, clientW: t.clientWidth, text: t.textContent.slice(0, 40) })),
    }
  })
  console.log(JSON.stringify(data, null, 2))
  const shotDir = process.env.OUT || path.join(os.tmpdir(), 'trae-v3-walk')
  fs.mkdirSync(shotDir, { recursive: true })
  await page.screenshot({ path: path.join(shotDir, 'main-canvas.png') })
  console.log('failed/404 resources:', failed.length ? failed : '(none)')
  console.log('console errors:', errors.length ? errors : '(none)')
  await browser.close()
})().catch((e) => { console.error('FATAL', e); process.exit(1) })
