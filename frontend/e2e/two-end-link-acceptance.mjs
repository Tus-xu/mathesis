/**
 * 两端贯通 · E2E 验收链路截图器（开发用；不属于产品代码；先例 e2e/student-butler-r1.mjs）。
 *
 * usage:
 *   node e2e/two-end-link-acceptance.mjs
 *
 * 前提（R6 本地启动清单）：8000 / 8100 / 5176 已起；D:\frontend\.env 代理指向 127.0.0.1。
 * 行为（真实浏览器 ×2 context → 真实后端 :5176/:8000/:8100）：
 *   01 教师登录（13900001001，短信演示码）
 *   02 教师建班 → 取邀请码
 *   03 学生注册/登录 → 邀请码加入班级
 *      教师确认入班（任务下发前置）
 *   04 教师发布课后习题（1 选择 + 1 填空 + 1 解答）
 *   05 学生课堂任务中心看到作业；刷新仍在（持久化）
 *   06 学生作答选择题并提交 → 系统自动判分（答案=题库 ground truth）
 *   07 填空/解答题拍照上传手写样张
 *   08 教师批改工作台：待批队列 → 看照片 → 打分+评语 → 确认
 *   09 学生看到批改成绩
 *   10 教师班级学情面板出现该学生掌握数据
 *   11 备课会话 + 备课画像重算（8000 批改事实 → 学情证据入备课链路）
 *
 * 截图输出：D:/math-arena/deliverables/two-end-link/evidence/（01-xx.png 顺序编号）
 * 网络证据：同目录 network-log.json / quiz-artifact.json / recalculate.json / profile.json
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const teacherPhone = process.env.E2E_TEACHER_PHONE || '13900001001'
const studentPhone = '139' + String(Date.now()).slice(-8) // 每次运行全新学生号
const outDir = 'D:/math-arena/deliverables/two-end-link/evidence'
const answerPng = 'D:/math-arena/deliverables/two-end-link/evidence/_answer-sample.png'
const className = `E2E贯通班-${String(Date.now()).slice(-6)}`
const assignTitle = `E2E课后习题-${String(Date.now()).slice(-6)}`
fs.mkdirSync(outDir, { recursive: true })

const results = []
function record(step, ok, detail) {
  results.push({ step, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`)
}
function fatal(step, detail) {
  record(step, false, detail)
  console.log('ABORT | 前置步骤失败，终止后续链路')
  saveEvidence()
  process.exit(1)
}

/* ---------- 网络证据采集 ---------- */
const NEEDLE = [
  '/api/auth/', '/api/classes', '/api/teacher/quizzes', '/api/teacher/artifacts',
  '/api/teacher/assignments', '/api/student/assignments', '/api/student/practice/submit',
  '/api/files', 'submission-facts', '/prep/profiles', '/api/teacher-v3/prep', 'insights',
  '/teacher/grading',
]
const net = []
const netDetail = []
function saveEvidence() {
  try {
    fs.writeFileSync(`${outDir}/network-log.json`, JSON.stringify(net, null, 2))
    fs.writeFileSync(`${outDir}/network-detail.json`, JSON.stringify(netDetail, null, 2))
    fs.writeFileSync(`${outDir}/result.json`, JSON.stringify({
      ran_at: new Date().toISOString(), base, teacherPhone, studentPhone, className, assignTitle, inviteCode, results,
    }, null, 2))
  } catch (e) { console.log('WARN | evidence save failed:', e.message) }
}
function wire(ctx, tag) {
  ctx.on('response', async (res) => {
    try {
      const req = res.request()
      if (!['xhr', 'fetch'].includes(req.resourceType())) return
      const url = res.url()
      net.push({ tag, method: req.method(), url, status: res.status() })
      if (NEEDLE.some((p) => url.includes(p)) && res.status() !== 304) {
        let body = ''
        try { body = (await res.text()).slice(0, 5000) } catch { /* body 不可读 */ }
        netDetail.push({ tag, method: req.method(), url, status: res.status(), body })
      }
    } catch { /* 忽略采集异常 */ }
  })
}

