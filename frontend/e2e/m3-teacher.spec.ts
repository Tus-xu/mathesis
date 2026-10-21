import { test, expect } from '@playwright/test'

const TEACHER_USER = { nickname: '王老师', roles: [{ role: 'teacher' }], active_role: 'teacher', grade: '' }
const STUDENT_USER = { nickname: '小婷', roles: [{ role: 'student' }], active_role: 'student', grade: '高二（3）班' }

async function presetMock(page: import('@playwright/test').Page, user: typeof TEACHER_USER) {
  await page.addInitScript((mockUser) => {
    localStorage.setItem('ma_token', 'mock-token-teacher-preview')
    localStorage.setItem('ma_user', JSON.stringify(mockUser))
  }, user)
}

test.describe('M3 teacher frontend journeys (mock)', () => {
  test('teacher root routes to the teacher workspace', async ({ page }) => {
    await presetMock(page, TEACHER_USER)
    await page.goto('/')

    await expect(page).toHaveURL(/\/teacher\/today/)
    await expect(page.getByRole('heading', { name: /李老师/ })).toBeVisible()
    await expect(page.getByRole('navigation', { name: '教师工作台导航' })).toBeVisible()
  })

  test('strict-source quiz fills the requested count, is reviewed by teacher, then publishes for students', async ({ page }) => {
    await presetMock(page, TEACHER_USER)
    await page.goto('/teacher/assign')
    await expect(page.getByText('组卷草稿')).toBeVisible()

    await page.getByLabel('目标班级').selectOption('c1')
    await page.getByLabel('知识点代码').fill('函数单调性')

    const genResponse = page.waitForResponse((response) =>
      response.url().includes('/api/teacher/quizzes/generate') && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '从严格题源生成草稿' }).click()
    const generated = await (await genResponse).json()
    const items = generated.data.content.items as any[]
    expect(items.length).toBe(8)
    expect(generated.data.degraded).toBe(false)
    for (const item of items) {
      expect(item.question_text).toBeTruthy()
      expect(item.answer).toBeTruthy()
      expect(['choice', 'blank', 'text']).toContain(item.q_type)
    }

    await expect(page.getByRole('button', { name: '确认题集草稿' })).toBeEnabled()
    await page.getByRole('button', { name: '确认题集草稿' }).click()
    await expect(page.getByRole('button', { name: '创建作业草稿' })).toBeVisible()
    await page.getByRole('button', { name: '创建作业草稿' }).click()

    const publishResponse = page.waitForResponse((response) =>
      /\/api\/teacher\/assignments\/[^/]+\/publish$/.test(new URL(response.url()).pathname)
        && response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '发布给学生' }).click()
    await page.getByRole('button', { name: '确认发布给学生' }).click()
    const published = await (await publishResponse).json()
    expect(published.data.status).toBe('published')
    await expect(page.getByRole('heading', { name: '我的作业' })).toBeVisible()
  })

  test('grading: question-focused workspace requires an explicit teacher decision', async ({ page }) => {
    await presetMock(page, TEACHER_USER)
    await page.goto('/teacher/grading?submission_item_id=si-2')
    await expect(page.getByRole('heading', { name: '函数的单调性', exact: true })).toBeVisible()
    await expect(page.getByText('正确求导')).toBeVisible()
    await page.getByRole('button', { name: '教师明确给分' }).click()
    await page.getByLabel('最终得分').fill('3')
    await page.getByRole('button', { name: '确认并下一份', exact: true }).click()
    await expect(page.getByText('教师确认已写入')).toBeVisible()

  })

  test('classroom session: start → launch question → live distribution → close (GP-13)', async ({ page }) => {
    await presetMock(page, TEACHER_USER)
    await page.goto('/teacher/classroom')

    // idle：填写课题后显式开启
    await expect(page.getByText('填写本节课信息后开启')).toBeVisible()
    await page.getByLabel('课题').fill('导数与函数单调性')
    await page.getByRole('button', { name: '开启课堂会话' }).click()
    await expect(page.getByText('已连接（人）')).toBeVisible()
    await expect(page.getByText('44')).toBeVisible()

    // 发题（0 基检测点）→ 实时结果渲染后端题面与分布
    await page.getByRole('button', { name: /检测点 1/ }).click()
    await expect(page.getByText('实时结果')).toBeVisible()
    await expect(page.getByText(/检测题 1/)).toBeVisible()
    await expect(page.getByText('正确率 66%')).toBeVisible()
    await expect(page.getByText('错误模式与最近作业一致')).toBeVisible()
    // 诚实标注：作答前分布为演示数据
    await expect(page.getByText('分布为演示数据')).toBeVisible()

    // 结束归档 → 回到启动面板（ended 态）
    await page.getByRole('button', { name: '结束课堂并归档' }).click()
    await expect(page.getByText('上一节课已归档，可开启新的一节。')).toBeVisible()
  })

  test('student role cannot enter teacher workspace', async ({ page }) => {
    await presetMock(page, STUDENT_USER)
    await page.goto('/teacher/today')
    await expect(page).toHaveURL(/\/overview/)
  })

  test('mock returns 40901 on a stale artifact version', async ({ request }) => {
    const headers = { Authorization: 'Bearer mock-token-teacher-preview' }
    const adapt = await request.post('/api/teacher/lessons/adapt', {
      headers,
      data: { class_id: 'c1', topic: '试讲', requirements: 'x' },
    })
    const artifact = (await adapt.json()).data
    const first = await request.put(`/api/teacher/artifacts/${artifact.artifact_id}`, {
      headers,
      data: { version: 1, content: artifact.content },
    })
    expect(first.status()).toBe(200)

    const stale = await request.put(`/api/teacher/artifacts/${artifact.artifact_id}`, {
      headers,
      data: { version: 1, content: artifact.content },
    })
    expect(stale.status()).toBe(409)
    expect((await stale.json()).code).toBe(40901)
  })
})
