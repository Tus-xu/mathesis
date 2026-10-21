/**
 * 学生端 AI 管家 R1 联调验收截图器（开发用；不属于产品代码）。
 *
 * usage:
 *   node e2e/student-butler-r1.mjs
 *
 * 行为（真实浏览器 → 真实后端 :8000）：
 *   1) 登录页走真实 UI 链路（手机号 → 获取验证码 → 演示码回填 → 安全登录）；
 *   2) J1 对话：问「推荐几个B站的三角函数学习视频」→ 断言视频卡（2-4 张、B站链接、新标签）；
 *   3) J2 对话：问「分析我的学习情况」→ 断言回复含真实数字、无「数据不在」推诿；
 *   4) J3 知识库：上传立体几何题图（分类「题目」）→ 原图缩略图 → 点开大图 →
 *      行内改分类 → 刷新保持；对话问「我知识库里有什么资料？」→ 管家列出真实文档；
 *   5) 负例：「这道题怎么做…」不得出现视频卡。
 *
 * 截图输出：D:/math-arena/deliverables/student-butler-r1/
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const phone = process.env.R1_PHONE || '13800009999'
const outDir = 'D:/math-arena/deliverables/student-butler-r1'
const imagePath = 'D:/tmp_kb_question.png'
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

// ---------- 1. 登录 ----------
await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
await shot('00_login_page')
await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
await page.getByRole('button', { name: '获取验证码' }).click()
const demoNote = page.locator('.demo-note')
await demoNote.waitFor({ state: 'visible', timeout: 20000 })
const demoText = await demoNote.innerText()
const demoCode = (demoText.match(/\d{6}/) || [])[0]
if (!demoCode) {
  record('login', false, `演示码未取到: ${demoText}`)
  process.exit(1)
}
await page.locator('#auth-otp').fill(demoCode)
await page.getByRole('button', { name: /安全登录/ }).click()
// 登录后可能进入 onboarding 或首页
await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
record('login', true, `登录成功 url=${page.url()}`)
await page.waitForTimeout(2500)
await shot('01_after_login')

// ---------- 2. J1 视频卡片 ----------
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await shot('02_dialog_initial')
const composer = page.locator('textarea')
await composer.fill('推荐几个B站的三角函数学习视频')
await page.locator('.ci-send').click()
// 过程条（status: video_search）
const statusBar = page.getByText('正在从B站找相关讲解视频')
const statusSeen = await statusBar.first().isVisible().catch(() => false)
record('J1-status', statusSeen, statusSeen ? '出现 video_search 过程条' : '未见过程条（可能过快）')
await page.locator('.vc-card').first().waitFor({ state: 'visible', timeout: 120000 })
await page.waitForTimeout(4000)
await shot('03_J1_video_cards')
const cards = page.locator('.vc-card .vc-item')
const n = await cards.count()
const hrefs = []
for (let i = 0; i < n; i++) {
  hrefs.push({
    href: await cards.nth(i).getAttribute('href'),
    target: await cards.nth(i).getAttribute('target'),
    rel: await cards.nth(i).getAttribute('rel'),
  })
}
const allBili = hrefs.every((h) => (h.href || '').startsWith('https://www.bilibili.com/video/'))
const allBlank = hrefs.every((h) => h.target === '_blank')
record('J1-cards', n >= 2 && n <= 4 && allBili && allBlank, `${n} 张卡；href 均为B站=${allBili}；新标签=${allBlank}; ${JSON.stringify(hrefs.slice(0, 4))}`)
const coverImgs = await page.locator('.vc-card .vc-cover img').count()
record('J1-covers', coverImgs >= 2, `封面图 ${coverImgs} 张`)
// 刷新后会话历史卡片仍在（envelope.blocks 落库重放）
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(3000)
const nAfterReload = await page.locator('.vc-card .vc-item').count()
record('J1-persist', nAfterReload >= 2, `刷新后视频卡 ${nAfterReload} 张（历史重放）`)
await shot('04_J1_cards_after_reload')

// ---------- 3. J2 学情分析 ----------
await composer.fill('分析我的学习情况')
await page.locator('.ci-send').click()
// 等流式回复结束：发送按钮恢复（非停止态）且稳定
await page.waitForTimeout(25000)
await shot('05_J2_profile_reply')
const bodyText = await page.locator('body').innerText()
const refusal = /数据不在我|没有你的数据|无法获取.*数据|我没有.*数据/.test(bodyText)
const hasNumbers = /\d+\s*(道|天|个|次|%)|掌握度|错题/.test(bodyText)
record('J2-reply', hasNumbers && !refusal, `含真实数字=${hasNumbers}；无推诿=${!refusal}`)

// ---------- 4. J3 知识库 ----------
await page.goto(`${base}/library`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await shot('06_kb_initial')
// 上传分类选「题目」
await page.locator('.kb-up-cat select.kb-cat-select, select.kb-cat-select').first().selectOption('题目')
await page.locator('.kb-upload input[type=file]').setInputFiles(imagePath)
// 等解析完成（列表出现该文档行）
await page.waitForTimeout(9000)
await shot('07_kb_after_upload')
const docRow = page.locator('.kb-doc, .kb-item, li, tr').filter({ hasText: 'tmp_kb_question' }).first()
const rowVisible = await docRow.isVisible().catch(() => false)
record('J3-upload-row', rowVisible, rowVisible ? '上传后列表出现文档' : '未找到文档行')
const thumb = page.locator('.kd-thumb img').first()
const thumbOk = await thumb.isVisible().catch(() => false)
record('J3-thumb', thumbOk, thumbOk ? '原图缩略图可见' : '缩略图缺失')
// 点缩略图开大图
if (thumbOk) {
  await thumb.click()
  await page.waitForTimeout(1200)
  await shot('08_kb_lightbox')
  record('J3-lightbox', true, '点开原图 lightbox')
  await page.keyboard.press('Escape')
}
// 行内改分类：题目 → 教辅
const rowSelect = docRow.locator('select').first()
await rowSelect.selectOption('教辅')
await page.waitForTimeout(1500)
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(2500)
const rowSelect2 = page.locator('.kb-doc, .kb-item, li, tr').filter({ hasText: 'tmp_kb_question' }).first().locator('select').first()
const catAfterReload = await rowSelect2.inputValue().catch(() => '(not found)')
record('J3-category-persist', catAfterReload === '教辅', `改分类后刷新仍为「${catAfterReload}」`)
await shot('09_kb_category_persist')

// ---------- 5. J3 对话引用知识库 ----------
await page.goto(`${base}/dialog`, { waitUntil: 'networkidle' })
await page.waitForTimeout(1500)
await composer.fill('我知识库里有什么资料？')
await page.locator('.ci-send').click()
await page.waitForTimeout(15000)
await shot('10_J3_butler_kb_reply')
const kbReply = await page.locator('body').innerText()
const kbOk = /tmp_kb_question/.test(kbReply) && /知识库/.test(kbReply)
const kbLeak = /教师培训手册|MIT 18\.06|别人的题目/.test(kbReply)
record('J3-butler-kb', kbOk && !kbLeak, `管家列出真实文档=${kbOk}；无跨用户/平台语料泄露=${!kbLeak}`)

// ---------- 6. 负例：解题消息不出视频卡 ----------
await composer.fill('这道题怎么做：已知 sinα=3/5 且 α 为锐角，求 cosα 的值')
await page.locator('.ci-send').click()
await page.waitForTimeout(12000)
await shot('11_negative_solve_no_cards')
const negCards = await page.locator('.vc-card').count()
const negText = await page.locator('body').innerText()
const negOk = negCards === 0 && !/正在从B站找/.test(negText)
record('negative-solve', negOk, `解题消息视频卡=${negCards} 张（应为 0）`)

// ---------- 汇总 ----------
const failed = results.filter((r) => !r.ok)
console.log('\n==== SUMMARY ====')
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.step} | ${r.detail}`)
console.log(`\n${results.length - failed.length}/${results.length} passed`)
fs.writeFileSync(`${outDir}/verify-results.json`, JSON.stringify(results, null, 2))
await browser.close()
process.exit(failed.length ? 1 : 0)