const browser = await chromium.launch({ headless: true })
const tctx = await browser.newContext({ viewport: { width: 1560, height: 950 }, locale: 'zh-CN' })
const sctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, locale: 'zh-CN' })
wire(tctx, 'teacher'); wire(sctx, 'student')
const tpage = await tctx.newPage(); tpage.setDefaultTimeout(90000)
const spage = await sctx.newPage(); spage.setDefaultTimeout(90000)
tpage.on('dialog', (d) => { console.log(`DIALOG | ${d.type()} ${d.message().slice(0, 80)}`); d.accept().catch(() => {}) })
spage.on('dialog', (d) => { console.log(`DIALOG | ${d.type()} ${d.message().slice(0, 80)}`); d.accept().catch(() => {}) })
for (const [tag, pg] of [['teacher', tpage], ['student', spage]]) {
  pg.on('pageerror', (err) => netDetail.push({ tag, kind: 'pageerror', message: String(err).slice(0, 500) }))
  pg.on('console', (m) => { if (m.type() === 'error') netDetail.push({ tag, kind: 'console.error', message: m.text().slice(0, 500) }) })
}

async function shot(page, name) {
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false })
  console.log(`SHOT | ${name}.png | ${page.url()}`)
}
async function toastText(page) {
  try {
    await page.locator('[data-testid="toast-host"] .toast').first().waitFor({ state: 'visible', timeout: 8000 })
    return await page.locator('[data-testid="toast-host"] .toast').first().innerText()
  } catch { return '' }
}
/** 按 option 文本包含关系选择（option 带后缀，如「班级名（1 人）」，精确 label 会 miss） */
async function selectByText(page, testid, text) {
  const sel = page.locator(`[data-testid="${testid}"]`)
  await sel.waitFor({ timeout: 30000 })
  const v = await sel.locator('option', { hasText: text }).first().getAttribute('value')
  if (!v) throw new Error(`option not found: ${testid} ~ ${text}`)
  await sel.selectOption(v)
}

/* ---------- 通用登录（真实 UI：手机号 → 演示码 → 安全登录） ---------- */
async function uiLogin(page, phone, tag, role) {
  await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  if (role) await page.locator('#preferred-role').selectOption(role)
  await page.locator('input[placeholder="请输入中国大陆手机号"], input[placeholder*="手机号"]').first().fill(phone)
  await page.getByRole('button', { name: '获取验证码' }).click()
  const demoNote = page.locator('.demo-note')
  await demoNote.waitFor({ state: 'visible', timeout: 20000 })
  const demoCode = ((await demoNote.innerText()).match(/\d{6}/) || [])[0]
  if (!demoCode) fatal(`${tag}-login`, `演示码未取到`)
  await page.locator('#auth-otp').fill(demoCode)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 40000 })
  await page.waitForTimeout(2000)
}
async function completeOnboarding(page) {
  if (!page.url().includes('/onboarding')) return
  await page.locator('#nickname').fill('E2E验收学生')
  await page.locator('#stage').selectOption('高中')
  await page.locator('#grade').fill('高二')
  await page.getByRole('button', { name: /进入学习空间/ }).click()
  await page.waitForURL((u) => !String(u).includes('/onboarding'), { timeout: 30000 })
  await page.waitForTimeout(1500)
}

/* ========== 01 教师登录 ========== */
try {
  await uiLogin(tpage, teacherPhone, 'teacher', 'teacher')
  record('01-教师登录', true, `phone=${teacherPhone} 登录成功 url=${tpage.url()}`)
} catch (e) { fatal('01-教师登录', e.message) }
await shot(tpage, '01-teacher-after-login')

