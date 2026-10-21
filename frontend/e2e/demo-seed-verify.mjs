import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.BASE || 'http://127.0.0.1:5176'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'demo-seed-verify')
fs.mkdirSync(outDir, { recursive: true })
const password = 'MathArenaDemo2026!'
const results = []
const errors = []
function rec(step, ok, detail) { results.push({ step, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`) }

async function login(page, phone, role) {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.locator('#preferred-role').selectOption(role)
  await page.getByRole('tab', { name: '密码登录' }).click()
  await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
  await page.locator('#auth-password').fill(password)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
  await page.waitForTimeout(1500)
}

async function shot(page, name, full = false) {
  await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage: full })
}

const browser = await chromium.launch({ headless: true })
const studentCtx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
const student = await studentCtx.newPage()
student.setDefaultTimeout(45000)
student.on('console', (m) => { if (m.type() === 'error') errors.push(`student: ${m.text().slice(0, 200)}`) })
student.on('response', (r) => { if (/\/api\//.test(r.url()) && r.status() >= 500) errors.push(`student ${r.status()} ${r.url()}`) })

try {
  await login(student, '13800001001', 'student')
  await student.waitForSelector('.lp', { timeout: 30000 })
  await student.locator('.lp', { hasText: 'FSRS' }).waitFor({ timeout: 30000 }).catch(() => {})
  await student.waitForTimeout(800)
  await shot(student, '01-student-home')
  const home = await student.locator('.lp').innerText()
  rec('student-home-learning', /BKT/.test(home) && /FSRS/.test(home) && /薄弱|掌握/.test(home), '学情脉搏可见')

  // 导数概念对话：用户提问 → 解释 → 追问 → 回答，不能是一问一答的死胡同
  await student.locator('.cs-item', { hasText: '导数的几何意义怎么理解' }).first().click()
  await student.waitForTimeout(1500)
  const derivativeText = await student.locator('body').innerText()
  await shot(student, '02-derivative-followup')
  rec(
    'student-derivative-followup',
    /导数的几何意义怎么理解/.test(derivativeText) && /第一充分条件/.test(derivativeText) && /为什么/.test(derivativeText),
    '导数对话包含追问与完整回答',
  )

  // 引导式解题会话
  await student.locator('.cs-item', { hasText: '引导式解题 · 三次函数极值' }).first().click().catch(async () => {
    await student.getByText('引导式解题 · 三次函数极值', { exact: false }).first().click()
  })
  await student.waitForSelector('.soc-choices, .soc-complete', { timeout: 20000 })
  await student.waitForTimeout(1200)
  await shot(student, '02-socratic-guide')
  const choices = await student.locator('.soc-choice').count()
  const complete = await student.locator('.soc-complete').count()
  rec('student-socratic', choices >= 1 && complete >= 1, `引导选项=${choices}, 完成卡=${complete}`)

  // 举一反三变式链
  await student.getByText('举一反三 · 三次函数变式链', { exact: false }).first().click()
  await student.waitForSelector('.qc-variant', { timeout: 20000 })
  await student.waitForTimeout(800)
  await shot(student, '03-variant-chain')
  const variants = await student.locator('.qc-variant').count()
  const options = await student.locator('.qc-opt').count()
  rec('student-variant-chain', variants >= 1 && options >= 2, `变式卡=${variants}, 选项=${options}`)
  // 变式作答真实判分：点选 C（正确答案）→ 判分横幅
  await student.locator('.qc-opt').nth(2).click()
  await student.locator('.qc-judge').first().waitFor({ timeout: 15000 })
  const judgeText = await student.locator('.qc-judge').first().innerText()
  await student.waitForTimeout(500)
  await shot(student, '03b-variant-judged')
  rec('student-variant-judge', /回答正确/.test(judgeText), judgeText.replace(/\s+/g, ' ').slice(0, 60))

  // 可视化讲解
  await student.getByText('可视化讲解 · 导数与极值', { exact: false }).first().click()
  await student.waitForSelector('.math-figure img', { timeout: 20000 })
  await student.waitForTimeout(900)
  await shot(student, '04-visual-figure')
  const frames = await student.locator('.math-figure img').count()
  const demoBtn = await student.locator('.math-figure .mf-replay', { hasText: /演示|重播/ }).count()
  rec('student-visual-figure', frames >= 1, `图形帧=${frames}, 演示按钮=${demoBtn}`)
  // 动态演示：跳到第 2 帧，关键点标注真实出现
  const dots = await student.locator('.mf-dot').count()
  if (dots >= 2) {
    await student.locator('.mf-dot').nth(1).click()
    await student.locator('.math-figure img[alt="标注关键点"]').waitFor({ timeout: 10000 })
    await student.waitForTimeout(600)
    await shot(student, '04b-figure-annotated')
  }
  rec('student-figure-frame', dots >= 2, `可切换帧=${dots}`)

  // 错题本 / 知识库 / 课堂 / 学情报告
  await student.goto(`${base}/errors`, { waitUntil: 'networkidle' })
  await student.waitForTimeout(1400)
  await shot(student, '05-errors')
  const errText = await student.locator('body').innerText()
  rec('student-errors', /错题|复习/.test(errText), '错题本有记录')

  await student.goto(`${base}/library`, { waitUntil: 'networkidle' })
  await student.waitForSelector('.doc-row', { timeout: 20000 })
  await student.waitForTimeout(800)
  await shot(student, '06-library')
  const docRows = await student.locator('.doc-row').count()
  rec('student-library', docRows >= 4, `知识库资料=${docRows}`)

  await student.goto(`${base}/classroom`, { waitUntil: 'networkidle' })
  await student.waitForTimeout(1800)
  await shot(student, '07-classroom')
  const classText = await student.locator('body').innerText()
  rec('student-classroom', /函数的单调性|课堂|作业/.test(classText), '课堂/作业可见')

  // 双师课堂：打开老师课件，必须走教师端同一渲染器且有真实正文
  await student.goto(`${base}/classroom?tab=dual`, { waitUntil: 'networkidle' })
  const cwCard = student.locator('.tc-card', { hasText: '函数的单调性' }).first()
  await cwCard.waitFor({ timeout: 20000 })
  await cwCard.click()
  await student.waitForURL(/\/dual\//, { timeout: 30000 })
  await student.waitForSelector('.pc-slide', { timeout: 30000 })
  await student.waitForTimeout(1200)
  const slideText = await student.locator('.pc-slide').innerText()
  const pcElCount = await student.locator('.pc-el').count()
  const narration = await student.locator('.dcx-narr-lines').innerText()
  const narrationSentences = await student.locator('.dcx-narr-lines p').count()
  await shot(student, '07b-courseware-slide')
  rec('student-courseware-slide', pcElCount > 0 && /单调递增|求导/.test(slideText), `正文元素=${pcElCount}, 文本=${slideText.replace(/\s+/g, ' ').slice(0, 40)}`)
  rec(
    'student-courseware-narration',
    narrationSentences >= 3 && narration.length > 80 && !/让学生|请学生|同桌互查/.test(narration),
    `讲稿=${narrationSentences}句/${narration.length}字`,
  )

  await student.goto(`${base}/me?tab=report`, { waitUntil: 'networkidle' })
  await student.waitForTimeout(1800)
  await shot(student, '08-report')
  rec('student-report', /学情|掌握|趋势/.test(await student.locator('body').innerText()), '学情报告可见')
  await studentCtx.close()

  // Teacher
  const tCtx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
  const teacher = await tCtx.newPage(); teacher.setDefaultTimeout(45000)
  teacher.on('console', (m) => { if (m.type() === 'error') errors.push(`teacher: ${m.text().slice(0, 200)}`) })
  await login(teacher, '13800001002', 'teacher')
  await teacher.goto(`${base}/teacher-v3/classes`, { waitUntil: 'networkidle' })
  await teacher.waitForTimeout(1500)
  await shot(teacher, '09-teacher-classes')
  rec('teacher-class', /高一\\(3\\)班|高一/.test(await teacher.locator('body').innerText()), '班级可见')

  await teacher.goto(`${base}/teacher-v3/assign`, { waitUntil: 'networkidle' })
  await teacher.waitForTimeout(1800)
  await shot(teacher, '10-teacher-grading')
  const assignText = await teacher.locator('body').innerText()
  rec('teacher-grading', /导数与函数单调性|批改/.test(assignText), '批改工作台有作业')

  await teacher.goto(`${base}/teacher-v3/classroom`, { waitUntil: 'networkidle' })
  await teacher.waitForTimeout(1800)
  await shot(teacher, '11-teacher-courseware')
  rec('teacher-courseware', /函数的单调性/.test(await teacher.locator('body').innerText()), '双师课件可见')
  await tCtx.close()

  // Research
  const rCtx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
  const research = await rCtx.newPage(); research.setDefaultTimeout(45000)
  research.on('console', (m) => { if (m.type() === 'error') errors.push(`research: ${m.text().slice(0, 200)}`) })
  await login(research, '13800001003', 'researcher')
  await research.waitForSelector('.rl-page', { timeout: 30000 })
  await research.waitForTimeout(1200)
  await shot(research, '12-research-landing', true)
  const projectCards = await research.locator('.rl-project').count()
  rec('research-landing-projects', projectCards >= 3, `科研项目=${projectCards}`)
  const firstRead = research.locator('.rl-project-actions button', { hasText: '阅读' }).first()
  await firstRead.click()
  await research.waitForSelector('.rw-shell', { timeout: 30000 })
  await research.waitForFunction(
    () => (document.querySelector('.rw-pdf__canvas')?.width || 0) > 400,
    null,
    { timeout: 30000 },
  ).catch(() => {})
  await research.waitForTimeout(1500)
  await shot(research, '13-research-workspace')
  const rText = await research.locator('body').innerText()
  const canvasW = await research.locator('.rw-pdf__canvas').evaluate((c) => c.width).catch(() => 0)
  rec('research-workspace', /Human-in-the-Loop|Abstract/.test(rText) && canvasW > 400, `PDF画布=${canvasW}px`)
  rec('research-agent-thread', /这篇文章的核心方法是什么/.test(rText) && /证据锚点|证据充分/.test(rText), '科研 Agent 对话/结果可见')
  await rCtx.close()

  rec('no-console-errors', errors.length === 0, errors.slice(0, 4).join(' | ') || '无')
} catch (e) {
  rec('script', false, String(e).slice(0, 600))
  try { await shot(student, '99-failure') } catch { /* ignore */ }
} finally {
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ results, errors }, null, 2), 'utf8')
  const failed = results.filter((r) => !r.ok).length
  console.log(`\n${failed ? 'FAIL' : 'PASS'} ${results.length - failed}/${results.length}; screenshots -> ${outDir}`)
  await browser.close()
  process.exit(failed ? 1 : 0)
}
