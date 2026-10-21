import { expect, test } from '@playwright/test'

async function presetRole(page, role) {
  await page.addInitScript((activeRole) => {
    localStorage.setItem('ma_user', JSON.stringify({ active_role: activeRole }))
  }, role)
}

async function completeMockSmsLogin(page, role = 'student') {
  await page.goto('/login')
  await page.getByLabel('进入身份').selectOption(role)
  await page.getByLabel('手机号').fill('13800000000')
  await page.getByRole('button', { name: '获取验证码' }).click()
  await expect(page.getByText('演示环境验证码：123456')).toBeVisible()
  await page.getByLabel('短信验证码').fill('123456')
  await page.getByRole('button', { name: '安全登录' }).click()
}

async function completeMockRegistration(page, { role = 'student', organization = '' } = {}) {
  await page.goto('/register')
  if (role !== 'student') await page.getByLabel('注册身份').selectOption(role)
  await page.getByLabel('手机号').fill('13800000000')
  await page.getByRole('button', { name: '获取验证码' }).click()
  await expect(page.getByText('演示环境验证码：123456')).toBeVisible()
  await page.getByLabel('短信验证码').fill('123456')
  if (organization) await page.getByLabel('学校或机构').fill(organization)
  await page.getByText('我已阅读并同意').click()
  await page.getByRole('button', { name: '注册并继续' }).click()
}

