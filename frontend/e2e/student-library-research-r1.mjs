/**
 * r1 验收：学生端首页学情脉搏 + 知识库重构 + 科研端启动页/个人中心。
 * 真实浏览器 → 真实后端（5176 / 8000 / 8300 经网关）。
 *
 * usage: node e2e/student-library-research-r1.mjs
 * 截图输出：artifacts/student-library-research-r1/（可用 OUT 覆盖）
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

const base = process.env.E2E_BASE_URL || 'http://127.0.0.1:5176'
const outDir = process.env.OUT || path.resolve(__dirname, '..', '..', 'artifacts', 'student-library-research-r1')
fs.mkdirSync(outDir, { recursive: true })

const results = []
const apiErrors = []
const consoleErrors = []
function record(step, ok, detail) {
  results.push({ step, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'} | ${step} | ${detail}`)
}
function attach(page, tag) {
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(`[${tag}] ${msg.text().slice(0, 240)}`)
  })
  page.on('response', (res) => {
    const url = res.url()
    if (!/\/api\//.test(url)) return
    if (res.status() >= 400 && res.status() !== 401) apiErrors.push(`${res.status()} ${url}`)
  })
}

async function login(page, phone, role = 'student') {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  await page.locator('#preferred-role').selectOption(role)
  await page.locator('input.input[placeholder="请输入中国大陆手机号"]').fill(phone)
  await page.getByRole('button', { name: '获取验证码' }).click()
  const demoNote = page.locator('.demo-note')
  await demoNote.waitFor({ state: 'visible', timeout: 20000 })
  const demoCode = ((await demoNote.innerText()).match(/\d{6}/) || [])[0]
  if (!demoCode) throw new Error('演示验证码未取到')
  await page.locator('#auth-otp').fill(demoCode)
  await page.getByRole('button', { name: /安全登录/ }).click()
  await page.waitForURL((u) => !String(u).includes('/login'), { timeout: 30000 })
  await page.waitForTimeout(1500)
  return page.url()
}

async function shot(page, name, fullPage = false) {
  await page.screenshot({ path: path.join(outDir, `${name}.png`), fullPage })
}

const browser = await chromium.launch({ headless: true })
const studentCtx = await browser.newContext({ viewport: { width: 1500, height: 980 }, locale: 'zh-CN' })
const student = await studentCtx.newPage()
student.setDefaultTimeout(45000)
attach(student, 'student')

try {
  const landed = await login(student, process.env.R1_STUDENT_PHONE || '13900001003', 'student')
  record('student-login', /\/dialog/.test(landed), `登录落点 ${landed}`)

  await student.waitForSelector('.lp', { timeout: 30000 })
  await student.waitForTimeout(2500)
  await shot(student, '01-student-home')

  const pulseText = await student.locator('.lp').innerText()
  const hasBkt = /BKT/.test(pulseText)
  const hasFsrs = /FSRS/.test(pulseText)
  const weakRows = await student.locator('.lp-weak').count()
  const dueRows = await student.locator('.lp-due').count()
  const recChips = await student.locator('.sugg-chip--rec').count()
  record('home-learning-pulse', hasBkt && hasFsrs && weakRows >= 1,
    `BKT=${hasBkt} FSRS=${hasFsrs} 薄弱行=${weakRows} 到期行=${dueRows} 学情推荐chip=${recChips}`)
  const garbled = /\$|\|\s*:?-{2,}/.test(pulseText)
  record('home-no-garbled-preview', !garbled, garbled ? '学情卡仍出现 $ 或表格分隔符' : '学情卡无 LaTeX/表格语法残片')

  // 学情推荐 chip 的“依据”可见（FSRS/BKT/正确率）
  const chipText = recChips ? await student.locator('.sugg-chip--rec').first().innerText() : ''
  record('recommendation-evidence', recChips >= 2 && /(FSRS|BKT|正确率)/.test(chipText),
    `首个推荐 chip：「${chipText.replace(/\s+/g, ' ').slice(0, 80)}」`)

  // 知识库
  await student.goto(`${base}/library`, { waitUntil: 'networkidle' })
  await student.waitForSelector('.doc-row', { timeout: 30000 })
  await student.waitForTimeout(1200)
  await shot(student, '02-library-materials')
  const docRows = await student.locator('.doc-row').count()
  const groupCount = await student.locator('.doc-group').count()
  record('library-materials-groups', docRows >= 1 && groupCount >= 1, `资料 ${docRows} 行 / 分类组 ${groupCount}`)

  // 上传真实文件（UI 文件选择 → 真实 /v1/kb/upload）
  const uploadFile = path.resolve(__dirname, '..', '..', '.tmp', 'kb-ui-test.md')
  fs.writeFileSync(uploadFile, '# 三角函数与恒等变换\n\n$\\sin^2 x+\\cos^2 x=1$。\n\n## 例题\n\n已知 $\\sin x=\\frac{3}{5}$，且 $x$ 为第一象限角，求 $\\cos x$。\n', 'utf8')
  await student.locator('.lib-upload input[type=file]').setInputFiles(uploadFile)
  let uploaded = false
  for (let i = 0; i < 20; i++) {
    await student.waitForTimeout(1500)
    const text = await student.locator('.library-page').innerText({ timeout: 2000 }).catch(() => '')
    if (/kb-ui-test\.md/.test(text)) {
      const rowText = await student.locator('.doc-row', { hasText: 'kb-ui-test.md' }).innerText({ timeout: 2000 }).catch(() => '')
      if (/可引用|解析中|排队中|向量化中|失败/.test(rowText)) { uploaded = true; break }
    }
  }
  await shot(student, '03-library-after-upload')
  record('kb-upload-ui', uploaded, 'UI 上传 kb-ui-test.md 后出现在资料列表（真实解析状态）')

  // 点开资料 → 切片抽屉
  await student.locator('.doc-row').first().click()
  await student.waitForSelector('.doc-panel', { timeout: 15000 })
  await student.waitForTimeout(900)
  const chunkCount = await student.locator('.dp-chunk').count()
  await shot(student, '04-library-doc-drawer')
  record('library-doc-clickable', chunkCount >= 1, `资料详情抽屉可打开，切片 ${chunkCount} 片`)

  // 资料 → 基于此提问（真实进入对话并发出用户消息）
  const canAsk = await student.getByRole('button', { name: /基于此资料提问/ }).isEnabled().catch(() => false)
  if (canAsk) {
    await student.getByRole('button', { name: /基于此资料提问/ }).click()
    await student.waitForURL((u) => String(u).includes('/dialog'), { timeout: 15000 })
    await student.waitForSelector('.msg-row.user', { timeout: 15000 })
    await shot(student, '05-library-ask-in-dialog')
    record('library-ask-dialog', true, '资料详情 → 对话，用户消息已发出')
  } else {
    record('library-ask-dialog', false, '资料未就绪，提问按钮不可用')
  }

  // 我的题库
  await student.goto(`${base}/library?tab=questions`, { waitUntil: 'networkidle' })
  await student.waitForSelector('.q-card', { timeout: 30000 })
  await student.waitForTimeout(800)
  await shot(student, '06-library-questions')
  const qCount = await student.locator('.q-card').count()
  const kpTags = await student.locator('.q-tag--kp').count()
  record('question-bank-list', qCount >= 1 && kpTags >= 1, `题目 ${qCount} 张 / 知识点标签 ${kpTags}`)

  await student.locator('.q-card').first().click()
  await student.waitForSelector('.q-panel', { timeout: 15000 })
  await student.waitForTimeout(700)
  const stem = await student.locator('.q-stem').innerText()
  await student.getByRole('button', { name: '显示答案与解析' }).click()
  await student.waitForTimeout(400)
  const hasAnswer = (await student.locator('.q-answer').count()) >= 1
  await shot(student, '07-question-drawer')
  record('question-detail', stem.length > 5 && hasAnswer, `题目详情可打开，答案区=${hasAnswer}`)
  await student.locator('.q-close').click()

  // 搜索筛选
  await student.locator('.lib-toolbar--q input[type=search]').fill('导数')
  await student.waitForTimeout(1200)
  const filtered = await student.locator('.q-card').count()
  record('question-search', filtered >= 1, `搜索「导数」返回 ${filtered} 张`)

  // 科研端：新账号 → 启动页 → 创建项目 → 个人中心
  const researchCtx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, locale: 'zh-CN' })
  const research = await researchCtx.newPage()
  research.setDefaultTimeout(45000)
  attach(research, 'research')
  const rLanded = await login(research, process.env.R1_RESEARCH_PHONE || '13900001006', 'researcher')
  record('research-login-landing', /\/research-workspace\/?$/.test(rLanded), `科研登录落点 ${rLanded}`)

  await research.waitForSelector('.rl-page', { timeout: 30000 })
  await research.waitForTimeout(1200)
  const pathCards = await research.locator('.rl-path').count()
  const hasProject = (await research.locator('.rl-project').count()) > 0
  await shot(research, hasProject ? '08-research-landing-existing' : '08-research-landing-empty', true)
  record('research-landing', pathCards === 3, `启动页能力入口 ${pathCards} 个；已有项目=${hasProject}`)

  if (hasProject) {
    await research.locator('.rl-path').first().click()
    await research.waitForURL((u) => /\/research-workspace\/[^/]+\/read/.test(String(u)), { timeout: 30000 })
    await research.waitForSelector('.rw-shell', { timeout: 30000 })
    await research.waitForTimeout(2500)
    await shot(research, '09-research-workspace-entry')
    record('research-enter-existing', true, `从「读论文」进入 ${research.url()}`)
  } else {
    await research.locator('.rl-paper-create').click()
    await research.waitForURL((u) => /\/research-workspace\/[^/]+\/read/.test(String(u)), { timeout: 30000 })
    await research.waitForSelector('.rw-shell', { timeout: 30000 })
    await research.waitForTimeout(2500)
    await shot(research, '09-research-workspace-entry')
    record('research-create-and-enter', true, `新建项目后进入 ${research.url()}`)
  }

  await research.goto(`${base}/research-workspace`, { waitUntil: 'networkidle' })
  await research.waitForSelector('.rl-project', { timeout: 30000 })
  await research.waitForTimeout(900)
  await shot(research, '10-research-landing-with-project', true)
  const projectCards = await research.locator('.rl-project').count()
  record('research-recent-project', projectCards >= 1, `启动页最近项目 ${projectCards} 张`)

  await research.getByRole('button', { name: '个人中心' }).first().click()
  await research.waitForSelector('.ra-profile', { timeout: 20000 })
  await research.waitForTimeout(800)
  const profileText = await research.locator('.ra-profile').innerText()
  await shot(research, '11-research-account-profile', true)
  record('research-account-profile', /科研/.test(profileText), '个人中心资料卡可打开')
  await research.locator('.ra-tabs button', { hasText: '账号安全' }).click()
  await research.waitForTimeout(1200)
  await shot(research, '12-research-account-security', true)
  const securityText = await research.locator('.ra-page').innerText()
  record('research-account-security', /修改密码/.test(securityText) && /登录设备/.test(securityText),
    '账号安全含密码与登录设备')
  await research.locator('.ra-tabs button', { hasText: '科研概览' }).click()
  await research.waitForTimeout(700)
  await shot(research, '13-research-account-overview', true)
  record('research-account-overview', /项目/.test(await research.locator('.ra-overview').innerText()), '科研概览可打开')

  record('no-api-5xx', apiErrors.filter((x) => /^5\d\d/.test(x)).length === 0, `API 4xx/5xx: ${apiErrors.length ? apiErrors.slice(0, 6).join(' | ') : '无'}`)
  record('no-console-errors', consoleErrors.length === 0, consoleErrors.length ? consoleErrors.slice(0, 4).join(' | ') : '无 console.error')
} catch (e) {
  record('script', false, e?.stack || String(e))
  try { await shot(student, '99-student-failure') } catch { /* ignore */ }
} finally {
  fs.writeFileSync(path.join(outDir, 'results.json'), JSON.stringify({ results, apiErrors, consoleErrors }, null, 2), 'utf8')
  const failed = results.filter((r) => !r.ok).length
  console.log(`\n${failed ? 'FAIL' : 'PASS'} ${results.length - failed}/${results.length}  steps; screenshots -> ${outDir}`)
  await browser.close()
  process.exit(failed ? 1 : 0)
}