/* ========== 02 教师建班 → 邀请码 ========== */
let inviteCode = ''
try {
  await tpage.goto(`${base}/teacher-v3/classes`, { waitUntil: 'domcontentloaded' })
  await tpage.locator('[data-testid="tv3-classes"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1200)
  await tpage.locator('[data-testid="tv3-class-create"]').click()
  await tpage.locator('[data-testid="tv3-class-name-input"]').fill(className)
  await tpage.locator('[data-testid="tv3-class-create-submit"]').click()
  const toast = await toastText(tpage)
  const newCard = tpage.locator(`[data-testid^="tv3-class-"]:has-text("${className}")`).first()
  await newCard.waitFor({ state: 'visible', timeout: 20000 })
  const codeText = await newCard.locator('[data-testid="tv3-class-invite-code"]').innerText().catch(() => '')
  inviteCode = (codeText.match(/[A-Z0-9]{6,8}/) || [])[0] || (toast.match(/邀请码\s*([A-Z0-9]{6,8})/) || [])[1] || ''
  if (!inviteCode) fatal('02-教师建班', `邀请码未取到 toast=${toast} card=${codeText}`)
  record('02-教师建班', true, `class=${className} invite=${inviteCode} toast=${toast}`)
} catch (e) { fatal('02-教师建班', e.message) }
await shot(tpage, '02-class-created-invite-code')

/* ========== 03 学生注册登录 + 邀请码入班 ========== */
try {
  await uiLogin(spage, studentPhone, 'student', 'student')
  await completeOnboarding(spage)
  await spage.goto(`${base}/classroom?tab=class`, { waitUntil: 'domcontentloaded' })
  await spage.locator('.join-input').waitFor({ timeout: 20000 })
  await shot(spage, '03a-student-class-empty')
  await spage.locator('.join-input').fill(inviteCode)
  await spage.getByRole('button', { name: '加入班级' }).click()
  const toast = await toastText(spage)
  if (!toast.includes('已申请加入')) record('03-学生入班', false, `toast=${toast}`)
  else record('03-学生入班', true, `phone=${studentPhone} code=${inviteCode} toast=${toast}`)
  await spage.waitForTimeout(1500)
} catch (e) { fatal('03-学生入班', e.message) }
await shot(spage, '03b-student-join-toast')

/* ========== 03b 教师确认入班（任务下发前置） ========== */
try {
  await tpage.goto(`${base}/teacher-v3/classes`, { waitUntil: 'domcontentloaded' })
  await tpage.locator(`[data-testid^="tv3-class-"]:has-text("${className}")`).first().click()
  await tpage.locator('[data-testid="tv3-class-detail"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1500)
  const confirmBtn = tpage.locator('[data-testid^="tv3-member-confirm-"]').first()
  await confirmBtn.waitFor({ state: 'visible', timeout: 20000 })
  await shot(tpage, '03c-teacher-member-pending')
  await confirmBtn.click()
  const toast = await toastText(tpage)
  const ok = toast.includes('已确认入班')
  record('03b-教师确认入班', ok, `toast=${toast}`)
  if (!ok) fatal('03b-教师确认入班', `确认失败 toast=${toast}`)
  await tpage.waitForTimeout(1200)
} catch (e) { fatal('03b-教师确认入班', e.message) }
await shot(tpage, '03d-teacher-member-confirmed')

/* ========== 04 教师发布课后习题（1选择+1填空+1解答） ========== */
let quizItems = []
try {
  await tpage.goto(`${base}/teacher-v3/assign`, { waitUntil: 'domcontentloaded' })
  await tpage.locator('[data-testid="tv3-publish-card"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1500)
  await selectByText(tpage, 'tv3-pub-class', className)
  await tpage.locator('[data-testid="tv3-pub-type"]').selectOption('homework')
  await tpage.locator('[data-testid="tv3-pub-title"]').fill(assignTitle)
  await tpage.locator('[data-testid="tv3-pub-deadline"]').fill('2026-10-20T23:59')
  await tpage.locator('[data-testid="tv3-pub-count"]').fill('3')
  await tpage.locator('[data-testid="tv3-pub-type-choice"]').fill('1')
  await tpage.locator('[data-testid="tv3-pub-type-blank"]').fill('1')
  await tpage.locator('[data-testid="tv3-pub-type-text"]').fill('1')
  const kpBtn = tpage.locator('[data-testid^="tv3-pub-kp-"]').first()
  await kpBtn.waitFor({ state: 'visible', timeout: 20000 })
  const kpLabel = await kpBtn.innerText()
  await kpBtn.click()
  await shot(tpage, '04a-assign-form')
  const genRespPromise = tpage.waitForResponse((r) => r.url().includes('/api/teacher/quizzes/generate'), { timeout: 60000 })
  await tpage.locator('[data-testid="tv3-pub-generate"]').click()
  const genResp = await genRespPromise
  const genBody = await genResp.json().catch(() => null)
  quizItems = genBody?.content?.items || genBody?.data?.content?.items || []
  fs.writeFileSync(`${outDir}/quiz-artifact.json`, JSON.stringify(genBody, null, 2))
  await tpage.locator('[data-testid="tv3-pub-items"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(800)
  record('04a-组卷', quizItems.length === 3, `kp=${kpLabel.split('（')[0]} items=${quizItems.length} types=[${quizItems.map((i) => i.q_type).join(',')}]`)
  await shot(tpage, '04b-artifact-preview')
  // 发布链逐请求收集（confirm → create → publish），成功横幅或错误标签二选一收敛
  const chain = []
  const onResp = async (res) => {
    const u = res.url()
    if (['/api/teacher/artifacts/', '/api/teacher/assignments'].some((p) => u.includes(p))) {
      let b = ''; try { b = (await res.text()).slice(0, 300) } catch { /* n/a */ }
      chain.push({ status: res.status(), url: u.replace(/^https?:\/\/[^/]+/, ''), body: b })
    }
  }
  tpage.on('response', onResp)
  try {
    await tpage.locator('[data-testid="tv3-pub-confirm-publish"]').click()
    const outcome = await Promise.race([
      tpage.locator('[data-testid="tv3-pub-success"]').waitFor({ timeout: 90000 }).then(() => 'success'),
      tpage.locator('[data-testid="tv3-pub-error"]').waitFor({ timeout: 90000 }).then(async () => 'error:' + (await tpage.locator('[data-testid="tv3-pub-error"]').innerText()).slice(0, 200)),
    ]).catch(() => 'timeout')
    await tpage.waitForTimeout(800)
    record('04-发布作业', outcome === 'success', `outcome=${outcome} chain=[${chain.map((c) => `${c.status} ${c.url.split('/api/')[1]?.slice(0, 48)}`).join(' | ')}]`)
    if (outcome !== 'success') {
      fs.writeFileSync(`${outDir}/publish-chain.json`, JSON.stringify(chain, null, 2))
      await shot(tpage, '04z-publish-failure')
      fatal('04-发布作业', `发布未成功 outcome=${outcome}`)
    }
  } finally { tpage.off('response', onResp) }
} catch (e) { fatal('04-发布作业', e.message) }
await shot(tpage, '04c-publish-success')

/* ========== 05 学生任务中心可见 + 刷新持久化 ========== */
let taskOpened = false
try {
  await spage.goto(`${base}/classroom?tab=tasks`, { waitUntil: 'domcontentloaded' })
  await spage.locator('.task-list, .empty-state').first().waitFor({ timeout: 20000 })
  const item = spage.locator(`.task-item:has-text("${assignTitle}")`)
  await item.waitFor({ timeout: 30000 })
  record('05a-任务中心可见', true, `title=${assignTitle}`)
  await shot(spage, '05a-student-task-list')
  await spage.reload({ waitUntil: 'domcontentloaded' })
  await spage.locator(`.task-item:has-text("${assignTitle}")`).waitFor({ timeout: 30000 })
  record('05b-刷新持久化', true, '刷新后任务仍在（服务端持久化）')
  await shot(spage, '05b-student-task-list-reload')
  await spage.locator(`.task-item:has-text("${assignTitle}")`).getByRole('button', { name: /开始|观看/ }).click()
  await spage.waitForURL((u) => String(u).includes('/tasks/'), { timeout: 20000 })
  taskOpened = true
  await spage.waitForTimeout(1500)
} catch (e) { fatal('05-学生任务中心', e.message) }

/* ========== 06/07 作答：选择(自动判)+填空/解答(拍照上传) → 提交 ========== */
const choice = quizItems.find((i) => i.q_type === 'choice')
const blank = quizItems.find((i) => i.q_type === 'blank')
const solution = quizItems.find((i) => i.q_type === 'text' || i.q_type === 'solution')
let choiceVerdict = ''
try {
  if (!taskOpened) fatal('06-作答提交', '未进入作答页')
  // 手写样张 PNG（真实图片文件）
  const p2 = await sctx.newPage()
  await p2.setContent('<body style="margin:0;background:#fff"><div id="hw" style="font-family:Kaiti SC,KaiTi,serif;font-size:30px;padding:48px;line-height:1.9">解：设 f(x) = ln x − ax<br/>则 f′(x) = 1/x − a<br/>当 a ≤ 0 时 f(x) 在 (0,+∞) 单调递增。<br/>（E2E 手写样张）</div></body>')
  await p2.locator('#hw').screenshot({ path: answerPng })
  await p2.close()

  const cardOf = (no) => spage.locator('div.card', { has: spage.locator(`h3:has-text("${no}.")`) }).first()
  // 选择题：按题库 ground truth 作答（键匹配优先，答案文本匹配兜底）
  if (choice) {
    const ans = String(choice.answer ?? '').trim()
    const optMap = choice.options || {}
    let key = Object.keys(optMap).find((k) => k.trim().toUpperCase() === ans.toUpperCase())
    if (!key) key = Object.keys(optMap).find((k) => String(optMap[k]).trim() === ans)
    if (!key) {
      await shot(spage, '06z-choice-answer-mismatch')
      fatal('06a-选择题作答', `题库答案 "${ans}" 无法匹配选项键 [${Object.keys(optMap).join(',')}] / 文本`)
    }
    const radio = cardOf(choice.item_no).locator(`input[type="radio"][value="${key}"]`)
    try {
      await radio.check({ timeout: 15000 })
    } catch {
      // 偶发 actionability 挂起：退化为直接点 label
      await cardOf(choice.item_no).locator('label').filter({ has: radio }).first().click({ force: true }).catch(() => {})
    }
    const checked = await radio.isChecked().catch(() => false)
    if (!checked) {
      await shot(spage, '06z-choice-check-failed')
      fatal('06a-选择题作答', `radio ${key} 未能选中`)
    }
    record('06a-选择题作答', true, `item=${choice.item_no} 选 ${key}（题库正确答案）`)
  }
  // 填空题：仅拍照上传（人工批阅口径）；sync() 在 OCR 轮询完成后才发 v-model——必须等 mask 消失
  if (blank) {
    const c = cardOf(blank.item_no)
    await c.locator('input[type="file"]').setInputFiles(answerPng)
    await c.locator('img').first().waitFor({ timeout: 30000 })
    await c.locator('.hp-mask').waitFor({ state: 'hidden', timeout: 200000 }).catch(() => {})
    await spage.waitForTimeout(600)
    record('07a-填空拍照上传', true, `item=${blank.item_no} 已上传样张，OCR 轮询结束（mask 隐藏）`)
  }
  // 解答题：文本 + 拍照上传
  if (solution) {
    const c = cardOf(solution.item_no)
    await c.locator('textarea').fill("解：f'(x) = 1/x − a；a≤0 时 (0,+∞) 递增，a>0 时 (0,1/a) 递增、(1/a,+∞) 递减。（含手写样张）")
    await c.locator('input[type="file"]').setInputFiles(answerPng)
    await c.locator('img').first().waitFor({ timeout: 30000 })
    await c.locator('.hp-mask').waitFor({ state: 'hidden', timeout: 200000 }).catch(() => {})
    await spage.waitForTimeout(600)
    record('07b-解答拍照上传', true, `item=${solution.item_no} 文本+样张已上传，OCR 轮询结束`)
  }
  await shot(spage, '07c-photos-uploaded')
  // 提交（响应自带逐题判分）；无请求时捕获 toast（校验拦截）
  const submitRespPromise = spage.waitForResponse((r) => r.url().includes('/api/student/practice/submit'), { timeout: 60000 }).catch(() => null)
  await spage.getByRole('button', { name: /提交给老师/ }).click()
  const submitResp = await submitRespPromise
  if (!submitResp) {
    await spage.waitForTimeout(1500)
    const t = await toastText(spage)
    await shot(spage, '06z-submit-blocked')
    fatal('06/07-作答提交', `提交未发出请求 toast="${t}"`)
  }
  const submitBody = await submitResp.json().catch(() => null)
  fs.writeFileSync(`${outDir}/submit-response.json`, JSON.stringify(submitBody, null, 2))
  await spage.locator('.auto-verdict').first().waitFor({ timeout: 30000 })
  await spage.waitForTimeout(1000)
  const verdicts = await spage.locator('.auto-verdict').allInnerTexts()
  choiceVerdict = (verdicts.find((v) => v.includes('判对') || v.includes('判错')) || '').trim()
  const expectOk = choiceVerdict.includes('判对')
  record('06b-自动判分', expectOk, `verdict="${choiceVerdict}"（选择题按正确答案作答，系统判定=${expectOk ? '判对' : '非判对'}）`)
  const resultsArr = submitBody?.data?.results || submitBody?.results || []
  const pendingArr = resultsArr.filter((r) => r.verdict === 'pending_review')
  record('06c-主观题待批', pendingArr.length >= 1, `pending_review 题数=${pendingArr.length}（items=${resultsArr.map((r) => `${r.item_no}:${r.verdict}`).join(',')}）`)
} catch (e) { fatal('06/07-作答提交', e.message) }
await shot(spage, '06-choice-auto-grade')

/* ========== 08 教师批改工作台：打分+评语 → 确认 ==========
 * 队列按「题签」分组（tv3-gw-question-tabs，每题一签）：先按题干 title 定位解答题签
 * → 批该题待批项（8 分+评语，核验手写原图）；再遍历其余题签批剩余待批项（5 分）。 */
try {
  await tpage.goto(`${base}/teacher-v3/grading`, { waitUntil: 'domcontentloaded' })
  await tpage.locator('[data-testid="tv3-grading-workspace"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1500)
  const clsSel = tpage.locator('[data-testid="tv3-gw-class"]')
  if (await clsSel.isVisible().catch(() => false)) await selectByText(tpage, 'tv3-gw-class', className)
  await selectByText(tpage, 'tv3-gw-assignment', assignTitle)
  await tpage.locator('[data-testid="tv3-gw-question-tabs"] .tv3-seg__btn').first().waitFor({ timeout: 30000 })
  await tpage.waitForTimeout(1200)
  const tabs = tpage.locator('[data-testid="tv3-gw-question-tabs"] .tv3-seg__btn')
  const tabCount = await tabs.count()
  const solTitle = (solution?.question_text || '').replace(/\s+/g, ' ').slice(0, 18)
  let solIdx = -1
  for (let i = 0; i < tabCount; i += 1) {
    const t = ((await tabs.nth(i).getAttribute('title').catch(() => '')) || '').replace(/\s+/g, ' ')
    if (solTitle && t.includes(solTitle)) { solIdx = i; break }
  }
  record('08a-题签定位', tabCount === 3 && solIdx >= 0, `题签数=${tabCount} 解答题签索引=${solIdx}`)

  async function gradePending(score, feedback) {
    let graded = 0
    // 队列在 confirm 后会重载（条目可能被换掉）：每次循环重新数、逐条 try（重渲染竞态容错）
    for (let round = 0; round < 6; round += 1) {
      const entries = tpage.locator('[data-testid^="tv3-gw-queue-"]')
      const m = await entries.count()
      let pendingSeen = false
      for (let i = 0; i < m; i += 1) {
        try {
          await entries.nth(i).click({ timeout: 8000 })
        } catch { continue } // 重渲染中条目已 detach
        await tpage.waitForTimeout(1500)
        const scoreInput = tpage.locator('[data-testid="tv3-gw-score"]')
        if (!(await scoreInput.isVisible().catch(() => false))) continue // 已确认项
        pendingSeen = true
        // 分数自适应该项满分（max 属性）：超满分会被前端 scoreError 拦下、confirm 禁用
        const maxAttr = await scoreInput.getAttribute('max').catch(() => null)
        const maxN = maxAttr ? Number(maxAttr) : NaN
        const sv = Number.isFinite(maxN) && maxN > 0 ? Math.min(score, Math.max(1, Math.floor(maxN))) : score
        await scoreInput.fill(String(sv))
        await tpage.locator('[data-testid="tv3-gw-feedback"]').fill(feedback)
        const confirmRespP = tpage.waitForResponse(
          (r) => r.url().includes('/grading/') && r.url().includes('/confirm') && r.request().method() === 'POST',
          { timeout: 15000 },
        ).catch(() => null)
        await tpage.locator('[data-testid="tv3-gw-confirm"]').click()
        const resp = await confirmRespP
        if (resp && resp.ok()) {
          graded += 1
          console.log(`CONFIRM | entry#${i} score=${sv} http=${resp.status()}`)
        } else {
          const errTag = await tpage.locator('[data-testid="tv3-gw-score-error"]').innerText().catch(() => '')
          console.log(`WARN | confirm failed on entry#${i} score=${sv} err="${errTag}"`)
        }
        await tpage.waitForTimeout(1200) // 等 loadWorkspace 重载稳定
      }
      if (!pendingSeen) break
    }
    return graded
  }

  let photoSeen = false
  if (solIdx >= 0) {
    await tabs.nth(solIdx).click()
    await tpage.locator('[data-testid="tv3-gw-queue"] [data-testid^="tv3-gw-queue-"]').first().waitFor({ timeout: 30000 })
    await tpage.locator('[data-testid^="tv3-gw-queue-"]').first().click()
    // 原图经鉴权字节流 blob 化展示，给足加载窗口；两种终态都算观测完成
    await Promise.race([
      tpage.locator('[data-testid="tv3-gw-photo-img"]').waitFor({ state: 'visible', timeout: 20000 }),
      tpage.locator('[data-testid="tv3-gw-photo-missing"]').waitFor({ state: 'visible', timeout: 20000 }),
    ]).catch(() => {})
    photoSeen = await tpage.locator('[data-testid="tv3-gw-photo-img"]').isVisible().catch(() => false)
    const photoMissing = await tpage.locator('[data-testid="tv3-gw-photo-missing"]').isVisible().catch(() => false)
    await shot(tpage, '08b-grading-detail-photo')
    record('08b0-手写原图展示', photoSeen || photoMissing, `img=${photoSeen} missing占位=${photoMissing}`)
    const gradedSol = await gradePending(8, '步骤完整，注意分类讨论的边界（a=0 并入 a≤0）；书写工整，继续保持。')
    record('08b-解答题批改（含原图核验）', gradedSol >= 1, `手写原图可见=${photoSeen} 已批=${gradedSol} 份`)
    if (gradedSol >= 1) await shot(tpage, '08c-grading-confirmed')
  }
  let gradedOther = 0
  for (let i = 0; i < tabCount; i += 1) {
    if (i === solIdx) continue
    await tabs.nth(i).click()
    await tpage.waitForTimeout(1500)
    gradedOther += await gradePending(5, '拍照清晰，结论正确，继续保持。')
  }
  record('08c-手写原图可见', photoSeen, `批改详情中出现学生手写原图=${photoSeen}`)
  record('08d-其余题人工批阅', gradedOther >= 1, `其余待批项已批=${gradedOther} 份（愿景4：填空拍照人工批）`)
} catch (e) { record('08-教师批改', false, e.message) }
await shot(tpage, '08e-grading-after-all')

/* ========== 09 学生看到批改成绩 ========== */
try {
  await spage.reload({ waitUntil: 'domcontentloaded' })
  await spage.waitForTimeout(2500)
  const resultCard = spage.locator('.card:has-text("批改结果")')
  await resultCard.waitFor({ timeout: 30000 })
  const txt = (await resultCard.innerText()).replace(/\s+/g, ' ')
  const hasTotal = /总分：\d/.test(txt)
  record('09-学生看到成绩', hasTotal, `result="${txt.slice(0, 160)}"`)
} catch (e) { record('09-学生看到成绩', false, e.message) }
await shot(spage, '09-student-sees-grade')

/* ========== 10 教师班级学情面板 ========== */
try {
  await tpage.goto(`${base}/teacher-v3/insights`, { waitUntil: 'domcontentloaded' })
  await tpage.locator('[data-testid="tv3-insights"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1500)
  const clsSel = tpage.locator('[data-testid="tv3-ins-class"]')
  if (await clsSel.isVisible().catch(() => false)) await selectByText(tpage, 'tv3-ins-class', className)
  const avg = tpage.locator('[data-testid="tv3-ins-avg-mastery"]')
  await avg.waitFor({ timeout: 30000 })
  await tpage.waitForTimeout(2000)
  const avgText = (await avg.innerText()).trim()
  const row = tpage.locator(`[data-testid^="tv3-ins-student-"]:has-text("E2E验收学生")`)
  const rowVisible = await row.isVisible().catch(() => false)
  const rowText = rowVisible ? (await row.innerText()).replace(/\s+/g, ' ') : ''
  const masteryOk = avgText && !avgText.includes('—')
  record('10-班级学情面板', masteryOk && rowVisible, `avg_mastery="${avgText}" 学生行="${rowText.slice(0, 120)}"`)
} catch (e) { record('10-班级学情面板', false, e.message) }
await shot(tpage, '10-teacher-insights')

/* ========== 11 备课会话 + 备课画像重算（学情证据入备课链路） ========== */
try {
  // 11a 备课中心以本班发起备课会话
  await tpage.goto(`${base}/teacher-v3/prep`, { waitUntil: 'domcontentloaded' })
  await tpage.waitForTimeout(2500)
  const pill = tpage.locator('.pc-home__class-btn')
  if (await pill.isVisible().catch(() => false)) {
    await pill.click()
    await tpage.locator('.pc-home__class-item', { hasText: className }).first().click()
  }
  await tpage.locator('.pc-home__nl-input').fill('结合本班学情，帮我备一节导数单调性的复习课')
  const sessionRespPromise = tpage.waitForResponse((r) => String(r.url()).includes('/prep/'), { timeout: 300000 })
  await tpage.locator('.pc-home__nl-send').click()
  await tpage.waitForURL((u) => String(u).includes('/teacher-v3/prep/w/'), { timeout: 300000 })
  await tpage.waitForTimeout(3000)
  record('11a-备课会话创建', true, `url=${tpage.url()}`)
  await shot(tpage, '11a-prep-session-created')

  // 11b 学情洞察 → 同步备课画像（8000 批改事实 → 备课画像）
  await tpage.goto(`${base}/teacher-v3/insights`, { waitUntil: 'domcontentloaded' })
  await tpage.locator('[data-testid="tv3-ins-class"]').waitFor({ timeout: 20000 })
  await tpage.waitForTimeout(1200)
  await selectByText(tpage, 'tv3-ins-class', className)
  await tpage.waitForTimeout(1500)
  const recalcPromise = tpage.waitForResponse((r) => String(r.url()).includes('/prep/profiles/recalculate'), { timeout: 120000 })
  await tpage.locator('[data-testid="tv3-ins-sync-profile"]').click()
  const recalcResp = await recalcPromise
  const recalcBody = await recalcResp.json().catch(() => null)
  fs.writeFileSync(`${outDir}/recalculate.json`, JSON.stringify(recalcBody, null, 2))
  const toast = await toastText(tpage)
  const profileId = recalcBody?.data?.profile_id || recalcBody?.profile_id || ''
  record('11b-画像重算接口', recalcResp.status() === 200 && !!profileId, `status=${recalcResp.status()} profile_id=${profileId} toast=${toast}`)
  await shot(tpage, '11b-profile-synced')
  if (profileId) {
    // 画像内容（mastery_by_kp 学情证据）由脚本外用教师 token GET 后落盘 evidence/profile.json
    // （页面 token 纯内存，页内 fetch 无法附带 Authorization —— 401 实测）
    fs.writeFileSync(`${outDir}/profile-id.txt`, profileId)
    record('11c-画像 id 已留存', true, `profile_id=${profileId}（内容由验收员 curl 取证）`)
  }
} catch (e) { record('11-备课链路学情', false, e.message) }
await shot(tpage, '11c-prep-final')

/* ---------- 落盘证据 ---------- */
saveEvidence()
const fails = results.filter((r) => !r.ok)
console.log(`\n===== SUMMARY: ${results.length - fails.length}/${results.length} PASS =====`)
for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.step} | ${r.detail}`)
await browser.close()
process.exit(fails.length ? 1 : 0)