test.describe('unified authentication journeys', () => {
  test('student SMS registration completes onboarding without storing access tokens', async ({ page }) => {
    await completeMockRegistration(page)
    await expect(page).toHaveURL(/\/onboarding\/student/)
    await page.getByLabel('姓名或昵称').fill('小数同学')
    await page.getByLabel('年级').fill('高二')
    await page.getByRole('button', { name: '进入学习空间' }).click()
    await expect(page).toHaveURL(/\/overview/)

    const stored = await page.evaluate(() => ({
      local: localStorage.getItem('ma_token'),
      session: sessionStorage.getItem('ma_token'),
    }))
    expect(stored).toEqual({ local: null, session: null })
  })

  test('authenticated student submits a teacher application and sees pending review', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('ma_user', JSON.stringify({ active_role: 'student' }))
    })
    await page.goto('/identity/apply')
    await page.getByLabel('学校或机构').fill('示例中学')
    await page.getByLabel('任教学段').fill('高中')
    await page.getByRole('button', { name: '提交审核' }).click()
    await expect(page).toHaveURL(/\/identity\/pending/)
    await expect(page.getByText('审核中')).toBeVisible()
  })

  test('login entry offers workspace selection for the three public roles', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByRole('tab', { name: '短信验证码' })).toBeVisible()
    await expect(page.getByRole('tab', { name: '密码登录' })).toBeVisible()
    const selector = page.locator('select[name="preferred_role"]')
    await expect(selector).toBeVisible()
    await expect(selector.locator('option')).toHaveText(['学生端', '教师端', '科研端'])
    await expect(page.locator('select[name="role"]')).toHaveCount(0)
  })

  test('approved teacher login opens the teacher workspace, not overview', async ({ page }) => {
    await completeMockSmsLogin(page, 'teacher')
    await expect(page).toHaveURL(/\/teacher\/today$/)
  })

  test('approved researcher password login opens the research workspace', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('tab', { name: '密码登录' }).click()
    await page.getByLabel('进入身份').selectOption('researcher')
    await page.getByLabel('手机号').fill('13800000000')
    await page.getByLabel('密码', { exact: true }).fill('correct horse battery staple')
    await page.getByRole('button', { name: '安全登录' }).click()
    await expect(page).toHaveURL(/\/research$/)
  })

  test('pending teacher registration opens review status, not overview', async ({ page }) => {
    await completeMockRegistration(page, { role: 'teacher', organization: '示例中学' })
    await expect(page).toHaveURL(/\/identity\/pending$/)
    await expect(page.getByText('审核中')).toBeVisible()
  })

  test('admin reauthenticates, approves a teacher, and the teacher can relogin', async ({ page, browser }) => {
    await presetRole(page, 'admin')
    await page.goto('/admin/identity/applications')
    await page.getByLabel('管理员手机号').fill('13800000000')
    await page.getByLabel('管理员密码').fill('Valid admin passphrase 2026')
    await page.getByRole('button', { name: '发送验证码' }).click()
    await page.getByLabel('二次验证码').fill('123456')
    await page.getByRole('button', { name: '完成认证' }).click()
    await page.getByLabel('申请状态').selectOption('')
    const teacher = page.locator('article').filter({ hasText: '教师' })
    await teacher.getByRole('button', { name: '批准' }).click()
    await expect(teacher.getByText('已通过')).toBeVisible()

    const teacherContext = await browser.newContext()
    const teacherPage = await teacherContext.newPage()
    await presetRole(teacherPage, 'teacher')
    await teacherPage.goto('/')
    await expect(teacherPage).toHaveURL(/\/teacher\/today/)
    await expect(teacherPage.getByText('导数与函数单调性')).toBeVisible()
    await teacherContext.close()
  })

  test('admin can approve a researcher application', async ({ page }) => {
    await presetRole(page, 'admin')
    await page.goto('/admin/identity/applications')
    await page.getByLabel('管理员手机号').fill('13800000000')
    await page.getByLabel('管理员密码').fill('Valid admin passphrase 2026')
    await page.getByRole('button', { name: '发送验证码' }).click()
    await page.getByLabel('二次验证码').fill('123456')
    await page.getByRole('button', { name: '完成认证' }).click()
    await page.getByLabel('申请状态').selectOption('')
    const researcher = page.locator('article').filter({ hasText: '科研人员' })
    await researcher.getByRole('button', { name: '批准' }).click()
    await expect(researcher.getByText('已通过')).toBeVisible()
  })

  for (const state of ['rejected', 'suspended']) {
    test(`${state} teacher cannot enter the teacher workspace`, async ({ page, context }) => {
      await context.addCookies([{ name: 'ma_mock_state', value: state, domain: 'localhost', path: '/' }])
      await presetRole(page, 'teacher')
      await page.goto('/teacher/today')
      await expect(page).toHaveURL(/\/identity\/pending/)
    })
  }

  test('approved dual-role student switches to the teacher workspace', async ({ page, context }) => {
    await context.addCookies([{ name: 'ma_mock_dual', value: '1', domain: 'localhost', path: '/' }])
    await presetRole(page, 'student')
    await page.goto('/overview')
    await page.getByRole('button', { name: '教师端' }).click()
    await expect(page).toHaveURL(/\/teacher\/today/)
  })

  test('password reset uses its own SMS challenge', async ({ page }) => {
    await page.goto('/account/password')
    await page.getByLabel('手机号').fill('13800000000')
    await page.getByRole('button', { name: '发送验证码' }).click()
    await page.getByLabel('短信验证码').fill('123456')
    await page.getByLabel('新密码').fill('correct horse battery staple')
    await page.getByRole('button', { name: '确认重置' }).click()
    await expect(page.getByText('密码已重置，请返回登录')).toBeVisible()
  })

  test('account security revokes another session', async ({ page }) => {
    await presetRole(page, 'student')
    await page.goto('/account/security')
    const otherDevice = page.locator('article').filter({ hasText: 'Firefox' })
    await otherDevice.getByRole('button', { name: '撤销' }).click()
    await expect(otherDevice.getByText('已撤销')).toBeVisible()
  })

  test('phone change verifies both numbers and signs out sessions', async ({ page }) => {
    await presetRole(page, 'student')
    await page.goto('/account/security')
    await page.getByRole('button', { name: '开始换绑' }).click()
    await page.getByLabel('当前手机号', { exact: true }).fill('13800000000')
    await page.getByLabel('新手机号', { exact: true }).fill('13900000000')
    await page.locator('[data-action="send-old-phone-code"]').click()
    await page.locator('[data-action="send-new-phone-code"]').click()
    await page.getByLabel('当前手机号验证码').fill('123456')
    await page.getByLabel('新手机号验证码').fill('123456')
    await page.getByRole('button', { name: '确认换绑' }).click()
    await expect(page.getByText('手机号已更换，请重新登录')).toBeVisible()
  })

  test('deletion cancellation remains reachable without an active session', async ({ page }) => {
    await page.goto('/account/deletion/cancel')
    await page.getByLabel('手机号').fill('13800000000')
    await page.getByRole('button', { name: '发送验证码' }).click()
    await page.getByLabel('短信验证码').fill('123456')
    await page.getByRole('button', { name: '取消注销' }).click()
    await expect(page.getByText('注销申请已取消，现在可以重新登录')).toBeVisible()
  })
})
